import argparse
import re
import time
import json
import os
import pathlib
import torch
import types
from pathlib import Path
from datasets import load_dataset
from torch.nn.functional import pad
from torch.utils.data import DataLoader
from transformers import AutoModelForCausalLM, AutoTokenizer, PretrainedConfig
import transformers
from modeling_gptj import GPTJForCausalLM
from modeling_llama import LlamaForCausalLM
from modeling_bloom import BloomForCausalLM
from modeling_gpt_neox import GPTNeoXForCausalLM
from modeling_opt import OPTForCausalLM
from optimum.utils import NormalizedConfigManager

# to use modeling gptj modification base transformers 4.28.1:
transformers.models.gptj.modeling_gptj.GPTJForCausalLM = GPTJForCausalLM
transformers.models.llama.modeling_llama.LlamaForCausalLM = LlamaForCausalLM
transformers.models.bloom.modeling_bloom.BloomForCausalLM = BloomForCausalLM
transformers.models.gpt_neox.modeling_gpt_neox.GPTNeoXForCausalLM = GPTNeoXForCausalLM
transformers.models.opt.modeling_opt.OPTForCausalLM = OPTForCausalLM
import numpy as np
from itertools import chain


parser = argparse.ArgumentParser()
parser.add_argument(
    "--model", nargs="?", default="EleutherAI/gpt-j-6B", const="EleutherAI/gpt-j-6B"
)
parser.add_argument(
    "--dataset", nargs="?", default="NeelNanda/pile-10k", const="NeelNanda/pile-10k"
)
parser.add_argument("--dtype", type=str, default="int8")
parser.add_argument(
    "--max-new-tokens", default=32, type=int, help="output max new tokens"
)
parser.add_argument("--output_dir", nargs="?", default="./saved_results")
parser.add_argument("--quantize", action="store_true")
parser.add_argument("--ipex", action="store_true")
parser.add_argument("--sq", action="store_true")
parser.add_argument("--alpha", default="auto", help="Smooth quant parameter.")
parser.add_argument(
    "--pad_max_length", default=512, type=int, help="Pad input ids to max length."
)
parser.add_argument("--calib_iters", default=512, type=int, help="calibration iters.")
parser.add_argument("--int8", action="store_true")
parser.add_argument(
    "--int8_bf16_mixed",
    action="store_true",
    help="by default it is int8-fp32 mixed, to enable int8 mixed amp bf16 (work on platforms like SPR)",
)
parser.add_argument("--benchmark", action="store_true")
parser.add_argument("--iters", default=100, type=int, help="num iter")
parser.add_argument("--num_warmup", default=10, type=int, help="num warmup")
parser.add_argument("--batch_size", default=1, type=int, help="batch size")
args = parser.parse_args()

calib_size = 1

# model
if re.search("llama", args.model.lower()):
    from transformers import LlamaForCausalLM, LlamaTokenizer

    user_model = LlamaForCausalLM.from_pretrained(
        args.model,
        torchscript=True
        if args.ipex
        else False,  # torchscript will force `return_dict=False` to avoid jit errors
    )
    tokenizer = LlamaTokenizer.from_pretrained(args.model)

else:
    user_model = AutoModelForCausalLM.from_pretrained(
        args.model,
        torchscript=True
        if args.ipex
        else False,  # torchscript will force `return_dict=False` to avoid jit errors
    )
    tokenizer = AutoTokenizer.from_pretrained(args.model)

# to channels last
user_model = user_model.to(memory_format=torch.channels_last)
user_model.eval()

if args.ipex:
    import intel_extension_for_pytorch as ipex
    from optimum.intel.generation.modeling import TSModelForCausalLM
    from typing import Optional, Tuple, Union
    from huggingface_hub import hf_hub_download
    from transformers.utils import WEIGHTS_NAME

    # Will remove the function when the PR merge to optimum-intel.
    @classmethod
    def _from_pretrained(
        cls,
        model_id: Union[str, Path],
        config: PretrainedConfig,
        use_auth_token: Optional[Union[bool, str, None]] = None,
        revision: Optional[Union[str, None]] = None,
        force_download: bool = False,
        cache_dir: Optional[str] = None,
        file_name: Optional[str] = WEIGHTS_NAME,
        local_files_only: bool = False,
        use_cache: bool = True,
        **kwargs,
    ):
        if not getattr(config, "torchscript", False):
            raise ValueError(
                "`torchscript` should be set to True to load TorchScript model"
            )

        # Load the model from local directory
        if os.path.isdir(model_id):
            file_name = os.path.join(model_id, file_name)
            model = cls.load_model(file_name)
            model_save_dir = model_id
        # Download the model from the hub
        else:
            model_cache_path = hf_hub_download(
                repo_id=model_id,
                filename=file_name,
                use_auth_token=use_auth_token,
                revision=revision,
                cache_dir=cache_dir,
                force_download=force_download,
                local_files_only=local_files_only,
            )
            model_save_dir = Path(model_cache_path).parent
            model = cls.load_model(model_cache_path)

        return cls(
            model,
            config=config,
            model_save_dir=model_save_dir,
            use_cache=use_cache,
            **kwargs,
        )

    torch._C._jit_set_texpr_fuser_enabled(False)
    TSModelForCausalLM._from_pretrained = _from_pretrained


# quantize
if args.quantize:

    def generate_dummy_past_key_values(input_bs, user_model):
        normalized_config = NormalizedConfigManager.get_normalized_config_class(
            user_model.config.model_type
        )(user_model.config)
        nb_pkv = 2
        num_layers = normalized_config.num_layers
        num_attention_heads = normalized_config.num_attention_heads
        hidden_size = normalized_config.hidden_size
        d_k = hidden_size // num_attention_heads

        if user_model.config.model_type != "bloom":
            new_shape = [input_bs, num_attention_heads, 0, d_k]
            empty_tensor = torch.empty(size=new_shape)
            past_key_values = tuple(
                tuple(empty_tensor for _ in range(nb_pkv)) for _ in range(num_layers)
            )
            pkv = tuple(empty_tensor for _ in range(nb_pkv))
        else:
            pkv = ()
            for nb_pkv in range(nb_pkv):
                if nb_pkv % 2 == 0:
                    new_shape = [input_bs * num_attention_heads, d_k, 0]
                else:
                    new_shape = [input_bs * num_attention_heads, 0, d_k]
                pkv = pkv + (torch.empty(size=new_shape),)
        past_key_values = tuple(tuple(pkv) for _ in range(num_layers))
        return past_key_values

    class Evaluator:
        def __init__(
            self,
            dataset,
            tokenizer,
            batch_size=8,
            pad_val=1,
            pad_max=512,
            is_calib=False,
        ):
            self.dataset = dataset
            self.tokenizer = tokenizer
            self.batch_size = batch_size
            self.pad_val = pad_val
            self.pad_max = pad_max
            self.is_calib = is_calib

            # tokenize the dataset
            self.dataset = self.dataset.map(self.tokenize_function, batched=True)
            self.dataset.set_format(type="torch", columns=["input_ids"])

        @torch.no_grad()
        def tokenize_function(self, examples):
            example = self.tokenizer(examples["text"])
            return example

        @torch.no_grad()
        def collate_batch(self, batch):
            input_ids_padded = []
            last_ind = []
            for text in batch:
                input_ids = text["input_ids"]
                pad_len = self.pad_max - input_ids.shape[0]
                last_ind.append(input_ids.shape[0] - 1)
                if self.is_calib:
                    input_ids = (
                        input_ids[: self.pad_max]
                        if len(input_ids) > self.pad_max
                        else input_ids
                    )
                else:
                    input_ids = pad(input_ids, (0, pad_len), value=self.pad_val)
                input_ids_padded.append(input_ids)
            return (
                torch.vstack(input_ids_padded),
                torch.tensor(last_ind),
            )

    calib_dataset = load_dataset(args.dataset, split="train")
    calib_dataset = calib_dataset.shuffle(seed=42)
    calib_evaluator = Evaluator(
        calib_dataset,
        tokenizer,
        args.batch_size,
        pad_max=args.pad_max_length,
        is_calib=True,
    )
    calib_dataloader = DataLoader(
        calib_evaluator.dataset,
        batch_size=calib_size,
        shuffle=False,
        collate_fn=calib_evaluator.collate_batch,
    )
    input_ids = user_model.dummy_inputs["input_ids"]
    input_bs, input_len = input_ids.shape
    past_key_values = generate_dummy_past_key_values(input_bs, user_model)
    attention_mask = torch.ones(input_bs, input_len)
    example_inputs = (
        input_ids,
        tuple(past_key_values),
        attention_mask,
    )

    def calib_func(prepared_model):
        for i, (input_ids, last_ind) in enumerate(calib_dataloader):
            input_bs, input_len = input_ids.shape
            past_key_values = generate_dummy_past_key_values(input_bs, user_model)
            attention_mask = torch.ones(input_bs, input_len)
            if i >= args.calib_iters:
                break
            prepared_model(
                input_ids=input_ids,
                past_key_values=past_key_values,
                attention_mask=attention_mask,
            )

    from neural_compressor import PostTrainingQuantConfig, quantization

    if re.search("gptj", user_model.config.model_type) or re.search(
        "gpt_neox", user_model.config.model_type
    ):
        op_type_dict = {
            "add": {"weight": {"dtype": ["fp32"]}, "activation": {"dtype": ["fp32"]}},
        }
    else:
        op_type_dict = {}
    excluded_precisions = [] if args.int8_bf16_mixed else ["bf16"]
    if args.sq:
        args.alpha = args.alpha if args.alpha == "auto" else float(args.alpha)
        recipes = {"smooth_quant": True, "smooth_quant_args": {"alpha": args.alpha}}
        conf = PostTrainingQuantConfig(
            backend="ipex" if args.ipex else "default",
            excluded_precisions=excluded_precisions,
            op_type_dict=op_type_dict,
            recipes=recipes,
            example_inputs=example_inputs,
        )
    else:
        conf = PostTrainingQuantConfig(
            backend="ipex" if args.ipex else "default",
            excluded_precisions=excluded_precisions,
            op_type_dict=op_type_dict,
            example_inputs=example_inputs,
        )
    # save config
    user_model.config.save_pretrained(args.output_dir)
    q_model = quantization.fit(
        user_model,
        conf,
        calib_func=calib_func,
    )
    q_model.save(args.output_dir)
    exit(0)

# Generation
generate_kwargs = dict(do_sample=False, temperature=0.9, num_beams=4)

if args.int8 or args.int8_bf16_mixed:
    # TorchScript model don't attribute generate method, the wrapper is provided.
    if args.ipex:
        user_model = TSModelForCausalLM.from_pretrained(
            args.output_dir, file_name="best_model.pt"
        )
    else:
        from neural_compressor.utils.pytorch import load

        user_model = load(args.output_dir, user_model)


if args.benchmark:
    prompt = "Once upon a time, there existed a little girl, who liked to have adventures. She wanted to go to places and meet new people, and have fun."

    input_size = tokenizer(prompt, return_tensors="pt").input_ids.size(dim=1)
    print("---- Prompt size:", input_size)

    # start
    total_time = 0.0
    num_iter = args.iters
    num_warmup = args.num_warmup

    with torch.inference_mode(), torch.no_grad():
        for i in range(num_iter):
            tic = time.time()
            input_ids = tokenizer(prompt, return_tensors="pt").input_ids
            gen_ids = user_model.generate(
                input_ids, max_new_tokens=args.max_new_tokens, **generate_kwargs
            )
            gen_text = tokenizer.batch_decode(gen_ids, skip_special_tokens=True)
            toc = time.time()
            print(gen_text, flush=True)
            if i >= num_warmup:
                total_time += toc - tic

    print("\n", "-" * 10, "Summary:", "-" * 10)
    latency = total_time / (num_iter - num_warmup)
    print("Inference latency: %.3f sec." % latency)
    throughput = (num_iter - num_warmup) / total_time
    print("Throughput: {} samples/sec".format(throughput))
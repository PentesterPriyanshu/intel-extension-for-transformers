#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright (c) 2023 Intel Corporation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from transformers import AutoTokenizer, TextStreamer
from intel_extension_for_transformers.transformers import AutoModelForCausalLM, WeightOnlyQuantConfig

model_name = "/mnt/disk1/data2/zhenweil/models/starcoder/starcoder"  # or local path to model
woq_config = WeightOnlyQuantConfig(compute_dtype="int8", weight_dtype="int4")
prompt = "one +one +one is what"

tokenizer = AutoTokenizer.from_pretrained(model_name, trust_remote_code=True)
# import pdb; pdb.set_trace()
inputs = tokenizer(prompt, return_tensors="pt").input_ids
streamer = TextStreamer(tokenizer)

# model = AutoModelForCausalLM.from_pretrained(model_name, quantization_config=woq_config, trust_remote_code=True)
# # import pdb; pdb.set_trace()
# outputs = model.generate(inputs, streamer=streamer, max_new_tokens=300)

prompt = "from config import load_config, vcenter_user, vcenter_ip, vcenter_pwd\nconf = load_config()\n# connect to vCenter using smartconnect API:"
inputs = tokenizer(prompt, return_tensors="pt").input_ids
# outputs = model.generate(inputs, streamer=streamer, max_new_tokens=300)

from intel_extension_for_transformers.llm.runtime.graph import Model
model = Model()
model.init_from_bin("starcoder", "/mnt/disk1/data2/zhenweil/models/starcoder/ne_starcoder_int4.bin", num_beams=1, do_sample=True, top_k=40, top_p=0.95, seed=1) # n_keep=4, ctx_size = 15, n_discard=1
# import pdb; pdb.set_trace()
# # # # import pudb; pudb.set_trace()
outputs = model.generate(inputs, streamer=streamer, interactive=False, ingore_prompt=False)

# prompt = "Please help calculate: one + one + one = ?"
# inputs = tokenizer(prompt, return_tensors="pt").input_ids
# outputs = model.generate(inputs, streamer=streamer, interactive=False, ingore_prompt=True)
# # print()
# # print(tokenizer.batch_decode(outputs))
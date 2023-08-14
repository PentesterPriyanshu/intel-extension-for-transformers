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
"""Neural Chat Chatbot API."""

from .config import NeuralChatConfig
from .config import OptimizationConfig
from .config import FinetuningConfig
from .pipeline.finetuning.finetuning import Finetuning
from .config import DeviceOptions, BackendOptions, AudioOptions
from models.base_model import get_model_adapter
from .utils.common import get_device_type, get_backend_type
from .pipeline.plugins.caching.cache import init_similar_cache_from_config
from .pipeline.plugins.audio.asr import AudioSpeechRecognition
from .pipeline.plugins.audio.asr_chinese import ChineseAudioSpeechRecognition
from .pipeline.plugins.audio.tts import TextToSpeech
from .pipeline.plugins.audio.tts_chinese_tts import ChineseTextToSpeech
from .pipeline.plugins.security.SensitiveChecker import SensitiveChecker


def build_chatbot(config: NeuralChatConfig):
    """Build the chatbot with a given configuration.

    Args:
        config (NeuralChatConfig):  The class of NeuralChatConfig containing model path,
                                    device, backend, plugin config etc.
    Example:
        from neural_chat.config import NeuralChatConfig
        from neural_chat.chatbot import build_chatbot
        config = NeuralChatConfig()
        chatbot = build_chatbot(config)
        response = chatbot.predict("Tell me about Intel Xeon Scalable Processors.")
    """
    # Validate input parameters
    if config.device not in [option.name for option in DeviceOptions]:
        valid_options = ", ".join([option.name.lower() for option in DeviceOptions])
        raise ValueError(f"Invalid device value '{config.device}'. Must be one of {valid_options}")

    if config.backend not in [option.name.lower() for option in BackendOptions]:
        valid_options = ", ".join([option.name.lower() for option in BackendOptions])
        raise ValueError(f"Invalid backend value '{config.backend}'. Must be one of {valid_options}")

    if config.device == "auto":
        config.device = get_device_type()

    if config.backend == "auto":
        config.backend = get_backend_type()

    # get model adapter
    adapter = get_model_adapter(config.model_name_or_path)

    # construct document retrieval using retrieval plugin
    if config.retrieval:
        if not config.retrieval_type or not config.document_path:
             raise ValueError(f"The retrieval type and document path must be set when enable retrieval.")
        # TODO construct document retrieval

    # construct audio plugin
    if config.audio_input or config.audio_output:
        if not config.audio_lang:
            raise ValueError(f"The audio language must be set when audio input or output.")
        if config.audio_lang not in [option.name for option in AudioOptions]:
            valid_options = ", ".join([option.name.lower() for option in AudioOptions])
            raise ValueError(f"Invalid audio language value '{config.audio_lang}'. Must be one of {valid_options}")
        if config.audio_input and not config.audio_input_path:
            raise ValueError(f"The audio input path must be set when audio input enabled.")
        if config.audio_output and not config.audio_output_path:
            raise ValueError(f"The audio output path must be set when audio output enabled.")
        if config.audio_lang == AudioOptions.CHINESE.lower():
            asr = ChineseAudioSpeechRecognition()
            tts = ChineseTextToSpeech()
            adapter.register_asr(asr)
            adapter.register_tts(tts)
        else:
            asr = AudioSpeechRecognition()
            tts = TextToSpeech()
            adapter.register_asr(asr)
            adapter.register_tts(tts)

    # construct response caching
    if config.cache_chat:
        if not config.cache_chat_config_file:
            cache_chat_config_file = "./pipeline/plugins/caching/cache_config.yaml"
        else:
            cache_chat_config_file = config.cache_chat_config_file
        if not config.cache_embedding_model_dir:
            cache_embedding_model_dir = "hkunlp/instructor-large"
        else:
            cache_embedding_model_dir = config.cache_embedding_model_dir
        init_similar_cache_from_config(config_dir=cache_chat_config_file,
                                       embedding_model_dir=cache_embedding_model_dir)

    # construct safety checker
    if config.safety_checker:
        safety_checker = SensitiveChecker()
        adapter.register_safety_checker(safety_checker)

    return adapter

def finetune_model(config: FinetuningConfig):
    assert config is not None, "FinetuningConfig is needed for finetuning."
    finetuning = Finetuning(config)
    finetuning.finetune()

def optimize_model(config: OptimizationConfig):
    # Implement the logic to optimize the model
    pass
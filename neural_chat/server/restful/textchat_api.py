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

import asyncio
from fastapi.routing import APIRouter
from pydantic import BaseModel
from typing import Dict, List, Optional
from fastapi import APIRouter
from neural_chat.cli.log import logger
from neural_chat.server.restful.openai_protocol import (
    CompletionRequest, CompletionResponse, CompletionResponseChoice, 
    ChatCompletionRequest, ChatCompletionResponseChoice, ChatCompletionResponse, 
    UsageInfo, ChatMessage
)


# TODO: process request and return params in Dict
def generate_params(request: CompletionRequest, chatbot) -> Dict:
    prompt = request.prompt
    temperature = request.temperature
    top_p = request.top_p
    top_k = 1
    repetition_penalty = request.presence_penalty
    max_new_tokens = request.max_tokens
    do_sample = True
    num_beams = 1
    num_return_sequences = 1
    bad_words_ids = None
    force_words_ids = None
    use_hpu_graphs = chatbot.config.use_hpu_graphs
    use_cache = True
    return prompt, temperature, top_p, top_k, repetition_penalty, \
            max_new_tokens, do_sample, num_beams, num_return_sequences, \
            bad_words_ids, force_words_ids, use_hpu_graphs, use_cache


def check_completion_request(request: BaseModel) -> Optional[str]:
    if request.max_tokens is not None and request.max_tokens <= 0:
        return f"Param Error: {request.max_tokens} is less than the minimum of 1 --- 'max_tokens'"

    if request.n is not None and request.n <= 0:
        return f"Param Error: {request.n} is less than the minimum of 1 --- 'n'"
    
    if request.temperature is not None and request.temperature < 0:
        return f"Param Error: {request.temperature} is less than the minimum of 0 --- 'temperature'"
    
    if request.temperature is not None and request.temperature > 2:
        return f"Param Error: {request.temperature} is greater than the maximum of 2 --- 'temperature'",

    if request.top_p is not None and request.top_p < 0:
        return f"Param Error: {request.top_p} is less than the minimum of 0 --- 'top_p'",

    if request.top_p is not None and request.top_p > 1:
        return f"Param Error: {request.top_p} is greater than the maximum of 1 --- 'top_p'",

    if request.stop is not None and (
        not isinstance(request.stop, str) and not isinstance(request.stop, )
    ):
        return f"Param Error: {request.stop} is not valid under any of the given schemas --- 'stop'",

    return None


class TextChatAPIRouter(APIRouter):

    def __init__(self) -> None:
        super().__init__()
        self.chatbot = None

    def set_chatbot(self, chatbot) -> None:
        self.chatbot = chatbot

    def get_chatbot(self):
        if self.chatbot is None:
            logger.error("Chatbot instance is not found.")
            raise RuntimeError("Chatbot instance has not been set.")
        return self.chatbot
    

    async def handle_completion_request(self, request:CompletionRequest) -> CompletionResponse:
        chatbot = self.get_chatbot()
        params = generate_params(request, chatbot)
        try:
            if request.stream:
                inference_results = chatbot.predict_stream(params)
            else:
                inference_results = chatbot.predict(params)
        except:
            raise Exception("Exception occurred when inferencing chatbot.")
        else:
            logger.info('Chatbot completions finished.')
         
        choices = []
        usage = UsageInfo()
        for i, content in enumerate(inference_results):
            choices.append(
                CompletionResponseChoice(
                    index=i,
                    text=content["text"],
                    logprobs=content.get("logprobs", None),
                    finish_reason=content.get("finish_reason", "stop"),
                )
            )
            task_usage = UsageInfo.parse_obj(content["usage"])
            for usage_key, usage_value in task_usage.dict().items():
                setattr(usage, usage_key, getattr(usage, usage_key) + usage_value)

        return CompletionResponse(
            model=request.model, choices=choices, usage=UsageInfo.parse_obj(usage)
        )
    

    async def handle_chat_completion_request(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        chatbot = self.get_chatbot()
        params = generate_params(request, chatbot)
        if request.stream:
            # TODO: process stream chat completion
            return ChatCompletionResponse()

        choices = []
        chat_completions = []
        usage = UsageInfo()
        for i in range(request.n):
            if request.stream:
                content = chatbot.predict_stream(params)
            else:
                content = chatbot.predict(params)
            chat_completions.append(content)
        try:
            all_tasks = await asyncio.gather(*chat_completions)
        except Exception as e:
            return e
        usage = UsageInfo()
        for i, content in enumerate(all_tasks):
            if content["error_code"] != 0:
                return f'Error {content["error_code"]}: content["text"]'
            choices.append(
                ChatCompletionResponseChoice(
                    index=i,
                    message=ChatMessage(role="assistant", content=content["text"]),
                    finish_reason=content.get("finish_reason", "stop"),
                )
            )
            if "usage" in content:
                task_usage = UsageInfo.parse_obj(content["usage"])
                for usage_key, usage_value in task_usage.dict().items():
                    setattr(usage, usage_key, getattr(usage, usage_key) + usage_value)

        return ChatCompletionResponse(model=request.model, choices=choices, usage=usage) 
    

router = TextChatAPIRouter()

    
@router.post("/v1/completions")
async def completion_endpoint(request: CompletionRequest) -> CompletionResponse:
    ret = check_completion_request()
    if ret is not None:
        raise RuntimeError("Invalid parameter.")
    return await router.handle_completion_request(request)


@router.post("/v1/chat/completions")
async def chat_completion_endpoint(chat_request: ChatCompletionRequest) -> ChatCompletionResponse:
    ret = check_completion_request()
    if ret is not None:
        raise RuntimeError("Invalid parameter.")
    return await router.handle_chat_completion_request(chat_request)
//  Copyright (c) 2023 Intel Corporation
//
//  Licensed under the Apache License, Version 2.0 (the "License");
//  you may not use this file except in compliance with the License.
//  You may obtain a copy of the License at
//
//    http://www.apache.org/licenses/LICENSE-2.0
//
//  Unless required by applicable law or agreed to in writing, software
//  distributed under the License is distributed on an "AS IS" BASIS,
//  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
//  See the License for the specific language governing permissions and
//  limitations under the License.
// Various helper functions and utilities

#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string>
#include <map>
#include <unordered_map>
#include <tuple>
#include <vector>
#include <random>
#include <thread>

#include "core/data_types.h"
#include "core/ne_layers.h"

#define COMMON_SAMPLE_RATE 16000

//
// CLI argument parsing
//

int32_t get_num_physical_cores();

struct common_params {
  int32_t n_threads = get_num_physical_cores();

  int32_t seed = -1;        // RNG seed
  int32_t n_predict = 200;  // new tokens to predict
  int32_t n_batch = 8;      // batch size for prompt processing
  int32_t n_ctx = 512;

  std::string model = "";  // model path
  std::string prompt = "";
  std::string token_test = "";

  bool perplexity = false;

  // sampling parameters
  int32_t top_k = 0;
  float top_p = 1.0f;
  float temp = 0.8f;
  int32_t repeat_last_n = 64;
  float repeat_penalty = 1.02f;
};

bool common_params_parse(int argc, char** argv, common_params& params);

bool isValidFilename(const std::string& filename);

void gpt_print_usage(int argc, char** argv, const common_params& params);

std::string gpt_random_prompt(std::mt19937& rng);

//
// Vocab utils
//

std::string trim(const std::string& s);

std::string replace(const std::string& s, const std::string& from, const std::string& to);

struct gpt_vocab {
  using id = int32_t;
  using token = std::string;

  std::map<token, id> token_to_id;
  std::map<id, token> id_to_token;
  std::vector<std::string> special_tokens;

  void add_special_token(const std::string& token);
};

// poor-man's JSON parsing
std::map<std::string, int32_t> json_parse(const std::string& fname);

std::string convert_to_utf8(const std::wstring& input);

std::wstring convert_to_wstring(const std::string& input);

// split text into tokens
//
// ref: https://github.com/openai/gpt-2/blob/a74da5d99abaaba920de8131d64da2862a8f213b/src/encoder.py#L53
//
// Regex (Python):
// r"""'s|'t|'re|'ve|'m|'ll|'d| ?\p{L}+| ?\p{N}+| ?[^\s\p{L}\p{N}]+|\s+(?!\S)|\s+"""
//
// Regex (C++):
// R"('s|'t|'re|'ve|'m|'ll|'d| ?[[:alpha:]]+| ?[[:digit:]]+| ?[^\s[:alpha:][:digit:]]+|\s+(?!\S)|\s+)"
//
std::vector<gpt_vocab::id> gpt_tokenize(const gpt_vocab& vocab, const std::string& text);

// test outputs of gpt_tokenize
//
//   - compare with tokens generated by the huggingface tokenizer
//   - test cases are chosen based on the model's main language (under 'prompt' directory)
//   - if all sentences are tokenized identically, print 'All tests passed.'
//   - otherwise, print sentence, huggingface tokens, ggml tokens
//
void test_gpt_tokenizer(gpt_vocab& vocab, const std::string& fpath_test);

// load the tokens from encoder.json
bool gpt_vocab_init(const std::string& fname, gpt_vocab& vocab);

// sample next token given probabilities for each embedding
//
//   - consider only the top K tokens
//   - from them, consider only the top tokens with cumulative probability > P
//
// TODO: not sure if this implementation is correct
// TODO: temperature is not implemented
//
gpt_vocab::id gpt_sample_top_k_top_p(const gpt_vocab& vocab, const float* logits, int top_k, double top_p, double temp,
                                     std::mt19937& rng);

gpt_vocab::id gpt_sample_top_k_top_p_repeat(const gpt_vocab& vocab, const float* logits,
                                            const int32_t* last_n_tokens_data, size_t last_n_tokens_data_size,
                                            int top_k, double top_p, double temp, int repeat_last_n,
                                            float repeat_penalty, std::mt19937& rng);

struct MyHash {
  std::size_t operator()(const std::tuple<int, std::string, int, std::string, std::string>& k) const {
    return std::hash<int>()(std::get<0>(k))
           ^ (std::hash<std::string>()(std::get<1>(k)))
           ^ std::hash<int>()(std::get<2>(k))
           ^ (std::hash<std::string>()(std::get<3>(k)))
           ^ (std::hash<std::string>()(std::get<4>(k)));
  }
};

static std::unordered_map<std::tuple<int, std::string, int, std::string, std::string>, enum ne_ftype, MyHash>
NE_FTYPE_MAP = {
  // bits, alg, block size, scale dtype, gemm_isa -> ne_ftype
  {{4,  "sym",   QK4_0,  "fp32",  "none"}, NE_FTYPE_MOSTLY_Q4_0},
  {{4, "asym",   QK4_1,  "fp32",  "none"}, NE_FTYPE_MOSTLY_Q4_1},
  {{5,  "sym",   QK5_0,  "fp32",  "none"}, NE_FTYPE_MOSTLY_Q5_0},
  {{5, "asym",   QK5_1,  "fp32",  "none"}, NE_FTYPE_MOSTLY_Q5_1},
  {{8,  "sym",   QK8_0,  "fp32",  "none"}, NE_FTYPE_MOSTLY_Q8_0},
};

struct quant_params {
  std::string model_file = "";
  std::string out_file = "";

  int32_t bits = 4;
  std::string alg = "sym";
  int32_t block_size = 32;
  std::string scale_dtype = "fp32";
  std::string gemm_isa = "none";
};

bool quant_params_parse(int argc, char** argv, quant_params& params);

bool ne_common_quantize_0(std::ifstream& finp, std::ofstream& fout, const ne_ftype ftype,
                          const std::vector<std::string>& to_quant, const std::vector<std::string>& to_skip);

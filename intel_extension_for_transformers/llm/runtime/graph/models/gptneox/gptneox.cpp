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
#include <math.h>
#include <stdio.h>
#include <stdlib.h>

#include <algorithm>
#include <cassert>
#include <cinttypes>
#include <cstring>
#include <exception>
#include <fstream>
#include <iterator>
#include <memory>
#include <random>
#include <string>
#include <unordered_map>
#include <vector>

#include "core/data_types.h"
#include "core/ne.h"
#include "core/ne_layers.h"
#include "core/ne_jblas.h"
#include "core/layers/mha_dense.h"
#include "models/model_utils/model_config.h"
#include "models/model_utils/model_utils.h"
#include "models/model_utils/util.h"

// feed-forward network
struct ne_tensor* gpt_neox_ff(const model_layer& layer, const int batch_size, const int N, ne_context* ctx0,
                              ne_tensor* inp) {
  struct ne_tensor* cur = ne_norm(ctx0, inp);

  cur = ne_add(ctx0, ne_mul(ctx0, ne_repeat(ctx0, layer.norm[2], cur), cur), ne_repeat(ctx0, layer.norm[3], cur));
  if (jblas_fusion_FFN_Add_GeLu_f32f32_support(layer.ffn[0]->data, layer.ffn[2]->data, N * batch_size, cur->ne[0],
                                               layer.ffn[0]->ne[1], layer.ffn[2]->ne[1])) {
    cur = ne_ffn_add_gelu(ctx0, layer.ffn[0], layer.ffn[2], layer.ffn[1], layer.ffn[3], cur);
  } else {
    cur = ne_mul_mat(ctx0, layer.ffn[0], cur);

    cur = ne_add(ctx0, ne_repeat(ctx0, layer.ffn[1], cur), cur);

    // GELU activation
    cur = ne_gelu(ctx0, cur);

    // projection
    // cur = proj_w*cur + proj_b
    cur = ne_mul_mat(ctx0, layer.ffn[2], cur);

    cur = ne_add(ctx0, ne_repeat(ctx0, layer.ffn[3], cur), cur);
  }
  return cur;
}

// evaluate the transformer
//
//   - lctx:      model context
//   - tokens:    new batch of tokens to process
//   - n_past:    the context size so far
//   - n_threads: number of threads to use
//

static bool gptneox_model_eval_internal(model_context& lctx, const model_token* tokens, const int n_tokens,
                                        const int n_past, const int n_threads) {
  // // enforce that the first token is BOS
  // if (n_past == 0 && tokens[0] != model_token_bos()) {
  //   fprintf(stderr, "%s: first token must be BOS\n", __func__);
  //   return false;
  // }

  const int64_t t_start_us = ne_time_us();

  const int N = n_tokens;

  const int batch_size = lctx.batch_size;

  const auto& model = lctx.model;
  const auto& hparams = model.hparams;

  const auto& kv_self = model.kv_self;

  MODEL_ASSERT(!!kv_self.ctx);

  const int n_embd = hparams.n_embd;
  const int n_layer = hparams.n_layer;
  const int n_ctx = hparams.n_ctx;
  const int n_head = hparams.n_head;
  const int n_vocab = hparams.n_vocab;
  const int n_rot = hparams.n_rot;

  auto& mem_per_token = lctx.mem_per_token;
  auto& buf_compute = lctx.buf_compute;

  struct ne_init_params params = {
      /*.mem_size   =*/buf_compute.size,
      /*.mem_buffer =*/buf_compute.addr,
      /*.no_alloc   =*/false,
  };

  struct ne_context* ctx0 = ne_init(params);

  // for big progptneoxs, if BLAS is enabled, it is better to use only one thread
  // otherwise, the threads are spin-lock waiting for the BLAS calls and are degrading the performance
  ne_cgraph gf = {};
  gf.n_threads = N >= 32 && ne_cpu_has_blas() ? 1 : n_threads;

  struct ne_tensor* embd = d_ne_new_tensor_1d(ctx0, NE_TYPE_I32, N);
  ne_set_name(embd, "embd");
  memcpy(embd->data, tokens, N * ne_element_size(embd));

  struct ne_tensor* inpL = ne_get_rows(ctx0, model.others[0], embd);

  for (int il = 0; il < n_layer; ++il) {
    struct ne_tensor* cur;

    lctx.use_buf(ctx0, 0);

    // self-attention
    {
      {
        cur = ne_norm(ctx0, inpL);

        cur = ne_add(ctx0, ne_mul(ctx0, ne_repeat(ctx0, model.layers[il].norm[0], cur), cur),
                     ne_repeat(ctx0, model.layers[il].norm[1], cur));
      }

      // compute QKV
      {
        cur = ne_mul_mat(ctx0, model.layers[il].attn[0], cur);

        cur = ne_add(ctx0, ne_repeat(ctx0, model.layers[il].attn[1], cur), cur);
      }

      struct ne_tensor* Qcur = ne_cont(ctx0, ne_view_3d(ctx0, cur, n_embd / n_head, n_head, N, cur->nb[1] / n_head,
                                                        cur->nb[1], 0 * sizeof(float) * n_embd / n_head));
      struct ne_tensor* Kcur = ne_cont(ctx0, ne_view_3d(ctx0, cur, n_embd / n_head, n_head, N, cur->nb[1] / n_head,
                                                        cur->nb[1], 1 * sizeof(float) * n_embd / n_head));
      struct ne_tensor* Vcur = ne_cont(ctx0, ne_view_3d(ctx0, cur, n_embd / n_head, n_head, N, cur->nb[1] / n_head,
                                                        cur->nb[1], 2 * sizeof(float) * n_embd / n_head));

      // using mode = 2 for GPT-NeoX mode
      Qcur = ne_rope_inplace(ctx0, Qcur, n_past, n_rot, 2, 0);
      Kcur = ne_rope_inplace(ctx0, Kcur, n_past, n_rot, 2, 0);

      // store key and value to memory
      {
        Vcur = ne_transpose(ctx0, ne_reshape_2d(ctx0, Vcur, n_embd, N));

        struct ne_tensor* k =
            ne_view_1d(ctx0, kv_self.k, N * n_embd, (ne_element_size(kv_self.k) * n_embd) * (il * n_ctx + n_past));
        struct ne_tensor* v =
            ne_view_2d(ctx0, kv_self.v, N, n_embd, (n_ctx)*ne_element_size(kv_self.v),
                       (il * n_ctx) * ne_element_size(kv_self.v) * n_embd + n_past * ne_element_size(kv_self.v));

        ne_build_forward_expand(&gf, ne_cpy(ctx0, Kcur, k));
        ne_build_forward_expand(&gf, ne_cpy(ctx0, Vcur, v));
      }
      // Q = Qcur.contiguous().view(n_embd/n_head, n_head, N).permute(0, 2, 1, 3)
      struct ne_tensor* Q = ne_permute(ctx0, Qcur, 0, 2, 1, 3);

      // K = Kmem.view(n_embd/n_head, n_head, n_past + N).permute(0, 2, 1, 3)
      struct ne_tensor* K = ne_permute(ctx0,
                                       ne_reshape_3d(ctx0,
                                                     ne_view_1d(ctx0, kv_self.k, (n_past + N) * n_embd,
                                                                il * n_ctx * ne_element_size(kv_self.k) * n_embd),
                                                     n_embd / n_head, n_head, n_past + N),
                                       0, 2, 1, 3);

      // K * Q
      struct ne_tensor* KQ = ne_mul_mat(ctx0, K, Q);

      // KQ_scaled = KQ / sqrt(n_embd/n_head)
      struct ne_tensor* KQ_scaled = ne_scale_inplace(ctx0, KQ, ne_new_f32(ctx0, 1.0f / sqrt(float(n_embd) / n_head)));

      // KQ_masked = mask_past(KQ_scaled)
      struct ne_tensor* KQ_masked = ne_diag_mask_inf_inplace(ctx0, KQ_scaled, n_past);

      // KQ = soft_max(KQ_masked)
      struct ne_tensor* KQ_soft_max = ne_soft_max_inplace(ctx0, KQ_masked);

      // V_trans = Vmem.view(n_embd/n_head, n_head, n_past + N).permute(1, 2, 0, 3).contiguous()
      struct ne_tensor* V = ne_view_3d(
          ctx0, kv_self.v, n_past + N, n_embd / n_head, n_head, n_ctx * ne_element_size(kv_self.v),
          n_ctx * ne_element_size(kv_self.v) * n_embd / n_head, il * n_ctx * ne_element_size(kv_self.v) * n_embd);

      // KQV = transpose(V) * KQ_soft_max
      struct ne_tensor* KQV = ne_mul_mat(ctx0, V, KQ_soft_max);

      // KQV_merged = KQV.permute(0, 2, 1, 3)
      struct ne_tensor* KQV_merged = ne_permute(ctx0, KQV, 0, 2, 1, 3);

      // cur = KQV_merged.contiguous().view(n_embd, N)
      cur = ne_cpy(ctx0, KQV_merged, ne_new_tensor_2d(ctx0, NE_TYPE_F32, n_embd, N, NE_SIZE_CALC));

      // projection
      {
        cur = ne_mul_mat(ctx0, model.layers[il].attn[2], cur);

        cur = ne_add(ctx0, ne_repeat(ctx0, model.layers[il].attn[3], cur), cur);
      }
    }
    lctx.use_buf(ctx0, 1);
    if (hparams.par_res == 0) {
      struct ne_tensor* inpFF = ne_add(ctx0, cur, inpL);

      cur = gpt_neox_ff(model.layers[il], N, batch_size, ctx0, inpFF);

      // input for next layer
      inpL = ne_add(ctx0, cur, inpFF);
    } else {
      struct ne_tensor* inpFF = cur;

      // this is independent of the self-attention result, so it could be done in parallel to the self-attention
      // note here we pass inpL instead of cur
      cur = gpt_neox_ff(model.layers[il], N, batch_size, ctx0, inpL);

      // layer input + FF
      cur = ne_add(ctx0, cur, inpFF);

      // input for next layer
      inpL = ne_add(ctx0, cur, inpL);
    }
  }

  lctx.use_buf(ctx0, 0);
  // used at the end to optionally extract the embeddings
  struct ne_tensor* embeddings = NULL;
  // norm
  {
    inpL = ne_norm(ctx0, inpL);

    // inpL = ln_f_g*inpL + ln_f_b
    inpL = ne_add(ctx0, ne_mul(ctx0, ne_repeat(ctx0, model.others[1], inpL), inpL),
                  ne_repeat(ctx0, model.others[2], inpL));
  }

  lctx.use_buf(ctx0, -1);
  // lm_head
  {
    inpL = ne_mul_mat(ctx0, model.others[3], inpL);

    // inpL = ne_add(ctx0,
    //         ne_repeat(ctx0, model.lmh_b, inpL),
    //         inpL);
  }

  // logits -> probs
  // inpL = ne_soft_max_inplace(ctx0, inpL);

  // run the computation
  ne_build_forward_expand(&gf, inpL);
  ne_graph_compute(ctx0, &gf);

#ifdef NE_PERF
  bool engine_profiling_ = (getenv("ENGINE_PROFILING") != NULL);
  if (engine_profiling_) {
    ne_graph_profiling(&gf);
  }
#endif

  // update kv token count
  lctx.model.kv_self.n = n_past + N;

  // extract logits
  {
    auto& logits_out = lctx.logits;

    if (lctx.logits_all) {
      logits_out.resize(n_vocab * N);
      memcpy(logits_out.data(), (float*)ne_get_data(inpL), sizeof(float) * n_vocab * N);
    } else {
      // return result for just the last token
      logits_out.resize(n_vocab);
      memcpy(logits_out.data(), (float*)ne_get_data(inpL) + (n_vocab * (N - 1)), sizeof(float) * n_vocab);
    }
  }

  // extract embeddings
  if (!lctx.embedding.empty()) {
    auto& embedding_out = lctx.embedding;

    embedding_out.resize(n_embd);
    memcpy(embedding_out.data(), (float*)ne_get_data(embeddings) + (n_embd * (N - 1)), sizeof(float) * n_embd);
  }

  if (mem_per_token == 0) {
    mem_per_token = ne_used_mem(ctx0) / N;
  }

  ne_free(ctx0);

  // measure the performance only for the single-token evals
  int64_t time_interval = ne_time_us() - t_start_us;
  if (N == 1) {
    lctx.t_eval_us += time_interval;
    lctx.n_eval++;
  } else if (N > 1) {
    lctx.t_p_eval_us += time_interval;
    lctx.n_p_eval += N;
  }
  lctx.eval_times.push_back(time_interval);

  return true;
}

int model_eval(struct model_context* ctx, const model_token* tokens, int n_tokens, int n_past, int n_threads) {
  if (!gptneox_model_eval_internal(*ctx, tokens, n_tokens, n_past, n_threads)) {
    fprintf(stderr, "%s: failed to eval\n", __func__);
    return 1;
  }

  // get a more accurate load time, upon first eval
  // TODO: fix this
  if (!ctx->has_evaluated_once) {
    ctx->t_load_us = ne_time_us() - ctx->t_start_us;
    ctx->has_evaluated_once = true;
  }

  return 0;
}
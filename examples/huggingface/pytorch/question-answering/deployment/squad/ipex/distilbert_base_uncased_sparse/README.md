Step-by-Step
======
This document describes the end-to-end workflow for Huggingface model [Distilbert Base Sparse](https://huggingface.co/Intel/distilbert-base-uncased-squadv1.1-sparse-80-1X4-block) with IPEX backend.

# Prerequisite
## Install python environment
Create a new python environment
```shell
conda create -n <env name> python=3.8
conda activate <env name>
```
Make sure you have the autoconf installed. 
Also, `gcc` higher than 9.0, `cmake` higher than 3 is required.
```shell
gcc -v
cmake --version
conda install cmake
sudo apt install autoconf
```
Install Intel® Extension for Transformers, please refer to [installation](https://github.com/intel/intel-extension-for-transformers/blob/main/docs/installation.md)
```shell
# Install from pypi
pip install intel-extension-for-transformers

# Install from source code
cd <intel_extension_for_transformers_folder>
git submodule update --init --recursive
python setup.py install
```
Install required dependencies for examples
```shell
cd <intel_extension_for_transformers_folder>/examples/examples/huggingface/pytorch/question-answering/deployment/squad/ipex/distilbert_base_uncased_sparse
pip install -r requirements.txt
```

# Inference Pipeline
Neural Engine can parse ONNX model and Neural Engine IR. 
We provide with three `mode`: `accuracy`, `throughput` or `latency`. For throughput mode, we will use multi-instance with 4cores/instance occupying one socket.
You can run fp32 model inference by setting `precision=fp32`, command as follows:

```shell
bash run_distilbert_sparse.sh --model=Intel/distilbert-base-uncased-squadv1.1-sparse-80-1X4-block --dataset=squad --precision=fp32 --mode=throughput
```

By setting `precision=int8` you could get PTQ int8 model and setting `precision=bf16` to get bf16 model.
```shell
bash run_distilbert_sparse.sh --model=Intel/distilbert-base-uncased-squadv1.1-sparse-80-1X4-block --dataset=squad --precision=int8 --mode=throughput
```

# Benchmark
## Accuracy
Python API command as follows:
  ```shell
  GLOG_minloglevel=2 python run_executor.py --input_model=./model_and_tokenizer --mode=accuracy --dataset_name=squad --batch_size=1
  ```
  If you just want a quick start, you can run only a part of dataset.
  ```shell
  GLOG_minloglevel=2 python run_executor.py --input_model=./model_and_tokenizer --mode=accuracy --dataset_name=squad --batch_size=1 --max_eval_samples=10
  ```
  But the accuracy of quick start is unauthentic.

## Performance
Python API command as follows:
  ```shell
  GLOG_minloglevel=2 python run_executor.py --input_model=./model_and_tokenizer --mode=performance --batch_size=1 --seq_len=384
  ```

>**Note**: The IPEX backend does not support sparse model optimization well currently. Please use the Neural Engine as the backend for this sparse model.
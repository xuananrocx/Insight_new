"""下载 bge-reranker-v2-m3 的 ONNX int8 模型到用户数据目录（rerank C2 方案）。

来源：onnx-community/bge-reranker-v2-m3-ONNX（官方社区转换）
产物：<data>/models/reranker/bge-reranker-v2-m3-int8/
  - onnx/model_int8.onnx  (~150MB，动态 int8 量化)
  - tokenizer/config 等

用法（Insight 根目录）：.venv/Scripts/python.exe scripts/download_reranker_onnx.py
需要能访问 huggingface.co（HTTP_PROXY 走 7897 代理时自动生效）。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

REPO = "onnx-community/bge-reranker-v2-m3-ONNX"
FILES = [
    "onnx/model_int8.onnx",
    "config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
]


def target_dir() -> Path:
    from src.core.config import settings
    return settings.get_path("metadata_db").parent / "models" / "reranker" / "bge-reranker-v2-m3-int8"


def main() -> int:
    from huggingface_hub import hf_hub_download
    out = target_dir()
    out.mkdir(parents=True, exist_ok=True)
    for f in FILES:
        print(f"[download] {f} ...")
        p = hf_hub_download(repo_id=REPO, filename=f, local_dir=str(out))
        print(f"[download] -> {p}")
    ok = (out / "onnx" / "model_int8.onnx").exists()
    print(f"[download] {'OK' if ok else 'FAILED'}: {out}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

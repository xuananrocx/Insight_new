"""Read-only local model checks and bounded download metadata probes."""
from __future__ import annotations

import json
import os
from pathlib import Path

from huggingface_hub import constants, get_hf_file_metadata, hf_hub_url, try_to_load_from_cache


def offline_mode() -> bool:
    return constants.HF_HUB_OFFLINE or os.environ.get("TRANSFORMERS_OFFLINE", "").upper() in {"1", "TRUE", "YES", "ON"}


def local_files_ready(model_name: str) -> bool:
    """Check required BGE files, not weight hashes or runtime compatibility."""
    root = Path(model_name)
    if not root.is_dir():
        try:
            cached = try_to_load_from_cache(model_name, "config.json")
        except (ValueError, OSError):
            return False
        if not isinstance(cached, str):
            return False
        root = Path(cached).parent

    def exists(name: str) -> bool:
        p = Path(name)
        return not p.is_absolute() and ".." not in p.parts and (root / p).is_file() and (root / p).stat().st_size > 0

    try:
        if not all(exists(n) for n in ("config.json", "tokenizer_config.json", "modules.json")):
            return False
        if not (exists("tokenizer.json") or exists("vocab.txt")):
            return False
        json.loads((root / "config.json").read_text(encoding="utf-8"))
        modules = json.loads((root / "modules.json").read_text(encoding="utf-8"))
        if not isinstance(modules, list) or not modules:
            return False
        for module in modules:
            path = module.get("path", "")
            if module.get("type") == "sentence_transformers.models.Normalize":
                # Normalize.load constructs a stateless layer; its directory is absent in BGE snapshots.
                continue
            if path and not exists(f"{path}/config.json"):
                return False
        for filename in ("model.safetensors", "pytorch_model.bin"):
            if exists(filename):
                return True
            index = filename + ".index.json"
            if exists(index):
                shards = json.loads((root / index).read_text(encoding="utf-8")).get("weight_map", {})
                if shards and all(exists(name) for name in shards.values()):
                    return True
    except (OSError, ValueError, TypeError, AttributeError):
        return False
    return False


def download_error_message(error: Exception) -> str:
    """Classify exception chains without exposing URLs, credentials or paths."""
    names = set()
    current: BaseException | None = error
    seen = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        names.add(type(current).__name__)
        current = current.__cause__ or current.__context__
    if "FileMetadataError" in names:
        return "下载源未返回完整的模型版本或文件信息，请检查下载地址与代理配置。"
    if "OfflineModeIsEnabled" in names:
        return "当前已禁止联网下载，请先在服务器部署完整模型文件。"
    if names & {"GatedRepoError", "RepositoryNotFoundError", "EntryNotFoundError", "RevisionNotFoundError"}:
        return "模型文件不存在或无权访问，请检查模型来源和访问权限。"
    if names & {"SSLError", "ProxyError", "ConnectTimeout", "ReadTimeout", "Timeout", "ConnectionError", "LocalEntryNotFoundError"}:
        return "无法从模型下载源取得所需文件，请检查服务器网络、代理和证书后重试。"
    return "模型下载或加载失败，请检查模型文件、运行环境及服务器日志。"


def model_availability(model_name: str, *, in_memory: bool = False, probe: bool = False) -> dict:
    def result(state, allowed, message):
        return {"state": state, "can_switch": allowed, "message": message}

    if in_memory:
        return result("in_memory", True, "模型已加载到内存，可直接切换。")
    if local_files_ready(model_name):
        return result("local", True, "已找到本地模型文件，切换时将验证并加载。")
    if offline_mode():
        return result("offline_missing", False, "当前为离线模式，目标模型未部署完整。请先部署模型文件，再切换。")
    if probe:
        try:
            metadata = get_hf_file_metadata(hf_hub_url(model_name, "config.json"), token=False, timeout=5)
            if not metadata.commit_hash or not metadata.etag or metadata.size is None:
                return result("download_unavailable", False, "下载源未返回完整的模型版本或文件信息，请检查下载地址与代理配置。")
        except Exception as error:
            return result("download_unavailable", False, download_error_message(error))
        return result("download_required", True, "需要下载模型；配置文件连接检查通过，完整下载仍取决于网络和磁盘空间。")
    return result("download_required", True, "本地模型文件不完整，切换前将检查下载连接。")


def require_model_access(client, model_name: str) -> dict:
    from fastapi import HTTPException
    status = client.get_embedding_status()
    availability = model_availability(model_name, in_memory=model_name in status.get("cached_models", []), probe=True)
    if not availability["can_switch"]:
        code = 409 if availability["state"] == "offline_missing" else 503
        raise HTTPException(code, availability["message"])
    return availability

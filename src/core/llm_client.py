"""LLM 客户端 - 支持 OpenAI 和 Anthropic 双协议 + 本地 embedding。

Chat：支持 OpenAI 协议（DeepSeek/Qwen/GLM/等）和 Anthropic 协议（Claude/archforce 等）。
Embedding：支持本地 sentence-transformers（无需 API key）或 API 模式。

架构约束：
    LLM client 只在 FastAPI 主进程内使用。
    Streamlit 等其他进程通过 HTTP 调 /api/v1/qa/ask 等接口，不直接调 LLM。
"""
from __future__ import annotations

import logging
from typing import Any, AsyncGenerator

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.core.config import ConfigError, settings
from src.core.llm_providers import (
    AnthropicArchProvider,
    AnthropicProvider,
    BaseLLMProvider,
    OpenAIProvider,
)

logger = logging.getLogger(__name__)


class LLMError(Exception):
    """LLM 调用失败。"""


class NoAvailableProviderError(LLMError):
    """所有 provider 都不可用。"""


class _Provider:
    """单个 LLM provider 的封装 - 支持 OpenAI 和 Anthropic 双协议。"""

    def __init__(self, name: str, cfg: dict[str, Any]) -> None:
        self.name = name
        self.cfg = cfg
        self.protocol = cfg.get("protocol", "openai")  # openai 或 anthropic
        self._api_key = settings.resolve_api_key(cfg)

        # SSRF 校验：启动加载 config.yaml 时也要拦（防止攻击者改 yaml 绕过 API 校验）
        # anthropic-arch 允许 http + 私有 IP（国内代理），其他 provider 强制 https + 禁止内网
        base_url = cfg.get("base_url") or ""
        if base_url:
            try:
                from src.core.security import validate_external_url, SecurityError
                is_arch = self.protocol == "anthropic-arch"
                allow_http = is_arch
                allow_private_ip = is_arch
                cfg["base_url"] = validate_external_url(base_url, allow_http=allow_http, allow_private_ip=allow_private_ip)
            except SecurityError as e:
                import logging
                logging.getLogger(__name__).warning(
                    f"provider {name} base_url 不安全，已禁用: {e}"
                )
                cfg = {**cfg, "enabled": False}

        # 根据协议类型创建相应的 provider 实例
        # "anthropic-arch" 是特殊协议：通过伪装 Claude Code CLI 绕过 archforce 服务端检测
        if self.protocol == "anthropic-arch":
            self._impl = AnthropicArchProvider(name, cfg, self._api_key)
        elif self.protocol == "anthropic":
            self._impl = AnthropicProvider(name, cfg, self._api_key)
        else:
            self._impl = OpenAIProvider(name, cfg, self._api_key)

    @property
    def enabled(self) -> bool:
        return self._impl.enabled

    @property
    def has_api_key(self) -> bool:
        return self._impl.has_api_key

    @property
    def usable(self) -> bool:
        return self._impl.usable

    @property
    def base_url(self) -> str:
        return self._impl.base_url

    @property
    def chat_model(self) -> str | None:
        return self._impl.chat_model

    @property
    def embedding_model(self) -> str | None:
        return self._impl.embedding_model

    def test_connection(self) -> bool:
        """测试 provider 连接是否可用。"""
        return self._impl.test_connection()

    def chat(self, messages: list[dict], **kwargs: Any) -> str:
        """同步聊天。"""
        return self._impl.chat(messages, **kwargs)

    async def chat_stream(self, messages: list[dict], **kwargs: Any) -> AsyncGenerator[str, None]:
        """流式聊天。"""
        async for token in self._impl.chat_stream(messages, **kwargs):
            yield token

    def embed(self, texts: list[str]) -> list[list[float]]:
        """文本向量化。"""
        return self._impl.embed(texts)


class _LocalEmbedding:
    """本地 sentence-transformers embedding（无需 API key）。
    cache_size 控制模型常驻内存数量：
      0 = 每次切换都重新加载（省内存，切换慢）
      1 = 只保留当前模型
      N = LRU 缓存最近 N 个模型
    """

    def __init__(self, model_name: str, device: str = "cpu", cache_size: int = 3) -> None:
        self.model_name = model_name
        self.device = device
        self._cache_size = max(0, int(cache_size))
        self._cache: dict[str, Any] = {}
        self._cache_dims: dict[str, int] = {}
        self._cache_order: list[str] = []
        self._model: Any = None
        self._dimensions: int | None = None

    def _load_into_cache(self, model_name: str) -> Any:
        # cache_size=0：不缓存，每次都新建（不存进 _cache），切换前显式释放旧模型
        if self._cache_size == 0:
            from sentence_transformers import SentenceTransformer
            # 显式释放旧 _model（避免新旧模型同时驻留导致 RSS 翻倍）
            old_model = getattr(self, "_model", None)
            if old_model is not None:
                logger.debug(f"释放旧 embedding 模型实例: {self.model_name}")
                del old_model
                self._model = None
                import gc
                gc.collect()
            logger.info(f"加载 embedding 模型（无缓存模式）: {model_name}")
            model = SentenceTransformer(model_name, device=self.device)
            self._dimensions = model.get_sentence_embedding_dimension()
            return model

        if model_name in self._cache:
            self._cache_order.remove(model_name)
            self._cache_order.append(model_name)
            return self._cache[model_name]
        from sentence_transformers import SentenceTransformer
        logger.info(f"加载本地 embedding 模型: {model_name}（首次运行需下载）")
        model = SentenceTransformer(model_name, device=self.device)
        self._cache[model_name] = model
        self._cache_dims[model_name] = model.get_sentence_embedding_dimension()
        self._cache_order.append(model_name)
        while len(self._cache_order) > self._cache_size:
            old = self._cache_order.pop(0)
            self._cache.pop(old, None)
            self._cache_dims.pop(old, None)
            logger.info(f"释放 embedding 模型缓存: {old}")
            # 主动 GC：SentenceTransformer 占用数百 MB PyTorch 权重
            import gc
            gc.collect()
        logger.info(f"模型加载完成，维度: {self._cache_dims[model_name]}")
        return model

    def _load(self) -> None:
        if self._cache_size == 0:
            # 无缓存：每次都重建（释放旧的）
            self._model = self._load_into_cache(self.model_name)
            return
        if self.model_name in self._cache:
            self._model = self._load_into_cache(self.model_name)
            self._dimensions = self._cache_dims[self.model_name]
            return
        self._model = self._load_into_cache(self.model_name)
        self._dimensions = self._cache_dims[self.model_name]

    def switch_model(self, model_name: str) -> None:
        if model_name == self.model_name and (
            self._cache_size == 0 or self._cache.get(model_name) is not None
        ):
            return
        was_cached = self._cache_size > 0 and model_name in self._cache
        logger.info(f"切换 embedding 模型: {self.model_name} → {model_name} ({'缓存命中' if was_cached else '首次加载'})")
        self.model_name = model_name
        self._load()

    @property
    def dimensions(self) -> int | None:
        return self._dimensions

    def embed(self, texts: list[str]) -> list[list[float]]:
        self._load()
        embeddings = self._model.encode(texts, show_progress_bar=False, normalize_embeddings=True)
        return embeddings.tolist()


class LLMClient:
    """统一 LLM 客户端：管理多个 provider + 本地 embedding。"""

    def __init__(self) -> None:
        self._providers: dict[str, _Provider] = {}
        for name, cfg in settings.config["llm"]["providers"].items():
            self._providers[name] = _Provider(name, cfg)
        self._chat_chain = self._build_chat_chain()
        self._local_embed: _LocalEmbedding | None = None
        self._embed_mode = self._get_embed_mode()
        self._embed_chain = self._build_embed_chain() if self._embed_mode == "api" else []

    def _get_embed_mode(self) -> str:
        emb_cfg = settings.config["llm"].get("embedding", {})
        return emb_cfg.get("mode", "api")

    def _build_chat_chain(self) -> list[str]:
        primary = settings.chat_provider
        chain: list[str] = []
        if primary in self._providers:
            chain.append(primary)
        for name in settings.fallback_chain:
            if name not in chain and name in self._providers:
                chain.append(name)
        return [n for n in chain if self._providers[n].usable and self._providers[n].chat_model]

    def _build_embed_chain(self) -> list[str]:
        primary = settings.config["llm"].get("embedding_provider", "qwen")
        chain: list[str] = []
        if primary in self._providers:
            chain.append(primary)
        for name in settings.fallback_chain:
            if name not in chain and name in self._providers:
                chain.append(name)
        return [
            n for n in chain
            if self._providers[n].usable
            and self._providers[n].embedding_model
            and self._providers[n].embedding_model is not False
        ]

    def _get_local_embedder(self) -> _LocalEmbedding:
        if self._local_embed is None:
            emb_cfg = settings.config["llm"].get("embedding", {})
            model_name = emb_cfg.get("local_model", "BAAI/bge-small-zh-v1.5")
            device = emb_cfg.get("device", "cpu")
            cache_size = emb_cfg.get("cache_size", 3)
            self._local_embed = _LocalEmbedding(model_name, device, cache_size=cache_size)
            # 预热：把默认模型加载到内存（首次 1-3 秒），后续切换永远秒级
            # cache_size=0 时跳过预热（用户选择不缓存）
            if cache_size > 0:
                try:
                    self._local_embed._load()
                except Exception as e:
                    logger.warning(f"预热 embedding 模型失败（下次用到时再加载）: {e}")
        return self._local_embed

    def set_embedding_cache_size(self, new_size: int) -> dict[str, Any]:
        """动态修改 embedding 缓存上限。
        - 0 = 清空所有缓存（立即释放内存），之后每次切换都重新加载
        - >0 = 更新 LRU 上限，淘汰多余缓存
        同时写回 config.yaml 以便重启后保留。
        """
        embedder = self._get_local_embedder()
        new_size = max(0, int(new_size))

        # 1. 更新运行时
        embedder._cache_size = new_size

        # 2. 如果缩小了，淘汰超出的缓存
        evicted: list[str] = []
        while len(embedder._cache_order) > new_size:
            old = embedder._cache_order.pop(0)
            embedder._cache.pop(old, None)
            embedder._cache_dims.pop(old, None)
            evicted.append(old)
            logger.info(f"缓存上限缩小，淘汰: {old}")

        # 3. 如果 new_size=0，连当前模型也释放（下次 embed 时重新加载）
        if new_size == 0:
            embedder._cache.clear()
            embedder._cache_dims.clear()
            embedder._cache_order.clear()
            # 当前 self._model 保留，因为正在用
            logger.info("缓存已禁用，所有常驻模型已释放")

        # 4. 写回 config.yaml（持久化）
        try:
            self._save_cache_size_to_config(new_size)
        except Exception as e:
            logger.warning(f"无法写回 config.yaml（仅本次运行生效）: {e}")

        logger.info(f"embedding cache_size 已改为 {new_size}，淘汰 {len(evicted)} 个模型")
        return self.get_embedding_status()

    def _save_cache_size_to_config(self, new_size: int) -> None:
        """写回用户目录 config.yaml 的 llm.embedding.cache_size 字段。"""
        import yaml
        from src.core.config import USER_CONFIG_PATH
        config_path = USER_CONFIG_PATH
        if not config_path.exists():
            return
        with open(config_path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        emb = raw.get("llm", {}).get("embedding", {})
        emb["cache_size"] = new_size
        raw.setdefault("llm", {})["embedding"] = emb
        # 同步到内存中的 settings
        settings.config["llm"]["embedding"]["cache_size"] = new_size
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(raw, f, allow_unicode=True, sort_keys=False)

    def switch_embedding_model(self, model_name: str) -> dict[str, Any]:
        """切换本地 embedding 模型。返回切换后的状态 + 维度冲突 KB 列表。

        同时写回 config.yaml 以便重启后保留（跟 set_embedding_cache_size 一致）。

        返回字段 dim_mismatched_kbs：维度与新模型不一致的 KB 列表，
        前端应据此提示用户去 KB 管理页重建。
        """
        embedder = self._get_local_embedder()
        embedder.switch_model(model_name)
        # 同步内存
        settings.config["llm"]["embedding"]["local_model"] = model_name
        # 持久化到 config.yaml
        try:
            self._save_local_model_to_config(model_name)
        except Exception as e:
            logger.warning(f"无法写回 config.yaml（仅本次运行生效）: {e}")
        status = self.get_embedding_status()
        status["dim_mismatched_kbs"] = self._find_dim_mismatched_kbs(status.get("dimensions"))
        if status["dim_mismatched_kbs"]:
            logger.warning(
                f"切到 {model_name}（{status.get('dimensions')}维）后，"
                f"{len(status['dim_mismatched_kbs'])} 个 KB 维度不匹配："
                f"{[k['kb_id'] for k in status['dim_mismatched_kbs']]}"
            )
        return status

    def _find_dim_mismatched_kbs(self, expected_dim: int | None) -> list[dict]:
        """扫描所有 KB，返回 collection 实际维度与 expected_dim 不一致的列表。"""
        if not expected_dim:
            return []
        try:
            from src.core import vector_store
            from src.db import metadata_db
            metadata_db.init_db()
            mismatches: list[dict] = []
            kbs = metadata_db.list_kbs()
            for kb in kbs:
                coll = kb.get("collection_name")
                if not coll:
                    continue
                actual = vector_store.get_collection_dim(coll)
                # 只关注有实际数据的 collection（空集合下次写入会自动锁新维度）
                if actual and actual != expected_dim:
                    mismatches.append({
                        "kb_id": kb.get("id") or kb.get("kb_id"),
                        "name": kb.get("name"),
                        "collection_name": coll,
                        "declared_dim": kb.get("embedding_dim"),
                        "actual_dim": actual,
                        "chunk_count": vector_store.count_chunks(coll),
                    })
            return mismatches
        except Exception as e:
            logger.warning(f"扫描 KB 维度冲突失败: {e}")
            return []

    def _save_local_model_to_config(self, model_name: str) -> None:
        """写回用户目录 config.yaml 的 llm.embedding.local_model 字段。"""
        import yaml
        from src.core.config import USER_CONFIG_PATH
        config_path = USER_CONFIG_PATH
        if not config_path.exists():
            return
        with open(config_path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        emb = raw.get("llm", {}).get("embedding", {})
        emb["local_model"] = model_name
        raw.setdefault("llm", {})["embedding"] = emb
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(raw, f, allow_unicode=True, sort_keys=False)

    def get_embedding_status(self) -> dict[str, Any]:
        """返回当前 embedding 状态。"""
        emb_cfg = settings.config["llm"].get("embedding", {})
        available = emb_cfg.get("available_models", [])
        current = emb_cfg.get("local_model", "")
        cache_size = emb_cfg.get("cache_size", 3)
        embedder = self._get_local_embedder()
        return {
            "mode": self._embed_mode,
            "current_model": current,
            "dimensions": embedder.dimensions,
            "cache_size": cache_size,
            "cached_models": list(embedder._cache_order) if hasattr(embedder, "_cache_order") else [],
            "available_models": [
                {
                    "name": m["name"],
                    "label": m["label"],
                    "size": m["size"],
                    "dimensions": m["dimensions"],
                    "description": m["description"],
                    "is_active": m["name"] == current,
                    "is_cached": m["name"] in (embedder._cache_order if hasattr(embedder, "_cache_order") else []),
                }
                for m in available
            ],
        }

    @retry(
        retry=retry_if_exception_type(LLMError),
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=4),
        reraise=True,
    )
    def chat(
        self,
        messages: list[dict],
        *,
        scene: str = "qa_chat",
        log_meta: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> tuple[str, str]:
        """对话。返回 (回答, 使用的 provider 名)。

        参数：
            scene: 调用场景标签（用于 AI 日志分类）
            log_meta: { session_id, turn_id, kb_id } 关联信息（可选）
        """
        import time as _time
        from src.core.ai_call_logger import log_call as _log_call

        start = _time.time()
        last_err: Exception | None = None
        # 提取 system_prompt 用于日志（messages[0] 是 system 的话）
        system_prompt = None
        if messages and messages[0].get("role") == "system":
            system_prompt = str(messages[0].get("content", ""))

        used_provider: str | None = None
        used_model: str | None = None
        try:
            for name in self._chat_chain:
                try:
                    p = self._providers[name]
                    if not p.usable:
                        continue
                    answer = p.chat(messages, **kwargs)
                    used_provider = name
                    used_model = getattr(p, "model_name", None)
                    # 成功 → 记日志
                    _log_call(
                        provider=name,
                        model=used_model,
                        scene=scene,
                        messages=messages,
                        response_text=answer,
                        system_prompt=system_prompt,
                        duration_ms=int((_time.time() - start) * 1000),
                        success=True,
                        **(log_meta or {}),
                    )
                    return answer, name
                except LLMError as e:
                    last_err = e
                    used_provider = name
                    continue
            raise NoAvailableProviderError(f"所有 chat provider 都失败: {last_err}")
        except Exception as e:
            # 失败也要记日志（用于审计失败原因）
            _log_call(
                provider=used_provider or "unknown",
                model=used_model,
                scene=scene,
                messages=messages,
                response_text=None,
                system_prompt=system_prompt,
                duration_ms=int((_time.time() - start) * 1000),
                success=False,
                error_message=str(e)[:2000],
                **(log_meta or {}),
            )
            raise

    async def chat_stream(
        self,
        messages: list[dict],
        *,
        scene: str = "qa_chat",
        log_meta: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[tuple[str, str], None]:
        """流式对话。逐 provider 尝试，第一个能 yield 的开始流式；中途失败切下一个。

        yield 模式：每个 item 是 (token, provider_name)；provider 名只在首个 token 时非空，
        后续 token 的 provider 字段为空字符串（前端可只看首个识别 provider）。

        参数：
            scene: 调用场景标签
            log_meta: { session_id, turn_id, kb_id } 关联信息
        """
        import time as _time
        from src.core.ai_call_logger import log_call as _log_call

        start = _time.time()
        system_prompt = None
        if messages and messages[0].get("role") == "system":
            system_prompt = str(messages[0].get("content", ""))

        last_err: Exception | None = None
        used_provider: str | None = None
        used_model: str | None = None
        collected_tokens: list[str] = []  # 收集所有 token 流结束后记日志

        try:
            for name in self._chat_chain:
                try:
                    p = self._providers[name]
                    if not p.usable:
                        continue
                    first = True
                    async for token in p.chat_stream(messages, **kwargs):
                        if first:
                            used_provider = name
                            used_model = getattr(p, "model_name", None)
                        collected_tokens.append(token)
                        yield token, (name if first else "")
                        first = False
                    # 流式正常结束 → 记日志
                    full_response = "".join(collected_tokens)
                    _log_call(
                        provider=name,
                        model=used_model,
                        scene=scene,
                        messages=messages,
                        response_text=full_response,
                        system_prompt=system_prompt,
                        duration_ms=int((_time.time() - start) * 1000),
                        success=True,
                        **(log_meta or {}),
                    )
                    return
                except LLMError as e:
                    last_err = e
                    used_provider = name
                    logger.warning(f"provider {name} chat_stream 失败，尝试下一个: {e}")
                    continue
            raise NoAvailableProviderError(f"所有 chat provider 都失败: {last_err}")
        except Exception as e:
            # 流式失败也记日志（response 是已收集的部分）
            partial = "".join(collected_tokens) if collected_tokens else None
            _log_call(
                provider=used_provider or "unknown",
                model=used_model,
                scene=scene,
                messages=messages,
                response_text=partial,
                system_prompt=system_prompt,
                duration_ms=int((_time.time() - start) * 1000),
                success=False,
                error_message=str(e)[:2000],
                **(log_meta or {}),
            )
            raise

    def is_embedding_warm(self) -> bool:
        """冷启动检测：本地 embedding 模型是否秒级可用。

        - API 模式：恒为 True（无冷启动）
        - cache_size=0：恒为 False（每次都要重新加载，每次都是 cold）
        - cache_size>0：当前模型是否在缓存中
        """
        if self._embed_mode != "local":
            return True
        cache_size = settings.config.get("llm", {}).get("embedding", {}).get("cache_size", 3)
        if cache_size == 0:
            return False
        if self._local_embed is None:
            return False
        current_model = settings.config["llm"]["embedding"].get("local_model", "")
        return current_model in self._local_embed._cache

    def embed(self, texts: list[str]) -> tuple[list[list[float]], str]:
        """向量化。返回 (向量列表, 使用的 provider 名)。"""
        if not texts:
            return [], ""
        if self._embed_mode == "local":
            embedder = self._get_local_embedder()
            vecs = embedder.embed(texts)
            return vecs, "local"
        # API 模式
        batch_size = 64
        all_vecs: list[list[float]] = []
        used_provider = ""
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            last_err: Exception | None = None
            for name in self._embed_chain:
                try:
                    p = self._providers[name]
                    if not p.usable:
                        continue
                    vecs = p.embed(batch)
                    all_vecs.extend(vecs)
                    used_provider = name
                    break
                except LLMError as e:
                    last_err = e
                    continue
            else:
                raise NoAvailableProviderError(f"所有 embed provider 都失败: {last_err}")
        return all_vecs, used_provider


# ===== 单例 =====
_client: LLMClient | None = None
_client_lock = __import__("threading").Lock()


def get_client() -> LLMClient:
    """获取 LLMClient 单例（线程安全，double-check lock）。"""
    global _client
    if _client is not None:
        return _client
    with _client_lock:
        # double-check：拿到锁后再检查一次，避免两个线程同时通过外层检查
        if _client is not None:
            return _client
        _client = LLMClient()
        return _client


def reset_client() -> None:
    """重置 client 单例（切换 provider / 测试用）。

    旧 client 的 httpx 连接会被释放（如有 in-flight 则延迟到流结束后）。
    遍历 _providers 时先快照，避免与 LLMClient 内部修改冲突。
    """
    global _client
    with _client_lock:
        old = _client
        _client = None
    if old is not None:
        try:
            # 快照 providers 列表（避免遍历期间 dict changed size）
            providers = list(old._providers.values())
            for prov in providers:
                try:
                    prov._impl.close()
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).debug(f"close provider 失败: {e}")
        except Exception as e:
            import logging
            logging.getLogger(__name__).debug(f"reset_client 关闭旧 client 失败: {e}")


def health_check() -> dict:
    """健康检查：返回 LLM 配置状态。"""
    cfg = settings.config["llm"]
    emb_cfg = cfg.get("embedding", {})
    providers_status = {}
    for name, p in cfg["providers"].items():
        providers_status[name] = {
            "enabled": p.get("enabled", False),
            "has_chat": p.get("chat_model") is not None,
            "has_embedding": p.get("embedding_model") not in (None, False),
            "api_key_configured": bool(settings.resolve_api_key(p)) or name == "local",
        }
    try:
        client = get_client()
        chat_chain = client._chat_chain
        embed_mode = client._embed_mode
        embed_chain = client._embed_chain
    except Exception:
        chat_chain = []
        embed_mode = emb_cfg.get("mode", "api")
        embed_chain = []
    return {
        "status": "configured",
        "current_chat_provider": cfg["chat_provider"],
        "embedding_mode": embed_mode,
        "embedding_local_model": emb_cfg.get("local_model", ""),
        "fallback_chain": cfg["fallback_chain"],
        "effective_chat_chain": chat_chain,
        "effective_embed_chain": embed_chain if embed_mode == "api" else ["local"],
        "providers": providers_status,
    }

"""get_embedding_status 秒回保证：不触发本地模型加载。"""

from src.core import llm_client


def test_embedding_status_does_not_load_model():
    """状态查询不应实例化 embedder（否则会同步加载 bge 模型，接口秒级变数十秒）。"""
    client = llm_client.get_client()
    status = client.get_embedding_status()
    assert client._local_embed is None
    # 维度从 config 的 available_models 兜底取到
    assert status["dimensions"] is not None
    assert isinstance(status["available_models"], list) and status["available_models"]
    assert status["cached_models"] == []

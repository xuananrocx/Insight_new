"""业务异常定义。

区分不同错误类型，便于路由层转换为对应的 HTTP 响应和中文提示。
"""


class EmbeddingDimensionMismatchError(Exception):
    """Embedding 维度与 collection 维度不匹配。

    抛出时携带 collection 名、期望维度、实际维度，便于上层生成中文提示。
    """

    def __init__(self, collection_name: str, expected_dim: int, got_dim: int):
        self.collection_name = collection_name
        self.expected_dim = expected_dim
        self.got_dim = got_dim
        super().__init__(
            f"维度不匹配：collection={collection_name} "
            f"期望 {expected_dim} 维，当前 embedding 模型返回 {got_dim} 维"
        )

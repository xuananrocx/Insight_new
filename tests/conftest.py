"""pytest 配置：测试用临时数据目录 + 单例 reset。"""
import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture(scope="session", autouse=True)
def _isolated_data_dir():
    """测试期间把所有数据目录指到临时目录，避免污染用户数据。

    设置环境变量 AMD_DATA_DIR，所有模块通过 settings.get_path 间接消费。
    """
    with tempfile.TemporaryDirectory(prefix="amd-test-") as tmp:
        tmp_path = Path(tmp)
        os.environ["AMD_DATA_DIR"] = str(tmp_path)
        # 各种子目录
        (tmp_path / "data").mkdir()
        (tmp_path / "logs").mkdir()
        yield tmp_path
        # cleanup 由 TemporaryDirectory 自动做


@pytest.fixture(autouse=True)
def _reset_singletons():
    """每个测试函数前重置全局单例（避免测试间状态污染）。"""
    # reset LLMClient
    try:
        from src.core import llm_client
        llm_client.reset_client()
    except Exception:
        pass
    # reset BM25 module-level _state
    try:
        from src.qa import bm25_index
        bm25_index._state = None
    except Exception:
        pass
    yield

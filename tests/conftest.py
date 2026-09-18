"""pytest 配置：测试用临时数据目录 + 单例 reset。"""
import atexit
import os
import shutil
import tempfile
from pathlib import Path

import pytest

# 必须在模块导入期就设好：src/core/config.py 的 USER_DATA_DIR 是 import 时
# 解析的常量，而测试模块在收集阶段（晚于本 conftest）才 import src。
# 之前只在 fixture 里设置，但 fixture 执行时 src 可能已被 import，导致隔离失效。
_TMP_DIR = tempfile.mkdtemp(prefix="amd-test-")
os.environ["AMD_DATA_DIR"] = _TMP_DIR
(Path(_TMP_DIR) / "data").mkdir()
(Path(_TMP_DIR) / "logs").mkdir()
atexit.register(shutil.rmtree, _TMP_DIR, ignore_errors=True)


@pytest.fixture(scope="session", autouse=True)
def _isolated_data_dir():
    """测试期间所有数据目录指到临时目录，避免污染用户数据。"""
    yield Path(_TMP_DIR)


@pytest.fixture(scope="session", autouse=True)
def _verify_isolation():
    """fail-fast：确认 src 真的吃到了临时目录，否则直接让整个测试会话失败。

    历史教训：曾经 AMD_DATA_DIR 没人读，测试的 DELETE FROM kbs 清空了真实用户库。
    """
    import src.core.config as config_mod
    actual = str(config_mod.USER_DATA_DIR).replace("/", "\\").lower()
    expected = _TMP_DIR.replace("/", "\\").lower()
    assert actual == expected, (
        f"测试隔离失效：USER_DATA_DIR={config_mod.USER_DATA_DIR}，应为 {_TMP_DIR}。\n"
        f"说明 src.core.config 在 AMD_DATA_DIR 设置前就被 import 了，"
        f"继续跑会写穿真实用户数据，已中止。"
    )
    yield


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

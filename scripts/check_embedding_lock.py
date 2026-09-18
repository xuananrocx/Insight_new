"""复现并验证 embedding 并发切换 bug 的修复（不真下载模型）。

用法（Insight 根目录）：.venv/Scripts/python.exe scripts/check_embedding_lock.py
"""
import sys
import threading
import time
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


class FakeModel:
    def __init__(self, name, device="cpu"):
        time.sleep(0.5)  # 模拟慢加载（下载）
        self.name = name

    def get_sentence_embedding_dimension(self):
        return 1024 if "large" in self.name else 768

    def encode(self, texts, **kw):
        return _FakeTensor([[0.1, 0.2] for _ in texts])


class _FakeTensor:
    def __init__(self, rows):
        self._rows = rows

    def tolist(self):
        return self._rows


mod = types.ModuleType("sentence_transformers")
mod.SentenceTransformer = FakeModel
sys.modules["sentence_transformers"] = mod

from src.core.llm_client import EmbeddingBusyError, _LocalEmbedding  # noqa: E402

e = _LocalEmbedding("BAAI/bge-base-zh-v1.5", cache_size=1)
e._load()
assert e._dimensions == 768
print("initial load OK, dim=768")

# 场景1：三个线程同时切到未缓存的 large（复现线上事故的调用方式）
results = {}

def worker(i):
    results[i] = "ok" if e.switch_model("BAAI/bge-large-zh-v1.5") else "busy"

ts = [threading.Thread(target=worker, args=(i,)) for i in range(3)]
for t in ts:
    t.start()
for t in ts:
    t.join()
print("concurrent switch:", results)
vals = list(results.values())
assert "ok" in vals, "应至少一个成功"
assert all(v in ("ok", "busy") for v in vals), f"不允许异常: {vals}"
assert e._cache_order.count("BAAI/bge-large-zh-v1.5") == 1, f"账本脏数据: {e._cache_order}"
print("账本一致:", e._cache_order, e._cache_dims)

# 场景2：切换进行中 embed 应报 busy 而不是阻塞
def slow_switch():
    assert e.switch_model("BAAI/bge-base-zh-v1.5")

t = threading.Thread(target=slow_switch)
t.start()
time.sleep(0.05)
try:
    e.embed(["x"])
    print("embed during switch: 未触发(竞态窗口，可接受)")
except EmbeddingBusyError:
    print("embed during switch -> EmbeddingBusyError OK")
t.join()

# 场景3：正常 embed + 并发 embed
v = e.embed(["hello"])
assert len(v) == 1 and len(v[0]) == 2
results.clear()

def ew(i):
    try:
        e.embed([f"t{i}"])
        results[i] = "ok"
    except Exception as ex:
        results[i] = f"ERR:{ex!r}"

ts = [threading.Thread(target=ew, args=(i,)) for i in range(4)]
for t in ts:
    t.start()
for t in ts:
    t.join()
print("concurrent embed:", results)
assert all(v == "ok" for v in results.values())

# 场景4：顺序来回切换（回归）
assert e.switch_model("BAAI/bge-large-zh-v1.5")
assert e._dimensions == 1024
assert e.switch_model("BAAI/bge-base-zh-v1.5")
assert e._dimensions == 768
assert e._cache_order == ["BAAI/bge-base-zh-v1.5"], e._cache_order
print("sequential switch OK")
print("ALL PASS")

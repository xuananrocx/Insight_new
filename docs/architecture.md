# 架构设计

## 核心原则

### 1. 单一数据访问入口

**Chroma（向量库）只在 FastAPI 进程内被访问。**

原因：Chroma 底层是 SQLite + DuckDB，多进程并发访问同一个数据目录会触发文件锁，导致 `database is locked` 错误。

落地：
- `src/core/vector_store.py` 提供 `get_chroma_client()` 单例
- 这个单例只在 FastAPI 启动时初始化
- Streamlit 前端**永远**通过 HTTP 调用 `/api/v1/knowledge/*`，不直接 import vector_store

### 2. 配置驱动

所有可变行为集中在 `config.yaml`，代码不硬编码业务规则。

例如：程序包解包的白名单/黑名单、功能开关、LLM provider 选择，全部走配置。

### 3. 公网 API 优先，本地部署可切换

LLM 调用通过 `src/core/llm_client.py` 适配层。Provider 配置在 yaml，切换 provider 不改代码。

### 4. 安全优先的默认值

所有高风险功能默认 `false`：
- OCR 默认关
- 自动反哺默认关
- 文件夹实时监听默认关
- Moonshot/OpenAI 默认关

启用前需要 PM 在 `config.local.yaml` 显式打开。

## 模块职责

```
src/
├── main.py                  启动入口（同时启 FastAPI + Streamlit）
│
├── core/                    基础设施层
│   ├── config.py            配置加载（单例）
│   ├── vector_store.py      Chroma 唯一访问点
│   └── llm_client.py        LLM 客户端（多 provider 适配）
│
├── api/                     FastAPI 路由层
│   └── main.py              路由注册、CORS、健康检查
│   （第二步：routes_qa.py, routes_knowledge.py, routes_feedback.py）
│
├── web/                     Streamlit 前端
│   └── streamlit_app.py     通过 HTTP 调 API，不直接碰数据
│
├── knowledge/               知识库流水线
│   ├── scanner.py           文件夹扫描 + hash 增量
│   ├── extractor.py         程序包解包
│   ├── parsers/             各格式解析器（PDF/Word/Excel/...）
│   ├── chunker.py           文本切片
│   └── ingester.py          投喂流水线协调
│
├── qa/                      RAG 问答
│   ├── retriever.py         向量检索
│   ├── generator.py         LLM 生成
│   └── prompts.py           Prompt 模板
│
├── feedback/                反馈与审批
│   ├── queue.py             待审批队列
│   └── approver.py          审批逻辑
│
└── db/                      SQLite 元数据
    ├── models.py            表结构
    └── session.py           连接管理
```

## 数据流

### 投喂流程

```
用户往 ~/Insight_Knowledge_Feeds 丢文件
       │
       │ 手动触发扫描（API: POST /knowledge/scan）
       ▼
[FastAPI 进程]
   │
   ├─ scanner: 遍历文件夹
   │     ├─ 算 hash
   │     ├─ 查 metadata.db: 文件变了吗？
   │     └─ 增量：只处理变化的
   │
   ├─ extractor: 是程序包？
   │     └─ 解压到 data/extracted/<hash>/
   │        按白名单提取文本资源
   │
   ├─ parsers: 按扩展名分发
   │     ├─ PDF → pypdf / pdfplumber
   │     ├─ Word → python-docx
   │     ├─ Excel → openpyxl
   │     └─ ...
   │
   ├─ chunker: 切片（500 字 + 50 重叠）
   │
   ├─ embedding 调用（DeepSeek/Qwen API）
   │
   ├─ 写入 Chroma（chunk + 元数据）
   │
   └─ 更新 metadata.db（status=done）
```

### 问答流程

```
用户在 Streamlit 输入问题
       │
       │ HTTP POST /qa/ask
       ▼
[FastAPI]
   │
   ├─ 把问题 embedding 化
   ├─ Chroma 检索 top-k=5
   ├─ 拼 prompt（问题 + 检索到的 chunks）
   ├─ 调 LLM 生成答案
   └─ 返回：答案 + 引用来源 + chunk_ids
       │
       ▼
Streamlit 渲染对话气泡 + 来源卡片
       │
       │ 用户点"👍 有用"
       ▼
[FastAPI]
   ├─ 把这条 Q&A 写入待审批队列
   └─ 等待 PM 审批
```

### 反哺流程

```
[待审批队列] (SQLite)
       │
       │ PM 在 Streamlit 审批界面操作
       ▼
   接受 / 拒绝
       │ 接受
       ▼
[知识库] 把 Q&A 作为新 chunk 入库
       │
       │ 下次问类似问题
       ▼
   直接命中，回答更精准
```

## 跨平台要点

| 问题 | 解决方案 |
|------|---------|
| 文档解析库系统依赖 | 全部用纯 Python 库（pypdf, python-docx, openpyxl 等） |
| 路径分隔符 | 用 `pathlib.Path`，不硬编码 `/` 或 `\\` |
| 终端编码 | Windows 设 `PYTHONIOENCODING=utf-8`（main.py 自动处理） |
| 文件锁 | Chroma 单进程持有，Streamlit 走 HTTP |
| 进程启动 | `sys.executable` 替代硬编码 `python` |

## 性能边界（预估）

| 指标 | 数值 |
|------|------|
| 内存占用 | 500 MB - 1 GB |
| 启动时间 | < 5 秒 |
| 单文档投喂（10 页 PDF） | < 5 秒 |
| 问答响应（含 LLM 调用） | 2-5 秒 |
| 知识库容量上限 | 10 万 chunks 内无压力 |

## 安全边界

- API key 只从环境变量读，不入仓库
- 默认仅监听 127.0.0.1，不对外暴露
- 上传文件大小限制 50 MB（可配置）
- 程序包解压有跳过列表，避免 zip slip 攻击（第二步实现）

## 不做的事（明确排除）

- ❌ 多用户/权限/SSO（单机工具）
- ❌ 实时日志采集（Phase 2 才做）
- ❌ 模型本地部署（未来可选）
- ❌ 在线模型微调（不必要，反哺闭环替代）
- ❌ 监控告警系统（用现成的，不自己写）

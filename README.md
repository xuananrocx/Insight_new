# Insight

> 基于 RAG 的本地知识库问答系统 · v0.5.0

完全本地化的企业知识助手：文档投喂 → 多 KB 管理 → 向量检索 + BM25 + Rerank → 多 LLM 问答 → 反馈反哺闭环。

---

## 它能做什么

- 📚 **多知识库管理**：创建/导入/导出/重建 KB，每个 KB 独立 collection，可配置默认 KB
- 📂 **批量投喂**：单/批量/扫描/ZIP，SSE 进度 + 失败重试 + 一键清理失败文件
- 💬 **3 种 RAG 策略**：basic（基础检索）/ summary（摘要增强）/ agentic（跨文档概念扩展）
- 🔌 **多 LLM 适配**：OpenAI / Anthropic / GLM / DeepSeek / 内网代理，UI 切换
- 👍 **反馈反哺**：点赞问答进审批队列，PM 审批后写回知识库
- 📊 **AI 日志**：记录 5 个场景的 LLM 调用（prompt/messages/response），可筛选/统计/清理
- 🎨 **多主题 + 渐变背景**：macOS 毛玻璃主题 + 9 种渐变背景
- 🚀 **完全本地化**：Chroma + SQLite + FastAPI + React，无外部依赖

---

## 架构

```
┌────────────────────────────────────────────────────┐
│  浏览器                                              │
│   └─ Streamlit Web UI (8501)                        │
│       └─ 纯前端，不碰数据层                          │
└──────────────────┬─────────────────────────────────┘
                   │ HTTP
                   ▼
┌────────────────────────────────────────────────────┐
│  FastAPI 后端 (8000)                                │
│   ├─ 路由：/api/v1/{knowledge, qa, feedback}        │
│   ├─ Chroma 客户端（唯一持有）                       │
│   ├─ SQLite 元数据（唯一持有）                       │
│   └─ LLM 客户端 → 公网 API 或本地 Ollama             │
└────────────────────────────────────────────────────┘
```

**关键架构约束**：Chroma 底层是 SQLite，不支持多进程并发。所有数据访问只在 FastAPI 进程内，Streamlit 通过 HTTP 调用。

---

## 安装与运行

### 1. 准备 Python 环境

要求 Python **3.11+**（3.12 也可以；3.13 可能部分库未适配）。

```bash
# Mac (用 Homebrew)
brew install python@3.11

# Windows
# 去 python.org 下载 3.11+，安装时勾选 "Add to PATH"
```

### 2. 克隆并初始化

```bash
git clone <你的 GitHub 仓库地址> amd-ai-assistant
cd amd-ai-assistant

# 创建虚拟环境（每个平台各做一次）
python3.11 -m venv .venv

# 激活
# Mac:
source .venv/bin/activate
# Windows PowerShell:
.venv\Scripts\Activate.ps1

# 装依赖
pip install -r requirements.txt
```

### 3. 配置 API Key

```bash
cp .env.example .env
# 编辑 .env，填入至少一个 chat provider 和一个 embedding provider 的 key
```

**最低配置**：
- `DEEPSEEK_API_KEY`：用于对话（注册 platform.deepseek.com，充 10 元够用很久）
- `DASHSCOPE_API_KEY`：用于 embedding（注册 dashscope.aliyun.com，有免费额度）

### 4. 准备投喂文件夹

```bash
# 默认在 ~/AMD-Knowledge-Feeds
mkdir -p ~/AMD-Knowledge-Feeds

# 把 AMD 文档拷进去
cp /path/to/AMD-*.pdf ~/AMD-Knowledge-Feeds/
cp /path/to/amd-release-notes.md ~/AMD-Knowledge-Feeds/
# 也可以拷整个压缩包，会自动解压
cp /path/to/amd-3.5.2.tar.gz ~/AMD-Knowledge-Feeds/
```

### 5. 启动服务

```bash
# 同时启动 FastAPI + Streamlit
python -m src.main

# 或分别启动
python -m src.main --api-only     # 只起 FastAPI
python -m src.main --web-only     # 只起 Streamlit
```

打开浏览器：
- Web UI: http://localhost:8501
- API 文档: http://localhost:8000/docs

---

## 使用流程

### 第一次使用

1. 启动服务后打开 Web UI
2. 进入 **📚 知识库** 标签页
3. 点击 **🔄 扫描投喂文件夹**
4. 等待扫描完成（首次会 embedding 所有文档，需要时间）
5. 进入 **💬 问答** 标签页提问

### 日常使用

- 文档有更新？丢到投喂文件夹，再点一次扫描（增量更新，只处理变化的文件）
- 答案有用？点 **👍 有用** → 进入待审批队列
- 进入 **✅ 审批** 标签页 → 通过/拒绝 → 通过的 Q&A 会写回知识库，下次同类问题命中

---

## 配置说明

主配置在 `config.yaml`。覆盖方式：创建 `config.local.yaml`（已 gitignore）。

```yaml
# config.local.yaml 示例：自定义投喂路径
paths:
  feed_folder: /Users/xuanan/Documents/AMD-Docs
```

常用配置项：
- `llm.chat_provider` / `llm.embedding_provider`：切换 LLM
- `feature_flags.feedback_loop.auto_feedback.enabled`：开启自动反哺（默认关）
- `ingest.chunking.chunk_size`：调整切片大小
- `qa.top_k`：检索多少条上下文

---

## 跨机器开发（Mac/Windows）

代码入仓库，数据不入。

**不同步**（已 gitignore）：
- `.venv/`：每台机器各自建
- `data/`：向量库、解压文件、SQLite
- `.env`：API key
- `config.local.yaml`：本地覆盖

**同步**：所有 `src/`、`config.yaml`、`prompts/`、`README.md`、`requirements.txt`

```bash
# 切换机器的工作流
# Mac → Win：
git add . && git commit -m "WIP" && git push

# Win：
git pull
python -m venv .venv        # 第一次需要
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m src.main          # 首次会重新 embedding 文档（投喂文件夹需自己同步）
```

**投喂文件夹同步建议**：放到 iCloud Drive / OneDrive / 坚果云里，让云盘同步文档原文件（不大）。修改 `config.local.yaml` 指向云盘路径即可。

---

## 支持的文件类型

**文档**：pdf, docx, xlsx, xlsm, pptx, html, htm, xml
**文本/配置**：md, txt, rst, log, json, yaml, yml, ini, conf, cfg, toml, properties, csv, tsv, env
**代码**：py, sh, bash, js, ts, go, java, c, cpp, h, hpp, sql
**压缩包**：tar.gz, tar.bz2, tar.xz, tar, tgz, tbz2, txz, zip

不支持的（自动跳过）：图片、视频、音频、加密文件、二进制可执行文件、扫描版 PDF（OCR 默认关）。

---

## 项目结构

```
amd-ai-assistant/
├── src/
│   ├── main.py                  # 启动器
│   ├── api/                     # FastAPI 后端
│   │   ├── main.py
│   │   ├── routes_knowledge.py
│   │   ├── routes_qa.py
│   │   └── routes_feedback.py
│   ├── core/                    # 核心模块
│   │   ├── config.py            # 配置加载
│   │   ├── llm_client.py        # 多 provider LLM 客户端
│   │   └── vector_store.py      # Chroma 客户端（唯一持有）
│   ├── knowledge/               # 知识库
│   │   ├── parsers/             # 文档解析器（一格式一文件）
│   │   ├── chunker.py           # 文本切片
│   │   ├── ingestion.py         # 摄入流程
│   │   └── package_extractor.py # 压缩包解压
│   ├── qa/
│   │   └── rag.py               # RAG 问答
│   ├── feedback/
│   │   └── approval.py          # 反馈与审批
│   ├── db/
│   │   └── metadata_db.py       # SQLite 元数据
│   └── web/
│       ├── streamlit_app.py     # Streamlit 前端
│       └── api_client.py        # HTTP 客户端
├── data/                        # 运行数据（gitignored）
├── config.yaml                  # 主配置
├── .env.example                 # 环境变量模板
├── requirements.txt
└── README.md
```

---

## 常见问题

### Q: 启动后 Web UI 提示 "API 不可用"
A: 先单独跑 `python -m src.main --api-only` 看 FastAPI 是否能起来，常见原因：
- `.env` 没配好（API key 缺失）
- 端口被占用（改 config.yaml 的 api_port）
- 依赖未装全（重新 `pip install -r requirements.txt`）

### Q: 扫描失败，提示 "没有可用的 LLM provider"
A: `.env` 里 embedding provider 的 key 没配。当前 `embedding_provider: qwen`，必须配 `DASHSCOPE_API_KEY`。

### Q: 想用 OpenAI 而不是 DeepSeek？
A: 编辑 `config.yaml`：
```yaml
llm:
  chat_provider: openai
  embedding_provider: openai
  providers:
    openai:
      enabled: true   # 改这里
```
然后 `.env` 里配 `OPENAI_API_KEY`。

### Q: 数据想完全清空重来？
A: 删 `data/` 目录后重启。
```bash
rm -rf data/ && python -m src.main
```

---

## License

私有项目，仅限内部使用。

# Insight 开发交接指南

这份文档面向第一次接手 Insight 的 AI、开发人员和代码审查者，说明当前主线代码的入口、数据边界、验证方式和协作规则。产品行为以源码和当前测试为准；历史设计文档只用于解释背景，不能直接当作现状。

## 接手任务的阅读顺序

1. `AGENTS.md`：当前会话的排查、修改、授权和沟通规则。
2. `README.md`：产品能力、本地启动、配置和部署概览。
3. 本文：代码入口、调用链、数据和开发协作规则。
4. `docs/architecture.md`：架构背景；其中 Streamlit、旧 API 或单机工具描述可能过时，冲突时以本文和源码为准。
5. `docs/retrieval-modes.md`、`docs/knowledge-tool-usage.md`、`docs/accounts.md`：检索、AI 工具和权限行为。
6. `docs/release-process.md`、`docs/selective-build.md`、`deploy/linux/README.md`：构建、候选包和 Linux 部署。

不要先读取用户数据目录、模型目录或服务器密钥。开发和测试必须使用独立数据目录，不能写入真实知识库。

## 项目边界

Insight 是 React + TypeScript 前端、FastAPI 后端和本地 SQLite/Chroma 数据组成的单应用。开发后端默认使用 8000 端口，前端 Vite 使用 5173；Linux 测试部署使用宿主机 Nginx 8088 和内部应用 8888。版本唯一来源是 `src/__init__.py` 的 `__version__`，当前为 `0.1.1`。

仓库不提交 API 密钥、账号密码、用户数据、知识库原文、SQLite 数据库、向量模型、虚拟环境和正式产物。`config.yaml` 是模板，运行时配置和数据位于 `AMD_DATA_DIR` 指定的用户目录；`config.local.yaml` 只放本地覆盖并保持在 gitignore 中。

## 代码地图

```text
src/
├── main.py                 开发启动入口，支持 --api-only
├── api/                    FastAPI 路由和请求权限
│   ├── main.py             应用、CORS、健康检查和路由注册
│   ├── routes_qa.py        问答、流式 SSE、会话上下文入口
│   ├── routes_knowledge.py 文件、上传、导入和进度
│   ├── routes_kbs.py       知识库、授权和概览
│   ├── routes_accounts.py  用户、角色和 API 凭据
│   ├── routes_permissions.py 菜单和功能权限
│   ├── routes_indexes.py   阅读索引和重建任务
│   └── routes_ai_logs.py / routes_logs.py 日志查询
├── core/                   配置、账号、模型、日志和基础设施
├── db/                     SQLite 元数据、账号、授权和会话持久化
├── knowledge/              解析、切片、向量入库、摘要和阅读索引
├── qa/                     检索、增强 AI、深度 AI、工具和上下文
├── feedback/               反馈和审批队列
└── web/                    旧 Streamlit 入口；当前产品界面是 frontend/

frontend/src/
├── pages/                  页面级功能
├── components/             对话、设置、知识库和管理组件
├── stores/                 会话、认证和界面状态
├── hooks/                  API 与交互逻辑
└── lib/                    API 客户端、引用渲染、导航和格式化工具
```

## 一次提问的调用链

1. React 通过 `frontend/src/lib/api.ts` 调用 `/api/v1/qa/ask_stream`。
2. `src/api/routes_qa.py` 校验登录、知识库授权、模式和请求参数，并准备会话上下文。
3. `src/qa/rag.py` 按模式分流：基础检索使用检索器，增强 AI 进入 `enhanced_ai.py`，深度 AI 进入 `tool_agent.py` / `deep_ai.py`。
4. `src/qa/retrieval.py`、`canonical_search.py`、`field_search.py`、`indexed_reading.py` 等工具读取 Chroma、BM25 和阅读索引。工具提供资料和调阅能力，不替 AI 写死最终结论。
5. `src/core/llm_client.py`、`account_llm.py` 和 provider 适配器调用模型；`src/core/api_retry.py` 统一处理重试并写入 AI 调用日志。
6. 后端按 SSE 发送思考、工具、引用和答案事件，前端负责折叠、引用链接、气泡和滚动展示。

知识库事实必须能追溯到资料和引用。允许模型结合通用知识、推断、可能原因和验证方法，但推断必须明确标注，不能冒充知识库事实。

## 导入和索引调用链

上传后，`routes_knowledge.py` 将任务交给 `knowledge/batch_upload.py` 和 `knowledge/ingestion.py`。解析器在 `knowledge/parsers/`，切片由 `chunker.py` 完成，向量写入由 `core/vector_store.py` 统一持有。`metadata.db` 保存文档状态、哈希、任务和引用关系；阅读索引由 `reading_index.py`、`index_tasks.py` 和 `rebuild.py` 维护。

取消导入是协作式的：任务在安全边界检查取消标志，清理未完成临时内容，保留已生效索引。不要在请求线程直接删除整个知识库目录，也不要让第二个进程并发打开同一 Chroma/SQLite 数据目录。

## 本地开发与验证

Windows：`py -3.11 -m venv .venv`，然后运行 `.venv\Scripts\python.exe -m pip install -r requirements.txt` 和 `cd frontend; pnpm install --frozen-lockfile`。启动后端使用 `.venv\Scripts\python.exe -m src.main --api-only`。

Linux/macOS：`python3.11 -m venv .venv`、`source .venv/bin/activate`、`python -m pip install -r requirements.txt`，再运行 `cd frontend && pnpm install --frozen-lockfile` 和 `python -m src.main --api-only`。

另开终端运行 `cd frontend && pnpm dev`，访问 `http://localhost:5173`；API 文档为 `http://localhost:8000/docs`。构建后可执行 `pnpm --dir frontend build`，再启动 FastAPI 提供同源页面。

测试隔离由 `tests/conftest.py` 自动设置临时 `AMD_DATA_DIR`，不得绕过它直接操作真实用户目录。常用检查是 `python -m pytest`、`python -m ruff check src tests scripts`、`python scripts/check_release.py`、`pnpm --dir frontend lint` 和 `pnpm --dir frontend build`。

## 修改边界与协作规则

- “排查/讨论”阶段只读；复杂批量修改、数据操作和服务重启先列计划并确认范围。
- 先定位调用链和现有测试，再改代码。一个任务保持单一范围，前端布局、问答策略、数据迁移和部署不要混在同一提交。
- 新功能沿用现有路由、权限检查、日志和配置入口，不在组件中硬编码权限或 API 地址。
- 涉及 SQLite schema、Chroma、模型目录、会话格式或部署数据时，先写兼容性和回退方案。
- 不用多个 worker 访问同一数据目录；不在生产服务器重新构建源码。
- AI 工具只提供知识库调阅能力，不替 AI 写死分析结论；工具说明必须和实际参数及权限一致。
- 不用修改版本号掩盖构建问题。版本变更必须单独确认，并运行 `scripts/check_release.py`。
- 缺少配置、日志或关键路径时先索要，不猜测。

提交前至少运行 `git status --short`、`git diff --check` 和相关测试。提交说明使用 `fix:`、`feat:`、`docs:`、`build:` 等清晰前缀；不要提交 `.env`、密钥、模型、数据库和临时产物。交接时说明改动范围、验证命令、未验证风险和候选提交 SHA。

## 文档入口

- [文档索引](README.md)
- [检索模式](retrieval-modes.md)
- [知识库工具使用说明](knowledge-tool-usage.md)
- [会话上下文](conversation-context.md)
- [账号与 API 管理](accounts.md)
- [配置说明](configuration.md)
- [发布流程](release-process.md)
- [按平台构建候选包](selective-build.md)
- [Linux 部署说明](../deploy/linux/README.md)

### 系统默认主题与个人偏好

系统设置 → 界面与交互中的默认主题保存在用户配置 `interface.default_theme`，缺省为 `minimal`。`GET/PUT /api/v1/settings/interface` 沿用 `system.view/system.edit` 权限；公开的 `/api/v1/auth/status` 仅额外返回主题标识供登录页和普通用户使用，不返回配置全文。

主题优先级为当前账号在此浏览器的显式选择 → 系统默认 → 简约灰白。个人主题按账号保存，选择“使用系统默认”会取消个人覆盖。旧的浏览器级主题仅迁移给升级后首个登录账号；历史版本无法区分自动写入与主动选择，因此保留已有值。无需数据库迁移，回退后端时旧前端/接口仍可运行，新前端遇到缺少默认主题字段时使用简约灰白。

灰白主题强调色由 `interface.default_accent` 控制，缺省 `graphite`；仅接受 graphite、blue、slate、terracotta、moss、stone。个人强调色以 `amd-ui-accent:<账号ID>` 保存在浏览器，独立于主题选择，清除个人颜色后跟随系统值。旧客户端省略颜色字段时不会覆盖已保存颜色；无需数据库迁移。

### Everforest 主题兼容

Everforest 森林替换 Linear 紫黑，采用 Everforest Dark Medium 配色与较大控件圆角。前端个人偏好和首屏缓存中的 `linear` 映射到 `everforest`；后端继续接受旧 `linear` 值，读取时返回 `everforest`。新主题可作为系统默认，个人优先级不变，不做数据库迁移。回退到旧版本前，将系统默认主题改为旧版本支持的主题；新个人偏好在旧客户端可能回退为默认主题。

# 只读知识 MCP 接入

Insight 提供授权知识的查找、搜索、结构导航与原文阅读。外部 Agent 自己处理任务、上下文、证据汇总及输出文档；MCP 不创建聊天会话、不调用问答 Agent、不生成答案，也不提供写入、删除、授权修改或联网搜索工具。

## 结构与兼容性

```text
Claude Code / Codex / 其他 MCP 客户端
    │ Streamable HTTP + 个人 Bearer 令牌
    ▼
独立 MCP SDK 网关（默认 127.0.0.1:8001/mcp）
    │ HTTP + 同一令牌，每次重新鉴权
    ▼
原 FastAPI 进程 /mcp-service（独立 Bearer 入口）
    │ 薄适配层，复用 KnowledgeTools 的查询/阅读实现
    ▼
原权限系统、SQLite、阅读索引、检索能力
```

网关使用官方 Python MCP SDK 1.30.0、独立虚拟环境，避免其 Pydantic >=2.11 要求改变主应用固定的 2.10.4 依赖。网关不导入主应用配置、数据库、向量库或模型，不开第二个 SQLite/Chroma 数据进程。原 `src/qa/knowledge_tools.py` 及聊天调用链保持原样；MCP 适配层复用原文阅读，独立实现受限扫描和候选融合，不要求用户配置聊天 API。

目录、精确搜索、已索引原文阅读不需要聊天 API。语义/混合检索仍依赖系统已有的 embedding 与检索设施，不承诺脱离这些设施离线完成语义检索。

MCP SDK 保留最多 128 个短期**协议连接**（空闲 300 秒回收），用于初始化、取消通知和客户端兼容；它不保存聊天历史、推理过程或知识证据累计。连接与令牌绑定，换令牌必须重新建立连接；网关重启后客户端重新连接。

## 配置与权限

- 系统设置 → **MCP 接入**：总开关、公开接入地址、独立查询限额、所有令牌摘要和调用记录。修改后点击“保存 MCP 配置”生效；不自动启动/停止进程。查看要求 `system.view`，修改/管理员撤销要求 `system.edit`。
- 个人设置 → **MCP 接入**：创建自己的令牌、选择知识库、撤销和查看客户端配置示例。创建及查询要求新权限 `mcp.use`；失去此权限后仍可撤销自己的令牌。
- 默认关闭；已有普通角色不会自动获得 `mcp.use`。超级管理员随权限目录初始化获得该权限；现有自定义/管理员角色需要显式分配。新建系统的内置管理员角色按现有机制包含完整权限目录。
- 令牌最多选择 100 个当前可读知识库。支持全选、清空选择；搜索后全选只追加当前结果，保留其他已选项，超过 100 个时明确提示且不改变选择。新增知识库不会自动加入旧令牌。知识库可读范围 = 令牌范围 ∩ 用户当前范围，管理员身份也不能绕过。
- 有效期默认 30 天，可选 7 / 30 / 90 / 365 天或「永久有效」。每用户最多 20 个有效令牌，永久令牌也计入额度；永久仅表示不按时间到期，撤销、账号和权限检查保持生效。
- 令牌只在创建成功后返回一次，数据库只保存 SHA-256 和展示前缀；浏览器不持久化明文，不把明文存入查询/变更缓存。请给每个客户端使用独立令牌。
- 创建成功弹窗提供访问令牌和接入地址的独立复制按钮；兼容 HTTP 下的复制，浏览器阻止复制时可在文本框中选中后手动复制。
- 总开关关闭、令牌到期/撤销、用户停用/待改密码、角色权限或知识库权限撤回，均在后续请求和处理中的权限检查处生效。已被客户端读走的内容不会被远程删除。
- 密码重置后的“必须改密”状态会阻止令牌访问；完成改密后，未撤销、未过期令牌仍有效。如需永久停止已有接入，请撤销令牌。

设置单独保存到 SQLite `app_state.mcp_settings`，不混用模型/API/AI 运行配置。公开地址仅生成客户端示例，网关监听及后端地址由环境变量配置。

### 永久令牌的兼容和回退

创建接口 `POST /api/v1/account/mcp/tokens` 的 `days: null` 表示永久有效，省略该字段仍为 30 天；有限期要求严格整数 1–365，`0` 不表示永久。创建、个人列表和管理员列表的 `expires_at: null` 表示永久有效，有限期仍返回 Unix 秒数。

本次无需数据库结构迁移，永久令牌在原 `expires_at INTEGER NOT NULL` 列中以 `-1` 保存，已有有限期令牌不变。回退到不支持永久令牌的旧服务时，永久令牌会被判为失效，需升级回来或重新创建有限期令牌；不会绕过鉴权。令牌永久有效不改变查询引用、分页快照和协议连接的独立有限有效期。

| 配置 | 默认 | 范围 / 含义 |
|---|---:|---|
| 单次查询超时 | 30 秒 | 5–120 秒 |
| 最大并发 | 1 | 正整数，无固定数值上限；单个主应用进程内所有 MCP 用户共用；界面提供 1 / 4 / 8 / 16 快捷值和自定义输入 |
| 每账号每分钟调用数 | 30 | 1–120，多个令牌共享 |
| 单次输出上限 | 32,000 字符 | 16,000–64,000，查询结果 envelope 上限 |
| 引用有效期 | 30 分钟 | 5–120 分钟，新生成的引用适用当前值 |

繁忙直接返回可重试错误，不积压队列。底层同步读取或 embedding 不能被 Python 强行终止：客户端取消/超时后协作式停止后续处理，正在运行的底层操作退出前继续占用并发槽。MCP 配额不改变原问答配额，但复用的检索/磁盘仍共享机器资源，默认 1 并发以减少影响。主应用继续使用现有的单 worker 部署约束。

工具使用 MCP 专用线程池并行执行，按实际请求懒创建线程，容量跟随最大并发配置，不使用 asyncio 公共线程池的默认容量。保存配置后后续请求即时使用新额度；调低时不会终止在途任务，在途任务（包括旧线程池中的任务）继续计入总数，直到低于新额度才接收新调用。闲置旧线程池退出。执行线程复制请求上下文，保持账号、权限和请求标识隔离。配置值不保证硬件吞吐量，底层模型锁、CPU 和磁盘仍可能限制实际速度。网关 HTTP 连接池不另设活动连接数上限，空闲连接单独回收；协议连接数和请求频率限制仍独立生效。

## 工具及返回边界

首版提供 11 项工具：

| 工具 | 用途 |
|---|---|
| `list_knowledge_bases` | 当前用户与令牌均可读的知识库 |
| `get_kb_overview` | 知识库背景和目录入口 |
| `list_documents` | 按文件名查找、分页 |
| `search_knowledge` | 全库搜索 |
| `search_document` | 文档内搜索 |
| `get_document_outline` | 章节、对象、工作表目录 |
| `search_scope` | 指定章节/对象/工作表内搜索 |
| `get_object_members` | 对象成员目录 |
| `read_document` | 原文阅读和相邻范围导航 |
| `read_table_rows` | 表格原始行读取 |
| `continue_search` | 固定搜索快照续页 |

除列知识库外，每次显式传 `kb_id`；内部知识工具的旧参数兼容形状不向 MCP 暴露，也不接受绕过引用的旧阅读参数。搜索 `limit` 为 1–20，请按返回的续页入口继续。

返回来源包括文档编号、名称、版本、章节/页面/坐标、阅读及覆盖说明。原文和概览不是系统指令，也不表示经过结论核验。`citation` 编号仅在本次调用内有意义；`coverage` 表示本次范围（扫描续接的累计计数另有明确字段），不代表答案完整性。稳定的 `source_id` / `evidence_id` 可用于跨调用关联，外部 Agent 自行合并证据、已读范围和上下文。没有累计证据包、问答历史、研究记录或对话预算。

`read_ref`、`scope_ref`、`next_cursor` 是令牌和知识库绑定的随机短期入口。服务端只保存实现跨调用导航必需的引用描述/分页快照；不要求重放前面的对话。每次继续使用都会重新验证权限和文档/索引版本。文档更新、重建索引、过期或容量回收会明确失败，Agent 应重新获取目录/搜索。列表/目录使用 `next_offset`，文档读取使用 `next_read_ref`/`next_section_ref`，搜索优先使用 `continue_search`。

引用存储限制：单条快照不超过 8 MB；每令牌最多 1,000 个入口、32 MB；全局 64 MB。生成新入口时清理过期项和旧入口，返回全文序列化超限则提示缩小范围。已返回入口可能提前被容量回收，不承诺永久链接。

独立调用记录只保存用户/令牌编号、工具、知识库编号、状态、耗时和时间；不保存查询参数/正文/明文令牌。最多 30 天、10,000 条完成处理的调用。未进入执行的鉴权失败或限流不属于该表。主应用原有工具阶段日志仍按原配置运行。

系统设置的 MCP「调用记录」支持按用户名或显示名称模糊筛选。筛选与总条数、分页在服务端统一计算，覆盖所有已保留记录，重置后恢复全部用户；查看权限仍为 `system.view`。

## 网关部署（仅示例，不会自动操作环境）

Linux 正式交付优先使用统一版本包：应用、网关、代理镜像一起校验，按 Compose 或 host 模式统一升级与回退，见 [Linux 部署说明](../deploy/linux/README.md)。host 模式可接管原主应用容器及宿主机 Nginx；不要求在目标机安装 SDK 或 Python 3。以下独立虚拟环境方式保留用于源码开发与自行维护的部署，不应与统一部署同时启动同名/同端口网关。

从与主应用**同一固定提交**的已校验源码/候选包部署。在应用根目录创建独立 Python 3.11 环境，切勿安装进主应用 `.venv`：

```bash
python3.11 -m venv .venv-mcp
.venv-mcp/bin/python -m pip install -r deploy/mcp/requirements.lock.txt
INSIGHT_BACKEND_URL=http://127.0.0.1:8000 \
INSIGHT_MCP_PORT=8001 \
.venv-mcp/bin/python -m src.mcp.gateway
```

`requirements.txt` 固定直接依赖，`requirements.lock.txt` 固定本轮 macOS Python 3.11 验证的完整依赖解析；未声称已经验证 Linux/Windows 安装和镜像。主应用原 `requirements*.txt` 不变。生产部署沿用仓库候选产物和目标平台验收规则。

| 网关变量 | 默认 | 说明 |
|---|---|---|
| `INSIGHT_BACKEND_URL` | `http://127.0.0.1:8000` | 原服务 HTTP 或 HTTPS origin，不带路径；支持本机、内网 IP 和域名，按配置协议连接 |
| `INSIGHT_MCP_BIND` | `127.0.0.1` | 默认不对局域网开放 |
| `INSIGHT_MCP_PORT` | `8001` | 网关端口 |
| `INSIGHT_MCP_ALLOWED_HOSTS` | `127.0.0.1:*,localhost:*,[::1]:*` | 逗号分隔；反向代理必须填实际 Host，支持 SDK 的端口通配形式 |
| `INSIGHT_MCP_ALLOWED_ORIGINS` | 空 | 默认不接受浏览器跨站 Origin；CLI/桌面 Agent 一般不带此头 |

主应用另外接受部署变量 `INSIGHT_MCP_HEALTH_URL`，指向内部网关 `/health`（Compose 为 `http://mcp:8001/health`，host 模式为 `http://127.0.0.1:8001/health`）。仅系统查看权限可读 `/api/v1/admin/mcp/status`；不接受用户输入的探测 URL，不使用 `public_url` 发请求，不跟随跳转、不传令牌。状态页分别展示部署状态与访问开关：未配置检测不能推断为网关未运行，内部就绪也不能替代客户端到公开地址的验证。

网关 `/health` 返回组件标识、版本及主应用连通状态，主应用不可达时为 503，业务接入关闭时仍可就绪。统一代理公开 `/mcp-health` 映射此端点，启动时核对版本并验证 `/mcp` POST 返回网关的 Bearer 鉴权响应，避免把网页兜底误判为 MCP。真正的有令牌握手/工具查询在候选流水线的临时空数据环境验证，不自动创建生产令牌。

接入地址和网关后端地址均支持 HTTP / HTTPS，不按本机或远程限制 HTTP，也不自动升级协议。公开接入地址只生成客户端配置，不会配置网关监听、Host 白名单、端口或反向代理；地址可为 `http://172.19.211.176:8001/mcp` 或 HTTPS 域名。HTTP 会明文传输令牌和内容，部署方自行选择协议和网络范围。请求体上限 16 KiB。可参考同目录 `insight-mcp.service.example` 和 `nginx-location.conf.example`，按实际运行用户/路径/主服务端口修改；HTTP 非默认端口同样需要加入实际 Host 白名单。`/mcp-service` 是 Bearer 保护的内部适配地址，代理应对公网屏蔽；同机网关可通过 loopback 访问它。

网关使用 JSON 形式 Streamable HTTP，不支持旧 SSE transport 或本地 stdio 命令。可选的独立 GET/SSE 流返回 405；工具调用走 POST，协议 DELETE 关闭连接。MCP 取消通知可取消正在等待的后端 HTTP 调用；关闭整个客户端连接也会停止后续等待。具体客户端需要实际发送取消或断开在途 HTTP 请求，单纯关闭一个设置窗口不保证如此。

纯内网无需访问互联网，只需 Agent 能访问部署的 MCP 网关；用本地安全隧道时，客户端仍可配置 localhost HTTP。

### 并发与协议调整的兼容和回退

默认并发仍为 1，已保存配置不自动放大，无数据库结构迁移，不改变原网页问答的执行池或限额。新服务继续读取旧配置；新前端可填写任意正整数。回退到限制为 1–4、远程仅 HTTPS 的旧版本前，先把最大并发改回 1–4，把接入地址改为旧版支持的 HTTPS/回环 HTTP 或留空；如使用远程 HTTP 后端，也需先调整网关环境变量。保留 Bearer 鉴权、知识库授权、Host/Origin 校验和请求体上限。

## 客户端示例

先在个人设置创建令牌，并在启动客户端的环境里设置 `INSIGHT_MCP_TOKEN`。示例中不包含真实令牌，也不自动修改客户端配置。

Codex `config.toml`：

```toml
[mcp_servers.insight]
url = "https://knowledge.example.com/mcp"
bearer_token_env_var = "INSIGHT_MCP_TOKEN"
tool_timeout_sec = 135
```

Claude Code `.mcp.json`：

```json
{
  "mcpServers": {
    "insight": {
      "type": "http",
      "url": "https://knowledge.example.com/mcp",
      "headers": { "Authorization": "Bearer ${INSIGHT_MCP_TOKEN}" }
    }
  }
}
```

重启/重新加载客户端后，先调用 `list_knowledge_bases`，再目录/搜索 → 读取原文 → 按引用续页。令牌失效时重新创建和配置；协议连接失效时重新连接。首版为个人 Bearer 令牌，不包含 OAuth 自动注册/浏览器登录流程，适用于支持自定义 Bearer 头的客户端。

Claude Code 官方文档已读取；当前执行环境读取 Codex 官方页面返回 HTTP 403，Codex 配置示例尚未在实际 Codex 客户端安装验证。已用官方 Python SDK 完成协议及真实查询链路验证，不能将它等同于所有客户端版本均已实测。

## 验证、启动影响与回退

启动升级后的主应用时，仅新增 `mcp_tokens`、`mcp_references`、`mcp_calls` 三张表及索引，并更新权限目录；不改原知识数据、索引格式或聊天记录。默认接入关闭，不自动创建任何人的令牌。

2026-11-04 本轮验收：全量后端 833 项通过，前端回归 39 项通过；新增/改动前端文件 lint、构建、Python Ruff、版本和 diff 检查通过。全项目前端 lint 存在原有 38 个错误和 7 个警告，未混入本次范围。官方 SDK 协议检查和真实查询链路检查均通过；未安装实际 Agent 客户端。

本轮验证使用临时 `AMD_DATA_DIR`，不操作真实数据：

```bash
.venv/bin/python -m pytest tests/test_mcp_access.py -q
# 单独 SDK 环境：协议、并发身份、取消、连接归属、撤销、Host/Origin、防超大请求
.venv-mcp/bin/python -m tests.mcp_gateway_check
# 全量后端回归和前端构建
.venv/bin/python -m pytest
cd frontend
npm run build
```

可选真实链路验收（自动新建并在退出时删除临时数据，绝不读取真实知识库；固定端口 18000，请确保空闲）：

```bash
.venv/bin/python -m tests.mcp_review_server /tmp/insight-mcp-review-info.json
# 另一个终端，在同一仓库根目录执行；info 中是一次性测试令牌，不要归档或提交
.venv-mcp/bin/python -m tests.mcp_live_check /tmp/insight-mcp-review-info.json
```

验收页面为 `http://127.0.0.1:18000/settings?section=mcp`，账号 `mcp-review`，密码 `temporary-review-password`。仅限该全新临时实例；SDK 检查通过 ASGI 运行网关并访问真实 HTTP 后端，不要求另开 18001 进程。UI 中的 18001 示例地址仅作为测试展示。

回退时关闭 MCP 接入并停止独立网关，主应用可以回到原版本；新增表和独立配置保留但旧版本不使用，不需要删除知识数据。原内部知识工具无改动；这不意味着共享机器资源完全隔离。尚未验证真实 Claude Code/Codex 客户端、Linux 网关部署、生产负载和真实语义 embedding 调用，本轮不部署、不提交或推送。

官方参考：

- [Python MCP SDK](https://github.com/modelcontextprotocol/python-sdk)
- [Streamable HTTP 传输规范](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports)
- [Claude Code MCP](https://code.claude.com/docs/en/mcp)
- [Codex MCP](https://developers.openai.com/codex/mcp)

## 2026-11-12 续验记录

预算优化已单独提交为 `fb04b87`，MCP 工作仍独立留在工作区。

- 与预算改动一起运行的全量后端回归：851 passed；前端回归 39 passed，构建通过。
- 再次通过官方 SDK 协议检查：初始化、工具列表/调用、并发身份、错误、取消/断连、连接归属、撤销、鉴权、Host/Origin 和请求体上限。
- 扩充真实 HTTP 后端验收：10 个工具涵盖知识库列表、概览、文档列表、全库精确搜索、文档搜索、目录、原文阅读、范围搜索、搜索续页和对象成员。使用全新临时知识库，没有调用聊天 API。表格工具仍由已有后端阅读测试覆盖，未加入此次 SDK 端到端样本。
- MCP 源码及验收脚本 Ruff、diff 检查通过。临时后端在验证后关闭。
- 尚未验证：真实 Claude Code/Codex 客户端接入、真实语义 embedding、Linux 部署和生产负载。本轮不自动配置客户端，不创建真实用户令牌，不操作 176/224。

## 本地人工验收

2026-11-12 用户反馈 MCP 测试通过，授权提交首版。具体客户端版本及测试覆盖未记录，不据此扩大为所有客户端兼容承诺。当前本地 8000 使用 `Insight-local/start-local.sh` 与原 `Insight-local/dev-data`，8001 为独立网关；继续开发应复用该脚本，避免误用系统默认数据目录。


## MCP 合约 v2：Agent 查询质量优化（2026-11-12）

本节更新首版行为；上面的历史验收记录不代表本轮新增能力已在真实客户端验证。11 个工具名保留，现有必填参数不增加，新增字段为加法兼容。网关初始化返回 Insight 应用版本，SDK 版本不再误作应用版本。

### 模型可执行的返回契约

每个结果包含 `contract_version="2"`、`request_id`、`outcome`、`coverage`、`continuations`。`outcome` 为 ok / partial / no_match / error；partial 中的已有证据可用，不被 SDK 当成整次失败。每项 continuation 给出 `kind`、`tool`、`arguments`，客户端可直接按参数继续，不必从说明文字猜测。

- `source_id` 绑定知识库、文档和内容版本；`evidence_id` 进一步绑定范围、解析产物和片段。同一内容版本的同一片段可跨调用关联。`citation_scope=call` 明确引用编号不跨调用复用。
- coverage 区分解析完整性（无法证明时 unknown）、扫描完成、候选截断和返回分页。候选搜索结束不代表全库无答案。
- `get_kb_overview` 读取已有摘要，不调用模型生成；未传 query 是目录样本，传 query 是文件名候选。文档 capability 只描述原文索引和 chunk 列表，`vector_health=not_probed` 不冒充向量健康检查。
- 错误保留 error/message，增加 code、retryable、recovery。busy 与 rate_limited 分开，退避时间只是建议。引用过期、被回收或不属于当前令牌统一为 reference_invalid，避免透露他人引用是否存在；文档版本变化返回 document_changed。

### 返回预算与续接

预算按完整 JSON envelope 计算，包括元数据和实际长度的引用。目录、成员和证据列表超限时保存余项，通过 continue_search 的 cursor 返回剩余页；尾页才恢复原查询后续入口。长原文缩小单段，保留 next_read_ref。表格按更小行区间重试，保持行与引用的对应；单行超限返回 pending_row 的原文入口，后续行另有 next_range。读取超大行的原文不等于已经获得完整结构化单元格。

不可拆分的巨型元数据或超过引用存储容量仍可能返回 output_limit；不承诺任意大小数据都能一次成功。引用容量和有效期沿用首版，不扩大存储上限。

exact / identifier 在原文索引可用时采用有界扫描：每次至多推进约 524,288 个字符，轮转文档，合并同范围重叠窗口并保留 match_positions。`cursor` 仅翻当前结果；`scan_cursor` 才推进剩余原文，二者互斥。消费当前结果后才返回 next_scan_cursor。`matches_seen` / `scanned_chars` 是该扫描链累计值，`call_matches` / `call_scanned_chars` 是本次增量；只有完成时 total_matches 才确定。分页快照不是 Agent 会话或长期上下文。

### 检索策略与诊断

全库/文档的非精确搜索可传最多两个、各不超过 200 字符的 `query_variants`。Agent 自行提供同义词或原文语言查询，服务端不自动翻译、不调用聊天模型；exact / identifier 不接受该参数。向量候选与关键词候选按模式融合，过滤过期/外来/摘要 chunk，适度优先展示不同来源；文件名候选只供导航，不当成证据。语义路径不可用时尝试关键词，明确记录降级；所有路径失败时返回错误，不能推断知识库无答案。

这些策略不证明真实 embedding 的跨语言能力或索引健康已修复。缺少原文索引时保留原工具降级路径；未统一重写网页问答的检索算法。新增阶段日志用 request_id 关联网关往返、工具总耗时、原文扫描、embedding、候选检索、定位和引用发布。files_inclusive_ms 是包含内部工作的耗时，不与内部阶段简单相加。

权限在请求入口、发布结果前及请求返回前校验；长循环每次检查取消，权限检查最多缓存 100ms。底层不可中断操作仍占用并发槽直到退出，不提高默认并发、不增加后台队列。

### 兼容与回退

本轮无数据库迁移，不修改知识数据、账号、模型配置或网页问答默认链路；新增代码集中在 src/mcp。旧引用仍可使用；升级生成的 result / scan 引用旧代码不识别，回退后客户端必须重新搜索/取目录。回退应用与网关到同一旧提交并重新连接；无需清空知识数据。当前优化未提交、未部署生产。

正式回答效果验收由用户在独立 Claude Code + GLM 会话执行；开发侧使用隔离知识库和官方 SDK 独立验证，不读取或操作该会话。评测口径见 [MCP 独立验收清单](mcp-agent-evaluation.md)。

开发侧最终记录：全量后端 860 passed；之后补充的超大表格行边界专项 10 passed。超大的 display_header 仅保留行/坐标和 read_ref，明确 values_omitted，不把省略的结构化单元格冒充已完整返回。原文仍可分段读完。SDK 协议与真实 HTTP 查询链路、Ruff、版本、diff、前端构建均通过。详见独立验收清单。

## 对比报告后修复

本地 embedding 完整缓存从本地 snapshot 加载；缺失模型在普通查询/预热时快速失败，不隐式下载。管理员模型加载入口仍可明确下载。失败冷却 60 秒；预热只有实际成功才记录完成。原索引需使用对应模型，不能替换成另一型号规避缺失问题。

MCP 的 diagnostics 返回当前调用关键阶段 phase_ms 与 request_log_key，阶段可能重叠，范围截至输出分页前；详细发布/总耗时仍在日志。search_scope.degraded 与 executed_paths 区分实际执行路径，retrieval_warnings 给出失败阶段和恢复建议。即使有候选，Agent 也应核对相关性，必要时提供原文语言 query_variants 或阅读目录/原文。

实现及独立测试结果见 [对比报告后的修复与验证](mcp-followup-results-2026-11-12.md)。

## MCP 相关性筛选

MCP 的概念检索增加独立本地相关性评估，系统 MCP 设置可调整开关、候选数、时间预算及最低评分。exact / identifier / keyword 不增加重排；网页检索不改。完整说明、回退和多类测试记录见 [MCP 相关性优化](mcp-relevance-implementation.md)。相关性分数不是事实正确概率；务必检查 relevance.status 与未评估数量，模型缺失会明确降级且不自动下载。

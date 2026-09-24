# Insight · 知识库

面向个人与单团队的知识库检索和 AI 问答应用。导入文档后，可以直接检索原文、让 AI 基于资料回答，或让 AI 自主调用工具搜索和阅读知识库。

当前主界面使用 **React + TypeScript**，后端使用 **FastAPI**，元数据和索引保存在服务器本地。聊天模型通过个人或团队 API 接入：使用远端模型时，问题、选取的知识库资料和必要的会话上下文会发送给所选服务商，因此“本地存储”不等于“完全离线”。

## 主要功能

- **知识库与文档**：个人私有库、团队共享库、资源授权；批量导入、后台进度、安全取消与清理；文档阅读索引自动建立并支持手动重建。
- **三种检索模式**：基础检索、增强 AI、深度 AI，支持原文引用、流式回答和查阅过程展示。
- **会话管理**：会话分组、自定义名称、问答时间、消息导航、滚动跟随；近期上下文、自动摘要和可编辑个人偏好。
- **单团队账号管理**：关闭公开注册，由管理员创建账号；自定义角色、菜单及功能权限、知识库和 API 授权。
- **模型与 API**：个人 API、授权使用的团队公共 API；请求重试、调用记录及错误排查。
- **三类日志**：系统运行日志、管理操作日志、AI 调用日志相互独立。
- **界面设置**：主题与毛玻璃效果、思考过程和引用显示开关、各检索模式独立的检索深度记忆。

## 检索模式与设置

| 模式 | 资料获取方式 | 适合场景 |
| --- | --- | --- |
| 基础检索 | 返回知识库原文片段，可对本轮结果继续扩展检索 | 查找文档、字段、原文依据 |
| 增强 AI | 系统检索、补读及组织资料，然后 AI 直接流式回答 | 日常问答、解释、常见排障 |
| 深度 AI | AI 自主选择搜索、目录、文档及表格阅读等工具，多轮调阅后回答 | 跨文档分析、复杂排障、需要继续查阅的问题 |

增强 AI 与深度 AI 都支持 **资料优先 / 综合分析 / 开放探索** 三种 AI 约束策略。区别在于资料获取方式，不是将增强 AI 限制为只能复述原文。知识库事实应有依据，通用知识、假设与推断应与已确认事实区分。深度 AI 需要所选 API 和模型支持工具调用。

**检索深度**有低、中、高、最高四档，对应 5、10、15、20；默认中。各模式分别记忆，悬停可查看含义：

| 模式 | 检索深度实际控制什么 |
| --- | --- |
| 基础检索 | 返回结果数量上限 |
| 增强 AI | 参考资料窗口数量上限，实际内容受去重及上下文预算限制 |
| 深度 AI | 单次搜索的默认返回数量；AI 明确指定 `limit` 时优先使用其值 |

检索深度不控制模型思考能力，也不等于查阅轮数。深度 AI 的**最大查阅轮数**和**总时长限制**另行设置：默认 10 轮，可不限轮数；总时长预设 6 分钟，默认不启用。实际返回量仍受匹配结果、候选范围及资料预算影响，更多资料不保证答案更好。

## 快速开始

以下是源码运行方式。使用 Python 3.11、Node.js 22 和 pnpm 11 可与仓库构建流程保持一致。首次安装依赖和加载本地模型可能需要联网。

### 1. 获取代码

```bash
git clone https://github.com/xuananrocx/Insight_new.git
cd Insight_new
```

### 2. 安装后端依赖

Windows PowerShell：

```powershell
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Linux / macOS：

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Linux / macOS 的 `python-magic` 还需要系统提供 libmagic。Linux 环境可运行后端及浏览器前端，但仍需按目标系统安装依赖并验证本地模型，Windows 的 `.bat` 脚本不能直接使用。

### 3. 安装前端依赖

```bash
npm install -g pnpm@11
cd frontend
pnpm install --frozen-lockfile
cd ..
```

### 4. 启动前后端

在项目根目录启动后端，Windows PowerShell：

```powershell
.venv\Scripts\python.exe -m src.main --api-only
```

Linux / macOS（已激活虚拟环境）：

```bash
python -m src.main --api-only
```

另开终端启动前端：

```bash
cd frontend
pnpm dev
```

访问：

- 主界面：<http://localhost:5173>
- API 文档：<http://localhost:8000/docs>

Vite 默认将 `/api` 转发到 `localhost:8000`。修改后端端口时，应同步调整 `frontend/vite.config.ts` 中的代理配置。

Windows 安装好依赖后也可使用 `dev.bat`。`start.bat` 仍启动 Vite 开发服务器，其名称不代表正式部署；其中关于 `.env` 和 DeepSeek 的提示是旧提示，当前账号体系的聊天 API 应在页面中配置。

> 不带参数的 `python -m src.main` 仍会启动旧 Streamlit 界面。当前功能以 React 界面为准，请使用 `--api-only` 配合前端运行。

### 5. 初始化账号与知识库

1. 首次访问按页面提示初始化管理员，从服务器用户数据目录读取 `setup-token.txt`。没有预设管理员密码。
2. 初始化后，管理员在系统管理中创建账号、配置角色和授权。新建用户首次登录需修改初始密码。
3. 在设置页添加个人聊天 API，或选择已获授权的团队 API。管理员也使用自己的个人 API 或团队 API。
4. 创建知识库并导入文档，等待解析、向量化和阅读索引完成；已有文档可手动重建阅读索引。
5. 创建会话，选择知识库、检索模式和检索深度后提问。

默认向量化使用本地 `BAAI/bge-base-zh-v1.5`，不要求先配置云端 embedding 密钥。本地模型尚未缓存时需要下载；文档 AI 摘要等功能仍需要可用的聊天 API。

## 构建后运行与部署

先构建 React 前端：

```bash
cd frontend
pnpm build
cd ..
```

随后在项目根目录运行单个后端实例，FastAPI 会同时提供 `frontend/dist` 中的页面和静态资源：

```bash
python -m uvicorn src.api.main:app --host 127.0.0.1 --port 8000
```

Windows 未激活虚拟环境时，将命令中的 `python` 替换为 `.venv\Scripts\python.exe`。此时主界面地址为 <http://127.0.0.1:8000>，无需另起 Vite。构建产物必须在后端启动前就绪。

面向团队在线使用时，使用 HTTPS 和同源反向代理，并正确转发协议头。SSE 流式响应需要关闭代理缓冲并设置合适的超时。当前按**单应用实例、本地 SQLite / Chroma 数据目录**运行，不要直接添加多个 worker 或让多个实例同时操作同一目录。账号权限、代理信任及恢复方式见 [账号与部署说明](docs/accounts.md)。

## 账号与权限

系统角色控制菜单和功能入口；知识库与团队 API 另有资源授权。拥有某个菜单权限，不等于拥有所有知识库或所有 API 的使用权。

- 普通用户可在获准后创建私有知识库、配置个人 API。
- 团队知识库和公共 API 由有管理权限的账号授权给用户或角色。
- 应用内管理员不会自动获得其他用户的聊天内容、个人 API 或私有知识库读取权。
- 撤销知识库使用权后保留历史会话，但不能继续使用该知识库提问。
- 服务器维护人员仍能访问本机数据；应用权限不能替代服务器权限管理。

详见 [单团队账号与 API 管理](docs/accounts.md)。

## 配置、数据与备份

仓库中的 `config.yaml` 是**出厂配置模板**。运行时使用用户数据目录中的配置，已有安装不会因修改仓库模板而自动覆盖所有用户设置。

默认数据目录：

| 平台 | 默认位置 |
| --- | --- |
| Windows | `%LOCALAPPDATA%\AI-Assistant` |
| Linux | 通常为 `~/.local/share/AI-Assistant`，遵循平台数据目录设置 |
| macOS | 通常为 `~/Library/Application Support/AI-Assistant` |

可在启动前设置 `AMD_DATA_DIR` 指定其他目录。数据目录名称沿用 `AI-Assistant`，与当前产品名不同。

配置读取顺序为用户配置，再叠加用户数据目录的 `config.local.yaml`，最后叠加项目根目录的 `config.local.yaml`。例如覆盖投喂目录：

```yaml
paths:
  feed_folder: /path/to/knowledge-feeds
```

聊天 API 主要通过页面管理；embedding、重排和服务端参数由系统配置管理。个人及团队 API 密钥加密存储，解密依赖数据目录中的 `credentials.key`。`.env.example` 保留用于环境变量配置参考，并非新账号必须填写的启动步骤。

备份前停止服务，完整备份用户数据目录，并额外备份放在其他路径的原始文档与投喂目录。**数据库和 `credentials.key` 必须一起保留**；不要只备份 Git 仓库，也不要将运行中的 SQLite / Chroma 目录直接作为多机器共享数据盘。恢复已有数据时保留原目录结构和配置路径。

## 文档导入与阅读

支持常用 PDF、Word、Excel、PowerPoint、HTML/XML、Markdown、文本、配置和源码文件，以及 ZIP、tar 等压缩包。具体扩展名支持以解析器为准；扫描 PDF 与图片不能视为默认具备完整 OCR 能力。

导入任务支持后台查看进度、安全取消及清理。安全取消在可停止的阶段生效，不等于强制终止进程。文档向量索引用于召回，阅读索引用于目录、结构和原文定位；二者职责不同，重建阅读索引不能替代向量索引重建。

相关说明：[导入进度](docs/import-progress.md) · [文档阅读索引](docs/document-indexes.md) · [知识库工具使用](docs/knowledge-tool-usage.md)。

## 日志与排查

| 日志 | 用途 |
| --- | --- |
| 系统日志 | 服务运行、检索阶段、模型请求异常、后台任务；文件位于用户数据目录 `logs/` |
| 操作日志 | 用户、角色、授权、API 配置等管理操作，可按用户筛选 |
| AI 调用日志 | 提示词、请求消息、响应及错误，实际记录范围取决于日志开关与调用类型 |

排查回答效果时，先核对所选模式、实际送入模型的资料、引用和调用错误，不应只看候选数量。AI 调用日志可能含原文及会话内容，分享排查材料前应检查敏感信息。

常见情况：

- **8000 端口只有后端提示，没有页面**：先构建 `frontend/dist` 并重启后端，或使用 5173 的 Vite 页面。
- **首次向量化较慢**：检查本地模型下载和加载进度，以及 CPU、内存和系统日志。
- **深度 AI 工具调用失败**：检查所选模型、API 协议与代理是否支持工具调用，并查看对应调用错误。
- **切换模型后仍无法提问**：检查当前用户是否拥有该 API 和知识库的有效授权。
- **忘记管理员密码**：服务器本地可执行 `python -m src.core.manage_accounts 用户名`，按交互提示恢复；详见账号文档。

## 开发与项目结构

```text
Insight_new/
├── frontend/            # React + TypeScript + Vite 主界面
├── src/
│   ├── api/             # FastAPI 路由、认证与静态页面服务
│   ├── core/            # 配置、账号、模型客户端及日志
│   ├── db/              # SQLite 元数据及迁移
│   ├── knowledge/       # 解析、导入、索引及重建
│   ├── qa/              # 检索、增强 AI、深度 AI 工具及上下文
│   ├── feedback/        # 反馈处理
│   └── web/             # 旧 Streamlit 界面
├── tests/               # 隔离测试
├── docs/                # 功能与设计文档
├── build/               # 打包配置
├── config.yaml          # 出厂配置模板
└── requirements.txt     # Python 依赖
```

在已安装依赖的虚拟环境中运行：

```bash
python -m pytest
cd frontend
pnpm build
```

测试通过 `tests/conftest.py` 使用临时数据目录。请保留这套隔离机制，不要用真实知识库数据库运行数据修改测试。源码、测试和模板提交到 Git；虚拟环境、依赖目录、密钥、运行数据及日志不应提交。

进一步阅读：[增强 AI 设计](docs/enhanced-ai-design.md) · [会话上下文](docs/conversation-context.md) · [会话组织](docs/session-organization.md) · [模式合并说明](docs/retrieval-mode-consolidation.md)。部分历史设计文档保留了当时的实现背景，实际行为以当前代码为准。

## 许可证

本仓库使用 [Apache License 2.0](LICENSE)。

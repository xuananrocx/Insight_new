# 按平台构建候选包

本阶段提供构建入口，不自动发版、推标签或部署服务器。版本仍为 **0.1.1**。构建成功表示产物已生成，安装与业务验收仍为 `pending`；第五阶段再验收。

## GitHub 手动入口

代码提交并推送到 `Insight_new` 的默认分支后，在 Actions 选择 **Build selected platforms → Run workflow**：

1. `source_sha` 填完整的 40 位小写提交 SHA，不填分支名或标签。该提交必须包含本构建工具。
2. 勾选 Windows、macOS、Linux 任意组合，至少一个。例如只勾选 Windows 和 Linux。
3. Linux 选择 `full`（首次安装，含模型）或 `upgrade`（升级包，不含模型文件，仍校验模型清单）。
4. 下载各平台 `candidate-*` 附件及 `release-summary-*` 汇总，保留构建运行编号。

未选平台标为 `not_selected`。任一所选平台失败、取消或缺少结果，汇总不得标为全部构建完成。成功的平台产物保留，供排查或单独验收，不代表整个组合已经发布。候选附件默认保留 30 天，正式发布前需另行留存。

Windows 为 x64 EXE；macOS 为 ARM64 DMG；Linux 为 AMD64 tar.gz。不同架构不能混用。Windows 暂无代码签名；macOS 仅临时签名、未公证，正式分发前需单独决定签名方案。

## Linux 构建材料

新增 `linux_bootstrap=true` 路径：在 GitHub Linux AMD64 runner 上准备公开构建材料，无需预先设置仓库变量或 secret。`deploy/linux/ci-materials.json` 固定官方 Python/Nginx 镜像 digest、官方模型提交和逐文件校验值；`requirements-runtime.lock` 固定 Linux Python 依赖，基线来自 176 的只读依赖清单，不包含服务器数据或配置。系统 apt 包仍来自发行版仓库，不承诺逐字节复现。

该路径先构建 CPU 运行时，再下载完整 BGE-Large、BGE-reranker-v2-m3 和 tokenizer，复用现有离线打包程序。下载后验证大小及哈希；候选镜像通过依赖一致性、断网模型推理、健康接口和前端页面检查后才上传。升级包仍不含模型文件。首次准备会有数 GB 下载，须使用独立构建机器，不在业务服务器运行。

候选包的官方模型清单不保证与 176/224 历史模型目录逐文件一致，不能绕过部署校验或直接套用 Compose 升级；现有 host 网络部署仍须另行验收并保留原模型目录。构建成功不等于数据迁移或真实 AI 服务已验收。

使用已准备好的运行时和模型归档时，将 `linux_bootstrap` 设为 `false`，继续使用下述原有配置。

GitHub 托管 runner 不具备本机 Docker 缓存和模型，需要先准备可访问的材料：

| 配置 | 类型 | 内容 |
| --- | --- | --- |
| `INSIGHT_RUNTIME_IMAGE` | Repository variable | 经验证的 Python/模型运行时镜像，使用 `registry/repository@sha256:...` |
| `INSIGHT_WEB_IMAGE` | Repository variable | 经验证的 Nginx 镜像，使用完整 digest |
| `INSIGHT_MODELS_URL` | Repository secret | HTTPS 模型 tar 包下载地址，可用有时效的签名地址 |
| `INSIGHT_MODELS_SHA256` | Repository variable | 模型压缩包本身的 SHA256 |

模型包根目录须直接对应部署的 models 目录，包含完整向量模型、重排模型及 tiktoken 材料，不再套一层 models；拒绝路径穿越和链接。模型内容由 `deploy/linux/package.py` 校验。升级包也需要这些材料来生成与目标模型一致的清单。

本步骤未上传模型或运行时镜像，也未配置仓库变量。镜像当前要求 runner 可直接拉取；私有镜像仓库登录需另行接入。材料未配置时 Linux 构建会明确失败，不会退回小模型、随机版本或在线重装运行依赖。Docker/Compose 的宿主机安装材料仍需按部署操作系统另备，应用完整包不等于包含所有 Linux 发行版的 Docker 安装器。

## 本地入口

使用对应操作系统的独立、干净 checkout；工作区中修改或未跟踪文件会阻止构建。输出和临时目录必须位于 checkout 外且尚不存在。先安装 Node 22、pnpm 11、Python 3.11；桌面平台另需安装工作流列明的 Python 依赖、PyInstaller，Windows 需 NSIS 3.10，macOS 需系统签名及 DMG 工具。

```text
python scripts/release_build.py --platform windows --revision <完整SHA> --work <新的临时目录> --output <新的产物目录>
python scripts/release_build.py --platform macos --revision <同一完整SHA> --work <新的临时目录> --output <新的产物目录>
python scripts/release_build.py --platform linux --revision <同一完整SHA> --linux-package full --runtime-image sha256:<本机镜像ID> --web-image sha256:<本机镜像ID> --models <完整模型目录> --work <新的临时目录> --output <新的产物目录>
```

Linux 构建必须在 Linux/Docker 环境运行，Windows 可使用 WSL。不要在日常开发目录里清理改动以迁就检查，使用独立 checkout。失败目录可能保留诊断材料，不得作为正式包；重新执行使用新目录。

## 追溯与边界

- 文件名包含版本、平台、架构和提交短编号；`build-manifest.json` 保存完整提交、版本、工具版本、输入文件哈希、产物大小和 SHA256，Linux 另记录镜像身份。
- `SHA256SUMS` 按原始字节计算，包含 manifest 的校验值。Linux 包内部另有部署清单与逐文件校验。
- 前端使用冻结的 pnpm lockfile；Linux 运行时与代理固定 digest；Python 解析结果保存为 `python-dependencies.txt`。
- Python 原有部分依赖仍是版本区间，Actions runner 和工具补丁版也会变化；当前保证来源可追溯，不承诺逐字节可重复构建。补构建需使用原始依赖记录并重新验收，不覆盖原候选包。
- 不读取或打包本地数据目录、账户、聊天记录和 API 密钥；Linux 仅使用部署配置模板。
- 发布时应使用已验收的原产物，不在服务器重新构建。第五阶段确认正式发布和生产部署方案。

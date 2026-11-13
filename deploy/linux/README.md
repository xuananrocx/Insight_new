# Linux 容器部署

版本包同时包含同一版本/提交的应用镜像、独立 MCP 网关镜像和代理镜像。MCP 的 SDK 依赖不安装进主应用，也不挂载数据或模型目录。安装网关不会打开 MCP 业务开关、创建令牌或修改账号权限。

支持两种模式：新环境使用 Compose；现有 host 网络应用与宿主机 Nginx 使用下文的 host 接管流程。两种模式不能交叉使用同一 state 目录。
Compose 控制器要求 Linux amd64、Python >= 3.8、Docker Engine、Compose v2（使用 `up --wait`）。
Compose 模式要求 bridge 网络和端口转发；176 原专用 daemon 禁用了这些能力，应使用 host 模式。
运行前先确认主机系统与运行时兼容性；不自动升级系统、修改防火墙、安装 Docker、增加 Swap 或调整原 Nginx。

## 构建应用镜像

在指定提交的干净源码中执行发布检查及前端构建，将这些文件作为构建上下文。
`Dockerfile` 只复制明确的应用文件，不将本地配置、数据库、密钥或虚拟环境打入镜像。
`RUNTIME_IMAGE` 必须是经过验证的 Python 3.11 CPU 依赖镜像的固定 digest/image ID。
过渡阶段可使用之前验收的 Insight 镜像作为依赖层；Dockerfile 会清空旧 `/app`，再复制当前应用。
依赖升级时必须单独重建、锁定和验证运行时，不能默认旧依赖适用于所有后续版本。

```sh
python scripts/check_release.py
npm --prefix frontend run build
docker build --network none -f deploy/linux/Dockerfile \
  --build-arg RUNTIME_IMAGE=sha256:已验证的运行时镜像ID \
  --build-arg REVISION=完整提交SHA --build-arg APP_VERSION=0.1.1 \
  -t insight:0.1.1-提交短码 .
```

版本号来自 `src/__init__.py`。本阶段尚未实现第四步的干净源码构建自动化；不要把未提交的构建标记为正式发布。
Nginx 使用经过测试的官方镜像，打包时记录实际 image ID，而非只依赖可变标签。

MCP 镜像使用 `deploy/mcp/Dockerfile` 和独立固定依赖构建；Python 基础镜像默认取 `ci-materials.json` 的固定 digest，也可用 `release_build.py --mcp-base-image` 指定已审查的 digest/本地镜像 ID。只有构建机下载依赖，目标机只加载离线镜像。网关包含独立 `/health`，检查网关到主应用的连通性，不要求打开 MCP 业务开关。

## 模型布局与打包

```text
models/
  huggingface/hub/models--BAAI--bge-large-zh-v1.5/...
  reranker-torch/...            # BGE-reranker-v2-m3 完整权重
  tiktoken/...
```

模型缓存以只读方式挂载；不要在包里混入用户数据。全量检查可能较慢，这是模型完整性校验。
所选模型不降级；增强 AI 的可选 ONNX 重排默认关闭，保持现有应用逻辑。

```sh
python deploy/linux/package.py --kind full \
  --app-image insight:版本-提交短码 --mcp-image insight-mcp:版本-同一提交短码 --web-image nginx:已验证版本 \
  --models /path/to/models --data-schema insight-0.1.1 --output /path/to/new-bundle
```

`--kind upgrade` 不复制模型，仍记录所需模型的校验值，部署时核对已有模型。
`--runtime DIR` 可附带预先审查的 Docker/Compose 安装材料；控制器不自动执行这些材料。
缺少运行时的目标机应先由运维按其系统安装，不能将“不带运行时的完整应用包”误称为裸机一键安装包。
`checksums.json` 校验每个文件；对整个归档另算 SHA256 后通过可信渠道传递。
可用 `tar -czf insight-linux-版本-提交-full.tar.gz -C 包的父目录 包目录名` 压缩，再用 `sha256sum` 生成归档校验值；升级包命名为 upgrade。
不要修改包内文件。把部署设置放在包外，升级时保持旧包完整供回退使用。

## 配置与首次安装

将 `settings.example.json` 复制到包外，例如 `/srv/insight/settings.json`，填写 UID/GID、目录、端口和网段。
UID/GID 使用已创建的普通部署用户，不自动创建用户或授予 sudo 权限。
数据、模型、备份、state、bundle 目录彼此分开；不要放在临时目录或被自动清理的位置。
网段没有默认值：用 `ip -4 route show table all` 和 `docker network inspect` 核对公司内网及现有容器网络。
WEB_IP 选择该网段非网关的地址，用于只信任代理的转发头。
默认只发布 8088，后端 8888 不发布。使用 HTTP；域名与 HTTPS 应另行配置和验收。
填写 `mcp_allowed_hosts` 为真实客户端访问的 Host（包括非默认端口），例如 `kb.example.com:8088,127.0.0.1:8088`。代理的 `/mcp` 转发到独立网关；`/mcp-health` 提供不含凭证的就绪状态；对外屏蔽 `/mcp-service`。网关没有发布单独端口。

资源示例：8GB 机器可从应用 6g 开始；4GB 完整模型试运行需要另行评估 Swap，可配置应用 3g、内存+Swap 10g。
脚本不创建 Swap；资源限额不代表容量保证。宿主机无相应资源时不能照抄。

```sh
sudo python3 /path/to/bundle/insightctl.py check \
  --bundle /path/to/bundle --settings /srv/insight/settings.json --state /srv/insight/state
sudo python3 /path/to/bundle/insightctl.py install --apply \
  --bundle /path/to/bundle --settings /srv/insight/settings.json --state /srv/insight/state
```

首次安装拒绝已有数据目录。full 包也拒绝覆盖已有模型目录；upgrade 包可用于已预置且校验通过的模型目录。
服务健康后打开 8088；初始化令牌在数据目录的 `setup-token.txt`，只在终端私下读取，不写进发布包或日志。
管理员、API、知识库都通过应用初始化；不复制其他部署的账号或接口地址。
状态检查和日志：同样传入 settings/state，action 改成 `status` 或 `logs`。

## 升级与回退

先确保没有导入、生成回答等进行中的任务；当前未实现业务维护模式，不能假设 stop 会等待所有后台任务。
运行 `check`，获得维护窗口，再执行 `upgrade --apply`（参数同上，bundle 指向新包）。
升级先校验镜像和模型，再停止本套容器，备份完整数据，启动新容器并检查 HTTP。
新包的应用和网关镜像同时加载并核对身份；启动检查同时覆盖网页、网关版本、后端连通性及 `/mcp` 的 Bearer 入口。旧版部署首次升级时增加网关，旧配置须补上 `mcp_allowed_hosts`。回退旧包时停止新三组件，再使用原包原 Compose 文件；不能只回退应用而保留新版网关。
备份失败则保持停止，不在备份不完整时自动继续；启动失败保留备份及 pending 记录，由操作者检查。
临时运行环境问题消除后，可用 `resume --apply`（settings/state 参数相同）恢复同一个 pending 启动；不重新初始化数据或重复备份，且拒绝已被修改的包和配置。
Schema 标识变化时拒绝普通升级，需要专门审查迁移；相同标识也必须通过真实升级测试。

```sh
sudo python3 /path/to/bundle/insightctl.py rollback --apply --restore-data \
  --backup /srv/insight/backups/明确的备份编号 \
  --settings /srv/insight/settings.json --state /srv/insight/state
```

回退恢复指定备份的整套数据及旧镜像。升级后的新数据移到 `data.before-rollback-*` 保留，不自动删除。
这意味着回退后的应用不包含备份之后的写入；必须先确认可接受。旧包、旧模型和足够磁盘空间都是回退前提。
备份含账号、会话和加密密钥，目录和文件限制权限，并应另外备份到服务器外。

## 运行维护与边界

- `restart: unless-stopped` 依赖 Docker 本身已配置开机启动；显式停止的容器不会自行恢复。
- 本套部署使用独立 Compose project；不要使用 `down -v`、清理全机镜像或更改别的 project。
- Nginx 临时文件在私有 /tmp，最大 64MB；上传上限 100MB，因此代理不缓冲上传请求，避免临时盘容量限制。
- 应用日志在 data 内，Docker/Nginx stdout 日志按 20MB×3 轮转。
- `check` 是只读预检，不能证明云防火墙、API 可达性、模型效果或大文档容量。
- 从旧部署迁移须单独安排：保留原数据备份、确认 UID/GID、目录和配置、处理 8088 占用，176 的 8090 服务不得受影响。

## 现有 host 网络部署：首次接管、升级与回退

此模式面向现有主应用容器和宿主机 Nginx，不改为 bridge，不接管其他业务。使用 `host-settings.example.json` 复制到包外填写实际值：原应用名称、UID:GID、/data 与只读 /models 挂载、专用 Docker Unix socket、Nginx 可执行文件/前缀/主配置、端口及 Host 白名单。设置值不能在后续升级时隐式改变。

176 这类没有宿主机 Python 3 的环境，可用包中的 `hostctl.sh`。它使用配套 MCP 镜像里的 Python 启动一次性控制器，结束即删除；不在宿主机安装 Python、Compose 或 SDK。控制器需要管理员权限，挂载宿主机根目录到 `/host`、共享 host PID/network，以访问指定 Docker socket、制作备份并调用宿主机原 Nginx 校验/加载；不是长期业务容器。请仅运行已通过外层 SHA256 校验且来源可信的发布包。使用 SELinux/userns-remap 等隔离策略的环境需要另外验收，脚本不会关闭这些策略。

先校验归档和包内清单，再加载 `images.tar`。例如使用专用 daemon：

```sh
export INSIGHT_DOCKER=/opt/insight-docker/bin/docker
export INSIGHT_DOCKER_SOCKET=/var/run/insight-docker.sock
"$INSIGHT_DOCKER" -H "unix://$INSIGHT_DOCKER_SOCKET" load -i /absolute/bundle/images.tar
# 只登记现有容器快照，不停止应用、不改代理。
sh /absolute/bundle/hostctl.sh adopt --apply \
  --settings /absolute/host-settings.json --state /absolute/private-state
```

状态目录含原容器配置（可能有密钥），限制为 0700、记录为 0600；不能提交、分享或放入公共发布包。主应用必须使用 host 网络，且 /data、/models、UID:GID 与设置完全匹配。已存在同名未知网关时拒绝接管，避免覆盖临时部署。

第一次启用需要准备**经过人工审阅的完整 Nginx 候选配置**，仅在 Insight 的 server 块加入一行 `include /实际路径/insight-mcp.conf;`（路径对应 `nginx_include`）。不要修改正在使用的主配置，也不要把 include 放在共用 http 块或其他业务 server 中。控制器在维护窗口里备份原配置，生成受管 include，安装候选配置，执行原 Nginx 的 `-t`，通过后才 reload；失败恢复原配置。不会自动重写未知 Nginx 配置或其他服务。

```sh
sh /absolute/bundle/hostctl.sh check \
  --bundle /absolute/bundle --settings /absolute/host-settings.json \
  --state /absolute/private-state --nginx-candidate /absolute/nginx-reviewed.conf
# 确认没有导入/回答任务，获得维护窗口后执行：
sh /absolute/bundle/hostctl.sh upgrade --apply \
  --bundle /absolute/bundle --settings /absolute/host-settings.json \
  --state /absolute/private-state --nginx-candidate /absolute/nginx-reviewed.conf
```

控制器保留原主应用的命令、环境、挂载、日志和重启策略，只替换镜像并增加内部网关检测地址；网关只监听 127.0.0.1。先加载并校验同版本镜像，再停止应用/旧网关并完整备份 /data，保留原容器，启动新的一对容器。历史模型目录沿用，不强制替换为 CI 模型；不重建索引、不覆盖用户配置。外部投喂目录等不在 /data 内的资料仍需另行备份。

后续版本继续使用同一 settings/state 和新的完整版本包执行 `check`、`upgrade`，一般不再需要 `--nginx-candidate`。主应用与网关统一升级，不另维护手工服务。启动失败会保留 `pending.json`；排除原因后用 `resume --apply`，不会重复备份。备份本身失败时不能 resume，应先 `rollback --apply` 恢复原运行状态。

回退在途失败用 `rollback --apply`；回退已完成升级须加 `--backup /absolute/backup/host-编号`。默认只回退容器和代理，不恢复数据；仅同 schema 版本允许这一流程。确实要恢复快照时显式加 `--restore-data`：先验证备份，当前数据移到 `data.before-rollback-*` 保留。MCP 永久令牌在不支持它的旧代码中失效，不会绕过鉴权。旧容器、旧镜像、备份及部署状态不自动清理；确认稳定后由维护人员按保留策略清理，不执行全机 prune。

`hostctl.sh` 如需自动加载缺失的控制器镜像，会加载包内 `images.tar`；这只准备镜像，不启动/停止业务容器。可先按上文显式加载再做只读 `check`。`status` 显示组件状态；日志使用指定 daemon 的 `docker logs insight-mcp`。所有参数路径使用宿主机绝对路径；不要混用容器路径或在同一个 state 中混用直接 Python 与打包控制器。

## 验证边界

构建流水线新增 Linux 配套镜像的依赖检查、独立 SDK 协议检查，以及隔离空数据中的真实应用 → 网关 → Nginx 握手、工具列表和查询。验收不打开现有服务器的开关或创建真实用户令牌。
当前开发机可运行事务/失败恢复和协议测试，但没有 Docker，Linux 镜像构建、Nginx 和 host 控制器容器运行须在候选构建环境通过后才能用于 176。源码改造完成不等于已经部署或生产验收。

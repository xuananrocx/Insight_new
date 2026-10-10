# Linux 容器部署（0.1.1）

这套材料用于新的 Compose 部署，不直接接管已有 host 网络容器或共用 Nginx。
当前实现目标：Linux amd64、Python >= 3.8、Docker Engine、Compose v2（使用 `up --wait`）。
Docker 必须支持 bridge 网络、端口转发、内存限额；176 原专用 daemon 禁用了这些能力，不能直接套用。
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
  --app-image insight:0.1.1-提交短码 --web-image nginx:已验证版本 \
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

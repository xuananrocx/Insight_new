# 开发指南 - Mac/Windows 双机切换

## 仓库与同步策略

```
GitHub (Private)
   ↑↓
本地代码（Mac 和 Win 各一份）
   ↑↓
本地数据（每台机器各自跑，不同步）
```

**同步什么：**
- ✅ 代码（src/, tests/, docs/）
- ✅ 配置模板（config.yaml, .env.example, requirements.txt）
- ✅ Prompt 模板（prompts/）

**不同步什么：**
- ❌ 虚拟环境（.venv/）：每台机器各自建
- ❌ API key（.env）：敏感信息
- ❌ 知识库数据（data/vector_db/, data/metadata.db）：体积大，每台各自跑
- ❌ 投喂文档（~/AMD-Knowledge-Feeds/）：建议通过云盘同步（iCloud/OneDrive/坚果云）

## 第一次拉代码（任一台新机器）

```bash
git clone git@github.com:你的用户名/amd-ai-assistant.git
cd amd-ai-assistant

# Mac
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Windows
py -3.11 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# 配置 .env（API key，从另一台机器复制或重新填）
cp .env.example .env
# 编辑填入 keys

# 首次会触发全量 embedding（取决于文档量，几分钟到十几分钟）
python -m src.main
```

## 日常切换流程

### 在 Mac 上做完工作

```bash
# 1. 提交并推送
git add .
git commit -m "feat: xxx"
git push

# 2. 等云盘把 ~/AMD-Knowledge-Feeds 同步完
```

### 切到 Windows

```bash
# 1. 拉最新代码
git pull

# 2. 如果 requirements.txt 变了，更新依赖
pip install -r requirements.txt

# 3. 如果 config.yaml 变了，对比一下

# 4. 启动
python -m src.main
# 系统：
#   - 扫描投喂文件夹
#   - 增量 hash 对比，跳过未变文件
#   - 仅对新增/修改文件做 embedding
```

## 常用命令

```bash
# 启动全部
python -m src.main

# 只起 API（调试用）
python -m src.main --api-only

# 只起 Web（前端调试）
python -m src.main --web-only

# FastAPI 单独启动（开发热重载）
uvicorn src.api.main:app --reload --port 8000

# Streamlit 单独启动
streamlit run src/web/streamlit_app.py --server.port 8501

# 运行测试
pytest

# 代码格式化
ruff format src/
ruff check src/ --fix
```

## IDE 推荐

**VS Code**（Mac/Win 一致体验）：
- 装 Python 扩展
- 装 Ruff 扩展（自动格式化）
- 装 Pylance（类型检查）

工作区设置（`.vscode/settings.json`）：
```json
{
  "python.defaultInterpreterPath": "${workspaceFolder}/.venv/bin/python",
  "python.formatting.provider": "none",
  "[python]": {
    "editor.defaultFormatter": "charliermarsh.ruff"
  },
  "python.testing.pytestEnabled": true
}
```

> Windows 把 `bin/python` 改成 `Scripts/python.exe`。

## 常见问题

### Q: 切机器后报 "Chroma database is locked"

A: 上一台机器的进程没正常退出。删除 `data/vector_db/` 重新初始化（或先确保上一台机器停止服务）。

### Q: pip install 在 Windows 上失败

A: 检查 Python 版本（必须 3.11+），尝试 `pip install --upgrade pip` 后重试。某些库（如 lxml）在 Win 上偶尔有 wheel 缺失，可改用 `pip install --only-binary :all: lxml`。

### Q: Streamlit 提示连接不到 API

A: 先单独起 API 验证：`python -m src.main --api-only`，再起 Web。检查端口是否被占用：`lsof -i :8000`（Mac）或 `netstat -ano | findstr :8000`（Win）。

### Q: 我修改了 config.yaml 但没生效

A: 配置在启动时加载，修改后需重启服务。Streamlit 页面上的 Rerun 按钮不会重载配置。

## 推荐的 Git 工作流

```
main          生产稳定版
  └─ develop    日常开发
       └─ feature/xxx  具体功能分支
```

简单项目可以直接在 main 上提交（如目前阶段）。等功能多了再开分支。

# macOS 应用打包指南

本文档说明如何把 AMD AI Assistant 打包成独立的 macOS 应用（.app）。

## 前置依赖

```bash
brew install pyinstaller
npm install  # 在 frontend/ 目录
```

## 打包流程

### 1. 构建前端

```bash
cd frontend
npm run build
# 产物：frontend/dist/ (含 index.html + assets/)
```

### 2. 打包 Python 后端（含静态资源）

```bash
pyinstaller build/amd-ai-assistant.spec --noconfirm
# 产物：dist/AMD AI Assistant.app
```

### 3. 代码签名 + 公证（可选，发布需要）

```bash
APP="dist/AMD AI Assistant.app"
# 签名
codesign --deep --force --verify --verbose=2 \
  --sign "Developer ID Application: Your Name (XXXXXXXXXX)" "$APP"
# 公证
xcrun notarytool submit "$APP.zip" \
  --apple-id you@example.com \
  --team-id XXXXXXXXXX \
  --password app-specific-password \
  --wait
xcrun stapler staple "$APP"
```

## 数据目录

打包后应用使用 `~/Library/Application Support/AI-Assistant/` 作为数据目录（macOS 标准），
含：
- `config.yaml`：用户配置（含 API key）
- `data/metadata.db`：SQLite 数据库
- `data/vector_db/`：Chroma 向量库
- `data/bm25_index.json`：BM25 索引
- `logs/`：应用日志

首次运行会自动创建并设置文件权限 0600（敏感数据）。

## 启动模式

应用启动时只跑 FastAPI（默认监听 127.0.0.1:8000），同时 serve `frontend/dist` 静态资源。
用户访问 `http://127.0.0.1:8000` 即可使用。

不再启动 Streamlit（保留为 dev 模式选项）。

## 依赖打包注意

- `sentence-transformers` 模型文件大（数百 MB），首次启动会自动下载到 `~/.cache/huggingface/`，
  用户首次使用需联网。可在打包时预下载模型到 bundle（增加 app 体积）。
- `chromadb` 含 native duckdb 扩展，pyinstaller 要 hook。
- `rank_bm25` / `jieba` 纯 Python，无特殊处理。

## 故障排查

- **启动崩溃 "frontend not built"**：`frontend/dist` 缺失，需要先 `npm run build`
- **chromadb duckdb 找不到**：检查 pyinstaller hook 是否包含 `chromadb` 的 native 库
- **首次启动慢**：在下载 embedding 模型（~400MB），耐心等待或预打包

## 相关文件

- `build/amd-ai-assistant.spec`：PyInstaller spec 配置
- `build/Info.plist`：macOS 应用元信息

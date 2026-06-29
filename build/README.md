# Insight 打包指南

本文档说明如何把 Insight 打包成独立的桌面应用。

- **macOS**：`Insight.app` → 进一步打成 `Insight-macOS.dmg`
- **Windows**：`dist/Insight/`（onedir）→ 进一步用 NSIS 打成 `Insight-Windows.exe` 安装包

GitHub Actions（`.github/workflows/build.yml`）会在推送 `v*` tag 时自动构建两个平台并发布到 Release。本指南供本地手动打包用。

---

## 前置依赖

```bash
# Python 依赖（含 pyinstaller）
pip install -r requirements.txt
pip install pyinstaller

# 前端依赖（项目用 pnpm）
cd frontend && pnpm install
```

## 打包流程

### 1. 构建前端

```bash
cd frontend
pnpm run build
# 产物：frontend/dist/（含 index.html + assets/）
```

### 2. 打包应用

```bash
pyinstaller build/insight.spec --noconfirm
```

产物：
- macOS：`dist/Insight.app`
- Windows：`dist/Insight/Insight.exe`（onedir 文件夹）

### 3. 生成分发包

**macOS（.dmg）**：

```bash
# 1) PyInstaller 6.x 不读 info_plist 版本键，手动注入版本号
V=$(python -c "from src import __version__; print(__version__)")
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $V" dist/Insight.app/Contents/Info.plist
/usr/libexec/PlistBuddy -c "Add :CFBundleVersion string 1" dist/Insight.app/Contents/Info.plist
# 2) 改了 plist 必须重新 ad-hoc 签名，否则提示「应用已损坏」
codesign --force --deep --sign - dist/Insight.app
# 3) 打 dmg
rm -rf dmg-staging && mkdir dmg-staging
ditto dist/Insight.app dmg-staging/Insight.app
ln -s /Applications dmg-staging/Applications
hdiutil create -volname "Insight" -srcfolder dmg-staging -ov -format UDZO Insight-macOS.dmg
```

**Windows（.exe 安装包，需 NSIS）**：

```bash
makensis -DAPPVERSION=0.1.1 build/insight.nsi
# 产物：Insight-Windows.exe
```

（GitHub windows-latest runner 已预装 NSIS；本地需自行安装 NSIS。）

### 4. 代码签名 + 公证（可选，正式分发需要）

```bash
APP="dist/Insight.app"
codesign --deep --force --verify --verbose=2 \
  --sign "Developer ID Application: Your Name (XXXXXXXXXX)" "$APP"
xcrun notarytool submit "$APP.zip" \
  --apple-id you@example.com \
  --team-id XXXXXXXXXX \
  --password app-specific-password \
  --wait
xcrun stapler staple "$APP"
```

当前未签名。未签名的应用首次打开会被系统拦截，需手动放行（见下方说明）。

---

## 数据目录

打包后应用使用跨平台用户数据目录作为数据根：

- macOS：`~/Library/Application Support/AI-Assistant/`
- Windows：`%APPDATA%\AI-Assistant\`
- Linux：`~/.local/share/AI-Assistant/`

含：`config.yaml`（用户配置）、`data/`（SQLite + 向量库 + BM25 索引）、`logs/`。
首次运行自动从内置模板拷贝默认 `config.yaml` 并创建目录。

## 启动模式

打包应用启动后只跑 FastAPI（默认监听 `127.0.0.1:8000`），同时 serve `frontend/dist` 静态资源，并自动打开浏览器。Streamlit 仅作为 dev 模式选项（`python -m src.main --web-only`），不在打包产物中作为主入口。

## 首次打开被拦截（未签名）

- **macOS**：双击 `.dmg` 拖到「应用程序」后，右键 Insight →「打开」→ 确认；或在终端 `xattr -cr "/Applications/Insight.app"`。
- **Windows**：首次运行安装包时 SmartScreen 会提示「未知发布者」→ 点「更多信息」→「仍要运行」。

## 依赖打包注意

- `sentence-transformers` 模型文件大（数百 MB），首次启动会自动下载到 HuggingFace 缓存目录，用户首次使用需联网。
- `chromadb` 含 native duckdb 扩展，spec 里已用 `collect_data_files('chromadb')` + hiddenimports 处理。
- `rank_bm25` / `jieba` 纯 Python，无特殊处理。

## 相关文件

- `build/insight.spec`：PyInstaller spec（macOS + Windows 通用）
- `build/Info.plist`：macOS 应用元信息（版本/品牌/bundle id）
- `build/insight.nsi`：Windows NSIS 安装包脚本

# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec：Insight 应用打包配置（macOS + Windows 通用）。

用法：
    pyinstaller build/insight.spec --noconfirm

产物：
    macOS:  dist/Insight.app（onedir 模式，启动快、可读 .app 结构）
    Windows: dist/Insight/（onedir 文件夹，再用 NSIS 打包成 Insight-Windows.exe 安装包）
"""
import os
import sys
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

# 第三方库的 data 文件（模型、配置、native 扩展）
datas = []
datas += collect_data_files('sentence_transformers', include_py_files=False)
datas += collect_data_files('chromadb')
datas += collect_data_files('rank_bm25')
datas += collect_data_files('jieba')

# 项目根目录：spec 在 build/ 下，项目根是其父目录
# SPEC 由 PyInstaller 注入，作为最可靠来源；cwd 作为兜底
if 'SPEC' in dir() and SPEC:
    _project_root = os.path.dirname(os.path.dirname(os.path.abspath(SPEC)))
else:
    _project_root = os.getcwd()
# 校验：项目根必须含 src/ 目录
if not os.path.isdir(os.path.join(_project_root, 'src')):
    raise SystemExit(f"项目根路径错误：{_project_root}（未找到 src/ 子目录）")

# 前端构建产物（frontend/dist）
frontend_dist = os.path.join(_project_root, 'frontend', 'dist')
if os.path.isdir(frontend_dist):
    datas.append((frontend_dist, 'frontend/dist'))

# 默认 config.yaml
config_yaml = os.path.join(_project_root, 'config.yaml')
if os.path.isfile(config_yaml):
    datas.append((config_yaml, '.'))

# 隐式导入（pyinstaller 静态分析漏掉的）
hiddenimports = []
hiddenimports += collect_submodules('sentence_transformers')
hiddenimports += collect_submodules('chromadb')
hiddenimports += collect_submodules('rank_bm25')
hiddenimports += ['duckdb']
hiddenimports += ['jieba']
hiddenimports += ['huggingface_hub']
hiddenimports += ['transformers']
hiddenimports += ['src.api.routes_ai_logs']
hiddenimports += ['src.api.routes_kbs']
hiddenimports += ['src.api.routes_settings']
hiddenimports += ['src.api.routes_logs']
hiddenimports += ['src.api.routes_sessions']
hiddenimports += ['src.core.llm_providers']
hiddenimports += ['src.knowledge.batch_upload']
hiddenimports += ['src.knowledge.kb_pack']
hiddenimports += ['src.knowledge.rebuild']
hiddenimports += ['src.qa.bm25_index']
hiddenimports += ['src.qa.reranker']

a = Analysis(
    [os.path.join(_project_root, 'src', 'launch.py')],
    pathex=[_project_root],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# 通用 EXE 配置
_common_exe_kwargs = dict(
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    icon=None,
)

if sys.platform == 'darwin':
    # macOS：onedir 模式 + .app 结构
    # EXE 只含入口脚本，不含 binaries/datas（这些放到 COLLECT 里）
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name='Insight',
        console=False,
        codesign_identity=None,
        entitlements_file=None,
        **_common_exe_kwargs,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.zipfiles,
        a.datas,
        strip=False,
        upx=False,
        name='Insight',
    )
    app = BUNDLE(
        coll,
        name='Insight.app',
        icon=None,
        bundle_identifier='com.insight.app',
        info_plist=os.path.join(_project_root, 'build', 'Info.plist'),
    )
else:
    # Windows：onedir 模式（生成 dist/Insight/ 文件夹，再用 NSIS 打成安装包）
    # EXE 只含入口脚本，binaries/datas 放到 COLLECT 里（与 macOS 一致）
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name='Insight',
        console=False,
        codesign_identity=None,
        entitlements_file=None,
        **_common_exe_kwargs,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.zipfiles,
        a.datas,
        strip=False,
        upx=False,
        name='Insight',
    )

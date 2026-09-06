# -*- mode: python ; coding: utf-8 -*-
"""BAZZ.AGENT PyInstaller spec —— 相对路径版（本地 & GitHub Actions 通用）。

数据源相对 spec 所在目录（= 仓库根）解析，不再依赖任何绝对路径；
sqlite3 原生 DLL（_sqlite3.pyd / sqlite3.dll）仅在解释器 DLLs 目录存在时显式补入，
python.org 官方构建（GitHub Actions setup-python）由 PyInstaller 依赖分析自动收集。

用法（仓库根）：
    pyinstaller --noconfirm --clean ScoutBackend.spec \
        --distpath <STAGE>/dist_py --workpath <STAGE>/build_py
"""
import os
import sys

from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.hooks import collect_all

_R = os.path.abspath(SPECPATH)          # 仓库根 = spec 所在目录


def _p(*parts):
    return os.path.join(_R, *parts)


# ---------- 数据资源（相对仓库根） ----------
datas = [
    (_p("src"), "src"),
    (_p(".agents"), ".agents"),
    (_p("plugins"), "plugins"),
    (_p("frontend", "dist"), "frontend/dist"),
    (_p("wallet_bridge"), "wallet_bridge"),
    (_p("skills-lock.json"), "."),
    (_p(".env.example"), "."),
    (_p("assets"), "assets"),
]

# ---------- sqlite3 原生库（存在才补，python.org 官方解释器可缺省） ----------
binaries = []
_dlls = os.path.join(sys.base_prefix, "DLLs")
for _f in ("_sqlite3.pyd", "sqlite3.dll"):
    _x = os.path.join(_dlls, _f)
    if os.path.isfile(_x):
        binaries.append((_x, "."))

hiddenimports = []
hiddenimports += collect_submodules("src")

for _m in (
    "fastapi", "starlette", "uvicorn", "requests", "PIL", "sqlalchemy", "urllib3",
    "aiohttp", "websockets", "charset_normalizer", "certifi", "idna", "cryptography",
    "anyio", "sniffio", "h11", "httpx", "dotenv", "httpcore", "click", "docutils",
    "jinja2", "markupsafe", "itsdangerous", "werkzeug", "pyyaml", "packaging", "pluggy",
):
    tmp_ret = collect_all(_m)
    datas += tmp_ret[0]
    binaries += tmp_ret[1]
    hiddenimports += tmp_ret[2]


a = Analysis(
    [_p("launcher.py")],
    pathex=[_p("src"), _R],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ScoutBackend',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='ScoutBackend',
)

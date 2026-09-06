"""PyInstaller entry: launch backend FastAPI server in portable mode.

onedir 模式：launcher.exe 启动 → import desktop_app → 启动 uvicorn。
state.py 的 .scout.db 重定向到 exe 同级目录，保证跨重启保留。
"""
import os
import sys

# frozen exe 所在目录（dist/ScoutBackend/）；资源在 _internal/
_EXE_DIR = os.path.dirname(os.path.abspath(__file__))
_INTERN = os.path.join(_EXE_DIR, "_internal")
BASE = _INTERN if os.path.isdir(_INTERN) else _EXE_DIR

# 让 stdlib 与 PyInstaller 收集的模块可解析
sys.path.insert(0, BASE)
os.chdir(BASE)

# 把 .scout.db / 用户数据放在 exe 同级（可写、跨重启保留）
os.environ["BAZZ_APP_DIR"] = _EXE_DIR
os.environ.setdefault("BAZZ_PORT", "8080")

import desktop_app                       # PyInstaller 自动收集
import state                             # 同上
state.DB_PATH = os.path.join(_EXE_DIR, ".scout.db")

import uvicorn
uvicorn.run(
    desktop_app.app,
    host="127.0.0.1",
    port=int(os.environ["BAZZ_PORT"]),
    log_level="warning",
)
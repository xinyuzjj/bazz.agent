"""PyInstaller entry: launch backend FastAPI server in portable mode.

onedir 模式：ScoutBackend.exe 启动 → import desktop_app → 启动 uvicorn。
state.db / 用户数据放在 exe 同级（可写、跨重启保留），即 <EXE_DIR>/workspace/。
"""
import os
import sys

# PyInstaller onedir 下，main 脚本的 __file__ 解析为 <sys._MEIPASS>/launcher.py（即 _internal/），
# 不能用作 exe 真实目录；sys.executable 才是真实 ScoutBackend.exe 的路径，永远指向 exe 所在目录。
_EXE_DIR = os.path.dirname(os.path.abspath(sys.executable))
_INTERN = os.path.join(_EXE_DIR, "_internal")
BASE = _INTERN if os.path.isdir(_INTERN) else _EXE_DIR

# 让 stdlib 与 PyInstaller 收集的模块可解析
sys.path.insert(0, BASE)
os.chdir(BASE)

# BAZZ_APP_DIR 必须是 exe 同级目录（约定 = ScoutBackend/），不是 _internal/。
# 早期版本用 __file__ 推 _EXE_DIR 错位到 _internal，导致 workspace 数据落到 _internal/workspace/
# 且 RUNTIME_DIR 解析错位（找不到内置 Node + baw）。v1.2.14 改用 sys.executable 修正。
os.environ["BAZZ_APP_DIR"] = _EXE_DIR
os.environ.setdefault("BAZZ_PORT", "8080")

# 切到 exe 目录（state.py 的 state.db 路径基于此 + BAZZ_APP_DIR 一致）
os.chdir(_EXE_DIR)

import desktop_app                       # PyInstaller 自动收集

# 注意：不要再覆写 state.DB_PATH！旧版这里曾把 state.DB_PATH 指到 <exe_dir>/.scout.db，
# 导致打包态状态库永远落在 ScoutBackend/ 里、不进工作区（更新即丢）。
# state.py 已统一走 workspace.DB_PATH（Electron 注入 BAZZ_WORKSPACE = <安装根>/workspace）。

# 启动后端：端口被占（重复拉起/冲突）时自动切空闲端口并按中文提示，避免 [Errno 10048] 裸崩
desktop_app.run_serve(int(os.environ["BAZZ_PORT"]))
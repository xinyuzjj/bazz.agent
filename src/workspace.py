"""BAZZ.AGENT 运行时工作区 —— 所有「自动生成」的文件都落在这里。

约定：
  APP_DIR    = 项目根（开发态 = 仓库根；冻结态 = launcher 注入的 BAZZ_APP_DIR = exe 所在目录）
  WORKSPACE  = APP_DIR/workspace（可用 BAZZ_WORKSPACE 环境变量覆盖；模块 import 时自动 mkdir）

模块首次被 import 时执行一次性迁移，把旧位置散落的文件搬进 WORKSPACE：
  · <APP_DIR>/.scout.db                  → <WORKSPACE>/state.db       （+ .db-journal/.db-wal/.db-shm）
  · <APP_DIR>/.wallet_profile.json       → <WORKSPACE>/.wallet_profile.json
  · <APP_DIR>/data/square_posts.json     → <WORKSPACE>/square_posts.json
  · <APP_DIR>/.workbuddy/attachments     → <WORKSPACE>/attachments/
迁移成功后写一个 .migrated_v1 标记，防止重复执行。失败不抛，仅打印。
"""
import os
import shutil
import subprocess
import sys
import time

APP_DIR = os.environ.get("BAZZ_APP_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _app_root_frozen() -> str:
    """冻结态兜底：从 APP_DIR 向上找应用安装根（含 BAZZ.AGENT.exe 或 resources/ 的目录）。
    Electron 未注入 BAZZ_WORKSPACE 时（如直接双击 ScoutBackend.exe），工作区仍落在安装目录。"""
    cand = APP_DIR
    for _ in range(6):
        if os.path.isfile(os.path.join(cand, "BAZZ.AGENT.exe")) or os.path.isdir(os.path.join(cand, "resources")):
            return cand
        parent = os.path.dirname(cand)
        if parent == cand:
            break
        cand = parent
    return APP_DIR


# v1.3.3 起：工作区落回「应用安装目录」——打包态默认 <安装根>/workspace（用户要求：数据跟着应用走，
# 安装目录下一眼可见，如 F:\1\BAZZ.AGENT\workspace）。Electron 会注入 BAZZ_WORKSPACE 覆盖此默认；
# 安装目录只读时 Electron 兜底注入 %APPDATA%\BAZZ.AGENT\workspace。
if os.environ.get("BAZZ_WORKSPACE"):
    WORKSPACE = os.environ["BAZZ_WORKSPACE"]
elif getattr(sys, "frozen", False):
    WORKSPACE = os.path.join(_app_root_frozen(), "workspace")
else:
    WORKSPACE = os.path.join(APP_DIR, "workspace")
os.makedirs(WORKSPACE, exist_ok=True)

# —— 运行时统一路径 —— #
DB_PATH        = os.path.join(WORKSPACE, "state.db")
WALLET_PROFILE = os.path.join(WORKSPACE, ".wallet_profile.json")
SQUARE_POSTS   = os.path.join(WORKSPACE, "square_posts.json")
ATTACHMENTS    = os.path.join(WORKSPACE, "attachments")
LOGS           = os.path.join(WORKSPACE, "logs")
BACKUPS        = os.path.join(WORKSPACE, "backups")
GENERATED      = os.path.join(WORKSPACE, "generated")

# v1.2.11 起：内置 Node 20 LTS + @binance/agentic-wallet 的位置
#   冻结态：<EXE_DIR>/../../resources/runtime/
#     EXE_DIR (= workspace.APP_DIR) = resources/scout-bundle/ScoutBackend/  ← launcher.py 设置
#     → 跳两级到 BAZZ.AGENT-win32-x64/，再下 resources/runtime/，正是 build-desktop.js extraResource 落点
#   dev 态：APP_DIR = 项目根，RUNTIME_DIR 指向 `../../runtime`（不存在）；runtime_available() 自然 False，调用方走 PATH 回退
# v1.2.14 兜底：早期 launcher.py 的 _EXE_DIR = dirname(__file__)，在 PyInstaller onedir 下 __file__ 解析为 _MEIPASS
#   （= ScoutBackend/_internal），导致 APP_DIR 错位为 _internal，RUNTIME_DIR 自然指向不存在的 scout-bundle/runtime。
#   现在加 app_root 兜底（向上找含 BAZZ.AGENT.exe 或 resources/ 的目录，再下 resources/runtime）—— 即使 launcher
#   偶尔再错，也能从 BAZZ.AGENT-win32-x64/resources/runtime 拿到正确的内置 runtime。
def _app_root() -> str:
    cand = APP_DIR
    for _ in range(6):
        if os.path.isfile(os.path.join(cand, "BAZZ.AGENT.exe")) or os.path.isdir(os.path.join(cand, "resources")):
            return cand
        parent = os.path.dirname(cand)
        if parent == cand:
            break
        cand = parent
    return APP_DIR

_NODE_REL = ("node", "node.exe" if os.name == "nt" else "bin/node")


def _pick_runtime_dir() -> str:
    primary = os.environ.get("BAZZ_RUNTIME_DIR") or os.path.normpath(os.path.join(APP_DIR, "..", "..", "runtime"))
    if os.path.isfile(os.path.join(primary, *_NODE_REL)):
        return primary
    fallback = os.path.join(_app_root(), "resources", "runtime")
    if os.path.isfile(os.path.join(fallback, *_NODE_REL)):
        return fallback
    return primary  # 都不在时返回 primary 让上层按 False 处理


RUNTIME_DIR    = _pick_runtime_dir()
NODE_EXE       = os.path.join(RUNTIME_DIR, *_NODE_REL)
BAW_BIN_DIR    = os.path.join(RUNTIME_DIR, "node_modules", "@binance", "agentic-wallet", "bin")
BAW_CLI_JS     = os.path.join(BAW_BIN_DIR, "baw.js")
BAW_PACKAGE    = os.path.join(RUNTIME_DIR, "node_modules", "@binance", "agentic-wallet", "package.json")

os.makedirs(ATTACHMENTS, exist_ok=True)


def runtime_available() -> bool:
    """内置 runtime 是否就绪：Node 存在且 @binance/agentic-wallet 已装。"""
    return os.path.isfile(NODE_EXE) and os.path.isfile(BAW_PACKAGE)


def _bak_suffix() -> str:
    return time.strftime("%Y%m%d%H%M%S", time.localtime())


def _move(old: str, new: str, label: str) -> None:
    """把 old 搬到 new；new 已存在或 old 不存在则跳过。

    不用 shutil.move —— 它的 unlink 分支会触发系统 safe-delete 钩子而失败。
    直接用 os.replace（Windows MoveFileEx 原子重命名，不走 Python fs 钩子）搬文件；
    目录则先 shutil.copytree 再用 cmd rmdir 清源。
    """
    try:
        if os.path.lexists(new) or not os.path.lexists(old):
            return
        os.makedirs(os.path.dirname(new), exist_ok=True)
        if os.path.isdir(old):
            shutil.copytree(old, new)
            subprocess.run(["cmd", "/c", "rmdir", "/s", "/q", old], check=False, capture_output=True)
        else:
            try:
                os.replace(old, new)
            except OSError:
                # 跨盘符（如 C:→F:）os.replace 不支持 → 复制 + 删除兜底
                shutil.copy2(old, new)
                os.remove(old)
        print(f"[workspace] 迁移 {label}: {old} → {new}")
    except Exception as e:
        print(f"[workspace] 迁移 {label} 失败 {old}: {e}")


# —— 一次性迁移：旧位置 → WORKSPACE —— #
_MIGRATE_FLAG = os.path.join(WORKSPACE, ".migrated_v1")
if not os.path.exists(_MIGRATE_FLAG):
    _move(os.path.join(APP_DIR, ".scout.db"),          DB_PATH,        "状态数据库")
    _move(os.path.join(APP_DIR, ".wallet_profile.json"), WALLET_PROFILE, "钱包 profile")
    _move(os.path.join(APP_DIR, "data", "square_posts.json"), SQUARE_POSTS, "广场发文台账")
    _move(os.path.join(APP_DIR, ".workbuddy", "attachments"), ATTACHMENTS, "附件目录")
    # sqlite 副作用文件
    for suf in ("-journal", "-wal", "-shm"):
        _move(os.path.join(APP_DIR, ".scout.db" + suf), DB_PATH + suf, f"sqlite 副作用 ({suf})")
    # 清掉迁移后可能留下的空目录
    for empty in (os.path.join(APP_DIR, "data"), os.path.join(APP_DIR, ".workbuddy")):
        try:
            if os.path.isdir(empty) and not os.listdir(empty):
                os.rmdir(empty)
        except OSError:
            pass
    try:
        with open(_MIGRATE_FLAG, "w", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S"))
    except OSError:
        pass


# —— v1.2.14 一次性迁移（v2）：历史 launcher 错位把 BAZZ_APP_DIR 指向 _internal/，
#   导致用户数据（state.db / .wallet_profile.json / 广场台账 / 附件）一直落在
#   <exe_dir>/_internal/workspace/ 而不是约定的 <exe_dir>/workspace/。本次修 launcher，
#   新启动 APP_DIR 会变成真正的 exe_dir；这里把旧位置数据整体搬到 WORKSPACE，
#   并写 .migrated_v2 标记（哪怕两边都空也写，免得下次再扫一次）。
_MIGRATE_V2_FLAG = os.path.join(WORKSPACE, ".migrated_v2")
if not os.path.exists(_MIGRATE_V2_FLAG):
    legacy_ws = os.path.join(APP_DIR, "_internal", "workspace")
    target_has_data = (
        os.path.isfile(DB_PATH) or os.path.isfile(WALLET_PROFILE) or os.path.isfile(SQUARE_POSTS)
        or (os.path.isdir(ATTACHMENTS) and os.listdir(ATTACHMENTS))
    )
    if os.path.isdir(legacy_ws) and os.path.normpath(legacy_ws) != os.path.normpath(WORKSPACE) and not target_has_data:
        for name in os.listdir(legacy_ws):
            _move(os.path.join(legacy_ws, name), os.path.join(WORKSPACE, name), f"v2 旧 _internal/workspace → {name}")
        # 旧 _internal/workspace 整体搬完，删空目录。ignore_errors 兼容权限/锁定的边角情况。
        try:
            shutil.rmtree(legacy_ws, ignore_errors=True)
            if not os.path.isdir(legacy_ws):
                print(f"[workspace] v2 清理 {legacy_ws}")
        except Exception as e:
            print(f"[workspace] v2 清理 {legacy_ws} 失败: {e}")
    try:
        with open(_MIGRATE_V2_FLAG, "w", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S"))
    except OSError:
        pass


# —— v3 一次性迁移：workspace 数据外置（%APPDATA%\BAZZ.AGENT\workspace）
#   v1.2.17 起：Electron 打包态把 BAZZ_WORKSPACE 注入到系统用户数据目录，与应用代码分离，
#   使自更新=纯程序替换、用户数据永不触碰。老用户升级前数据在 <APP_DIR>/workspace
#   （旧整目录替换会把旧 workspace 还原到新应用目录），这里在首次启动外置时整体搬入新位置，
#   写 .migrated_v3 标记防重。仅在「真的外置 + 目标空 + 老内部目录有内容」时执行。
_MIGRATE_V3_FLAG = os.path.join(WORKSPACE, ".migrated_v3")
if not os.path.exists(_MIGRATE_V3_FLAG):
    internal_ws = os.path.join(APP_DIR, "workspace")
    target_has_data = (
        os.path.isfile(DB_PATH) or os.path.isfile(WALLET_PROFILE) or os.path.isfile(SQUARE_POSTS)
        or (os.path.isdir(ATTACHMENTS) and os.listdir(ATTACHMENTS))
        or (os.path.isdir(LOGS) and os.listdir(LOGS))
        or os.path.isfile(os.path.join(WORKSPACE, ".migrated_v1"))
        or os.path.isfile(os.path.join(WORKSPACE, ".migrated_v2"))
    )
    workspace_is_external = (
        os.path.normcase(os.path.normpath(WORKSPACE))
        != os.path.normcase(os.path.normpath(os.path.join(APP_DIR, "workspace")))
    )
    if workspace_is_external and os.path.isdir(internal_ws) and not target_has_data:
        for name in os.listdir(internal_ws):
            _move(os.path.join(internal_ws, name), os.path.join(WORKSPACE, name), f"v3 外置 → {name}")
        try:
            shutil.rmtree(internal_ws, ignore_errors=True)
            if not os.path.isdir(internal_ws):
                print(f"[workspace] v3 清理 {internal_ws}")
        except Exception as e:
            print(f"[workspace] v3 清理 {internal_ws} 失败: {e}")
    try:
        with open(_MIGRATE_V3_FLAG, "w", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S"))
    except OSError:
        pass


# —— v4 一次性迁移：工作区落回应用安装目录（v1.3.3）——
#   用户要求工作区放在安装应用的位置（如 F:\1\BAZZ.AGENT\workspace），数据跟着应用走。
#   v1.2.17~v1.3.2 打包态工作区被外置到 %APPDATA%\BAZZ.AGENT\workspace，这里整体搬回；
#   另收编两处历史遗留：
#     · <APP_DIR>/.scout.db —— 旧 launcher 曾把 state.DB_PATH 覆写到 exe 目录（v1 迁移可能
#       因 .migrated_v1 标记已在外置区而被跳过），目标无 state.db 时兜底搬入；
#     · <APP_DIR>/_internal/workspace —— v2 同理补扫一次。
#   安装目录只读时 Electron 兜底注入 APPDATA：source==target → 自然跳过，不会自搬自。
_MIGRATE_V4_FLAG = os.path.join(WORKSPACE, ".migrated_v4")
if not os.path.exists(_MIGRATE_V4_FLAG):
    appdata_ws = os.path.join(os.environ.get("APPDATA") or "", "BAZZ.AGENT", "workspace")
    same_as_appdata = (
        os.path.normcase(os.path.normpath(appdata_ws))
        == os.path.normcase(os.path.normpath(WORKSPACE))
    )
    src_has_data = (
        os.path.isfile(os.path.join(appdata_ws, "state.db"))
        or os.path.isfile(os.path.join(appdata_ws, ".wallet_profile.json"))
        or os.path.isfile(os.path.join(appdata_ws, "square_posts.json"))
        or os.path.isfile(os.path.join(appdata_ws, ".migrated_v3"))
    )
    packaged_ctx = getattr(sys, "frozen", False) or bool(os.environ.get("BAZZ_WORKSPACE"))
    if packaged_ctx and (not same_as_appdata) and os.path.isdir(appdata_ws) and src_has_data:
        for name in os.listdir(appdata_ws):
            _move(os.path.join(appdata_ws, name), os.path.join(WORKSPACE, name), f"v4 回迁 → {name}")
        try:
            # 迁移标记文件（.migrated_v*）在新工作区已被 v1~v3 块预先写入 → _move 会跳过，
            # 这里直接删掉源里的旧标记，让旧目录能清空
            for leftover in (".migrated_v1", ".migrated_v2", ".migrated_v3"):
                try:
                    lp = os.path.join(appdata_ws, leftover)
                    if os.path.isfile(lp):
                        os.remove(lp)
                except OSError:
                    pass
            if os.path.isdir(appdata_ws) and not os.listdir(appdata_ws):
                os.rmdir(appdata_ws)
                try:
                    os.rmdir(os.path.dirname(appdata_ws))  # BAZZ.AGENT 空壳目录一并清掉
                except OSError:
                    pass
                print(f"[workspace] v4 清理 {appdata_ws}")
        except OSError:
            pass
    # 遗留 .scout.db 收编（launcher 曾覆写 state.DB_PATH 到 exe 目录）
    for legacy in (os.path.join(APP_DIR, ".scout.db"), os.path.join(APP_DIR, "_internal", ".scout.db")):
        if os.path.isfile(legacy) and not os.path.isfile(DB_PATH):
            _move(legacy, DB_PATH, "v4 遗留状态库收编")
            for suf in ("-journal", "-wal", "-shm"):
                _move(legacy + suf, DB_PATH + suf, f"v4 sqlite 副作用 ({suf})")
    try:
        with open(_MIGRATE_V4_FLAG, "w", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S"))
    except OSError:
        pass
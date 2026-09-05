"""Hermes 风格插件 SDK host（轻量级）

目录约定（项目根/plugins/<pluginId>/）：
- plugin.json   manifest：{id, name, version, description, author, commands:[{name,description,args_schema}]}
- main.py       命令处理器：每个 command 对应一个同名函数 fn(params)->str|dict{ok,text,data}

能力：
- 发现/列表：list_plugins / get_plugin / is_plugin
- 执行：exec_command(pid, command, params)（动态加载 main.py，按需刷新）
- LLM 集成：list_command_schemas() 把命令转成 OpenAI function schema（name=<pid>.<command>），
  agent_core._run_llm_agent 每次循环拼进 tools，让 LLM 能直接调用插件命令。
"""
import os
import json
import importlib.util
import threading

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGINS_DIR = os.path.join(PROJECT_DIR, "plugins")

_lock = threading.Lock()
_mod_cache = {}


def _plugin_dirs():
    if not os.path.isdir(PLUGINS_DIR):
        return []
    out = []
    try:
        for name in sorted(os.listdir(PLUGINS_DIR)):
            d = os.path.join(PLUGINS_DIR, name)
            if os.path.isdir(d) and os.path.isfile(os.path.join(d, "plugin.json")):
                out.append(name)
    except Exception:
        pass
    return out


def _read_manifest(pid):
    path = os.path.join(PLUGINS_DIR, pid, "plugin.json")
    try:
        with open(path, encoding="utf-8") as f:
            man = json.load(f)
        if isinstance(man, dict):
            man.setdefault("id", pid)
            man.setdefault("name", pid)
            man.setdefault("version", "0.1.0")
            man.setdefault("description", "")
            man.setdefault("commands", [])
            return man
    except Exception:
        pass
    return None


def list_plugins():
    out = []
    for pid in _plugin_dirs():
        man = _read_manifest(pid)
        if man:
            out.append(man)
    return out


def get_plugin(pid):
    if pid not in _plugin_dirs():
        return None
    return _read_manifest(pid)


def is_plugin(pid):
    return pid in _plugin_dirs()


def _load_module(pid):
    """缓存加载 main.py；mtime 变化时重新加载（便于热改）。"""
    d = os.path.join(PLUGINS_DIR, pid)
    mp = os.path.join(d, "main.py")
    if not os.path.isfile(mp):
        return None
    mtime = os.path.getmtime(mp)
    with _lock:
        hit = _mod_cache.get(pid)
        if hit and hit[0] == mtime:
            return hit[1]
        try:
            spec = importlib.util.spec_from_file_location(f"scout_plugin_{pid}", mp)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            _mod_cache[pid] = (mtime, mod)
            return mod
        except Exception:
            return None


def list_command_schemas():
    """把已装插件命令转成 LLM function schema（Hermes：动态工具注入）。"""
    tools = []
    for man in list_plugins():
        pid = man.get("id")
        for c in man.get("commands") or []:
            schema = c.get("args_schema") or {}
            tools.append({
                "type": "function",
                "function": {
                    "name": f"{pid}.{c.get('name')}",
                    "description": f"【插件 {man.get('name', pid)}】{c.get('description', c.get('name'))}",
                    "parameters": {
                        "type": "object",
                        "properties": schema.get("properties") or {},
                        "required": schema.get("required") or [],
                    },
                },
            })
    return tools


def exec_command(pid, command, params=None):
    """执行插件命令：params dict。返回 {ok, text?, data?, error?}。"""
    if pid not in _plugin_dirs():
        return {"ok": False, "error": f"plugin '{pid}' not found"}
    man = _read_manifest(pid)
    cmd = next((c for c in (man or {}).get("commands") or [] if c.get("name") == command), None)
    if cmd is None:
        return {"ok": False, "error": f"command '{command}' not in plugin '{pid}'"}
    mod = _load_module(pid)
    if mod is None:
        return {"ok": False, "error": f"plugin '{pid}' 缺少可执行的 main.py 处理器"}
    fn = getattr(mod, command, None)
    if not callable(fn):
        return {"ok": False, "error": f"handler '{command}' missing in plugin main.py"}
    try:
        r = fn(params or {})
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "plugin": pid, "command": command}
    if isinstance(r, str):
        return {"ok": True, "text": r, "plugin": pid, "command": command}
    if isinstance(r, dict):
        return {"ok": bool(r.get("ok", True)),
                "text": r.get("text") or r.get("detail") or ("" if r.get("ok", True) else r.get("error", "")),
                "data": r.get("data"),
                "error": r.get("error"),
                "plugin": pid, "command": command}
    return {"ok": True, "text": str(r), "plugin": pid, "command": command}

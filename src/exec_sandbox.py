"""受限本地执行沙箱（Hermes 式「能干活」但受限）：

- read_file   ：只读项目根内文件；黑名单目录(.git/.venv/node_modules/dist/.agents)不可读；
- write_file  ：只允许写 .workbuddy/generated/ 与 workspace/（自动建目录），防逃逸/防覆盖工程文件；
- run_command ：cwd 锁定项目根，可执行文件白名单(python/node/npx/npm/git)，超时 + 输出截断；
                一律子进程隔离，绝不走 shell=True。
- run_skill   ：执行已安装 skill（baw / scripts/cli.mjs），同受命令白名单约束。

策略违规抛 SandboxError；上层转成工具错误卡，绝不静默执行。
"""
import os
import re
import shlex
import shutil
import subprocess
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # E:/.../binance-agent-os-scout
GEN_DIR = os.path.join(PROJECT_ROOT, ".workbuddy", "generated")
WORK_DIR = os.path.join(PROJECT_ROOT, "workspace")

# 读黑名单：这些目录永远不可读（密钥/依赖/构建产物/agent 私档）
READ_BLACKLIST = {".git", ".venv", "venv", "node_modules", "__pycache__", "dist", ".agents", ".curator_backups", "bootstrap-cache"}


def _decode_text(data: bytes) -> str:
    """智能解码子进程/文件字节：优先 UTF-8，失败回退 GBK（中文 Windows 控制台输出），
    再不行 latin-1 兜底。彻底解决「OS 中文报错在对话里变成乱码」。"""
    if not data:
        return ""
    for enc in ("utf-8", "gbk"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, ValueError):
            continue
    return data.decode("latin-1")


def _looks_binary(raw: bytes) -> bool:
    """粗略判断是否二进制（SQLite 库/图片等），用于 read_text 提示而非吐乱码。"""
    sample = raw[:2048]
    if not sample:
        return False
    if b"\x00" in sample:
        return True
    ctl = sum(
        1 for b in sample
        if b == 0 or (b < 9) or (b in (11, 12)) or (13 < b < 32)
    )
    return ctl / len(sample) > 0.3


def _allowlist_path(path: str) -> bool:
    """已安装 skill 的公开文件可读/可执行例外：.agents/skills/<name>/SKILL.md 和 scripts/cli.mjs。
    这两个是 npx skills add 拉下来的标准接口，允许 Agent 读说明 + 直接 node 跑 cli。"""
    rel = os.path.relpath(path, PROJECT_ROOT).replace("\\", "/")
    parts = rel.split("/")
    if len(parts) >= 4 and parts[0] == ".agents" and parts[1] == "skills":
        # 白名单后缀
        if parts[-1] == "SKILL.md":
            return True
        if len(parts) >= 4 and parts[-2] == "scripts" and parts[-1] == "cli.mjs":
            return True
    return False
# 可写根目录（白名单）：只在这两个目录里允许模型落盘
WRITE_ROOTS = [GEN_DIR, WORK_DIR]

# 可执行命令白名单：token0 → 实际可执行文件
# 真二进制白名单：进程隔离直接 subprocess 调
EXE_BIN = {
    "python": sys.executable,
    "python3": sys.executable,
    "py": sys.executable,
    "node": "node",
    "npx": "npx",
    "npm": "npm",
    "git": "git",
    "baw": "baw",  # Binance Agentic Wallet CLI：run_skill(binance-agentic-wallet) 走它
}
# Python wrapper 命令：高频 read/inspect（部分写），跨平台稳定、走沙箱白名单路径校验
PY_WRAPPERS = {"ls", "cat", "head", "tail", "wc", "grep", "find", "pwd",
               "mkdir", "touch", "cp", "mv", "rm", "echo"}
ALLOWED_EXE = {**EXE_BIN, **{w: w for w in PY_WRAPPERS}}
# 绝对禁止出现在命令里的危险片段（白名单之外的兜底）
FORBIDDEN_PATTERNS = [r"\brm\s+-rf", r"\bdel\s+/[sSqQ]", r"\bformat\s+[a-zA-Z]:", r"\bmkfs", r"\bshutdown", r">\s*/dev/sd"]

DEFAULT_TIMEOUT = 15          # 秒
DEFAULT_MAX_OUT = 4000        # 输出截断字符


class SandboxError(Exception):
    pass


def _norm(p: str) -> str:
    return os.path.normpath(os.path.abspath(os.path.expanduser((p or "").strip() or "")))


def _under(root: str, p: str) -> bool:
    try:
        return os.path.commonpath([root, p]) == root
    except Exception:
        return False


def _is_blacklisted(p: str) -> bool:
    rel = os.path.relpath(p, PROJECT_ROOT)
    parts = rel.split(os.sep)
    if any(part in READ_BLACKLIST for part in parts):
        # 例外：已安装 skill 的 SKILL.md / scripts/cli.mjs 可读/可执行
        if _allowlist_path(p):
            return False
        return True
    return False


def resolve_read(path: str) -> str:
    """校验可读路径，返回绝对路径。非法抛 SandboxError。"""
    p = _norm(path or "")
    if not p:
        raise SandboxError("path 为空")
    if not _under(PROJECT_ROOT, p):
        raise SandboxError(f"只能读取项目目录内的文件（越界: {path}）")
    if _is_blacklisted(p):
        raise SandboxError(f"路径命中黑名单目录，不可读: {path}")
    if not os.path.isfile(p):
        raise SandboxError(f"文件不存在: {path}")
    return p


def resolve_write(path: str) -> str:
    """校验可写路径：必须落在 WRITE_ROOTS 之下。返回绝对路径。"""
    p = _norm(path or "")
    if not p:
        raise SandboxError("path 为空")
    ok = any(_under(root, p) for root in WRITE_ROOTS)
    if not ok:
        allowed = "、".join(os.path.relpath(r, PROJECT_ROOT) for r in WRITE_ROOTS)
        raise SandboxError(f"只允许写入工作区白名单目录（{allowed}），越界: {path}")
    if _is_blacklisted(p):
        raise SandboxError(f"路径命中黑名单目录，不可写: {path}")
    return p


def read_text(path: str, max_bytes: int = 6000, max_lines: int = 260) -> dict:
    """读取文本内容（截断防爆）。返回 {content, path, bytes, truncated}。"""
    p = resolve_read(path)
    try:
        raw = open(p, "rb").read()
    except Exception as e:
        raise SandboxError(f"读取失败: {e}")
    if _looks_binary(raw):
        return {"content":
                "[二进制文件（如 SQLite 数据库 state.db / 图片 / 压缩包），非文本，无法直接在此预览。"
                "请用数据库工具（如 DB Browser for SQLite）或对应应用查看。]",
                "path": p, "bytes": len(raw), "truncated": False, "binary": True}
    truncated = len(raw) > max_bytes
    data = raw[:max_bytes]
    text = _decode_text(data)
    lines = text.splitlines()
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        truncated = True
    return {"content": "\n".join(lines), "path": p, "bytes": len(raw), "truncated": truncated}


def write_text(path: str, content: str, append: bool = False) -> dict:
    """写入文本（先校验白名单）。返回 {path, bytes, created}。"""
    p = resolve_write(path)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    created = not os.path.exists(p)
    try:
        with open(p, "a" if append else "w", encoding="utf-8") as f:
            f.write(content or "")
    except Exception as e:
        raise SandboxError(f"写入失败: {e}")
    return {"path": p, "bytes": len(content or ""), "created": created}


def _tokenize(cmdline: str):
    try:
        toks = shlex.split(cmdline or "", posix=True)
    except Exception:
        raise SandboxError("命令解析失败（检查引号）")
    if not toks:
        raise SandboxError("command 为空")
    return toks


def _split_chain(cmdline: str):
    """按顶层 && / || / ; 拆分命令串（保留引号/转义）。

    返回 [(segment, op_before_this_segment), ...]，op_before 是「本段之前的运算符」：
    - 第一段 op 为 None（无条件执行）
    - && 后接的段仅当上一步成功（exit=0）才执行
    - || 后接的段仅当上一步失败（exit!=0）才执行
    - ;  后接的段总是执行
    不支持单个 |（管道）——遇到则抛 SandboxError。
    """
    out = []
    buf = []
    in_quote = None
    op_before_next = None
    i = 0
    n = len(cmdline or "")
    while i < n:
        ch = cmdline[i]
        if in_quote:
            if ch == '\\' and i + 1 < n and cmdline[i+1] == in_quote:
                buf.append(ch); buf.append(cmdline[i+1]); i += 2; continue
            if ch == in_quote:
                in_quote = None
            buf.append(ch); i += 1; continue
        if ch in ('"', "'"):
            in_quote = ch; buf.append(ch); i += 1; continue
        if ch == '\\' and i + 1 < n:
            buf.append(ch); buf.append(cmdline[i+1]); i += 2; continue
        if ch == '|':
            if i + 1 < n and cmdline[i+1] == '|':
                out.append(("".join(buf).strip(), op_before_next))
                buf = []
                op_before_next = "||"
                i += 2; continue
            raise SandboxError("沙箱不支持单根管道 |（要看前 N 行请用 head -n N <file>，要 head/grep + 限制条数直接给参数）")
        if ch == '&' and i + 1 < n and cmdline[i+1] == '&':
            out.append(("".join(buf).strip(), op_before_next))
            buf = []
            op_before_next = "&&"
            i += 2; continue
        if ch == ';':
            out.append(("".join(buf).strip(), op_before_next))
            buf = []
            op_before_next = ";"
            i += 1; continue
        buf.append(ch); i += 1
    if buf:
        out.append(("".join(buf).strip(), op_before_next))
    return [(s, op) for s, op in out if s]


# ---------------- Python wrapper 命令（跨平台稳定，路径仍走沙箱校验） ----------------

def _flag(toks, names, default=None):
    """简易 flag 解析：返回 names 列表中第一次出现的下一个参数值（若已 =X 也可）。"""
    i = 0
    while i < len(toks):
        if toks[i] in names and i + 1 < len(toks):
            return toks[i + 1], toks[:i] + toks[i + 2:]
        if toks[i].startswith(tuple(n + "=" for n in names)):
            return toks[i].split("=", 1)[1], toks[:i] + toks[i + 1:]
        i += 1
    return default, toks


def _fmt_size(n: int) -> str:
    if n < 1024:
        return str(n)
    if n < 1024 * 1024:
        return f"{n/1024:.1f}K"
    return f"{n/1024/1024:.1f}M"


def _cwd_path(arg: str) -> str:
    """解析 wrapper 命令里的相对路径 → 绝对路径（基于项目根）。"""
    p = (arg or ".").strip()
    if not os.path.isabs(p):
        p = os.path.join(PROJECT_ROOT, p)
    return _norm(p)


def _w_ls(toks):
    """ls [-l/-a/-la/-h/-F/-R/-r/-t/-S] [path...]

    支持：组合 flag（-la/-lat 等）、多路径、递归（-R）、按 mtime/大小/反向排序。
    默认行为：cwd 下非隐藏、非递归、按名排序、详细格式（含大小/时间）。
    """
    flag = {"l": True, "a": False, "h": False, "F": False, "R": False, "r": False, "t": False, "S": False}
    paths = []
    for t in toks[1:]:
        if t.startswith("-") and t != "-":
            for ch in t.lstrip("-"):
                if ch in flag:
                    flag[ch] = True
                else:
                    raise SandboxError(f"ls 不支持 flag -{ch}（支持 -l/-a/-la/-h/-F/-R/-r/-t/-S）")
        else:
            paths.append(t)
    if not paths:
        paths = ["."]

    def _walk(abs_p, depth_remaining, recurse):
        out_lines = []
        try:
            entries = list(os.scandir(abs_p))
        except Exception as e:
            raise SandboxError(f"无法列出 {abs_p}: {e}")
        # 过滤隐藏（-a 包含；默认隐藏 . 开头的）
        if not flag["a"]:
            entries = [e for e in entries if not e.name.startswith(".")]
        # 排序：S 优先大小；其次 t=mtime；默认字母
        if flag["S"]:
            entries = sorted(entries, key=lambda e: (e.stat(follow_symlinks=False).st_size if e.is_file(follow_symlinks=False) else -1), reverse=True)
        elif flag["t"]:
            entries = sorted(entries, key=lambda e: e.stat(follow_symlinks=False).st_mtime, reverse=True)
        else:
            entries = sorted(entries, key=lambda e: (not e.is_file(follow_symlinks=False), e.name.lower()))
        if flag["r"]:
            entries = list(reversed(entries))
        for e in entries:
            try:
                st = e.stat(follow_symlinks=False)
                size = _fmt_size(st.st_size)
                mtime = time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime))
                kind = "d" if e.is_dir(follow_symlinks=False) else "-"
                marker = ("/" if kind == "d" else "") if flag["F"] else ""
                out_lines.append(f"{kind} {size:>6} {mtime} {e.name}{marker}")
            except Exception:
                out_lines.append(f"?  {e.name}")
        if recurse:
            for e in entries:
                if e.is_dir(follow_symlinks=False):
                    sub = os.path.join(abs_p, e.name)
                    if _is_blacklisted(sub):
                        out_lines.append(f"⛔ 跳过黑名单: {e.name}/")
                        continue
                    out_lines.append("")
                    out_lines.append(f"📁 {os.path.relpath(sub, PROJECT_ROOT)}/")
                    out_lines.extend(_walk(sub, depth_remaining - 1, depth_remaining - 1 > 0))
        return out_lines

    blocks = []
    for p in paths:
        abs_p = _cwd_path(p)
        if not os.path.isdir(abs_p):
            raise SandboxError(f"目录不存在: {p}")
        if _is_blacklisted(abs_p):
            raise SandboxError(f"路径命中黑名单目录: {p}")
        rel = os.path.relpath(abs_p, PROJECT_ROOT) or "."
        body = _walk(abs_p, depth_remaining=99, recurse=flag["R"])
        blocks.append(f"📁 {rel}（{len(body)} 项）\n" + "\n".join(body))
    return "\n\n".join(blocks)


def _w_cat(toks):
    """cat <file>...    拼出文件内容（截断 8KB）。"""
    if len(toks) < 2:
        raise SandboxError("cat 用法: cat <file>...")
    out = []
    for f in toks[1:]:
        p = resolve_read(f)
        with open(p, "r", encoding="utf-8", errors="replace") as fh:
            data = fh.read(8000)
            if len(data) >= 8000:
                data += "\n... (已截断到 8000 字节)"
        out.append(f"=== {os.path.relpath(p, PROJECT_ROOT)} ===\n{data}")
    return "\n\n".join(out)


def _w_head_tail(toks, kind: str):
    """head/tail -n N <file>    默认 N=20。"""
    n = 20
    if len(toks) >= 3 and toks[1] == "-n":
        try: n = int(toks[2])
        except Exception: raise SandboxError("-n 需整数")
        target = toks[3] if len(toks) > 3 else None
    elif len(toks) >= 4 and toks[2] == "-n":
        try: n = int(toks[3])
        except Exception: raise SandboxError("-n 需整数")
        target = toks[1]
    else:
        target = toks[1] if len(toks) > 1 else None
    if not target:
        raise SandboxError(f"{kind} 用法: {kind} [-n N] <file>")
    p = resolve_read(target)
    with open(p, "r", encoding="utf-8", errors="replace") as fh:
        lines = fh.readlines()
    sel = lines[:n] if kind == "head" else lines[-n:]
    return f"=== {os.path.relpath(p, PROJECT_ROOT)}（{kind} -n {n}/{len(lines)}）===\n" + "".join(sel)


def _w_wc(toks):
    """wc -l <file>"""
    args = toks[1:]
    if not args or args[0] != "-l" or len(args) < 2:
        raise SandboxError("wc 用法: wc -l <file>")
    p = resolve_read(args[1])
    with open(p, "r", encoding="utf-8", errors="replace") as fh:
        data = fh.read()
    return f"{len(data.splitlines())}  {os.path.relpath(p, PROJECT_ROOT)}"


def _w_grep(toks):
    """grep <pattern> <path>    简化：path 可为文件或目录（递归匹配 .* 文件内）。"""
    if len(toks) < 3:
        raise SandboxError("grep 用法: grep <pattern> <path>")
    pattern = toks[1]
    target = _cwd_path(toks[2])
    rx = re.compile(pattern)
    hits = []
    files = []
    if os.path.isfile(target):
        files.append(target)
    elif os.path.isdir(target):
        for root, _, fnames in os.walk(target):
            if _is_blacklisted(root):
                continue
            for fn in fnames:
                files.append(os.path.join(root, fn))
    else:
        raise SandboxError(f"路径不存在: {toks[2]}")
    for fp in files[:2000]:
        if _is_blacklisted(fp):
            continue
        try:
            with open(fp, "r", encoding="utf-8", errors="replace") as fh:
                for i, ln in enumerate(fh, 1):
                    if rx.search(ln):
                        rel = os.path.relpath(fp, PROJECT_ROOT)
                        hits.append(f"{rel}:{i}:{ln.rstrip()}")
                        if len(hits) >= 500:
                            break
        except Exception:
            continue
        if len(hits) >= 500:
            break
    if not hits:
        return f"（无匹配）pattern={pattern} target={toks[2]}"
    return f"匹配 {len(hits)} 处：\n" + "\n".join(hits[:200])


def _w_find(toks):
    """find <path> [-name <glob>] [-type f|d] [-maxdepth N] [-mindepth N] [-not] [-path <glob>]

    简化 GNU find；谓词支持：-name/-iname/-type/-path/-maxdepth/-mindepth/-not。
    -not 仅否定其后紧邻的一个谓词；-not -name X 等价于「名不是 X」。
    """
    if len(toks) < 2:
        raise SandboxError("find 用法: find <path> [-name/-path/-maxdepth/-mindepth/-type/-not ...]")
    target = toks[1]
    preds = []   # list of dict {negate, name, value}
    i = 2
    while i < len(toks):
        t = toks[i]
        if t in ("-name", "-iname", "-path", "-maxdepth", "-mindepth", "-type") and i + 1 < len(toks):
            preds.append({"negate": False, "name": t, "value": toks[i+1]})
            i += 2
        elif t == "-not":
            preds.append({"negate": True, "name": "_MARK_", "value": None})
            i += 1
        elif t == "-true" or t == "-false":
            # 占位谓词（永真/永假），与 -not 配合可凑出任意表达式
            preds.append({"negate": False, "name": t, "value": None})
            i += 1
        else:
            raise SandboxError(f"find 不支持参数 {t}（支持 -name/-path/-maxdepth/-mindepth/-type/-not）")
    abs_p = _cwd_path(target)
    if not os.path.isdir(abs_p):
        raise SandboxError(f"目录不存在: {target}")
    if _is_blacklisted(abs_p):
        raise SandboxError(f"路径命中黑名单目录: {target}")
    import fnmatch

    maxdepth = None
    mindepth = 0
    for p in preds:
        if p["name"] == "-maxdepth":
            try: maxdepth = int(p["value"])
            except: raise SandboxError("-maxdepth 需整数")
        elif p["name"] == "-mindepth":
            try: mindepth = int(p["value"])
            except: raise SandboxError("-mindepth 需整数")
    base_depth = abs_p.rstrip(os.sep).count(os.sep)

    def _match(name, full, is_dir, depth_from_base):
        """遍历 preds 链表：-not 仅否定其后紧邻的谓词，其余按 and 串联。"""
        # 先按链逐项判断
        i = 0
        while i < len(preds):
            p = preds[i]
            if p["name"] == "_MARK_":  # -not
                # 找其后第一个真谓词
                if i + 1 < len(preds) and preds[i+1]["name"] != "_MARK_":
                    nxt = preds[i+1]
                    truth = _single_match(nxt, name, full, is_dir, depth_from_base)
                    i += 2
                    if truth:  # -not 真谓词为真 → 整体失败
                        return False
                    continue
                # -not 后面没谓词 → 视为「-not -true」，永假
                return False
            if p["name"] in ("-maxdepth", "-mindepth"):
                i += 1
                continue
            truth = _single_match(p, name, full, is_dir, depth_from_base)
            if not truth:
                return False
            i += 1
        return True

    def _single_match(p, name, full, is_dir, depth_from_base):
        if p["name"] == "-name":
            return fnmatch.fnmatchcase(name, p["value"])
        if p["name"] == "-iname":
            return fnmatch.fnmatchcase(name.lower(), p["value"].lower())
        if p["name"] == "-path":
            # Windows 下 os.path.relpath 用反斜杠，但用户传的多是 POSIX 风格的 glob，统一替换
            return fnmatch.fnmatchcase(full.replace("\\", "/"), p["value"].replace("\\", "/"))
        if p["name"] == "-type":
            if p["value"] == "f":
                return not is_dir
            if p["value"] == "d":
                return is_dir
            raise SandboxError(f"find -type 仅支持 f/d: {p['value']}")
        if p["name"] == "-true":
            return True
        if p["name"] == "-false":
            return False
        return True

    out = []
    for root, dirs, files in os.walk(abs_p):
        rel_root = os.path.relpath(root, abs_p) or "."
        depth_from_base = root.count(os.sep) - base_depth
        if maxdepth is not None and depth_from_base > maxdepth:
            dirs[:] = []; continue
        if _is_blacklisted(root):
            dirs[:] = []
            continue
        for d in list(dirs):
            full = os.path.join(root, d)
            rel = os.path.relpath(full, PROJECT_ROOT)
            if depth_from_base < mindepth:
                continue
            if maxdepth is not None and depth_from_base > maxdepth:
                continue
            if not _match(d, rel, True, depth_from_base):
                continue
            out.append(rel)
            if len(out) >= 2000: break
        if len(out) >= 2000: break
        for f in files:
            full = os.path.join(root, f)
            rel = os.path.relpath(full, PROJECT_ROOT)
            if depth_from_base < mindepth:
                continue
            if maxdepth is not None and depth_from_base > maxdepth:
                continue
            if not _match(f, rel, False, depth_from_base):
                continue
            out.append(rel)
            if len(out) >= 2000: break
        if len(out) >= 2000: break
    header = f"📂 find {target}（{len(out)} 项）"
    return header + ("\n" + "\n".join(out) if out else "\n（无匹配）")


def _w_pwd(toks):
    return PROJECT_ROOT


def _w_mkdir(toks):
    """mkdir [-p] <path>"""
    rec = "-p" in toks[1:]
    rest = [t for t in toks[1:] if t != "-p"]
    if not rest:
        raise SandboxError("mkdir 用法: mkdir [-p] <path>")
    p = resolve_write(rest[0])
    os.makedirs(p, exist_ok=rec)
    return f"✅ mkdir {os.path.relpath(p, PROJECT_ROOT)}"


def _w_touch(toks):
    """touch <path>"""
    if len(toks) < 2:
        raise SandboxError("touch 用法: touch <path>")
    p = resolve_write(toks[1])
    os.makedirs(os.path.dirname(p), exist_ok=True)
    if not os.path.exists(p):
        open(p, "w", encoding="utf-8").close()
    else:
        os.utime(p, None)
    return f"✅ touch {os.path.relpath(p, PROJECT_ROOT)}"


def _w_cp(toks):
    """cp <src> <dst>"""
    if len(toks) != 3:
        raise SandboxError("cp 用法: cp <src> <dst>")
    src = resolve_read(toks[1])
    dst = resolve_write(toks[2])
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    import shutil
    shutil.copy2(src, dst)
    return f"✅ cp {os.path.relpath(src, PROJECT_ROOT)} → {os.path.relpath(dst, PROJECT_ROOT)}"


def _w_mv(toks):
    """mv <src> <dst>"""
    if len(toks) != 3:
        raise SandboxError("mv 用法: mv <src> <dst>")
    src = resolve_read(toks[1])
    dst_p = _cwd_path(toks[2])
    if not _under(PROJECT_ROOT, dst_p) or _is_blacklisted(dst_p):
        dst = resolve_write(toks[2])
    else:
        os.makedirs(os.path.dirname(dst_p), exist_ok=True)
        dst = dst_p
    import shutil
    shutil.move(src, dst)
    return f"✅ mv {os.path.relpath(src, PROJECT_ROOT)} → {os.path.relpath(dst, PROJECT_ROOT)}"


def _w_rm(toks):
    """rm <path>    拒绝 -r/-rf 等递归与强制；路径必须落在沙箱写白名单目录里。"""
    if any(t in toks for t in ("-r", "-rf", "-fr", "-R")):
        raise SandboxError("rm 拒绝 -r/-rf 等递归/强制（删除文件即可，目录另写脚本）")
    args = [t for t in toks[1:] if not t.startswith("-")]
    if not args:
        raise SandboxError("rm 用法: rm <file>")
    p = resolve_write(args[0])  # 必须落在白名单写目录
    if os.path.isdir(p):
        raise SandboxError("rm 不接受目录；改用脚本/手动")
    if os.path.isfile(p):
        os.remove(p)
    return f"✅ rm {os.path.relpath(p, PROJECT_ROOT)}"


def _w_echo(toks):
    return " ".join(toks[1:]) if len(toks) > 1 else ""


_WRAPPERS = {
    "ls": _w_ls, "cat": _w_cat, "head": lambda t: _w_head_tail(t, "head"),
    "tail": lambda t: _w_head_tail(t, "tail"), "wc": _w_wc, "grep": _w_grep,
    "find": _w_find, "pwd": _w_pwd, "mkdir": _w_mkdir, "touch": _w_touch,
    "cp": _w_cp, "mv": _w_mv, "rm": _w_rm, "echo": _w_echo,
}


def _run_one(command: str, timeout: int, max_out: int) -> dict:
    """执行单条命令（不含 &&/||/; 链式）。返回 {exit_code, output, truncated, cmd, elapsed}。"""
    toks = _tokenize(command)
    exe0 = toks[0].lower()
    if exe0 not in ALLOWED_EXE:
        raise SandboxError(f"命令不在白名单（{', '.join(sorted(ALLOWED_EXE))}）: {toks[0]}")
    lower = command.lower()
    for pat in FORBIDDEN_PATTERNS:
        if re.search(pat, lower):
            raise SandboxError(f"命令包含危险操作，已拦截: {command}")
    t0 = time.time()
    if exe0 in _WRAPPERS:
        try:
            out = _WRAPPERS[exe0](toks)
            code = 0
        except SandboxError:
            raise
        except Exception as e:
            raise SandboxError(f"{exe0} 执行失败: {e}")
        truncated = len(out) > max_out
        return {"exit_code": code, "output": out[:max_out], "truncated": truncated,
                "cmd": command, "elapsed": round(time.time() - t0, 2)}
    argv = [ALLOWED_EXE[exe0]] + toks[1:]
    # Windows 上 npm 全局命令是 .cmd/.bat 包装器，CreateProcess 不能直跑 → 用 cmd /c 包裹
    exe_path = shutil.which(ALLOWED_EXE[exe0]) or ALLOWED_EXE[exe0]
    if os.name == "nt" and exe_path.lower().endswith((".cmd", ".bat")):
        argv = ["cmd", "/c"] + argv
    try:
        proc = subprocess.run(argv, cwd=PROJECT_ROOT, capture_output=True, timeout=timeout)
        out = _decode_text(proc.stdout or b"")
        if proc.stderr:
            out += "\n[stderr]\n" + _decode_text(proc.stderr)
        code = proc.returncode
    except subprocess.TimeoutExpired:
        raise SandboxError(f"命令超时（>{timeout}s），已终止: {command[:80]}")
    except FileNotFoundError as e:
        raise SandboxError(f"命令不存在: {e}")
    except Exception as e:
        raise SandboxError(f"执行失败: {e}")
    truncated = len(out) > max_out
    return {"exit_code": code, "output": out[:max_out], "truncated": truncated,
            "cmd": command, "elapsed": round(time.time() - t0, 2)}


def run_command(command: str, timeout: int = DEFAULT_TIMEOUT, max_out: int = DEFAULT_MAX_OUT) -> dict:
    """执行白名单命令，支持顶层 &&/||/; 链式。cwd 锁项目根。返回 {exit_code, output, truncated, cmd}。

    链式语义：
    - 'a && b'：a 退出码 0 才执行 b
    - 'a || b'：a 退出码非 0 才执行 b
    - 'a ; b'：总是顺序执行
    - 'a' 单条无操作符 → 直接执行
    """
    chain = _split_chain(command)
    if not chain:
        raise SandboxError("command 为空")
    if len(chain) == 1 and chain[0][1] is None:
        # 单条命令，无链式
        return _run_one(chain[0][0], timeout=timeout, max_out=max_out)

    t0 = time.time()
    parts_out = []
    last_code = 0
    truncated_any = False
    for idx, (seg, op) in enumerate(chain):
        # 短路判定：op 是「本段之前的」分隔符，第一段 op=None 不参与短路
        if op == "&&" and last_code != 0:
            parts_out.append(f"(跳过 {seg[:60]!r}: 上一步 exit={last_code})")
            continue
        if op == "||" and last_code == 0:
            parts_out.append(f"(跳过 {seg[:60]!r}: 上一步已成功)")
            continue
        # 单段输出也截断到 max_out（整链共享配额：剩余/剩余段数 ≈ 平均）
        seg_budget = max(800, max_out // max(1, len(chain) - idx))
        try:
            r = _run_one(seg, timeout=timeout, max_out=seg_budget)
        except SandboxError as e:
            # 错误立即中断整链
            raise SandboxError(f"{seg[:80]} → {e}")
        last_code = r["exit_code"]
        if r["truncated"]:
            truncated_any = True
        parts_out.append(r["output"])
    full = "\n".join(parts_out)
    if len(full) > max_out:
        truncated_any = True
        full = full[:max_out]
    return {"exit_code": last_code, "output": full, "truncated": truncated_any,
            "cmd": command, "elapsed": round(time.time() - t0, 2)}


def run_skill_cmd(skill_name: str, args: str = "", timeout: int = 90, max_out: int = 6000) -> dict:
    """执行 skill（复用 skills_client 的智能路由：baw 扩展 / launcher 绕 Windows dispatch / 直跑）。
    超时 90s（baw 冷启动/网络慢）。"""
    import skills_client
    sdir = os.path.join(skills_client.AGENTS_DIR, skill_name)
    cli = os.path.join(sdir, "scripts", "cli.mjs")
    arg_s = (args or "").strip()
    try:
        if skill_name in skills_client._BAW_SKILLS:
            # v1.2.11：与 skills_client 一致，baw 调用走 wallet_runtime.baw_invocation() 解析
            import wallet_runtime
            toks = shlex.split(arg_s, posix=True) if arg_s else ["wallet", "status"]
            cmd, mode = wallet_runtime.baw_invocation(toks)
            if mode == "missing":
                raise SandboxError("baw 不可用：APP 内置 runtime 未就绪且 PATH 也未找到 baw。")
            cmd = " ".join(shlex.quote(c) for c in cmd)
        elif os.path.isfile(cli):
            if skills_client._cli_uses_meta_url_dispatch(cli):
                launcher = os.path.join(os.path.dirname(os.path.abspath(skills_client.__file__)), "skill_launcher.mjs")
                cmd = f"node {shlex.quote(launcher)} {shlex.quote(sdir)} " + (arg_s if arg_s else "")
            else:
                cmd = f"node {shlex.quote(cli)} " + (arg_s if arg_s else "")
        else:
            raise SandboxError(f"skill `{skill_name}` 无本地执行入口（仅指引类，直接问模型要说明即可）")
    except SandboxError:
        raise
    except Exception as e:
        raise SandboxError(f"skill 命令构造失败: {e}")
    return run_command(cmd, timeout=timeout, max_out=max_out)


def skill_is_executable(skill_name: str) -> bool:
    """该 skill 是否属于本地可执行类（需要审批确认）。"""
    import os as _os
    import skills_client
    if skill_name in skills_client._BAW_SKILLS:
        return True
    sdir = _os.path.join(skills_client.AGENTS_DIR, skill_name)
    return _os.path.isfile(_os.path.join(sdir, "scripts", "cli.mjs"))


if __name__ == "__main__":
    # 自检
    print("root:", PROJECT_ROOT)
    print("read ok:", read_text(__file__)["content"][:20])
    try:
        resolve_read(r"C:/Windows/win.ini")
    except SandboxError as e:
        print("read escape blocked:", e)
    try:
        resolve_write(os.path.join(PROJECT_ROOT, "src", "agent_core.py"))
    except SandboxError as e:
        print("write escape blocked:", e)
    print("run ok:", run_command("python -c \"print(1+1)\"")["output"].strip())

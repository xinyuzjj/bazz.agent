"""定时调度（Hermes 式 cron）

轻量实现，后端启动一个守护线程周期性检查。支持两种 schedule：
  - "interval:3600"   每 3600 秒
  - "m h * * *"       每日 m 分 h 时（支持字段式，目前取前两个字段=分/时）
内置任务：daily_scan_report —— 扫描 Top 行情 + 生成报告，存入固定会话「BAZZ Agent 日报」。
任务配置存 settings("cron_jobs") = JSON 列表。
"""
import os
import json
import time
import threading
import re

from state import (get_setting, set_setting, new_conversation,
                   add_message, list_conversations)

REPORT_CONV_TITLE = "BAZZ Agent 日报"
DEFAULT_JOBS = [
    {"id": "default-daily", "name": "每日市场扫描 + 日报", "schedule": "0 9 * * *",
     "task": "daily_scan_report", "enabled": True, "last_run": 0, "next_run": 0},
]


def get_jobs():
    raw = get_setting("cron_jobs", "")
    if not raw:
        return [dict(j) for j in DEFAULT_JOBS]
    try:
        return json.loads(raw)
    except Exception:
        return [dict(j) for j in DEFAULT_JOBS]


def save_jobs(jobs):
    set_setting("cron_jobs", json.dumps(jobs, ensure_ascii=False))


def add_job(name, schedule, task="daily_scan_report", enabled=True, persona=""):
    jobs = get_jobs()
    jid = os.urandom(4).hex()
    jobs.append({"id": jid, "name": name, "schedule": schedule, "task": task,
                 "enabled": enabled, "last_run": 0, "next_run": _next(schedule, time.time()),
                 "persona": persona})
    save_jobs(jobs)
    return jid


def remove_job(jid):
    save_jobs([j for j in get_jobs() if j["id"] != jid])


def get_job(jid):
    return next((j for j in get_jobs() if j["id"] == jid), None)


def set_job_enabled(jid, enabled):
    jobs = get_jobs()
    for j in jobs:
        if j["id"] == jid:
            j["enabled"] = enabled
            j["next_run"] = _next(j["schedule"], time.time()) if enabled else 0
    save_jobs(jobs)


def _parse(spec):
    if spec.startswith("interval:"):
        return ("interval", int(spec.split(":")[1]))
    parts = spec.split()
    if len(parts) >= 2:
        return ("cron", parts)
    return ("interval", 21600)


def parse_time_to_spec(s):
    """把自然语言/简写时间归一到调度器可读的 cron / interval 规格字符串。
    支持：
      - '09:00' / '9:30' → cron 'MM HH * * *'
      - '9 点' / '9点'    → cron '0 9 * * *'
      - '每天 09:00'      → 同 09:00（去掉『每天/每日』前缀）
      - '0 9 * * *' 等 5 字段 cron 原样回传
      - 'interval:30m' / 'interval:1h' / 'interval:3600' → 'interval:<秒>'
    解析失败返回 None。"""
    if not s:
        return None
    t = str(s).strip().strip("`'\"")
    t = re.sub(r"^(每天|每日|每天早上|每天上午|每天下午|每天晚上|每天\\s*)", "", t).strip()
    if not t:
        return None
    m = re.match(r"^interval\s*:\s*(\d+)\s*([smhd]?)$", t, re.I)
    if m:
        n = int(m.group(1))
        u = (m.group(2) or "s").lower()
        sec = {"s": 1, "m": 60, "h": 3600, "d": 86400}.get(u, 1) * n
        return f"interval:{sec}"
    if re.match(r"^interval\s*:\s*\d+$", t):
        return t  # 已规范化的 interval:秒
    m = re.match(r"^(\d{1,2})\s*[:点]\s*(\d{1,2})$", t)
    if m:
        h, mn = int(m.group(1)), int(m.group(2))
        if 0 <= h <= 23 and 0 <= mn <= 59:
            return f"{mn} {h} * * *"
    m = re.match(r"^(\d{1,2})\s*[点时:]\s*(\d{1,2})\s*分?$", t)
    if m:
        h, mn = int(m.group(1)), int(m.group(2))
        if 0 <= h <= 23 and 0 <= mn <= 59:
            return f"{mn} {h} * * *"
    # 中文口语：『N 点』『N 点半』『N 点 N 分』
    m = re.match(r"^(\d{1,2})\s*点\s*半$", t)
    if m:
        h = int(m.group(1))
        if 0 <= h <= 23:
            return f"30 {h} * * *"
    m = re.match(r"^(\d{1,2})\s*点$", t)
    if m:
        h = int(m.group(1))
        if 0 <= h <= 23:
            return f"0 {h} * * *"
    # 『上午 N 点 / 下午 N 点』→ 12 小时制换算（仅整点；半/分带也支持）
    m = re.match(r"^(上午|早上|凌晨)\s*(\d{1,2})\s*点(\s*半)?$", t)
    if m:
        h = int(m.group(2)); half = bool(m.group(3))
        return f"{(30 if half else 0)} {h % 12} * * *"
    m = re.match(r"^(下午|晚上)\s*(\d{1,2})\s*点(\s*半)?$", t)
    if m:
        h = int(m.group(2)); half = bool(m.group(3))
        h12 = h % 12 + 12 if h != 12 else 12
        return f"{(30 if half else 0)} {h12} * * *"
    parts = t.split()
    if len(parts) >= 2 and all(re.match(r"^[\d*/,\-]+$", p) for p in parts):
        return t
    return None


def _next(spec, now):
    kind, val = _parse(spec)
    if kind == "interval":
        return now + val
    try:
        minute = int(val[0])
        hour = int(val[1])
        t = time.localtime(now)
        nt = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, hour, minute, 0, 0, 0, t.tm_isdst))
        if nt <= now:
            nt += 86400
        return nt
    except Exception:
        return now + 3600


def _run_market_scan():
    """默认任务：全市场 Top 行情扫描 + 风险提示 → 报告文本。"""
    from scanner import scan_universe
    from reporter import format_report
    from risk_guard import check_risks
    sigs = scan_universe(min_change_pct=2.0, min_funding=0.01, universe_size=300, max_signals=12)
    best = sigs[0] if sigs else {"symbol": "BTCUSDT", "price": "0", "change_pct": 0, "funding_rate": 0}
    tips = check_risks(best, 50)
    return format_report(best, {"status": "cron_daily"}, tips)


def _run_meme_scan():
    """妖币雷达日报：取 Monster Radar 同源 ignition + takeoff 两组，按 RPS 给简报。"""
    try:
        from scanner import get_ignition_coins, get_monster_coins
        ign = get_ignition_coins(limit=8) or []
        tkf = get_monster_coins(limit=8) or []
    except Exception as e:
        return f"（妖币雷达暂不可用：{e}）"

    def _fmt(rows, label):
        if not rows:
            return f"- {label}：暂无信号"
        head = []
        for r in rows[:6]:
            sym = r.get("symbol", "?")
            px = r.get("price", "n/a")
            qv = r.get("quote_volume", 0) or 0
            ch = r.get("change_pct", 0) or 0
            head.append(f"- **{sym}** · 价 {px} · 24h {ch:+.2f}% · 量 {qv:,.0f} USDT")
        return "\n".join(head)

    lines = [
        "### 🐸 妖币雷达日报 · " + time.strftime("%Y-%m-%d %H:%M"),
        "",
        "**启动前埋伏（ignition）**",
        _fmt(ign, "启动前"),
        "",
        "**起飞中跟踪（takeoff）**",
        _fmt(tkf, "起飞中"),
    ]
    return "\n".join(lines)


TASK_HANDLERS = {
    "daily_scan_report": _run_market_scan,
    "meme_scan_report": _run_meme_scan,
}


def run_job(job):
    """执行一个任务，返回摘要文本。若绑定 persona，则报告写入该 Agent 的专属会话（Hermes Routines）。"""
    task = job.get("task") or "daily_scan_report"
    handler = TASK_HANDLERS.get(task)
    if not handler:
        return f"未知任务类型: {task}"
    try:
        report = handler()
    except Exception as e:
        report = f"任务 {task} 执行失败：{type(e).__name__}: {e}"
    persona = job.get("persona") or ""
    if persona:
        # Hermes Routines：结果投递到该 Agent 的专属会话
        from state import last_persona_conv, touch_conversation
        cid = last_persona_conv(persona)
        if not cid:
            cid = new_conversation(f"@{persona} · Routines", persona=persona)
        add_message(cid, "assistant",
                    f"## 📅 Routine · {time.strftime('%Y-%m-%d %H:%M')}\n\n" + report,
                    tools=[{"icon": "📅", "name": "定时任务", "status": "success",
                            "detail": f"{task} · 周期 {job.get('schedule','?')}"}],
                    data={"persona": persona, "intent": "routine"})
        touch_conversation(cid)
        return report
    convs = [c for c in list_conversations() if c["title"] == REPORT_CONV_TITLE]
    cid = convs[0]["id"] if convs else new_conversation(REPORT_CONV_TITLE)
    add_message(cid, "assistant",
                f"## 📅 定时日报 · {time.strftime('%Y-%m-%d %H:%M')}\n\n" + report,
                tools=[{"icon": "📅", "name": "定时扫描", "status": "success",
                        "detail": f"{task} · 周期 {job.get('schedule','?')}"}])
    return report


def _loop():
    while True:
        try:
            now = time.time()
            jobs = get_jobs()
            changed = False
            for job in jobs:
                if not job.get("enabled"):
                    continue
                nxt = job.get("next_run", 0)
                if now >= nxt:
                    try:
                        run_job(job)
                    except Exception:
                        pass
                    job["last_run"] = now
                    job["next_run"] = _next(job["schedule"], now)
                    changed = True
            if changed:
                save_jobs(jobs)
        except Exception:
            pass
        time.sleep(30)


_thread = None


def start():
    """后端启动时调用，拉起调度守护线程。"""
    global _thread
    if _thread is None or not _thread.is_alive():
        _thread = threading.Thread(target=_loop, daemon=True)
        _thread.start()

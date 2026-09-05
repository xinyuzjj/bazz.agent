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


def run_job(job):
    """执行一个任务，返回摘要文本。若绑定 persona，则报告写入该 Agent 的专属会话（Hermes Routines）。"""
    from scanner import scan_universe
    from reporter import format_report
    from risk_guard import check_risks

    sigs = scan_universe(min_change_pct=2.0, min_funding=0.01, universe_size=300, max_signals=12)
    best = sigs[0] if sigs else {"symbol": "BTCUSDT", "price": "0", "change_pct": 0, "funding_rate": 0}
    tips = check_risks(best, 50)
    report = format_report(best, {"status": "cron_daily"}, tips)
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
                            "detail": f"{len(sigs)} 个信号"}],
                    data={"persona": persona, "intent": "routine"})
        touch_conversation(cid)
        return report
    convs = [c for c in list_conversations() if c["title"] == REPORT_CONV_TITLE]
    cid = convs[0]["id"] if convs else new_conversation(REPORT_CONV_TITLE)
    add_message(cid, "assistant",
                f"## 📅 定时日报 · {time.strftime('%Y-%m-%d %H:%M')}\n\n" + report,
                tools=[{"icon": "📅", "name": "定时扫描", "status": "success",
                        "detail": f"{len(sigs)} 个信号"}])
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

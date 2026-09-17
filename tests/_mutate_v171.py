"""v1.7.1 护栏变异验证：把每条新护栏对应的实现改坏，确认它真的会红。

⚠️ 全程在**副本**里做（%TEMP%/v171mut），绝不碰工作区源码。
⚠️ 变异条目里的 `old` 锚点必须是**唯一**的；命中次数 ≠ 1 会打印「跳过」并计入失败，
   因为那说明锚点写得不够精确（同一个陷阱：锚点本身也会随源码演进失效）。

运行：.venv/Scripts/python.exe tests/_mutate_v171.py
"""
import os
import shutil
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
MUT = os.path.join(os.environ.get("TEMP") or "/tmp", "v171mut")
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")

MUTATIONS = [
    # —— C3 ① TTL 可配 ——
    ("env 覆盖失效（radar2_ttl 永远返回常量）",
     "src/scanner.py",
     "            if v > 0 and math.isfinite(v):\n                return v",
     "            if False:\n                return v"),
    ("非法 env 被放行（inf 会让缓存永不失效）",
     "src/scanner.py",
     "            if v > 0 and math.isfinite(v):\n                return v",
     "            if v > 0:\n                return v"),
    ("缓存判断绕开 radar2_ttl()，改回写死的常量",
     "src/scanner.py",
     'if not force and _radar2_cache["data"] and now - _radar2_cache["ts"] < radar2_ttl():\n        return _with_age(_radar2_cache["data"], now)',
     'if not force and _radar2_cache["data"] and now - _radar2_cache["ts"] < RADAR2_TTL:\n        return _with_age(_radar2_cache["data"], now)'),
    # —— C3 ② 新鲜度可见 ——
    ("_with_age 直接改原对象（污染缓存，第二次读 age 会叠加）",
     "src/scanner.py",
     "    out = dict(data or {})\n    try:\n        ts = float(out.get(\"updated_at\") or 0.0)",
     "    out = data if data is not None else {}\n    try:\n        ts = float(out.get(\"updated_at\") or 0.0)"),
    ("stale 判定忽略 payload 的 ttl，改用模块常量",
     "src/scanner.py",
     '    out["stale"] = bool(age > ttl)',
     '    out["stale"] = bool(age > RADAR2_TTL)'),
    ("age_sec 不夹到 0（时钟回拨时出现负年龄）",
     "src/scanner.py",
     "    age = max(0.0, float(now) - ts) if ts > 0 else 0.0",
     "    age = (float(now) - ts) if ts > 0 else 0.0"),
    ("get_radar_v2 的缓存返回路径不补 age（延迟重新变不可见）",
     "src/scanner.py",
     '    if not force and _radar2_cache["data"] and now - _radar2_cache["ts"] < radar2_ttl():\n        return _with_age(_radar2_cache["data"], now)',
     '    if not force and _radar2_cache["data"] and now - _radar2_cache["ts"] < radar2_ttl():\n        return _radar2_cache["data"]'),
    ("前端轮询回到 180s",
     "frontend/src/views/MarketsView.tsx",
     "const t = setInterval(() => fetchRadar(), 60_000);",
     "const t = setInterval(() => fetchRadar(), 180_000);"),
    ("前端自己拿本地时钟算 age（服务端下发被无视）",
     "frontend/src/views/MarketsView.tsx",
     '        typeof d?.age_sec === "number"',
     '        typeof d?.updated_at === "number"'),
    ("陈旧时不标红（旧缓存兜底看起来像新鲜数据）",
     "frontend/src/views/MarketsView.tsx",
     '                <span className="pill pill-red text-[10.5px]"\n                  title={t("markets.staleTip"',
     '                <span className="pill pill-dim text-[10.5px]"\n                  title={t("markets.staleTip"'),
    ("i18n 只补中文（英文界面显示裸 key）",
     "frontend/src/i18n/locales.ts",
     '  "markets.fresh": "data @{age} old",\n',
     ""),
    ("mock 不带 age_sec（隔离预览看不到 pill）",
     "frontend/src/preview/mock.ts",
     "updated_at: FIXTURE_SEC - 47, ttl: 300, age_sec: 47, stale: false });",
     "updated_at: FIXTURE_SEC - 47, ttl: 300 });"),

    # —— #10 ① 结算周期 ——
    ("周期参数被无视（sim_cost 回到写死 8h）",
     "src/radar_tracker.py",
     "    cycles = max(0.0, float(hours or 0.0)) / iv",
     "    cycles = max(0.0, float(hours or 0.0)) / FUNDING_CYCLE_H"),
    ("非正/非有限周期被放行（除零或成本凭空消失）",
     "src/radar_tracker.py",
     "    if not (iv > 0) or not math.isfinite(iv):\n        iv = FUNDING_CYCLE_H",
     "    if False:\n        iv = FUNDING_CYCLE_H"),
    ("sim_pnl_net 不透传周期给 sim_cost（成本仍是 8h 口径）",
     "src/radar_tracker.py",
     "    cost = sim_cost(hours, funding, interval_h)",
     "    cost = sim_cost(hours, funding)"),
    ("调用点漏传周期（hold 分支回到默认 8h）",
     "src/radar_tracker.py",
     "                        net, roi_net, cost = sim_pnl_net(\n                            direction, ctx[\"found\"], px,\n                            _hold_hours(t, now), _last_funding(sym), _funding_interval(sym))",
     "                        net, roi_net, cost = sim_pnl_net(\n                            direction, ctx[\"found\"], px,\n                            _hold_hours(t, now), _last_funding(sym))"),
    ("_funding_interval 对未测到回退 0（而不是默认 8h）",
     "src/radar_tracker.py",
     "        iv = float(v) if v is not None else 0.0\n        return iv if iv > 0 else FUNDING_CYCLE_H",
     "        iv = float(v) if v is not None else 0.0\n        return iv"),
    ("两侧默认周期常量不一致（只改一边）",
     "src/scanner.py",
     "FUNDING_CYCLE_DEFAULT_H = 8.0",
     "FUNDING_CYCLE_DEFAULT_H = 4.0"),
    ("_sim_pnl 被塞进成本（毛值被净值悄悄替换）",
     "src/radar_tracker.py",
     "def _sim_pnl(direction: str, found: float, px: float) -> tuple:",
     "def _sim_pnl(direction: str, found: float, px: float, interval_h: float = 8.0) -> tuple:"),

    # —— #10 ② 新数据源解析 ——
    ("fundingInfo 失败也报 measured=True（默认值被当成实测）",
     "src/scanner.py",
     "        return _funding_info_cache[\"intervals\"], bool(_funding_info_cache[\"measured\"])",
     "        return _funding_info_cache[\"intervals\"], True"),
    ("fundingInfo 非正周期也收（0 会让 hours/interval 除零）",
     "src/scanner.py",
     "            if h > 0:\n                iv[sym] = h",
     "            if h != 0:\n                iv[sym] = h"),
    ("fundingInfo 空响应被当成成功（「没数据」读成「没有 4h 周期」）",
     "src/scanner.py",
     "        if not iv:\n            raise ValueError(\"fundingInfo yielded no usable intervals\")",
     "        if False:\n            raise ValueError(\"fundingInfo yielded no usable intervals\")"),
    ("fundingInfo 不再走 TTL 缓存（每轮都打接口）",
     "src/scanner.py",
     "    if _funding_info_cache[\"measured\"] and now - _funding_info_cache[\"ts\"] < FUNDING_INFO_TTL:\n        return _funding_info_cache[\"intervals\"], True",
     "    if False:\n        return _funding_info_cache[\"intervals\"], True"),
    ("bookTicker 零价/交叉盘也入表（写出 0 = 谎称零价差）",
     "src/scanner.py",
     "            if b > 0 and a >= b:\n                mid = (a + b) / 2.0\n                if mid > 0:\n                    out[sym] = round((a - b) / mid * 1e4, 2)",
     "            if True:\n                mid = (a + b) / 2.0\n                if mid != 0:\n                    out[sym] = round((a - b) / mid * 1e4, 2)"),
    ("bookTicker 失败也报 measured=True",
     "src/scanner.py",
     "        return _book_cache[\"spreads\"], bool(_book_cache[\"measured\"])",
     "        return _book_cache[\"spreads\"], True"),
    ("价差换算差 100 倍（百分比与基点混用）",
     "src/scanner.py",
     "                    out[sym] = round((a - b) / mid * 1e4, 2)",
     "                    out[sym] = round((a - b) / mid * 1e2, 2)"),

    # —— ts_series 新列 ——
    ("_TS_COLS 漏掉新列（INSERT 列数与值数不符 → 静默写 0 行）",
     "src/state.py",
     '            "liq_5m", "liq_side", "fut_qv", "fut_spread_bps")',
     '            "liq_5m", "liq_side", "fut_qv")'),
    ("新列与旧列顺序互换（列顺序错位，不报错只错位）",
     "src/state.py",
     '            "mark_price", "index_price", "basis_bps", "spread_bps", "taker_ratio",\n            "liq_5m", "liq_side", "fut_qv", "fut_spread_bps")',
     '            "mark_price", "index_price", "basis_bps", "fut_spread_bps", "taker_ratio",\n            "liq_5m", "liq_side", "fut_qv", "spread_bps")'),
    ("INSERT 元组漏掉新列的值（列数与值数不符）",
     "src/state.py",
     "            _ts_num(r.get(\"fut_qv\")),\n            _ts_num(r.get(\"fut_spread_bps\")),\n        ))",
     "            _ts_num(r.get(\"fut_qv\")),\n        ))"),
    ("未测到写成 0（None 语义被破坏）",
     "src/state.py",
     "            _ts_num(r.get(\"fut_spread_bps\")),",
     "            _ts_num(r.get(\"fut_spread_bps\")) or 0.0,"),
    ("老库不补列（只在别人机器上炸）",
     "src/state.py",
     '        if _tscols and "fut_spread_bps" not in _tscols:\n            c.execute("ALTER TABLE ts_series ADD COLUMN fut_spread_bps REAL")',
     "        if False:\n            c.execute(\"ALTER TABLE ts_series ADD COLUMN fut_spread_bps REAL\")"),

    # —— 接线 ——
    ("扫描不再取盘口（函数定义了但没人调）",
     "src/scanner.py",
     "    book, _bk_ok = get_book_spreads()",
     "    book, _bk_ok = {}, False"),
    ("扫描不再取结算周期",
     "src/scanner.py",
     "    fund_iv, _fi_ok = get_funding_intervals()",
     "    fund_iv, _fi_ok = {}, False"),
    ("行里不下发合约价差",
     "src/scanner.py",
     '             "fut_spread_bps": (book.get(sym) if _bk_ok else None),',
     '             "fut_spread_bps": None,'),
    ("现货口径被合约口径覆盖（两个口径被合成一个）",
     "src/scanner.py",
     '             "spread_bps": r.get("spread_bps"),',
     '             "spread_bps": (book.get(sym) if _bk_ok else None),'),
    ("factors 层不带合约价差（回放读不到）",
     "src/scanner.py",
     '\n                "fut_spread_bps": d.get("fut_spread_bps"),',
     '\n                "fut_spread_bps": None,'),
    ("时序库行不写合约价差（标定样本一个都攒不到）",
     "src/scanner.py",
     '\n            "fut_spread_bps": d.get("fut_spread_bps"),',
     '\n            "fut_spread_bps": None,'),
]


def prepare():
    if os.path.isdir(MUT):
        shutil.rmtree(MUT)
    os.makedirs(MUT)
    shutil.copytree(os.path.join(ROOT, "src"), os.path.join(MUT, "src"))
    shutil.copytree(os.path.join(ROOT, "tests"), os.path.join(MUT, "tests"))
    os.makedirs(os.path.join(MUT, "frontend"))
    shutil.copytree(os.path.join(ROOT, "frontend", "src"), os.path.join(MUT, "frontend", "src"))


def run():
    r = subprocess.run([PY, os.path.join(MUT, "tests", "test_v171_latency_cost.py")],
                       capture_output=True, text=True, cwd=MUT, timeout=300)
    out = (r.stdout or "") + (r.stderr or "")
    fails = [ln.strip()[2:] for ln in out.splitlines() if ln.strip().startswith("- ")]
    return r.returncode, out, fails


def main():
    prepare()
    rc, out, _ = run()
    baseline_ok = rc == 0 and "全部通过" in out
    print(f"基线（未变异）: rc={rc} 全部通过={'全部通过' in out}")
    if not baseline_ok:
        print("⚠️ 基线就不通过，变异验证无意义")
        print(out[-2000:])
        return 2
    bad = []
    for i, (name, rel, old, new) in enumerate(MUTATIONS, 1):
        prepare()
        p = os.path.join(MUT, rel)
        s = open(p, encoding="utf-8", newline="").read()
        if s.count(old) != 1:
            print(f"[{i:2d}] 跳过（锚点命中 {s.count(old)} 次）：{name}")
            bad.append(name)
            continue
        open(p, "w", encoding="utf-8", newline="").write(s.replace(old, new, 1))
        rc, out, fails = run()
        caught = rc != 0 or "全部通过" not in out
        print(f"[{i:2d}] {'✅ 转红' if caught else '❌ 没红'}  {name}")
        if caught and fails:
            print(f"       触发：{fails[0][:96]}")
        if not caught:
            bad.append(name)
    print("\n" + "=" * 60)
    print(f"有效 {len(MUTATIONS) - len(bad)} / {len(MUTATIONS)}")
    if bad:
        print("未生效的变异：")
        for b in bad:
            print("  -", b)
    shutil.rmtree(MUT, ignore_errors=True)
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())

"""档二护栏变异验证：把每条新护栏对应的实现改坏，确认它真的会红。

⚠️ 全程在**副本**里做（/tmp/v170mut），绝不碰工作区源码。
"""
import os
import shutil
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
MUT = os.path.join(os.environ.get("TEMP") or "/tmp", "v170mut")
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")

MUTATIONS = [
    ("列顺序错位（_TS_COLS 里 basis_bps / spread_bps 互换）",
     "src/state.py",
     '            "mark_price", "index_price", "basis_bps", "spread_bps", "taker_ratio",',
     '            "mark_price", "index_price", "spread_bps", "basis_bps", "taker_ratio",'),
    ("未测到写成 0（_ts_num 回退 0.0）",
     "src/state.py",
     "    except Exception:\n        return None\n    return f if f == f and f not in (float(\"inf\"), float(\"-inf\")) else None",
     "    except Exception:\n        return 0.0\n    return f if f == f and f not in (float(\"inf\"), float(\"-inf\")) else 0.0"),
    ("时序落盘没接进扫描（去掉 _ts_persist 调用）",
     "src/scanner.py",
     "    _ts_persist(_ts_rows)\n",
     "    pass\n"),
    ("payload 不下发 ts_store",
     "src/scanner.py",
     '        "ts_store": ts_store_view(),\n',
     ""),
    ("回放把「结局价缺失」当 0 盈亏混进汇总",
     "src/radar_replay.py",
     "    if not found or found <= 0 or not px or px <= 0:\n        return 0.0, 0.0, False",
     "    if not found or found <= 0:\n        return 0.0, 0.0, False\n    if not px or px <= 0:\n        px = found"),
    ("回放不做样本外切分（in = out = full）",
     "src/radar_replay.py",
     "    cut = int(len(kept) * float(split or DEFAULT_SPLIT))",
     "    cut = len(kept)"),
    ("sweep 改按样本内排序（样本内排序没有信息量）",
     "src/radar_replay.py",
     '        "sorted_by": "out_pnl",\n        "ranked": sorted(rows, key=lambda x: -(x["out_pnl"] or 0.0)),',
     '        "sorted_by": "in_pnl",\n        "ranked": sorted(rows, key=lambda x: -(x["in_pnl"] or 0.0)),'),
    ("成本模型静默替换毛值（_sim_pnl 直接返回净值）",
     "src/radar_tracker.py",
     "    chg = (float(px) / float(found) - 1.0) * 100.0\n    roi = (-chg if direction == \"SHORT\" else chg) * LEVERAGE\n    return round(max(-POS_USDT, POS_USDT * roi / 100.0), 2), round(roi, 1)",
     "    chg = (float(px) / float(found) - 1.0) * 100.0\n    roi = (-chg if direction == \"SHORT\" else chg) * LEVERAGE\n    gross = round(max(-POS_USDT, POS_USDT * roi / 100.0), 2)\n    return round(max(-POS_USDT, gross - sim_cost()), 2), round(roi, 1)"),
    ("爆仓封底不再作用于净值（亏完本金还倒扣手续费）",
     "src/radar_tracker.py",
     "    net = round(max(-POS_USDT, gross - cost), 2)",
     "    net = round(gross - cost, 2)"),
    ("事件里把毛值换成净值（历史告警与新增告警变成两个口径）",
     "src/radar_tracker.py",
     "                    pnl_usdt=pnl, roi_pct=roi,\n                    pnl_net_usdt=net, roi_net_pct=roi_net, cost_usdt=cost)",
     "                    pnl_usdt=net, roi_pct=roi_net,\n                    pnl_net_usdt=net, roi_net_pct=roi_net, cost_usdt=cost)"),
    ("前端拿净值替换毛值（Toasts 只显示 net）",
     "frontend/src/components/Toasts.tsx",
     "const pnlTxt = e.pnl_usdt != null ? ` · ${pnlU(e.pnl_usdt)}${netSuffix}` : \"\";",
     "const pnlTxt = e.pnl_usdt != null ? ` · ${pnlU(e.pnl_net_usdt ?? e.pnl_usdt)}` : \"\";"),
    ("i18n 只补中文（英文界面会显示裸 key）",
     "frontend/src/i18n/locales.ts",
     '  "markets.tsStore": "TS store @{rows} rows · @{syms} syms",\n',
     ""),
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
    r = subprocess.run([PY, os.path.join(MUT, "tests", "test_v170_ts_replay.py")],
                       capture_output=True, text=True, cwd=MUT, timeout=300)
    out = (r.stdout or "") + (r.stderr or "")
    fails = [ln.strip()[2:] for ln in out.splitlines() if ln.strip().startswith("- ")]
    return r.returncode, out, fails


def main():
    prepare()
    rc, out, _ = run()
    baseline_ok = rc == 0 and "ALL PASS" in out
    print(f"基线（未变异）: rc={rc} ALL PASS={'ALL PASS' in out}")
    if not baseline_ok:
        print("⚠️ 基线就不通过，变异验证无意义")
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
        caught = rc != 0 or "ALL PASS" not in out
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

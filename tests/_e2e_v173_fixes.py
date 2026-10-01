# -*- coding: utf-8 -*-
"""v1.7.3 端到端验证：妖币雷达四修的真实链路（**隔离工作区，绝不触碰真实数据**）。

为什么必须做这一步：本轮四个修复里有三个是「接线类」缺陷 —— 代码都写对了，但
**没人调用 / 没接进链路**（monster 不在记账白名单、后端新参数没被路由透传、
LLM 分流时根本不查雷达名单）。这类缺陷**离线单元测试全绿也照样存在**，
只有真的跑一遍链路才暴露。

全部在隔离工作区里做（`BAZZ_WORKSPACE` 指向临时目录），不写用户真实库。

验证三条真实链路（不是护栏式源码扫描）：
  ① 历史战绩可展开 —— `tracks_view(history_limit=N)` 真实下发条数随 N 变，
     且 `history_total` 恒为全量、超上限被夹到 `HISTORY_FETCH`(200)
  ② monster 发帖 → 落台账 + 建模拟挂单（此前只认 rich，妖币发了帖不留纸单）——
     真实调 `square_store.record_from_run("square-monster-post")`，
     走完「落台账 → 建单 → 幂等（同 run_dir 不重复）→ 发布失败不建单」四步
  ③ `radar_lookup` 分流 —— 在册走 monster、不在册走 rich、小写/base 名也要命中
     （「只给了 base」被读成「不在名单」这个假阴性就是本脚本抓出来的）

运行：.venv/Scripts/python.exe tests/_e2e_v173_fixes.py
"""
import json
import os
import sys
import tempfile
import time

WS = tempfile.mkdtemp(prefix="bazz_v173_")
os.environ["BAZZ_WORKSPACE"] = WS
ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

import workspace  # noqa: E402
import state      # noqa: E402
import scanner    # noqa: E402

print("隔离工作区:", workspace.WORKSPACE)
FAILS = []


def ck(name, cond, extra=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  ({extra})" if (extra and not cond) else ""))
    if not cond:
        FAILS.append(name)


# ---------------- ① 历史战绩可展开 ----------------
for i in range(12):
    tid = state.radar_track_add(symbol=f"C{i}USDT", stage="IGNITION", found_price=1.0 + i, found_score=60)
    state.radar_track_close(tid, "moon", 1.5 + i)

import radar_tracker  # noqa: E402

tv8 = radar_tracker.tracks_view(history_limit=8)
tv_def = radar_tracker.tracks_view()
tv200 = radar_tracker.tracks_view(history_limit=200)
ck("① history_limit=8 只回 8 条", len(tv8["history"]) == 8, str(len(tv8["history"])))
ck("① 默认(0) 用 HISTORY_SHOWN 下发", len(tv_def["history"]) == 12, str(len(tv_def["history"])))
ck("① history_limit=200 回全量 12", len(tv200["history"]) == 12, str(len(tv200["history"])))
ck("① history_total 恒为全量", tv8["history_total"] == 12 == tv200["history_total"])
ck("① history_shown 跟随本次 limit", tv8["history_shown"] == 8 and tv200["history_shown"] == 12)
ck("① 暴露 history_fetch_max=200 供前端", tv8.get("history_fetch_max") == 200, str(tv8.get("history_fetch_max")))
ck("① 超上限被夹到 200", len(radar_tracker.tracks_view(history_limit=9999)["history"]) == 12)

# ---------------- ② monster 发帖 → 台账 + 建单 ----------------
scanner.klines_ohlcv = lambda *a, **k: {}   # 离线：不取现价，走 plan.price
import square_store  # noqa: E402

run_dir = os.path.join(WS, "monster_out", "TESTXUSDT_x")
os.makedirs(run_dir, exist_ok=True)
json.dump(
    {"symbol": "TESTXUSDT", "market": "futures", "ts": int(time.time()),
     "engine": "monster", "title": "TESTX 剧本",
     "plan": {"direction": "long", "entry": 1.0, "zone_lo": None, "zone_hi": None,
              "stop": 0.9, "tp": 1.25, "price": 1.0, "engine": "monster", "stop_pct": 10.0}},
    open(os.path.join(run_dir, "meta.json"), "w", encoding="utf-8"), ensure_ascii=False)

out = f"已合成 → {run_dir}\n已发布\nID: 999888777\nLink: https://www.binance.com/square/post/999888777\n"
rec = square_store.record_from_run("square-monster-post", "", out, 0, via="agent")
ck("② monster 发帖落台账(posted)", rec is not None and rec.get("status") == "posted", str(rec and rec.get("status")))
ck("② 台账可查", len(square_store.list_posts()) == 1)

pl = state.paper_list()
ck("② monster 发帖建了模拟挂单", len(pl) == 1, f"paper={len(pl)}")
if pl:
    o = pl[0]
    ck("② 纸单 symbol/entry/stop 正确",
       o["symbol"] == "TESTXUSDT" and abs(o["entry"] - 1.0) < 1e-9 and abs(o["stop"] - 0.9) < 1e-9, str(o))
    ck("② 纸单带 run_dir（幂等键）", o.get("run_dir") == run_dir, str(o.get("run_dir")))
    ck("② 纸单关联帖子 ID", str(o.get("post_id")) == "999888777", str(o.get("post_id")))

square_store.record_from_run("square-monster-post", "", out, 0, via="agent")
ck("② 重复调用不重复建单（幂等）", len(state.paper_list()) == 1, f"paper={len(state.paper_list())}")

before = len(state.paper_list())
square_store.record_from_run("square-monster-post", "", f"已合成 → {run_dir}\nFailed: 发布失败\n", 1, via="agent")
ck("② 发布失败不建单", len(state.paper_list()) == before)

# ---------------- ③ radar_lookup 分流 ----------------
import square_monster  # noqa: E402

scanner.get_radar_v2 = lambda **k: {
    "coins": [{"symbol": "AAAUSDT", "stage": "IGNITION", "stage_label": "点火前",
               "score": 77, "price": 2.0, "change24_pct": 3.0}],
    "takeoff": [], "ignition": [], "scanned": 1, "candidates": 1, "triggered": 1,
    "confirmed": 1, "env": "t", "updated_at": 0, "stage_counts": {},
}
r1 = square_monster.radar_lookup("AAAUSDT")
r2 = square_monster.radar_lookup("BBBUSDT")
r3 = square_monster.radar_lookup("aaa")     # 小写也应命中
ck("③ 在册币 → in_radar + monster", r1.get("in_radar") is True and r1.get("suggest") == "square-monster-post", str(r1))
ck("③ 在册币带位阶/分数", r1.get("stage_label") == "点火前" and r1.get("score") == 77, str(r1))
ck("③ 不在册币 → rich", r2.get("in_radar") is False and r2.get("suggest") == "square-rich-post", str(r2))
ck("③ 小写符号命中", r3.get("in_radar") is True, str(r3))

# ---------------- 结果 ----------------
print("\n" + "=" * 52)
if FAILS:
    print(f"失败 {len(FAILS)} 项：")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("全部通过 ✅")

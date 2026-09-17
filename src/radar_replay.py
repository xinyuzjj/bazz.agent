"""妖币雷达回放框架（v1.6.6 · 档二 C5）

## 为什么需要它

报告 §15 C5 的原话：**「没有回放框架，回放靠作者手工做，且全部是样本内。」**
此前所有关键决策都来自作者手工回放 —— 那些数字写在代码注释里
（`scanner.py:1017` 的「20 笔 → 存活 9 笔」、`scanner.py:1901` 的「7 笔 +213.5U / 胜率 42.9%」、
`scanner.py:1555` 的「23 笔已关单回放」），却**不可复现、不可回归**：
换个口径重算就可能得出另一个答案。而且它们**全部是样本内的** ——
在同一批 20 笔上调阈值，等于在这 20 笔上拟合噪声。

本模块把回放做成**可复现的代码**，并强制两件事：

1. **任意阈值组合可重算**：`RULES` 里每一项都是一个可开关的判据，输入一行 `radar_tracks`
   （含 C4 补齐的 14 列登记快照），输出该组合下的存活数 / 胜率 / PnL。
2. **必须样本外**：`replay()` 默认按 `found_at` 排序后**前 70% 调参 / 后 30% 验证**，
   两组指标分开给。只报样本内数字 = 自欺。

## 两条不可妥协的口径

* **「未测到」≠「测得为 0」**：快照缺值一律 `None`，判据对 `None` **fail-open**（不误杀），
  但会在 `unknown` 里逐轴计数 —— 让「有多少样本其实是没数据才通过的」可见。
  想反过来（宁可错杀）就用 `require_snap=True` 把无快照的老样本整条剔除。
* **毛 PnL 与含成本 PnL 分开报**：成本模型在 `radar_tracker.sim_cost` / `sim_pnl_net`
  （同一份实现，**不在这里复制常量** —— 两份常量必然漂移）。

## 用法

```bash
# 全量口径
.venv/Scripts/python.exe src/radar_replay.py

# 指定规则
.venv/Scripts/python.exe src/radar_replay.py --rule max_chg24=3 --rule max_oi24=5

# 单轴扫描（前 70% 调参 / 后 30% 验证，直接看哪一档在样本外也站得住）
.venv/Scripts/python.exe src/radar_replay.py --sweep max_chg24=1,3,5,10
```
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import state          # noqa: E402
import radar_tracker  # noqa: E402

# 样本外切分比例：前 70% 用于「调参」，后 30% 用于「验证」。
# 不做切分的话，任何「调完更好」都是必然结果 —— 因为调参用的就是这批数据本身。
DEFAULT_SPLIT = 0.7
MIN_SAMPLE_WARN = 20     # 样本少于它时给显式告警（与 state.RADAR_SNAP_MIN_N 同量级）

# 规则表：(键名, 人话说明, 比较方向, 取哪个字段)
#   op="max" → 字段值必须 **≤** 阈值（用于「挡住过大/过高的」）
#   op="min" → 字段值必须 **≥** 阈值
# 字段一律取自 `sample_of()` 的拍平结果，名字与 C4 的快照列一致（去 snap_ 前缀）。
RULES: tuple = (
    ("max_chg24",    "登记时 24h 涨幅 ≤ 阈值（挡住「已经涨了」的）", "max", "chg24"),
    ("min_chg24",    "登记时 24h 涨幅 ≥ 阈值（挡住「还在跌」的）",   "min", "chg24"),
    ("max_oi24",     "登记时 OI 24h 变化 ≤ 阈值（挡住持仓已堆积）", "max", "oi24"),
    ("min_oi24",     "登记时 OI 24h 变化 ≥ 阈值",                   "min", "oi24"),
    ("max_amp24",    "登记时 24h 振幅 ≤ 阈值",                      "max", "amp24"),
    ("min_amp24",    "登记时 24h 振幅 ≥ 阈值",                      "min", "amp24"),
    ("max_rvol15",   "登记时 RVOL15 ≤ 阈值",                        "max", "rvol15"),
    ("min_rvol15",   "登记时 RVOL15 ≥ 阈值",                        "min", "rvol15"),
    ("max_funding",  "登记时资金费率 ≤ 阈值",                       "max", "funding"),
    ("min_funding",  "登记时资金费率 ≥ 阈值",                       "min", "funding"),
    ("max_taker",    "登记时 taker 买比 ≤ 阈值",                    "max", "taker"),
    ("min_taker",    "登记时 taker 买比 ≥ 阈值",                    "min", "taker"),
    ("min_top",      "登记时大户持仓比 ≥ 阈值",                     "min", "top"),
    ("max_top48",    "登记时大户持仓比 48h 变化 ≤ 阈值",            "max", "top48"),
    ("max_liq5m",    "登记时 5m 强平额 ≤ 阈值",                     "max", "liq5m"),
    ("min_liq5m",    "登记时 5m 强平额 ≥ 阈值",                     "min", "liq5m"),
    ("max_basis_bps", "登记时基差 ≤ 阈值（基点）",                  "max", "basis"),
    ("min_basis_bps", "登记时基差 ≥ 阈值（基点）",                  "min", "basis"),
    ("max_spread_bps", "登记时价差 ≤ 阈值（基点）",                 "max", "spread"),
    ("min_score",    "登记分数 ≥ 阈值",                             "min", "score"),
    ("max_score",    "登记分数 ≤ 阈值",                             "max", "score"),
)
_RULE_KEYS = {k for k, _, _, _ in RULES}
# 非数值型开关（单独处理，不进 RULES 的数值比较）
BOOL_KEYS = ("require_snap", "exclude_dormant")
LIST_KEYS = ("stages", "exclude_stages")


def sample_of(row: dict) -> dict:
    """把一行 `radar_tracks` 拍平成回放样本（**纯函数**，不碰库）。

    快照优先取 `row["snap"]`（C4 的 14 列），缺失回退平铺的 `snap_*` 键 ——
    两条路径读的是同一批数据，但老消费方按平铺键取值，不能只留一条。
    `found_at` 保留原始秒级时间戳，用于**样本外切分**。
    """
    snap = dict(row.get("snap") or {})
    for k, v in row.items():
        if k.startswith("snap_") and k[len("snap_"):] not in snap:
            snap[k[len("snap_"):]] = v

    def _f(key):
        v = snap.get(key)
        if v is None or isinstance(v, bool):
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    return {
        "id": row.get("id"), "symbol": row.get("symbol"),
        "stage": row.get("stage") or "", "direction": (row.get("direction") or "LONG"),
        "status": row.get("status") or "", "outcome": row.get("outcome") or "",
        "score": (float(row["found_score"]) if row.get("found_score") is not None else None),
        "found_price": _f_of(row.get("found_price")),
        "outcome_price": _f_of(row.get("outcome_price")),
        "found_at": _f_of(row.get("found_at")) or 0.0,
        "closed_at": _f_of(row.get("closed_at")) or 0.0,
        "has_snap": bool(snap),
        "chg24": _f("chg24"), "oi24": _f("oi24"), "amp24": _f("amp24"),
        "funding": _f("funding"), "rvol15": _f("rvol15"), "taker": _f("taker"),
        "oi15": _f("oi15"), "top": _f("top"), "top48": _f("top48"),
        "glob": _f("glob"), "liq5m": _f("liq5m"),
        "basis": _f("basis"), "spread": _f("spread"),
        "liq_side": snap.get("liqside") or "",
    }


def _f_of(v):
    """宽松转 float；非数值 / 空 → None（未测到）。"""
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def load_samples(direction: str = "LONG", status: str = "closed") -> list:
    """从库里取样本（默认：**已关单 + LONG**，与 `radar_tracks_stats` 同口径）。

    `direction="ALL"` 取全量（含历史遗留空单），用于复核「砍掉做空」这个决策本身。
    """
    rows = state.radar_tracks_list(status, limit=0)
    out = []
    for r in rows:
        s = sample_of(r)
        if direction and direction.upper() != "ALL" and s["direction"].upper() != direction.upper():
            continue
        out.append(s)
    out.sort(key=lambda x: x["found_at"] or 0.0)
    return out


def apply_rules(samples: list, rules: dict = None) -> dict:
    """按规则过滤，返回 `{kept, dropped, unknown, bad_rules}`。

    **未测到的轴 fail-open**：`None` 不参与该轴比较（不因缺数据被误杀），
    但会记进 `unknown[axis]` —— 这是「有多少样本是没数据才通过的」的观测点。
    """
    rules = dict(rules or {})
    bad = [k for k in rules if k not in _RULE_KEYS and k not in BOOL_KEYS and k not in LIST_KEYS]
    kept, dropped = [], []
    unknown: dict = {}
    want_snap = bool(rules.get("require_snap"))
    stages = rules.get("stages")
    ex_stages = rules.get("exclude_stages") or []
    ex_dormant = bool(rules.get("exclude_dormant"))
    for s in samples:
        if want_snap and not s["has_snap"]:
            dropped.append(s)
            continue
        if stages and s["stage"] not in stages:
            dropped.append(s)
            continue
        if ex_dormant and s["stage"] == "DORMANT":
            dropped.append(s)
            continue
        if s["stage"] in ex_stages:
            dropped.append(s)
            continue
        ok = True
        for key, _desc, op, field in RULES:
            if key not in rules:
                continue
            thr = _f_of(rules.get(key))
            if thr is None:
                continue
            v = s.get(field)
            if v is None:
                unknown[field] = unknown.get(field, 0) + 1   # 未测到 → fail-open，但要可见
                continue
            if (op == "max" and v > thr) or (op == "min" and v < thr):
                ok = False
                break
        (kept if ok else dropped).append(s)
    return {"kept": kept, "dropped": dropped, "unknown": unknown, "bad_rules": bad}


def _pnl_of(s: dict, costs: bool) -> tuple:
    """单笔 PnL：返回 `(pnl, cost, known)`。`known=False` 表示结局价缺失 → **不参与汇总**。

    ⚠️ 结局价缺失（0 / None）与「结局价恰好是 0」必须分开：前者是老样本没写 `outcome_price`，
    后者不可能（价格不可能为 0）。这里按「非正数即未知」处理，并把笔数单独报出来，
    绝不把它当成 0 盈亏混进胜率与 PnL。
    """
    found, px = s.get("found_price"), s.get("outcome_price")
    if not found or found <= 0 or not px or px <= 0:
        return 0.0, 0.0, False
    direction = s.get("direction") or "LONG"
    gross, _ = radar_tracker._sim_pnl(direction, found, px)
    if not costs:
        return gross, 0.0, True
    net, _roi, cost = radar_tracker.sim_pnl_net(
        direction, found, px,
        radar_tracker._hold_hours({"found_at": s.get("found_at")},
                                  s.get("closed_at") or s.get("found_at") or 0.0),
        0.0)   # 资金费率按 0 计：快照里只有「登记那一刻」的费率，拿它外推持有期是臆测
    return net, cost, True


def summarize(samples: list, costs: bool = True) -> dict:
    """一组样本的汇总指标（胜率 / 存活数 / PnL）。

    胜率分母只用**已关单**样本：`pending` 既不算赢也不算输，混进来会把胜率稀释成噪声
    （与 `radar_tracks_stats` 同一口径）。
    """
    moon = sum(1 for s in samples if s["outcome"] == "moon")
    dump = sum(1 for s in samples if s["outcome"] == "dump")
    exp = sum(1 for s in samples if s["outcome"] == "expired")
    closed = moon + dump + exp
    pnl = cost_sum = 0.0
    unknown_px = 0
    for s in samples:
        p, c, known = _pnl_of(s, costs)
        if not known:
            unknown_px += 1
            continue
        pnl += p
        cost_sum += c
    return {
        "n": len(samples), "closed": closed, "pending": len(samples) - closed,
        "moon": moon, "dump": dump, "expired": exp,
        "win_rate": round(moon / closed * 100, 1) if closed else 0.0,
        "pnl_usdt": round(pnl, 2), "cost_usdt": round(cost_sum, 2),
        "pnl_per_trade": round(pnl / closed, 2) if closed else 0.0,
        "n_pnl_unknown": unknown_px,
        "costs_applied": bool(costs),
    }


def replay(rules: dict = None, direction: str = "LONG", split: float = DEFAULT_SPLIT,
           costs: bool = True, status: str = "closed") -> dict:
    """按阈值组合重算，并给出**样本内 / 样本外**两组指标。

    切分方式：按 `found_at` 升序，前 `split` 为样本内（调参用），其余为样本外（验证用）。
    这是 §17 第 9 项明确要求的「必须带样本外切分」——
    只报样本内数字的话，「调完更好」是必然结果，不构成证据。
    """
    samples = load_samples(direction, status)
    applied = apply_rules(samples, rules)
    kept = applied["kept"]
    cut = int(len(kept) * float(split or DEFAULT_SPLIT))
    inside, outside = kept[:cut], kept[cut:]
    warn = []
    if len(kept) < MIN_SAMPLE_WARN:
        warn.append(f"存活样本仅 {len(kept)} 笔（< {MIN_SAMPLE_WARN}）—— "
                    f"任何「更优」都可能是这几十笔的噪声，别据此改规则。")
    if applied["bad_rules"]:
        warn.append(f"未知规则键被忽略：{applied['bad_rules']}（可用键见 RULES）")
    if not outside:
        warn.append("样本外区间为空（切分后无剩余样本）—— 无法验证泛化性。")
    if applied["unknown"]:
        warn.append("有样本在某轴上「未测到」而 fail-open 通过：" +
                    "、".join(f"{k}×{v}" for k, v in sorted(applied["unknown"].items())) +
                    "（要整条剔除请用 require_snap=True）")
    return {
        "ok": True, "direction": direction, "status": status,
        "rules": dict(rules or {}), "split": float(split or DEFAULT_SPLIT),
        "n_all": len(samples), "n_kept": len(kept), "n_dropped": len(applied["dropped"]),
        "in_sample": summarize(inside, costs),
        "out_sample": summarize(outside, costs),
        "full": summarize(kept, costs),
        "unknown": applied["unknown"], "bad_rules": applied["bad_rules"],
        "warnings": warn,
        "note": "本函数**只算不改**：不动任何规则、不写库、不发请求。改闸门是产品决策。",
    }


def sweep(key: str, values: list, direction: str = "LONG", split: float = DEFAULT_SPLIT,
          costs: bool = True) -> dict:
    """单轴扫描：对 `key` 依次取 `values`，逐个跑 `replay()` 并**按样本外 PnL 排序**。

    为什么按样本外排序而不是样本内：样本内总是「越贴越好」，排序没有信息量。
    真正该看的信号是「**样本内变好、样本外也变好**」—— 只有这种档位才值得考虑。
    """
    rows = []
    for v in values:
        r = replay({key: v}, direction=direction, split=split, costs=costs)
        rows.append({
            "value": v,
            "n_kept": r["n_kept"],
            "in_n": r["in_sample"]["n"], "in_win": r["in_sample"]["win_rate"],
            "in_pnl": r["in_sample"]["pnl_usdt"],
            "out_n": r["out_sample"]["n"], "out_win": r["out_sample"]["win_rate"],
            "out_pnl": r["out_sample"]["pnl_usdt"],
        })
    return {
        "axis": key, "direction": direction, "split": float(split or DEFAULT_SPLIT),
        "rows": rows,
        "sorted_by": "out_pnl",
        "ranked": sorted(rows, key=lambda x: -(x["out_pnl"] or 0.0)),
        "note": ("看的是「样本内与样本外**同时**变好」的档位；只有样本内变好、"
                 "样本外变差，说明该阈值只是拟合了这批样本。"),
    }


def _parse_rule(s: str) -> tuple:
    """`key=value` → (key, value)。布尔键支持 `=1/true/yes`。"""
    if "=" not in s:
        raise argparse.ArgumentTypeError(f"规则要写成 key=value，收到：{s}")
    k, v = s.split("=", 1)
    k, v = k.strip(), v.strip()
    if k in BOOL_KEYS:
        return k, v.lower() in ("1", "true", "yes", "y", "on")
    if k in LIST_KEYS:
        return k, [x.strip() for x in v.split(",") if x.strip()]
    try:
        return k, float(v)
    except ValueError:
        return k, v


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="妖币雷达回放（只算不改）")
    ap.add_argument("--rule", action="append", default=[], help="key=value，可重复")
    ap.add_argument("--sweep", default="", help="单轴扫描，形如 max_chg24=1,3,5,10")
    ap.add_argument("--direction", default="LONG")
    ap.add_argument("--split", type=float, default=DEFAULT_SPLIT)
    ap.add_argument("--gross", action="store_true", help="只算毛 PnL（不含成本）")
    a = ap.parse_args(argv)
    costs = not a.gross

    if a.sweep:
        if "=" not in a.sweep:
            print("--sweep 要写成 key=v1,v2,v3")
            return 2
        k, vs = a.sweep.split("=", 1)
        try:
            values = [float(x) for x in vs.split(",") if x.strip()]
        except ValueError:
            print("--sweep 的值必须是数字")
            return 2
        print(json.dumps(sweep(k.strip(), values, a.direction, a.split, costs),
                         ensure_ascii=False, indent=2))
        return 0

    rules = {}
    for item in a.rule:
        k, v = _parse_rule(item)
        rules[k] = v
    out = replay(rules, a.direction, a.split, costs)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

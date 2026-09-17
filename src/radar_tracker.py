"""妖币追踪（v1.5.2）：启动前发现 → 后续暴涨/暴跌结局验证。

- 登记源：scanner.get_radar_v2 扫描完成后调用 record_from_radar(payload)，
  只登记 ignition 组（吸筹/点火/做空埋伏 = 启动前窗口）的币，同币在跟踪中自动幂等。
  **v1.6.2 起**：① 「已拉升」（EXTENDED，当日涨幅越过 12% 追高线）不再进 ignition 组，
  不再被登记；② 做空埋伏须已破位才成立；③ 同币关单后 24h 内拒绝重登
  （`state.RADAR_REENTRY_COOLDOWN`，旧实现关单即可立刻重登，实测 MTLUSDT 被登记 4 次全 dump）。
- 跟踪：daemon 线程 60s 轮询 pending 记录；价格三级兜底（v1.6.4）：
  `market_ws.price("spot")` → **`market_ws.price("futures")`** → 现货∪合约快照
  （`scanner.get_snapshot()` ∪ `scanner.futures_snapshot()`，现货优先），不新增外网请求路径。
  合约那两级是扩池后的必需品：纯合约币（无现货市场）只查现货会永远取不到价。
- 结局（v1.5.8，按 10x 合约杠杆口径，仓位模拟 100U×10x）：逆向 ≥10%（近强平线）→ dump（失败）；
  顺向 ≥25% 达标不直接关单——判反转因子（费率极值回落/OI 脉冲/爆仓潮）：现反转 → 落袋 moon，
  否则转持有模式移动止盈（自持有期极值回撤/反弹 ≥12% → moon）；7 天未触发 → expired。
  关单即终态，market_ws.publish_event("alert", kind="radar_outcome", ...) 推前端 toast + 系统通知。
"""
import json
import math
import os
import threading
import time

import state
import market_ws
import scanner
import workspace

GAIN_HIT = 25.0    # 顺向最大波动 % → 达标（不再直接关单：判定反转则落袋，否则持有移动止盈）
FAIL_HIT = 10.0    # 逆向最大波动 % → dump（失败）：按 10x 合约杠杆计，逆向 ~10% 即近强平线
TRAIL_PCT = 12.0   # 持有期移动止盈：自持有期极值回撤/反弹 ≥12% → moon 落袋
POS_USDT = 100.0   # 仓位模拟：每只妖币默认 100U 本金
LEVERAGE = 10      # 仓位模拟：10x 合约
TTL_SEC = 7 * 86400  # 跟踪期限：超时未触发 → expired（未兑现）
POLL_SEC = 60.0

# v1.6.6（问题清单 #12）：历史展示条数 vs 统计口径。顶部战绩计数基于**全量**已关单，
# 下方历史表只列 HISTORY_SHOWN 条 —— 两个数必须都能拿到，前端才能注明
# 「显示最近 50 条 / 统计基于全部 N 条」，否则样本超 50 笔后数字与可见行数对不上。
HISTORY_SHOWN = 50      # 前端历史表展示条数
HISTORY_FETCH = 200     # 从库里取来排序的上限（展示路径专用，不影响状态机）

# ---- v1.6.5（OPT-06）持仓质量：疑似假启动 ----
# 证据（安装版 20 笔 LONG 已关单；口径必须说清，否则数字会被读成另一个意思）：
#   · 17 笔 dump 里 **15 笔的 max_gain ≤ 6.7%**（只有 ETHFI 15.1 / RAY 13.4 例外）——
#     dump 单几乎从来没给过超过 7% 的浮盈；
#   · 3 笔 moon **全部在 24h 内就摸到 ≥25%**（关单耗时 23.8h / 25.1h / 31.9h），
#     而 dump 的关单耗时中位数是 46.5h。
# 但这个标志**只在「24h 时还没关单」的行上才有意义**：17 笔 dump 里有 7 笔在 24h 前
# 就已经逆向破 10% 关单了，它们根本走不到被标记那一步。所以真实口径是：
#   「活过 24h 的 10 笔 dump 里，**7 笔**在 24h 时 max_gain < 5%；3 笔 moon **0 笔**被标」。
# 提示的正确用法是「这只跟了很久还不动的，别指望它」，而不是「命中率 XX% 所以要砍掉它」。
# ⚠️ 只提示，**绝不做自动离场**：VTHO 就是反例 —— 早期回撤 7.9% 之后冲到 +31.3%。
FAKE_START_HOURS = 24.0   # 观察窗：满 24h 还没浮盈才谈「疑似」
FAKE_START_GAIN = 5.0     # 浮盈线（用历史极值 max_gain，不用当前价）

_started = False


def _pick(row: dict, factors: dict, key: str):
    """取登记快照值（v1.6.4）：**顶层存在就用顶层**，不存在才回退 factors。

    必须是「键是否存在」而不是「值是否为 None」—— 顶层 `oi_chg24` 在确认因子缺失时
    显式写 None（未测到），若按值回退会拿到 `factors.oi_chg24` 的 0.0，
    把「没测到」伪装成「持仓没动」，恰好污染 OPT-03 的判据口径。
    """
    if key in row:
        return row[key]
    return factors.get(key)


def record_from_radar(payload: dict) -> None:
    """雷达扫描完成后登记启动前（吸筹/点火）发现。任何异常静默，不拖垮雷达同步响应。
    方向取雷达行的 side：WATCH_SHORT → SHORT（做空观察），其余（LONG/WATCH）→ LONG。

    v1.6.4：同时落库**登记时刻数值快照**（chg24/oi24/amp24/funding/rvol15）。
    此前只有 reasons_json 文本，回放靠解析文本反推，口径会漂；落快照后新样本可直接
    按数值复算，规则改动能做真正的 A/B 回放。

    v1.6.6（问题清单 C4）：快照补到 **14 项**。v1.6.4 只落了触发层/日线层的 5 个数，
    而**点火判据与加分段真正依赖的是确认层** —— `_stage_of_raw` 的点火条件读
    `taker_ratio`，`_radar_score` 的加分段读 `oi_pulse15` / `top_ratio` / `liq_5m`。
    缺了它们，下一轮改点火判据时仍然只能解析文本（即 v1.6.4 想解决的问题只做了一半）。
    新增 9 项：taker / oi15 / top / top48 / glob / liq5m / liqside / basis / spread。
    """
    try:
        rows = (payload or {}).get("ignition") or []
        for r in rows:
            sym = str(r.get("symbol") or "").upper()
            px = float(r.get("price") or 0)
            if not sym or px <= 0:
                continue
            direction = "SHORT" if str(r.get("side") or "").upper() == "WATCH_SHORT" else "LONG"
            f = r.get("factors") or {}
            snap = {
                # —— v1.6.4：触发层 / 日线层 ——
                "chg24": _pick(r, f, "change24_pct"),
                "oi24": _pick(r, f, "oi_chg24"),
                "amp24": _pick(r, f, "amp24"),
                "funding": _pick(r, f, "funding"),
                "rvol15": _pick(r, f, "rvol15"),
                # —— v1.6.6（C4）：确认层（点火判据 / 加分段真正读的）+ 档一新信号 ——
                "taker": f.get("taker_ratio"),
                "oi15": f.get("oi_pulse15"),
                "top": f.get("top_ratio"),
                "top48": f.get("top_mean48"),
                "glob": f.get("global_ratio"),
                "liq5m": f.get("liq_5m"),
                "liqside": f.get("liq_side"),
                "basis": f.get("basis_bps"),
                "spread": f.get("spread_bps"),
            }
            state.radar_track_add(sym, str(r.get("stage") or "IGNITION"), px,
                                  int(r.get("score") or 0), r.get("reasons") or [],
                                  direction=direction, snap=snap)
    except Exception:
        pass


def _current_price(sym: str, snap: dict):
    """取价三级兜底（v1.6.4）：现货 WS → **合约 WS** → 合并快照。

    扩池到合约全市场后，池里含「只上合约、不上现货」的币（RAVE / LAB 这类纯合约妖币），
    只查现货会永远取到 None → `_tick` 直接 continue → 该单永久卡 pending，
    7 天后被 expired 静默吞掉，战绩里完全看不见。`market_ws.price("futures", …)` 本就存在。
    """
    px = market_ws.price("spot", sym)
    if px is None:
        px = market_ws.price("futures", sym)
    if px is None:
        px = snap.get(sym)
    return float(px) if px else None


def _sim_pnl(direction: str, found: float, px: float) -> tuple:
    """仓位模拟（100U 本金 × 10x 合约，爆仓封底 -100U）。返回 (pnl_usdt, roi_pct)。

    ⚠️ 问题清单 #11：`direction == "SHORT"` 分支自 v1.6.3 起**对新登记不可达** ——
    `record_from_radar` 只遍历 payload 的 `ignition` 组，而该组仅含 ACCUMULATION /
    IGNITION（side 恒为 LONG）；SHORT_AMBUSH 已移入 takeoff（仅展示）。
    保留此分支只为结算**历史遗留的 pending 空单**，勿据此推断「系统支持做空」。
    """
    if not found or not px:
        return 0.0, 0.0
    chg = (float(px) / float(found) - 1.0) * 100.0
    roi = (-chg if direction == "SHORT" else chg) * LEVERAGE
    return round(max(-POS_USDT, POS_USDT * roi / 100.0), 2), round(roi, 1)


# ---------------- 成本模型（v1.6.6 · 档二 #10） ----------------
# 报告 §11 B3 的判断：`_sim_pnl` 是「纯价格差 × 10 倍杠杆」—— **无滑点、无手续费、
# 无资金费率**，于是所有 PnL 结论都**系统性偏乐观**。这里补上，且**零新增数据源**。
#
# 口径必须写清楚，否则数字会被读成另一个意思：
#   · **taker 手续费** 0.05% 单边（币安 USDT 永续吃单口径）。妖币盘口薄，不能按 maker 算。
#   · **滑点** 单边 0.15%（保守常数）。**这不是实测值** —— 真实滑点取决于订单簿深度，
#     而档三实测已确认**合约 ticker 不返回盘口**，所以先给保守常数并把口径标明；
#     等 C6 时序库攒够「价差 + 成交额」样本再回来标定。
#   · **资金费率** 按 持有小时 / **该币实际结算周期** 累计，费率取调用方给的最新已知费率。
#     v1.7.1 修正：周期此前硬编码 8h，但实测 `/fapi/v1/fundingInfo`（782 条）里
#     **4h 周期才是多数**（4h=467 / 8h=312 / 1h=3），成交额前 110 有 40 个是 4h ——
#     对这些币的资金费率成本**低估 2 倍**（1h 周期的低估 8 倍）。现在周期由
#     `interval_h` 逐币传入（来源见 `scanner.get_funding_intervals`）。
#
# ⚠️ **`_sim_pnl` 保持原样返回毛值**（向后兼容：历史复盘文本、既有消费方一律不动），
# 成本**单列**在 `pnl_cost` 里一起下发 —— 绝不用净值**悄悄替换**毛值，
# 否则历史样本与新增样本会变成两个口径，而没有任何地方会说出来。
FEE_TAKER = 0.0005      # 单边手续费率（吃单）
SLIP_PCT = 0.15         # 单边滑点 %（保守常数，非实测）
# ⚠️ v1.7.1 起 `ts_series.fut_spread_bps` 已在攒**合约盘口价差**实测样本（实测中位 3.97bps /
# p90 9.81bps，均小于这里的 15bps/边）。但**不要**因此把这里改成「用盘口价差」：
# 盘口买一卖一是**下限**，而成本模型估的是**市价单实际成交价**——薄盘里吃穿几档的冲击
# 远大于顶档价差。样本的用途是「验证 15bps 够不够保守」，不是「替换成 4bps」。
# 真要替换，得先有「下单规模 → 实际成交均价」的样本，那需要成交回报，不是盘口。
SLIP_SRC = "constant"   # 标定状态：constant（常数）/ measured（已有成交样本标定）—— 目前恒为常数
FUNDING_CYCLE_H = 8.0   # 资金费率结算周期**默认值**（小时）；实测值优先，见 sim_cost 的 interval_h


def sim_cost(hours: float = 0.0, funding: float = 0.0, interval_h: float = FUNDING_CYCLE_H) -> float:
    """一次完整往返的**成本**（USDT，正数），名义额按 `POS_USDT × LEVERAGE` = 1000U 计。

    `hours` = 持有小时数（决定资金费率结算次数）；`funding` = 最近一次已知费率。
    `interval_h` = 该币**实际结算周期**（小时），实测值优先；`None`/非正数 → 回退默认 8h。
    费率正 = 多头付钱（本系统只做多，故取正号；做空口径对称）。

    v1.7.1：`interval_h` 是**新增的第三个参数**，默认值保持 8.0 所以旧调用行为不变。
    周期越小 → 结算次数越多 → 资金费率成本越高：4h 周期的成本是 8h 的 **2 倍**。
    """
    notional = float(POS_USDT) * float(LEVERAGE)
    fee = notional * FEE_TAKER * 2                    # 开仓 + 平仓
    slip = notional * (SLIP_PCT / 100.0) * 2          # 进 + 出
    try:
        iv = float(interval_h)
    except (TypeError, ValueError):
        iv = 0.0
    # `inf` 也要挡：`float("inf") > 0` 为真，放过去会让 cycles = hours/inf = 0，
    # 即「资金费率成本凭空消失」—— 一个不会报错、只会让数字变好看的失效方式。
    if not (iv > 0) or not math.isfinite(iv):
        iv = FUNDING_CYCLE_H
    cycles = max(0.0, float(hours or 0.0)) / iv
    fund = notional * float(funding or 0.0) * cycles
    return round(fee + slip + fund, 2)


def sim_pnl_net(direction: str, found: float, px: float,
                hours: float = 0.0, funding: float = 0.0,
                interval_h: float = FUNDING_CYCLE_H) -> tuple:
    """含成本的仓位模拟：返回 `(pnl_net, roi_net_pct, cost_usdt)`。

    爆仓封底作用在**净值**上（`max(-POS_USDT, …)`）：强平时亏损以本金为限，
    不会「亏完本金还继续收手续费」—— 这与真实机制一致，不是为了让数字好看。
    `interval_h` 见 `sim_cost`。
    """
    gross, _ = _sim_pnl(direction, found, px)
    if not found or not px:
        return 0.0, 0.0, 0.0
    cost = sim_cost(hours, funding, interval_h)
    net = round(max(-POS_USDT, gross - cost), 2)
    return net, round(net / float(POS_USDT) * 100.0, 1), cost


def _hold_hours(t: dict, now: float) -> float:
    """持有小时数（用登记时间算；缺失 → 0，即不计资金费率次数，**不猜**）。"""
    try:
        found_at = float(t.get("found_at") or 0)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, (float(now) - found_at) / 3600.0) if found_at else 0.0


def _last_funding(sym: str) -> float:
    """最近一次已知资金费率（零外呼，取雷达缓存；拿不到 → 0.0 并视作「未测到」）。"""
    try:
        v = (_last_scan_row(sym).get("factors") or {}).get("funding")
        return float(v) if v is not None else 0.0
    except Exception:
        return 0.0


def _funding_interval(sym: str) -> float:
    """该币资金费率**结算周期**（小时）。零外呼，取雷达缓存下发的实测值（v1.7.1）。

    三态映射（见 `scanner` 里 `funding_interval_h` 的注释）：
      · 具体小时数 → 直接用（4h 的币成本是 8h 的 2 倍）
      · 8.0        → 币安默认周期（接口通了但该币不在 `fundingInfo` 里）
      · **None**   → 未测到 → 回退默认 8h，**但这是假设不是实测**

    与 `_last_funding` 同样的零外呼纪律：只读最近一次扫描的缓存行，绝不在
    「追踪 tick」里发 HTTP（那会让一个纯计算路径突然依赖网络）。
    """
    try:
        v = (_last_scan_row(sym).get("factors") or {}).get("funding_interval_h")
        iv = float(v) if v is not None else 0.0
        return iv if iv > 0 else FUNDING_CYCLE_H
    except Exception:
        return FUNDING_CYCLE_H


def _reversal_now(sym: str, direction: str) -> bool:
    """达标瞬间是否出现反转因子（取最近一次雷达缓存，零外呼）：是→直接落袋 moon；否→持有。
    无因子数据（缓存缺失）默认继续持有——真妖币不会只到 25% 就结束。

    ⚠️ 问题清单 #11：`SHORT` 分支同样只对**历史遗留的 pending 空单**生效（见 `_sim_pnl`）。
    """
    f = (_last_scan_row(sym).get("factors") or {})
    if not f:
        return False
    fund, fund_pk = f.get("funding"), f.get("funding_peak")
    oi15, liq5, liqside = f.get("oi_pulse15"), f.get("liq_5m") or 0, f.get("liq_side")
    if direction == "SHORT":
        # ⚠️ #11：历史遗留空单专用分支（新登记恒为 LONG）
        return bool((fund is not None and fund <= 0)
                    or (oi15 is not None and oi15 >= 5.0)
                    or (liqside == "short" and liq5 >= 5e5))
    return bool((fund_pk and fund is not None and fund_pk >= 0.003 and fund <= 0.4 * fund_pk)
                or (oi15 is not None and oi15 <= -5.0)
                or (liqside == "long" and liq5 >= 5e5))


def _judge_outcome(ctx: dict, now: float, direction: str = "LONG") -> str:
    """ctx 来自 radar_track_progress：累计 max_gain/max_drop + 当前 gain/drop + holding/hold_ext + found_at。
    方向感知（v1.5.8，按 10x 合约杠杆口径 + 持有模式）：
      未持有：仅判 dump（逆向 ≥10% 近强平）与 expired；顺向达标（≥25%）由 _tick 决定落袋/持有。
      持有中：自持有期极值回撤（做多）/反弹（做空）≥12% → moon 移动止盈落袋；逆向 ≥10% 仍 → dump（爆仓优先）。
    7 天未触发 → expired。

    ⚠️ 问题清单 #11：`direction == "SHORT"` 分支自 v1.6.3 起对新登记不可达，
    仅结算**历史遗留的 pending 空单**（见 `_sim_pnl`）。未来若要重新开放做空，
    必须同时改 `scanner.get_radar_v2` 的 `rows_ign` 分组，否则本分支仍是死代码。
    """
    gain = ctx.get("max_gain") or 0
    drop = ctx.get("max_drop") or 0
    found = float(ctx.get("found") or 0)
    holding = bool(ctx.get("holding"))
    if direction == "SHORT":
        # ⚠️ #11：历史遗留空单专用分支（新登记恒为 LONG）
        if gain >= FAIL_HIT:
            return "dump"
        if holding:
            ext = float(ctx.get("hold_ext") or 0)
            px = found * (1.0 - (ctx.get("drop") or 0) / 100.0)
            if ext > 0 and px > 0 and (px - ext) / ext * 100.0 >= TRAIL_PCT:
                return "moon"
    else:
        if drop >= FAIL_HIT:
            return "dump"
        if holding:
            ext = float(ctx.get("hold_ext") or 0)
            px = found * (1.0 + (ctx.get("gain") or 0) / 100.0)
            if ext > 0 and px > 0 and (ext - px) / ext * 100.0 >= TRAIL_PCT:
                return "moon"
    if now - (ctx.get("found_at") or 0) > TTL_SEC:
        return "expired"
    return ""


def _fmt_price(p: float) -> str:
    p = float(p or 0)
    if p <= 0:
        return "—"
    return f"{p:.8f}".rstrip("0").rstrip(".") if p < 1 else f"{p:,.4f}"


def _last_scan_row(sym: str) -> dict:
    """取最近一次雷达扫描中该币的行（含 factors/env），零外呼；无缓存返回 {}。"""
    try:
        data = (getattr(scanner, "_radar2_cache", {}) or {}).get("data") or {}
        for r in (data.get("coins") or []):
            if r.get("symbol") == sym:
                r = dict(r)
                r["_env"] = data.get("env")
                return r
    except Exception:
        pass
    return {}


def _build_failure_review(t: dict, ctx: dict, px: float, now: float) -> str:
    """dump 关单时生成失败复盘（启发式，零外呼）：失败路径 + 关单时因子 + 大盘环境 + 教训。

    ⚠️ 问题清单 #11：`SHORT` 分支只对**历史遗留的 pending 空单**生效（见 `_sim_pnl`）。
    """
    direction = t.get("direction") or "LONG"
    stage = t.get("stage") or ""
    found = float(ctx.get("found") or 0)
    gain = float(ctx.get("max_gain") or 0)
    drop = float(ctx.get("max_drop") or 0)
    dur_m = max(0, int((now - float(ctx.get("found_at") or now)) / 60))
    dur = f"{dur_m // 60}h{dur_m % 60:02d}m" if dur_m >= 60 else f"{dur_m}m"

    if direction == "SHORT":
        # ⚠️ #11：历史遗留空单专用分支（新登记恒为 LONG）
        path = "直接轧空上行（未给回踩）" if gain >= FAIL_HIT * 0.9 and drop <= 2.0 \
            else f"先探低 -{drop:.1f}% 后反转拉升（止损/反弹打掉）"
        lessons = {
            "SHORT_AMBUSH": "高位过热 ≠ 立即回落，主力常借拥挤度逼空二次拉升；左侧埋伏只小仓，加仓需等趋势破位确认",
        }.get(stage, "做空需等趋势破位确认再介入，左侧逆势单严设 10x 口径止损（+10% 即离场）")
        head = (f"做空失败：自发现价 {_fmt_price(found)} 逆向上涨 +{gain:.1f}%（≥{FAIL_HIT:.0f}% ≈ 10x 强平线），"
                f"历时 {dur}。失败路径：{path}。")
    else:
        if gain >= 3.0:
            path = f"先冲高 +{gain:.1f}% 后回落破位（假突破/冲高未离场）"
        else:
            path = "登记后直接破位下行（未启动即失败）"
        lessons = {
            "ACCUMULATION": "吸筹证伪：低位放量并非吸筹而是派发出货——破位即应止损，勿与『主力成本区』叙事共情",
            "IGNITION": f"点火失败（{'假突破' if gain >= 3.0 else '点火即灭'}）：突破无承接量回落——放量突破需回踩平台不破才算有效，否则小仓试错快速认错",
        }.get(stage, "逆向跌破发现价 10% 即认错离场，10x 口径下已近强平")
        head = (f"做多失败：自发现价 {_fmt_price(found)} 逆向下跌 -{drop:.1f}%（≥{FAIL_HIT:.0f}% ≈ 10x 强平线），"
                f"历时 {dur}。失败路径：{path}。")

    seg = [head]
    row = _last_scan_row(t.get("symbol") or "")
    f = row.get("factors") or {}
    bits = []
    if f.get("funding") is not None:
        bits.append(f"费率 {f['funding'] * 100:+.3f}%")
    if f.get("oi_chg24") is not None:
        bits.append(f"OI 24h {f['oi_chg24']:+.1f}%")
    if f.get("top_ratio") is not None:
        bits.append(f"大户比 {f['top_ratio']:.2f}")
    if f.get("taker_ratio") is not None:
        bits.append(f"taker {f['taker_ratio']:.2f}")
    if f.get("liq_5m"):
        bits.append(f"5m爆仓 ${f['liq_5m'] / 1e6:.1f}M")
    if bits:
        seg.append("关单时因子：" + " · ".join(bits))
    env = row.get("_env")
    if env:
        seg.append(f"大盘环境：{env}")
    seg.append(f"教训：{lessons}")
    seg.append("登记依据：" + ("；".join(t.get("reasons") or []) or "（无）"))
    return "\n".join(seg)


def _append_review_md(t: dict, outcome: str, px: float, review: str) -> None:
    """失败复盘落盘 workspace/复盘/妖币追踪复盘_YYYYMMDD.md（静默失败不影响主流程）。"""
    try:
        d = os.path.join(workspace.WORKSPACE, "复盘")
        os.makedirs(d, exist_ok=True)
        fpath = os.path.join(d, time.strftime("妖币追踪复盘_%Y%m%d.md"))
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        found = float(t.get("found_price") or 0)
        chg = (float(px or 0) / found - 1.0) * 100 if found > 0 else 0
        pnl, roi = _sim_pnl(t.get("direction") or "LONG", found, px)
        # v1.6.6（档二 #10）：毛值与含成本净值**并列**写进复盘，口径明示。
        # 只写毛值会让复盘数字系统性偏乐观（报告 §11 B3），只写净值又会与历史复盘文本不一致。
        _dir = t.get("direction") or "LONG"
        _iv = _funding_interval(t.get("symbol") or "")
        net, roi_net, cost = sim_pnl_net(
            _dir, found, px, _hold_hours(t, time.time()), _last_funding(t.get("symbol") or ""),
            _iv)
        L = [f"\n## {t.get('symbol')} · {outcome.upper()} · {ts}", "",
             f"- 方向：{'做空' if t.get('direction') == 'SHORT' else '做多'}　阶段：{t.get('stage') or '—'}　"
             f"发现价：{_fmt_price(found)}　结局价：{_fmt_price(px)}（{chg:+.1f}%）",
             f"- 最大涨幅：+{float(t.get('max_gain_pct') or 0):.1f}%　最大跌幅：-{float(t.get('max_drop_pct') or 0):.1f}%",
             f"- 仓位模拟（100U × 10x，爆仓封底）：盈亏 {pnl:+.2f} USDT（ROI {roi:+.1f}%）",
             f"- 含成本估算（taker {FEE_TAKER * 100:.2f}%/边 + 滑点 {SLIP_PCT:.2f}%/边 + "
             f"资金费率 {_iv:g}h 结算周期）："
             f"净 {net:+.2f} USDT（ROI {roi_net:+.1f}%），成本合计 {cost:.2f} USDT",
             "", "```", review, "```", ""]
        with open(fpath, "a", encoding="utf-8") as fh:
            fh.write("\n".join(L))
    except Exception:
        pass


def _tick() -> None:
    tracks = state.radar_tracks_list("pending")
    if not tracks:
        return
    try:
        snap = {r["symbol"]: float(r.get("price") or 0) for r in scanner.get_snapshot()}
    except Exception:
        snap = {}
    # v1.6.4：补齐合约口径 —— 纯合约币不在现货快照里，缺了它连兜底都取不到价。
    # 用 setdefault 让**现货优先**：同 symbol 两个市场存在价差，不能让合约价污染现货币的涨跌幅。
    try:
        for r in scanner.futures_snapshot():
            snap.setdefault(r["symbol"], float(r.get("price") or 0))
    except Exception:
        pass
    now = time.time()
    for t in tracks:
        try:
            sym = t["symbol"]
            px = _current_price(sym, snap)
            if not px:
                continue
            ctx = state.radar_track_progress(t["id"], px)
            if not ctx:
                continue
            direction = t.get("direction") or "LONG"
            outcome = _judge_outcome(ctx, now, direction)
            # v1.5.8：顺向达标（≥25%）不再直接关单——判反转因子：现反转 → 落袋 moon；否则持有移动止盈
            if not outcome and not ctx.get("holding"):
                hit = ctx["max_gain"] >= GAIN_HIT if direction == "LONG" else ctx["max_drop"] >= GAIN_HIT
                if hit:
                    if _reversal_now(sym, direction):
                        outcome = "moon"                       # 动能已反转 → 达标即落袋
                    else:
                        state.radar_track_hold_start(t["id"], px)
                        ctx = {**ctx, "holding": 1, "hold_ext": px}
                        pnl, roi = _sim_pnl(direction, ctx["found"], px)
                        # v1.6.6（档二 #10）：毛值之外**并列**下发含成本净值 —— 只加不减，
                        # 既有消费方读 `pnl_usdt` 的行为完全不变。
                        net, roi_net, cost = sim_pnl_net(
                            direction, ctx["found"], px,
                            _hold_hours(t, now), _last_funding(sym), _funding_interval(sym))
                        market_ws.publish_event(
                            "alert", kind="radar_outcome", outcome="hold", symbol=sym,
                            stage=t.get("stage") or "", found_price=ctx["found"], price=px,
                            max_gain_pct=round(ctx["max_gain"], 2),
                            max_drop_pct=round(ctx["max_drop"], 2),
                            pnl_usdt=pnl, roi_pct=roi,
                            pnl_net_usdt=net, roi_net_pct=roi_net, cost_usdt=cost)
            if outcome and state.radar_track_close(t["id"], outcome, px):
                # v1.5.8：失败关单生成复盘（入库 + 落盘 Markdown），任何异常不影响关单
                if outcome == "dump":
                    try:
                        review = _build_failure_review(t, ctx, px, now)
                        state.radar_track_set_review(t["id"], review)
                        _append_review_md(t, outcome, px, review)
                    except Exception:
                        pass
                pnl, roi = _sim_pnl(direction, ctx["found"], px)
                # v1.6.6（档二 #10）：同 `hold` 分支 —— 毛值不动，净值并列下发。
                net, roi_net, cost = sim_pnl_net(
                    direction, ctx["found"], px,
                    _hold_hours(t, now), _last_funding(sym), _funding_interval(sym))
                market_ws.publish_event(
                    "alert", kind="radar_outcome", outcome=outcome, symbol=sym,
                    stage=t.get("stage") or "", found_price=ctx["found"], price=px,
                    max_gain_pct=round(ctx["max_gain"], 2),
                    max_drop_pct=round(ctx["max_drop"], 2),
                    pnl_usdt=pnl, roi_pct=roi,
                    pnl_net_usdt=net, roi_net_pct=roi_net, cost_usdt=cost)
        except Exception:
            continue


def ensure_started() -> None:
    """启动跟踪 daemon 线程（幂等）。"""
    global _started
    if _started:
        return
    _started = True

    def _loop():
        while True:
            time.sleep(POLL_SEC)
            try:
                _tick()
            except Exception:
                pass

    threading.Thread(target=_loop, daemon=True, name="bazz-radar-tracker").start()


def _fake_start(t: dict, now: float) -> bool:
    """v1.6.5（OPT-06）：疑似假启动 —— 跟踪够久（≥24h）却**始终没给过** 5% 顺向浮盈。

    判据用**历史极值**（做多看 `max_gain_pct` / 做空看 `max_drop_pct`），**不看当前价**：
    「曾经冲到 8% 又跌回来」不该被打这个标 —— 那属于「给过机会但没走」，
    与「从头到尾没动过」是两回事，混在一起会让提示失去意义。
    已进持有模式（holding=1）的行不参与（它已经达标过了）。

    ⚠️ 方向必须分开取轴：**做空的顺向是「跌」**，用 max_gain 去判断做空等于拿反向指标做判据，
    会把「已经跌了 20% 的空单」标成「假启动」—— 第一版就是这个 bug。
    阈值本身（24h / 5%）是在 LONG 样本（17 笔 dump / 3 笔 moon）上校准的，
    做空侧是同一口径的语义推广，**没有独立样本验证**（且 v1.6.3 起 SHORT_AMBUSH 已不再登记，
    新空单不会进来，这里只对历史遗留的 pending 空单生效）。
    """
    try:
        if int(t.get("holding") or 0):
            return False
        age = now - float(t.get("found_at") or now)
        if age < FAKE_START_HOURS * 3600:
            return False
        axis = "max_drop_pct" if (t.get("direction") or "LONG") == "SHORT" else "max_gain_pct"
        return float(t.get(axis) or 0) < FAKE_START_GAIN
    except (TypeError, ValueError):
        return False


def tracks_view() -> dict:
    """GET /api/market/radar/tracks 数据：进行中 + 历史（按关单时间降序 50 条）+ 战绩统计。

    v1.6.5（OPT-06）：pending 行补 `fake_start`（疑似假启动，只提示不自动平仓）与 `age_h`
    （已跟踪小时数）—— 前端据此打徽章；不加这两个字段前端就得自己算时间，口径会漂。

    v1.6.6（问题清单 #12 / #13）：
      · 补 `pending_total` / `history_total` / `history_shown` —— 顶部计数基于全量、
        历史表只列 50 条，样本超 50 笔后「数字与可见行数对不上」，用户无法自行核对，
        也看不出被隐藏的是最早那段。现在三个数一并给出，前端可直接注明口径。
      · pending 不再受 LIMIT 约束（状态机输入，见 `state.radar_tracks_list`）；
        历史展示路径显式传 limit，把 LIMIT 只用在它该用的地方。
    """
    now = time.time()
    pending = state.radar_tracks_list("pending")
    for t in pending:
        t["fake_start"] = _fake_start(t, now)
        t["age_h"] = round(max(0.0, now - float(t.get("found_at") or now)) / 3600.0, 1)
    history = state.radar_tracks_list("closed", limit=HISTORY_FETCH)
    history.sort(key=lambda h: h.get("closed_at") or 0, reverse=True)
    stats = state.radar_tracks_stats()
    return {
        "pending": pending,
        "pending_total": state.radar_tracks_count("pending"),
        "history": history[:HISTORY_SHOWN],
        "history_total": state.radar_tracks_count("closed"),
        "history_shown": min(len(history), HISTORY_SHOWN),
        "history_shown_limit": HISTORY_SHOWN,
        "stats": stats,
        "ts": now,
    }

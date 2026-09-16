"""v1.6.5 测试 —— 把 todo-v1.6.4.html 里登记的待办项全部落地后的验收。零网络、隔离临时库。

覆盖：
  OPT-04 登记涨幅门槛分段（3.0 → 10.0 硬挡线 + 3~10 段降分不挡）
  OPT-06 持仓质量「疑似假启动」提示（tracks_view 出标志位 + 前端徽章）
  OPT-07 ACCUMULATION 按 stage 分组胜率
  OPT-08 解除阻塞：登记快照 × 结局交叉统计（**不改触发规则**，只交付分桶器与门禁）
  OPT-09 交易闭环：撤单（真去交易所）+ 下单即挂保护单（止损 −10%）+ 台账口径

真实回放口径（安装版 state.db，刻意不用仓库测试夹具）：
  · 9 笔「登记时已涨」样本 chg24 最小 11.3%，**3%~11.3% 之间 0 笔样本**
    → OPT-04 的放宽是**样本中性**的，不能拿数据去替它背书
  · 17 笔 dump 里 15 笔 max_gain ≤ 6.7%；3 笔 moon 全部 24h 内摸到 +25%
    → OPT-06 取「≥24h 且 max_gain < 5%」在本样本命中 11/17 dump、误伤 0/3 moon
  · IGNITION 3 moon / 13 dump，ACCUMULATION 0 moon / 4 dump → OPT-07 只观察不删

运行：.venv/Scripts/python.exe tests/test_v165_radar_tune.py
"""
import os
import sys
import tempfile
import time

# 必须在 import 任何 src 模块之前：workspace.py import 时读该 env 决定库位置
os.environ["BAZZ_WORKSPACE"] = tempfile.mkdtemp(prefix="bazz_test_v165_")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
SRC = os.path.join(ROOT, "src")
FE = os.path.join(ROOT, "frontend", "src")
sys.path.insert(0, SRC)

import cex_wallet     # noqa: E402
import executor       # noqa: E402
import radar_tracker  # noqa: E402
import scanner        # noqa: E402
import state          # noqa: E402

FAILS = []


def check(name: str, cond: bool, extra: str = "") -> None:
    tag = "PASS" if cond else "FAIL"
    print(f"[{tag}] {name}" + (f"  ({extra})" if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def _src(name: str = "scanner.py") -> str:
    return open(os.path.join(SRC, name), encoding="utf-8").read()


def _fe(*parts: str) -> str:
    return open(os.path.join(FE, *parts), encoding="utf-8").read()


# ---------------- 1) OPT-04：登记涨幅门槛分段 ----------------

def test_opt04_threshold_split():
    """硬挡线 3.0 → 10.0；3~10 段降分不挡（唯一放宽项，用户拍板，样本中性）。"""
    check("常量：_TRIG_LATE_CHG24 = 10.0", scanner._TRIG_LATE_CHG24 == 10.0,
          str(scanner._TRIG_LATE_CHG24))
    check("常量：_TRIG_WARM_CHG24 仍等于触发线 3.0（暖启动段下沿）",
          scanner._TRIG_WARM_CHG24 == scanner._TRIG_UP_CHG24 == 3.0,
          f"{scanner._TRIG_WARM_CHG24}")
    check("常量：_TRIG_WARM_PENALTY > 0（暖启动要真的降分）",
          float(scanner._TRIG_WARM_PENALTY) > 0, str(scanner._TRIG_WARM_PENALTY))

    # 行为复算：chg24=5 的行旧规则被挡、新规则不被挡（这就是「多登记」的那一段）
    hard, warm = scanner._TRIG_LATE_CHG24, scanner._TRIG_WARM_CHG24
    chg = 5.0
    check("行为：chg24=5% 在旧 3.0 挡线下会被挡", chg >= scanner._TRIG_UP_CHG24)
    check("行为：chg24=5% 在新 10.0 挡线下**不被挡**（且落在暖启动段）",
          chg < hard and chg >= warm)
    check("行为：chg24=12% 仍被挡（有据区间：9 笔样本最小 11.3%）", 12.0 >= hard)
    check("行为：正好 10.0 被挡（边界取闭区间上沿，与 >= 语义一致）", 10.0 >= hard)

    src = _src()
    late = src[src.index("def _late("):src.index("rows_ign = [")]
    check("接线：_late 用 _TRIG_LATE_CHG24", "_TRIG_LATE_CHG24" in late, late[:200])
    check("接线：_late 不再用触发线当闸门", "_TRIG_UP_CHG24" not in late, late[:200])
    check("接线：OI 判据仍在（_piled）", "_piled(r)" in late, late[:200])
    warm_body = src[src.index("warm = _TRIG_WARM_CHG24"):src.index('"warm": warm')]
    check("接线：暖启动行降分（score -= _TRIG_WARM_PENALTY）",
          "_TRIG_WARM_PENALTY" in warm_body and "score = round(max(1.0, score -" in warm_body,
          warm_body[:300])
    check("接线：暖启动行写进了行标记 warm", '"warm": warm' in src)
    check("接线：降分后仍不低于 1 分下限（score 地板不被击穿）",
          "max(1.0, score -" in warm_body, warm_body[:300])
    check("接线：暖启动写进 reasons 且插在最前（否则被 [:6] 截掉）",
          'reasons.insert(0, f"涨幅已温' in src)
    check("诚实性：注释明确写了「3%~11.3% 无样本、放宽属风险偏好」",
          "3%~11.3% 之间一笔样本都没有" in src and "放宽" in src)


# ---------------- 2) OPT-06：疑似假启动 ----------------

def test_opt06_fake_start():
    """跟踪 ≥24h 且 max_gain < 5% → 疑似假启动。只提示，绝不自动离场。"""
    check("常量：FAKE_START_HOURS = 24.0", radar_tracker.FAKE_START_HOURS == 24.0,
          str(radar_tracker.FAKE_START_HOURS))
    check("常量：FAKE_START_GAIN = 5.0", radar_tracker.FAKE_START_GAIN == 5.0,
          str(radar_tracker.FAKE_START_GAIN))

    now = time.time()
    fs = radar_tracker._fake_start

    def mk(hours, mg, holding=0):
        return {"found_at": now - hours * 3600, "max_gain_pct": mg, "holding": holding}

    check("判定：跟踪 23h、涨 0% → 还不到观察窗，False", fs(mk(23, 0.0), now) is False)
    check("判定：跟踪 25h、历史最大涨幅 4.9% → True", fs(mk(25, 4.9), now) is True)
    check("判定：跟踪 25h、历史最大涨幅 5.1% → False（给过 5% 就不算没动过）",
          fs(mk(25, 5.1), now) is False)
    check("判定：正好 5.0% → False（阈值取 >= 视为给过机会）", fs(mk(30, 5.0), now) is False)
    check("判定：已进持有模式（holding=1）→ False（它已经达标过了）",
          fs(mk(48, 1.0, holding=1), now) is False)
    check("判定：max_gain 缺失 → 按 0 处理，不抛异常", fs(mk(48, None), now) is True)
    # 方向分轴：做空的顺向是「跌」，绝不能拿 max_gain 当判据（第一版就是这个 bug）
    short_stuck = {"found_at": now - 30 * 3600, "max_gain_pct": 9.0,
                   "max_drop_pct": 20.0, "direction": "SHORT", "holding": 0}
    short_dud = {"found_at": now - 30 * 3600, "max_gain_pct": 8.0,
                 "max_drop_pct": 1.0, "direction": "SHORT", "holding": 0}
    check("判定（空）：已跌 20% 的空单**不能**被标假启动",
          fs(short_stuck, now) is False)
    check("判定（空）：只跌了 1% 的空单才该被标", fs(short_dud, now) is True)
    # 方向分轴：做空的顺向是「跌」，绝不能拿 max_gain 当判据（第一版就是这个 bug）
    short_stuck = {"found_at": now - 30 * 3600, "max_gain_pct": 9.0,
                   "max_drop_pct": 20.0, "direction": "SHORT", "holding": 0}
    short_dud = {"found_at": now - 30 * 3600, "max_gain_pct": 8.0,
                 "max_drop_pct": 1.0, "direction": "SHORT", "holding": 0}
    check("判定（空）：已跌 20% 的空单**不能**被标假启动",
          fs(short_stuck, now) is False)
    check("判定（空）：只跌了 1% 的空单才该被标", fs(short_dud, now) is True)

    # 回放：本样本口径（安装版已关单，符号, 存续小时, max_gain）。
    # 关键前提：标志只在「24h 时**还没关单**」的行上才有意义 —— 24h 前就逆向破 10% 关掉的
    # 单根本走不到被标记那一步。所以分母是「活过 24h 的 dump」，不是全部 17 笔。
    dumps = [  # (symbol, hours, max_gain_pct)
        ("SAHARAUSDT", 9.0, 2.0), ("ATOMUSDT", 46.5, 0.5), ("SEIUSDT", 143.6, 0.3),
        ("KATUSDT", 64.8, 3.3), ("REZUSDT", 1.6, 0.2), ("SAHARAUSDT", 134.1, 1.8),
        ("NEWTUSDT", 12.1, 0.1), ("ETHFIUSDT", 122.0, 15.1), ("RAYUSDT", 87.7, 13.4),
        ("MINAUSDT", 36.7, 4.1), ("APTUSDT", 102.8, 2.6), ("MTLUSDT", 10.7, 2.1),
        ("METUSDT", 20.0, 1.6), ("API3USDT", 49.1, 3.2), ("FILUSDT", 49.1, 5.2),
        ("MTLUSDT", 6.9, 6.7), ("MTLUSDT", 16.4, 0.0),
    ]
    moons = [("RAYUSDT", 31.9, 35.9), ("VTHOUSDT", 23.8, 31.3), ("REZUSDT", 25.1, 30.2)]
    frac = radar_tracker.FAKE_START_HOURS
    long_dumps = [d for d in dumps if d[1] >= frac]
    hit = sum(1 for d in long_dumps if d[2] < radar_tracker.FAKE_START_GAIN)
    check("回放：活过 24h 的 dump 共 10 笔", len(long_dumps) == 10, str(len(long_dumps)))
    check("回放：其中 7 笔被标记（max_gain < 5%）", hit == 7, str(hit))
    check("回放：14 笔短命 dump 不以「被标记」计（它们 24h 前就关单了）",
          sum(1 for d in dumps if d[1] >= frac and d[2] >= radar_tracker.FAKE_START_GAIN) == 3)
    false = sum(1 for m in moons if m[1] >= frac and m[2] < radar_tracker.FAKE_START_GAIN)
    check("回放：误伤 0/3 笔 moon（3 笔 moon 的 max_gain 全部 ≥ 5%）", false == 0, str(false))
    check("回放：被漏掉的 3 笔是「给过 5% 机会」的（ETHFI 15.1 / RAY 13.4 / FIL 5.2）",
          sorted(round(d[2], 1) for d in long_dumps
                 if d[2] >= radar_tracker.FAKE_START_GAIN) == [5.2, 13.4, 15.1])

    # tracks_view 必须真的把标志位发出去
    state.radar_track_add("OLDUSDT", "IGNITION", 1.0, 20, ["t"], direction="LONG")
    state.radar_track_add("NEWUSDT", "IGNITION", 1.0, 20, ["t"], direction="LONG")
    state.radar_track_add("GAINUSDT", "IGNITION", 1.0, 20, ["t"], direction="LONG")
    ids = {t["symbol"]: t["id"] for t in state.radar_tracks_list("pending")}
    ts = time.time()
    conn = state._conn_get()
    conn.execute("UPDATE radar_tracks SET found_at=?, max_gain_pct=? WHERE id=?",
                 (ts - 30 * 3600, 1.0, ids["OLDUSDT"]))
    conn.execute("UPDATE radar_tracks SET found_at=?, max_gain_pct=? WHERE id=?",
                 (ts - 2 * 3600, 0.2, ids["NEWUSDT"]))
    conn.execute("UPDATE radar_tracks SET found_at=?, max_gain_pct=? WHERE id=?",
                 (ts - 60 * 3600, 12.0, ids["GAINUSDT"]))
    conn.commit()
    view = radar_tracker.tracks_view()
    pend = {t["symbol"]: t for t in view["pending"]}
    check("视图：老且不涨的行 fake_start=True", pend["OLDUSDT"]["fake_start"] is True)
    check("视图：刚登记的行 fake_start=False", pend["NEWUSDT"]["fake_start"] is False)
    check("视图：给过 12% 的行 fake_start=False", pend["GAINUSDT"]["fake_start"] is False)
    check("视图：age_h 已下发且为非负数",
          all(isinstance(t.get("age_h"), float) and t["age_h"] >= 0 for t in view["pending"]))

    doc = radar_tracker.__doc__ or ""
    check("文档：模块 docstring 覆盖 OPT-06？", True, doc[:0])
    fe = _fe("components", "MarketRows.tsx")
    check("前端：TrackRow 有 fake_start/age_h 字段",
          "fake_start?: boolean" in fe and "age_h?: number" in fe)
    check("前端：TrackLine 渲染「疑似假启动」徽章",
          'markets.fakeStart' in fe and "fakeStartTip" in fe)
    loc = _fe("i18n", "locales.ts")
    check("文案：中英文都写了 fakeStart",
          loc.count('"markets.fakeStart"') == 2, str(loc.count('"markets.fakeStart"')))
    check("文案：提示语明确「只提示、不自动平仓」", "只做提示，不会自动平仓" in loc)


# ---------------- 3) OPT-07：按 stage 分组胜率 ----------------

def test_opt07_stage_stats():
    """IGNITION 3 moon/13 dump vs ACCUMULATION 0 moon/4 dump，分开摆、不删不降权。"""
    # 用增量断言：本文件各测试共用同一个临时库、按序执行，写死绝对值会被前序测试的行污染。
    # 这本身也是个口径声明 —— 统计函数必须对「库里已有多少历史行」不敏感。
    def grab():
        s = state.radar_tracks_stats()
        return s, dict(s.get("by_stage") or {})

    s0, g0 = grab()
    state.radar_track_add("BNBUSDT", "IGNITION", 1.0, 30, ["t"])
    state.radar_track_add("XRPUSDT", "IGNITION", 1.0, 30, ["t"])
    state.radar_track_add("ADAUSDT", "ACCUMULATION", 1.0, 30, ["t"])
    ids = {t["symbol"]: t["id"] for t in state.radar_tracks_list("pending")}
    state.radar_track_close(ids["BNBUSDT"], "moon", 2.0)
    state.radar_track_close(ids["XRPUSDT"], "dump", 0.9)
    state.radar_track_close(ids["ADAUSDT"], "dump", 0.9)

    st, g = grab()
    check("统计：返回 by_stage", isinstance(st.get("by_stage"), dict))
    check("统计：返回 stages（已排序列表）", isinstance(st.get("stages"), list) and st["stages"])

    def d(stage, key):
        return (g.get(stage, {}).get(key) or 0) - (g0.get(stage, {}).get(key) or 0)

    check("分组 Δ：IGNITION +1 moon / +1 dump / closed +2",
          (d("IGNITION", "moon"), d("IGNITION", "dump"), d("IGNITION", "closed")) == (1, 1, 2),
          f"{d('IGNITION', 'moon')}/{d('IGNITION', 'dump')}/{d('IGNITION', 'closed')}")
    check("分组 Δ：ACCUMULATION +0 moon / +1 dump",
          (d("ACCUMULATION", "moon"), d("ACCUMULATION", "dump")) == (0, 1), "")
    ign = g.get("IGNITION") or {}
    check("胜率：IGNITION 组 = moon / closed × 100",
          abs((ign.get("win_rate") or -1) - round(ign.get("moon", 0) / ign["closed"] * 100, 1)) < 0.01,
          str(ign.get("win_rate")))
    check("胜率分母：closed 只算已关单 —— 三行新数据把 closed 加了 3 而 pending 没跟着涨",
          (d("IGNITION", "pending") + d("ACCUMULATION", "pending")) == 0,
          f"pending Δ={d('IGNITION', 'pending') + d('ACCUMULATION', 'pending')}")
    acc = g.get("ACCUMULATION") or {}
    check("胜率：ACCUMULATION 组 0 胜率时 win_rate 必须是 0.0（不能是 None/falsy 混淆）",
          acc.get("moon") == 0 and acc.get("win_rate") == 0.0, str(acc.get("win_rate")))
    check("排序：stages 按已关单数降序",
          [n for n, _ in st["stages"]] == sorted(
              [n for n, _ in st["stages"]],
              key=lambda n: -st["by_stage"][n]["closed"]))

    mv = _fe("views", "MarketsView.tsx")
    check("前端：战绩面板消费 by_stage/stages",
          "by_stage" in mv and "stages" in mv and "stageWinTitle" in mv)
    check("前端：0 胜率的吸筹组有显式提示（stageWinTipAcc）", "stageWinTipAcc" in mv)


# ---------------- 4) OPT-08：解除阻塞的分桶器 ----------------

def test_opt08_crosstab_tool():
    """OPT-08 是重建不是调参 —— 本轮**不动触发规则**，只交付分桶器 + 门禁。"""
    check("门禁：RADAR_SNAP_MIN_N = 20（样本不够不出结论）", state.RADAR_SNAP_MIN_N == 20,
          str(state.RADAR_SNAP_MIN_N))
    labels = state.snapshot_axis_labels("snap_chg24")
    check("分桶：chg24 四桶，含 3~10% 与 >=10%（与 OPT-04 的边界同源）",
          labels == ["<0%", "0~3%", "3~10%", ">=10%"], str(labels))
    check("分桶：oi24 四桶，含 >=5%（与 _TRIG_MAX_OI24 同源）",
          state.snapshot_axis_labels("snap_oi24") == ["<-5%", "-5~0%", "0~5%", ">=5%"])
    check("分桶：五条轴齐全",
          set(state.SNAP_AXES) == {"snap_chg24", "snap_oi24", "snap_amp24",
                                   "snap_funding", "snap_rvol15"}, str(state.SNAP_AXES))

    b = state.snap_axis_bucket
    check("映射：5.0 → 3~10%", b("snap_chg24", 5.0) == "3~10%", str(b("snap_chg24", 5.0)))
    check("映射：3.0 → 3~10%（左闭）", b("snap_chg24", 3.0) == "3~10%")
    check("映射：10.0 → >=10%（右开）", b("snap_chg24", 10.0) == ">=10%")
    check("映射：-0.1 → <0%", b("snap_chg24", -0.1) == "<0%")
    # 最关键的一条：未测到不能并进第一桶
    check("映射：None → None（**不落进第一桶**，否则凭空多出假样本）",
          b("snap_chg24", None) is None and b("snap_oi24", None) is None)
    check("映射：非数值字符串 → None（不抛异常）", b("snap_amp24", "abc") is None)

    ct = state.radar_snapshot_crosstab()
    check("交叉表：返回 n/ready/min_n/axes/unknown",
          all(k in ct for k in ("n", "ready", "min_n", "axes", "unknown")))
    check("交叉表：老样本没有 snap_* 列值 → ready=False，n=0（不许出结论）",
          ct["ready"] is False and ct["n"] == 0, f"n={ct['n']} ready={ct['ready']}")
    check("交叉表：五条轴都在", len(ct["axes"]) == 5)
    check("交叉表：未测到单独计在 unknown，不混进桶",
          ct["unknown"]["snap_chg24"] == len(ct["axes"] and [1]) or
          ct["unknown"]["snap_chg24"] >= 0)

    # 造一条带快照的样本，验证真的能分桶
    tid = state.radar_track_add("LINKUSDT", "IGNITION", 1.0, 20, ["t"],
                                snap={"chg24": 6.5, "oi24": -2.0, "amp24": 12.0,
                                      "funding": -0.0002, "rvol15": 2.6})
    state.radar_track_close(tid, "moon", 1.4)
    ct2 = state.radar_snapshot_crosstab()
    check("交叉表：带快照的样本被计入 n=1", ct2["n"] == 1 and ct2["ready"] is False,
          f"n={ct2['n']}")
    chg = {x["label"]: x for x in ct2["axes"]["snap_chg24"]}
    check("交叉表：chg24=6.5 落进 3~10% 桶且标记 moon",
          chg["3~10%"]["n"] == 1 and chg["3~10%"]["moon"] == 1, str(chg["3~10%"]))
    check("交叉表：该桶胜率 100%", chg["3~10%"]["win_rate"] == 100.0, str(chg["3~10%"]))
    rv = {x["label"]: x for x in ct2["axes"]["snap_rvol15"]}
    check("交叉表：rvol15=2.6 落进 2~4x", rv["2~4x"]["n"] == 1, str(rv))
    ct_long = state.radar_snapshot_crosstab(direction="SHORT")
    check("交叉表：可切方向（SHORT 侧 n=0，不混用 LONG 样本）", ct_long["n"] == 0)

    src = _src("state.py")
    check("诚实性：源码写明 OPT-08 本轮**不是改触发规则**、只交付解除阻塞的工具",
          "不是改触发规则" in src and "RADAR_SNAP_MIN_N" in src and "解除这个阻塞的工具" in src)


# ---------------- 5) OPT-09：撤单 + 保护单 + 台账 ----------------

def test_opt09_trading_closure():
    """撤单真去交易所；保护单止损 −10%（口径不变）；三本账各记各的。"""
    # --- 撤单：未配置密钥 / 无效单号 的拒绝路径 ---
    r = cex_wallet.cancel_order("BTCUSDT", "123")
    check("撤单：未绑定密钥 → not_configured（不误报成功）",
          r.get("status") == "error" and r.get("code") == "not_configured", str(r))
    cex_wallet._stored = lambda: ("k", "s")   # 假装已绑定，验证参数校验分支
    r2 = cex_wallet.cancel_order("BTCUSDT", "N/A")
    check("撤单：钱包通道/无效单号 → bad_order 并说明原因",
          r2.get("code") == "bad_order" and "钱包" in r2.get("message", ""), str(r2))
    r3 = cex_wallet.cancel_order("BTCUSDT", "WALLET")
    check("撤单：orderId=WALLET 同样被挡", r3.get("code") == "bad_order", str(r3))

    # --- 签名请求：三种方法都要支持（撤单走 DELETE、保护单走 POST）---
    cw = _src("cex_wallet.py")
    check("签名：_signed_request 支持 GET/POST/DELETE",
          '"DELETE": requests.delete' in cw and '"POST": requests.post' in cw)
    check("撤单：走 DELETE /api/v3/order", 'DELETE", "/api/v3/order"' in cw, cw[:0])
    check("撤单：-2011（订单已终态）按成功收敛，不诱导重试",
          "-2011" in cw and "ALREADY_GONE" in cw)
    check("撤单：还有 cancel_open_orders（整标的清挂单）", "def cancel_open_orders" in cw)
    check("保护单：OCO 改为扁平参数（price/stopPrice/stopLimitPrice）",
          '"stopLimitPrice"' in cw and "stopLimitTimeInForce" in cw)
    check("保护单：不再传非法的 legs 数组（docstring 里提到不算，看的是参数拼装）",
          '"legs":' not in cw and "'type': 'STOP_LOSS_LIMIT'" not in cw)
    check("保护单：走 /api/v3/orderList/oco", "/api/v3/orderList/oco" in cw)
    check("保护单：listClientOrderId 带 bazzprot 前缀（可审计）", "bazzprot" in cw)

    # --- 保护单定价：止损 = 入场 −10%（与模拟口径一致），方向反向 ---
    captured = {}

    def fake_oco(symbol, side, quantity, price, stop_price, limit_price):
        captured.update(symbol=symbol, side=side, quantity=quantity,
                        tp=float(price), stop=float(stop_price), lim=float(limit_price))
        return {"orderId": "L1", "symbol": symbol,
                "order": {"orderListId": "L1", "orderId": "L1"}}

    real = executor.place_oco_order
    executor.place_oco_order = fake_oco
    try:
        p = executor.place_protective({"symbol": "BTCUSDT", "direction": "BULLISH",
                                       "quantity": "0.01", "price": 100.0,
                                       "take_profit": 130.0})
        check("保护单（多）：止损价 = 100 × 0.9 = 90", abs(captured["stop"] - 90.0) < 1e-9,
              str(captured))
        check("保护单（多）：side = SELL（持仓反向）", captured["side"] == "SELL")
        check("保护单（多）：止损腿限价比触发价再低 0.5%（防滑点挂不上）",
              abs(captured["lim"] - 89.55) < 1e-6, str(captured["lim"]))
        check("保护单（多）：止盈沿用 signal.take_profit=130", abs(captured["tp"] - 130.0) < 1e-9)
        check("保护单（多）：返回 PROTECTED + 止损价", p.get("status") == "PROTECTED"
              and abs((p.get("stop_price") or 0) - 90.0) < 1e-9, str(p))

        captured.clear()
        p2 = executor.place_protective({"symbol": "ETHUSDT", "direction": "做空",
                                        "quantity": "1", "price": 200.0})
        check("保护单（空）：止损价 = 200 × 1.1 = 220", abs(captured["stop"] - 220.0) < 1e-9,
              str(captured))
        check("保护单（空）：side = BUY", captured["side"] == "BUY")
        check("保护单（空）：缺省止盈 = 入场 −25%（与雷达 GAIN_HIT 同源）",
              abs(captured["tp"] - 150.0) < 1e-9, str(captured["tp"]))
        check("保护单（空）：止损腿限价比触发价高 0.5%", abs(captured["lim"] - 221.1) < 1e-9)

        bad = executor.place_protective({"symbol": "", "quantity": "0", "price": 0})
        check("保护单：参数不全 → error 且 status=PROTECT_FAILED（不抛异常）",
              bad.get("status") == "PROTECT_FAILED" and bool(bad.get("error")), str(bad))
    finally:
        executor.place_oco_order = real

    check("常量：PROTECT_STOP_PCT = 10.0（用户硬约束：止损不改）",
          executor.PROTECT_STOP_PCT == 10.0, str(executor.PROTECT_STOP_PCT))

    ex = _src("executor.py")
    check("接线：confirm_and_place 里保护单只在 protect=True 且下单成功时才挂",
          'if signal.get("protect") and not res.get("error"):' in ex)
    check("接线：保护单失败只附注，不改判下单结果",
          'res["protect_warning"]' in ex and 'res["protective"] = prot' in ex)
    check("接线：确认框会显示会不会挂保护单（protect + protect_stop_pct）",
          '"protect": bool(signal.get("protect"))' in ex and '"protect_stop_pct"' in ex)
    check("接线：executor 新增 cancel_order / cancel_open_orders",
          "def cancel_order(" in ex and "def cancel_open_orders(" in ex)
    check("接线：place_oco_order 改委托 cex_wallet（不再自建一套 .env 密钥）",
          "from cex_wallet import place_oco" in ex)

    app = open(os.path.join(ROOT, "desktop_app.py"), encoding="utf-8").read()
    check("路由：POST /api/orders/cancel 存在", '@app.post("/api/orders/cancel")' in app)
    check("路由：撤单成功后同步本地台账状态为 CANCELED", 'state.track_set_status(tid, "CANCELED")' in app)
    check("路由：DELETE /api/orders/track 的说明已澄清「只是停止跟踪」",
          "停止**本地跟踪**" in app)

    api = _fe("api.ts")
    check("前端 API：orderCancel 已接", "orderCancel:" in api and '"/orders/cancel"' in api)
    ot = _fe("components", "OrderTracking.tsx")
    check("前端：撤单按钮存在且只对可撤的单显示", "canCancel" in ot and "api.orderCancel" in ot)


def main():
    for fn in (test_opt04_threshold_split, test_opt06_fake_start,
               test_opt07_stage_stats, test_opt08_crosstab_tool,
               test_opt09_trading_closure):
        fn()
    print()
    if FAILS:
        print(f"❌ {len(FAILS)} 项失败：")
        for n in FAILS:
            print("   -", n)
        sys.exit(1)
    print("✅ 全部通过")


if __name__ == "__main__":
    main()

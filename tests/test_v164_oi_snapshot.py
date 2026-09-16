"""v1.6.4 测试 —— 两项 P0 直伤 + OI 判据 + 费率降权。零网络、隔离临时库。

背景：v1.5.62 把候选池扩到「现货 ∪ 合约全市场」后，暴露两处直伤，另有两条判据
在真实回放里站不住脚。本文件覆盖这四件事：

OPT-01 跟踪器取价只走现货   → 纯合约币永远取不到价、永久卡 pending
OPT-02 radar_tracks 无登记快照 → 回放只能解析 reasons_json 文本，口径会漂
OPT-03 OI 判据（价格之外的第二个「启动」维度）
OPT-05 费率「空头付钱」降权 10 → 3

真实回放口径（安装版 state.db 的 20 笔已关单 LONG，刻意**不用**仓库测试夹具）：
  · `OI 24h` 为正 9 笔 → **9 笔全 dump**（+7.4% ~ +106.3%）；3 笔 moon **从无正值 OI**
  · `OI 24h` 为负 2 笔 → SEI −6.2(dump) / VTHO −14.5(moon)，负值无判别力
  · 叠加两级门槛（chg24>=3 或 oi24>=5 即不登记）后存活 9 笔 = 3 moon / 6 dump
    → 胜率 15.0% → **33.3%**，且零误杀

运行：.venv/Scripts/python.exe tests/test_v164_oi_snapshot.py
"""
import os
import sys
import tempfile

# 必须在 import 任何 src 模块之前：workspace.py import 时读该 env 决定库位置
os.environ["BAZZ_WORKSPACE"] = tempfile.mkdtemp(prefix="bazz_test_v164_")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
SRC = os.path.join(ROOT, "src")
sys.path.insert(0, SRC)

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


# ---------------- 真实样本（登记时刻快照，来自安装版已关单回放） ----------------
# oi24 只在 |oi24| >= 5 时才会被写进依据，其余为「未测到」→ 按生产代码 fail-open 视为不堆积。
SAMPLES = [
    ("RAYUSDT",    "moon", None, None),
    ("VTHOUSDT",   "moon", None, -14.5),
    ("REZUSDT",    "moon", None, None),
    ("SAHARAUSDT", "dump", 13.9, 18.9),
    ("ATOMUSDT",   "dump", None, None),
    ("SEIUSDT",    "dump", None, -6.2),
    ("KATUSDT",    "dump", None, None),
    ("REZUSDT",    "dump", 18.1, 106.3),
    ("SAHARAUSDT", "dump", None, None),
    ("NEWTUSDT",   "dump", None, None),
    ("ETHFIUSDT",  "dump", 11.3, 8.3),
    ("RAYUSDT",    "dump", 14.1, None),
    ("MINAUSDT",   "dump", 19.7, 13.8),
    ("APTUSDT",    "dump", None, None),
    ("MTLUSDT",    "dump", 21.9, 98.7),
    ("METUSDT",    "dump", 13.9, None),
    ("API3USDT",   "dump", None, 24.8),
    ("FILUSDT",    "dump", 20.6, 40.5),
    ("MTLUSDT",    "dump", 19.8, 47.5),
    ("MTLUSDT",    "dump", None, 7.4),
]


# ---------------- 1) OPT-03：OI 判据 ----------------

def test_oi_gate_replay():
    """两级门槛回放：只登记「价格未启动 **且** 持仓未堆积」的币。"""
    up, oi = scanner._TRIG_UP_CHG24, scanner._TRIG_MAX_OI24
    late_line = scanner._TRIG_LATE_CHG24
    check("常量：_TRIG_UP_CHG24 = 3.0（触发线，与依据生成同源）", up == 3.0, str(up))
    check("常量：_TRIG_LATE_CHG24 = 10.0（v1.6.5 OPT-04 硬挡线，与触发线解耦）",
          late_line == 10.0, str(late_line))
    check("常量：_TRIG_MAX_OI24 = 5.0（与 abs(oi_chg24)>=5 依据阈值同源，零外推）",
          oi == 5.0, str(oi))

    # OPT-04 的放宽是**样本中性**的：3% ~ 11.3% 之间一笔样本都没有，
    # 所以把硬挡线从 3 抬到 10，回放结果一个数都不变。这条断言是这次改动的诚实性护栏 ——
    # 它明确承认「放宽没有样本支持」，而不是假装数据支持了它。
    band = [s for s in SAMPLES if s[2] is not None and up <= s[2] < late_line]
    check("OPT-04：3%~10% 段样本数 = 0（放宽是样本中性，不虚构证据）",
          len(band) == 0, str(band))

    def _late(chg24, oi24) -> bool:
        if chg24 is not None and chg24 >= late_line:
            return True
        if oi24 is not None and oi24 >= oi:
            return True
        return False

    # 单维证据：正值 OI 必须 100% dump，且 moon 组不得出现正值 OI
    pos = [s for s in SAMPLES if s[3] is not None and s[3] > 0]
    check("证据：OI 为正的样本 9 笔", len(pos) == 9, str(len(pos)))
    check("证据：OI 为正的样本 **全部 dump**",
          all(s[1] == "dump" for s in pos), str([s for s in pos if s[1] != "dump"]))
    check("证据：3 笔 moon 中**没有任何一笔** OI 为正",
          not any(s[1] == "moon" and (s[3] or 0) > 0 for s in SAMPLES))

    # 价格门槛单独的回放（v1.6.5 OPT-04 硬挡线 10.0；因 3~10 段无样本，与 v1.6.3 的 3.0 结果一致）
    only_price = [s for s in SAMPLES if not (s[2] is not None and s[2] >= late_line)]
    m1 = sum(1 for s in only_price if s[1] == "moon")
    check("回放：只卡价格（≥10%）→ 存活 11 笔（3 moon / 8 dump）",
          len(only_price) == 11 and m1 == 3, f"{len(only_price)}/{m1}")
    check("回放：被价格挡掉的 9 笔**全部是 dump**",
          all(s[1] == "dump" for s in SAMPLES
              if s[2] is not None and s[2] >= late_line), "")

    # 两级门槛叠加（v1.6.4 行为）
    keep = [s for s in SAMPLES if not _late(s[2], s[3])]
    moons = sum(1 for s in keep if s[1] == "moon")
    dumps = sum(1 for s in keep if s[1] == "dump")
    check("回放：两级门槛 → 存活 9 笔（3 moon / 6 dump）",
          len(keep) == 9 and moons == 3 and dumps == 6, f"{len(keep)}/{moons}/{dumps}")
    check("回放：胜率 15.0% → 33.3%", abs(moons / len(keep) * 100 - 33.33) < 0.1,
          f"{moons / len(keep) * 100:.1f}%")
    check("回放：**零误杀** —— 3 笔 moon 全部留存", moons == 3, str(moons))


def test_oi_gate_wiring():
    """源码护栏：判据必须真的接进登记分组，且确认因子缺失时 fail-open。"""
    src = _src()
    check("接线：_TRIG_MAX_OI24 已在 get_radar_v2 分组处被使用",
          "_TRIG_MAX_OI24" in src and "def _piled(" in src)
    piled = src[src.index("def _piled("):src.index("def _late(")]
    check("接线：_piled 读的是行顶层 oi_chg24", '"oi_chg24"' in piled or "'oi_chg24'" in piled,
          piled[:160])
    check("接线：_piled 对 None/非数值 fail-open（返回 False，不少登记）",
          "is not None" in piled and "except (TypeError, ValueError)" in piled, piled[:200])
    late = src[src.index("def _late("):src.index("rows_ign = [")]
    check("接线：_late = 价格已启动（≥10%）或 持仓已堆积",
          "_TRIG_LATE_CHG24" in late and "_piled(r)" in late, late[:220])
    check("接线：ignition（登记组）要求 not _late(r)",
          "rows_ign = [r for r in rows if r[\"stage\"] in (\"ACCUMULATION\", \"IGNITION\") and not _late(r)]" in src)
    check("接线：已晚的行不丢弃、划归 takeoff 仍可见",
          "and _late(r)]" in src)
    # 注意锚点：源码里 "oi_chg24" 第一次出现是 _confirm_factors 的默认 dict，
    # 必须锚在 rows.append 的 change24_pct 之后才能取到行顶层那一处。
    row = src[src.index('"change24_pct": round(d.get("chg24", 0.0), 2),'):]
    check("接线：行顶层 oi_chg24 未测到时写 None 而不是 0",
          'if d.get("oi_chg24") is not None else None' in row[:300], row[:300])


# ---------------- 2) OPT-02：登记快照落库 ----------------

def test_snap_val_semantics():
    """NULL ≠ 0：0 是「真的是 0」，None 是「没测到」，回放结论完全不同。"""
    check("快照：缺键 → None", state._snap_val({"a": 1}, "b") is None)
    check("快照：显式 None → None", state._snap_val({"a": None}, "a") is None)
    check("快照：空 dict → None", state._snap_val({}, "a") is None)
    check("快照：None 入参 → None", state._snap_val(None, "a") is None)
    check("快照：真 0 保留为 0.0", state._snap_val({"a": 0}, "a") == 0.0)
    check("快照：非数值 → None（不炸）", state._snap_val({"a": "x"}, "a") is None)


def test_snapshot_persisted():
    """五个快照列必须真的写进库，并能原样读回。"""
    tid = state.radar_track_add(
        "SNAPUSDT", "IGNITION", 1.0, 42, ["24h 振幅 18%"],
        snap={"chg24": 1.5, "oi24": -3.2, "amp24": 18.0, "funding": -0.002, "rvol15": 2.3})
    row = next(r for r in state.radar_tracks_list("pending") if r["id"] == tid)
    for col, want in (("snap_chg24", 1.5), ("snap_oi24", -3.2), ("snap_amp24", 18.0),
                      ("snap_funding", -0.002), ("snap_rvol15", 2.3)):
        check(f"落库：{col} 写入并读回 {want}", row.get(col) is not None
              and abs(float(row[col]) - want) < 1e-9, str(row.get(col)))

    # 缺键写 NULL，不得写成 0（否则回放会把「没测到」当成「没动」）
    tid2 = state.radar_track_add("SNAP2USDT", "IGNITION", 1.0, 10, [], snap={"chg24": 2.0})
    row2 = next(r for r in state.radar_tracks_list("pending") if r["id"] == tid2)
    check("落库：未提供的键写 NULL（不是 0）", row2.get("snap_oi24") is None
          and row2.get("snap_funding") is None, str(row2.get("snap_oi24")))

    # 完全不传 snap（老调用方）必须照旧能登记
    tid3 = state.radar_track_add("SNAP3USDT", "ACCUMULATION", 1.0, 5, [])
    row3 = next(r for r in state.radar_tracks_list("pending") if r["id"] == tid3)
    check("落库：不传 snap 也能登记（向后兼容），5 列全 NULL",
          all(row3.get(c) is None for c in
              ("snap_chg24", "snap_oi24", "snap_amp24", "snap_funding", "snap_rvol15")))


def test_record_from_radar_writes_snapshot():
    """链路：record_from_radar 必须把雷达行的数值带进库。"""
    payload = {"ignition": [{
        "symbol": "LINKAUSDT", "stage": "IGNITION", "price": 2.0, "score": 30,
        "side": "LONG", "reasons": ["24h 振幅 20%"],
        "change24_pct": 1.2, "amp24": 20.0, "rvol15": 3.1, "oi_chg24": None,
        "factors": {"oi_chg24": 0.0, "funding": -0.0004, "amp24": 20.0, "rvol15": 3.1},
    }]}
    radar_tracker.record_from_radar(payload)
    row = next(r for r in state.radar_tracks_list("pending") if r["symbol"] == "LINKAUSDT")
    check("链路：chg24 落库", row.get("snap_chg24") == 1.2, str(row.get("snap_chg24")))
    check("链路：amp24 落库", row.get("snap_amp24") == 20.0, str(row.get("snap_amp24")))
    check("链路：funding 从 factors 取到", row.get("snap_funding") == -0.0004,
          str(row.get("snap_funding")))
    check("链路：rvol15 落库", row.get("snap_rvol15") == 3.1, str(row.get("snap_rvol15")))
    # 关键：顶层 oi_chg24 显式为 None（未测到）时，**不得**回退到 factors 里的 0.0
    check("链路：顶层 None 不回退 factors 的 0.0（否则把「没测到」伪装成「没动」）",
          row.get("snap_oi24") is None, str(row.get("snap_oi24")))


def test_pick_prefers_existing_key():
    """_pick 必须按「键是否存在」判断，不是「值是否为 None」。"""
    P = radar_tracker._pick
    check("_pick：键存在且为 None → 返回 None",
          P({"oi_chg24": None}, {"oi_chg24": 0.0}, "oi_chg24") is None)
    check("_pick：键存在 → 用顶层", P({"a": 1.0}, {"a": 9.0}, "a") == 1.0)
    check("_pick：键不存在 → 回退 factors", P({}, {"a": 9.0}, "a") == 9.0)
    check("_pick：两边都没有 → None", P({}, {}, "a") is None)


def test_schema_has_snapshot_columns():
    """建表 + 老库迁移两条路都要有这 5 列。"""
    src = _src("state.py")
    for col in ("snap_chg24", "snap_oi24", "snap_amp24", "snap_funding", "snap_rvol15"):
        check(f"建表：{col} 出现在 CREATE TABLE 与 ALTER 迁移里", src.count(col) >= 2,
              str(src.count(col)))
    check("迁移：沿用「缺列才 ALTER」模式（老库安全）",
          'if rtcols and _scol not in rtcols:' in src)
    check("插入：INSERT 语句含 5 个快照列",
          "outcome_price,snap_chg24,snap_oi24,snap_amp24,snap_funding,snap_rvol15," in src)


def test_state_decorator_not_displaced():
    """护栏：往 radar_track_add 前插函数时，极易把 `@_serialized` 挤到别的函数头上
    （v1.6.4 真踩过：_snap_val 抢走了装饰器、radar_track_add 变成无锁），断言锁死。"""
    src = _src("state.py")
    i = src.index("def radar_track_add(")
    head = src[max(0, i - 120):i]
    check("护栏：radar_track_add 仍带 @_serialized（DB 访问串行化）",
          "@_serialized" in head, head[-60:].replace("\n", "\\n"))
    j = src.index("def _snap_val(")
    head2 = src[max(0, j - 120):j]
    check("护栏：_snap_val 是纯函数、不带 @_serialized",
          "@_serialized" not in head2, head2[-60:].replace("\n", "\\n"))
    # INSERT 列数与占位符数必须配平（手工改过 SQL 后最容易错在这里）
    sql = src[src.index('"INSERT INTO radar_tracks'):]
    sql = sql[:sql.index("VALUES") + 200]
    cols = src[src.index("INSERT INTO radar_tracks ("):]
    cols = cols[:cols.index(") VALUES")]
    n_cols = len([c for c in cols[cols.index("(") + 1:].split(",") if c.strip()])
    vals = sql[sql.index("VALUES") + len("VALUES"):]
    vals = vals[:vals.index(")")]
    n_vals = len([v for v in vals.split(",") if v.strip()])
    check("护栏：INSERT 列数 = 占位符数", n_cols == n_vals and n_cols == 23,
          f"cols={n_cols} vals={n_vals}")


# ---------------- 3) OPT-01：取价三级兜底 ----------------

class _FakeWS:
    def __init__(self, spot=None, fut=None):
        self._s, self._f = spot, fut

    def price(self, scope, sym):
        return self._s if scope == "spot" else self._f


class _SilentWS:
    def price(self, scope, sym):        # 两个市场都没数据（WS 未覆盖）
        return None


def test_current_price_fallback():
    P = radar_tracker._current_price
    orig = radar_tracker.market_ws

    try:
        # ① 现货有 → 用现货（合约价必须被忽略，避免两个市场价差污染）
        radar_tracker.market_ws = _FakeWS(spot=1.5, fut=1.8)
        check("取价①：现货可用 → 走现货（不碰合约价）", P("XUSDT", {"XUSDT": 9.0}) == 1.5)

        # ② 纯合约币：现货为 None → 必须回退合约 WS
        radar_tracker.market_ws = _FakeWS(spot=None, fut=1.8)
        check("取价②：**纯合约币**（现货无）→ 回退合约 WS", P("XUSDT", {"XUSDT": 9.0}) == 1.8)

        # ③ WS 没覆盖 → 回退快照
        radar_tracker.market_ws = _SilentWS()
        check("取价③：WS 全无 → 回退快照", P("XUSDT", {"XUSDT": 9.0}) == 9.0)

        # ④ 三处都没有 → None（_tick 会跳过，与旧行为一致）
        radar_tracker.market_ws = _SilentWS()
        check("取价④：三处皆无 → None（不抛异常）", P("XUSDT", {}) is None)
    finally:
        radar_tracker.market_ws = orig


def test_tick_merges_futures_snapshot():
    """_tick 的快照必须合并合约口径，且**现货优先**（setdefault）。"""
    src = _src("radar_tracker.py")
    tick = src[src.index("def _tick("):src.index("def ensure_started(")]
    check("快照：_tick 合并 scanner.futures_snapshot()", "futures_snapshot()" in tick)
    check("快照：用具名 setdefault → **现货优先**，合约只补空缺",
          "snap.setdefault(" in tick, tick[:400])
    check("快照：合并包在 try 里（接口失败不影响主循环）",
          tick.index("futures_snapshot()") > tick.index("try:"))
    doc = radar_tracker.__doc__ or ""
    check("文档：模块 docstring 已写明三级兜底",
          "三级兜底" in doc and "futures" in doc)


# ---------------- 4) OPT-05：费率判据降权 ----------------

def test_neg_funding_penalty_downweighted():
    """「空头付钱」10 → 3：VTHOUSDT 费率 −0.776% 却是 +31.3% moon，判别力弱。"""
    d = {"price": 1.0, "quote_volume": 1e7, "spot_qv": 1e7, "fut_qv": 0.0, "oi_usd": 0.0,
         "chg24": 0.0, "funding": -0.002, "liq_5m": 0.0, "liq_n5m": 0}
    flags, note, pen = scanner._manip_flags(d)
    check("降权：负费率仍然命中「空头付钱」", "空头付钱" in flags, str(flags))
    check("降权：扣分 = 3.0（不再是 10.0）", abs(pen - 3.0) < 1e-9, str(pen))
    check("降权：风险提示语义保留（note 仍写明严禁做空）", "严禁做空" in note, note)
    src = _src()
    check("降权：源码不再出现该分支的 10.0", "penalty += 10.0" not in src)


def main():
    for fn in (test_oi_gate_replay, test_oi_gate_wiring,
               test_snap_val_semantics, test_snapshot_persisted,
               test_record_from_radar_writes_snapshot, test_pick_prefers_existing_key,
               test_schema_has_snapshot_columns, test_state_decorator_not_displaced,
               test_current_price_fallback, test_tick_merges_futures_snapshot,
               test_neg_funding_penalty_downweighted):
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

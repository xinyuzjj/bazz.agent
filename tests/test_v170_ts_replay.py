"""v1.6.6 测试 —— 档二（C6 本地时序库 / C5 回放框架 / #10 成本模型）回归护栏。

背景：`outputs/monster-radar-analysis.html` §17 把待研究问题分成四档，其中**档二**是
「需要写代码，但不需要新数据源」的四项：

  #8  C6 本地时序库     → 此前唯一的持久化是 radar_tracks（只记登记那一刻），
                         「某币被登记前 3 小时长什么样」在数据层无法回答
  #9  C5 回放框架       → 关键决策全靠作者**手工**回放（数字写在注释里），不可复现、
                         不可回归，且**全部是样本内**
  #10 `_sim_pnl` 成本模型 → 纯价格差 × 10x 杠杆，无滑点/手续费/资金费率 →
                         所有 PnL 结论**系统性偏乐观**
  #11 强平流接进入场    → **档三实测已推翻可行性**（fstream 不推 forceOrder，
                         REST 兜底 404/401）→ 本文件只钉「不要谎称已接通」

本文件钉住的核心不变量：
  ① **列顺序**：`_TS_COLS` 与建表顺序、与 INSERT 的元组顺序必须三方一致 ——
     顺序错位**不报错**，只会静默错位（C4 踩过的同一个坑），所以必须有**真实落库往返**。
  ② **「未测到」≠「测得为 0」**：快照/时序缺值一律 None；回放判据对 None **fail-open**
     但必须记进 `unknown`；结局价缺失的样本**不进** PnL 汇总（不是当 0 算）。
  ③ **毛值不被悄悄替换**：`_sim_pnl` 仍是毛值，成本**单列**；净值只做「并列新增」。
  ④ **样本外切分真实生效**：`replay()` 的 in/out 两组必须按 found_at 切开。

运行：.venv/Scripts/python.exe tests/test_v170_ts_replay.py
"""
import io
import os
import sys
import tempfile
import time

# 必须在 import 任何 src 模块之前：workspace.py import 时读该 env 决定库位置
os.environ["BAZZ_WORKSPACE"] = tempfile.mkdtemp(prefix="bazz_test_v170_")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
SRC = os.path.join(ROOT, "src")
FE = os.path.join(ROOT, "frontend", "src")
sys.path.insert(0, SRC)

import radar_replay as R      # noqa: E402
import radar_tracker          # noqa: E402
import scanner                # noqa: E402
import state                  # noqa: E402

FAILS = []


def check(name: str, cond: bool, extra: str = "") -> None:
    tag = "PASS" if cond else "FAIL"
    print(f"[{tag}] {name}" + (f"  ({extra})" if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def _src(name: str, base: str = None) -> str:
    return io.open(os.path.join(base or SRC, name), encoding="utf-8").read()


def _code_only(name: str, base: str = None) -> str:
    """剔掉注释，只留可执行代码（**别测注释**：修复说明会原样引用修复前的写法）。"""
    import tokenize
    with io.open(os.path.join(base or SRC, name), encoding="utf-8") as fh:
        toks = list(tokenize.generate_tokens(fh.readline))
    return "\n".join(t.string for t in toks if t.type != tokenize.COMMENT)


def _squash(s: str) -> str:
    """去掉全部空白。

    ⚠️ 必须的：`_code_only` 是 tokenize 重建出来的，**每个 token 之间都插了换行**，
    于是 `_ts_rows.append(` 会变成 `_ts_rows\\n.\\nappend\\n(` —— 直接搜子串必然漏。
    这个坑在 v1.6.6 踩过一次（`_code_only` 只用于「旧写法 not in 源码」这类断言时不会暴露，
    一旦拿它搜多 token 片段就现形）。
    """
    return "".join(s.split())


def _squashed_src(name: str, base: str = None) -> str:
    return _squash(_code_only(name, base))


def _mk_track(sym, chg24, oi24, outcome, entry=1.0, exit_=1.3, found_at=None,
              score=60, stage="IGNITION", direction="LONG", extra_snap=None):
    """登记并关单一条记录，返回 tid。"""
    snap = {"chg24": chg24, "oi24": oi24, "amp24": 25.0, "funding": 0.0003,
            "rvol15": 2.0, "taker": 1.4, "liq5m": 1e5, "basis": 5.0, "spread": 3.0,
            "liqside": "long"}
    if extra_snap:
        snap.update(extra_snap)
    tid = state.radar_track_add(sym, stage, entry, score, ["t"], found_at=found_at, snap=snap,
                               direction=direction)
    state.radar_track_close(tid, outcome, exit_)
    return tid


# ============================================================
# C6 ① 表结构：列顺序三方一致（静态）
# ============================================================
def test_ts_schema_shape():
    cols = [r[1] for r in state._conn_get().execute("PRAGMA table_info(ts_series)").fetchall()]
    check("ts_series 表已建", bool(cols), str(cols))
    check("列顺序与 _TS_COLS 完全一致（顺序错位不报错，只能靠这条钉住）",
          cols == list(state._TS_COLS), f"{cols} vs {list(state._TS_COLS)}")
    check("主键是 (symbol, ts) —— 保证一轮扫描每币一行且可幂等覆盖",
          "(symbol, ts)" in (state._conn_get().execute(
              "SELECT sql FROM sqlite_master WHERE name='ts_series'").fetchone()[0] or ""))
    check("ts 上有索引（清理与区间查询都靠它）",
          bool(state._conn_get().execute(
              "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_ts_series_ts'"
          ).fetchone()))


# ============================================================
# C6 ② 真实落库往返（唯一能发现「顺序错位」的护栏）
# ============================================================
def test_ts_roundtrip_all_columns():
    """写进去再读出来，**逐字段**比对。静态数个数查不出顺序错位 —— 见 test_v164 的教训。"""
    t0 = time.time() - 3600.0
    # v1.7.1：新增 `fut_spread_bps`（合约盘口价差）后，本 fixture 必须同步补上 ——
    # 这个护栏刻意按 `state._TS_COLS` **遍历**，所以新列会被自动纳入比对（设计如此）；
    # 代价是加列时 fixture 也要加值，否则 KeyError。这正是想要的耦合。
    row = {"symbol": "RTUSDT", "ts": t0, "price": 11.0, "chg24": 12.0, "oi_usd": 13.0,
           "oi_chg24": 14.0, "funding": 15.0, "mark_price": 16.0, "index_price": 17.0,
           "basis_bps": 18.0, "spread_bps": 19.0, "taker_ratio": 20.0, "liq_5m": 21.0,
           "liq_side": "long", "fut_qv": 22.0, "fut_spread_bps": 23.0}
    n = state.ts_append([row])
    check("ts_append 返回写入行数", n == 1, f"got={n}")
    got = state.ts_range("RTUSDT")
    check("ts_range 读回 1 行", len(got) == 1, f"got={len(got)}")
    if got:
        g = got[0]
        # 两个文本列（symbol / liq_side）按文本比，其余数值列按数值比 ——
        # 一行里混着文本列时**不能**统一 float()（那会把「列类型对不上」误报成转换异常）。
        bad = []
        for k in state._TS_COLS:
            if k in ("symbol", "liq_side"):
                if str(g.get(k) or "") != str(row[k]):
                    bad.append(f"{k}(text) {g.get(k)!r} != {row[k]!r}")
            elif float(g.get(k) or -1) != float(row[k]):
                bad.append(f"{k}(num) {g.get(k)!r} != {row[k]!r}")
        check(f"{len(state._TS_COLS) - 2} 个数值列 + 2 个文本列全部按列名对上（顺序错位会在这里现形）",
              not bad, f"错位/不符的列：{bad}")


# ============================================================
# C6 ③ 「未测到」写 NULL，不写 0
# ============================================================
def test_ts_none_not_zero():
    t0 = time.time() - 1800.0
    state.ts_append([{"symbol": "NONEUSDT", "ts": t0, "price": 1.0, "chg24": None,
                      "oi_usd": 0.0, "oi_chg24": "", "funding": "x", "mark_price": float("nan"),
                      "index_price": float("inf"), "basis_bps": None, "spread_bps": None,
                      "taker_ratio": None, "liq_5m": None, "liq_side": "", "fut_qv": None}])
    g = state.ts_range("NONEUSDT")[0]
    check("None / 空串 / 非数值 → NULL（不是 0）",
          g["chg24"] is None and g["oi_chg24"] is None and g["funding"] is None
          and g["mark_price"] is None and g["index_price"] is None
          and g["basis_bps"] is None and g["taker_ratio"] is None,
          str({k: g[k] for k in ("chg24", "oi_chg24", "funding", "mark_price",
                                 "index_price", "basis_bps", "taker_ratio")}))
    check("NaN / inf 不算读数，写 NULL",
          g["mark_price"] is None and g["index_price"] is None)
    check("**测得为 0 保留为 0**（0 与未测到是两件事）", float(g["oi_usd"]) == 0.0,
          f"oi_usd={g['oi_usd']}")
    check("空 liq_side 写 NULL 而非空串", g["liq_side"] is None, repr(g["liq_side"]))


# ============================================================
# C6 ④ 幂等 / 旁路 / 清理 / 体检
# ============================================================
def test_ts_idempotent_and_sidechannel():
    t0 = time.time() - 900.0
    before = state.ts_stats()["rows"]
    state.ts_append([{"symbol": "IDEMUSDT", "ts": t0, "price": 1.0}])
    state.ts_append([{"symbol": "IDEMUSDT", "ts": t0, "price": 2.0}])
    rows = state.ts_range("IDEMUSDT")
    check("同 (symbol, ts) 重复写 → REPLACE 覆盖，不增行", len(rows) == 1, f"got={len(rows)}")
    check("覆盖后取到的是最后一次的值", float(rows[0]["price"]) == 2.0, str(rows[0]["price"]))
    check("幂等写不改变总行数", state.ts_stats()["rows"] == before + 1)

    # 旁路：脏输入一律返回 0，绝不抛（时序库是旁路，不能让雷达整体报错）
    for bad in ([], None, [{}], [{"symbol": ""}], [{"symbol": "X", "ts": 0}],
                [{"symbol": "X", "ts": "abc"}], [{"symbol": "X", "ts": t0, "price": "zzz"}]):
        try:
            n = state.ts_append(bad)
            ok = n == 0 or bad == [{"symbol": "X", "ts": t0, "price": "zzz"}]
        except Exception as e:                     # noqa: BLE001
            ok = False
            n = f"raised {e!r}"
        check(f"脏输入不抛且返回 0：{str(bad)[:34]}", ok, str(n))

    # 清理：删旧留新
    old = time.time() - 40 * 86400
    state.ts_append([{"symbol": "OLDUSDT", "ts": old, "price": 1.0},
                     {"symbol": "NEWUSDT", "ts": time.time(), "price": 1.0}])
    state.ts_prune(keep_days=14.0)
    check("ts_prune 删掉超期行", state.ts_range("OLDUSDT") == [])
    check("ts_prune 不误删未超期行", len(state.ts_range("NEWUSDT")) == 1)

    st = state.ts_stats()
    check("ts_stats 结构完整",
          {"rows", "symbols", "oldest", "newest", "keep_days"} <= set(st), str(sorted(st)))
    check("ts_stats 的 rows 与 symbols 自洽", st["rows"] > 0 and st["symbols"] > 0)

    # 保留期：env 覆盖 + 非法值回退（不抛）
    os.environ["BAZZ_TS_KEEP_DAYS"] = "3"
    ok_env = state.ts_keep_days() == 3.0
    os.environ["BAZZ_TS_KEEP_DAYS"] = "abc"
    ok_bad = state.ts_keep_days() == state.TS_KEEP_DAYS
    os.environ["BAZZ_TS_KEEP_DAYS"] = "-1"
    ok_neg = state.ts_keep_days() == state.TS_KEEP_DAYS
    os.environ.pop("BAZZ_TS_KEEP_DAYS", None)
    check("保留期 env 可覆盖", ok_env)
    check("保留期非法值回退默认（不抛）", ok_bad and ok_neg)


def test_ts_range_window_and_order():
    base = time.time() - 7200.0
    state.ts_append([{"symbol": "WINUSDT", "ts": base + i * 60, "price": float(i)}
                     for i in (2, 0, 1)])          # 故意乱序写入
    rows = state.ts_range("WINUSDT")
    check("ts_range 按 ts **升序**返回（回放要按时间读）",
          [int(r["price"]) for r in rows] == [0, 1, 2], str([r["price"] for r in rows]))
    mid = state.ts_range("WINUSDT", since=base + 60, until=base + 60)
    check("since/until 是闭区间且真的生效", len(mid) == 1 and int(mid[0]["price"]) == 1)
    check("limit 生效", len(state.ts_range("WINUSDT", limit=2)) == 2)
    check("未知 symbol 返回空表（不抛）", state.ts_range("NOPEUSDT") == [])
    syms = state.ts_symbols(limit=3)
    check("ts_symbols 返回 symbol/最新 ts/条数", bool(syms) and {"symbol", "last_ts", "n"} <= set(syms[0]),
          str(syms[:1]))


# ============================================================
# C6 ⑤ 扫描接线：真的把行交出去，且是旁路
# ============================================================
def test_ts_wired_into_scan():
    code = _squashed_src("scanner.py")
    check("扫描循环里在收集时序行（_ts_rows.append）", "_ts_rows.append(" in code)
    check("循环后一次事务写入（_ts_persist(_ts_rows)）", "_ts_persist(_ts_rows)" in code)
    check("payload 下发 ts_store", '"ts_store":ts_store_view()' in code)
    check("落盘在 state 上走惰性 import（不连坐 scanner 的导入）",
          "importstate" in code.split("def_ts_persist")[1][:200])
    check("ts_store_view 有 TTL 缓存（COUNT(*) 不能每次 payload 都算）",
          "_TS_STATS_TTL" in code and "_TS_STATS_CACHE" in code)

    # 行为：脏输入不抛、空输入返回 0
    check("_ts_persist([]) 返回 0 且不抛", scanner._ts_persist([]) == 0)
    check("_ts_persist(脏行) 不抛（旁路）", scanner._ts_persist([{"symbol": ""}, None]) == 0)
    v = scanner.ts_store_view(force=True)
    check("ts_store_view 结构完整",
          {"rows", "symbols", "oldest", "newest", "keep_days", "last_written", "last_ts"} <= set(v),
          str(sorted(v)))
    check("last_written 记录最近一次真正写入的行数",
          scanner._ts_persist([{"symbol": "LASTUSDT", "ts": time.time(), "price": 1.0}]) == 1
          and scanner.ts_store_view(force=True)["last_written"] == 1)


# ============================================================
# C5 ① 样本拍平：snap 子字典与平铺键两条路径同源
# ============================================================
def test_sample_of_two_paths():
    row = {"id": "x", "symbol": "S", "stage": "IGNITION", "direction": "LONG",
           "status": "closed", "outcome": "moon", "found_score": 61,
           "found_price": 1.0, "outcome_price": 1.3, "found_at": 1.0, "closed_at": 2.0,
           "snap": {"chg24": 3.5, "oi24": 1.5}}
    s = R.sample_of(row)
    check("从 snap 子字典取到 chg24/oi24", s["chg24"] == 3.5 and s["oi24"] == 1.5)
    check("score 拍平成 float", s["score"] == 61.0)
    check("has_snap 为真", s["has_snap"] is True)
    # 只有平铺键（老消费方口径）时也必须读到同一批数据
    row2 = {k: v for k, v in row.items() if k != "snap"}
    row2["snap_chg24"], row2["snap_oi24"] = 3.5, 1.5
    s2 = R.sample_of(row2)
    check("只有平铺 snap_* 键时取值一致（两条路径同源）",
          s2["chg24"] == s["chg24"] and s2["oi24"] == s["oi24"])
    check("无快照时 has_snap 为假", R.sample_of({"symbol": "S"})["has_snap"] is False)


# ============================================================
# C5 ② 「未测到」fail-open 但必须可见
# ============================================================
def test_apply_rules_unmeasured_fail_open():
    samples = [{"stage": "IGNITION", "has_snap": True, "chg24": None, "oi24": 9.0},
               {"stage": "IGNITION", "has_snap": True, "chg24": 1.0, "oi24": 9.0},
               {"stage": "IGNITION", "has_snap": False, "chg24": None, "oi24": None}]
    r = R.apply_rules(samples, {"max_chg24": 3.0})
    check("未测到的轴 **fail-open**（不因缺数据被误杀）", len(r["kept"]) == 3, str(len(r["kept"])))
    check("未测到的轴被记进 unknown（可见）", r["unknown"].get("chg24") == 2, str(r["unknown"]))
    r2 = R.apply_rules(samples, {"max_chg24": 3.0, "require_snap": True})
    check("require_snap 整条剔除无快照样本", len(r2["kept"]) == 2, str(len(r2["kept"])))
    r3 = R.apply_rules(samples, {"max_oi24": 5.0})
    check("测得的值真的参与判定（9.0 > 5.0 被挡；未测到那条 fail-open 留下）",
          len(r3["kept"]) == 1 and r3["kept"][0]["chg24"] is None, str(len(r3["kept"])))
    r4 = R.apply_rules(samples, {"min_chg24": 0.5})
    check("min 方向判据生效（1.0 ≥ 0.5 通过，未测到仍 fail-open）", len(r4["kept"]) == 3)
    r5 = R.apply_rules(samples, {"exclude_stages": ["IGNITION"]})
    check("exclude_stages 生效", len(r5["kept"]) == 0)
    r6 = R.apply_rules(samples, {"nope_key": 1})
    check("未知规则键被报出来而不是静默忽略", r6["bad_rules"] == ["nope_key"], str(r6["bad_rules"]))
    check("规则表覆盖 21 个数值轴", len(R.RULES) == 21, str(len(R.RULES)))


# ============================================================
# C5 ③ PnL：结局价缺失的样本**不进**汇总（不是当 0 算）
# ============================================================
def test_pnl_unknown_excluded():
    s_ok = {"direction": "LONG", "found_price": 1.0, "outcome_price": 1.3,
            "found_at": 0.0, "closed_at": 0.0}
    s_no = {"direction": "LONG", "found_price": 1.0, "outcome_price": 0.0,
            "found_at": 0.0, "closed_at": 0.0}
    check("有结局价 → known=True", R._pnl_of(s_ok, True)[2] is True)
    check("结局价为 0（老样本没写）→ known=False", R._pnl_of(s_no, True)[2] is False)
    sm = R.summarize([dict(s_ok, outcome="moon"), dict(s_no, outcome="dump")], True)
    check("缺失结局价的样本被单独计数（n_pnl_unknown）", sm["n_pnl_unknown"] == 1, str(sm))
    check("缺失样本不计入 PnL（否则等于把「没数据」当「亏 0」）",
          sm["pnl_usdt"] > 0, str(sm["pnl_usdt"]))
    check("胜率分母是已关单样本数", sm["closed"] == 2 and sm["win_rate"] == 50.0, str(sm))


# ============================================================
# C5 ④ 样本外切分真实生效（按 found_at）
# ============================================================
def test_replay_out_of_sample_split():
    base = time.time() - 400 * 3600
    # 10 笔：偶数序「未启动」(chg24=1) 走 moon，奇数序「已涨」(chg24=20) 走 dump
    for i in range(10):
        late = i % 2 == 1
        _mk_track("SP%02dUSDT" % i, 20.0 if late else 1.0, 6.0 if late else 1.0,
                  "dump" if late else "moon",
                  entry=1.0, exit_=0.88 if late else 1.3,
                  found_at=base + i * 3600)
    r = R.replay({"max_chg24": 3.0}, direction="LONG", split=0.7)
    check("n_all 为全部已关单 LONG 样本", r["n_all"] == 10, str(r["n_all"]))
    check("规则把「已涨」的 5 笔挡掉", r["n_kept"] == 5 and r["n_dropped"] == 5,
          f"kept={r['n_kept']} dropped={r['n_dropped']}")
    check("in_sample = 前 70%（3 笔）", r["in_sample"]["n"] == 3, str(r["in_sample"]["n"]))
    check("out_sample = 后 30%（2 笔）", r["out_sample"]["n"] == 2, str(r["out_sample"]["n"]))
    check("切分是按时间切的（in 全是早期样本）", r["in_sample"]["moon"] == 3
          and r["out_sample"]["moon"] == 2, f"{r['in_sample']} / {r['out_sample']}")
    check("两组都给出完整指标（胜率/PnL/存活数）",
          {"n", "moon", "dump", "expired", "win_rate", "pnl_usdt"} <= set(r["out_sample"]))
    check("样本不足时给显式告警（不许据此改规则）",
          any("噪声" in w for w in r["warnings"]), str(r["warnings"]))
    check("未知规则键会被写进 warnings",
          any("未知规则键" in w for w in R.replay({"bogus": 1})["warnings"]))
    check("只算不改：结果里明示不改规则", "不改" in r["note"])


def test_replay_does_not_touch_db():
    before = state.radar_tracks_count("closed")
    R.replay({"max_chg24": 3.0})
    R.sweep("max_chg24", [1.0, 3.0])
    check("回放不写库（只算不改）", state.radar_tracks_count("closed") == before)


def test_sweep_ranks_by_out_sample():
    base = time.time() - 400 * 3600
    for i in range(12):
        late = i % 2 == 1
        _mk_track("SW%02dUSDT" % i, 20.0 if late else 1.0, 6.0 if late else 1.0,
                  "dump" if late else "moon",
                  entry=1.0, exit_=0.88 if late else 1.3,
                  found_at=base + i * 3600)
    sw = R.sweep("max_chg24", [3.0, 999.0], direction="LONG")
    check("sweep 每个档位一行", len(sw["rows"]) == 2, str(len(sw["rows"])))
    check("sweep 同时给出样本内与样本外两组指标",
          all({"in_pnl", "out_pnl", "in_win", "out_win"} <= set(x) for x in sw["rows"]))
    check("ranked 按**样本外** PnL 排序（样本内排序没有信息量）",
          sw["sorted_by"] == "out_pnl"
          and sw["ranked"][0]["out_pnl"] >= sw["ranked"][-1]["out_pnl"], str(sw["ranked"]))
    check("sweep 的 note 说明看的是「内外同时变好」",
          "同时" in sw["note"], sw["note"])


def test_rule_parsing_cli():
    check("key=value 解析为数值", R._parse_rule("max_chg24=3.5") == ("max_chg24", 3.5))
    check("布尔键解析为 True", R._parse_rule("require_snap=true") == ("require_snap", True))
    check("布尔键 off 解析为 False", R._parse_rule("require_snap=0") == ("require_snap", False))
    check("列表键按逗号切分", R._parse_rule("stages=IGNITION,VERTICAL")
          == ("stages", ["IGNITION", "VERTICAL"]))
    check("main() 可直接跑通（离线、无网络）", R.main(["--rule", "max_chg24=3", "--gross"]) == 0)


# ============================================================
# 档二 #10 成本模型
# ============================================================
def test_cost_model_numbers():
    check("sim_cost(0h, 0 费率) = 双边手续费 1.0 + 双边滑点 3.0 = 4.0",
          radar_tracker.sim_cost(0.0, 0.0) == 4.0, str(radar_tracker.sim_cost(0.0, 0.0)))
    check("资金费率按 8h 周期累计：1000 × 0.01% × 1 = 0.1 → 4.0 + 0.1 = 4.1",
          radar_tracker.sim_cost(8.0, 0.0001) == 4.1, str(radar_tracker.sim_cost(8.0, 0.0001)))
    check("持有 4h 只算半个周期（0.05）", radar_tracker.sim_cost(4.0, 0.0001) == 4.05,
          str(radar_tracker.sim_cost(4.0, 0.0001)))
    check("成本随持有时间单调增（费率 > 0 时）",
          radar_tracker.sim_cost(24.0, 0.0001) > radar_tracker.sim_cost(8.0, 0.0001))
    check("负费率（空头付钱）时成本下降",
          radar_tracker.sim_cost(8.0, -0.0001) == 3.9, str(radar_tracker.sim_cost(8.0, -0.0001)))


def test_gross_unchanged_and_net_added():
    """**最关键的一条**：加成本模型不许改动毛值。"""
    check("_sim_pnl 毛值不变（LONG +30%）", radar_tracker._sim_pnl("LONG", 1.0, 1.3) == (300.0, 300.0),
          str(radar_tracker._sim_pnl("LONG", 1.0, 1.3)))
    check("_sim_pnl 毛值不变（LONG -50% → 爆仓封底 -100）",
          radar_tracker._sim_pnl("LONG", 1.0, 0.5) == (-100.0, -500.0),
          str(radar_tracker._sim_pnl("LONG", 1.0, 0.5)))
    check("_sim_pnl 仍只吃 3 个参数（签名向后兼容）",
          radar_tracker._sim_pnl.__code__.co_argcount == 3,
          str(radar_tracker._sim_pnl.__code__.co_varnames))
    net, roi_net, cost = radar_tracker.sim_pnl_net("LONG", 1.0, 1.3)
    check("净值 = 毛值 − 成本", net == 296.0 and cost == 4.0, f"net={net} cost={cost}")
    check("净值 ROI 相对本金（100U）", roi_net == 296.0, str(roi_net))
    net2, _, _ = radar_tracker.sim_pnl_net("LONG", 1.0, 0.5)
    check("爆仓封底作用在**净值**上（亏完本金不再倒扣手续费）", net2 == -100.0, str(net2))
    check("缺价时净值/成本都为 0（不猜）",
          radar_tracker.sim_pnl_net("LONG", 0, 1.0) == (0.0, 0.0, 0.0))


def test_cost_wired_into_events_and_review():
    code = _squashed_src("radar_tracker.py")
    check("_tick 两处事件都下发净值（hold + 关单）",
          code.count("pnl_net_usdt=net") == 2, str(code.count("pnl_net_usdt=net")))
    check("事件同时保留毛值（pnl_usdt=pnl 未被替换）",
          code.count("pnl_usdt=pnl") == 2, str(code.count("pnl_usdt=pnl")))
    check("复盘 Markdown 并列写毛值与净值", "含成本估算" in code)
    check("_hold_hours 用登记时间算（缺失 → 0，不猜）",
          "def_hold_hours" in code and "found_at" in code)
    check("_last_funding 零外呼取雷达缓存", "def_last_funding" in code
          and "_last_scan_row" in code)


# ============================================================
# 档二 #11：强平流入场 —— 档三实测已推翻可行性，只钉「不谎称接通」
# ============================================================
def test_liq_stream_not_claimed():
    code = _squashed_src("radar_tracker.py")
    check("`_reversal_now` 仍只把强平用于**离场**（未被偷偷接进入场）",
          "def_reversal_now" in code)
    doc = _src("radar_tracker.py")
    check("代码里写明强平流实测不可用（fstream 不推 + REST 404/401）",
          "forceOrder" in doc or "强平" in doc)


# ============================================================
# 前端接线
# ============================================================
def test_frontend_wiring():
    mr = _src("components/MarketRows.tsx", FE)
    mv = _src("views/MarketsView.tsx", FE)
    lc = _src("i18n/locales.ts", FE)
    mock = _src("preview/mock.ts", FE)
    toasts = _src("components/Toasts.tsx", FE)

    check("RadarTsStore 类型已定义",
          "export type RadarTsStore" in mr and "last_written" in mr)
    check("fmtCount 不复用 fmtVol（避免把行数印成美元）",
          "export const fmtCount" in mr and "fmtVol" not in mr.split("export const fmtCount")[1][:260])
    check("MarketsView 读取 ts_store 并置状态",
          "ts_store" in mv and "setRadarTs" in mv)
    check("库为空时显式提示（而不是静默无痕）",
          "markets.tsEmpty" in mv and "pill-red" in mv)
    for k in ("markets.tsStore", "markets.tsStoreTip", "markets.tsEmpty", "markets.tsEmptyTip"):
        check(f"i18n 键 {k} zh/en 成对", lc.count(f'"{k}"') == 2, str(lc.count(f'"{k}"')))
    check("mock 提供 ts_store（隔离预览也能看到）", "ts_store" in mock)
    check("Toasts 读 pnl_net_usdt 但**不替换** pnl_usdt",
          "pnl_net_usdt" in toasts and "pnl_usdt" in toasts)
    # ⚠️ 上面那条只查「两个字段名都在文件里」，**太弱**：把毛值换成净值也照样通过
    # （变异验证 #11 实测没红）。下面两条钉住「毛值仍是主显示」这件事本身。
    tsq = _squash(toasts)
    check("毛值在两处告警里都仍是主显示（净值只做并列新增，不替换）",
          tsq.count("${pnlU(e.pnl_usdt)}${netSuffix}") == 2,
          f"出现 {tsq.count('${pnlU(e.pnl_usdt)}${netSuffix}')} 次（应为 2：hold + 关单）")
    check("净值带差值判断（与毛值相同则不显示，避免噪音）",
          "Math.abs(" in tsq and "pnl_net_usdt" in tsq)
    check("净值的 i18n 键 zh/en 成对", lc.count('"alert.netPnl"') == 2,
          str(lc.count('"alert.netPnl"')))


# ============================================================
def main():
    print("=" * 60)
    print("v1.6.6 档二回归护栏（C6 时序库 / C5 回放 / #10 成本模型）")
    print("=" * 60)
    groups = [
        ("C6 ① 表结构", [test_ts_schema_shape]),
        ("C6 ② 真实落库往返", [test_ts_roundtrip_all_columns]),
        ("C6 ③ 未测到写 NULL", [test_ts_none_not_zero]),
        ("C6 ④ 幂等 / 旁路 / 清理 / 体检", [test_ts_idempotent_and_sidechannel,
                                    test_ts_range_window_and_order]),
        ("C6 ⑤ 扫描接线", [test_ts_wired_into_scan]),
        ("C5 ① 样本拍平", [test_sample_of_two_paths]),
        ("C5 ② 未测到 fail-open", [test_apply_rules_unmeasured_fail_open]),
        ("C5 ③ PnL 缺失不计入", [test_pnl_unknown_excluded]),
        ("C5 ④ 样本外切分", [test_replay_out_of_sample_split, test_replay_does_not_touch_db,
                          test_sweep_ranks_by_out_sample, test_rule_parsing_cli]),
        ("#10 成本模型", [test_cost_model_numbers, test_gross_unchanged_and_net_added,
                       test_cost_wired_into_events_and_review]),
        ("#11 强平流不谎称接通", [test_liq_stream_not_claimed]),
        ("前端接线", [test_frontend_wiring]),
    ]
    for title, fns in groups:
        print(f"\n—— {title} ——")
        for fn in fns:
            try:
                fn()
            except Exception as e:                 # noqa: BLE001
                import traceback
                check(f"{fn.__name__} 未抛异常", False, f"{e!r}")
                traceback.print_exc()
    print("\n" + "=" * 60)
    if FAILS:
        print(f"{len(FAILS)} 项失败：")
        for f in FAILS:
            print(f"  - {f}")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""v1.6.6 测试 —— 妖币雷达「问题清单」逐项落地的回归护栏（前后端契约 + 语义层行为）。

背景：`outputs/monster-radar-analysis.html` 是一份只读分析报告，列出 13 项问题
（P0 三项 / P1 五项 / P2 五项）+ 6 项结构缺口（C1~C6）+ 免费数据能力清单。
本文件把其中**已落地**的部分钉成机器可验的断言，避免「改回去了也没人知道」。

覆盖（按问题编号）：
  #1  日报 24h 涨幅字段名错位      → scheduler 按引擎取数，缺失写 n/a 而非 +0.00%
  #2  前端 taker 阈值硬编码 1.5    → 改读 payload 下发的 thresholds（单一来源）
  #4  DORMANT 判据 `or 1.0` 吞 0   → 「测得为 0」必须真进沉寂；「没测到」不参与
  #5  10~12% 标签断层              → 只改展示层（stage 不动），前端按 late 渲染
  #6  确认层 24 币封顶致漏报       → 前端显式标「控盘未测」
  #8  v2 静默降级 v1               → 消费方检查 engine 并加降级提示
  #9  无扫描级锁                   → _radar2_lock + 等锁后双重检查
  #10 _daily2_bars 缓存键不含参数  → 键纳入 (top_n, min_qv)
  #12 历史表 50 条 vs 全量统计     → tracks_view 下发两个数，前端注明口径
  #13 pending 无上限               → radar_tracks_list(limit=0) 不拼 LIMIT
  C1  无日线币被静默豁免过滤       → 改用 exchangeInfo onboardDate + 计数
  C2  合约 ticker 的 count 未取用  → futures_snapshot / _radar_pool 带 count
  C4  登记快照只有 5 列            → 14 列（往返护栏见 test_v164）
  D1  爆仓流恒 0 无法区分未测      → liq_available 贯穿到前端

运行：.venv/Scripts/python.exe tests/test_v169_radar_remediation.py
"""
import io
import os
import sys
import tempfile

# 必须在 import 任何 src 模块之前：workspace.py import 时读该 env 决定库位置
os.environ["BAZZ_WORKSPACE"] = tempfile.mkdtemp(prefix="bazz_test_v169_")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
SRC = os.path.join(ROOT, "src")
FE = os.path.join(ROOT, "frontend", "src")
sys.path.insert(0, SRC)

import market_ws      # noqa: E402
import radar_tracker  # noqa: E402
import scanner        # noqa: E402
import state          # noqa: E402

FAILS = []


def check(name: str, cond: bool, extra: str = "") -> None:
    tag = "PASS" if cond else "FAIL"
    print(f"[{tag}] {name}" + (f"  ({extra})" if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def _src(name: str) -> str:
    return io.open(os.path.join(SRC, name), encoding="utf-8").read()


def _code_only(name: str) -> str:
    """去掉注释与字符串外的干扰，只留可执行代码文本。

    为什么需要：修复说明**就写在代码旁边**（注释里会原样引用「修复前的写法」），
    于是 `旧写法 not in src` 这类断言会被自己的注释绊倒 —— 那是在测注释、不是测代码。
    这里用 tokenize 剔除 COMMENT，保留字符串字面量（有些断言确实要查字面量）。
    """
    import tokenize
    with io.open(os.path.join(SRC, name), encoding="utf-8") as fh:
        toks = list(tokenize.generate_tokens(fh.readline))
    out = []
    for t in toks:
        if t.type == tokenize.COMMENT:
            continue
        out.append(t.string)
    return "".join(out)


def _fe(rel: str) -> str:
    return io.open(os.path.join(FE, rel), encoding="utf-8").read()


# ============================================================
# 一组：#4 语义层「未测到」与「真的是 0」必须分开
# ============================================================
# 修复前写作 `(d.get("rvol_d", 1.0) or 1.0) <= 0.6`：
#   `_daily_feats._vr()` 在基准均量为 0 时返回 0.0，而 `0.0 or 1.0` == 1.0
#   → 1.0 <= 0.6 恒 False → **真·零成交量这个最该判沉寂的情形永远进不了沉寂**。

def _dormant_input(**over):
    """构造一个「不会先命中其它档」的最小输入，再按需覆盖字段。"""
    d = {"chg24": 0.0, "chg1h": 0.0, "chg3d": 0.0, "chg30d": 0.0, "pos": 1.0,
         "oi_chg24": 0.0, "oi_chg48": 0.0, "oi_pulse15": 0.0, "funding": 0.0,
         "taker_ratio": 0.0, "taker_drop": False, "top_ratio": 0.0,
         "liq_5m": 0.0, "liq_side": "", "amp24": 2.0, "rvol_d": 0.0}
    d.update(over)
    return d


def test_dormant_zero_rvol():
    """rvol_d == 0.0（量能枯竭的极端）必须进 DORMANT。"""
    st, label, _tag, side, _note = scanner._stage_of_raw(_dormant_input(rvol_d=0.0))
    check("DORMANT：rvol_d=0.0（真·零成交量）→ 判沉寂", st == "DORMANT",
          f"stage={st!r} label={label!r}")
    check("DORMANT：side 为 WATCH（沉寂只观察、不下注）", side == "WATCH", str(side))
    # 边界：0.6 是闭区间上界
    st2 = scanner._stage_of_raw(_dormant_input(rvol_d=0.6))[0]
    check("DORMANT：rvol_d=0.6 边界仍判沉寂", st2 == "DORMANT", str(st2))
    st3 = scanner._stage_of_raw(_dormant_input(rvol_d=0.61))[0]
    check("DORMANT：rvol_d=0.61 越界不判沉寂", st3 != "DORMANT", str(st3))


def test_dormant_missing_rvol_fail_open():
    """rvol_d 缺失（无日线）→ 不参与判定（fail-open），不得把「没测到」当枯竭。"""
    d = _dormant_input()
    d.pop("rvol_d")
    st = scanner._stage_of_raw(d)[0]
    check("DORMANT：rvol_d 缺失（未测到）→ 不判沉寂（fail-open）", st != "DORMANT", str(st))
    st2 = scanner._stage_of_raw(_dormant_input(rvol_d=None))[0]
    check("DORMANT：rvol_d=None（未测到）→ 不判沉寂", st2 != "DORMANT", str(st2))
    # 源码锚点：不得再出现 `or 1.0` 形式的双吞值（**只看可执行代码** ——
    # 修复说明的注释里会原样引用修复前的写法，否则这条会被自己的注释绊倒）
    code = _code_only("scanner.py")
    compact = "".join(code.split())          # tokenize 拼接后空格不可靠 → 去空白比对
    check("DORMANT：可执行代码里不再有 `rvol_d`, 1.0) or 1.0` 的双吞值写法",
          '(d.get("rvol_d",1.0)or1.0)' not in compact)
    check("DORMANT：改为显式判空 rv_d is not None",
          'rv_d=d.get("rvol_d")' in compact and "rv_disnotNoneandrv_d<=0.6" in compact)


# ============================================================
# 二组：#5 10~12% 标签断层 —— 只改展示层，状态机键不动
# ============================================================

def test_late_display_only():
    """展示层对齐循环必须只写 late/tag/stage_label/side/note，**不得**写 stage。"""
    src = _src("scanner.py")
    i = src.index("# 病根是「阶段标签断层」")
    seg = src[i:i + 1400]
    check("#5：展示层改写循环存在", 'r["late"] = True' in seg)
    check("#5：改写了展示用 tag", '"tag"] = "已启动 · 追高区"' in seg)
    check("#5：改写后 side 降级为 WATCH", 'r["side"] = "WATCH"' in seg)
    check("#5：**没有**改写 stage（状态机键必须保持原判）",
          'r["stage"] =' not in seg, "展示层动了 stage")
    # 只作用于 ACCUMULATION / IGNITION —— 不能把 EXTENDED/VERTICAL 再包一层
    check("#5：仅作用于 ACCUMULATION / IGNITION",
          'r["stage"] in ("ACCUMULATION", "IGNITION") and _late(r)' in seg)
    # 阈值分裂必须已被记录：_late 用 10.0，EXTENDED 用 _TRIG_MAX_CHG24
    check("#5：_late 用 _TRIG_LATE_CHG24（10%）", ">= _TRIG_LATE_CHG24" in src)
    check("#5：EXTENDED 用 _TRIG_MAX_CHG24", "if chg24 > _TRIG_MAX_CHG24:" in src)


def test_late_frontend_and_i18n():
    """前端按 late 单独渲染金标（不能沿用 stage 的绿色 pill）。"""
    rows = _fe("components/MarketRows.tsx")
    check("#5：前端 RadarRow 类型有 late 字段", "late?: boolean;" in rows)
    check("#5：前端有 m.late 独立分支", "m.late ? (" in rows)
    check("#5：late 分支用金标（pill-gold）",
          "pill pill-gold" in rows[rows.index("m.late ? ("):rows.index("m.late ? (") + 200])
    check("#5：late 文案走 i18n（后端 stage_label 是中文字面量，不能直接印）",
          't("markets.stageStarted")' in rows)


# ============================================================
# 三组：#2 taker 阈值单一来源下发
# ============================================================

def test_thresholds_single_source():
    src = _src("scanner.py")
    check("#2：payload 下发 thresholds", '"thresholds": {' in src)
    for k in ("taker_buy_dominant", "trig_late_chg24", "trig_max_oi24", "new_coin_days",
              "manip_churn_max", "manip_spot_share_min", "trig_max_chg24"):
        check(f"#2：thresholds 含 {k}", f'"{k}":' in src)
    check("#2：taker_buy_dominant 取自模块常量（不是字面量 1.5）",
          '"taker_buy_dominant": _TAKER_BUY_DOMINANT,' in src)


def test_frontend_taker_uses_payload():
    rows = _fe("components/MarketRows.tsx")
    check("#2：前端不再硬编码 1.5", "taker_ratio >= 1.5" not in rows,
          "仍有 taker_ratio >= 1.5")
    check("#2：前端读 th?.taker_buy_dominant",
          "th?.taker_buy_dominant" in rows)
    check("#2：RadarThresholds 类型已导出", "export type RadarThresholds" in rows)
    # 兜底常量必须与后端常量同值，否则旧后端/预览下又漂
    import re
    m = re.search(r"FALLBACK_TAKER_BUY_DOMINANT\s*=\s*([0-9.]+)", rows)
    check("#2：兜底常量存在", bool(m))
    if m:
        check("#2：兜底常量 == 后端 _TAKER_BUY_DOMINANT",
              abs(float(m.group(1)) - scanner._TAKER_BUY_DOMINANT) < 1e-9,
              f"前端 {m.group(1)} vs 后端 {scanner._TAKER_BUY_DOMINANT}")
    view = _fe("views/MarketsView.tsx")
    check("#2：MarketsView 保存并透传 thresholds",
          "setRadarTh(" in view and "th={radarTh}" in view)


# ============================================================
# 四组：#6 控盘未测 + D1 爆仓流可用性
# ============================================================

def test_manip_unmeasured_frontend():
    rows = _fe("components/MarketRows.tsx")
    check("#6：RadarRow 带 oi_usd / fut_qv", "oi_usd?: number" in rows and "fut_qv?: number" in rows)
    check("#6：判定条件 = 无指纹 且 oi_usd==0 且 fut_qv>0",
          "!m.manip?.length && (m.oi_usd ?? 0) === 0 && (m.fut_qv ?? 0) > 0" in rows)
    check("#6：渲染「控盘未测」pill", 't("markets.manipUnmeasured")' in rows)
    # 后端必须真的把这两个字段出行，否则前端条件恒不成立
    src = _src("scanner.py")
    check("#6：后端雷达行含 fut_qv", '"fut_qv": round(' in src)
    check("#6：后端雷达行含 oi_usd", '"oi_usd": round(' in src)


def test_liq_available_semantics():
    """liq_available 的判据必须是「收到过帧」，而不是「连接建立」。"""
    src = _src("market_ws.py")
    i = src.index("def liq_available(")
    seg = src[i:src.index("def liq_recent(", i)]
    check("D1：liq_available 判据读 msgs（收到过帧，不是连接建立）",
          '"msgs"' in seg, seg[:160].replace("\n", "\\n"))
    check("D1：liq_stats 带 available 字段", '"available"' in src)
    # 行为：隔离地翻转 msgs，验证 available 跟着变
    st = market_ws._stats["liq"]
    orig = dict(st)
    try:
        st["msgs"] = 0
        check("D1：msgs=0 → liq_available() is False", market_ws.liq_available() is False)
        check("D1：msgs=0 → liq_symbol_stats 的 quote 为 None（不得写 0）",
              market_ws.liq_symbol_stats("BTCUSDT").get("quote") is None)
        st["msgs"] = 7
        check("D1：msgs>0 → liq_available() is True", market_ws.liq_available() is True)
    finally:
        st.clear()
        st.update(orig)


def test_confirm_layer_liq_not_faked_as_zero():
    src = _src("scanner.py")
    check("D1：_confirm_factors 默认 liq_5m 为 None（未测到）",
          '"liq_5m": None' in src)
    check("D1：_confirm_factors 默认 liq_available 为 False",
          '"liq_available": False' in src)
    check("D1：只有 available 时才写爆仓值",
          'if ls.get("available"):' in src)
    check("D1：行顶层带 liq_available",
          '"liq_available": bool(cf.get("liq_available")) if cf else None' in src)
    check("D1：payload 顶层带 liq_available",
          '"liq_available": _liq_source_available(),' in src)
    check("D1：_liq_source_available 包一层且异常返回 False",
          "def _liq_source_available()" in src and "market_ws.liq_available()" in src)


# ============================================================
# 五组：#9 扫描级锁 / #10 缓存键
# ============================================================

def test_scan_lock():
    src = _src("scanner.py")
    check("#9：有扫描级锁常量", "RADAR2_LOCK_WAIT" in src)
    check("#9：锁对象存在", "_radar2_lock = threading.Lock()" in src)
    check("#9：get_radar_v2 拿锁并设超时", "_radar2_lock.acquire(timeout=RADAR2_LOCK_WAIT)" in src)
    check("#9：拿不到锁且无缓存时抛错（不无限阻塞）",
          'raise RuntimeError("radar v2 scan busy")' in src)
    check("#9：锁内双重检查缓存", "双重检查：等锁期间可能已有别的线程跑完并写好缓存" in src)
    check("#9：扫描主体抽成 _radar_v2_scan", "def _radar_v2_scan(" in src)
    check("#9：finally 里释放锁", "finally:\n        _radar2_lock.release()" in src)


def test_daily2_cache_key():
    src = _src("scanner.py")
    check("#10：缓存键纳入 (top_n, min_qv)", 'key = (int(top_n), float(min_qv))' in src)
    check("#10：命中时比较键", '_daily2_cache.get("key") == key' in src)
    check("#10：写缓存时记录键", '_daily2_cache["key"] = key' in src)
    check("#10：_daily2_cache 初始含 key", '"key": None' in src)


# ============================================================
# 六组：#12 / #13 战绩口径
# ============================================================

def test_tracks_view_reports_scope():
    view = radar_tracker.tracks_view()
    for k in ("pending_total", "history_total", "history_shown", "history_shown_limit"):
        check(f"#12：tracks_view 下发 {k}", k in view, f"keys={sorted(view)}")
    check("#12：history 实际条数 <= 展示上限",
          len(view.get("history") or []) <= (view.get("history_shown_limit") or 0),
          f"{len(view.get('history') or [])} vs {view.get('history_shown_limit')}")
    check("#12：history_shown == 实际下发条数",
          view.get("history_shown") == len(view.get("history") or []))
    check("#13：pending 走无 LIMIT 查询（默认 limit=0）",
          "state.radar_tracks_list(\"pending\")" in _src("radar_tracker.py"))


def test_tracks_view_frontend_note():
    view = _fe("views/MarketsView.tsx")
    check("#12：前端读 history_total / history_shown",
          "tracks.history_total" in view and "tracks.history_shown" in view)
    check("#12：前端注明口径", 't("markets.trackHistoryNote"' in view)
    check("#12：仅当全量 > 展示时才提示（否则是噪音）", "if (total <= shown) return null;" in view)
    rows = _fe("components/MarketRows.tsx")
    check("#12：TracksData 类型含新字段",
          "pending_total?: number" in rows and "history_shown?: number" in rows)


# ============================================================
# 七组：#1 / #8 日报
# ============================================================

def test_daily_report_fields():
    src = _src("scheduler.py")
    check("#1：日报不再读 change_pct（两个引擎都不产出该字段）",
          'r.get("change_pct", 0)' not in src)
    check("#1：按引擎取 change24_pct", 'r.get("change24_pct")' in src)
    check("#1：v1 回退 change7d_pct", 'r.get("change7d_pct")' in src)
    check("#1：都没有时写 24h n/a（不伪装 +0.00%）", '"24h n/a"' in src)
    check("#8：消费方读 engine", 'p.get("engine") or "v2"' in src)
    check("#8：降级时插入提示块", "已回退日 K 兜底口径（engine=v1）" in src)
    check("#8：提示块里写明「没有涨幅硬挡线与 OI 堆积线」",
          "没有涨幅硬挡线与 OI 堆积线" in src)


# ============================================================
# 八组：C1 / C2 结构缺口
# ============================================================

def test_c1_no_bars_not_silently_exempt():
    src = _src("scanner.py")
    check("C1：payload 上报 excluded_no_bars（无日线计数可见）",
          '"excluded_no_bars": n_no_bars,' in src)
    check("C1：计数变量已初始化", "n_new = n_wash = n_no_bars = 0" in src)
    check("C1：过滤层拆成两条显式路径", "n_no_bars += 1" in src)
    check("C1：无日线时改用 exchangeInfo onboardDate 判币龄",
          "listed = futures_listed_days(sym)" in src)
    check("C1：futures_listed_days 返回 None 表示未知（不是 0）",
          "def futures_listed_days(sym: str) -> float | None:" in src)
    check("C1：币龄未知时不拦截（fail-open）",
          "if listed is not None and listed < NEW_COIN_DAYS:" in src)


def test_c2_contract_count_used():
    src = _src("scanner.py")
    check("C2：futures_snapshot 取 count", '"count": int(float(d.get("count") or 0))' in src)
    check("C2：_radar_pool 用真实 count（此前恒 0）",
          'm["count"] = int(r.get("count") or 0)' in src)
    check("C2：换手畸高判据只在 count>0 时成立（分母为 0 不误判）",
          "if cnt > 0 and r[\"quote_volume\"] > 1e6" in src)


def test_c4_snapshot_columns_wired():
    """C4 的往返护栏在 test_v164；这里只确认「采集侧真的在填」——否则列加了也是空。"""
    src = _src("radar_tracker.py")
    for k in ("taker", "oi15", "top", "top48", "glob", "liq5m", "liqside", "basis", "spread"):
        check(f"C4：record_from_radar 采集 snap.{k}", f'"{k}":' in src, "")
    check("C4：_radar_track_out 生成 snap 子字典",
          'd["snap"] = {k[len("snap_"):]: v for k, v in d.items() if k.startswith("snap_")}'
          in _src("state.py"))


def test_archive_facing_columns_in_row():
    """档一：新增的 basis / spread 必须真的出行（否则前端/回放拿不到）。"""
    src = _src("scanner.py")
    check("档一：行含 spread_bps", '"spread_bps": r.get("spread_bps")' in src)
    check("档一：_spread_bps 纯函数存在", "def _spread_bps(bid, ask):" in src)
    # 行为：价差计算
    check("档一：_spread_bps(100,101) == 99.5 bps",
          abs(scanner._spread_bps(100, 101) - 99.5) < 1e-9,
          str(scanner._spread_bps(100, 101)))
    check("档一：_spread_bps(0,0) → None（未测到）", scanner._spread_bps(0, 0) is None)
    check("档一：_spread_bps(101,100)（倒挂）→ None", scanner._spread_bps(101, 100) is None)
    check("档一：factors 带 basis_bps / spread_bps / top_mean48",
          '"basis_bps"' in src and '"spread_bps"' in src and '"top_mean48"' in src)


def test_factors_oi_chg24_same_semantics():
    """#7：factors 层与顶层必须同语义（缺值写 None，不再写字面 0.0）。"""
    src = _src("scanner.py")
    check("#7：factors.oi_chg24 缺值写 None",
          '"oi_chg24": (round(d["oi_chg24"], 2) if d.get("oi_chg24") is not None else None)'
          in src)
    check("#7：不再出现 round(cf.get(\"oi_chg24\", 0.0) or 0.0, 2)",
          'cf.get("oi_chg24", 0.0) or 0.0' not in src)
    check("#7：factors.oi_pulse15 同语义", '"oi_pulse15": (round(d["oi_pulse15"], 2)' in src)
    check("#7：factors.liq_5m 同语义", '"liq_5m": (round(d["liq_5m"], 0)' in src)


# ============================================================
# 九组：#11 SHORT 分支注释（历史兼容标记）
# ============================================================

def test_short_branches_annotated():
    """#11：SHORT 分支自 v1.6.3 起对**新登记**不可达，四处都要有历史兼容说明。"""
    src = _src("radar_tracker.py")
    n = src.count("历史遗留")
    check("#11：SHORT 分支补了「历史遗留」注释（>=4 处）", n >= 4, f"count={n}")
    check("#11：说明未来重开做空需要同时改 rows_ign 分组",
          "rows_ign" in src or "ignition" in src)


# ============================================================
# 十组：档一 §16 阈值命中率监控
# ============================================================
# 缘起：`_TAKER_BUY_DOMINANT = 1.85` 自上线起命中 **0 个**，却在四处被使用
# （点火判据 / `_radar_score` +3 / 依据文案 / 妖币引擎燃料项）。这类「死规则」
# 不报错、不抛异常、也不会让任何测试变红 —— 只能靠命中率暴露。

def test_thr_rules_pure_function():
    """判定函数：命中/不命中/未测到三种情况必须分清。"""
    R = scanner._thr_rules
    # 全命中（chg24=13.0 才能同时越过 10% 硬挡线与 12% 追高线）
    hot = {"chg24": 13.0, "amp24": 25.0, "oi_chg24": 6.0, "fut_qv": 1e6,
           "spot_qv": 1000.0, "oi_usd": 5e4, "funding": -0.002}
    f = R(hot, {"taker_ratio": 1.35})
    check("§16：全命中输入 → 9 条规则全部为 True", all(f.values()), str(f))
    check("§16：规则集合与 _THR_RULES 一致",
          set(f) == {k for k, _ in scanner._THR_RULES}, str(sorted(f)))
    # 全不命中
    cold = {"chg24": 1.0, "amp24": 3.0, "oi_chg24": None, "fut_qv": 1e6,
            "spot_qv": 1e6, "oi_usd": 0.0, "funding": 0.0}
    g = R(cold, {"taker_ratio": 1.0})
    check("§16：冷输入 → 一条都不命中", not any(g.values()), str({k for k, v in g.items() if v}))
    # 边界：>= vs >
    check("§16：chg24 = 10.0 命中硬挡线（闭区间）",
          R({**cold, "chg24": 10.0}, {})["trig_late_chg24"] is True)
    check("§16：chg24 = 9.99 不命中硬挡线", R({**cold, "chg24": 9.99}, {})["trig_late_chg24"] is False)
    check("§16：chg24 = 12.0 不命中追高线（严格 >）",
          R({**cold, "chg24": 12.0}, {})["trig_max_chg24"] is False)
    check("§16：chg24 = 12.01 命中追高线", R({**cold, "chg24": 12.01}, {})["trig_max_chg24"] is True)


def test_thr_rules_unmeasured_is_not_a_hit():
    """核心：**未测到 ≠ 命中**。缺失一律 False，绝不能把 None 当 0 当命中。"""
    R = scanner._thr_rules
    base = {"chg24": 0.0, "amp24": 0.0, "fut_qv": 1e6, "spot_qv": 1e6, "funding": 0.0}
    check("§16：taker_ratio 缺失 → 不命中",
          R({**base, "oi_usd": 1.0, "oi_chg24": None}, {})["taker_buy_dominant"] is False)
    check("§16：taker_ratio = None → 不命中",
          R({**base, "oi_usd": 1.0, "oi_chg24": None}, {"taker_ratio": None})["taker_buy_dominant"] is False)
    check("§16：oi_chg24 = None（未测到）→ 不命中堆积线",
          R({**base, "oi_usd": 1.0, "oi_chg24": None}, {})["trig_max_oi24"] is False)
    check("§16：oi_chg24 = 0.0（测得没动）→ 也不命中",
          R({**base, "oi_usd": 1.0, "oi_chg24": 0.0}, {})["trig_max_oi24"] is False)
    check("§16：funding 缺失 → 不命中负费率",
          R({"chg24": 0.0, "amp24": 0.0, "oi_usd": 1.0, "oi_chg24": None, "fut_qv": 1.0,
             "spot_qv": 1.0}, {})["manip_neg_funding"] is False)
    # 换手畸高：分母为 0（未测到持仓额）不得成立 —— 这正是 #6 的病根
    check("§16：oi_usd = 0 → 换手畸高不成立（分母为 0 不是命中）",
          R({**base, "oi_chg24": None, "oi_usd": 0.0}, {})["manip_churn"] is False)
    check("§16：oi_usd > 0 且 fut_qv/oi_usd > 10 → 命中",
          R({**base, "oi_chg24": None, "oi_usd": 5e4, "fut_qv": 1e6}, {})["manip_churn"] is True)


def test_thr_hitrate_snapshot_and_dead():
    """快照：累计、命中率、以及「样本够但 0 命中」才判 dead。"""
    scanner.threshold_hitrate_reset()
    try:
        h0 = scanner.threshold_hitrate()
        check("§16：reset 后计数归零", h0["scans"] == 0 and h0["rows"] == 0)
        check("§16：空快照不误报 dead（样本不足）", h0["dead_rules"] == [])
        check("§16：快照带 min_rows 与规则说明",
              h0["min_rows"] == scanner._THR_DEAD_MIN_ROWS
              and all(v.get("desc") for v in h0["rules"].values()))

        # 只命中「顺向涨幅 ≥3%」与「硬挡线 ≥10%」两条的窄样本：
        # 其余 7 条规则在整个窗口内 0 命中 —— 正是要检测的「死规则」形态
        narrow = {"chg24": 10.0, "amp24": 0.0, "oi_chg24": None, "fut_qv": 1e6,
                  "spot_qv": 1e6, "oi_usd": 0.0, "funding": 0.0}
        scanner._thr_new_scan()
        for _ in range(5):
            scanner._thr_tally(scanner._thr_rules(narrow, {}))
            scanner._THR_HITS["rows"] = int(scanner._THR_HITS["rows"]) + 1
        h = scanner.threshold_hitrate()
        check("§16：scans 累计 +1", h["scans"] == 1, str(h["scans"]))
        check("§16：rows 累计 5", h["rows"] == 5, str(h["rows"]))
        check("§16：命中率 = 5/5 = 1.0", h["rules"]["trig_late_chg24"]["rate"] == 1.0,
              str(h["rules"]["trig_late_chg24"]))
        check("§16：命中数 = 5", h["rules"]["trig_late_chg24"]["hits"] == 5)
        check("§16：未命中的规则 hits = 0 且 rate = 0",
              h["rules"]["taker_buy_dominant"]["hits"] == 0
              and h["rules"]["taker_buy_dominant"]["rate"] == 0.0,
              str(h["rules"]["taker_buy_dominant"]))
        # 样本不足 → 即便 0 命中也不判 dead
        check("§16：样本 < min_rows 时不判 dead", h["dead_rules"] == [], str(h["dead_rules"]))
        # 样本够了 → 0 命中的规则进 dead_rules，命中过的规则不进
        scanner._THR_HITS["rows"] = scanner._THR_DEAD_MIN_ROWS + 100
        h2 = scanner.threshold_hitrate()
        check("§16：样本够且 0 命中 → 进 dead_rules（含追高线 / 买盘主导）",
              {"trig_max_chg24", "taker_buy_dominant"} <= set(h2["dead_rules"]),
              str(h2["dead_rules"]))
        check("§16：有命中的规则不进 dead_rules",
              "trig_late_chg24" not in h2["dead_rules"], str(h2["dead_rules"]))
        check("§16：dead_rules 已排序（输出稳定）", h2["dead_rules"] == sorted(h2["dead_rules"]))
    finally:
        scanner.threshold_hitrate_reset()


def test_thr_monitor_wired():
    """接线：监控必须在扫描路径上被调用，且随 payload 下发；否则是死代码。"""
    code = _code_only("scanner.py")
    compact = "".join(code.split())
    check("§16：扫描开始计一轮", "_thr_new_scan()" in code)
    check("§16：逐行 tally", "_thr_tally(_thr_rules(d,cf))" in compact)
    check("§16：分母逐行自增", '_THR_HITS["rows"]=int(_THR_HITS["rows"]or0)+1' in compact)
    check("§16：payload 下发 threshold_hits", '"threshold_hits":threshold_hitrate(),' in compact)
    # 生产判据与监控必须用**同一个常量对象**，否则监控的是另一套数
    check("§16：监控引用 _TAKER_BUY_DOMINANT 本身（不是复制的字面量）",
          ">=_TAKER_BUY_DOMINANT" in compact)
    src = _src("scanner.py")
    check("§16：taker 规则不再出现 1.85 字面量",
          "1.85" not in _code_only("scanner.py") or ">= 1.85" not in _code_only("scanner.py"))


def test_thr_monitor_frontend():
    rows = _fe("components/MarketRows.tsx")
    view = _fe("views/MarketsView.tsx")
    check("§16：前端类型 RadarThresholdHits 已导出", "export type RadarThresholdHits" in rows)
    check("§16：MarketsView 读取 threshold_hits", "setRadarThr(" in view)
    check("§16：仅 dead_rules 非空时显示（冷启动不噪音）",
          "radarThr?.dead_rules?.length" in view)
    check("§16：告警文案走 i18n", 't("markets.thrDead"' in view)
    check("§16：悬浮说明列出具体规则与样本量",
          't("markets.thrDeadTip"' in view and "radarThr.rules?.[k]?.desc" in view)


def main():
    for fn in (test_dormant_zero_rvol, test_dormant_missing_rvol_fail_open,
               test_late_display_only, test_late_frontend_and_i18n,
               test_thresholds_single_source, test_frontend_taker_uses_payload,
               test_manip_unmeasured_frontend, test_liq_available_semantics,
               test_confirm_layer_liq_not_faked_as_zero,
               test_scan_lock, test_daily2_cache_key,
               test_tracks_view_reports_scope, test_tracks_view_frontend_note,
               test_daily_report_fields,
               test_c1_no_bars_not_silently_exempt, test_c2_contract_count_used,
               test_c4_snapshot_columns_wired, test_archive_facing_columns_in_row,
               test_factors_oi_chg24_same_semantics, test_short_branches_annotated,
               test_thr_rules_pure_function, test_thr_rules_unmeasured_is_not_a_hit,
               test_thr_hitrate_snapshot_and_dead, test_thr_monitor_wired,
               test_thr_monitor_frontend):
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

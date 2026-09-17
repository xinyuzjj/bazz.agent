"""v1.7.1 测试 —— C3 延迟治理 + #10 成本模型闭环（资金费率结算周期 / 合约盘口价差）。

背景（本轮做了什么、为什么）：
  C3 端到端延迟 3~8 分钟，报告原先把链路上三个固定项并列，其中「触发层 15m K 线」被
  当成主要嫌疑。**实测推翻了这个判断**：`/fapi/v1/klines` 返回的最后一根是**在途未收盘**
  K 线（实测其 closeTime 比当前时间晚 713s），触发层特征直接读 `c15[-1]`/`c5[-1]`，
  每轮扫描用的都是当下最新价 —— 不存在「等 15 分钟收盘」的盲区。真正的固定项是
  后端 `RADAR2_TTL`（300s）与前端轮询（180s）。所以本轮：
    · 后端 TTL 支持 env 覆盖（`radar2_ttl()`），默认行为不变
    · 所有返回路径经 `_with_age()` 补写 `age_sec` / `stale` —— 延迟**可见**
    · 前端轮询 180s → 60s，并把新鲜度做成 pill
  #10 成本模型发现两处**系统性偏乐观**（同一种形状：数字不报错、只是偏）：
    · 资金费率结算周期硬编码 8h，而实测 `/fapi/v1/fundingInfo`（782 条）里
      **4h 才是多数**（4h=467 / 8h=312 / 1h=3），成交额前 110 有 40 个是 4h →
      这些币的资金费率成本**低估 2 倍**（1h 的低估 8 倍）
    · 滑点常数「等样本攒够再标定」的闭环**是断的**：`ts_series.spread_bps` 存的是
      **现货**价差，而纯合约币恒为 None —— 偏偏妖币里「只上合约」是经典形态
  修法不是逐币打 depth（110 次请求），而是 `/fapi/v1/ticker/bookTicker` 不带 symbol：
  **一次请求拿全市场 766 个合约**的买一卖一（实测 1.64s）。

本文件钉住的核心不变量：
  ① **「未测到」≠「测得为 0」**：新加的两处数据（周期 / 合约价差）缺值一律 None，
     消费方回退默认值时必须**自知那是假设**（`measured` 布尔不得被丢掉）。
  ② **口径不覆盖、只并列**：`spread_bps`（现货）保持原样，合约价差单列
     `fut_spread_bps`；`_sim_pnl` 仍是毛值，净值只做并列新增。
  ③ **向后兼容**：`sim_cost` / `sim_pnl_net` 新增的 `interval_h` 默认值让两参数调用
     结果**逐字节不变**；`radar2_ttl()` 默认值等于原 `RADAR2_TTL`。
  ④ **列顺序三方一致**：`_TS_COLS` / 建表 / INSERT 元组顺序必须一致（C4 踩过的静默错位）。

运行：.venv/Scripts/python.exe tests/test_v171_latency_cost.py
"""
import io
import inspect
import math
import os
import sqlite3
import sys
import tempfile
import time

# 必须在 import 任何 src 模块之前：workspace.py import 时读该 env 决定库位置
os.environ["BAZZ_WORKSPACE"] = tempfile.mkdtemp(prefix="bazz_test_v171_")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
SRC = os.path.join(ROOT, "src")
FE = os.path.join(ROOT, "frontend", "src")
sys.path.insert(0, SRC)

import radar_tracker          # noqa: E402
import scanner                # noqa: E402
import state                  # noqa: E402

FAILS = []
PASSED = []


def check(name: str, cond: bool, extra: str = "") -> None:
    tag = "PASS" if cond else "FAIL"
    print(f"[{tag}] {name}" + (f"  ({extra})" if extra and not cond else ""))
    if cond:
        PASSED.append(name)
    else:
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
    """去掉全部空白。`_code_only` 是 tokenize 重建的，token 之间都插了换行，
    直接搜多 token 片段必然漏（v1.6.6 踩过）。"""
    return "".join(s.split())


def _squashed_src(name: str, base: str = None) -> str:
    return _squash(_code_only(name, base))


def _branch(src: str, start_marker: str, end_marker: str, after: int = 0) -> str:
    """截取 `start_marker` 与**其后第一个** `end_marker` 之间的源码片段（从 `after` 之后开始找）。

    用于断言「某个三元分支里的类名/文案」—— 直接在整个文件里搜 `pill-red` 是不够的
    （同一文件别处也有 `pill-red`，变异验证实测：把 stale 分支改成 pill-dim 照样通过）。
    """
    i = src.find(start_marker, after)
    if i < 0:
        return ""
    j = src.find(end_marker, i + len(start_marker))
    return src[i:j if j > 0 else len(src)]


def _freshness_branches(mv: str) -> tuple:
    """返回 `(stale 分支, fresh 分支)` 的源码片段。

    必须按「先 stale、再 fresh」的顺序定位：`) : (` 在文件里不止一处，
    直接 `find` 会取到别的分支（那样两条断言就都在测别处的代码）。
    """
    i = mv.find("radarFresh.stale ? (")
    if i < 0:
        return "", ""
    j = mv.find(") : (", i)
    if j < 0:
        return "", ""
    k = mv.find(")}", j)
    return mv[i:j], mv[j:k if k > 0 else len(mv)]


class _FakeResp:
    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._p


class _FakeSession:
    """最小假会话：按 URL 子串派发，可注入异常，并记录调用次数。"""

    def __init__(self, routes: dict):
        self.routes = routes
        self.calls = []

    def get(self, url, **kw):
        self.calls.append(url)
        for key, val in self.routes.items():
            if key in url:
                if isinstance(val, Exception):
                    raise val
                return _FakeResp(val)
        raise AssertionError(f"未预置的 URL: {url}")


def _swap_session(mod, fake):
    """临时替换模块的 `_session`，返回恢复函数。"""
    old = mod._session
    mod._session = fake
    return lambda: setattr(mod, "_session", old)


# ============================================================
# A. C3 延迟治理
# ============================================================

def test_radar2_ttl_env_override():
    old = os.environ.get("BAZZ_RADAR2_TTL")
    try:
        os.environ.pop("BAZZ_RADAR2_TTL", None)
        check("默认 TTL == 原常量 RADAR2_TTL（默认行为不变）",
              scanner.radar2_ttl() == scanner.RADAR2_TTL == 300.0,
              f"{scanner.radar2_ttl()}")
        os.environ["BAZZ_RADAR2_TTL"] = "90"
        check("env 覆盖生效", scanner.radar2_ttl() == 90.0, f"{scanner.radar2_ttl()}")
        for bad in ("abc", "", "   ", "-5", "0", "0.0", "nan", "inf"):
            os.environ["BAZZ_RADAR2_TTL"] = bad
            check(f"非法值 {bad!r} 回退默认（不抛、不放行 0）",
                  scanner.radar2_ttl() == 300.0, f"{scanner.radar2_ttl()}")
    finally:
        if old is None:
            os.environ.pop("BAZZ_RADAR2_TTL", None)
        else:
            os.environ["BAZZ_RADAR2_TTL"] = old


def test_cache_check_uses_radar2_ttl():
    code = _squashed_src("scanner.py")
    # 精确计数（不是 `>=`）：三处缓存判断各一次。用 `>=` 会让「只改一处」漏网 ——
    # 变异验证实测过：把第一处改回 `RADAR2_TTL`，`>=2` 仍然通过（还剩两处）。
    n = code.count('_radar2_cache["ts"]<radar2_ttl()')
    check("三处缓存判断全部走 radar2_ttl()", n == 3, f"{n} 处")
    check("没有一处漏回写死的 RADAR2_TTL（env 覆盖才不会半失效）",
          '_radar2_cache["ts"]<RADAR2_TTL' not in code)
    check("payload 的 ttl 字段下发的是本轮生效值",
          '"ttl":radar2_ttl()' in code)
    check("v1 兜底分支的 ttl 默认值也走 radar2_ttl()",
          code.count('"ttl":v2.get("ttl",radar2_ttl())') == 2,
          str(code.count('"ttl":v2.get("ttl",radar2_ttl())')))


def test_with_age_semantics():
    payload = {"updated_at": 1000, "ttl": 300, "coins": [{"symbol": "A"}]}
    out = scanner._with_age(payload, 1120)
    check("age_sec = 读的时刻 − 扫描时刻", out["age_sec"] == 120, f"{out['age_sec']}")
    check("未超 TTL 时 stale=False", out["stale"] is False)
    check("_with_age 不修改原对象（浅拷贝，缓存里的对象必须原样）",
          "age_sec" not in payload and "stale" not in payload)
    check("浅拷贝共享嵌套列表（不做无谓深拷贝）", out["coins"] is payload["coins"])

    out2 = scanner._with_age({"updated_at": 1000, "ttl": 300}, 1400)
    check("超过 TTL → stale=True", out2["stale"] is True, f"age={out2['age_sec']}")

    out3 = scanner._with_age({"updated_at": 1000, "ttl": 60}, 1300)
    check("stale 依据 payload 里**本轮**的 ttl（不是模块常量）",
          out3["stale"] is True and scanner._with_age(
              {"updated_at": 1000, "ttl": 600}, 1300)["stale"] is False)

    out4 = scanner._with_age({"ttl": 300}, 1400)
    check("缺 updated_at → age_sec=0 且不 stale（宁可不报，也不拿 1970 年算出一个巨大值）",
          out4["age_sec"] == 0 and out4["stale"] is False, f"{out4}")
    out5 = scanner._with_age({"updated_at": "bad", "ttl": None}, 1400)
    check("updated_at 非数值 → 不抛、age_sec=0", out5["age_sec"] == 0)
    out6 = scanner._with_age({}, 1400)
    check("空 payload → 仍返回带 age_sec/stale 的 dict", "age_sec" in out6 and "stale" in out6)

    # 未来时间戳（时钟回拨）不得产生负数
    out7 = scanner._with_age({"updated_at": 2000, "ttl": 300}, 1000)
    check("读的时刻早于扫描时刻 → age_sec 夹到 0（不出现负年龄）", out7["age_sec"] == 0)


def test_get_radar_v2_returns_age():
    saved_data, saved_ts = scanner._radar2_cache["data"], scanner._radar2_cache["ts"]
    try:
        fake = {"updated_at": time.time() - 42.0, "ttl": 300, "coins": []}
        scanner._radar2_cache["data"] = fake
        scanner._radar2_cache["ts"] = fake["updated_at"]
        got = scanner.get_radar_v2()
        check("走缓存的返回带 age_sec（>0，约 42）",
              isinstance(got.get("age_sec"), int) and 41 <= got["age_sec"] <= 60,
              str(got.get("age_sec")))
        check("走缓存且未超 TTL → stale=False", got.get("stale") is False)
        check("返回的是拷贝，缓存里的原对象没被污染", "age_sec" not in scanner._radar2_cache["data"])
    finally:
        scanner._radar2_cache["data"], scanner._radar2_cache["ts"] = saved_data, saved_ts


def test_frontend_latency_wiring():
    mv = _src("views/MarketsView.tsx", FE)
    mr = _src("components/MarketRows.tsx", FE)
    lc = _src("i18n/locales.ts", FE)
    mock = _src("preview/mock.ts", FE)

    check("轮询间隔已从 180s 压到 60s", "60_000" in mv and "180_000" not in mv,
          f"60_000={mv.count('60_000')} 180_000={mv.count('180_000')}")
    check("RadarFresh 类型已定义", "export type RadarFresh" in mr and "age_sec" in mr)
    check("fmtAge 存在且不复用 fmtAgo（后者是「多久以前」的相对措辞，语义不同）",
          "export const fmtAge" in mr)
    check("fmtAge 已导入 MarketsView", "fmtAge" in mv.split("from \"../components/MarketRows\"")[0])
    check("MarketsView 读 age_sec/stale 并置状态",
          "setRadarFresh" in mv and "d?.age_sec" in mv and "d?.stale" in mv)
    check("缺字段时不显示假 pill（旧后端兼容）", 'typeofd?.age_sec==="number"' in _squash(mv))
    stale_b, fresh_b = _freshness_branches(mv)
    check("陈旧分支已定位到（否则下面两条断言测的是别处的代码）",
          "markets.stale" in stale_b and "markets.fresh" in fresh_b,
          f"stale={stale_b[:40]!r} fresh={fresh_b[:40]!r}")
    check("陈旧时显式红标（不把「旧缓存兜底」显示得像新鲜数据）",
          "pill-red" in stale_b, "stale 分支未见 pill-red")
    check("新鲜时是中性 pill（否则「没提示」与「没数据」分不开）",
          "pill-dim" in fresh_b, "fresh 分支未见 pill-dim")
    for k in ("markets.fresh", "markets.freshTip", "markets.stale", "markets.staleTip"):
        check(f"i18n 键 {k} zh/en 成对", lc.count(f'"{k}"') == 2, str(lc.count(f'"{k}"')))
    check("mock 提供 age_sec/stale（隔离预览也能看到 pill）",
          "age_sec" in mock and "stale" in mock)


# ============================================================
# B. #10 成本模型：资金费率结算周期
# ============================================================

def test_cost_interval_numbers():
    base = radar_tracker.sim_cost(24.0, 0.0001)                       # 默认 8h
    check("两参数调用 == 显式 interval_h=8（向后兼容）",
          base == radar_tracker.sim_cost(24.0, 0.0001, 8.0), f"{base}")
    c4 = radar_tracker.sim_cost(24.0, 0.0001, 4.0)
    c1 = radar_tracker.sim_cost(24.0, 0.0001, 1.0)
    # notional = 100 × 10 = 1000；费率 0.0001 → 每结算一次 0.1
    # 8h → 3 次 = 0.3；4h → 6 次 = 0.6；1h → 24 次 = 2.4
    check("4h 周期的资金费率部分是 8h 的 2 倍（差值恰为 0.3）",
          abs((c4 - base) - 0.3) < 1e-9, f"{c4} - {base} = {c4 - base}")
    check("1h 周期的资金费率部分是 8h 的 8 倍（差值恰为 2.1）",
          abs((c1 - base) - 2.1) < 1e-9, f"{c1} - {base} = {c1 - base}")
    check("固定成本（手续费+滑点）不受周期影响",
          abs((radar_tracker.sim_cost(0.0, 0.0001, 4.0)
               - radar_tracker.sim_cost(0.0, 0.0001, 8.0))) < 1e-9)

    for bad in (None, 0, 0.0, -1, -8.0, "x", "", float("nan"), float("inf")):
        got = radar_tracker.sim_cost(24.0, 0.0001, bad)
        check(f"周期 {bad!r} → 回退默认 8h（不接受会让 hours/interval 除零或翻符号的值）",
              got == base, f"{got} != {base}")


def test_cost_net_uses_interval():
    net8, roi8, cost8 = radar_tracker.sim_pnl_net("LONG", 1.0, 1.5, 24.0, 0.0001, 8.0)
    net4, roi4, cost4 = radar_tracker.sim_pnl_net("LONG", 1.0, 1.5, 24.0, 0.0001, 4.0)
    check("4h 周期成本更高", cost4 > cost8, f"{cost4} vs {cost8}")
    check("4h 周期净值更低（成本真的参与判定，不是摆设）", net4 < net8, f"{net4} vs {net8}")
    check("两参数 sim_pnl_net == 显式 8h",
          radar_tracker.sim_pnl_net("LONG", 1.0, 1.5, 24.0, 0.0001) == (net8, roi8, cost8))
    # 无仓位 → 全 0（不得因为「周期」就凭空产生成本）
    check("未成交（found/px 缺失）→ 成本也是 0（不凭空收费）",
          radar_tracker.sim_pnl_net("LONG", 0, 1.5, 24.0, 0.0001, 4.0) == (0.0, 0.0, 0.0))


def test_gross_unchanged():
    """`_sim_pnl` 必须仍是**毛值**、仍是 3 参数 —— 净值只做并列新增。"""
    import inspect
    sig = inspect.signature(radar_tracker._sim_pnl)
    check("_sim_pnl 仍是 3 参数（未被塞进 interval_h）", len(sig.parameters) == 3,
          str(list(sig.parameters)))
    gross, roi = radar_tracker._sim_pnl("LONG", 1.0, 1.5)
    check("_sim_pnl 仍返回纯价格差 × 杠杆的毛值", gross == 500.0 and roi == 500.0,
          f"{gross}/{roi}")
    net, _, cost = radar_tracker.sim_pnl_net("LONG", 1.0, 1.5, 24.0, 0.0001, 8.0)
    check("净值 = 毛值 − 成本（并列，不替换）", abs(net - (gross - cost)) < 1e-9,
          f"{net} vs {gross}-{cost}")
    check("SLIP_SRC 标定状态存在且当前是常数（不谎称已按实测标定）",
          radar_tracker.SLIP_SRC == "constant")


def test_funding_interval_lookup():
    saved = scanner._radar2_cache["data"]
    try:
        scanner._radar2_cache["data"] = {"coins": [
            {"symbol": "FOURUSDT", "factors": {"funding_interval_h": 4.0}},
            {"symbol": "NONEUSDT", "factors": {"funding_interval_h": None}},
            {"symbol": "MISSUSDT", "factors": {}},
            {"symbol": "ZEROUSDT", "factors": {"funding_interval_h": 0}},
        ]}
        check("实测 4h → 用 4h", radar_tracker._funding_interval("FOURUSDT") == 4.0)
        check("未测到（None）→ 回退 8h", radar_tracker._funding_interval("NONEUSDT") == 8.0)
        check("字段缺失 → 回退 8h", radar_tracker._funding_interval("MISSUSDT") == 8.0)
        check("0（不该出现的值）→ 回退 8h，不放行除零", radar_tracker._funding_interval("ZEROUSDT") == 8.0)
    finally:
        scanner._radar2_cache["data"] = saved
    check("无缓存行 → 回退 8h（不抛）", radar_tracker._funding_interval("NOPEUSDT") == 8.0)
    check("_funding_interval 零外呼（不引用 requests/网络函数）",
          "requests" not in inspect.getsource(radar_tracker._funding_interval))


def test_interval_wired_into_call_sites():
    """用 AST 精确判定：每个 `sim_pnl_net(...)` 调用都传了第 6 个实参（周期）。

    ⚠️ 不用「源码里出现几次 `_funding_interval(`」这种字符串计数 —— 它既会漏掉
    `def _funding_interval(sym: str)`（那里是 `(` 后跟类型注解），也数不出「哪个调用点漏了」。
    """
    import ast
    tree = ast.parse(_src("radar_tracker.py"))
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "sim_pnl_net"]
    check("sim_pnl_net 有 3 个调用点", len(calls) == 3, str(len(calls)))
    for i, c in enumerate(calls):
        check(f"第 {i + 1} 个调用点传了第 6 个实参（实际结算周期）",
              len(c.args) == 6, f"args={len(c.args)}")
    check("复盘文本写明用了哪个结算周期（数字口径必须可追溯）",
          "结算周期" in _src("radar_tracker.py"))
    check("scanner 下发 funding_interval_h",
          "funding_interval_h" in _squashed_src("scanner.py"))
    check("两侧默认周期常量一致（防止只改一边）",
          scanner.FUNDING_CYCLE_DEFAULT_H == radar_tracker.FUNDING_CYCLE_H == 8.0,
          f"{scanner.FUNDING_CYCLE_DEFAULT_H} vs {radar_tracker.FUNDING_CYCLE_H}")


# ============================================================
# C. fundingInfo / bookTicker 解析
# ============================================================

def test_funding_intervals_parse():
    saved = dict(scanner._funding_info_cache)
    try:
        scanner._funding_info_cache.update({"ts": 0.0, "intervals": {}, "measured": False})
        fake = _FakeSession({"/fapi/v1/fundingInfo": [
            {"symbol": "AUSDT", "fundingIntervalHours": 4, "adjustedFundingRateCap": "0.02"},
            {"symbol": "BUSDT", "fundingIntervalHours": 8},
            {"symbol": "CUSDT", "fundingIntervalHours": 0},      # 非正 → 丢弃
            {"symbol": "DUSDT", "fundingIntervalHours": -1},     # 负 → 丢弃
            {"symbol": "EUSDT", "fundingIntervalHours": "x"},    # 非数值 → 丢弃
            {"symbol": "", "fundingIntervalHours": 4},           # 空 symbol → 丢弃
            {"symbol": "FUSDT"},                                 # 缺字段 → 丢弃
        ]})
        restore = _swap_session(scanner, fake)
        try:
            iv, ok = scanner.get_funding_intervals()
        finally:
            restore()
        check("返回的是 (dict, measured) 二元组", isinstance(iv, dict) and isinstance(ok, bool))
        check("measured=True（真读到了）", ok is True)
        check("解析出 4h / 8h", iv.get("AUSDT") == 4.0 and iv.get("BUSDT") == 8.0, str(iv))
        check("非正/非数值/空 symbol/缺字段一律丢弃（不写入 0 或垃圾）",
              set(iv) == {"AUSDT", "BUSDT"}, str(sorted(iv)))
        # ⚠️ 判「不消费 cap/floor」必须看**消费形态**，不能搜字符串：
        # `get_funding_intervals` 的 docstring 里就写着 `adjustedFundingRateCap/Floor`
        # （说明「本轮不消费」），而 `_code_only` 只剔 COMMENT、**不剔 docstring** ——
        # 这正是 v1.6.6 在 `test_v167` 上踩过的同一个坑。所以断言「取值/下标」这种真消费形态。
        sq = _squashed_src("scanner.py")
        check("只取 fundingIntervalHours，不引入 cap/floor（避免再落一个「已付费未取用」）",
              '["adjustedFundingRateCap"]' not in sq
              and '.get("adjustedFundingRateCap")' not in sq
              and '["adjustedFundingRateFloor"]' not in sq
              and '.get("adjustedFundingRateFloor")' not in sq)

        # 缓存：第二次不再打接口
        fake2 = _FakeSession({"/fapi/v1/fundingInfo": AssertionError("不该再打接口")})
        restore2 = _swap_session(scanner, fake2)
        try:
            iv2, ok2 = scanner.get_funding_intervals()
        finally:
            restore2()
        check("TTL 内命中缓存，零额外请求", iv2 == iv and ok2 is True and not fake2.calls)
    finally:
        scanner._funding_info_cache.clear()
        scanner._funding_info_cache.update(saved)


def test_funding_intervals_failure():
    saved = dict(scanner._funding_info_cache)
    try:
        scanner._funding_info_cache.update({"ts": 0.0, "intervals": {}, "measured": False})
        restore = _swap_session(scanner, _FakeSession({"/fapi/v1/fundingInfo": RuntimeError("net down")}))
        try:
            iv, ok = scanner.get_funding_intervals()
        finally:
            restore()
        check("接口失败 → 空表", iv == {}, str(iv))
        check("接口失败 → measured=False（**必须**，否则「默认 8h」会被当成实测）",
              ok is False)

        # 空数组也算失败（不能把「响应为空」读成「全市场都没有周期」）
        restore = _swap_session(scanner, _FakeSession({"/fapi/v1/fundingInfo": []}))
        try:
            iv2, ok2 = scanner.get_funding_intervals()
        finally:
            restore()
        check("响应为空列表 → 视作失败（不把「没数据」读成「没有 4h 周期」）",
              iv2 == {} and ok2 is False)

        # 「响应非空但一条可用周期都没有」—— 与「空数组」是两条不同的路径：
        # 前者要靠 `if not iv: raise` 拦，后者靠 `not data` 拦。两条都要单独钉，
        # 否则把 `if not iv:` 删掉照样全绿（变异验证实测过）。
        scanner._funding_info_cache.update({"ts": 0.0, "intervals": {}, "measured": False})
        restore = _swap_session(scanner, _FakeSession(
            {"/fapi/v1/fundingInfo": [{"symbol": "AUSDT"}, {"symbol": "BUSDT", "fundingIntervalHours": "x"}]}))
        try:
            iv_none, ok_none = scanner.get_funding_intervals()
        finally:
            restore()
        check("响应非空但全部字段不可用 → 也算失败（不更新缓存、不报 measured=True）",
              iv_none == {} and ok_none is False, f"{iv_none} {ok_none}")

        # 失败后回退上次成功内容，但 measured 保持上次的值
        scanner._funding_info_cache.update({"ts": 0.0, "intervals": {"XUSDT": 4.0}, "measured": True})
        restore = _swap_session(scanner, _FakeSession({"/fapi/v1/fundingInfo": RuntimeError("again")}))
        try:
            iv3, ok3 = scanner.get_funding_intervals()
        finally:
            restore()
        check("失败时回退上次成功内容（不让成本模型突然全变默认）",
              iv3 == {"XUSDT": 4.0} and ok3 is True, f"{iv3} {ok3}")
    finally:
        scanner._funding_info_cache.clear()
        scanner._funding_info_cache.update(saved)


def test_book_spreads_parse():
    saved = dict(scanner._book_cache)
    try:
        scanner._book_cache.update({"ts": 0.0, "spreads": {}, "measured": False})
        fake = _FakeSession({"/fapi/v1/ticker/bookTicker": [
            {"symbol": "AUSDT", "bidPrice": "100.00", "askPrice": "100.02"},   # 2bps
            {"symbol": "BUSDT", "bidPrice": "0", "askPrice": "1"},             # bid=0 → 丢
            {"symbol": "CUSDT", "bidPrice": "2", "askPrice": "1"},             # 交叉盘 → 丢
            {"symbol": "DUSDT", "bidPrice": "x", "askPrice": "1"},             # 非数值 → 丢
            {"symbol": "", "bidPrice": "1", "askPrice": "1"},                  # 空 symbol → 丢
        ]})
        restore = _swap_session(scanner, fake)
        try:
            sp, ok = scanner.get_book_spreads()
        finally:
            restore()
        check("返回 (dict, measured) 二元组", isinstance(sp, dict) and ok is True)
        # (100.02-100.00)/100.01*1e4 = 1.9998 → 2.0
        check("价差 = (ask−bid)/mid×1e4", sp.get("AUSDT") == 2.0, str(sp.get("AUSDT")))
        check("零价/交叉盘/非数值/空 symbol 一律不入表（不写 0 = 不谎称零价差）",
              set(sp) == {"AUSDT"}, str(sorted(sp)))

        # 重置缓存再测失败路径 —— 否则 TTL 内会直接命中上面那份成功结果，
        # 「失败 → measured=False」这条永远测不到（假通过）。
        scanner._book_cache.update({"ts": 0.0, "spreads": {}, "measured": False})
        restore = _swap_session(scanner, _FakeSession({"/fapi/v1/ticker/bookTicker": RuntimeError("down")}))
        try:
            sp2, ok2 = scanner.get_book_spreads()
        finally:
            restore()
        check("失败 → 空表 + measured=False", sp2 == {} and ok2 is False)
        check("单请求拿全市场（源码里不带 symbol 参数）",
              "bookTicker" in _squashed_src("scanner.py")
              and "ticker/bookTicker\"" in _src("scanner.py"))
    finally:
        scanner._book_cache.clear()
        scanner._book_cache.update(saved)


def test_book_spreads_live_median():
    """用固定 fixture 复现「中位/p90」量级 —— 钉住「常数偏保守」这个结论的算法。"""
    rows = [{"symbol": f"S{i}USDT", "bidPrice": "100", "askPrice": str(100 + i * 0.0001)}
            for i in range(1, 101)]
    fake = _FakeSession({"/fapi/v1/ticker/bookTicker": rows})
    saved = dict(scanner._book_cache)
    scanner._book_cache.update({"ts": 0.0, "spreads": {}, "measured": False})
    restore = _swap_session(scanner, fake)
    try:
        sp, ok = scanner.get_book_spreads()
    finally:
        restore()
        scanner._book_cache.clear()
        scanner._book_cache.update(saved)
    vals = sorted(sp.values())
    check("价差序列可用于标定（样本数正确）", ok and len(vals) == 100, f"{len(vals)}")
    check("最大价差 1bps（构造数据自检：bps 换算没差 100 倍）",
          abs(vals[-1] - 1.0) < 1e-6, f"{vals[-1]}")


# ============================================================
# D. ts_series 新列
# ============================================================

def test_ts_cols_match_schema():
    db = os.path.join(os.environ["BAZZ_WORKSPACE"], "state.db")
    state.ts_stats()                     # 触发建表
    con = sqlite3.connect(db)
    try:
        cols = [r[1] for r in con.execute("PRAGMA table_info(ts_series)").fetchall()]
    finally:
        con.close()
    check("建表列 == _TS_COLS（顺序也一致）", tuple(cols) == tuple(state._TS_COLS),
          f"schema={cols}\ncols={list(state._TS_COLS)}")
    check("新列 fut_spread_bps 已在表中", "fut_spread_bps" in cols)
    check("旧列 spread_bps 未被替换（口径不覆盖、只并列）", "spread_bps" in cols)


def test_ts_roundtrip_new_col():
    now = time.time() - 600.0            # 贴近生产口径：太老的时间戳会被首次 ts_prune 清掉
    sym = "RT171USDT"
    check("写入 1 行", state.ts_append([{"symbol": sym, "ts": now, "price": 1.5,
                                     "chg24": 7.0, "fut_spread_bps": 3.25}]) == 1)
    got = state.ts_range(sym, now - 10, now + 10)
    check("读回 1 行", len(got) == 1, str(len(got)))
    check("fut_spread_bps 值精确往返", abs(got[0]["fut_spread_bps"] - 3.25) < 1e-9,
          str(got[0].get("fut_spread_bps")))
    check("全部 16 列都读得回（列顺序错位会在这里现形）",
          len(got[0]) == len(state._TS_COLS) == 16, f"{len(got[0])}")

    sym2 = "RT171NONEUSDT"
    state.ts_append([{"symbol": sym2, "ts": now, "price": 2.0, "fut_spread_bps": None}])
    g2 = state.ts_range(sym2, now - 10, now + 10)
    check("None 落库读回仍是 None（不是 0）", g2[0]["fut_spread_bps"] is None,
          repr(g2[0]["fut_spread_bps"]))


def test_ts_migration_adds_column():
    """老库（缺列）必须能自动补列 —— 否则只在别人机器上炸。"""
    ws = tempfile.mkdtemp(prefix="bazz_test_v171_mig_")
    db = os.path.join(ws, "state.db")
    con = sqlite3.connect(db)
    con.execute("""CREATE TABLE ts_series (
        symbol TEXT NOT NULL, ts REAL NOT NULL, price REAL, chg24 REAL,
        oi_usd REAL, oi_chg24 REAL, funding REAL, mark_price REAL, index_price REAL,
        basis_bps REAL, spread_bps REAL, taker_ratio REAL, liq_5m REAL,
        liq_side TEXT, fut_qv REAL, PRIMARY KEY (symbol, ts))""")
    con.commit()
    con.close()
    # 用独立进程跑一次建表逻辑，避免污染当前连接
    import subprocess
    code = (
        "import os,sys,sqlite3;"
        f"os.environ['BAZZ_WORKSPACE']={ws!r};"
        f"sys.path.insert(0,{SRC!r});"
        "import state;state.ts_stats();"
        f"con=sqlite3.connect({db!r});"
        "print(','.join(r[1] for r in con.execute('PRAGMA table_info(ts_series)')))"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    cols = (r.stdout or "").strip().splitlines()[-1] if r.stdout.strip() else ""
    check("缺列的老库被自动补上 fut_spread_bps",
          "fut_spread_bps" in cols, f"rc={r.returncode} cols={cols} err={r.stderr[-200:]}")


# ============================================================
# E. 接线
# ============================================================

def test_wiring():
    code = _squashed_src("scanner.py")
    check("行里下发合约价差 fut_spread_bps", '"fut_spread_bps":(book.get(sym)if_bk_okelseNone)' in code)
    check("现货口径 spread_bps 保持原样（未被覆盖）", '"spread_bps":r.get("spread_bps")' in code)
    # ⚠️ 用**相邻键**锚定，而不是数 `"fut_spread_bps"` 出现几次 ——
    # 计数会把「行 / factors / 时序库行」三处混为一谈，任一处改成 None 都不会红
    # （变异验证实测过）。
    check("factors 层带的是真值，不是占位 None",
          '"spread_bps":d.get("spread_bps"),"fut_spread_bps":d.get("fut_spread_bps"),' in code)
    check("时序库行带的是真值，不是占位 None",
          '"fut_qv":d.get("fut_qv"),"fut_spread_bps":d.get("fut_spread_bps"),' in code)
    check("payload 有 age_sec / stale 占位",
          '"age_sec":0' in code and '"stale":False' in code)
    check("扫描时真的取了盘口（不是只定义了函数）",
          "book,_bk_ok=get_book_spreads()" in code)
    check("扫描时真的取了结算周期", "fund_iv,_fi_ok=get_funding_intervals()" in code)


# ============================================================
def main():
    groups = [
        ("C3 ① TTL 可配", [test_radar2_ttl_env_override, test_cache_check_uses_radar2_ttl]),
        ("C3 ② 新鲜度可见", [test_with_age_semantics, test_get_radar_v2_returns_age,
                          test_frontend_latency_wiring]),
        ("#10 ① 结算周期数值", [test_cost_interval_numbers, test_cost_net_uses_interval,
                            test_gross_unchanged, test_funding_interval_lookup,
                            test_interval_wired_into_call_sites]),
        ("#10 ② 新数据源解析", [test_funding_intervals_parse, test_funding_intervals_failure,
                            test_book_spreads_parse, test_book_spreads_live_median]),
        ("ts_series 新列", [test_ts_cols_match_schema, test_ts_roundtrip_new_col,
                         test_ts_migration_adds_column]),
        ("接线", [test_wiring]),
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
    print(f"全部通过：{len(PASSED)}/{len(PASSED)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

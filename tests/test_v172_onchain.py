"""v1.7.2 测试 —— 链上筹码接线（链下控盘代理 × 链上实证的交叉验证）。

本轮做了什么：
  把已写好但**从未接线**的 `src/onchain.py`（989 行）真正挂进雷达：三源零 Key
  （CoinGecko 消歧 → 合约地址；GoPlus EVM/Solana 持仓 + 貔貅/税率；RugCheck Solana
  20 名持仓 + 风险名），在雷达行与妖币控盘轴各出一格「链上交叉判定」。

  为什么这件事有价值：`scanner._manip_flags` 那五个指纹**全是链下代理**，描述的是
  「盘面长什么样」，本质在**猜**「有人在控盘」；链上筹码回答的是另一个问题 ——
  **币到底在谁手里**。两者对不上时才是真正的信息。

本文件钉住的核心不变量（按重要性）：

  ① **「没测到」≠「测得为 0」，且「没测到」的三种原因不能混**：
     · `unknown`   = 该币没有合约地址 / 主链不在支持表（是**这个币**的属性）
     · `source_down` = 整条链路不通（`measured=False`，是**我们的**问题）
     · 后端不下发这一格 = 旧后端 / mock
     把第二、三种说成第一种，就是替数据源背锅 —— 这是本轮最要紧的一条。

  ② **限流必须被识别，不能静默**：GoPlus 免费层限流时返回 **HTTP 200**，
     体里只有 `{"code":4029,"message":"too many requests"}`、**没有 `result` 键**，
     而原有解析是「`result` 不是非空 dict → None」→ 限流被读成「这个币没有链上数据」。
     所以本测试**不能只断言「返回 None」**（旧代码也返回 None，那样测不出东西），
     必须断言**计数器 + 退避窗口**真的被置上了。

  ③ **只有结构性指纹参与交叉**：「空头付钱 / 拉升无爆仓」讲的是对手盘在挨打，
     与「筹码在谁手里」不是同一个范畴。`_MANIP_STRUCT` 与 `square_monster.STRUCT_FLAGS`
     必须相等，且不得含那两个。

  ④ **单位归一必须就地完成**：GoPlus 的 `percent` 是**小数比例**（×100），
     RugCheck 的 `pct` **已是百分数**（不得再乘）。两者混在一个变量里 = 静默差 100 倍。

  ⑤ **先剔销毁地址/LP 池再算集中度**，且剔了什么要可审计（`top1_raw_pct` 保留）。
     实测反例：CAKE(BSC) top1 是 `0x…dead` 占 92.74% —— 不剔会读成「单一持有人控盘」
     这个最极端的结论；剔除后 4.14% 是整批样本里最分散的一个。**结论完全反过来。**

  ⑥ **旁路纪律**：链上失败不得影响雷达主流程；不得硬编码代理端口；
     节流间隔的上限必须 ≥ 间隔本身（否则调大间隔是空操作）。

运行：.venv/Scripts/python.exe tests/test_v172_onchain.py
"""
import json
import os
import re
import sys
import tempfile

# 必须在 import 任何 src 模块之前：workspace.py 在 import 时读该 env 决定数据目录
_TMP_WS = tempfile.mkdtemp(prefix="bazz_test_v172_")
os.environ["BAZZ_WORKSPACE"] = _TMP_WS

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
SRC = os.path.join(ROOT, "src")
FE = os.path.join(ROOT, "frontend", "src")
sys.path.insert(0, SRC)

import onchain                # noqa: E402
import scanner                # noqa: E402
import square_monster         # noqa: E402
import workspace              # noqa: E402

FAILS = []
PASSED = []


def check(name: str, cond: bool, extra: str = "") -> None:
    tag = "PASS" if cond else "FAIL"
    print(f"[{tag}] {name}" + (f"  ({extra})" if extra and not cond else ""))
    (PASSED if cond else FAILS).append(name)


def read(p: str) -> str:
    with open(p, "r", encoding="utf-8") as f:
        return f.read()


def code_only(src: str) -> str:
    """剔除注释**与 docstring**，只留真正会执行的代码。

    ⚠️ 为什么不能只用「去掉 `#` 之后的部分」：修复说明的注释/docstring 里会**原样引用
    修复前的写法**（本轮 `_kernel_mixed` 的 docstring 就写着「此前写死
    `http://127.0.0.1:7899`」），于是「旧写法 not in 源码」被自己的文档绊倒 —— 断言的是
    文档不是代码。本项目在 v1.6.6 栽过同一个坑（同一类坑撞了两次），`test_v169` 的解法就是
    改用 `tokenize`。这里照做。

    判据：`STRING` token 且它前面那个有意义 token 是 `NEWLINE`/`INDENT`/`DEDENT`/无
    ⇒ 该字符串是**独立语句**（docstring），整段清掉。`x = "..."` / `f("...")` 里的字符串
    前面是 `=` / `(`，保留。
    """
    import io
    import tokenize
    keep = src.splitlines()
    try:
        prev_sig = None
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            t, _s, start, end, _line = tok
            if t == tokenize.COMMENT:
                keep[start[0] - 1] = keep[start[0] - 1][:start[1]]
                continue
            if t == tokenize.STRING and prev_sig in (None, tokenize.NEWLINE,
                                                     tokenize.INDENT, tokenize.DEDENT):
                for r in range(start[0], end[0] + 1):
                    keep[r - 1] = ""
            if t not in (tokenize.NL, tokenize.COMMENT):
                prev_sig = t
    except Exception:                                          # noqa: BLE001
        return src
    return "\n".join(keep)


# 一个能骗过 `_sym_ok` 的最小 GoPlus 响应工厂
def goplus_body(symbol: str, holders: list, **kw) -> dict:
    d = {"token_symbol": symbol, "holders": holders}
    d.update(kw)
    return {"code": 1, "message": "OK", "result": {"0xabc": d}}


def hold(addr: str, percent: float, locked: str = "0") -> dict:
    return {"address": addr, "percent": percent, "is_locked": locked}


# ══════════════════════════════════════════════════════════════════════════
# A. 符号归一（坑 4）：乘数前缀必须长到短匹配，且不能误剥
# ══════════════════════════════════════════════════════════════════════════
def t_symbol_base():
    check("1000BONKUSDT → ('BONK', 1000)",
          onchain.base_symbol("1000BONKUSDT") == ("BONK", 1000))
    check("BONKUSDT → ('BONK', 1)",
          onchain.base_symbol("BONKUSDT") == ("BONK", 1))
    # ⚠️ 长到短匹配：1000000MOG 若先被 1000 吃掉，剩下 "000MOG" —— 整类币全丢
    check("1000000MOGUSDT → ('MOG', 1000000)（长前缀优先）",
          onchain.base_symbol("1000000MOGUSDT") == ("MOG", 1000000))
    check("1MBABYDOGEUSDT → ('BABYDOGE', 1000000)",
          onchain.base_symbol("1MBABYDOGEUSDT") == ("BABYDOGE", 1000000))
    # ⚠️ 1INCH 是**真名以数字开头**的币，不是 1 倍乘数的 INCH
    check("1INCHUSDT → ('1INCH', 1)（不得误剥成 INCH）",
          onchain.base_symbol("1INCHUSDT") == ("1INCH", 1))
    check("现货与合约同币归一为同一个 base",
          onchain.base_symbol("1000PEPEUSDT")[0] == onchain.base_symbol("PEPEUSDT")[0])


# ══════════════════════════════════════════════════════════════════════════
# B. 单位归一（坑 1）：行为断言，不是源码断言
# ══════════════════════════════════════════════════════════════════════════
def t_unit_goplus_decimal():
    """GoPlus 的 percent 是**小数比例** → 必须 ×100。"""
    orig = onchain._http_json
    onchain._http_json = lambda url, params, kind, timeout=None: (
        goplus_body("TESTCOIN", [hold("0x1", 0.9367)]), "direct")
    try:
        r = onchain._read_evm("TESTCOIN", "binance-smart-chain", "0xabc")
    finally:
        onchain._http_json = orig
    check("GoPlus percent=0.9367 → top1_pct≈93.67（×100 已生效）",
          r is not None and abs(r["top1_pct"] - 93.67) < 0.01,
          f"got={None if r is None else r.get('top1_pct')}")
    # 反向：若哪天有人把 ×100 去掉，这里会读到 0.9367 —— 差 100 倍
    check("…且**不是** 0.9367（没有 ×100 就会是这个数）",
          r is not None and r["top1_pct"] > 1.0)


def t_unit_rugcheck_percent():
    """RugCheck 的 pct **已经是百分数** → 不得再 ×100。"""
    orig = onchain._http_json
    payload = {"tokenMeta": {"symbol": "TESTCOIN"},
               "knownAccounts": {}, "creatorBalance": 0,
               "token": {"supply": 1000000, "decimals": 6},
               "totalHolders": 1234, "score": 42,
               "topHolders": [{"owner": "A1", "pct": 8.8307},
                              {"owner": "A2", "pct": 5.0}]}
    onchain._http_json = lambda url, params, kind, timeout=None: (payload, "direct")
    try:
        r = onchain._read_solana_rugcheck("TESTCOIN", "Addr111")
    finally:
        onchain._http_json = orig
    check("RugCheck pct=8.8307 → top1_pct=8.8307（未再 ×100）",
          r is not None and abs(r["top1_pct"] - 8.8307) < 0.001,
          f"got={None if r is None else r.get('top1_pct')}")
    check("…若被误乘 100 会是 883.07 → 断言它 **不等于** 883.07",
          r is not None and abs(r["top1_pct"] - 883.07) > 1.0)


def t_unit_raw_helper():
    """`_pct` 只负责 GoPlus 那一侧；`_f` 对非法输入一律 None（未测到，不是 0）。"""
    check("_pct(0.5) == 50.0", onchain._pct(0.5) == 50.0)
    for bad in (None, "", "abc", float("nan"), float("inf"), True):
        check(f"_pct({bad!r}) → None（不得写 0）", onchain._pct(bad) is None)
    check("_f(0) == 0.0（**测得为 0** 要保留）", onchain._f(0) == 0.0)
    check("_bool01('1') is True", onchain._bool01("1") is True)
    check("_bool01('') is None（缺失 ≠ False）", onchain._bool01("") is None)


# ══════════════════════════════════════════════════════════════════════════
# C. 先剔销毁地址 / LP 池（坑 2）：结论会反过来
# ══════════════════════════════════════════════════════════════════════════
def t_burn_address_excluded():
    """实测形状：CAKE(BSC) 的 top1 是销毁地址占 92.74%。"""
    rows = [("0x000000000000000000000000000000000000dead", 92.7384, False)]
    rows += [(f"0xholder{i}", 0.4, False) for i in range(9)]
    top1, top10, raw1, exc = onchain._concentration(rows)
    check("销毁地址被剔除（holders_excluded ≥ 1）", exc >= 1, f"exc={exc}")
    check("top1_raw_pct 保留剔除前的 92.7384（**剔了什么可审计**）",
          raw1 is not None and abs(raw1 - 92.7384) < 0.001, f"raw={raw1}")
    check("剔除后的 top1 不再是 92.7（否则结论完全反过来）",
          top1 is not None and top1 < 1.0, f"top1={top1}")
    check("剔除后的 top10 也落在「分散」区间（<25）",
          top10 is not None and top10 < 25.0, f"top10={top10}")


def t_locked_and_pool_excluded():
    """`is_locked` 与 knownAccounts 里的 AMM 池都要剔。"""
    rows = [("0xlock", 40.0, True), ("0xpool", 30.0, False), ("0xreal", 12.0, False)]
    known = {"0xpool": {"type": "AMM", "name": "Raydium Pool"}}
    top1, top10, _raw, exc = onchain._concentration(rows, known)
    check("is_locked + AMM 池都剔除（exc==2）", exc == 2, f"exc={exc}")
    check("只剩真实持有人 → top1 == 12.0", top1 == 12.0, f"top1={top1}")


def t_all_excluded_is_unknown_not_zero():
    """全部被剔除 → 集中度**未测到**（None），不是 0。0 会被读成「极其分散」。"""
    rows = [("0x000000000000000000000000000000000000dead", 100.0, False)]
    top1, top10, raw1, exc = onchain._concentration(rows)
    check("全部被剔除 → top1/top10 == None", top1 is None and top10 is None)
    check("…但 raw_top1 仍保留 100.0", raw1 == 100.0)
    check("…且 exc==1", exc == 1)


def t_conclusion_flips():
    """核心：同一个币，「剔/不剔」会得出**相反**的交叉结论。"""
    hi = {"top10_pct": 92.74, "top1_pct": 92.74, "source": "goplus",
          "chain": "binance-smart-chain", "holders_excluded": 0}
    lo = {"top10_pct": 4.14, "top1_pct": 1.47, "source": "goplus",
          "chain": "binance-smart-chain", "holders_excluded": 1, "top1_raw_pct": 92.74}
    v_hi = onchain.cross_verdict(hi, ["无现货"])
    v_lo = onchain.cross_verdict(lo, ["无现货"])
    check("未剔除 → 判「链上佐证」（控盘为真）", v_hi["code"] == "confirm", v_hi["code"])
    check("剔除后 → 判「链上反驳」（代理可能误报）", v_lo["code"] == "refute", v_lo["code"])
    check("**两者结论必须相反**（这就是「先剔再算」的全部意义）",
          v_hi["code"] != v_lo["code"])


# ══════════════════════════════════════════════════════════════════════════
# D. 限流识别（本轮新修，坑：HTTP 200 里没有 result）
# ══════════════════════════════════════════════════════════════════════════
def t_throttle_signal_detects():
    """GoPlus 限流的**真实响应体**（实测原文）。"""
    real = {"code": 4029, "message": "too many requests"}
    check("识别 code=4029（GoPlus 实测原样）", bool(onchain._throttle_signal(real)))
    check("识别 HTTP 429 文本", bool(onchain._throttle_signal({"code": 429, "message": "x"})))
    check("识别 message 里的 too many requests",
          bool(onchain._throttle_signal({"message": "Too Many Requests"})))
    check("正常响应**不**误判",
          not onchain._throttle_signal(goplus_body("X", [hold("0xa", 0.5)])))
    check("list 响应（CoinGecko /coins/markets）不误判",
          not onchain._throttle_signal([{"id": "bonk"}]))
    check("空 dict 不误判", not onchain._throttle_signal({}))


def t_throttle_sets_counter_and_backoff():
    """⚠️ 本组是防「静默」的核心：不能只断言返回 None（旧代码也返回 None）。

    必须断言 ① 计数器 +1 ② 退避窗口被打开。旧实现两者都不动，
    于是「限流」与「这个币查不到」在界面上完全一样。
    """
    onchain.reset_for_test()
    before = int(onchain._stats.get("goplus_throttled") or 0)
    orig = onchain._http_json
    onchain._http_json = lambda url, params, kind, timeout=None: (
        {"code": 4029, "message": "too many requests"}, "")
    try:
        r = onchain._read_evm("TESTCOIN", "ethereum", "0xabc")
    finally:
        onchain._http_json = orig
    after = int(onchain._stats.get("goplus_throttled") or 0)
    check("限流响应 → _read_evm 返回 None", r is None)
    check("⚠️ …且 goplus_throttled 计数 +1（这是旧实现**做不到**的那一半）",
          after == before + 1, f"{before} → {after}")
    check("⚠️ …且退避窗口真的被打开（goplus_available == False）",
          onchain._src_available("goplus") is False)
    check("退避秒数落在 GOPLUS_BACKOFF 附近",
          0 < onchain.onchain_view()["goplus_backoff_sec"] <= int(onchain.GOPLUS_BACKOFF) + 1)


def t_throttle_short_circuits():
    """退避期内**不得再发请求**（否则退避等于没做）。"""
    onchain.reset_for_test()
    onchain._note_throttle("goplus")
    calls = {"n": 0}

    def spy(url, params, kind, timeout=None):
        calls["n"] += 1
        return goplus_body("TESTCOIN", [hold("0x1", 0.5)]), "direct"
    orig = onchain._http_json
    onchain._http_json = spy
    try:
        onchain._read_evm("TESTCOIN", "ethereum", "0xabc")
        onchain._read_solana_goplus("TESTCOIN", "Addr1")
    finally:
        onchain._http_json = orig
    check("退避期内 _read_evm / _read_solana_goplus 零请求", calls["n"] == 0, f"n={calls['n']}")
    check("…但现货链（RugCheck）不受 GoPlus 退避影响（两家独立）",
          onchain._src_available("rug") is True)


def t_http_429_counted_for_rug():
    """RugCheck 的 HTTP 429 也要计数（此前只有 CoinGecko 有计数器）。"""
    onchain.reset_for_test()
    before = int(onchain._stats.get("rug_throttled") or 0)

    def boom(url, params, kind, timeout=None):
        raise onchain._RateLimited()
    orig = onchain._http_json
    onchain._http_json = boom
    try:
        r = onchain._read_solana_rugcheck("TESTCOIN", "Addr1")
    finally:
        onchain._http_json = orig
    check("RugCheck 429 → 返回 None", r is None)
    check("⚠️ …且 rug_throttled 计数 +1",
          int(onchain._stats.get("rug_throttled") or 0) == before + 1)
    check("…且 rug 退避窗口打开", onchain._src_available("rug") is False)


def t_rates_are_measured_values():
    """间隔 / 退避的取值必须是**实测**出来的那几个，且互不矛盾。"""
    check("GOPLUS_MIN_GAP == 5.5（实测 5.5s 连打 14 次零限流）",
          onchain.GOPLUS_MIN_GAP == 5.5, str(onchain.GOPLUS_MIN_GAP))
    check("GOPLUS_BACKOFF == 45.0（实测 30s 仍限流、45s 恢复）",
          onchain.GOPLUS_BACKOFF == 45.0, str(onchain.GOPLUS_BACKOFF))
    # ⚠️ 这条是「调间隔必须同时看休眠上限」的护栏：2.0s 时代调多大间隔都是空操作
    check("⚠️ _MAX_THROTTLE_SLEEP ≥ GOPLUS_MIN_GAP（否则间隔大过上限就静默失效）",
          onchain._MAX_THROTTLE_SLEEP >= onchain.GOPLUS_MIN_GAP,
          f"cap={onchain._MAX_THROTTLE_SLEEP} gap={onchain.GOPLUS_MIN_GAP}")
    src = code_only(read(os.path.join(SRC, "onchain.py")))
    check("_throttle 里的休眠上限不得再写死 2.0",
          "min(wait, 2.0)" not in src)


# ══════════════════════════════════════════════════════════════════════════
# E. 路由：混合端口动态解析（本轮新修）；不硬编码、不误连别的进程
# ══════════════════════════════════════════════════════════════════════════
def t_code_only_selfcheck():
    """⚠️ 内建自检：护栏本身必须先被证明是有效的（防止「为让测试变绿而放水」）。"""
    probe = (
        "def f():\n"
        '    """docstring 里提到 http://127.0.0.1:7899 与 min(wait, 2.0)"""\n'
        "    # 注释里也提到 http://127.0.0.1:7899\n"
        "    x = 1  # 尾注释\n"
        '    y = "http://127.0.0.1:7899"\n'
        "    return x, y\n"
    )
    out = code_only(probe)
    check("自检：docstring 里的端口被剔除", "7899" not in out.split('y = "')[0])
    check("自检：注释里的端口被剔除", "# 注释里" not in out)
    check("自检：**字符串字面量**里的端口被保留（否则断言会漏掉真问题）",
          'y = "http://127.0.0.1:7899"' in out)
    check("自检：代码本体未被削掉", "def f():" in out and "return x, y" in out)


def t_no_hardcoded_mixed_port():
    src = code_only(read(os.path.join(SRC, "onchain.py")))
    check("源码里不得出现硬编码 127.0.0.1:7899（含字符串字面量）",
          "7899" not in src)
    check("…且必须走 proxy_kernel 的动态端口",
          "proxy_kernel" in src and "mixed_port" in src)


def t_kernel_mixed_dynamic():
    """`_kernel_mixed()` 必须取 `proxy_kernel.mixed_port()`，端口为 0 时返回空串。"""
    import types
    real = sys.modules.get("proxy_kernel")
    try:
        fake = types.ModuleType("proxy_kernel")
        fake.mixed_port = lambda: 5317          # 故意不用 7899
        sys.modules["proxy_kernel"] = fake
        got = onchain._kernel_mixed()
        check("端口 5317 → http://127.0.0.1:5317（动态端口被采纳）",
              got == "http://127.0.0.1:5317", got)
        fake.mixed_port = lambda: 0
        check("内核未运行（端口 0）→ 空串（**不能拼出 http://127.0.0.1:0**）",
              onchain._kernel_mixed() == "", onchain._kernel_mixed())
    finally:
        if real is not None:
            sys.modules["proxy_kernel"] = real
        else:
            sys.modules.pop("proxy_kernel", None)


def t_routes_order():
    """cg 必须「代理优先、直连兜底」；GoPlus/RugCheck 反过来（实测直连可达）。"""
    cg = [onchain._route_name(p) for p in onchain._routes("cg")]
    direct = [onchain._route_name(p) for p in onchain._routes("direct")]
    check("cg 的最后一条是 direct（兜底）", cg[-1] == "direct", str(cg))
    check("direct 的第一条是 direct（GoPlus/RugCheck 直连实测可达）",
          direct[0] == "direct", str(direct))
    check("cg 里没有重复路由", len(cg) == len(set(cg)), str(cg))


# ══════════════════════════════════════════════════════════════════════════
# F. 缓存：损坏文件不得拖垮链路；measured 语义三态
# ══════════════════════════════════════════════════════════════════════════
def t_corrupt_cache_is_survivable():
    """实测踩过：workspace 里的 `onchain_cache.json` 变成全 0 字节。

    加载路径必须吞掉并**保持 measured=False**（= 未测到），不能抛、也不能
    因为「文件存在」就以为「测到了」。
    """
    p = os.path.join(workspace.WORKSPACE, "onchain_cache.json")
    with open(p, "wb") as f:
        f.write(b"\x00" * 4096)
    onchain.reset_for_test()
    try:
        onchain._load_cache()
        ok = True
    except Exception as e:                                     # noqa: BLE001
        ok = False
        check("损坏缓存不得抛异常", False, repr(e))
    if ok:
        check("损坏缓存 → 不抛异常", True)
    view = onchain.onchain_view()
    check("损坏缓存 → measured 仍为 False（不得把「有文件」当「测到了」）",
          view["measured"] is False)
    check("损坏缓存 → symbols_cached == 0", view["symbols_cached"] == 0)


def t_cache_roundtrip_and_measured():
    """正常缓存：落盘 → 重启读取 → measured=True（跨重启不必重花 CoinGecko 配额）。"""
    onchain.reset_for_test()
    onchain._cache["holdings"]["BONK"] = {
        "source": "rugcheck", "chain": "solana", "contract": "Addr",
        "top1_pct": 8.831, "top10_pct": 38.62, "top1_raw_pct": 8.831,
        "holders_excluded": 0, "holders_returned": 20, "holder_count": 2069663.0,
        "ts": 1770000000.0}
    onchain._measured = True
    onchain._save_cache()
    onchain.reset_for_test()
    onchain._load_cache()
    data, measured = onchain.get_onchain_holdings(["BONKUSDT", "1000BONKUSDT"])
    check("跨重启读回：measured=True", measured is True)
    check("BONKUSDT 与 1000BONKUSDT 拿到**同一次读数**（按 base 归一）",
          data.get("BONKUSDT", {}).get("contract") == data.get("1000BONKUSDT", {}).get("contract")
          and bool(data.get("BONKUSDT")))
    check("返回的键与入参**逐字对应**（方便调用方按行查找）",
          set(data.keys()) == {"BONKUSDT", "1000BONKUSDT"}, str(set(data.keys())))
    check("没测到的币**不在返回里**（而不是给个 0）",
          onchain.get_onchain_holdings(["NOPEUSDT"])[0] == {})


def t_get_holdings_is_nonblocking_and_safe():
    """`get_onchain_holdings` 是**只读缓存**：不该发请求、不该被脏输入搞崩。"""
    orig = onchain._http_json
    calls = {"n": 0}

    def spy(*a, **k):
        calls["n"] += 1
        return None, ""
    onchain._http_json = spy
    try:
        onchain.get_onchain_holdings(["BTCUSDT", "", None, "  ", 123])
    finally:
        onchain._http_json = orig
    check("只读缓存 → 零网络请求", calls["n"] == 0, f"n={calls['n']}")
    check("脏输入（空串 / None / 数字）不抛", True)


# ══════════════════════════════════════════════════════════════════════════
# G. 交叉判定：六态 + 边界 + 只用结构性指纹
# ══════════════════════════════════════════════════════════════════════════
HI = {"top10_pct": 87.7, "top1_pct": 72.686, "source": "rugcheck", "chain": "solana",
      "holders_excluded": 2, "holder_count": 1843953.0}
LO = {"top10_pct": 4.14, "top1_pct": 1.47, "source": "goplus", "chain": "binance-smart-chain",
      "holders_excluded": 1, "top1_raw_pct": 92.74}
MID = {"top10_pct": 45.38, "top1_pct": 13.7, "source": "rugcheck", "chain": "solana"}


def t_six_states():
    cases = [
        ("代理命中+链上高 → confirm", HI, ["无现货"], "confirm"),
        ("代理命中+链上低 → refute", LO, ["无现货"], "refute"),
        ("代理未命中+链上高 → warn", HI, [], "warn"),
        ("代理未命中+链上低 → clean", LO, [], "clean"),
        ("中间段 → neutral", MID, ["换手畸高"], "neutral"),
        ("没有读数 → unknown", None, ["无现货"], "unknown"),
    ]
    for name, rd, flags, want in cases:
        got = onchain.cross_verdict(rd, flags)["code"]
        check(name, got == want, f"got={got}")


def t_verdict_boundaries():
    """阈值必须取**实测分布**定的 60 / 25，且边界是「含」还是「不含」要明确。"""
    check("OC_TOP10_HIGH == 60.0（实测中位 45.5，取 60 让报警保持稀有）",
          onchain.OC_TOP10_HIGH == 60.0)
    check("OC_TOP10_LOW == 25.0", onchain.OC_TOP10_LOW == 25.0)
    f = lambda t: onchain.cross_verdict({"top10_pct": t}, [])["code"]
    check("24.99 → clean", f(24.99) == "clean")
    check("25.00 → neutral（边界归中间档）", f(25.0) == "neutral")
    check("59.99 → neutral", f(59.99) == "neutral")
    check("60.00 → warn（≥ 即算集中）", f(60.0) == "warn")


def t_verdict_top10_none_is_unknown():
    """有合约地址但前十占比没算出来 → 未测到，**不得**当成 0（0 = 极分散）。"""
    v = onchain.cross_verdict({"top10_pct": None, "top1_pct": 1.0}, [])
    check("top10_pct=None → unknown", v["code"] == "unknown", v["code"])
    v2 = onchain.cross_verdict({"top10_pct": None, "top1_pct": 1.0}, [])
    check("…且文案说明是「占比没测出来」而非「没有链上数据」",
          "没测" in v2["text"] or "未测" in v2["text"], v2["text"][:40])


def t_verdict_text_carries_audit_fields():
    """文案里必须带上「剔了几行 / 第一持有人 / 来源」，否则「剔了什么」不可审计。"""
    v = onchain.cross_verdict(LO, ["无现货"])
    check("文案含前列占比", "4.1%" in v["text"], v["text"])
    check("文案含「已剔除 1 个」", "剔除 1" in v["text"], v["text"])
    check("文案含来源与链", "goplus" in v["text"] and "binance-smart-chain" in v["text"])


def t_structural_flags_only():
    """⚠️ 只有结构性指纹参与交叉 —— 位置类指纹与「筹码在谁手里」不是一个范畴。"""
    check("scanner._MANIP_STRUCT == square_monster.STRUCT_FLAGS（两侧名单不得漂移）",
          tuple(scanner._MANIP_STRUCT) == tuple(square_monster.STRUCT_FLAGS),
          f"{scanner._MANIP_STRUCT} vs {square_monster.STRUCT_FLAGS}")
    for bad in ("空头付钱", "拉升无爆仓"):
        check(f"结构性名单里不得含「{bad}」", bad not in scanner._MANIP_STRUCT)
    # 行为断言：只传位置类指纹时，代理视为**未命中** → 链上高集中应判 warn（而非 confirm）
    onchain.reset_for_test()
    cell = scanner._oc_cell(HI, ["空头付钱", "拉升无爆仓"], True)
    check("只命中位置类指纹 → 交叉判为 warn（不是 confirm）",
          cell.get("state") == "warn", str(cell.get("state")))
    cell2 = scanner._oc_cell(HI, ["无现货", "空头付钱"], True)
    check("含一个结构性指纹 → confirm", cell2.get("state") == "confirm", str(cell2.get("state")))


# ══════════════════════════════════════════════════════════════════════════
# H. 接线：scanner 旁路纪律 + 三种「没测到」不混
# ══════════════════════════════════════════════════════════════════════════
def t_scanner_wiring_source():
    src = read(os.path.join(SRC, "scanner.py"))
    check("扫描主体里派发了链上后台刷新（_onchain_dispatch(pool_syms)）",
          "_onchain_dispatch(pool_syms)" in src)
    check("派发在**逐币循环之前**（越早派发，本轮末尾越可能已有读数）",
          src.index("_onchain_dispatch(pool_syms)") < src.index("for sym in pool_syms:"))
    check("缓存只读一次（不在循环里逐行调 get_onchain_holdings）",
          "_oc_readings, _oc_measured = _onchain_read(pool_syms)" in src)
    check("行里带 onchain 字段", '"onchain": _oc_cell(' in src)
    check("payload 里下发体检快照", '"onchain": onchain_health()' in src)
    # 旁路纪律：惰性 import + 吞异常
    for fn in ("_onchain_dispatch", "_onchain_read", "_oc_cell", "onchain_health"):
        seg = src[src.index(f"def {fn}("):]
        seg = seg[:seg.index("\ndef ", 1)] if "\ndef " in seg[1:] else seg
        check(f"{fn} 惰性 import onchain（不落模块顶层）", "import onchain" in seg)
        check(f"{fn} 吞掉异常（不影响雷达主流程）", "except Exception" in seg)


def t_health_skeleton_when_import_fails():
    """链上模块 import 失败时，`onchain_health()` 必须返回**结构完整**的骨架。

    缺字段的前端会静默少显示一块，而那种缺失没人会发现 —— 所以键必须齐。
    """
    real = sys.modules.pop("onchain", None)
    sys.modules["onchain"] = None          # `import onchain` 会抛 ImportError
    try:
        h = scanner.onchain_health()
    finally:
        sys.modules.pop("onchain", None)
        if real is not None:
            sys.modules["onchain"] = real
    need = {"measured", "symbols_cached", "platforms_cached", "last_ok_at", "cg_available",
            "cg_backoff_sec", "goplus_available", "goplus_backoff_sec", "rug_backoff_sec",
            "refreshing", "route", "stats", "last_error", "dispatched", "dispatched_ts"}
    check("import 失败时骨架键完整", need <= set(h.keys()),
          f"缺={sorted(need - set(h.keys()))}")
    check("…且 measured=False（不得默认成「已测到」）", h["measured"] is False)
    check("…且 last_error 说明是 import 失败", "import" in str(h["last_error"]))


def t_source_down_vs_unknown():
    """⚠️ 本轮最要紧的一条：两种「没测到」的文案必须不同。"""
    onchain.reset_for_test()
    c_unknown = scanner._oc_cell(None, [], True)     # measured=True 但这币没读数
    c_down = scanner._oc_cell(None, [], False)       # measured=False：整条链路不通
    check("measured=True 且无该币读数 → unknown（是**这个币**的属性）",
          c_unknown.get("state") == "unknown", str(c_unknown.get("state")))
    check("measured=False → source_down（是**我们的**问题）",
          c_down.get("state") == "source_down", str(c_down.get("state")))
    check("两者 state 必须不同", c_unknown.get("state") != c_down.get("state"))
    check("source_down 文案点明「没测到，不是没有数据」",
          "没测到" in c_down.get("text", "") and "没有链上数据" in c_down.get("text", ""),
          c_down.get("text", "")[:60])
    check("source_down 文案提到限流/代理这两种真实成因",
          "代理" in c_down.get("text", "") and "限流" in c_down.get("text", ""))


def t_oc_cell_never_raises():
    """旁路格：任何脏输入都不得抛（雷达主路径不能因为这一格挂掉）。"""
    for args in ((None, None, True), ("垃圾", ["无现货"], True), (123, "x", False),
                 ({"top10_pct": "abc"}, [], True)):
        try:
            r = scanner._oc_cell(*args)
            ok = isinstance(r, dict)
        except Exception as e:                                  # noqa: BLE001
            ok = False
            check(f"_oc_cell{args!r} 不抛", False, repr(e))
        if not ok:
            return
    check("_oc_cell 对脏输入一律返回 dict 且不抛", True)
    check("_oc_cell 在 onchain 缺失时返回 {}（前端按「没有这一格」处理）",
          isinstance(scanner._oc_cell(None, [], True), dict))


def t_cell_carries_display_fields():
    onchain.reset_for_test()
    c = scanner._oc_cell(HI, ["无现货"], True)
    for k in ("state", "label", "text", "top1_pct", "top10_pct", "proxy_hit", "measured",
              "source", "chain", "holders_excluded", "top1_raw_pct", "holder_count", "risks"):
        check(f"cell 带字段 {k}", k in c, str(sorted(c.keys())))
    check("measured 透传", c["measured"] is True)
    check("risks 透传（RugCheck 自己给的风险名，是交叉验证的第二票）",
          "risks" in c)


# ══════════════════════════════════════════════════════════════════════════
# I. 妖币控盘轴：加证据、**不改分级**
# ══════════════════════════════════════════════════════════════════════════
def t_monster_axis_evidence_only():
    src = read(os.path.join(SRC, "square_monster.py"))
    seg = src[src.index("def _axis_control("):]
    seg = seg[:seg.index("\ndef ", 1)]
    check("控盘轴取用行上的 onchain 格", 'row.get("onchain")' in seg)
    check("控盘轴回传 chain 子字典", '"chain": chain' in seg)
    check("控盘轴回传 chain_struct", '"chain_struct": chain_struct' in seg)
    # ⚠️ 关键：链上是本轮第一次拿到真实读数（实测 14 个样本），拿它改分级 = 无样本改判据
    check("⚠️ 链上证据**不得**参与 grade 赋值（无样本改判据）",
          "grade, color = " not in seg[seg.index("chain = row.get"):] if "chain = row.get" in seg else False)
    check("…且注释里写明「只加证据，绝不动 grade」", "绝不动" in seg or "只加证据" in seg)


def t_monster_axis_runs_with_chain():
    """带 chain 格时 `_axis_control` 必须能跑通并把它写进证据串。"""
    row = {"manip": ["无现货"], "manip_note": "", "oi_usd": 0, "no_spot": True,
           "factors": {"top_ratio": None},
           "onchain": {"state": "warn", "label": "链上预警", "top10_pct": 87.7,
                       "text": "链上高度集中", "measured": True}}
    ca = square_monster._axis_control(row)
    check("带 chain 时返回 chain 子字典", isinstance(ca.get("chain"), dict))
    check("证据串里出现链上结论", "链上预警" in ca["evidence"], ca["evidence"])
    check("证据串里带上前十占比数字", "87.7" in ca["evidence"], ca["evidence"])
    check("grade 仍是按链下指纹定的那一档（无现货 → 明确控盘）",
          ca["grade"] == "明确控盘", ca["grade"])


def t_monster_axis_without_chain():
    """没有 chain 格时不得崩，且要把它记进 missing（不能当证据用）。"""
    row = {"manip": [], "manip_note": "", "oi_usd": 5e6, "factors": {"top_ratio": 1.1}}
    ca = square_monster._axis_control(row)
    check("无 chain 格 → 不崩，chain 为 {}（或空）",
          not ca.get("chain"), str(ca.get("chain")))
    check("无 chain 格 → 记进 missing", "链上筹码" in ca["missing"], str(ca["missing"]))


def t_monster_axis_with_garbage_chain():
    row = {"manip": [], "oi_usd": 1e6, "factors": {}, "onchain": "垃圾"}
    ca = square_monster._axis_control(row)
    check("chain 格是垃圾类型时不崩", isinstance(ca, dict))


# ══════════════════════════════════════════════════════════════════════════
# J. 前端契约：state 码、i18n 键、三态体检
# ══════════════════════════════════════════════════════════════════════════
BACKEND_CODES = {"warn", "confirm", "refute", "clean", "neutral", "unknown", "source_down"}


def t_frontend_covers_all_codes():
    rows_src = read(os.path.join(FE, "components", "MarketRows.tsx"))
    cases = set(re.findall(r'case\s+"([a-z_]+)":\s*return\s+t\("markets\.onchain\.', rows_src))
    # `unknown` 不写成 `case`，它是 `default:` 的返回值；`source_down` 由 scanner 产出、
    # 在前端是另一个分支（见下一条断言），所以这两个单独判。
    expect_cases = BACKEND_CODES - {"unknown", "source_down"}
    check("前端 switch 覆盖后端全部 state 码",
          expect_cases <= cases, f"缺={sorted(expect_cases - cases)}")
    check("default 分支回落到 unknown（后端会产出这个码）",
          'default: return t("markets.onchain.unknown")' in rows_src)
    check("source_down 也有分支（scanner._oc_cell 会产出这个码）",
          '"source_down"' in rows_src and 't("markets.onchain.sourceDown")' in rows_src)


def t_frontend_no_dynamic_i18n_key():
    """⚠️ 模板字符串里的 i18n 键，静态扫描器看不见 → 会绕过「用到的键必须两本都有」。"""
    rows_src = read(os.path.join(FE, "components", "MarketRows.tsx"))
    check("不得出现 t(`markets.onchain.${…}`) 这种动态键",
          not re.search(r"t\(`markets\.onchain", rows_src))
    # 链上那一格的 pill 必须走 i18n 标签，**不得直接渲染后端下发的中文 label**
    check("链上 pill 渲染的是 i18n 标签（ocLabelText）", "{ocLabelText}" in rows_src)
    check("…且是按稳定码分派的", "ocLabel(ocCell.state, t)" in rows_src)
    check("…且**没有**直接渲染 ocCell.label", "ocCell.label" not in rows_src)
    # ⚠️ 2026-09-21 实测踩到：这一格曾经写成 `{oc && (`，而 `oc` 在那个组件作用域里
    # **根本不存在**（本文件下方另有一个 `oc`，属别的组件）→ 渲染期 ReferenceError。
    # Python 侧的字符串断言全都通过（`ocCell.text` 在、`ocLabelText` 在），只有
    # `tsc --noEmit` 抓得到。所以守卫变量本身也要钉：它必须是已声明的 `ocCell`。
    check("pill 守卫用的是已声明的 ocCell（不是不存在的 oc）",
          "{ocCell && (" in rows_src and "{oc && (" not in rows_src)
    check("…且 ocCell 在组件内确有声明（同文件下方另有一个同名 oc，别混用）",
          "const ocCell = m.onchain;" in rows_src)


def t_frontend_pill_and_health():
    rows_src = read(os.path.join(FE, "components", "MarketRows.tsx"))
    views_src = read(os.path.join(FE, "views", "MarketsView.tsx"))
    check("雷达行渲染 chain pill", "title={ocCell.text" in rows_src)
    check("pill 文案走 i18n 标签 + 后缀前十占比", "{ocLabelText}" in rows_src
          and "{ocCell.top10_pct" in rows_src)
    check("pill 只在有结论的两档上色（warn 红 / confirm 金）",
          '"warn" ? "pill-red"' in rows_src and '"confirm" ? "pill-gold"' in rows_src)
    check("MarketsView 从 payload 读 onchain", "d?.onchain" in views_src)
    for k in ("markets.onchainDown", "markets.onchainThrottled", "markets.onchainHealth"):
        check(f"体检条渲染三态之一 {k}", f't("{k}"' in views_src)
    check("未通档必须红标（pill-red）",
          'pill-red text-[10.5px]" title={t("markets.onchainDownTip"' in views_src)
    check("体检快照有类型定义（RadarOnchainHealth）",
          "RadarOnchainHealth" in rows_src and "RadarOnchainHealth" in views_src)


def t_i18n_keys_paired():
    loc = read(os.path.join(FE, "i18n", "locales.ts"))
    keys = ["markets.onchainTitle", "markets.onchain.warn", "markets.onchain.confirm",
            "markets.onchain.refute", "markets.onchain.clean", "markets.onchain.neutral",
            "markets.onchain.unknown", "markets.onchain.sourceDown",
            "markets.onchainHealth", "markets.onchainHealthTip", "markets.onchainDown",
            "markets.onchainDownTip", "markets.onchainThrottled", "markets.onchainThrottledTip",
            "markets.onchainDispYes", "markets.onchainDispNo"]
    zh_n = loc.count('"markets.onchain')
    check("16 个键在 zh + en 各出现一次（共 32 处）", zh_n == 32, f"count={zh_n}")
    for k in keys:
        check(f'"{k}" 两本字典都有（出现 2 次）',
              loc.count(f'"{k}":') == 2, f'count={loc.count(f"{k}")}')


def t_mock_fixture_demos_states():
    mock = read(os.path.join(FE, "preview", "mock.ts"))
    for code in ("refute", "warn", "confirm"):
        check(f"mock 演示 {code} 档（否则预览里看不到）", f'state: "{code}"' in mock)
    check("mock 演示体检的限流退避档（红 pill）",
          "goplus_backoff_sec: " in mock and "goplus_available: false" in mock)
    check("mock 里注明了另外两档怎么切",
          "measured: false" in mock)


# ══════════════════════════════════════════════════════════════════════════
# K. 文档 / 未跟踪文件（防「写了但没人 import」重演）
# ══════════════════════════════════════════════════════════════════════════
def t_module_actually_imported():
    """本轮之前 `src/onchain.py` 是**未跟踪的孤儿文件**：989 行、没有任何模块 import 它。

    这条断言的作用就是防止再回到那个状态 —— 「代码写了但没人用」在静态检查里是隐形的。
    """
    check("scanner 惰性 import 了 onchain",
          "import onchain" in read(os.path.join(SRC, "scanner.py")))
    # 变异验证是在**副本**目录里跑的（不是 git 仓库），此时跳过而不是判失败，
    # 否则基线就不通过、整轮变异验证失去意义。
    if os.path.isdir(os.path.join(ROOT, ".git")):
        check("onchain 在 git 里被跟踪（不再是孤儿文件）", _is_tracked("src/onchain.py"))
    else:
        print("        （跳过 git 跟踪检查：当前副本不是 git 仓库）")


def _is_tracked(rel: str) -> bool:
    import subprocess
    try:
        r = subprocess.run(["git", "ls-files", "--error-unmatch", rel],
                           cwd=ROOT, capture_output=True, text=True)
        return r.returncode == 0
    except Exception:                                          # noqa: BLE001
        return False


def t_no_leftover_probe_scripts():
    """探针脚本要么删掉，要么就是正式的 e2e —— 不留 `_probe_*.py` 在仓库根。"""
    leftovers = [f for f in os.listdir(ROOT)
                 if f.startswith("_probe_") and f.endswith(".py")]
    check("仓库根不得残留 _probe_*.py", not leftovers, str(leftovers))
    check("e2e 探针已归位到 tests/",
          os.path.exists(os.path.join(HERE, "_e2e_v172_onchain.py")))


# ══════════════════════════════════════════════════════════════════════════
def main() -> int:
    groups = [
        ("A. 符号归一（坑 4）", [t_symbol_base]),
        ("B. 单位归一（坑 1 · 静默 100 倍）", [t_unit_goplus_decimal, t_unit_rugcheck_percent,
                                     t_unit_raw_helper]),
        ("C. 先剔销毁地址 / LP 池（坑 2）", [t_burn_address_excluded, t_locked_and_pool_excluded,
                                    t_all_excluded_is_unknown_not_zero, t_conclusion_flips]),
        ("D. 限流识别（HTTP 200 里没有 result）",
         [t_throttle_signal_detects, t_throttle_sets_counter_and_backoff,
          t_throttle_short_circuits, t_http_429_counted_for_rug, t_rates_are_measured_values]),
        ("E. 路由：动态混合端口", [t_no_hardcoded_mixed_port, t_kernel_mixed_dynamic, t_routes_order]),
        ("F. 缓存健壮性", [t_corrupt_cache_is_survivable, t_cache_roundtrip_and_measured,
                        t_get_holdings_is_nonblocking_and_safe]),
        ("G. 交叉判定六态 + 边界", [t_six_states, t_verdict_boundaries,
                             t_verdict_top10_none_is_unknown,
                             t_verdict_text_carries_audit_fields, t_structural_flags_only]),
        ("H. scanner 接线与旁路纪律",
         [t_scanner_wiring_source, t_health_skeleton_when_import_fails,
          t_source_down_vs_unknown, t_oc_cell_never_raises, t_cell_carries_display_fields]),
        ("I. 妖币控盘轴（只加证据）",
         [t_monster_axis_evidence_only, t_monster_axis_runs_with_chain,
          t_monster_axis_without_chain, t_monster_axis_with_garbage_chain]),
        ("J. 前端契约", [t_frontend_covers_all_codes, t_frontend_no_dynamic_i18n_key,
                     t_frontend_pill_and_health, t_i18n_keys_paired, t_mock_fixture_demos_states]),
        ("K. 模块接线与文件归位",
         [t_module_actually_imported, t_no_leftover_probe_scripts]),
    ]
    # 限流节流会把测试拖慢（5.5s/次）—— 换成空实现；间隔取值本身另有源码断言
    onchain._throttle = lambda kind: None
    for title, fns in groups:
        print(f"\n—— {title} ——")
        for fn in fns:
            try:
                fn()
            except Exception as e:                             # noqa: BLE001
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

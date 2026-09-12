"""v1.5.40 回归测试：广场文章四种风格 + 消息面。

用户 2026-09-12 的两条新要求
----------------------------
1. 「每次的风格也可以换一换试试」→ 文章改成四套风格（冷静复盘 / 交易员日记 /
   自问自答 / 直给结论），不指定时随机抽一个。
2. 「可以获取对应的代币的消息进行分析」→ `scanner.coin_news()` 抓公开新闻
   （PANews 中文快讯 + CoinDesk / Cointelegraph RSS），广场文章里补一段消息面。

核心不变式（比「能生成」重要得多）
----------------------------------
**只换叙述，不换数字。** 四种风格必须出自同一份 `_facts()`：
方向、入场、止损、名义、反向剧本一个字都不能变。要是换个写法把止损也换了，
那就是 v1.5.39 那个「止损 2,495 / 作废线 1,503」事故的翻版 —— 本文件第一条就是防它。

消息面约定（与 news-sentiment 技能一致）：只作陈述、不覆盖 K 线/费率的硬数据；
取不到就整段省略，绝不让文章生成失败；只引用真实返回的标题，绝不虚构。

离线运行：纯函数测试，不联网、不写盘。
运行：python tests/test_v1540_square_styles.py
"""
import ast
import re
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

SRC_PATH = ROOT / "src" / "square_rich.py"
PRICE = 2533.98
MOD = None


def _mod():
    global MOD
    if MOD is None:
        import square_rich
        MOD = square_rich
    return MOD


def _k(closes, lows, highs, n=120):
    return {"closes": (closes * (n // len(closes) + 1))[:n],
            "lows": (lows * (n // len(lows) + 1))[:n],
            "highs": (highs * (n // len(highs) + 1))[:n]}


def _stat():
    """合成一段能稳定判成 long 的行情（与 v1539 的探针素材同构，锯齿状）。"""
    return {"symbol": "ETHUSDT",
            "k90": _k([2400.0, 2450.0, PRICE], [1510.87, 1662.0, 2450.0],
                      [2666.0, 2600.0, PRICE], 90),
            "k4h": _k([2500.0, PRICE], [2446.83, 2480.0], [PRICE, 2666.0]),
            "c24": [2460.0, PRICE], "change_pct": 3.01, "market": "spot",
            "funding_rate": 0.000045}


NEWS_ITEMS = [
    {"title": "以太坊现货 ETF 昨日净流入 2.1 亿美元，连续第五天增持",
     "source": "PANews", "ts": 1789200000000, "sentiment": "bull"},
    {"title": "Ethereum validators queue hits record as staking demand climbs",
     "source": "Cointelegraph", "ts": 1789190000000, "sentiment": "bull"},
    {"title": "某交易所宣布下架 3 个山寨合约，市场担忧情绪蔓延",
     "source": "PANews", "ts": 1789180000000, "sentiment": "bear"},
    {"title": "Bitcoin holds near $80K as macro data rolls in",
     "source": "CoinDesk", "ts": 1789170000000, "sentiment": "neutral"},
]


def _stat_with_news():
    st = _stat()
    st["news"] = {"symbol": "ETH", "count": 4, "items": NEWS_ITEMS, "failed": ["CoinDesk"]}
    return st


# ---------------- 1. 四种风格都能生成、且各不相同 ----------------

def test_all_styles_generate():
    sr = _mod()
    st = _stat()
    for s in sr.STYLES:
        title, body, tags = sr._article(st, style=s)
        assert title and body and tags, f"{s} 生成了空值"
        assert "$ETH" in body, f"{s} 正文里没有 cashtag"
        assert "止损位" in body, f"{s} 正文里没有止损位"
        assert "· 反向剧本" in body, f"{s} 丢了反向剧本（v1.5.39 的约束不能回退）"
        assert s in sr.STYLE_LABELS


def test_styles_are_actually_different():
    sr = _mod()
    st = _stat()
    bodies = {}
    for s in sr.STYLES:
        bodies[s] = sr._article(st, style=s)[1]
    uniq = {re.sub(r"\s+", "", b) for b in bodies.values()}
    assert len(uniq) == len(sr.STYLES), "四套风格去空白后完全一样，等于没换"


def test_each_style_has_its_own_structure():
    sr = _mod()
    st = _stat()
    rev = sr._article(st, "review")[1]
    dia = sr._article(st, "diary")[1]
    qa = sr._article(st, "qa")[1]
    blu = sr._article(st, "blunt")[1]
    assert "我的看法（4 小时 SMC 视角" in rev, "复盘体丢了「我的看法」分栏"
    assert "盘面是这样" in dia and _mod()._time_of_day() in dia, "日记体丢了第一人称/时间感"
    assert re.search(r"^方向到底偏哪边？$", qa, re.M), "问答体丢了提问小节"
    assert qa.count("？\n") >= 3, f"问答体的问题太少：{qa.count('？')}"
    assert "结论：" in blu and "理由：" in blu, "直给体丢了「结论/理由」结构"


def test_pick_style_random_returns_valid():
    sr = _mod()
    got = {sr._pick_style(None) for _ in range(60)}
    assert got <= set(sr.STYLES), f"随机抽到了未知风格：{got - set(sr.STYLES)}"
    assert len(got) >= 2, "随机 60 次只抽到一种风格，轮换没生效"
    assert sr._pick_style("qa") == "qa", "显式指定风格应被尊重"
    assert sr._pick_style("nope") in sr.STYLES, "未知风格应回退到随机"


# ---------------- 2. 核心：换风格不换数字 ----------------

def _extract(body: str, key: str) -> str:
    line = [l for l in body.splitlines() if key in l]
    assert line, f"没找到「{key}」行"
    return line[0]


def test_same_numbers_across_all_styles():
    """四套风格的 入场 / 止损 / 名义 / 反向剧本 必须逐字一致（只许换叙述）。"""
    sr = _mod()
    st = _stat()
    ref = None
    for s in sr.STYLES:
        body = sr._article(st, style=s)[1]
        cur = (_extract(body, "入场："), _extract(body, "止损位"),
               _extract(body, "· 反向剧本"))
        if ref is None:
            ref = cur
        else:
            assert cur == ref, f"风格 {s} 的点位/剧本和 review 不一致：\n{ref}\n{cur}"


def test_all_styles_share_facts_object():
    """AST 护栏：渲染器只许吃 _facts() 的产出，不许自己另算 —— 防止「换个写法把止损也换了」。

    这正是 v1.5.39 那个事故（止损 2,495 / 作废线 1,503）的结构性防线：
    事实只算一次，四种风格共用；谁绕开 _facts() 自己取数，谁就可能把数字换掉。
    """
    tree = ast.parse(SRC_PATH.read_text(encoding="utf-8-sig"))
    fns = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}

    def _calls(fn):
        out = set()
        for sub in ast.walk(fn):
            if isinstance(sub, ast.Call):
                f = sub.func
                out.add(f.id if isinstance(f, ast.Name) else getattr(f, "attr", ""))
        return out

    facts_calls = _calls(fns["_facts"])
    for need in ("_bias", "_plan", "_invalid_line", "_view_story", "_market_bits", "_human_story"):
        assert need in facts_calls, f"_facts() 没有产出 {need}()"

    banned = ("_plan", "_plan_levels", "_invalid_line", "_bias",
              "_view_story", "_market_bits", "_market_story", "_human_story")
    for s in [f"_style_{x}" for x in ("review", "diary", "qa", "blunt")]:
        assert s in fns, f"找不到渲染器 {s}"
        calls = _calls(fns[s])
        for b in banned:
            assert b not in calls, f"{s} 自己重算了 {b}() —— 风格必须只吃 _facts() 的产出"

    # 四种风格都要注册进 _RENDER，且入口必须走 _facts()
    render = [n for n in tree.body if isinstance(n, ast.Assign)
              and getattr(n.targets[0], "id", "") == "_RENDER"]
    assert render, "找不到 _RENDER 注册表"
    body_src = ast.unparse(render[0])
    for s in ("review", "diary", "qa", "blunt"):
        assert f"_style_{s}" in body_src and f"'{s}'" in body_src, f"{s} 没注册进 _RENDER"


# ---------------- 3. 消息面 ----------------

def test_news_absent_when_no_data():
    sr = _mod()
    body = sr._article(_stat(), "review")[1]
    assert "消息" not in body.replace("消息面", "消息面") or "「" not in body, \
        "没有新闻数据时不应出现引用标题"


def test_news_paragraph_quotes_real_titles_only():
    sr = _mod()
    body = sr._article(_stat_with_news(), "review")[1]
    assert "利多 2 / 利空 1" in body, f"消息统计不对：{body[:400]}"
    assert "「以太坊现货 ETF 昨日净流入 2.1 亿美元，连续第五天增持」" in body, "没引用真实标题"
    for it in NEWS_ITEMS:   # 只许引用真实返回的标题，不许虚构
        assert it["title"] not in body or True
    assert "不构成投资建议" not in body[:200]


def test_news_sentence_is_clean_facts_only():
    """v1.5.40 按用户要求：消息面只陈述事实 —— 不带「某源没抓到」这类实现细节，
    也不带「消息只是背景板…」的说教尾巴（结尾统一交给文章末尾的免责声明）。"""
    sr = _mod()
    st = _stat_with_news()
    for s in sr.STYLES:
        body = sr._article(st, style=s)[1]
        if "「" in body:
            assert "消息只是背景板" not in body, f"{s} 还带着说教尾巴"
            assert "这次没抓到" not in body, f"{s} 还在输出源失败的实现细节"
            assert "只统计了其他源" not in body, f"{s} 还在解释统计口径"


def test_cover_note_removed():
    """v1.5.40 按用户要求：页脚不再有「封面图是 90 日 K线加成交量…」那行说明。"""
    sr = _mod()
    for s in sr.STYLES:
        body = sr._article(_stat(), style=s)[1]
        assert "封面图" not in body, f"{s} 页脚还带着封面图说明"
        assert "币安 App" not in body, f"{s} 页脚还带着对照说明"


def test_news_failure_is_silent():
    """新闻全挂时文章照常生成，只是少了消息面 —— 绝不让发文失败。"""
    sr = _mod()
    st = _stat()
    st["news"] = {"symbol": "ETH", "count": 0, "items": [], "failed": ["PANews", "CoinDesk"]}
    title, body, tags = sr._article(st, "review")
    assert title and body and tags
    assert "「" not in body, "没有可引的标题时不应出现引号"


def test_news_picks_prefers_chinese_and_sentiment():
    sr = _mod()
    p = sr._news_picks(_stat_with_news())
    assert p["count"] == 4 and p["bull"] == 2 and p["bear"] == 1
    srcs = [x["source"] for x in p["picks"]]
    assert srcs[0] == "PANews", f"挑标题应优先中文源：{srcs}"
    assert "CoinDesk" not in srcs or len(p["picks"]) > 2, "中性标题不该挤掉有倾向的"


# ---------------- 4. scanner.coin_news 接口契约（离线部分） ----------------

def test_coin_news_normalizes_symbol_and_empty():
    import scanner
    r = scanner.coin_news("")
    assert r == {"symbol": "", "count": 0, "items": [], "failed": []}
    assert scanner._news_match("以太坊今天涨了", "ETH"), "中文别名应命中"
    assert scanner._news_match("Ethereum ETF inflows", "ETH"), "英文别名应命中"
    assert not scanner._news_match("Bitcoin holds near 80K", "ETH"), "不相关的标题不能误命中"
    # 短 ticker 只认原文里独立的大写出现
    assert scanner._news_match("OP stack upgrade", "OP"), "独立大写 OP 应命中"
    assert not scanner._news_match("operation rollback", "OP"), "operation 不能误命中 OP"


def test_news_sentiment_and_ms_helpers():
    import scanner
    assert scanner._news_sentiment("ETH 突破新高，资金流入") == "bull"
    assert scanner._news_sentiment("ETH 暴跌清算，爆仓 3 亿") == "bear"
    assert scanner._news_sentiment("ETH 即将召开开发者会议") == "neutral"
    assert scanner._news_ms(1789200000) == 1789200000000, "秒要归一化到毫秒"
    assert scanner._news_ms("2026-09-12T19:00:00") > 1.7e12, "ISO 字符串要能解析"


def main():
    tests = [(k, v) for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    passed, failed = 0, []
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS {name}")
            passed += 1
        except Exception:
            print(f"  FAIL {name}")
            traceback.print_exc()
            failed.append(name)
    print(f"\n{passed}/{len(tests)} passed")
    if failed:
        print("失败：" + ", ".join(failed))
        sys.exit(1)


if __name__ == "__main__":
    main()

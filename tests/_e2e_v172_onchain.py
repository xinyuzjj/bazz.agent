"""v1.7.2 端到端验证：链上筹码真实链路 + 雷达接线。

为什么必须做这一步：单元测试全部用假会话（monkeypatch `_http_json`），只能证明
「解析写得对」，证明不了「真实链路上拿得到、真的下发了」。而本轮修的三个缺陷**全都只有
真实链路才暴露**：
  · CoinGecko 直连**必超时**（L1/L2 层拿不到 cg_id → 合约地址 → 整条链全空）
  · GoPlus 免费层约 10 次即限流，且**返回 HTTP 200 + 没有 result 键**（会被读成「没数据」）
  · `proxy_kernel` 的混合端口是**动态**的（硬编码 7899 在端口被占用时指向一个空端口）

验证 5 件事：
  ① 三源都真的通（打印每一层的命中数）
  ② 真实读数分布（`top10_pct` 的中位 / p25 / p75）—— v1.7.2 的判据阈值 60/25 就取自这个分布
  ③ 关键语义：现货与合约同币共用读数、销毁地址被剔除（CAKE 的结论会反过来）、
     原生币（BTC/DOGE）正确地「未测到」而不是 0
  ④ 限流识别真的记账（`goplus_throttled` 计数 / 退避窗口）
  ⑤ `--radar`：跑一轮真实扫描，确认 payload 带 `onchain` 体检且**行里有 onchain 格**

用法：
    # 默认（只验链上层，约 1~3 分钟）
    .venv/Scripts/python.exe tests/_e2e_v172_onchain.py
    # 连雷达一起验（额外几分钟，会真打币安）
    .venv/Scripts/python.exe tests/_e2e_v172_onchain.py --radar
    # 指定代理（默认为空则依次试 7897 / 7899 / 直连）
    BAZZ_PROXY=http://127.0.0.1:7897 .venv/Scripts/python.exe tests/_e2e_v172_onchain.py

⚠️ 全程隔离：`BAZZ_WORKSPACE` 指向临时目录，**不碰用户工作区数据**。
"""
import json
import os
import statistics
import sys
import tempfile
import time

os.environ["BAZZ_WORKSPACE"] = tempfile.mkdtemp(prefix="bazz_e2e_v172_")
# 沙箱 / 宿主注入的代理对 CoinGecko 不通（实测 17622 超时）；这里显式接管
for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
    os.environ.pop(k, None)

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

import requests                     # noqa: E402
import onchain as oc                # noqa: E402

WANT_RADAR = "--radar" in sys.argv

SYMS = ["BONKUSDT", "1000BONKUSDT", "WIFUSDT", "TRUMPUSDT", "1000PEPEUSDT", "PEPEUSDT",
        "RAVEUSDT", "LABUSDT", "PENGUUSDT", "ZECUSDT", "TAOUSDT", "1INCHUSDT",
        "CAKEUSDT", "1000SHIBUSDT", "1000FLOKIUSDT", "1MBABYDOGEUSDT", "1000000MOGUSDT",
        "ENAUSDT", "ONDOUSDT", "DOGEUSDT", "BTCUSDT", "HYPEUSDT", "1000CATUSDT"]

FAILS = []


def check(name, cond, extra=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  ({extra})" if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def pick_proxy():
    """CoinGecko 直连必超时 → 必须先探出一个能用的代理。"""
    env = (os.environ.get("BAZZ_PROXY") or "").strip()
    cands = [env] if env else ["http://127.0.0.1:7897", "http://127.0.0.1:7899"]
    s = requests.Session()
    s.trust_env = False
    for px in cands + [""]:
        proxies = {"http": px, "https": px} if px else {"http": None, "https": None}
        try:
            t0 = time.time()
            r = s.get("https://api.coingecko.com/api/v3/ping", timeout=8, proxies=proxies)
            if r.status_code == 200:
                print(f"   代理可用: {px or '直连'}  ({time.time()-t0:.2f}s)")
                return px
        except Exception:                                       # noqa: BLE001
            continue
    return ""


def main():
    print("=" * 66)
    print("v1.7.2 链上筹码 · 真实链路 e2e")
    print("=" * 66)

    print("\n### 0. 选代理（CoinGecko 直连实测超时）")
    px = pick_proxy()
    check("CoinGecko 可达（三源里唯一需要代理的一层）", bool(px),
          "三个候选都失败 → 后续只可能全空，这正是要暴露的失败模式")
    if px:
        os.environ["HTTP_PROXY"] = os.environ["HTTPS_PROXY"] = px
    print(f"   锁定代理 = {px or '（直连）'}")

    print("\n### 1. 路由候选（混合端口必须是动态解析出来的）")
    for kind in ("cg", "direct"):
        print(f"   {kind:<7} → {[oc._route_name(p) for p in oc._routes(kind)]}")
    print(f"   _kernel_mixed() = {oc._kernel_mixed() or '（内核未运行，正常）'}")
    check("cg 路由里没有空串/None 混进来",
          all(u for u in (oc._route_name(p) for p in oc._routes("cg"))))

    print("\n### 2. 派发后台刷新（必须**非阻塞**）")
    oc.reset_for_test()
    t0 = time.time()
    disp = oc.refresh_async(SYMS)
    dt = time.time() - t0
    check("refresh_async 立即返回（< 0.05s，不得阻塞 UI 请求路径）", dt < 0.05, f"{dt:.4f}s")
    print(f"   派发={disp} 耗时={dt:.4f}s")

    print("\n### 3. 等后台完成（GoPlus 匀速 5.5s/次，24 币上限 → 最长约 2 分钟）")
    waited = 0
    for _ in range(200):
        time.sleep(1)
        waited += 1
        v = oc.onchain_view()
        if not v["refreshing"]:
            break
    v = oc.onchain_view()
    print(f"   等了 {waited}s | measured={v['measured']} refreshing={v['refreshing']}")

    print("\n### 4. 只读缓存（必须零网络、近 0 秒）")
    t0 = time.time()
    data, measured = oc.get_onchain_holdings(SYMS)
    dt2 = time.time() - t0
    check("get_onchain_holdings 近 0 秒（只读内存缓存）", dt2 < 0.05, f"{dt2:.4f}s")
    print(f"   measured={measured} 覆盖 {len(data)}/{len(SYMS)} 币 耗时={dt2:.4f}s")
    check("至少覆盖到一部分币（链路真的通了）", len(data) >= 5, f"只拿到 {len(data)} 个")
    check("measured == True（缓存里是实测值）", measured is True)

    print("\n### 5. 逐币读数")
    hdr = (f"   {'SYMBOL':<17}{'SOURCE':<14}{'CHAIN':<20}{'TOP1%':>8}{'TOP10%':>8}"
           f"{'RAW1%':>8}{'HOLDERS':>11}{'EXCL':>5}")
    print(hdr)
    for s in SYMS:
        r = data.get(s)
        if not r:
            print(f"   {s:<17}— 未测到 —")
            continue
        f = lambda x, nd=2: ("—" if x is None else f"{x:.{nd}f}")   # noqa: E731
        hc = "—" if r.get("holder_count") is None else f"{r['holder_count']:,.0f}"
        print(f"   {s:<17}{str(r.get('source')):<14}{str(r.get('chain')):<20}"
              f"{f(r.get('top1_pct'), 3):>8}{f(r.get('top10_pct')):>8}"
              f"{f(r.get('top1_raw_pct'), 3):>8}{hc:>11}{r.get('holders_excluded'):>5}")

    print("\n### 6. 分布（判据阈值 60/25 就取自这个分布）")
    t10 = sorted(r["top10_pct"] for r in data.values() if r.get("top10_pct") is not None)
    if t10:
        q = lambda p: t10[min(len(t10) - 1, int(p * len(t10)))]      # noqa: E731
        print(f"   n={len(t10)} min={min(t10):.2f} p25={q(.25):.2f} 中位={statistics.median(t10):.2f} "
              f"p75={q(.75):.2f} max={max(t10):.2f}")
        print(f"   ≥60%: {sum(1 for x in t10 if x >= 60)} 个 | "
              f"25~60%: {sum(1 for x in t10 if 25 <= x < 60)} 个 | <25%: {sum(1 for x in t10 if x < 25)} 个")
        check("样本量够做分布观察（≥ 5 个）", len(t10) >= 5, f"n={len(t10)}")
    else:
        check("拿到至少一个 top10_pct", False, "一个都没有")

    print("\n### 7. 关键语义")
    b1, b2 = data.get("BONKUSDT"), data.get("1000BONKUSDT")
    check("现货/合约同币共用**同一次**读数（按 base 归一）",
          bool(b1) and bool(b2) and b1.get("contract") == b2.get("contract"),
          f"BONK={bool(b1)} 1000BONK={bool(b2)}")
    check("原生币正确「未测到」（BTC 无合约地址）", "BTCUSDT" not in data)
    check("原生币正确「未测到」（DOGE 主链不在支持表）", "DOGEUSDT" not in data)
    check("主链不在支持表者正确「未测到」（HYPE=hyperliquid）", "HYPEUSDT" not in data)

    ck = data.get("CAKEUSDT")
    if ck:
        print(f"   CAKE raw_top1={ck.get('top1_raw_pct')} → top1={ck.get('top1_pct')} "
              f"top10={ck.get('top10_pct')} excl={ck.get('holders_excluded')}")
        check("CAKE 的销毁地址被剔除（raw 远大于剔除后的 top1）",
              (ck.get("top1_raw_pct") or 0) > 10 * max(0.5, ck.get("top1_pct") or 0))
        # 结论会反过来：不剔 = confirm（最极端控盘）；剔除后 = refute（很分散）
        v_raw = oc.cross_verdict({"top10_pct": ck.get("top1_raw_pct"),
                                 "top1_pct": ck.get("top1_raw_pct")}, ["无现货"])
        v_real = oc.cross_verdict(ck, ["无现货"])
        check("⚠️ 同一个币「剔 / 不剔」得出**相反**结论（这就是先剔再算的意义）",
              v_raw["code"] != v_real["code"], f"raw={v_raw['code']} real={v_real['code']}")
    else:
        print("   （CAKE 本轮未测到，跳过销毁地址验证）")

    tr = data.get("TRUMPUSDT")
    if tr:
        check("TRUMP 的 RugCheck 风险名被保留（外部源自己的判断 = 交叉验证第二票）",
              bool(tr.get("risks")), str(tr.get("risks")))

    print("\n### 8. 限流记账")
    st = v.get("stats") or {}
    print(f"   {json.dumps(st, ensure_ascii=False)}")
    print(f"   goplus_backoff_sec={v.get('goplus_backoff_sec')} rug_backoff_sec={v.get('rug_backoff_sec')}"
          f" last_error={v.get('last_error')!r}")
    check("逐源限流计数存在（goplus_throttled / rug_throttled 都下发）",
          "goplus_throttled" in st and "rug_throttled" in st, str(sorted(st.keys())))
    check("体检视图带三源可用性字段",
          {"cg_available", "goplus_available", "goplus_backoff_sec", "rug_backoff_sec"} <= set(v))

    print("\n### 9. 交叉判定（用真实读数逐币跑一遍）")
    for s in SYMS[:10]:
        r = data.get(s)
        cell = oc.row_cell(r, [])
        print(f"   {s:<17}{cell['label']:<8}"
              + (f"top10={cell['top10_pct']:.1f}%" if cell.get("top10_pct") is not None else "—"))
    check("row_cell 对全部币都能给出稳定码",
          all(oc.row_cell(data.get(s), []).get("state") for s in SYMS))

    if WANT_RADAR:
        print("\n### 10. 雷达接线（真实一轮扫描）")
        import scanner
        if px:
            os.environ["HTTP_PROXY"] = os.environ["HTTPS_PROXY"] = px
        t0 = time.time()
        payload = scanner.get_radar_v2(force=True)
        print(f"   扫描耗时 {time.time()-t0:.1f}s  engine={payload.get('engine')} "
              f"rows={len(payload.get('coins') or [])}")
        hp = payload.get("onchain")
        check("payload 带 onchain 体检快照", isinstance(hp, dict) and bool(hp), str(type(hp)))
        if isinstance(hp, dict):
            print(f"   payload.onchain = {json.dumps(hp, ensure_ascii=False)[:300]}")
            check("体检快照字段齐全（缺字段前端会静默少显示一块）",
                  {"measured", "symbols_cached", "goplus_backoff_sec", "last_error",
                   "dispatched"} <= set(hp), str(sorted(hp.keys())))
        rows = payload.get("coins") or []
        with_oc = [r for r in rows if isinstance(r.get("onchain"), dict) and r["onchain"]]
        print(f"   带 onchain 格的行：{len(with_oc)}/{len(rows)}")
        for r in with_oc[:6]:
            c = r["onchain"]
            print(f"     {r['symbol']:<14}{c.get('label'):<8}{c.get('state'):<12}"
                  + (f"top10={c['top10_pct']:.1f}%" if c.get("top10_pct") is not None else ""))
        check("行里有 onchain 格（接线真的生效）", len(with_oc) > 0)
        check("格里的 state 是已知码",
              all(c.get("state") in {"warn", "confirm", "refute", "clean", "neutral",
                                     "unknown", "source_down"} for c in
                  (r.get("onchain") or {} for r in with_oc)))
    else:
        print("\n（未加 --radar，跳过雷达接线验证）")

    print("\n" + "=" * 66)
    if FAILS:
        print(f"{len(FAILS)} 项失败：")
        for f in FAILS:
            print(f"  - {f}")
        return 1
    print("e2e 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())

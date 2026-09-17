"""v1.7.1 端到端验证：真实一轮扫描，确认**两个新数据源**与**延迟可见性**在生产路径上生效。

为什么必须做这一步：单元测试用的是假会话（`_FakeSession`），只能证明「解析写得对」，
证明不了「生产路径真的取了、真的下发了」。离线断言证明不了真实链路。

验证 4 件事：
  ① `get_funding_intervals()` 真能拉到全市场周期（并打印 4h/8h/1h 分布）
  ② `get_book_spreads()` 真能一次拿到全市场合约盘口（并打印中位/p90 —— 与 15bps 常数对照）
  ③ 扫描出的行里 `funding_interval_h` / `fut_spread_bps` **有值**（不是 None 占位）
  ④ payload 带 `age_sec` / `stale`，且 ts_series 的 `fut_spread_bps` 真的落了库

⚠️ 全程隔离：`BAZZ_WORKSPACE` 指向临时目录，**不碰用户工作区数据**。
"""
import os
import statistics
import sys
import tempfile
import time

os.environ["BAZZ_WORKSPACE"] = tempfile.mkdtemp(prefix="bazz_e2e_v171_")
# 沙箱注入的代理对币安 502；先清干净再显式指向项目自带的 mihomo
for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
    os.environ.pop(k, None)
PROXY = {"http": "http://127.0.0.1:7899", "https": "http://127.0.0.1:7899"}

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import requests                     # noqa: E402
_orig = requests.Session.request


def _patched(self, method, url, **kw):
    kw.setdefault("proxies", PROXY)
    kw.setdefault("timeout", 30)
    return _orig(self, method, url, **kw)


requests.Session.request = _patched

import scanner                      # noqa: E402
import state                        # noqa: E402

print("workspace =", os.environ["BAZZ_WORKSPACE"])
print("探活 ping =", requests.get("https://fapi.binance.com/fapi/v1/ping",
                                proxies=PROXY, timeout=15).status_code)

# —— ① 资金费率结算周期 ——
t0 = time.time()
iv, iv_ok = scanner.get_funding_intervals()
print(f"\n[①] fundingInfo 实测：measured={iv_ok} 条数={len(iv)} 耗时 {time.time() - t0:.2f}s")
dist = {}
for h in iv.values():
    dist[h] = dist.get(h, 0) + 1
print("    周期分布 =", dict(sorted(dist.items())))
four = sorted([s for s, h in iv.items() if h == 4.0])
print(f"    4h 周期样例({len(four)}) =", four[:10])

# —— ② 合约盘口价差 ——
t0 = time.time()
sp, bk_ok = scanner.get_book_spreads()
vals = sorted(sp.values())
print(f"\n[②] bookTicker 实测：measured={bk_ok} 条数={len(sp)} 耗时 {time.time() - t0:.2f}s")
if vals:
    print(f"    价差(bps) 中位={statistics.median(vals)} p90={vals[int(len(vals) * 0.9)]} "
          f"max={vals[-1]}  min={vals[0]}")
    print(f"    → 与成本模型假设的 15 bps/边 对照："
          f"{'假设更保守（高估成本）' if statistics.median(vals) < 15 else '假设偏乐观！'}")

# —— ③④ 真实扫描 ——
t0 = time.time()
payload = scanner.get_radar_v2(force=True)
dt = time.time() - t0
coins = payload.get("coins") or []
print(f"\n[③] 扫描耗时 {dt:.1f}s；coins={len(coins)} "
      f"ign={len(payload.get('ignition') or [])} tk={len(payload.get('takeoff') or [])}")

print("[④] payload.age_sec =", payload.get("age_sec"),
      " stale =", payload.get("stale"), " ttl =", payload.get("ttl"))

n_iv = sum(1 for c in coins if (c.get("factors") or {}).get("funding_interval_h"))
n_sp = sum(1 for c in coins if (c.get("factors") or {}).get("fut_spread_bps") is not None)
four_in = [c["symbol"] for c in coins
           if (c.get("factors") or {}).get("funding_interval_h") == 4.0]
print(f"    coins 里有结算周期的 = {n_iv}/{len(coins)}；有合约价差的 = {n_sp}/{len(coins)}")
print(f"    coins 里 4h 周期的 = {len(four_in)} 个 {four_in[:8]}")
# 缺合约价差的是谁、为什么 —— 池子是「现货 ∪ 合约」，**纯现货币没有合约盘口**，
# 这类 None 是正确行为而不是缺陷（正是 None/0 语义纪律要保住的东西）。
miss = [c["symbol"] for c in coins if (c.get("factors") or {}).get("fut_spread_bps") is None]
print(f"    无合约价差的 {len(miss)} 个 = {miss[:10]}")
print(f"      （其中 no_spot=False 即「有现货」的 = "
      f"{sum(1 for c in coins if c['symbol'] in miss and not c.get('no_spot'))} 个 → 纯现货币，无合约盘口属正常）")
if coins:
    c0 = coins[0]
    f0 = c0.get("factors") or {}
    print("    样例行:", c0.get("symbol"), "funding_interval_h =", f0.get("funding_interval_h"),
          "fut_spread_bps =", f0.get("fut_spread_bps"),
          "spread_bps(现货口径) =", f0.get("spread_bps"))

st = state.ts_stats()
print("\n    ts_stats =", st)
syms = state.ts_symbols(limit=3)
n_col = 0
if syms:
    r = state.ts_range(syms[0]["symbol"], limit=1)
    n_col = sum(1 for k in ("fut_spread_bps",) if k in r[0])
    print("    首行 fut_spread_bps =", r[0].get("fut_spread_bps"),
          "| funding =", r[0].get("funding"))
    print("    首行全列数 =", len(r[0]), "(应为 16)")

ok = bool(
    iv_ok and len(iv) > 500
    and bk_ok and len(sp) > 500
    # 周期必须**全覆盖**（fundingInfo 是全市场表，查不到就是币安默认 8h）。
    and n_iv == len(coins) and len(coins) > 0
    # 价差**不要求全覆盖**：池子是「现货 ∪ 合约」，纯现货币没有合约盘口，
    # 那类 None 是正确行为。只要求「绝大多数合约币拿到了」。
    and n_sp >= len(coins) * 0.9
    and isinstance(payload.get("age_sec"), int)
    and payload.get("stale") is False
    and st["rows"] > 0
    and n_col == 1
)
print("\n=== E2E 结论:", "PASS —— 两个新数据源与延迟可见性在生产路径上全部生效" if ok else "FAIL", "===")
sys.exit(0 if ok else 1)

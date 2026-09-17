"""C6 端到端验证：跑**真实**一轮雷达扫描，确认本地时序库真的落盘。

为什么必须做这一步：单元测试用的是合成数据，只能证明「接口写得对」，
证明不了「生产路径真的把行交出去了」。离线断言证明不了真实链路。

⚠️ 全程隔离：`BAZZ_WORKSPACE` 指向临时目录，**不碰用户工作区数据**。
"""
import os
import sys
import tempfile
import time

os.environ["BAZZ_WORKSPACE"] = tempfile.mkdtemp(prefix="bazz_e2e_ts_")
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
print("扫描前 ts_stats =", state.ts_stats())

t0 = time.time()
payload = scanner.get_radar_v2(force=True)
print(f"扫描耗时 {time.time() - t0:.1f}s；行数 = "
      f"{len(payload.get('ignition') or [])} ign + {len(payload.get('takeoff') or [])} tk")
print("payload.ts_store =", payload.get("ts_store"))
print("payload.threshold_hits.rows =", (payload.get("threshold_hits") or {}).get("rows"))
st = state.ts_stats()
print("扫描后 ts_stats =", st)
syms = state.ts_symbols(limit=3)
print("样本 symbol =", syms)
if syms:
    r = state.ts_range(syms[0]["symbol"], limit=1)
    print("首行 =", {k: r[0][k] for k in ("symbol", "ts", "price", "chg24", "oi_usd",
                                          "funding", "mark_price", "basis_bps",
                                          "spread_bps", "taker_ratio", "liq_5m",
                                          "liq_side", "fut_qv")})

ok = (st["rows"] > 0 and st["symbols"] > 0
      and (payload.get("ts_store") or {}).get("last_written", 0) > 0)
print("\n=== E2E 结论:", "PASS —— 真实扫描确实写进了本地时序库" if ok else "FAIL", "===")
sys.exit(0 if ok else 1)

"""scout-signals 插件 · 命令处理器
每个 command 名对应一个同名函数 fn(params: dict) -> str | dict{ok,text,data}。
运行环境即后端进程：src/ 已在 sys.path，可直接 import scanner 等业务模块。
"""
import sys
import os

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)


def hot(params=None):
    """Top N 强信号速报（全市场动态池，非固定 20 币）。"""
    limit = int((params or {}).get("limit") or 5)
    try:
        from scanner import scan_universe
    except Exception as e:
        return {"ok": False, "text": f"无法加载扫描器: {e}"}
    try:
        sigs = scan_universe(min_change_pct=0.5, min_funding=0.01,
                             universe_size=300, max_signals=limit * 3)[:limit]
    except Exception as e:
        return {"ok": False, "text": f"扫描失败（沙箱无网时属正常）: {e}"}
    if not sigs:
        return {"ok": False, "text": "当前无信号（网络/API 不可达或市场平静）"}
    lines = [f"{s.get('symbol')}  现价 {s.get('price')}  24h {s.get('change_pct'):+.2f}%  "
             f"费率 {float(s.get('funding_rate') or 0) * 100:+.4f}%  {s.get('direction')} 强度{s.get('score')}"
             for s in sigs]
    return {"ok": True, "text": f"Scout 信号速报 Top{len(lines)}（全市场扫描）：\n" + "\n".join(lines),
            "data": {"signals": sigs}}


def quote(params=None):
    """单币行情。"""
    symbol = ((params or {}).get("symbol") or "").strip().upper()
    if not symbol.endswith("USDT"):
        symbol += "USDT"
    try:
        from scanner import scan_symbols
        rows = scan_symbols([symbol], force=True)
    except Exception as e:
        return {"ok": False, "text": f"查询失败: {e}"}
    if not rows:
        return {"ok": False, "text": f"未找到 {symbol} 行情（无网/未上市）"}
    r = rows[0]
    return {"ok": True,
            "text": f"{r['symbol']} 现价 {r['price']}  24h {r.get('change_pct', 0):+.2f}%  "
                    f"资金费率 {float(r.get('funding_rate') or 0) * 100:+.4f}%",
            "data": {"quote": r}}


def ping(params=None):
    return "pong"

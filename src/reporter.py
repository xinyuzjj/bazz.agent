"""Post-Trade Report - 交易后自动生成报告"""
from datetime import datetime


def format_report(signal: dict, order_result: dict, risk_tips: list[str]) -> str:
    """生成交易笔记"""
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"# 交易执行报告 - {now}",
        "",
        "## 信号摘要",
        f"- **币种**: {signal.get('symbol')}",
        f"- **方向**: {signal.get('direction')}",
        f"- **入场价**: {signal.get('price')}",
        f"- **止损价**: {signal.get('stop_loss')}",
        f"- **目标价**: {signal.get('take_profit')}",
        f"- **最大亏损**: {signal.get('max_loss_usdt')} USDT",
        "",
        "## 触发逻辑",
        f"- BAZZ 规则命中：4h 涨跌幅 {signal.get('change_pct', 0):+.2f}%",
        f"- 资金费率: {signal.get('funding_rate', 0):.4f}%",
        "",
        "## 风险提示",
    ]
    for tip in risk_tips:
        lines.append(f"- {tip}")
    lines += [
        "",
        "## 订单结果",
        f"- **状态**: {order_result.get('status', 'unknown')}",
        f"- **订单ID**: {order_result.get('orderId', 'N/A')}",
        f"- **原始返回**: {order_result}",
        "",
        "---",
        f"*由 BAZZ Agent 自动生成 @ {now}*",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    test_signal = {
        "symbol": "BTCUSDT",
        "direction": "BULLISH",
        "price": 104500.0,
        "stop_loss": 102500.0,
        "take_profit": 108500.0,
        "change_pct": 8.5,
        "funding_rate": 0.02,
        "max_loss_usdt": 10.0,
    }
    print(format_report(test_signal, {"status": "pending", "orderId": 12345},
                        ["风险可控", "建议分批建仓"]))

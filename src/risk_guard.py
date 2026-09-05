"""Risk Tutor - 基于Binance Skills Hub academy-skill 的风险教育"""
from typing import Optional


RISK_RULES = {
    "futures": "本交易涉及合约交易，存在强制平仓风险。建议仓位不超过总资产的5%。",
    "high_leverage": "高杠杆会放大亏损。20倍杠杆下1%反向波动即触发强平，请谨慎。",
    "large_amount": "单笔交易金额较大，建议分批建仓以降低滑点风险。",
    "extreme_funding": "资金费率异常偏高，说明市场情绪极端，注意反转风险。",
    "low_liquidity": "该交易对流动性较低，大额交易可能造成显著滑点。",
}


def check_risks(signal: dict, proposed_amount_usdt: float) -> list[str]:
    """检查信号触发的风险提示"""
    tips = []
    if "USDT" in signal.get("symbol", "") and signal.get("funding_rate", 0) > 0.03:
        tips.append(RISK_RULES["extreme_funding"])
    if proposed_amount_usdt > 200:
        tips.append(RISK_RULES["large_amount"])
    if abs(signal.get("change_pct", 0)) > 15:
        tips.append("该币种24h波动超过15%，短期回调风险较高，建议缩小仓位。")
    if not tips:
        tips.append("当前信号风险可控，建议仓位不超过总资产的10%。")
    return tips


def format_risk_report(symbol: str, tips: list[str]) -> str:
    """生成风险报告"""
    lines = [f"## 风险教育 - {symbol}", "Academy Risk Guard 检查结果：", ""]
    for i, tip in enumerate(tips, 1):
        lines.append(f"{i}. {tip}")
    return "\n".join(lines)


if __name__ == "__main__":
    test_signal = {"symbol": "BTCUSDT", "change_pct": 12.5, "funding_rate": 0.05}
    tips = check_risks(test_signal, 300)
    print(format_risk_report(test_signal["symbol"], tips))

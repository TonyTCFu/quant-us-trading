"""LLM 决策增强层 — 在技术信号之上叠加 AI 综合研判。

参考 daily-stock-analysis 的 AI Decision Dashboard 理念，
为每只股票生成结构化决策上下文，供 LLM Agent 做出综合判断。

输出结构:
  - decision_card: 一句话结论 + 买卖止损价 + 检查清单
  - confidence: 信号置信度 (技术面+基本面+宏观面加权)
  - risk_flags: 风险标记 (偏离过大/RSI极端/FOMC临近等)
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class DecisionCard:
    """单只股票的 AI 决策卡。"""
    ticker: str
    price: float
    signal: str           # BUY / SELL / HOLD / AVOID
    trend: str            # BULLISH / BEARISH
    conclusion: str       # 一句话结论
    buy_price: float      # 建议买入价
    stop_loss: float      # 建议止损价
    take_profit: float    # 建议止盈价
    confidence: float     # 0-100 置信度
    checklist: List[str]  # 检查清单
    risk_flags: List[str] # 风险标记


def compute_deviation(price: float, ma5: float) -> float:
    """计算价格偏离 MA5 的百分比。"""
    if ma5 <= 0:
        return 0
    return (price - ma5) / ma5


def should_skip_chase(price: float, ma5: float, ma20: float) -> tuple[bool, str]:
    """判断是否应该追高。强趋势自动放宽阈值。

    Returns:
        (是否跳过, 原因描述)
    """
    deviation = abs(compute_deviation(price, ma5))
    # 强趋势(MA5 > MA20 + 3%) 放宽到 8%
    if ma20 > 0 and ma5 > ma20 * 1.03:
        threshold = 0.08
        reason = "强趋势模式"
    else:
        threshold = 0.05
        reason = "标准模式"

    if deviation > threshold:
        return True, f"偏离 MA5 {deviation*100:.1f}% > {threshold*100:.0f}% ({reason})"
    return False, ""


def estimate_confidence(signal_data: dict, macro_score: float) -> float:
    """估算信号置信度 (0-100)。

    加权公式:
      - 趋势方向 (BULL=+20, BEAR=+10)
      - RSI 合理性 (+20 若 30<RSI<70)
      - MACD 方向 (+20 若方向匹配)
      - 宏观风险折扣 (-macro_score*0.3)
    """
    confidence = 40.0  # 基础分

    trend = signal_data.get("trend", "BEAR")
    if trend == "BULL":
        confidence += 20
    else:
        confidence += 10

    rsi = signal_data.get("rsi", 50)
    if 30 < rsi < 70:
        confidence += 20
    elif rsi <= 30 or rsi >= 70:
        confidence += 5  # 极值区域信号弱

    macd_h = signal_data.get("macd_hist", 0)
    action = signal_data.get("action", "AVOID")
    if (action == "BUY" and macd_h > 0) or (action == "SELL" and macd_h < 0):
        confidence += 20

    # 宏观折扣
    confidence -= macro_score * 0.3

    return max(0, min(100, confidence))


def detect_risk_flags(signal_data: dict, macro: dict, deviation_pct: float) -> List[str]:
    """检测风险标记。"""
    flags = []

    if deviation_pct > 5:
        flags.append(f"价格偏离MA5 {deviation_pct:.1f}%")
    rsi = signal_data.get("rsi", 50)
    if rsi >= 70:
        flags.append(f"RSI={rsi:.0f} 超买")
    elif rsi <= 30:
        flags.append(f"RSI={rsi:.0f} 超卖")
    if macro.get("recommendation", "") in ("FOMC_EVACUATION", "FOMC_BLACKOUT"):
        flags.append("FOMC 黑屏期")
    if macro.get("position_multiplier", 1.0) < 0.5:
        flags.append(f"仓位系数仅 {macro['position_multiplier']*100:.0f}%")
    vix = next((f for f in macro.get("factors", []) if "VIX" in f.get("name", "")), None)
    if vix and vix.get("score", 0) >= 60:
        flags.append(f"VIX 高波动 ({vix['detail'][:30]})")

    return flags


def build_decision_card(ticker: str, signal_data: dict, macro: dict,
                         fast_ma: float = 0, slow_ma: float = 0) -> DecisionCard:
    """为单只股票构建完整的 AI 决策卡。"""

    price = signal_data.get("close", 0)
    deviation = compute_deviation(price, fast_ma)
    skip, skip_reason = should_skip_chase(price, fast_ma, slow_ma)
    confidence = estimate_confidence(signal_data, macro.get("macro_risk_score", 30))
    flags = detect_risk_flags(signal_data, macro, abs(deviation) * 100)

    # 买卖价位
    atr = signal_data.get("atr", price * 0.02)
    if signal_data.get("action") == "BUY":
        buy_price = round(price * 0.995, 2)  # 稍低于现价
        stop_loss = round(buy_price * 0.95, 2)
        take_profit = round(buy_price * 1.10, 2)
    else:
        buy_price = round(price * 0.98, 2)
        stop_loss = round(price * 0.92, 2)
        take_profit = round(price * 1.15, 2)

    # 一句话结论
    action = signal_data.get("action", "AVOID")
    trend = signal_data.get("trend", "BEAR")
    if skip:
        conclusion = f"[追高跳过] {trend}趋势, {skip_reason}, 建议等待回调至 ${fast_ma:.2f} 附近"
    elif action == "BUY" and confidence >= 50:
        conclusion = f"[偏多] {trend}趋势 | RSI={signal_data.get('rsi',50):.0f} | 置信度{confidence:.0f}% | 可入场"
    elif action == "BUY":
        conclusion = f"[谨慎] {trend}趋势但置信度低({confidence:.0f}%) | 轻仓或观望"
    elif action == "SELL":
        conclusion = f"[偏空] {trend}趋势 | MACD走弱 | 建议减仓"
    else:
        conclusion = f"[观望] {trend}趋势 | 无明确信号 | 等待突破"

    # 检查清单
    checklist = [
        f"MA5({'%.2f'%fast_ma if fast_ma>0 else 'N/A'}) {'>' if fast_ma>slow_ma else '<'} MA20({'%.2f'%slow_ma if slow_ma>0 else 'N/A'})",
        f"RSI={signal_data.get('rsi',50):.0f} {'正常' if 30<signal_data.get('rsi',50)<70 else '极值'}",
        f"偏离MA5 {deviation*100:+.1f}% {'⚠' if abs(deviation)>0.05 else '✓'}",
        f"宏观评分 {macro.get('macro_risk_score',30):.0f}/100 {'⚠' if macro.get('macro_risk_score',30)>=50 else '✓'}",
        f"成交量 {'正常' if signal_data.get('volume',0)>0 else 'N/A'}",
    ]

    return DecisionCard(
        ticker=ticker, price=price, signal=action, trend=trend,
        conclusion=conclusion, buy_price=buy_price,
        stop_loss=stop_loss, take_profit=take_profit,
        confidence=round(confidence, 1), checklist=checklist,
        risk_flags=flags,
    )


def build_panel(signals: dict, macro: dict) -> List[dict]:
    """从信号字典和宏观评估批量生成决策面板。"""
    cards = []
    for ticker, sig in signals.items():
        card = build_decision_card(
            ticker, sig, macro,
            fast_ma=sig.get("fast_ma", 0),
            slow_ma=sig.get("slow_ma", 0),
        )
        cards.append({
            "ticker": card.ticker,
            "price": card.price,
            "signal": card.signal,
            "trend": card.trend,
            "conclusion": card.conclusion,
            "buy_price": card.buy_price,
            "stop_loss": card.stop_loss,
            "take_profit": card.take_profit,
            "confidence": card.confidence,
            "risk_flags": card.risk_flags,
            "checklist": card.checklist,
        })
    # 按置信度降序
    cards.sort(key=lambda c: -c["confidence"])
    return cards

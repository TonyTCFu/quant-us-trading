"""高级短线策略：布林带突破、RSI 反转、均值回归。

每个策略函数返回带 signal 列的 DataFrame，与现有回测引擎兼容。
"""

import numpy as np
import pandas as pd

from .indicators import compute_bollinger, compute_rsi, compute_atr


def bollinger_breakout(
    df: pd.DataFrame,
    period: int = 20,
    std_dev: float = 2.0,
    confirmation_bars: int = 1,
) -> pd.DataFrame:
    """布林带突破策略。

    规则：
      价格突破上轨 → 做多 (1)
      价格跌破下轨 → 空仓 (0)
      突破需成交量确认

    Args:
        df: OHLCV DataFrame
        period: 布林带周期
        std_dev: 标准差倍数
        confirmation_bars: 突破确认 bar 数

    Returns:
        原 df 附加 signal, bb_mid, bb_upper, bb_lower 列
    """
    df = compute_bollinger(df, period=period, std_dev=std_dev)
    result = df.copy()
    result["signal"] = 0

    if all(c in result.columns for c in ["bb_upper", "bb_lower", "Close", "Volume"]):
        close = result["Close"]
        upper, lower = result["bb_upper"], result["bb_lower"]

        # 上轨突破做多
        breakout_up = close > upper
        # 成交量放大确认（大于 20 日均量）
        vol_ma = result["Volume"].rolling(20).mean()
        vol_confirm = result["Volume"] > vol_ma * 1.2

        buy_signal = breakout_up & vol_confirm
        for i in range(1, confirmation_bars + 1):
            buy_signal = buy_signal & breakout_up.shift(i)

        result.loc[buy_signal, "signal"] = 1

        # 下轨跌破或中轨回落空仓
        exit_signal = (close < result["bb_mid"]) | (close < lower)
        # 持有仓位直到退出信号
        in_position = False
        for i in range(len(result)):
            if buy_signal.iloc[i] and not in_position:
                in_position = True
                result.loc[result.index[i], "signal"] = 1
            elif exit_signal.iloc[i] and in_position:
                in_position = False
                result.loc[result.index[i], "signal"] = 0
            elif in_position:
                result.loc[result.index[i], "signal"] = 1

    return result


def rsi_reversal(
    df: pd.DataFrame,
    rsi_period: int = 14,
    oversold: int = 30,
    overbought: int = 70,
    recovery_bars: int = 2,
) -> pd.DataFrame:
    """RSI 反转策略。

    规则：
      RSI 从超卖区反弹（< oversold 后回升）→ 做多 (1)
      RSI 从超买区回落（> overbought 后下跌）→ 空仓 (0)
      需价格确认：超卖反弹时价格>前低，超买回落时价格<前高

    Args:
        df: OHLCV DataFrame
        rsi_period: RSI 周期
        oversold: 超卖阈值
        overbought: 超买阈值
        recovery_bars: 反弹确认 bar 数

    Returns:
        原 df 附加 signal, rsi 列
    """
    df = compute_rsi(df, period=rsi_period)
    result = df.copy()
    result["signal"] = 0

    if "rsi" not in result.columns:
        return result

    rsi = result["rsi"]
    close = result["Close"]

    # 超卖区反弹：之前 RSI < oversold，当前 RSI > oversold 且连续上升
    was_oversold = rsi.shift(recovery_bars) < oversold
    now_recovering = True
    for i in range(1, recovery_bars + 1):
        now_recovering = now_recovering & (rsi.shift(i) < rsi.shift(i - 1))
    now_recovering = now_recovering & (rsi > rsi.shift(1))

    # 价格确认
    price_rising = close > close.shift(recovery_bars)

    buy_signal = was_oversold & now_recovering & price_rising

    # 超买区回落
    was_overbought = rsi.shift(recovery_bars) > overbought
    now_falling = True
    for i in range(1, recovery_bars + 1):
        now_falling = now_falling & (rsi.shift(i) > rsi.shift(i - 1))
    now_falling = now_falling & (rsi < rsi.shift(1))

    exit_signal = was_overbought & now_falling

    result.loc[buy_signal, "signal"] = 1
    result.loc[exit_signal, "signal"] = 0
    result.loc[result["rsi"].isna(), "signal"] = 0

    return result


def bollinger_squeeze_mean_reversion(
    df: pd.DataFrame,
    bb_period: int = 20,
    squeeze_threshold: float = 0.05,
    atr_period: int = 14,
) -> pd.DataFrame:
    """布林带挤压均值回归策略。

    识别布林带收窄（挤压），当价格突破挤压区间后：
      - 向上突破 → 做空（等待回归）
      - 向下跌破 → 做多（等待反弹）
    这是经典的低波动后高波动的反转策略。

    规则：
      1. 识别 squeeze：bb_width < squeeze_threshold
      2. squeeze 后价格突破上轨 + 高成交量 → 潜在顶部，空仓
      3. squeeze 后价格跌破下轨 + 高成交量 → 潜在底部，做多 (1)
      4. 价格回归中轨时退出

    Args:
        df: OHLCV DataFrame
        bb_period: 布林带周期
        squeeze_threshold: 挤压阈值（带宽/中轨 比例）
        atr_period: ATR 周期用于波动率参考

    Returns:
        原 df 附加 signal 列
    """
    df = compute_bollinger(df, period=bb_period)
    df = compute_atr(df, period=atr_period)
    result = df.copy()
    result["signal"] = 0

    if "bb_width" not in result.columns:
        return result

    # 识别挤压
    is_squeeze = result["bb_width"] < squeeze_threshold

    # 成交量放大 = 前 50 bar 均量的 1.5x
    vol_ma50 = result["Volume"].rolling(50).mean()
    high_volume = result["Volume"] > vol_ma50 * 1.5

    close = result["Close"]
    lower = result["bb_lower"]
    upper = result["bb_upper"]
    mid = result["bb_mid"]

    # squeeze 后下轨反弹做多
    was_squeeze = is_squeeze.shift(1) | is_squeeze.shift(2) | is_squeeze.shift(3)
    bounce_lower = (close.shift(1) <= lower.shift(1)) & (close > lower)
    long_signal = was_squeeze & bounce_lower & high_volume

    # 退出：价格回归中轨或跌破
    exit_long = close >= mid * 0.99

    for i in range(len(result)):
        if long_signal.iloc[i]:
            result.loc[result.index[i], "signal"] = 1
        elif exit_long.iloc[i] and i > 0 and result["signal"].iloc[i - 1] == 1:
            result.loc[result.index[i], "signal"] = 0
        elif i > 0 and result["signal"].iloc[i - 1] == 1:
            result.loc[result.index[i], "signal"] = 1

    return result


def generate_bollinger_signal(df: pd.DataFrame, period: int = 20, std_dev: float = 2.0) -> pd.Series:
    """简化版布林带信号（用于组合策略）。"""
    df = compute_bollinger(df, period=period, std_dev=std_dev)
    if "bb_upper" not in df.columns:
        return pd.Series(0, index=df.index)
    close = df["Close"]
    signal = pd.Series(0, index=df.index)
    signal[(close > df["bb_upper"]) & (df["Volume"] > df["Volume"].rolling(20).mean())] = 1
    signal[close < df["bb_lower"]] = 0
    signal[close < df["bb_mid"]] = 0
    return signal


def strategy_comparison(
    df: pd.DataFrame,
    strategies: dict,
    commission: float = 0.005,
    slippage: float = 0.0005,
) -> list:
    """批量比较多个策略的效果。

    Args:
        df: OHLCV DataFrame
        strategies: {"name": strategy_fn} 策略函数字典
        commission, slippage: 交易成本

    Returns:
        [{name: str, signal: pd.Series, summary: dict}, ...]
    """
    from src.backtest.engine import run_backtest

    results = []
    for name, fn in strategies.items():
        df_sig = fn(df)
        signal = df_sig["signal"] if "signal" in df_sig.columns else df_sig
        result = run_backtest(df, signal, commission_per_share=commission, slippage=slippage)
        results.append({"name": name, "signal": signal, "summary": result.summary})

    results.sort(key=lambda x: x["summary"]["sharpe_ratio"], reverse=True)
    return results

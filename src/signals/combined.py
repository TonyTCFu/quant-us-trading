"""组合策略：MA 交叉 + MACD + RSI 多信号投票。

使用方式:
    from src.signals.combined import combined_strategy
    df = combined_strategy(df, fast_ma=10, slow_ma=30, macd_fast=12, macd_slow=26,
                            rsi_period=14, rsi_oversold=30, rsi_overbought=70)
    # df 新增 signal 列
"""

import pandas as pd

from .indicators import compute_macd, compute_rsi, generate_macd_signal, generate_rsi_signal
from .ma_cross import ma_cross_signal


def combined_strategy(
    df: pd.DataFrame,
    fast_ma: int = 10,
    slow_ma: int = 30,
    macd_fast: int = 12,
    macd_slow: int = 26,
    macd_signal_period: int = 9,
    rsi_period: int = 14,
    rsi_oversold: int = 30,
    rsi_overbought: int = 70,
    weights: tuple = (0.4, 0.3, 0.3),
) -> pd.DataFrame:
    """三信号加权组合策略。

    Args:
        df: OHLCV DataFrame
        fast_ma, slow_ma: 双均线参数
        macd_fast, macd_slow, macd_signal_period: MACD 参数
        rsi_period, rsi_oversold, rsi_overbought: RSI 参数
        weights: (ma_weight, macd_weight, rsi_weight)

    Returns:
        原 df 附加 signal 及所有计算指标列
    """
    # MA 信号
    df = ma_cross_signal(df, fast_period=fast_ma, slow_period=slow_ma)

    # MACD
    df = compute_macd(df, fast=macd_fast, slow=macd_slow, signal=macd_signal_period)
    macd_sig = generate_macd_signal(df)

    # RSI
    df = compute_rsi(df, period=rsi_period)
    rsi_sig = generate_rsi_signal(df, oversold=rsi_oversold, overbought=rsi_overbought)

    # 加权组合
    ma_w, macd_w, rsi_w = weights
    composite = (
        ma_w * df["signal"].fillna(0).astype(float)
        + macd_w * macd_sig.astype(float)
        + rsi_w * rsi_sig.astype(float)
    )
    df["ma_signal"] = df["signal"]
    df["macd_raw_signal"] = macd_sig
    df["rsi_raw_signal"] = rsi_sig
    df["signal"] = (composite >= 0.5).astype(int)
    return df

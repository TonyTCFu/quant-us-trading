"""技术指标计算模块：MACD、RSI、布林带、ATR。

基于本地 OHLCV 数据计算，不依赖外部 API。
与 ma_cross.py 风格一致：输入 DataFrame，输出附加指标列。
"""

import numpy as np
import pandas as pd


def compute_macd(
    df: pd.DataFrame,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
    price_col: str = "Close",
) -> pd.DataFrame:
    """计算 MACD (Moving Average Convergence Divergence)。

    Returns: 原 df 附加 macd, macd_signal, macd_histogram 列
    """
    if df.empty or price_col not in df.columns:
        return df.assign(macd=pd.NA, macd_signal=pd.NA, macd_histogram=pd.NA)

    result = df.copy()
    ema_fast = result[price_col].ewm(span=fast, adjust=False).mean()
    ema_slow = result[price_col].ewm(span=slow, adjust=False).mean()
    result["macd"] = ema_fast - ema_slow
    result["macd_signal"] = result["macd"].ewm(span=signal, adjust=False).mean()
    result["macd_histogram"] = result["macd"] - result["macd_signal"]
    return result


def compute_rsi(
    df: pd.DataFrame,
    period: int = 14,
    price_col: str = "Close",
) -> pd.DataFrame:
    """计算 RSI (Relative Strength Index)  using Wilder's smoothing。

    Returns: 原 df 附加 rsi 列
    """
    if df.empty or price_col not in df.columns:
        return df.assign(rsi=pd.NA)

    result = df.copy()
    delta = result[price_col].diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)

    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    result["rsi"] = 100 - (100 / (1 + rs))
    result.loc[avg_loss == 0, "rsi"] = 100
    return result


def compute_bollinger(
    df: pd.DataFrame,
    period: int = 20,
    std_dev: float = 2.0,
    price_col: str = "Close",
) -> pd.DataFrame:
    """计算布林带 (Bollinger Bands)。

    Returns: 原 df 附加 bb_mid, bb_upper, bb_lower, bb_width 列
    """
    if df.empty or price_col not in df.columns:
        return df.assign(bb_mid=pd.NA, bb_upper=pd.NA, bb_lower=pd.NA, bb_width=pd.NA)

    result = df.copy()
    result["bb_mid"] = result[price_col].rolling(window=period).mean()
    std = result[price_col].rolling(window=period).std()
    result["bb_upper"] = result["bb_mid"] + std_dev * std
    result["bb_lower"] = result["bb_mid"] - std_dev * std
    result["bb_width"] = (result["bb_upper"] - result["bb_lower"]) / result["bb_mid"]
    return result


def compute_atr(
    df: pd.DataFrame,
    period: int = 14,
) -> pd.DataFrame:
    """计算 ATR (Average True Range)。

    需要 df 含 High, Low, Close 列。
    """
    required = ["High", "Low", "Close"]
    if df.empty or not all(c in df.columns for c in required):
        return df.assign(atr=pd.NA)

    result = df.copy()
    high, low, close = result["High"], result["Low"], result["Close"]
    prev_close = close.shift(1)

    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    result["atr"] = true_range.ewm(alpha=1 / period, adjust=False).mean()
    return result


def generate_macd_signal(df: pd.DataFrame) -> pd.Series:
    """从 MACD 生成交易信号。
    规则：MACD 柱状图由负转正 → 做多 (1)，由正转负 → 空仓 (0)
    需要先调用 compute_macd()
    """
    if "macd_histogram" not in df.columns:
        return pd.Series(0, index=df.index)
    # histogram 正值且上穿 0 线
    signal = (df["macd_histogram"] > 0).astype(int)
    return signal


def generate_rsi_signal(df: pd.DataFrame, oversold: int = 30, overbought: int = 70) -> pd.Series:
    """从 RSI 生成交易信号。
    规则：RSI < oversold → 做多 (1)，RSI > overbought → 空仓 (0)，否则持仓不变
    需要先调用 compute_rsi()
    """
    if "rsi" not in df.columns:
        return pd.Series(0, index=df.index)

    signal = pd.Series(0, index=df.index, dtype=int)
    # RSI 超卖做多
    signal[df["rsi"] < oversold] = 1
    # RSI 超买空仓
    signal[df["rsi"] > overbought] = 0
    # 中间区域保持前一状态
    mask_mid = (df["rsi"] >= oversold) & (df["rsi"] <= overbought)
    if mask_mid.any():
        signal[mask_mid] = pd.NA
        signal = signal.ffill().fillna(0).astype(int)
    return signal


def generate_combined_signal(
    df: pd.DataFrame,
    ma_signal: pd.Series,
    macd_weight: float = 0.3,
    rsi_weight: float = 0.3,
    ma_weight: float = 0.4,
) -> pd.Series:
    """组合信号： MA + MACD + RSI 加权投票。

    各信号独立生成后加权平均，>0.5 做多。
    macd_signal 和 rsi_signal 需要由上层传入（可由 compute_* + generate_* 获得）。
    """
    macd_sig = generate_macd_signal(df)
    rsi_sig = generate_rsi_signal(df)

    composite = (
        ma_weight * ma_signal.fillna(0).astype(float)
        + macd_weight * macd_sig.astype(float)
        + rsi_weight * rsi_sig.astype(float)
    )
    return (composite > 0.5).astype(int)

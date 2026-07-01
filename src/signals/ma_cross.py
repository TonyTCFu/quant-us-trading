"""双均线交叉策略 (MA Crossover)。

信号规则：
  - fast_ma > slow_ma → 做多 (signal=1)
  - fast_ma <= slow_ma → 空仓 (signal=0)
信号时点：收盘后生成，次日开盘执行（在回测引擎中 shift）

参数可配置，无硬编码。
"""

from typing import Dict, Optional

import pandas as pd


def ma_cross_signal(
    df: pd.DataFrame,
    fast_period: int = 20,
    slow_period: int = 50,
    price_col: str = "Close",
) -> pd.DataFrame:
    """生成双均线交叉信号。

    Args:
        df: OHLCV DataFrame, 需含 price_col 列
        fast_period: 快线周期
        slow_period: 慢线周期
        price_col: 用于计算均线的价格列名

    Returns:
        原 df 附加列:
          - fast_ma: 快均线
          - slow_ma: 慢均线
          - signal: 1=做多, 0=空仓 (基于当日收盘价计算)
    """
    if df.empty or price_col not in df.columns:
        return df.assign(fast_ma=pd.NA, slow_ma=pd.NA, signal=0)

    result = df.copy()
    result["fast_ma"] = result[price_col].rolling(window=fast_period).mean()
    result["slow_ma"] = result[price_col].rolling(window=slow_period).mean()
    result["signal"] = (result["fast_ma"] > result["slow_ma"]).astype(int)
    # 均线未就绪时无信号
    result.loc[result["slow_ma"].isna(), "signal"] = 0
    return result

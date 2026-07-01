"""回测结果可视化，使用 matplotlib。"""

from pathlib import Path
from typing import List, Optional

import matplotlib
matplotlib.use("Agg")  # 非交互后端，仅生成文件
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np
import pandas as pd


# 中文兼容：macOS 默认无中文字体，回退英文标签以避免乱码
plt.rcParams["axes.unicode_minus"] = False


def plot_equity_and_drawdown(
    equity: pd.Series,
    ticker: str = "",
    save_path: Optional[str] = None,
) -> str:
    """绘制权益曲线与回撤。

    Args:
        equity: 每日权益 Series
        ticker: 股票代码，用于标题
        save_path: 保存路径，默认 outputs/{ticker}_report.png

    Returns: 实际保存路径
    """
    if save_path is None:
        Path("outputs").mkdir(exist_ok=True)
        tag = f"{ticker}_" if ticker else ""
        save_path = f"outputs/{tag}report.png"

    rolling_max = equity.cummax()
    drawdown = (equity - rolling_max) / rolling_max

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8), sharex=True,
                                    gridspec_kw={"height_ratios": [3, 1]})

    # 权益曲线
    ax1.plot(equity.index, equity.values, color="steelblue", linewidth=1.0, label="Equity")
    ax1.axhline(y=equity.iloc[0], color="gray", linestyle="--", alpha=0.5, label="Initial")
    ax1.fill_between(equity.index, equity.values, equity.iloc[0],
                      where=(equity.values >= equity.iloc[0]),
                      color="steelblue", alpha=0.15)
    ax1.fill_between(equity.index, equity.values, equity.iloc[0],
                      where=(equity.values < equity.iloc[0]),
                      color="crimson", alpha=0.15)
    ax1.set_ylabel("Equity ($)")
    ax1.set_title(f"Backtest: {ticker}" if ticker else "Backtest Report")
    ax1.legend(loc="upper left")
    ax1.grid(True, alpha=0.3)

    # 回撤
    ax2.fill_between(equity.index, drawdown.values, 0,
                      where=(drawdown.values < 0),
                      color="crimson", alpha=0.3)
    ax2.plot(equity.index, drawdown.values, color="crimson", linewidth=0.8)
    ax2.set_ylabel("Drawdown")
    ax2.set_xlabel("Date")
    ax2.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _: f"{y:.0%}"))
    ax2.grid(True, alpha=0.3)

    fig.tight_layout()
    fig.savefig(save_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return save_path


def plot_signal_overlay(
    df: pd.DataFrame,
    ticker: str = "",
    save_path: Optional[str] = None,
) -> Optional[str]:
    """绘制价格 + 均线 + 信号叠加图。

    Args:
        df: 含 Open/Close/fast_ma/slow_ma/signal 列的 DataFrame
    """
    required = ["Close", "fast_ma", "slow_ma", "signal"]
    if not all(c in df.columns for c in required):
        return None

    if save_path is None:
        Path("outputs").mkdir(exist_ok=True)
        tag = f"{ticker}_" if ticker else ""
        save_path = f"outputs/{tag}signal.png"

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 7), sharex=True,
                                    gridspec_kw={"height_ratios": [3, 1]})

    # 价格 + 均线
    ax1.plot(df.index, df["Close"], color="black", linewidth=0.8, alpha=0.7, label="Close")
    ax1.plot(df.index, df["fast_ma"], color="steelblue", linewidth=1.0, label=f"Fast MA")
    ax1.plot(df.index, df["slow_ma"], color="coral", linewidth=1.0, label=f"Slow MA")
    ax1.set_ylabel("Price ($)")
    ax1.set_title(f"MA Cross Signal: {ticker}" if ticker else "MA Cross Signal")
    ax1.legend(loc="upper left")
    ax1.grid(True, alpha=0.3)

    # 信号
    signal = df["signal"].fillna(0)
    ax2.fill_between(df.index, signal.values, 0,
                      where=(signal.values == 1),
                      color="steelblue", alpha=0.5, step="post")
    ax2.set_ylabel("Signal")
    ax2.set_xlabel("Date")
    ax2.set_yticks([0, 1])
    ax2.set_yticklabels(["Flat", "Long"])
    ax2.grid(True, alpha=0.3)

    fig.tight_layout()
    fig.savefig(save_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return save_path

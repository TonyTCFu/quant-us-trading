"""向量化回测引擎。

交易假设：
  - 收盘后生成信号 → 次日开盘成交（signal 向前 shift 1 天）
  - 单边做多（long only），无做空
  - 交易成本：佣金 + 滑点
  - 初始资金可配置

输出：
  - 权益曲线
  - 交易记录
  - 绩效摘要（总收益、年化收益、最大回撤、Sharpe、胜率等）
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


@dataclass
class BacktestResult:
    """回测结果容器。"""
    equity_curve: pd.Series        # 每日权益
    trade_log: pd.DataFrame        # 逐笔交易记录
    summary: dict                  # 绩效摘要
    config: dict                   # 回测参数快照


def run_backtest(
    df: pd.DataFrame,
    signal: pd.Series,
    initial_capital: float = 100_000.0,
    commission_per_share: float = 0.005,
    slippage: float = 0.0005,
    signal_delay: str = "next_open",
) -> BacktestResult:
    """执行向量化回测。

    Args:
        df: OHLCV DataFrame, 必须含 Open, Close 列
        signal: 交易信号 Series (1=做多, 0=空仓), index 与 df 对齐
        initial_capital: 初始资金
        commission_per_share: 每股佣金
        slippage: 滑点比例
        signal_delay: 信号延迟方式，"next_open" = 次日开盘执行

    Returns:
        BacktestResult 含权益曲线、交易记录、绩效摘要
    """
    df = df.copy()
    signal = signal.copy()
    # 对齐索引
    common_idx = df.index.intersection(signal.index)
    df = df.loc[common_idx]
    signal = signal.loc[common_idx]

    if df.empty:
        return _empty_result(initial_capital, commission_per_share, slippage)

    # 信号延迟：当日收盘信号 → 次日开盘执行
    if signal_delay == "next_open":
        trade_signal = signal.shift(1).fillna(0).astype(int)
    else:
        trade_signal = signal.astype(int)

    # 每日收益率（基于 close-to-close）
    daily_ret = df["Close"].pct_change().fillna(0.0)

    # 策略收益 = 持仓信号 × 市场收益
    strategy_ret = trade_signal * daily_ret

    # 交易成本：信号变化时产生换仓成本
    turnover = trade_signal.diff().abs()
    # 滑点成本 = 换仓时承担 bid-ask spread
    slippage_cost = turnover * slippage
    # 佣金成本（假设每股固定佣金，简化处理：按成交额比例估算）
    commission_cost = turnover * (commission_per_share / df["Close"])

    strategy_ret = strategy_ret - slippage_cost - commission_cost

    # 权益曲线
    equity = (1 + strategy_ret).cumprod() * initial_capital

    # 提取交易记录
    trade_log = _extract_trades(df, trade_signal, commission_per_share, slippage)

    # 绩效摘要
    summary = _compute_metrics(equity, strategy_ret, trade_log, initial_capital)

    return BacktestResult(
        equity_curve=equity,
        trade_log=trade_log,
        summary=summary,
        config={
            "initial_capital": initial_capital,
            "commission_per_share": commission_per_share,
            "slippage": slippage,
            "signal_delay": signal_delay,
        },
    )


def _extract_trades(
    df: pd.DataFrame,
    signal: pd.Series,
    commission: float,
    slippage: float,
) -> pd.DataFrame:
    """从信号序列中提取逐笔交易记录。"""
    trades = []
    # 找到信号变化点
    changes = signal.diff().fillna(0)
    entries = changes[changes == 1].index  # 0→1 入场
    exits = changes[changes == -1].index    # 1→0 出场

    # 处理第一个 bar 就有信号的情况
    if signal.iloc[0] == 1:
        entries = entries.insert(0, signal.index[0])

    for i, entry_date in enumerate(entries):
        # 找到对应出场点
        exit_dates = exits[exits > entry_date]
        if len(exit_dates) == 0:
            # 仍在持仓中
            exit_date = signal.index[-1]
            is_open = True
        else:
            exit_date = exit_dates[0]
            is_open = False

        entry_price = df.loc[entry_date, "Open"]
        exit_price = df.loc[exit_date, "Open"]

        # 扣除成本
        gross_ret = (exit_price / entry_price) - 1
        cost = (commission / entry_price) + slippage  # 入场
        cost += (commission / exit_price) + slippage  # 出场
        net_ret = gross_ret - cost

        trades.append({
            "entry_date": entry_date,
            "exit_date": exit_date,
            "entry_price": round(entry_price, 4),
            "exit_price": round(exit_price, 4),
            "return": round(net_ret, 6),
            "holding_days": (exit_date - entry_date).days,
            "is_open": is_open,
        })

    if not trades:
        return pd.DataFrame(
            columns=["entry_date", "exit_date", "entry_price", "exit_price",
                     "return", "holding_days", "is_open"]
        )
    return pd.DataFrame(trades)


def _compute_metrics(
    equity: pd.Series,
    strategy_ret: pd.Series,
    trade_log: pd.DataFrame,
    initial_capital: float,
) -> dict:
    """计算绩效指标。"""
    if equity.empty or len(equity) < 2:
        return _empty_metrics()

    total_return = (equity.iloc[-1] / initial_capital) - 1

    # 年化收益（假设 252 个交易日）
    n_days = len(equity)
    years = n_days / 252
    annual_return = (1 + total_return) ** (1 / max(years, 0.1)) - 1

    # 最大回撤
    rolling_max = equity.cummax()
    drawdown = (equity - rolling_max) / rolling_max
    max_drawdown = drawdown.min()

    # 波动率（年化）
    daily_vol = strategy_ret.std()
    annual_vol = daily_vol * np.sqrt(252)

    # Sharpe ratio（假设无风险利率 0）
    sharpe = (strategy_ret.mean() / max(daily_vol, 1e-10)) * np.sqrt(252)

    # 胜率 & 盈亏比
    if not trade_log.empty:
        closed = trade_log[trade_log["is_open"] == False]
        if len(closed) > 0:
            win_rate = (closed["return"] > 0).mean()
            avg_win = closed.loc[closed["return"] > 0, "return"].mean() if (closed["return"] > 0).any() else 0
            avg_loss = abs(closed.loc[closed["return"] < 0, "return"].mean()) if (closed["return"] < 0).any() else 0
            profit_factor = (avg_win / avg_loss) if avg_loss > 0 else float("inf")
            avg_holding = closed["holding_days"].mean()
            max_loss = closed["return"].min()
        else:
            win_rate = avg_win = avg_loss = profit_factor = avg_holding = 0.0
            max_loss = 0.0
        n_trades = len(trade_log)
    else:
        win_rate = avg_win = avg_loss = profit_factor = avg_holding = 0.0
        max_loss = 0.0
        n_trades = 0

    return {
        "total_return": round(total_return, 6),
        "annual_return": round(annual_return, 6),
        "max_drawdown": round(max_drawdown, 6),
        "annual_volatility": round(annual_vol, 6),
        "sharpe_ratio": round(sharpe, 4),
        "win_rate": round(win_rate, 4),
        "profit_factor": round(profit_factor, 4) if profit_factor != float("inf") else None,
        "avg_holding_days": round(avg_holding, 1),
        "max_single_loss": round(max_loss, 6),
        "n_trades": n_trades,
        "n_days": n_days,
    }


def _empty_result(
    initial_capital: float, commission: float, slippage: float
) -> BacktestResult:
    return BacktestResult(
        equity_curve=pd.Series(dtype=float),
        trade_log=pd.DataFrame(),
        summary=_empty_metrics(),
        config={
            "initial_capital": initial_capital,
            "commission_per_share": commission,
            "slippage": slippage,
        },
    )


def _empty_metrics() -> dict:
    return {
        "total_return": 0.0,
        "annual_return": 0.0,
        "max_drawdown": 0.0,
        "annual_volatility": 0.0,
        "sharpe_ratio": 0.0,
        "win_rate": 0.0,
        "profit_factor": None,
        "avg_holding_days": 0.0,
        "max_single_loss": 0.0,
        "n_trades": 0,
        "n_days": 0,
    }

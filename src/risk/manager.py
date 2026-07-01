"""风控模块：仓位管理、止损止盈、回撤熔断、流动性过滤。

可在回测前/后集成，对信号或权益曲线施加约束。
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


@dataclass
class RiskConfig:
    """风控参数，从 config 文件的 risk 段加载。"""
    max_position_pct: float = 0.20
    max_positions: int = 10
    max_total_exposure: float = 0.95
    stop_loss_pct: float = -0.05
    take_profit_pct: float = 0.10
    max_daily_loss_pct: float = -0.03
    max_drawdown_pct: float = -0.20
    min_avg_volume: int = 500_000

    @classmethod
    def from_config(cls, cfg: dict) -> "RiskConfig":
        return cls(**{k: v for k, v in cfg.items() if k in cls.__dataclass_fields__})


@dataclass
class Position:
    ticker: str
    entry_idx: int              # entry bar index
    exit_idx: Optional[int] = None
    entry_price: float = 0.0
    exit_price: float = 0.0
    shares: int = 0


def filter_liquidity(df: pd.DataFrame, min_avg_volume: int = 500_000) -> bool:
    """流动性过滤：检查股票是否满足最低日均成交量。"""
    if "Volume" not in df.columns or df.empty:
        return False
    avg_vol = df["Volume"].tail(60).mean()
    return avg_vol >= min_avg_volume


def apply_stop_loss_take_profit(
    df: pd.DataFrame,
    signal: pd.Series,
    stop_loss_pct: float = -0.05,
    take_profit_pct: float = 0.10,
) -> pd.Series:
    """在信号序列上叠加止损止盈逻辑。

    对每段持仓区间，检查 price path 是否触及止损/止盈线，
    触及则在当天收盘退出（信号置 0 并保持到下次入场）。

    Args:
        df: OHLCV DataFrame 含至少 Close 列
        signal: 原始交易信号 (1=持仓, 0=空仓)
        stop_loss_pct: 止损比例（负值），如 -0.05
        take_profit_pct: 止盈比例（正值），如 0.10

    Returns:
        修正后的信号序列
    """
    if stop_loss_pct >= 0:
        stop_loss_pct = -stop_loss_pct  # 确保是负值

    sig = signal.copy().astype(int)
    close = df["Close"]

    in_position = False
    entry_idx = 0
    entry_price = 0.0

    for i in range(len(sig)):
        if not in_position and sig.iloc[i] == 1:
            in_position = True
            entry_idx = i
            entry_price = close.iloc[i]
        elif in_position:
            # 检查退出条件
            current_price = close.iloc[i]
            ret = (current_price / entry_price) - 1
            # 也检查 bar 路径中的 High/Low
            low_ret = (df["Low"].iloc[i] / entry_price) - 1 if "Low" in df.columns else ret
            high_ret = (df["High"].iloc[i] / entry_price) - 1 if "High" in df.columns else ret

            should_exit = False
            if low_ret <= stop_loss_pct:
                should_exit = True
            elif high_ret >= take_profit_pct:
                should_exit = True
            elif sig.iloc[i] == 0:  # 原信号已退出
                should_exit = True

            if should_exit:
                sig.iloc[i] = 0
                # 从下一 bar 到原信号再次入场期间保持空仓
                for j in range(i + 1, len(sig)):
                    if sig.iloc[j] == 1 and j > i:
                        # 重置入场标记，下一 bar 开始新仓位
                        break
                    sig.iloc[j] = 0
                in_position = False

    return sig


class RiskManager:
    """风控管理器：在回测循环中逐 bar 执行风控约束。

    用法:
        rm = RiskManager(RiskConfig.from_config(config["risk"]), initial_capital=100000)
        for date, row in df.iterrows():
            signal = strategy(row)
            approved = rm.check(signal, row)
            # approved 是经过风控过滤后的实际成交信号
    """

    def __init__(self, config: RiskConfig, initial_capital: float = 100_000.0):
        self.cfg = config
        self.initial_capital = initial_capital
        self.current_capital = initial_capital
        self.peak_capital = initial_capital
        self.positions: Dict[str, Position] = {}
        self.daily_pnl = 0.0
        self.halted = False
        self.halt_reason = ""

    def reset(self):
        self.current_capital = self.initial_capital
        self.peak_capital = self.initial_capital
        self.positions.clear()
        self.daily_pnl = 0.0
        self.halted = False
        self.halt_reason = ""

    def position_size(self, price: float) -> int:
        """计算当前允许的最大股数。"""
        max_value = self.current_capital * self.cfg.max_position_pct
        return int(max_value // price) if price > 0 else 0

    def check(self, signal: int, price: float, date, ticker: str = "") -> int:
        """检查风控约束，返回实际执行的信号。

        Args:
            signal: 原始信号 (1=买入, 0=空仓, -1=卖出)
            price: 当前价格
            date: 当日时间戳
            ticker: 股票代码

        Returns:
            过滤后信号 (0=被风控阻止)
        """
        if self.halted:
            return 0

        # 最大回撤熔断
        self.peak_capital = max(self.peak_capital, self.current_capital)
        current_dd = (self.current_capital - self.peak_capital) / self.peak_capital
        if current_dd <= self.cfg.max_drawdown_pct:
            self.halted = True
            self.halt_reason = f"max drawdown {current_dd:.2%}"
            return 0

        # 最大持仓数
        if signal == 1 and len(self.positions) >= self.cfg.max_positions:
            return 0

        # 总仓位限制
        total_exposure = sum(
            p.shares * price for p in self.positions.values()
        ) / self.current_capital if self.current_capital > 0 else 0
        if signal == 1 and total_exposure >= self.cfg.max_total_exposure:
            return 0

        # 单票仓位限制
        if signal == 1 and ticker:
            pos_val = self.positions.get(ticker, Position(ticker, 0)).shares * price
            pos_pct = pos_val / self.current_capital if self.current_capital > 0 else 0
            if pos_pct >= self.cfg.max_position_pct:
                return 0

        return signal

    def update_capital(self, new_capital: float):
        self.current_capital = new_capital


def compute_drawdown_constrained_equity(
    equity: pd.Series,
    max_drawdown_pct: float = -0.20,
) -> pd.Series:
    """对已有权益曲线施加最大回撤限制（事后修正）。

    当回撤超过阈值时，权益冻结（不参与后续交易），模拟熔断后的现金状态。
    """
    result = equity.copy()
    peak = result.iloc[0]
    halted = False
    freeze_value = None

    for i in range(len(result)):
        if halted:
            result.iloc[i] = freeze_value
            continue
        val = result.iloc[i]
        if val > peak:
            peak = val
        dd = (val - peak) / peak
        if dd <= max_drawdown_pct:
            halted = True
            freeze_value = val

    return result

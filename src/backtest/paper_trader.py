"""Paper Trading 模拟交易系统。

模拟每日信号 → 订单 → 成交 → 结算全流程。
输出：持仓明细、P&L、交易日志、与基准对比。
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class Order:
    """订单。"""
    order_id: str
    ticker: str
    date: str
    side: str          # "BUY" or "SELL"
    quantity: int
    price: float
    status: str = "PENDING"  # PENDING / FILLED / CANCELLED
    fill_price: float = 0.0
    fill_date: str = ""
    reason: str = ""


@dataclass
class Position:
    """持仓。"""
    ticker: str
    shares: int = 0
    avg_cost: float = 0.0
    market_value: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    entry_date: str = ""
    entry_price: float = 0.0


class PaperTradingEngine:
    """模拟交易引擎。

    每日处理流程：
      1. 接收日末信号
      2. 生成订单（次日开盘成交）
      3. 执行撮合（开盘价 + 滑点）
      4. 更新持仓 & 权益
      5. 记录日志

    用法:
        engine = PaperTradingEngine(initial_capital=100000)
        for date, row in df.iterrows():
            signal = strategy(row)  # 1=LONG, 0=FLAT
            engine.process_day(date, ticker, signal, row)
        report = engine.summary()
    """

    def __init__(
        self,
        initial_capital: float = 100_000.0,
        commission_per_share: float = 0.005,
        slippage: float = 0.0005,
        max_position_pct: float = 0.20,
        max_positions: int = 10,
        stop_loss_pct: float = -0.05,
        take_profit_pct: float = 0.10,
    ):
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.commission = commission_per_share
        self.slippage = slippage
        self.max_position_pct = max_position_pct
        self.max_positions = max_positions
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct

        self.positions: Dict[str, Position] = {}
        self.orders: List[Order] = []
        self.order_counter = 0
        self.equity_curve: List[dict] = []
        self.trade_log: List[dict] = []

    def reset(self):
        self.cash = self.initial_capital
        self.positions.clear()
        self.orders.clear()
        self.order_counter = 0
        self.equity_curve.clear()
        self.trade_log.clear()

    def total_equity(self, prices: Dict[str, float]) -> float:
        """计算总权益：现金 + 持仓市值。"""
        pos_value = sum(
            p.shares * prices.get(p.ticker, p.avg_cost)
            for p in self.positions.values()
        )
        return self.cash + pos_value

    def _check_stop_loss_take_profit(self, date, ticker: str, row: pd.Series) -> bool:
        """检查是否需要止损止盈。返回 True 表示已触发退出。"""
        pos = self.positions.get(ticker)
        if pos is None or pos.shares == 0:
            return False

        current_price = row.get("Close", row.get("Open", 0))
        ret = (current_price / pos.entry_price) - 1

        # 用日内高低价检查
        low_ret = (row.get("Low", current_price) / pos.entry_price) - 1
        high_ret = (row.get("High", current_price) / pos.entry_price) - 1

        exit_reason = ""
        if low_ret <= self.stop_loss_pct:
            exit_reason = f"STOP_LOSS ({ret*100:.1f}%)"
        elif high_ret >= self.take_profit_pct:
            exit_reason = f"TAKE_PROFIT ({ret*100:.1f}%)"

        if exit_reason:
            self._close_position(date, ticker, current_price, exit_reason)
            return True
        return False

    def _close_position(self, date, ticker: str, price: float, reason: str = ""):
        """平仓。成本含滑点+佣金。"""
        pos = self.positions.get(ticker)
        if pos is None or pos.shares == 0:
            return

        fill_price = price * (1 - self.slippage) - self.commission
        proceeds = fill_price * pos.shares

        pos.realized_pnl += proceeds - pos.avg_cost * pos.shares
        self.cash += proceeds
        self.trade_log.append({
            "date": date,
            "ticker": ticker,
            "side": "SELL",
            "shares": pos.shares,
            "price": fill_price,
            "pnl": round(proceeds - pos.avg_cost * pos.shares, 2),
            "pnl_pct": round((fill_price / pos.avg_cost - 1) if pos.avg_cost > 0 else 0, 6),
            "reason": reason or "SIGNAL_EXIT",
            "holding_days": (pd.Timestamp(date) - pd.Timestamp(pos.entry_date)).days,
        })
        pos.shares = 0
        pos.market_value = 0.0

    def _open_position(self, date, ticker: str, price: float, reason: str = ""):
        """开仓。成本含滑点+佣金。"""
        active = sum(1 for p in self.positions.values() if p.shares > 0)
        if active >= self.max_positions:
            return

        max_value = self.total_equity({ticker: price}) * self.max_position_pct
        cost_per_share = price * (1 + self.slippage) + self.commission
        shares = int(min(max_value // cost_per_share, self.cash // cost_per_share))
        if shares < 1:
            return

        cost = cost_per_share * shares
        if cost > self.cash:
            shares = int(self.cash // cost_per_share)
            if shares < 1:
                return
            cost = cost_per_share * shares

        self.cash -= cost

        if ticker in self.positions and self.positions[ticker].shares > 0:
            pos = self.positions[ticker]
            total_shares = pos.shares + shares
            pos.avg_cost = (pos.avg_cost * pos.shares + cost_per_share * shares) / total_shares
            pos.shares = total_shares
        else:
            self.positions[ticker] = Position(
                ticker=ticker,
                shares=shares,
                avg_cost=cost_per_share,
                entry_date=str(date),
                entry_price=cost_per_share,
            )

        self.trade_log.append({
            "date": date,
            "ticker": ticker,
            "side": "BUY",
            "shares": shares,
            "price": round(cost_per_share, 4),
            "pnl": 0,
            "pnl_pct": 0,
            "reason": reason or "SIGNAL_ENTRY",
            "holding_days": 0,
        })

    def process_day(self, date, ticker: str, signal: int, row: pd.Series):
        """处理单只股票单日流程。"""
        price = row.get("Open", row.get("Close", 0))

        # 1. 检查已有持仓的止损止盈
        if ticker in self.positions and self.positions[ticker].shares > 0:
            self._check_stop_loss_take_profit(date, ticker, row)

        # 2. 当前持仓
        has_position = (ticker in self.positions and self.positions[ticker].shares > 0)

        # 3. 信号驱动
        if signal == 1 and not has_position:
            self._open_position(date, ticker, price, "SIGNAL")
        elif signal == 0 and has_position:
            self._close_position(date, ticker, price, "SIGNAL")

        # 4. 更新持仓市值 & 快照权益
        for t, pos in self.positions.items():
            if pos.shares > 0:
                pos.market_value = pos.shares * row.get("Close", price)
                pos.unrealized_pnl = (row.get("Close", price) - pos.avg_cost) * pos.shares

        prices_snapshot = {ticker: row.get("Close", price)}
        for t, pos in self.positions.items():
            if t not in prices_snapshot and pos.shares > 0:
                prices_snapshot[t] = pos.avg_cost
        equity = self.total_equity(prices_snapshot)
        self.equity_curve.append({
            "date": str(date),
            "equity": equity,
            "cash": self.cash,
            "positions": sum(1 for p in self.positions.values() if p.shares > 0),
        })

    def process_day_batch(self, date, signals: Dict[str, int], data: Dict[str, pd.Series]):
        """批量处理多只股票当日流程。"""
        # 先检查止损止盈
        for ticker in list(self.positions.keys()):
            if ticker in data and self.positions[ticker].shares > 0:
                self._check_stop_loss_take_profit(date, ticker, data[ticker])

        # 信号驱动
        for ticker, signal in signals.items():
            if ticker in data:
                self.process_day(date, ticker, signal, data[ticker])

        # 快照
        prices = {t: data[t].get("Close", 0) for t in self.positions if t in data}
        equity = self.total_equity(prices)
        pos_count = sum(1 for p in self.positions.values() if p.shares > 0)
        self.equity_curve.append({
            "date": date,
            "equity": equity,
            "cash": self.cash,
            "positions": pos_count,
        })

    def summary(self) -> dict:
        """生成模拟交易绩效报告。"""
        if not self.equity_curve:
            return {"total_return": 0, "total_trades": 0}

        equity_df = pd.DataFrame(self.equity_curve)
        if "date" in equity_df.columns:
            equity_df["date"] = pd.to_datetime(equity_df["date"])
            equity_df = equity_df.set_index("date")

        equity = equity_df["equity"]
        initial = equity.iloc[0]
        final = equity.iloc[-1]
        total_ret = (final / initial) - 1

        n_days = len(equity)
        years = n_days / 252
        annual_ret = (1 + total_ret) ** (1 / max(years, 0.01)) - 1

        daily_ret = equity.pct_change().dropna()
        annual_vol = float(daily_ret.std() * np.sqrt(252))
        sharpe = float(daily_ret.mean() / daily_ret.std() * np.sqrt(252)) if daily_ret.std() > 0 else 0

        rolling_max = equity.cummax()
        dd = (equity - rolling_max) / rolling_max
        max_dd = float(dd.min())

        trades_df = pd.DataFrame(self.trade_log)
        if not trades_df.empty:
            sells = trades_df[trades_df["side"] == "SELL"]
            total_trades = len(sells)
            win_rate = float((sells["pnl"] > 0).mean()) if len(sells) > 0 else 0
            avg_win = sells.loc[sells["pnl"] > 0, "pnl"].mean() if (sells["pnl"] > 0).any() else 0
            avg_loss = abs(sells.loc[sells["pnl"] < 0, "pnl"].mean()) if (sells["pnl"] < 0).any() else 0
            profit_factor = avg_win / avg_loss if avg_loss > 0 else float("inf")
            total_pnl = float(sells["pnl"].sum())
        else:
            total_trades = win_rate = avg_win = avg_loss = profit_factor = total_pnl = 0

        return {
            "initial_capital": self.initial_capital,
            "final_equity": round(float(final), 2),
            "total_return": round(total_ret, 6),
            "annual_return": round(annual_ret, 6),
            "annual_volatility": round(annual_vol, 6),
            "sharpe_ratio": round(sharpe, 4),
            "max_drawdown": round(max_dd, 6),
            "total_trades": total_trades,
            "win_rate": round(win_rate, 4),
            "profit_factor": round(profit_factor, 4) if profit_factor != float("inf") else None,
            "avg_win": round(float(avg_win), 2),
            "avg_loss": round(float(avg_loss), 2),
            "total_pnl": round(float(total_pnl), 2),
        }

    def current_positions(self, prices: Dict[str, float]) -> pd.DataFrame:
        """返回当前持仓表。"""
        rows = []
        for t, p in self.positions.items():
            if p.shares > 0:
                price = prices.get(t, p.avg_cost)
                rows.append({
                    "ticker": t,
                    "shares": p.shares,
                    "avg_cost": round(p.avg_cost, 2),
                    "current_price": round(price, 2),
                    "market_value": round(p.shares * price, 2),
                    "unrealized_pnl": round((price - p.avg_cost) * p.shares, 2),
                    "unrealized_pnl_pct": round((price / p.avg_cost) - 1, 4),
                    "entry_date": p.entry_date,
                })
        return pd.DataFrame(rows)

    def export_trade_log(self) -> pd.DataFrame:
        """导出交易日志。"""
        return pd.DataFrame(self.trade_log)

    def export_equity_curve(self) -> pd.DataFrame:
        """导出权益曲线。"""
        return pd.DataFrame(self.equity_curve)

"""投资组合优化：仓位分配、VaR/CVaR、风险平价、相关矩阵、有效前沿。

用法:
    from src.risk.portfolio import PortfolioOptimizer
    po = PortfolioOptimizer(returns_df, capital=100000)
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from numpy.linalg import inv, pinv

logger = logging.getLogger(__name__)


def compute_returns_matrix(data: Dict[str, pd.DataFrame],
                           price_col: str = "Close",
                           period: str = "1d") -> pd.DataFrame:
    """从多只股票 OHLCV 数据构建收益率矩阵。

    Args:
        data: {ticker: DataFrame}
        price_col: 价格列名

    Returns:
        DataFrame: 行=日期, 列=ticker, 值=日收益率
    """
    returns = {}
    for ticker, df in data.items():
        if price_col not in df.columns or df.empty:
            continue
        ret = df[price_col].pct_change().dropna()
        if not ret.empty:
            returns[ticker] = ret

    return pd.DataFrame(returns).dropna(how="all")


class PortfolioOptimizer:
    """投资组合优化器。

    输入多只股票的日收益率矩阵，输出各类优化权重。
    """

    def __init__(self, returns: pd.DataFrame, capital: float = 100_000.0,
                 risk_free_rate: float = 0.0):
        """
        Args:
            returns: 日收益率矩阵 (行=日期, 列=ticker)
            capital: 总资金
            risk_free_rate: 年化无风险利率
        """
        self.returns = returns.dropna(how="all")
        self.tickers = list(self.returns.columns)
        self.n = len(self.tickers)
        self.capital = capital
        self.rf_annual = risk_free_rate
        self.rf_daily = risk_free_rate / 252

        self.mean_ret = self.returns.mean()  # 日度
        self.cov_matrix = self.returns.cov()  # 日度
        self.annual_ret = self.mean_ret * 252
        self.annual_cov = self.cov_matrix * 252
        self.corr_matrix = self.returns.corr()

    def equal_weight(self) -> Dict[str, float]:
        """等权重。"""
        return {t: 1.0 / self.n for t in self.tickers}

    def kelly_positions(self, max_position_pct: float = 0.20) -> Dict[str, float]:
        """凯利公式仓位分配。

        对每只股票独立计算 f* = (mu - rf) / sigma^2，
        然后归一化。适合短线独立策略。

        Args:
            max_position_pct: 单票仓位上限
        """
        weights = {}
        for t in self.tickers:
            mu = self.mean_ret[t] - self.rf_daily
            var = self.cov_matrix.loc[t, t]
            if var > 0:
                f = mu / var
            else:
                f = 0
            # 半凯利降低风险
            f = f / 2
            weights[t] = max(0, min(f, max_position_pct))
        # 归一化到总仓位 <= 1
        total = sum(weights.values())
        if total > 0:
            weights = {t: w / total for t, w in weights.items()}
        else:
            weights = self.equal_weight()
        return weights

    def risk_parity(self) -> Dict[str, float]:
        """风险平价 (Risk Parity) 权重。

        各资产对组合风险贡献相等。
        """
        cov = self.cov_matrix.values
        # Newton 法求解
        w = np.ones(self.n) / self.n
        for _ in range(100):
            sigma = np.sqrt(w.T @ cov @ w)
            marginal = cov @ w
            risk_contrib = w * marginal / sigma
            target = sigma / self.n
            grad = 2 * (risk_contrib - target) * marginal / sigma
            # 简单梯度下降
            w = w - 0.01 * grad
            w = np.maximum(w, 0)
            w = w / w.sum()
            if np.abs(risk_contrib - target).max() < 1e-6:
                break
        return {self.tickers[i]: float(w[i]) for i in range(self.n)}

    def min_variance(self) -> Dict[str, float]:
        """最小方差组合。"""
        cov = self.cov_matrix.values
        try:
            inv_cov = inv(cov)
        except np.linalg.LinAlgError:
            inv_cov = pinv(cov)
        ones = np.ones(self.n)
        w = inv_cov @ ones
        w = w / w.sum()
        return {self.tickers[i]: float(w[i]) for i in range(self.n)}

    def max_sharpe(self) -> Dict[str, float]:
        """最大 Sharpe 组合（切线组合）。"""
        cov = self.cov_matrix.values
        excess = self.mean_ret.values - self.rf_daily
        try:
            inv_cov = inv(cov)
        except np.linalg.LinAlgError:
            inv_cov = pinv(cov)
        w = inv_cov @ excess
        w = np.maximum(w, 0)
        w = w / w.sum()
        return {self.tickers[i]: float(w[i]) for i in range(self.n)}

    def efficient_frontier(self, points: int = 50) -> pd.DataFrame:
        """计算有效前沿。

        Returns:
            DataFrame with columns: return, risk, sharpe, weights
        """
        n = min(self.n, 100)  # 限制计算规模

        # 随机组合
        frontier = []
        for _ in range(5000):
            w = np.random.random(n)
            w = w / w.sum()
            mu = w @ self.annual_ret.values[:n]
            sigma = np.sqrt(w @ self.annual_cov.values[:n, :n] @ w)
            sharpe = (mu - self.rf_annual) / sigma if sigma > 0 else 0
            frontier.append({"return": mu, "risk": sigma, "sharpe": sharpe,
                           "weights": w.copy()})

        df = pd.DataFrame(frontier)
        # 沿风险排序取前沿包络
        df = df.sort_values("risk")
        hull = []
        max_ret = -np.inf
        for _, row in df.iterrows():
            if row["return"] > max_ret:
                max_ret = row["return"]
                hull.append(row)
        return pd.DataFrame(hull)

    def portfolio_stats(self, weights: Dict[str, float]) -> dict:
        """计算给定权重的组合统计量。"""
        w = np.array([weights.get(t, 0) for t in self.tickers])
        w = w / w.sum()

        ann_ret = float(w @ self.annual_ret.values)
        ann_vol = float(np.sqrt(w @ self.annual_cov.values @ w))
        sharpe = (ann_ret - self.rf_annual) / ann_vol if ann_vol > 0 else 0

        port_ret = (self.returns @ w).dropna()
        cum_ret = float(np.prod(1 + port_ret) - 1)
        rolling_max = (1 + port_ret).cumprod().cummax()
        dd = ((1 + port_ret).cumprod() - rolling_max) / rolling_max
        max_dd = float(dd.min())

        return {
            "annual_return": round(ann_ret, 6),
            "annual_volatility": round(ann_vol, 6),
            "sharpe_ratio": round(sharpe, 4),
            "total_return": round(cum_ret, 6),
            "max_drawdown": round(max_dd, 6),
            "n_assets": sum(1 for v in w if v > 0.001),
        }


def compute_var_cvar(equity_curve: pd.Series, confidence: float = 0.95,
                     horizon: int = 1) -> dict:
    """计算 VaR 和 CVaR。

    Args:
        equity_curve: 权益曲线
        confidence: 置信水平
        horizon: 预测期（天）

    Returns:
        {"var": float, "cvar": float, "var_pct": float, "cvar_pct": float}
    """
    returns = equity_curve.pct_change().dropna()
    if len(returns) < 20:
        return {"var": 0, "cvar": 0, "var_pct": 0, "cvar_pct": 0}

    sorted_ret = returns.sort_values()
    idx = int(len(sorted_ret) * (1 - confidence))
    var_daily = abs(sorted_ret.iloc[idx])
    cvar_daily = abs(sorted_ret.iloc[:idx].mean()) if idx > 0 else var_daily

    var_horizon = var_daily * np.sqrt(horizon)
    cvar_horizon = cvar_daily * np.sqrt(horizon)

    return {
        "var": round(float(var_horizon * equity_curve.iloc[-1]), 2),
        "cvar": round(float(cvar_horizon * equity_curve.iloc[-1]), 2),
        "var_pct": round(float(var_horizon), 6),
        "cvar_pct": round(float(cvar_horizon), 6),
        "confidence": confidence,
        "horizon_days": horizon,
    }


def position_sizing(weights: Dict[str, float], capital: float,
                    prices: Dict[str, float]) -> Dict[str, dict]:
    """将组合权重转换为实际股数。

    Returns:
        {ticker: {"weight": float, "shares": int, "value": float, "price": float}}
    """
    positions = {}
    for ticker, w in weights.items():
        price = prices.get(ticker, 0)
        value = capital * w
        shares = int(value // price) if price > 0 else 0
        positions[ticker] = {
            "weight": round(w, 4),
            "shares": shares,
            "value": round(shares * price, 2),
            "price": price,
        }
    return positions

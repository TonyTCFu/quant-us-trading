"""参数优化：网格搜索 + 滚动窗口样本外验证。

用法:
    from src.backtest.optimizer import optimize_ma_params, walk_forward_validate
"""

import logging
import warnings
from itertools import product
from typing import Dict, List, Optional, Tuple, Union

import numpy as np
import pandas as pd

from .engine import run_backtest, BacktestResult

logger = logging.getLogger(__name__)
warnings.filterwarnings("ignore", category=FutureWarning)


def _grid_to_param_list(
    fast_range: List[int],
    slow_range: List[int],
) -> List[Dict]:
    """生成 MA 参数网格，排除 fast >= slow。"""
    params = []
    for fast, slow in product(fast_range, slow_range):
        if fast < slow:
            params.append({"fast": fast, "slow": slow})
    return params


def optimize_ma_params(
    df: pd.DataFrame,
    fast_range: Optional[List[int]] = None,
    slow_range: Optional[List[int]] = None,
    metric: str = "sharpe_ratio",
    commission: float = 0.005,
    slippage: float = 0.0005,
    risk_config: Optional[dict] = None,
) -> List[Dict]:
    """对 MA 交叉策略做网格搜索。

    使用完整数据集上的 in-sample 回测。结果按 metric 排序。

    Args:
        df: OHLCV DataFrame
        fast_range: 快线周期列表，默认 [3,5,7,10,15,20,30,50]
        slow_range: 慢线周期列表，默认 [10,20,30,50,75,100,150,200]
        metric: 排序指标，支持 sharpe_ratio / total_return / profit_factor / win_rate

    Returns:
        按 metric 降序排列的参数结果列表，每项含 params, metrics
    """
    from src.signals.ma_cross import ma_cross_signal
    from src.risk.manager import apply_stop_loss_take_profit

    if fast_range is None:
        fast_range = [3, 5, 7, 10, 15, 20, 30, 50]
    if slow_range is None:
        slow_range = [10, 20, 30, 50, 75, 100, 150, 200]

    param_list = _grid_to_param_list(fast_range, slow_range)
    results = []

    for params in param_list:
        df_sig = ma_cross_signal(df, fast_period=params["fast"], slow_period=params["slow"])
        signal = df_sig["signal"]

        if risk_config:
            signal = apply_stop_loss_take_profit(
                df, signal,
                stop_loss_pct=risk_config.get("stop_loss", -0.05),
                take_profit_pct=risk_config.get("take_profit", 0.10),
            )

        result = run_backtest(
            df, signal,
            commission_per_share=commission,
            slippage=slippage,
        )

        results.append({
            "params": params,
            "metrics": result.summary,
            "n_trades": result.summary["n_trades"],
        })

        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("fast=%d slow=%d -> Sharpe=%.2f ret=%.1f%%",
                         params["fast"], params["slow"],
                         result.summary["sharpe_ratio"],
                         result.summary["total_return"] * 100)

    results.sort(key=lambda x: x["metrics"].get(metric, 0), reverse=True)
    return results


def walk_forward_validate(
    df: pd.DataFrame,
    param_list: List[Dict],
    train_months: int = 24,
    test_months: int = 6,
    metric: str = "sharpe_ratio",
    commission: float = 0.005,
    slippage: float = 0.0005,
    risk_config: Optional[dict] = None,
) -> Dict:
    """滚动窗口样本外验证 (Walk-Forward Validation)。

    将数据分为滚动窗口，每个窗口在训练集上选最优参数，
    在测试集上评估，最终汇总所有窗口的样本外表现。

    Args:
        df: OHLCV DataFrame
        param_list: 候选参数列表 [{fast: X, slow: Y}, ...]
        train_months: 训练窗口月数
        test_months: 测试窗口月数
        metric: 选优指标

    Returns:
        {
            "train_periods": [(start, end), ...],
            "test_periods": [(start, end), ...],
            "best_params_per_window": [...],
            "oof_returns": [...],       # 每段测试期收益
            "total_oof_return": float,  # 累计样本外收益
            "annual_oof_return": float,
            "sharpe_ratio": float,
            "win_rate": float,
        }
    """
    from src.signals.ma_cross import ma_cross_signal
    from src.risk.manager import apply_stop_loss_take_profit

    dates = df.index.sort_values()
    start_date = dates[0]
    end_date = dates[-1]

    # 生成滚动窗口
    train_start = start_date
    test_results = []
    best_params_per_window = []
    window_idx = 0

    while True:
        train_end = train_start + pd.DateOffset(months=train_months)
        test_start = train_end
        test_end = test_start + pd.DateOffset(months=test_months)

        if test_end > end_date:
            break

        train_df = df.loc[train_start:train_end]
        test_df = df.loc[test_start:test_end]

        if len(train_df) < 50 or len(test_df) < 10:
            train_start = test_start
            continue

        # 在训练集上选最优参数
        best_params = None
        best_metric = -999
        for p in param_list:
            df_sig = ma_cross_signal(train_df, fast_period=p["fast"], slow_period=p["slow"])
            signal = df_sig["signal"]
            if risk_config:
                signal = apply_stop_loss_take_profit(
                    train_df, signal,
                    stop_loss_pct=risk_config.get("stop_loss", -0.05),
                    take_profit_pct=risk_config.get("take_profit", 0.10),
                )
            result = run_backtest(
                train_df, signal,
                commission_per_share=commission,
                slippage=slippage,
            )
            val = result.summary.get(metric, 0)
            if val and val > best_metric:
                best_metric = val
                best_params = p.copy()

        if best_params is None:
            train_start = test_start
            continue

        best_params_per_window.append({
            "window": window_idx,
            "train": (train_start.strftime("%Y-%m-%d"), train_end.strftime("%Y-%m-%d")),
            "test": (test_start.strftime("%Y-%m-%d"), test_end.strftime("%Y-%m-%d")),
            "best_params": best_params,
            "train_metric": round(best_metric, 4),
        })

        # 在测试集上用最优参数评估
        df_sig = ma_cross_signal(test_df, fast_period=best_params["fast"], slow_period=best_params["slow"])
        signal = df_sig["signal"]
        if risk_config:
            signal = apply_stop_loss_take_profit(
                test_df, signal,
                stop_loss_pct=risk_config.get("stop_loss", -0.05),
                take_profit_pct=risk_config.get("take_profit", 0.10),
            )
        result = run_backtest(
            test_df, signal,
            commission_per_share=commission,
            slippage=slippage,
        )
        test_results.append(result.summary["total_return"])
        logger.info("Window %d: train %s~%s -> best=(%d,%d) metric=%.2f -> test ret=%.2f%%",
                    window_idx,
                    train_start.strftime("%Y-%m"), train_end.strftime("%Y-%m"),
                    best_params["fast"], best_params["slow"],
                    best_metric,
                    result.summary["total_return"] * 100)

        train_start = test_start
        window_idx += 1

    if not test_results:
        return {
            "train_periods": [],
            "test_periods": [],
            "best_params_per_window": best_params_per_window,
            "oof_returns": [],
            "total_oof_return": 0,
            "annual_oof_return": 0,
            "sharpe_ratio": 0,
            "win_rate": 0,
        }

    oof_array = np.array(test_results)
    total_ret = float(np.prod(1 + oof_array) - 1)
    total_months = window_idx * test_months
    annual_ret = float((1 + total_ret) ** (12 / max(total_months, 1)) - 1)
    sharpe = float(oof_array.mean() / oof_array.std() * np.sqrt(12 / test_months)) if oof_array.std() > 0 else 0
    win_rate = float((oof_array > 0).mean())

    return {
        "train_periods": [(t["train"][0], t["train"][1]) for t in best_params_per_window],
        "test_periods": [(t["test"][0], t["test"][1]) for t in best_params_per_window],
        "best_params_per_window": best_params_per_window,
        "oof_returns": test_results,
        "total_oof_return": round(total_ret, 6),
        "annual_oof_return": round(annual_ret, 6),
        "sharpe_ratio": round(sharpe, 4),
        "win_rate": round(win_rate, 4),
    }


def report_optimization(
    grid_results: List[Dict],
    top_n: int = 10,
) -> str:
    """格式化输出优化结果。"""
    lines = [f"{'Rank':<5} {'Fast':<6} {'Slow':<6} {'Sharpe':<8} {'Return':<12} {'MaxDD':<10} {'Trades':<8} {'WinRate':<8}"]
    lines.append("-" * 65)
    for i, r in enumerate(grid_results[:top_n]):
        m = r["metrics"]
        lines.append(
            f"{i+1:<5} {r['params']['fast']:<6} {r['params']['slow']:<6} "
            f"{m['sharpe_ratio']:<8.2f} {m['total_return']*100:<11.1f}% "
            f"{m['max_drawdown']*100:<9.1f}% {m['n_trades']:<8} {m['win_rate']*100:<7.1f}%"
        )
    return "\n".join(lines)

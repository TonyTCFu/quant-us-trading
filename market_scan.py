"""全市场回测扫描：对股票池中所有股票运行回测，按 Sharpe 排名。

用法: python3 market_scan.py [--top 10] [--start 2023-01-01]
"""

import argparse
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.data.fetcher import DataFetcher, _generate_synthetic_ohlcv
from src.utils.config import load_config
from src.signals.ma_cross import ma_cross_signal
from src.signals.combined import combined_strategy
from src.backtest.engine import run_backtest
from src.risk.manager import apply_stop_loss_take_profit, filter_liquidity

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def scan(data_dir: str = "data", start: str = "2023-01-01", end=None,
         strategy: str = "ma", fast: int = 5, slow: int = 20,
         use_risk: bool = True, stop_loss: float = -0.05, take_profit: float = 0.10,
         min_volume: int = 500_000, capital_per_stock: float = 100_000):
    """扫描所有缓存股票，返回排名结果。"""
    cfg = load_config()
    if end is None:
        end = datetime.now().strftime("%Y-%m-%d")

    # 发现所有缓存文件
    data_path = Path(data_dir)
    tickers = sorted(set(f.stem.split("_")[0] for f in data_path.glob("*_1d.csv")))

    if not tickers:
        logger.error("data/ 目录无缓存文件，请先运行 batch_pull.py")
        return []

    logger.info("扫描 %d 只股票: %s ~ %s", len(tickers), start, end)

    fetcher = DataFetcher(cfg["data"])
    results = []

    for ticker in tickers:
        try:
            df = fetcher.fetch(ticker, start=start, end=end)
        except Exception:
            continue

        if df is None or df.empty:
            continue

        # 流动性过滤
        if not filter_liquidity(df, min_volume):
            continue

        # 信号
        if strategy == "combined":
            df = combined_strategy(df, fast_ma=fast, slow_ma=slow)
        else:
            df = ma_cross_signal(df, fast_period=fast, slow_period=slow)

        signal = df["signal"]

        # 风控
        if use_risk:
            signal = apply_stop_loss_take_profit(df, signal, stop_loss_pct=stop_loss, take_profit_pct=take_profit)

        # 回测
        result = run_backtest(df, signal, initial_capital=capital_per_stock,
                             commission_per_share=cfg["backtest"]["commission_per_share"],
                             slippage=cfg["backtest"]["slippage"])
        s = result.summary
        if s["n_trades"] >= 3:
            results.append({"ticker": ticker, **s})

    # 排名
    results.sort(key=lambda x: x["sharpe_ratio"], reverse=True)
    return results


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--top", type=int, default=20)
    p.add_argument("--start", type=str, default="2023-01-01")
    p.add_argument("--end", type=str, default=None)
    p.add_argument("--strategy", type=str, default="ma", choices=["ma", "combined"])
    p.add_argument("--fast", type=int, default=5)
    p.add_argument("--slow", type=int, default=20)
    p.add_argument("--no-risk", action="store_true")
    args = p.parse_args()

    results = scan(
        start=args.start, end=args.end,
        strategy=args.strategy, fast=args.fast, slow=args.slow,
        use_risk=not args.no_risk,
    )

    if not results:
        print("无结果")
        return

    # 表格输出
    header = f"{'Rank':<5} {'Ticker':<7} {'Sharpe':<8} {'Return':<10} {'MaxDD':<9} {'WinRate':<8} {'Trades':<7} {'Vol':<8}"
    print(header)
    print("-" * 65)
    for i, r in enumerate(results[:args.top]):
        print(f"{i+1:<5} {r['ticker']:<7} {r['sharpe_ratio']:<8.2f} "
              f"{r['total_return']*100:<9.1f}% {r['max_drawdown']*100:<8.1f}% "
              f"{r['win_rate']*100:<7.1f}% {r['n_trades']:<7} {r['annual_volatility']*100:<7.1f}%")

    # 汇总
    valid = [r for r in results if r["sharpe_ratio"] != 0]
    if valid:
        avg_ret = sum(r["total_return"] for r in valid) / len(valid)
        avg_sharpe = sum(r["sharpe_ratio"] for r in valid) / len(valid)
        pos = sum(1 for r in valid if r["total_return"] > 0)
        print(f"\n总计 {len(results)} 只满足条件 | 平均收益: {avg_ret*100:.1f}% | 平均 Sharpe: {avg_sharpe:.2f} | 正收益比例: {pos}/{len(valid)}")

    # 保存完整结果
    df_out = pd.DataFrame(results)
    df_out.to_csv("outputs/market_scan.csv", index=False)
    print(f"完整结果已保存: outputs/market_scan.csv")


if __name__ == "__main__":
    main()

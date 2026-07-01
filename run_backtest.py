"""端到端回测运行脚本：数据获取 → 信号生成 → 回测 → 报告。

用法:
    python3 run_backtest.py                                    # MA cross (默认)
    python3 run_backtest.py --strategy combined                # MA + MACD + RSI 组合
    python3 run_backtest.py --tickers AAPL,TSLA,NVDA           # 自定义股票池
    python3 run_backtest.py --fast 10 --slow 30                # 自定义 MA 参数
    python3 run_backtest.py --strategy combined --rsi-oversold 20  # 自定义 RSI
"""

import argparse
import logging
import sys
from datetime import datetime, timedelta

from src.utils.config import load_config
from src.data.fetcher import DataFetcher
from src.signals.ma_cross import ma_cross_signal
from src.signals.combined import combined_strategy as run_combined_strategy
from src.backtest.engine import run_backtest
from src.risk.manager import apply_stop_loss_take_profit, compute_drawdown_constrained_equity, RiskConfig, filter_liquidity
from src.utils.plotting import plot_equity_and_drawdown, plot_signal_overlay

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def parse_args():
    p = argparse.ArgumentParser(description="美股短线回测")
    p.add_argument("--tickers", type=str, default=None,
                   help="股票代码，逗号分隔，默认使用 config 中的 tickers")
    p.add_argument("--start", type=str, default=None,
                   help="起始日期 YYYY-MM-DD")
    p.add_argument("--end", type=str, default=None,
                   help="结束日期 YYYY-MM-DD")
    p.add_argument("--strategy", type=str, default="ma",
                   choices=["ma", "combined"],
                   help="策略类型: ma = 纯均线, combined = MA+MACD+RSI")
    # MA params
    p.add_argument("--fast", type=int, default=20)
    p.add_argument("--slow", type=int, default=50)
    # MACD params
    p.add_argument("--macd-fast", type=int, default=12)
    p.add_argument("--macd-slow", type=int, default=26)
    p.add_argument("--macd-signal", type=int, default=9)
    # RSI params
    p.add_argument("--rsi-period", type=int, default=14)
    p.add_argument("--rsi-oversold", type=int, default=30)
    p.add_argument("--rsi-overbought", type=int, default=70)
    # 资金
    p.add_argument("--capital", type=float, default=None)
    # 风控
    p.add_argument("--risk", action="store_true", default=False,
                   help="启用止损止盈 + 回撤熔断")
    p.add_argument("--stop-loss", type=float, default=5.0,
                   help="止损比例%%，默认 5")
    p.add_argument("--take-profit", type=float, default=10.0,
                   help="止盈比例%%，默认 10")
    # 图表
    p.add_argument("--no-charts", action="store_true", help="跳过图表生成")
    return p.parse_args()


def main():
    args = parse_args()
    cfg = load_config()

    tickers = [t.strip() for t in args.tickers.split(",")] if args.tickers else cfg["data"]["tickers"]
    capital = args.capital or cfg["backtest"]["initial_capital"]
    lookback = cfg["data"]["lookback_days"]

    if args.start is None:
        args.start = (datetime.now() - timedelta(days=lookback)).strftime("%Y-%m-%d")
    if args.end is None:
        args.end = datetime.now().strftime("%Y-%m-%d")

    strategy_label = "MA Cross" if args.strategy == "ma" else "MA + MACD + RSI"

    logger.info("=" * 60)
    logger.info("回测参数 | %s", strategy_label)
    logger.info("股票池: %s", tickers)
    logger.info("时间范围: %s ~ %s", args.start, args.end)
    if args.strategy == "ma":
        logger.info("均线: fast=%d, slow=%d", args.fast, args.slow)
    else:
        logger.info("MA: %d/%d | MACD: %d/%d/%d | RSI: %d (%d/%d)",
                    args.fast, args.slow, args.macd_fast, args.macd_slow,
                    args.macd_signal, args.rsi_period, args.rsi_oversold, args.rsi_overbought)
    logger.info("初始资金: $%.2f", capital)
    logger.info("=" * 60)

    # 1. 数据
    fetcher = DataFetcher(cfg["data"])
    api_ok = fetcher.is_api_available()
    logger.info("数据源: %s", "yfinance API" if api_ok else "本地缓存")
    data = fetcher.fetch_batch(tickers, start=args.start, end=args.end)
    if not data:
        logger.error("未获取到任何数据，退出")
        sys.exit(1)

    # 2. 回测
    all_summaries = []
    for ticker in tickers:
        df = data.get(ticker)
        if df is None or df.empty:
            logger.warning("跳过 %s: 无数据", ticker)
            continue

        if args.strategy == "combined":
            df = run_combined_strategy(
                df,
                fast_ma=args.fast,
                slow_ma=args.slow,
                macd_fast=args.macd_fast,
                macd_slow=args.macd_slow,
                macd_signal_period=args.macd_signal,
                rsi_period=args.rsi_period,
                rsi_oversold=args.rsi_oversold,
                rsi_overbought=args.rsi_overbought,
            )
        else:
            df = ma_cross_signal(df, fast_period=args.fast, slow_period=args.slow)

        signal = df["signal"]

        # 风控：止损止盈
        if args.risk:
            sl_pct = -abs(args.stop_loss) / 100.0
            tp_pct = abs(args.take_profit) / 100.0
            logger.info("风控已启用: 止损 %.0f%% | 止盈 %.0f%%", args.stop_loss, args.take_profit)
            signal = apply_stop_loss_take_profit(df, signal, stop_loss_pct=sl_pct, take_profit_pct=tp_pct)
            df["signal_risk"] = signal

        result = run_backtest(
            df, signal,
            initial_capital=capital / len(tickers),
            commission_per_share=cfg["backtest"]["commission_per_share"],
            slippage=cfg["backtest"]["slippage"],
        )

        # 风控：回撤熔断（事后修正权益曲线 + 重算指标）
        if args.risk:
            max_dd = cfg["risk"]["max_drawdown_pct"]
            orig_equity = result.equity_curve.copy()
            result.equity_curve = compute_drawdown_constrained_equity(
                orig_equity, max_drawdown_pct=max_dd
            )
            # 用修正后权益重算 summary
            from src.backtest.engine import _compute_metrics
            constrained_ret = result.equity_curve.pct_change().fillna(0)
            result.summary = _compute_metrics(
                result.equity_curve, constrained_ret,
                result.trade_log, capital / len(tickers)
            )

        s = result.summary
        logger.info("")
        logger.info("--- %s ---", ticker)
        logger.info("总收益: %.2f%% | 年化: %.2f%% | 最大回撤: %.2f%%",
                    s["total_return"] * 100, s["annual_return"] * 100, s["max_drawdown"] * 100)
        logger.info("波动率: %.2f%% | Sharpe: %.2f | 胜率: %.2f%%",
                    s["annual_volatility"] * 100, s["sharpe_ratio"], s["win_rate"] * 100)
        logger.info("交易次数: %d | 平均持仓: %.0f 天 | 单笔最大亏损: %.2f%%",
                    s["n_trades"], s["avg_holding_days"], s["max_single_loss"] * 100)
        if s["profit_factor"] is not None:
            logger.info("盈亏比: %.2f", s["profit_factor"])

        all_summaries.append({"ticker": ticker, **s})

        equity_path = f"outputs/{ticker}_{args.strategy}_equity.csv"
        result.equity_curve.to_csv(equity_path)

        if not args.no_charts:
            tag = f"{ticker} [{strategy_label}]"
            chart_path = plot_equity_and_drawdown(result.equity_curve, ticker=tag)
            logger.info("图表: %s", chart_path)

    # 3. 汇总
    logger.info("")
    logger.info("=" * 60)
    logger.info("回测完成。%s ~ %s, 共 %d 只股票, 策略: %s",
                args.start, args.end, len(all_summaries), strategy_label)
    if all_summaries:
        avg_ret = sum(x["total_return"] for x in all_summaries) / len(all_summaries)
        avg_sharpe = sum(x["sharpe_ratio"] for x in all_summaries) / len(all_summaries)
        logger.info("组合平均收益: %.2f%% | 平均 Sharpe: %.2f",
                    avg_ret * 100, avg_sharpe)

    logger.info("注：如使用合成数据，结果仅供流程验证，不可作为投资依据。")


if __name__ == "__main__":
    main()

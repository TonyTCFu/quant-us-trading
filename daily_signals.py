"""每日信号报告生成器。

用法:
    python3 daily_signals.py                # 输出今日信号
    python3 daily_signals.py --top 10       # Top N 股票
    python3 daily_signals.py --date 2026-06-10  # 指定日期
"""

import argparse
import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.data.fetcher import DataFetcher
from src.utils.config import load_config
from src.signals.ma_cross import ma_cross_signal
from src.signals.indicators import compute_macd, compute_rsi, compute_bollinger, compute_atr
from src.signals.combined import combined_strategy
from src.risk.manager import apply_stop_loss_take_profit, filter_liquidity

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

# 最优参数（来自网格搜索 + 滚动验证）
OPTIMAL_PARAMS = {"fast": 5, "slow": 20, "stop_loss": -0.05, "take_profit": 0.10}

TOP_TICKERS = ["GS", "GE", "NVDA", "JNJ", "GOOGL", "CAT", "META", "JPM", "NFLX", "WMT"]


def load_top_tickers(n: int = 10, scan_file: str = "outputs/market_scan.csv") -> list:
    """从扫描结果加载 Top N 股票。"""
    path = Path(scan_file)
    if path.exists():
        df = pd.read_csv(path)
        if "ticker" in df.columns and "sharpe_ratio" in df.columns:
            df = df.sort_values("sharpe_ratio", ascending=False)
            return df["ticker"].head(n).tolist()
    return TOP_TICKERS[:n]


def generate_daily_signal(df: pd.DataFrame, date: str = None,
                          params: dict = None) -> dict:
    """为单只股票生成当日综合信号。"""
    if params is None:
        params = OPTIMAL_PARAMS

    fast, slow = params["fast"], params["slow"]
    sl, tp = params["stop_loss"], params["take_profit"]

    # 所有指标
    df = ma_cross_signal(df, fast_period=fast, slow_period=slow)
    df = compute_macd(df)
    df = compute_rsi(df, period=14)
    df = compute_bollinger(df)
    df = compute_atr(df, period=14)

    # 组合信号
    df = combined_strategy(df, fast_ma=fast, slow_ma=slow)

    # 风控修正
    df["signal_risk"] = apply_stop_loss_take_profit(df, df["signal"], stop_loss_pct=sl, take_profit_pct=tp)

    # 最近 5 天摘要
    tail = df.tail(5)
    last = df.iloc[-1]

    # 判断趋势
    if "fast_ma" in df.columns and "slow_ma" in df.columns:
        last_fast = last.get("fast_ma", last["Close"])
        last_slow = last.get("slow_ma", last["Close"])
        trend = "BULLISH" if last_fast > last_slow else "BEARISH"
    else:
        trend = "N/A"

    # RSI 区间
    rsi_val = last.get("rsi", 50)
    if pd.isna(rsi_val):
        rsi_val = 50
    if rsi_val < 30:
        rsi_zone = "OVERSOLD"
    elif rsi_val > 70:
        rsi_zone = "OVERBOUGHT"
    else:
        rsi_zone = "NEUTRAL"

    # 布林带位置
    close = last["Close"]
    bb_lower = last.get("bb_lower", close * 0.9)
    bb_upper = last.get("bb_upper", close * 1.1)
    bb_pct = (close - bb_lower) / (bb_upper - bb_lower) * 100 if bb_upper > bb_lower else 50

    # MACD 方向
    macd_hist = last.get("macd_histogram", 0)
    if pd.isna(macd_hist):
        macd_hist = 0
    macd_dir = "RISING" if macd_hist > 0 else "FALLING"

    # 持仓建议
    signal_raw = int(last.get("signal", 0))
    signal_risk = int(last.get("signal_risk", 0))

    if signal_risk == 1:
        action = "BUY"
    elif signal_raw == 1:
        action = "HOLD (risk stop)"
    elif signal_raw == 0 and macd_dir == "RISING":
        action = "WATCH"
    else:
        action = "AVOID"

    return {
        "date": str(last.name.date()) if hasattr(last.name, "date") else str(last.name),
        "close": round(float(close), 2),
        "fast_ma": round(float(last.get("fast_ma", 0)), 2),
        "slow_ma": round(float(last.get("slow_ma", 0)), 2),
        "rsi": round(float(rsi_val), 1),
        "rsi_zone": rsi_zone,
        "macd_histogram": round(float(macd_hist), 4),
        "macd_direction": macd_dir,
        "bb_position_pct": round(float(bb_pct), 1),
        "atr": round(float(last.get("atr", 0)), 2),
        "trend": trend,
        "signal_raw": signal_raw,
        "signal_risk": signal_risk,
        "action": action,
        "volume": int(last.get("Volume", 0)),
    }


def generate_report(signals: list, params: dict = None) -> str:
    """生成 Markdown 格式信号报告。"""
    if params is None:
        params = OPTIMAL_PARAMS

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"# 美股短线交易信号日报",
        f"",
        f"**生成时间**: {now} ET",
        f"**策略参数**: MA {params['fast']}/{params['slow']} | "
        f"止损 {abs(params['stop_loss'])*100:.0f}% | 止盈 {params['take_profit']*100:.0f}%",
        f"",
        f"## 信号汇总",
        f"",
        f"| Ticker | Close | Trend | RSI | MACD | BB% | ATR | Raw | Risk | Action |",
        f"|--------|-------|-------|-----|------|-----|-----|-----|------|--------|",
    ]

    buy_signals = []
    watch_signals = []

    for s in signals:
        ticker = s.get("ticker", s.get("close", 0))
        # Get ticker from surrounding context
        lines.append(
            f"| {s.get('ticker','?')} | ${s['close']:.2f} | {s['trend']} | "
            f"{s['rsi']:.0f}({s['rsi_zone'][:4]}) | {s['macd_direction'][:4]} | "
            f"{s['bb_position_pct']:.0f}% | ${s['atr']:.2f} | "
            f"{'LONG' if s['signal_raw'] else 'FLAT'} | "
            f"{'LONG' if s['signal_risk'] else 'FLAT'} | "
            f"**{s['action']}** |"
        )
        if s["action"] == "BUY":
            buy_signals.append(s)
        elif s["action"] == "WATCH":
            watch_signals.append(s)

    lines.extend([
        f"",
        f"## 操作建议",
        f"",
    ])

    if buy_signals:
        lines.append("### BUY 信号")
        for s in buy_signals:
            lines.append(f"- **{s['ticker']}**: ${s['close']:.2f}, RSI={s['rsi']:.0f}, "
                        f"止损大约 ${s['close']*(1+OPTIMAL_PARAMS['stop_loss']):.2f}, "
                        f"止盈大约 ${s['close']*(1+OPTIMAL_PARAMS['take_profit']):.2f}")
    else:
        lines.append("### 无 BUY 信号")
        lines.append("当前没有触发买入信号的股票。")

    if watch_signals:
        lines.append(f"\n### WATCH 候选 ({len(watch_signals)})")
        for s in watch_signals:
            lines.append(f"- {s['ticker']}: ${s['close']:.2f}, MACD 转强但未触发完整信号")

    lines.extend([
        f"",
        f"## 风控提醒",
        f"- 单票最大仓位: 20%",
        f"- 总仓位上限: 95%",
        f"- 最大回撤熔断: -20%",
        f"- 止损纪律: 每笔交易入场即设 {abs(OPTIMAL_PARAMS['stop_loss'])*100:.0f}% 止损",
        f"",
        f"---",
        f"*本报告由量化模型自动生成，仅供参考，不构成投资建议。*",
    ])

    return "\n".join(lines)


def main():
    p = argparse.ArgumentParser(description="每日信号报告")
    p.add_argument("--top", type=int, default=10)
    p.add_argument("--date", type=str, default=None)
    p.add_argument("--output", type=str, default=None)
    p.add_argument("--workers", type=int, default=4, help="并行扫描线程数")
    args = p.parse_args()

    cfg = load_config()
    tickers = load_top_tickers(n=args.top)
    fetcher = DataFetcher(cfg["data"])

    end = args.date or datetime.now().strftime("%Y-%m-%d")
    start = (datetime.strptime(end, "%Y-%m-%d") - timedelta(days=180)).strftime("%Y-%m-%d")

    logger.info("生成信号: %s ~ %s, %d 只股票 (并行 %d 线程)", start, end, len(tickers), args.workers)

    signals = []
    def process_one(ticker):
        try:
            df = fetcher.fetch(ticker, start=start, end=end)
            if df is None or df.empty:
                return None
            sig = generate_daily_signal(df, date=end)
            sig["ticker"] = ticker
            return sig
        except Exception as e:
            logger.warning("  %s: skip - %s", ticker, str(e)[:50])
            return None

    with ThreadPoolExecutor(max_workers=min(args.workers, len(tickers))) as executor:
        futures = {executor.submit(process_one, t): t for t in tickers}
        for future in as_completed(futures):
            result = future.result()
            if result is not None:
                signals.append(result)

    # 保持原始顺序
    ticker_order = {t: i for i, t in enumerate(tickers)}
    signals.sort(key=lambda s: ticker_order.get(s["ticker"], 999))

    report = generate_report(signals)
    print(report)

    if args.output:
        Path(args.output).write_text(report)
        logger.info("\n报告已保存: %s", args.output)


if __name__ == "__main__":
    main()

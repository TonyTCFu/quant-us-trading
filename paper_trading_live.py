#!/usr/bin/env python3
"""
【Claude Code】美股量化模型 — 实盘模拟交易系统

用法:
    python3 paper_trading_live.py init       # 初始化 $20,000 模拟盘，今日建仓
    python3 paper_trading_live.py update     # 每日运行：更新信号 → 调仓 → 生成报告
    python3 paper_trading_live.py report     # 查看当前持仓和绩效
    python3 paper_trading_live.py backtest   # 运行同行滚动回测

状态文件: outputs/paper_state.json
"""

import json
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.data.unified_fetcher import UnifiedFetcher
from src.signals.ma_cross import ma_cross_signal
from src.signals.indicators import compute_macd, compute_rsi, compute_bollinger, compute_atr
from src.risk.manager import apply_stop_loss_take_profit
from src.signals.macro_factors import MacroOverlay

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s",
                    datefmt="%Y-%m-%d %H:%M")
logger = logging.getLogger("PaperTrading")

# ── 配置 ──
INITIAL_CAPITAL = 20_000.0
UNIVERSE = ["GS", "GE", "NVDA", "JNJ", "GOOGL", "CAT", "META", "JPM", "NFLX", "WMT",
            "AMZN", "MSFT", "AAPL"]
MAX_POSITIONS = 8
MAX_POSITION_PCT = 0.25
STOP_LOSS_PCT = -0.05
TAKE_PROFIT_PCT = 0.10
COMMISSION = 0.005
SLIPPAGE = 0.0005
DEVIATION_THRESHOLD = 0.05   # 价格偏离 MA5 超过 5% 不追高，强趋势自动放宽至 8%
BENCHMARK = "SPY"
STATE_FILE = Path("outputs/paper_state.json")
TRADE_LOG_FILE = Path("outputs/paper_trades.csv")
EQUITY_FILE = Path("outputs/paper_equity.csv")

# NYSE 2026 休市日 (周末之外的假日)
NYSE_HOLIDAYS_2026 = {
    "2026-01-01",  # New Year's Day
    "2026-01-19",  # Martin Luther King Jr. Day
    "2026-02-16",  # Presidents' Day
    "2026-04-03",  # Good Friday
    "2026-05-25",  # Memorial Day
    "2026-06-19",  # Juneteenth
    "2026-07-03",  # Independence Day (observed)
    "2026-09-07",  # Labor Day
    "2026-11-26",  # Thanksgiving
    "2026-12-25",  # Christmas
}

def is_trading_day(date_str: str = None) -> bool:
    """判断是否为美股交易日 (周一到周五, 非 NYSE 假日)."""
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    if dt.weekday() >= 5:
        return False
    if date_str in NYSE_HOLIDAYS_2026:
        return False
    return True

def latest_trading_day() -> str:
    """返回最近一个交易日."""
    d = datetime.now()
    for _ in range(10):
        ds = d.strftime("%Y-%m-%d")
        if is_trading_day(ds):
            return ds
        d -= timedelta(days=1)
    return datetime.now().strftime("%Y-%m-%d")


# ── 状态管理 ──
def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {
        "initial_capital": INITIAL_CAPITAL,
        "cash": INITIAL_CAPITAL,
        "positions": {},
        "trade_log": [],
        "equity_history": [],
        "start_date": None,
        "last_update": None,
        "total_trades": 0,
        "total_commission": 0.0,
    }


def save_state(state: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, default=str))


# ── 信号生成 ──
def get_today_signals(tickers: List[str], fetcher) -> Dict[str, dict]:
    """生成今日所有股票的综合信号。"""
    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
    signals = {}

    for ticker in tickers:
        try:
            df = fetcher.fetch(ticker, start=start, end=end)
            if df is None or df.empty:
                continue
            df = ma_cross_signal(df, fast_period=5, slow_period=20)
            df = compute_macd(df)
            df = compute_rsi(df, period=14)
            df = compute_bollinger(df)
            df = compute_atr(df, period=14)

            sig_raw = int(df["signal"].iloc[-1])
            df_tail = df.tail(60)
            sig_full = apply_stop_loss_take_profit(
                df_tail, df_tail["signal"],
                stop_loss_pct=STOP_LOSS_PCT, take_profit_pct=TAKE_PROFIT_PCT
            )
            sig_risk = int(sig_full.iloc[-1])

            last = df.iloc[-1]
            rsi_val = last.get("rsi", 50)
            if pd.isna(rsi_val):
                rsi_val = 50
            macd_h = last.get("macd_histogram", 0)
            if pd.isna(macd_h):
                macd_h = 0

            fast_ma = last.get("fast_ma", last["Close"])
            slow_ma = last.get("slow_ma", last["Close"])
            trend = "BULL" if (not pd.isna(fast_ma) and not pd.isna(slow_ma) and fast_ma > slow_ma) else "BEAR"

            action = "BUY" if sig_risk == 1 else ("WATCH" if sig_raw == 1 else "SELL")

            signals[ticker] = {
                "close": round(float(last["Close"]), 2),
                "signal_raw": sig_raw,
                "signal_risk": sig_risk,
                "action": action,
                "trend": trend,
                "rsi": round(float(rsi_val), 1),
                "macd_hist": round(float(macd_h), 4),
                "atr": round(float(last.get("atr", 0)), 2),
                "fast_ma": round(float(fast_ma), 2) if not pd.isna(fast_ma) else 0,
                "slow_ma": round(float(slow_ma), 2) if not pd.isna(slow_ma) else 0,
            }
        except Exception as e:
            logger.warning(f"  {ticker}: skip — {e}")

    return signals


# ── 订单执行 ──
def execute_buy(state: dict, ticker: str, price: float, shares: int, date: str, reason: str = "SIGNAL"):
    """执行买入。成本含滑点+佣金。非交易日拒绝执行。"""
    if not is_trading_day(date):
        logger.warning("  ⏸ BUY  %s 跳过 — %s 非交易日", ticker, date)
        return
    cost_per_share = price * (1 + SLIPPAGE) + COMMISSION
    cost = shares * cost_per_share
    if cost > state["cash"]:
        shares = int(state["cash"] // cost_per_share)
        if shares < 1:
            return
        cost = shares * cost_per_share

    state["cash"] -= cost
    state["total_commission"] += shares * COMMISSION

    if ticker in state["positions"] and state["positions"][ticker]["shares"] > 0:
        pos = state["positions"][ticker]
        total = pos["shares"] + shares
        pos["avg_cost"] = round((pos["avg_cost"] * pos["shares"] + cost_per_share * shares) / total, 4)
        pos["shares"] = total
    else:
        state["positions"][ticker] = {
            "shares": shares,
            "avg_cost": round(cost_per_share, 4),
            "entry_date": date,
            "entry_price": round(cost_per_share, 4),
        }

    state["trade_log"].append({
        "date": date, "ticker": ticker, "side": "BUY",
        "shares": shares, "price": round(price, 2),
        "cost": round(cost, 2), "reason": reason,
    })
    state["total_trades"] += 1
    logger.info(f"  BUY  {ticker}: {shares} × ${price:.2f} = ${cost:.2f}  (avg_cost=${cost_per_share:.4f})")


def execute_sell(state: dict, ticker: str, price: float, date: str, reason: str = "SIGNAL"):
    """执行卖出。成本含滑点+佣金。非交易日拒绝执行。"""
    if not is_trading_day(date):
        logger.warning("  ⏸ SELL %s 跳过 — %s 非交易日", ticker, date)
        return
    pos = state["positions"].get(ticker)
    if not pos or pos["shares"] <= 0:
        return

    shares = pos["shares"]
    proceeds_per_share = price * (1 - SLIPPAGE) - COMMISSION
    proceeds = shares * proceeds_per_share
    pnl = proceeds - pos["avg_cost"] * shares
    pnl_pct = (proceeds_per_share / pos["avg_cost"] - 1) if pos["avg_cost"] > 0 else 0

    state["cash"] += proceeds
    state["total_commission"] += shares * COMMISSION

    state["trade_log"].append({
        "date": date, "ticker": ticker, "side": "SELL",
        "shares": shares, "price": round(price, 2),
        "proceeds": round(proceeds, 2), "pnl": round(pnl, 2),
        "pnl_pct": round(pnl_pct, 4),
        "holding_days": (datetime.strptime(date, "%Y-%m-%d") -
                         datetime.strptime(pos["entry_date"], "%Y-%m-%d")).days,
        "reason": reason,
    })
    state["total_trades"] += 1

    pos["shares"] = 0
    logger.info(f"  SELL {ticker}: {shares} × ${price:.2f} = ${proceeds:.2f}  PnL: ${pnl:+.2f} ({pnl_pct*100:+.1f}%)")


# ── 权益快照 ──
def snapshot_equity(state: dict, prices: Dict[str, float], date: str):
    """记录当日权益。"""
    pos_value = sum(
        state["positions"][t]["shares"] * prices.get(t, state["positions"][t]["avg_cost"])
        for t in state["positions"] if state["positions"][t]["shares"] > 0
    )
    equity = state["cash"] + pos_value
    ret = (equity / INITIAL_CAPITAL - 1) if state["equity_history"] else 0

    state["equity_history"].append({
        "date": date,
        "equity": round(equity, 2),
        "cash": round(state["cash"], 2),
        "position_value": round(pos_value, 2),
        "total_return": round(ret, 4),
        "positions": sum(1 for p in state["positions"].values() if p["shares"] > 0),
    })
    state["last_update"] = date


# ── 绩效指标 ──
def compute_performance(state: dict) -> dict:
    """计算当前绩效指标。"""
    hist = state["equity_history"]
    if len(hist) < 2:
        sells = [t for t in state.get("trade_log", []) if t["side"] == "SELL"]
        win_trades = [t for t in sells if t.get("pnl", 0) > 0]
        return {"total_return": 0, "annual_return": 0, "sharpe": 0, "max_drawdown": 0,
                "total_trades": state["total_trades"], "days": len(hist),
                "closed_trades": len(sells),
                "win_rate": round(len(win_trades)/max(len(sells),1),4),
                "total_pnl": round(sum(t.get("pnl",0) for t in sells),2),
                "total_commission": round(state.get("total_commission",0),2),
                "start_date": hist[0]["date"] if hist else None,
                "last_update": hist[-1]["date"] if hist else None}

    equity = pd.Series([h["equity"] for h in hist])
    initial = equity.iloc[0]
    final = equity.iloc[-1]
    total_ret = float(final / initial - 1)

    n_days = len(equity)
    years = n_days / 252
    annual_ret = float((1 + total_ret) ** (1 / max(years, 0.02)) - 1)

    daily_ret = equity.pct_change().dropna()
    sharpe = float(daily_ret.mean() / daily_ret.std() * np.sqrt(252)) if daily_ret.std() > 0 else 0

    rolling_max = equity.cummax()
    dd = (equity - rolling_max) / rolling_max
    max_dd = float(dd.min())

    # Count closed trades
    trade_log = state.get("trade_log", [])
    sells = [t for t in trade_log if t["side"] == "SELL"]
    win_trades = [t for t in sells if t.get("pnl", 0) > 0]

    return {
        "total_return": round(total_ret, 6),
        "annual_return": round(annual_ret, 6),
        "sharpe": round(sharpe, 4),
        "max_drawdown": round(max_dd, 6),
        "total_trades": state["total_trades"],
        "closed_trades": len(sells),
        "win_rate": round(len(win_trades) / max(len(sells), 1), 4),
        "total_pnl": round(sum(t.get("pnl", 0) for t in sells), 2),
        "total_commission": round(state.get("total_commission", 0), 2),
        "days": len(hist),
        "start_date": hist[0]["date"] if hist else None,
        "last_update": hist[-1]["date"] if hist else None,
    }


# ═══════════════════════════════════════════════════
# 主命令
# ═══════════════════════════════════════════════════

def cmd_init():
    """初始化模拟盘：$20,000 起始资金，今日建仓。"""
    today = datetime.now().strftime("%Y-%m-%d")
    state = load_state()

    if state["start_date"] is not None:
        logger.warning("模拟盘已初始化，起始日期: %s", state["start_date"])
        logger.warning("如需重置请删除 %s", STATE_FILE)
        return

    logger.info("=" * 70)
    logger.info("初始化模拟盘 — 初始资金 $%s — %s", f"{INITIAL_CAPITAL:,.0f}", today)
    logger.info("策略: MA 5/20 + 止损5%%/止盈10%% | 股票池: %d 只 | 最大持仓: %d",
                len(UNIVERSE), MAX_POSITIONS)
    logger.info("=" * 70)

    fetcher = UnifiedFetcher(cache_dir="data")

    # 生成今日信号
    logger.info("扫描信号...")
    signals = get_today_signals(UNIVERSE, fetcher)

    buy_list = sorted(
        [(t, s) for t, s in signals.items() if s["action"] == "BUY"],
        key=lambda x: -x[1]["rsi"]
    )[:MAX_POSITIONS]

    if not buy_list:
        logger.warning("今日无 BUY 信号！使用 WATCH 列表顶部股票建仓")
        watch_list = sorted(
            [(t, s) for t, s in signals.items() if s["action"] in ("BUY", "WATCH")],
            key=lambda x: (-x[1]["signal_raw"], -x[1]["rsi"])
        )
        buy_list = watch_list[:min(5, len(watch_list))]

    if not buy_list:
        logger.error("无法建仓：无可用标的")
        return

    # 等权分配
    per_stock = INITIAL_CAPITAL / len(buy_list)
    state["start_date"] = today
    state["last_update"] = today
    state["cash"] = INITIAL_CAPITAL
    state["positions"] = {}

    logger.info("\n初始建仓:")
    for ticker, sig in buy_list:
        shares = int(per_stock // (sig["close"] * (1 + SLIPPAGE) + COMMISSION))
        if shares > 0:
            execute_buy(state, ticker, sig["close"], shares, today, "INIT")

    # 快照
    prices = {t: s["close"] for t, s in signals.items()}
    snapshot_equity(state, prices, today)

    # 保存
    save_state(state)

    # 报告
    invested = INITIAL_CAPITAL - state["cash"]
    logger.info("\n" + "=" * 70)
    logger.info("建仓完成！")
    logger.info("  初始资金:  $%s", f"{INITIAL_CAPITAL:,.2f}")
    logger.info("  已投资:    $%s (%.1f%%)", f"{invested:,.2f}", invested / INITIAL_CAPITAL * 100)
    logger.info("  剩余现金:  $%s", f"{state['cash']:,.2f}")
    logger.info("  持仓数:    %d", sum(1 for p in state["positions"].values() if p["shares"] > 0))
    logger.info("  状态文件:  %s", STATE_FILE)
    logger.info("=" * 70)
    print()
    cmd_report()


def cmd_update():
    """每日更新：重新扫描信号，调仓。"""
    today = datetime.now().strftime("%Y-%m-%d")
    state = load_state()

    if state["start_date"] is None:
        logger.error("模拟盘未初始化，请先运行: python3 paper_trading_live.py init")
        return

    if not is_trading_day(today):
        logger.warning("今日 %s 非美股交易日，跳过更新。最近交易日: %s", today, latest_trading_day())
        return

    if state["last_update"] == today and state["equity_history"]:
        logger.warning("今日已更新 (%s)，跳过。", today)
        return

    logger.info("=" * 70)
    logger.info("每日更新 — %s", today)
    logger.info("=" * 70)

    fetcher = UnifiedFetcher(cache_dir="data")

    # 先增量刷新数据
    logger.info("刷新行情数据...")
    refresh_result = fetcher.refresh_daily(UNIVERSE)
    logger.info("数据刷新完成: %d updated, %d failed",
                len(refresh_result["updated"]), len(refresh_result["failed"]))

    # 宏观因子评估
    mo = MacroOverlay(fetcher)
    macro = mo.evaluate(today)
    macro_multiplier = macro["position_multiplier"]
    logger.info("宏观环境: 风险评分 %d/100 | 建议 %s | 仓位系数 %.0f%%",
                int(macro["macro_risk_score"]), macro["recommendation"], macro_multiplier * 100)
    for f in macro["factors"]:
        if f["score"] >= 40:
            logger.info("  ⚠ %s: %s (score=%.0f)", f["name"], f["detail"], f["score"])

    # 扫描信号
    signals = get_today_signals(UNIVERSE, fetcher)
    prices = {t: s["close"] for t, s in signals.items()}

    # 补全缺失价格（从持仓取）
    for t in state["positions"]:
        if t not in prices and state["positions"][t]["shares"] > 0:
            try:
                df = fetcher.fetch(t, start=(datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d"), end=today)
                if df is not None and not df.empty:
                    prices[t] = float(df["Close"].iloc[-1])
            except:
                pass

    # 检查止损止盈
    for ticker in list(state["positions"].keys()):
        pos = state["positions"][ticker]
        if pos["shares"] <= 0:
            continue
        price = prices.get(ticker, pos["avg_cost"])
        ret = (price / pos["entry_price"]) - 1

        if ret <= STOP_LOSS_PCT:
            execute_sell(state, ticker, price, today, f"STOP_LOSS ({ret*100:.1f}%)")
        elif ret >= TAKE_PROFIT_PCT:
            execute_sell(state, ticker, price, today, f"TAKE_PROFIT ({ret*100:.1f}%)")

    # 信号驱动调仓（含宏观因子缩放）
    if macro_multiplier == 0:
        logger.info("⚠ %s — 宏���因子仓位系数为0，暂停所有交易", macro["recommendation"])
    else:
        for ticker, sig in signals.items():
            has_position = (ticker in state["positions"] and state["positions"][ticker]["shares"] > 0)
            active_count = sum(1 for p in state["positions"].values() if p["shares"] > 0)

            if sig["action"] == "BUY" and not has_position and active_count < MAX_POSITIONS:
                # 偏离阈值检查: 价格偏离 MA5 太远不追高
                fast_ma = sig.get("fast_ma", 0)
                if fast_ma > 0:
                    deviation = abs(sig["close"] - fast_ma) / fast_ma
                    # 强趋势(MA5 > MA20 + 3%)自动放宽阈值到 8%
                    slow_ma = sig.get("slow_ma", 0)
                    if slow_ma > 0 and fast_ma > slow_ma * 1.03:
                        threshold = 0.08
                    else:
                        threshold = DEVIATION_THRESHOLD
                    if deviation > threshold:
                        logger.info("  ⏭ SKIP %s: 偏离 MA5 %.1f%% > %.1f%%，不追高",
                                    ticker, deviation * 100, threshold * 100)
                        continue
                current_equity = state["cash"] + sum(
                    state["positions"][t]["shares"] * prices.get(t, state["positions"][t]["avg_cost"])
                    for t in state["positions"] if state["positions"][t]["shares"] > 0
                )
                max_val = current_equity * MAX_POSITION_PCT * macro_multiplier
                shares = int(max_val // (sig["close"] * (1 + SLIPPAGE) + COMMISSION))
                if shares > 0:
                    reason = f"SIGNAL" if macro_multiplier >= 0.75 else f"SIGNAL ({macro['recommendation']})"
                    execute_buy(state, ticker, sig["close"], shares, today, reason)

            elif sig["action"] == "SELL" and has_position:
                execute_sell(state, ticker, sig["close"], today, "SIGNAL_SELL")

    # 快照
    snapshot_equity(state, prices, today)

    # 保存
    save_state(state)

    # 绩效
    perf = compute_performance(state)
    active = sum(1 for p in state["positions"].values() if p["shares"] > 0)

    logger.info("\n" + "=" * 70)
    logger.info("更新完成 — %s", today)
    logger.info("  宏观评分: %d/100 | 建议: %s | 仓位系数: %.0f%%",
                int(macro["macro_risk_score"]), macro["recommendation"], macro_multiplier * 100)
    logger.info("  权益:     $%s", f"{state['equity_history'][-1]['equity']:,.2f}")
    logger.info("  收益:     %+.2f%% (年化: %+.2f%%)", perf["total_return"] * 100, perf["annual_return"] * 100)
    logger.info("  持仓数:   %d", active)
    logger.info("  累计交易: %d", perf["total_trades"])
    logger.info("  胜率:     %.0f%%", perf["win_rate"] * 100)
    logger.info("=" * 70)

    _export_files(state)

    # 刷新 Dashboard
    try:
        from build_dashboard_live import build_dashboard
        dash_path = build_dashboard()
        logger.info("Dashboard: %s", dash_path)
    except Exception as e:
        logger.warning("Dashboard 刷新失败: %s", e)


def cmd_report():
    """查看当前持仓和绩效。"""
    state = load_state()
    if state["start_date"] is None:
        logger.info("模拟盘尚未初始化。运行: python3 paper_trading_live.py init")
        return

    perf = compute_performance(state)
    prices = {}
    # Try to get today's prices
    try:
        fetcher = UnifiedFetcher(cache_dir="data")
        today = datetime.now().strftime("%Y-%m-%d")
        signals = get_today_signals(list(state["positions"].keys()), fetcher)
        prices = {t: s["close"] for t, s in signals.items()}
    except:
        pass

    print()
    print("=" * 70)
    print("  【Claude Code】美股量化模型 — 模拟盘持仓报告")
    print("=" * 70)
    print(f"  起始日期: {state['start_date']}   |   初始资金: ${INITIAL_CAPITAL:,.0f}")
    print(f"  最后更新: {state['last_update']}   |   运行天数: {perf['days']}")
    print()

    # 持仓表
    active_positions = {t: p for t, p in state["positions"].items() if p["shares"] > 0}
    if active_positions:
        total_value = 0
        total_pnl = 0
        print(f"  {'Ticker':<7} {'Shares':>6} {'Entry':>10} {'Current':>10} {'Value':>10} {'PnL':>10} {'PnL%':>8} {'Days':>5}")
        print(f"  {'-'*7} {'-'*6} {'-'*10} {'-'*10} {'-'*10} {'-'*10} {'-'*8} {'-'*5}")
        for t, p in active_positions.items():
            price = prices.get(t, p["avg_cost"])
            value = p["shares"] * price
            pnl = (price - p["avg_cost"]) * p["shares"]
            pnl_pct = (price / p["entry_price"] - 1) * 100
            days = (datetime.strptime(state["last_update"], "%Y-%m-%d") -
                    datetime.strptime(p["entry_date"], "%Y-%m-%d")).days
            total_value += value
            total_pnl += pnl
            print(f"  {t:<7} {p['shares']:>6} ${p['entry_price']:>9.2f} ${price:>9.2f} ${value:>9,.0f} ${pnl:>9,.0f} {pnl_pct:>7.1f}% {days:>4}d")
        print(f"  {'-'*7} {'-'*6} {'-'*10} {'-'*10} {'-'*10} {'-'*10} {'-'*8} {'-'*5}")
        print(f"  {'合计':<7} {'':>6} {'':>10} {'':>10} ${total_value:>9,.0f} ${total_pnl:>9,.0f}")
    else:
        print("  (无持仓)")

    total_equity = state["cash"] + sum(
        state["positions"][t]["shares"] * prices.get(t, state["positions"][t]["avg_cost"])
        for t in state["positions"] if state["positions"][t]["shares"] > 0
    )

    print(f"\n  现金:     ${state['cash']:,.2f}")
    print(f"  总权益:   ${total_equity:,.2f}")
    print(f"  总收益:   {perf['total_return']*100:+.2f}%")
    print(f"  年化收益: {perf['annual_return']*100:+.2f}%")
    print(f"  Sharpe:   {perf['sharpe']:.2f}")
    print(f"  最大回撤: {perf['max_drawdown']*100:+.2f}%")
    print(f"  累计交易: {perf['total_trades']}  (已平仓: {perf['closed_trades']})")
    print(f"  胜率:     {perf['win_rate']*100:.0f}%")
    print(f"  已实现PnL: ${perf['total_pnl']:+,.2f}")
    print(f"  佣金合计: ${perf['total_commission']:,.2f}")

    # 年化目标进度
    target_annual = 0.08
    days_run = max(perf["days"], 1)
    target_cumulative = (1 + target_annual) ** (days_run / 252) - 1
    actual_cumulative = perf["total_return"]
    progress = actual_cumulative / target_cumulative * 100 if target_cumulative != 0 else 0

    print(f"\n  🎯 目标: 年化 +8% | 当前累计目标: {target_cumulative*100:+.2f}% | 实际: {actual_cumulative*100:+.2f}%")
    if progress >= 100:
        print(f"  ✅ 进度: {progress:.0f}% — 超过目标！")
    elif progress >= 50:
        print(f"  ⏳ 进度: {progress:.0f}% — 追赶目标中")
    else:
        print(f"  ⚠️  进度: {progress:.0f}% — 需要改善")

    print("=" * 70)

    # 权益曲线摘要
    if len(state["equity_history"]) >= 2:
        eq = [h["equity"] for h in state["equity_history"]]
        print(f"\n  权益范围: ${min(eq):,.0f} ~ ${max(eq):,.0f}")
        print(f"  最近5日权益:")
        for h in state["equity_history"][-5:]:
            print(f"    {h['date']}  ${h['equity']:,.2f}  ({h['total_return']*100:+.2f}%)")


def cmd_backtest():
    """运行同行滚动回测（与模拟盘同期对比）。"""
    state = load_state()
    if state["start_date"] is None:
        logger.error("模拟盘未初始化")
        return

    today = datetime.now().strftime("%Y-%m-%d")
    start = state["start_date"]

    logger.info("滚动回测: %s → %s, MA 5/20 + SL5%%/TP10%%", start, today)

    fetcher = UnifiedFetcher(cache_dir="data")

    from src.backtest.engine import run_backtest

    print()
    print(f"  {'Ticker':<7} {'Sharpe':>7} {'Return':>9} {'MaxDD':>8} {'WinRate':>7} {'Trades':>6}")
    print(f"  {'-'*7} {'-'*7} {'-'*9} {'-'*8} {'-'*7} {'-'*6}")

    total_ret = 0
    count = 0
    for ticker in UNIVERSE:
        try:
            df = fetcher.fetch(ticker, start=start, end=today)
            if df is None or df.empty:
                continue
            df = ma_cross_signal(df, fast_period=5, slow_period=20)
            sig = apply_stop_loss_take_profit(df, df["signal"], stop_loss_pct=STOP_LOSS_PCT, take_profit_pct=TAKE_PROFIT_PCT)
            result = run_backtest(df, sig, initial_capital=INITIAL_CAPITAL / 5,
                                 commission_per_share=COMMISSION, slippage=SLIPPAGE)
            s = result.summary
            print(f"  {ticker:<7} {s['sharpe_ratio']:>7.2f} {s['total_return']*100:>8.1f}% {s['max_drawdown']*100:>7.1f}% {s['win_rate']*100:>6.1f}% {s['n_trades']:>6}")
            total_ret += s["total_return"]
            count += 1
        except Exception as e:
            logger.warning(f"  {ticker}: skip — {e}")

    if count > 0:
        avg_ret = total_ret / count
        annual = (1 + avg_ret) ** (252 / max((datetime.strptime(today, "%Y-%m-%d") -
                                               datetime.strptime(start, "%Y-%m-%d")).days, 1)) - 1
        print(f"  {'-'*7} {'-'*7} {'-'*9} {'-'*8} {'-'*7} {'-'*6}")
        print(f"  {'均值':<7} {'':>7} {avg_ret*100:>8.1f}% 年化{annual*100:+.1f}%")
        print(f"\n  回测平均 vs 模拟盘: {avg_ret*100:+.1f}% vs {compute_performance(state)['total_return']*100:+.2f}%")


def _export_files(state: dict):
    """导出 CSV 文件。"""
    # 交易日志
    if state.get("trade_log"):
        pd.DataFrame(state["trade_log"]).to_csv(TRADE_LOG_FILE, index=False)

    # 权益曲线
    if state.get("equity_history"):
        pd.DataFrame(state["equity_history"]).to_csv(EQUITY_FILE, index=False)


# ═══════════════════════════════════════
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1].lower()
    if cmd == "init":
        cmd_init()
    elif cmd == "update":
        cmd_update()
    elif cmd == "report":
        cmd_report()
    elif cmd == "backtest":
        cmd_backtest()
    else:
        print(f"未知命令: {cmd}")
        print(__doc__)

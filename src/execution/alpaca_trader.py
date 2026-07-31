"""Alpaca Paper 真实下单执行引擎 — 将量化模型信号转化为实际订单。

用法:
    from src.execution.alpaca_trader import AlpacaTrader
    trader = AlpacaTrader()
    trader.sync_from_simulation(paper_state)  # 同步仿真持仓到 Alpaca
    trader.execute_buy("AAPL", 10, 150.0)    # 真实下单
"""

import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (
    MarketOrderRequest, LimitOrderRequest, GetOrdersRequest
)
from alpaca.trading.enums import OrderSide, OrderType, TimeInForce, OrderStatus, QueryOrderStatus

logger = logging.getLogger(__name__)

PAPER_BASE_URL = "https://paper-api.alpaca.markets"

def _is_market_open() -> bool:
    """判断美股是否在交易时间 — 调用 Alpaca 官方时钟。"""
    try:
        from alpaca.trading.client import TradingClient
        api_key = os.getenv("ALPACA_API_KEY")
        secret_key = os.getenv("ALPACA_SECRET_KEY")
        if api_key and secret_key:
            tc = TradingClient(api_key, secret_key, paper=True)
            clock = tc.get_clock()
            return clock.is_open
    except Exception:
        pass
    # Fallback: UTC-based ET conversion
    from datetime import timezone
    now_utc = datetime.now(timezone.utc)
    et_offset = -4 if now_utc.month > 3 and now_utc.month < 11 else -5
    now_et = now_utc + timedelta(hours=et_offset)
    if now_et.weekday() >= 5:
        return False
    open_t = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
    close_t = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
    return open_t <= now_et <= close_t


class AlpacaTrader:
    """Alpaca Paper 真实下单器。

    连接量化模型的信号输出 → 实际 Alpaca Paper Trading 账户。
    """

    def __init__(self):
        api_key = os.getenv("ALPACA_API_KEY")
        secret_key = os.getenv("ALPACA_SECRET_KEY")
        if not api_key or not secret_key:
            raise ValueError("ALPACA_API_KEY/ALPACA_SECRET_KEY 未设置 — 检查 .env")
        self.tc = TradingClient(api_key, secret_key, paper=True, url_override=PAPER_BASE_URL)
        self._acct = None
        self._positions = None

    @property
    def account(self):
        if self._acct is None:
            self._acct = self.tc.get_account()
        return self._acct

    @property
    def positions(self):
        if self._positions is None:
            self._positions = {p.symbol: p for p in self.tc.get_all_positions()}
        return self._positions

    def refresh(self):
        """刷新账户和持仓缓存."""
        self._acct = self.tc.get_account()
        self._positions = {p.symbol: p for p in self.tc.get_all_positions()}
        return self

    # ─── 查询 ───
    def get_cash(self) -> float:
        return float(self.account.cash)

    def get_equity(self) -> float:
        return float(self.account.equity)

    def get_position(self, symbol: str):
        """获取某只股票的当前 Alpaca 持仓，无则返回 None."""
        self.refresh()
        return self.positions.get(symbol)

    def get_all_positions(self) -> Dict[str, any]:
        self.refresh()
        return self.positions

    # ─── 下单 ───
    def buy_market(self, symbol: str, qty: int) -> Optional[dict]:
        """市价买入，返回订单摘要。非交易时间拒绝执行。"""
        if not _is_market_open():
            logger.warning("⏸ BUY %s: 非交易时间，跳过市价单", symbol)
            return None
        try:
            order = self.tc.submit_order(
                MarketOrderRequest(symbol=symbol, qty=qty, side=OrderSide.BUY,
                                   time_in_force=TimeInForce.DAY))
            logger.info("✅ BUY %s %d股 市价单已提交 id=%s", symbol, qty, order.id)
            return {"id": str(order.id), "symbol": symbol, "qty": qty, "side": "BUY", "type": "MARKET"}
        except Exception as e:
            logger.error("❌ BUY %s 失败: %s", symbol, e)
            return None

    def sell_market(self, symbol: str, qty: int) -> Optional[dict]:
        """市价卖出全部持仓。非交易时间拒绝执行。"""
        if not _is_market_open():
            logger.warning("⏸ SELL %s: 非交易时间，跳过市价单", symbol)
            return None
        try:
            order = self.tc.submit_order(
                MarketOrderRequest(symbol=symbol, qty=qty, side=OrderSide.SELL,
                                   time_in_force=TimeInForce.DAY))
            logger.info("✅ SELL %s %d股 市价单已提交 id=%s", symbol, qty, order.id)
            return {"id": str(order.id), "symbol": symbol, "qty": qty, "side": "SELL", "type": "MARKET"}
        except Exception as e:
            logger.error("❌ SELL %s 失败: %s", symbol, e)
            return None

    def buy_limit(self, symbol: str, qty: int, limit_price: float) -> Optional[dict]:
        """限价买入，非交易时间提交盘前单。"""
        try:
            order = self.tc.submit_order(
                LimitOrderRequest(symbol=symbol, qty=qty, side=OrderSide.BUY,
                                  limit_price=limit_price, time_in_force=TimeInForce.DAY))
            logger.info("✅ BUY LIMIT %s %d股 @$%.2f id=%s", symbol, qty, limit_price, order.id)
            return {"id": str(order.id), "symbol": symbol, "qty": qty, "side": "BUY", "type": "LIMIT", "price": limit_price}
        except Exception as e:
            logger.error("❌ BUY LIMIT %s 失败: %s", symbol, e)
            return None

    def sell_limit(self, symbol: str, qty: int, limit_price: float) -> Optional[dict]:
        """限价卖出。"""
        try:
            order = self.tc.submit_order(
                LimitOrderRequest(symbol=symbol, qty=qty, side=OrderSide.SELL,
                                  limit_price=limit_price, time_in_force=TimeInForce.DAY))
            logger.info("✅ SELL LIMIT %s %d股 @$%.2f id=%s", symbol, qty, limit_price, order.id)
            return {"id": str(order.id), "symbol": symbol, "qty": qty, "side": "SELL", "type": "LIMIT", "price": limit_price}
        except Exception as e:
            logger.error("❌ SELL LIMIT %s 失败: %s", symbol, e)
            return None

    def liquidate(self, symbol: str) -> Optional[dict]:
        """一键清仓单个标的 (Alpaca 内置方法)."""
        try:
            self.tc.close_position(symbol)
            logger.info("✅ LIQUIDATE %s 已提交", symbol)
            return {"symbol": symbol, "side": "SELL", "type": "LIQUIDATE"}
        except Exception as e:
            logger.error("❌ LIQUIDATE %s 失败: %s", symbol, e)
            return None

    def liquidate_all(self) -> List[dict]:
        """一键清仓所有持仓."""
        self.refresh()
        results = []
        for sym in list(self.positions.keys()):
            r = self.liquidate(sym)
            if r:
                results.append(r)
            time.sleep(0.3)  # 避免 API 限流
        return results

    # ─── 同步 ───
    def sync_to_simulation(self, state: dict):
        """从 Alpaca 真实持仓覆盖 paper_state.json 的持仓数据。"""
        self.refresh()
        acct = self.account
        cash = float(acct.cash)
        equity = float(acct.equity)

        state["cash"] = round(cash, 2)
        state["positions"] = {}
        for sym, pos in self.positions.items():
            state["positions"][sym] = {
                "shares": int(float(pos.qty)),
                "avg_cost": round(float(pos.avg_entry_price), 4),
                "entry_date": "",
                "entry_price": round(float(pos.avg_entry_price), 4),
            }
        logger.info(f"📊 同步完成: {len(self.positions)} 持仓, 现金 ${cash:,.2f}, 权益 ${equity:,.2f}")
        return state

    def execute_daily_signals(self, paper_state: dict, signals: dict, macro: dict):
        """根据日信号 + 宏观因子在 Alpaca 上真实下单。

        执行规则:
        1. TP/SL 触发 → 清仓对应标的
        2. SELL 信号 + 有持仓 → 卖出
        3. BUY 信号 + 无持仓 + 仓位未满 → 市价买入 (交易时间) 或限价买入 (非交易时间)
        4. macro_multiplier = 0 → 只卖不买
        """
        self.refresh()
        macro_mult = macro.get("position_multiplier", 1.0)
        alpaca_positions = self.get_all_positions()
        active_count = len(alpaca_positions)

        MAX_POS = 6
        MAX_PCT = 0.28
        equity = self.get_equity()

        orders = []

        # First: sell positions that have SELL signal or hit TP/SL
        for sym, sig in signals.items():
            action = sig.get("action", "AVOID")
            has_pos = sym in alpaca_positions
            pos = alpaca_positions.get(sym)
            qty = int(float(pos.qty)) if pos else 0

            if not has_pos or qty <= 0:
                continue

            entry = float(pos.avg_entry_price) if pos else 0
            current = sig.get("close", 0)
            ret = (current / entry - 1) if entry > 0 else 0
            reason = sig.get("reason", "")

            # TP/SL trigger via signal
            if action == "SELL" or "STOP_LOSS" in reason or "TAKE_PROFIT" in reason:
                o = self.sell_market(sym, qty) if _is_market_open() else self.sell_limit(sym, qty, current * 0.995)
                if o:
                    orders.append(o)

        # Second: buy new positions
        if macro_mult > 0:
            for sym, sig in signals.items():
                has_pos = sym in alpaca_positions
                if has_pos:
                    continue
                if active_count + len([o for o in orders if o["side"] == "BUY"]) >= MAX_POS:
                    break
                if sig.get("action") != "BUY":
                    continue

                price = sig.get("close", 0)
                if price <= 0:
                    continue

                # 偏离阈值检查
                fast_ma = sig.get("fast_ma", 0)
                if fast_ma > 0:
                    deviation = abs(price - fast_ma) / fast_ma
                    if deviation > 0.08:
                        logger.info("⏭ SKIP %s: 偏离 %.1f%%", sym, deviation * 100)
                        continue

                qty = int(equity * MAX_PCT // (price * 1.01))
                if qty < 1:
                    continue

                o = self.buy_market(sym, qty) if _is_market_open() else self.buy_limit(sym, qty, price * 1.005)
                if o:
                    orders.append(o)
                time.sleep(0.3)

        return orders

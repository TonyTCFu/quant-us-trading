"""Alpaca Paper Trading 数据源 — 免费、实时行情、历史数据、零 credits。

Alpaca Paper Trading 免费层级：
  - IEX 历史日线 (15 分钟延迟) — 足够回测和日线信号
  - 交易日 4:00 PM ET 收盘后可拉取当日数据
  - 无 API 调用限制，仅限 Paper 环境

用法:
    from src.data.alpaca_fetcher import AlpacaFetcher
    fetcher = AlpacaFetcher()
    df = fetcher.fetch("AAPL", start="2024-01-01")
"""

import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient

logger = logging.getLogger(__name__)

PRICE_COLS = ["Open", "High", "Low", "Close", "Volume"]


def _load_alpaca_env():
    """从 .env 加载 Alpaca 凭证。"""
    env_path = Path(__file__).resolve().parent.parent.parent / ".env"
    if not env_path.exists():
        env_path = Path(".env")
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    k, v = k.strip(), v.strip().strip('"').strip("'")
                    if k.startswith("ALPACA_") and k not in os.environ:
                        os.environ[k] = v


_load_alpaca_env()


class AlpacaFetcher:
    """Alpaca 行情数据获取器 — 替代 yfinance/QVeris。

    两层缓存: 内存 (pandas) + 本地 CSV
    """

    def __init__(self, cache_dir: str = "data"):
        self.api_key = os.getenv("ALPACA_API_KEY")
        self.secret_key = os.getenv("ALPACA_SECRET_KEY")
        if not self.api_key or not self.secret_key:
            raise RuntimeError("Alpaca 凭证未设置。检查 .env 中的 ALPACA_API_KEY / ALPACA_SECRET_KEY")

        self.data_client = StockHistoricalDataClient(
            api_key=self.api_key, secret_key=self.secret_key
        )
        self.trading_client = TradingClient(
            api_key=self.api_key, secret_key=self.secret_key, paper=True
        )
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._mem_cache: Dict[str, pd.DataFrame] = {}

    # ── Public API ──

    def fetch(
        self,
        ticker: str,
        start: str = "2023-01-01",
        end: Optional[str] = None,
        frequency: str = "1d",
    ) -> pd.DataFrame:
        """获取单只股票历史日线（复权）。缓存优先。"""
        if end is None:
            end = datetime.now().strftime("%Y-%m-%d")

        # 先查内存
        cache_key = f"{ticker}_{frequency}"
        if cache_key in self._mem_cache:
            df = self._mem_cache[cache_key]
            if not df.empty:
                return self._slice(df, start, end)

        # 再查本地 CSV
        df = self._load_csv(ticker, frequency)
        if df is not None and not df.empty:
            c_min = df.index.min()
            c_max = df.index.max()
            start_ts = pd.Timestamp(start, tz="US/Eastern")
            end_ts = pd.Timestamp(end, tz="US/Eastern")
            if c_min <= start_ts and c_max >= end_ts:
                self._mem_cache[cache_key] = df
                return self._slice(df, start, end)

        # 从 Alpaca 拉取
        df = self._fetch_bars(ticker, start=start, end=end)
        if df is not None and not df.empty:
            # 与缓存合并
            if df is not None and not df.empty:
                cached = self._load_csv(ticker, frequency)
                if cached is not None and not cached.empty:
                    merged = pd.concat([cached, df])
                    merged = merged[~merged.index.duplicated(keep="last")]
                    merged = merged.sort_index()
                else:
                    merged = df
                self._save_csv(ticker, frequency, merged)
                self._mem_cache[cache_key] = merged
            return self._slice(df, start, end) if not df.empty else df

        return pd.DataFrame(columns=PRICE_COLS)

    def refresh_daily(self, tickers: List[str]) -> dict:
        """每日增量刷新：拉取最新 30 天，与缓存合并。"""
        updated, failed = [], []
        end = datetime.now()
        start = end - timedelta(days=30)

        for ticker in tickers:
            try:
                df = self._fetch_bars(ticker, start=start.strftime("%Y-%m-%d"),
                                      end=end.strftime("%Y-%m-%d"))
                if df is not None and not df.empty:
                    cache_key = f"{ticker}_1d"
                    cached = self._load_csv(ticker, "1d")
                    if cached is not None and not cached.empty:
                        merged = pd.concat([cached, df])
                        merged = merged[~merged.index.duplicated(keep="last")]
                        merged = merged.sort_index()
                    else:
                        merged = df
                    self._save_csv(ticker, "1d", merged)
                    self._mem_cache[cache_key] = merged
                    updated.append(ticker)
                    logger.debug("%s: refreshed, %d bars", ticker, len(merged))
                else:
                    failed.append(ticker)
            except Exception as e:
                logger.warning("%s: refresh failed — %s", ticker, e)
                failed.append(ticker)
            time.sleep(0.1)
        return {"updated": updated, "failed": failed}

    def fetch_batch(
        self,
        tickers: List[str],
        start: str = "2023-01-01",
        end: Optional[str] = None,
        frequency: str = "1d",
    ) -> Dict[str, pd.DataFrame]:
        """批量获取，一次性 API 调用。"""
        if end is None:
            end = datetime.now().strftime("%Y-%m-%d")

        # 先查缓存
        results = {}
        remaining = []
        for ticker in tickers:
            cache_key = f"{ticker}_{frequency}"
            if cache_key in self._mem_cache:
                results[ticker] = self._slice(self._mem_cache[cache_key], start, end)
            else:
                df = self._load_csv(ticker, frequency)
                if df is not None and not df.empty:
                    c_min = df.index.min()
                    c_max = df.index.max()
                    if c_min <= pd.Timestamp(start, tz="US/Eastern") and c_max >= pd.Timestamp(end, tz="US/Eastern"):
                        self._mem_cache[cache_key] = df
                        results[ticker] = self._slice(df, start, end)
                        continue
                remaining.append(ticker)

        if not remaining:
            return results

        # 批量拉取
        try:
            req = StockBarsRequest(
                symbol_or_symbols=remaining,
                timeframe=TimeFrame.Day,
                start=datetime.strptime(start, "%Y-%m-%d"),
                end=datetime.strptime(end, "%Y-%m-%d"),
                adjustment="all",
                limit=5000,
                feed="iex",
            )
            bars = self.data_client.get_stock_bars(req)
            for sym, bar_list in bars.data.items():
                df = _bars_to_df(bar_list)
                if df is not None and not df.empty:
                    # 合并缓存
                    cache_key = f"{sym}_{frequency}"
                    cached = self._load_csv(sym, frequency)
                    if cached is not None and not cached.empty:
                        merged = pd.concat([cached, df])
                        merged = merged[~merged.index.duplicated(keep="last")]
                        merged = merged.sort_index()
                    else:
                        merged = df
                    self._save_csv(sym, frequency, merged)
                    self._mem_cache[cache_key] = merged
                    results[sym] = self._slice(merged, start, end)
        except Exception as e:
            logger.warning("批量获取失败: %s", e)
            # 逐只回退
            for ticker in remaining:
                try:
                    df = self.fetch(ticker, start=start, end=end)
                    if not df.empty:
                        results[ticker] = df
                except:
                    pass
        return results

    def get_account(self) -> dict:
        """读取 Alpaca Paper 账户信息（只读）。"""
        acct = self.trading_client.get_account()
        return {
            "equity": float(acct.equity),
            "cash": float(acct.cash),
            "buying_power": float(acct.buying_power),
            "status": acct.status,
            "portfolio_value": float(acct.portfolio_value),
        }

    def get_alpaca_positions(self) -> pd.DataFrame:
        """读取 Alpaca 实际持仓（只读，不交易）。"""
        positions = self.trading_client.get_all_positions()
        if not positions:
            return pd.DataFrame(columns=["symbol", "qty", "avg_entry_price", "current_price", "unrealized_pl"])
        rows = []
        for p in positions:
            rows.append({
                "symbol": p.symbol,
                "qty": float(p.qty),
                "avg_entry_price": float(p.avg_entry_price),
                "current_price": float(p.current_price),
                "market_value": float(p.market_value),
                "unrealized_pl": float(p.unrealized_pl),
                "unrealized_plpc": float(p.unrealized_plpc),
            })
        return pd.DataFrame(rows)

    # ── Internal ──

    def _fetch_bars(self, ticker: str, start: str, end: str) -> Optional[pd.DataFrame]:
        """从 Alpaca API 拉取单只股票日线。"""
        try:
            # Paper Trading 免费版仅支持 IEX feed（15 分钟延迟）
            req_start = datetime.strptime(start, "%Y-%m-%d")
            req_end = datetime.strptime(end, "%Y-%m-%d") + timedelta(days=1)
            req = StockBarsRequest(
                symbol_or_symbols=[ticker],
                timeframe=TimeFrame.Day,
                start=req_start, end=req_end,
                adjustment="all", limit=5000, feed="iex",
            )
            bars = self.data_client.get_stock_bars(req)
            for sym, bar_list in bars.data.items():
                return _bars_to_df(bar_list)
        except Exception as e:
            logger.warning("%s: Alpaca fetch failed — %s", ticker, e)
        return None

    def _cache_path(self, ticker: str, frequency: str) -> Path:
        return self.cache_dir / f"{ticker}_{frequency}.csv"

    def _load_csv(self, ticker: str, frequency: str) -> Optional[pd.DataFrame]:
        path = self._cache_path(ticker, frequency)
        if not path.exists():
            return None
        try:
            df = pd.read_csv(path, index_col=0)
            if df.empty:
                return None
            df.index = pd.DatetimeIndex(pd.to_datetime(df.index, utc=True)).tz_convert("US/Eastern")
            return df
        except:
            return None

    def _save_csv(self, ticker: str, frequency: str, df: pd.DataFrame):
        path = self._cache_path(ticker, frequency)
        df_save = df.copy()
        try:
            df_save.index = df_save.index.tz_convert("UTC").tz_localize(None)
        except:
            pass
        df_save.to_csv(path)

    @staticmethod
    def _slice(df: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
        start_ts = pd.Timestamp(start, tz="US/Eastern")
        end_ts = pd.Timestamp(end, tz="US/Eastern")
        return df.loc[start_ts:end_ts]


def _bars_to_df(bar_list) -> pd.DataFrame:
    """将 Alpaca Bar 列表转为标准 OHLCV DataFrame。"""
    if not bar_list:
        return pd.DataFrame(columns=PRICE_COLS)
    records = []
    for b in bar_list:
        records.append({
            "Open": round(float(b.open), 4),
            "High": round(float(b.high), 4),
            "Low": round(float(b.low), 4),
            "Close": round(float(b.close), 4),
            "Volume": int(b.volume),
        })
    df = pd.DataFrame(records, index=[b.timestamp for b in bar_list])
    df.index = pd.DatetimeIndex(df.index).tz_convert("US/Eastern")
    df = df.sort_index()
    return df

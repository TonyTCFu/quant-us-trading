"""统一数据源适配器 — Alpaca IEX (主) + QVeris Alpha Vantage (备)。

策略：
  1. 优先 Alpaca IEX — 免费、直连、历史深
  2. Alpaca 失败时自动回退 QVeris — 付费但可靠
  3. 两层缓存: 内存 → 本地 CSV，双源数据自动合并

用法:
    from src.data.unified_fetcher import UnifiedFetcher
    fetcher = UnifiedFetcher()
    df = fetcher.fetch("AAPL", start="2023-01-01")
"""

import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from .alpaca_fetcher import AlpacaFetcher, PRICE_COLS

logger = logging.getLogger(__name__)


class UnifiedFetcher:
    """双数据源行情获取器 — Alpaca 主, QVeris 备。"""

    def __init__(self, cache_dir: str = "data"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._primary_ok = False
        self._backup_ok = False
        self._alpaca = None
        self._mem_cache: Dict[str, pd.DataFrame] = {}

        # 初始化 Alpaca
        try:
            self._alpaca = AlpacaFetcher(cache_dir=cache_dir)
            self._primary_ok = True
            logger.info("数据源: Alpaca IEX (主)")
        except Exception as e:
            logger.warning("Alpaca 不可用: %s", str(e)[:80])
            self._alpaca = None

        # 探测 QVeris
        self._qveris_available = self._probe_qveris()
        if self._qveris_available:
            logger.info("数据源: QVeris (备)")

    def _probe_qveris(self) -> bool:
        """探测 QVeris CLI 是否可用。"""
        import subprocess, os
        try:
            env = {**os.environ, "HTTP_PROXY": "", "HTTPS_PROXY": ""}
            result = subprocess.run(
                ["qveris", "whoami"], capture_output=True, text=True,
                env=env, timeout=10,
            )
            return result.returncode == 0
        except:
            return False

    def fetch(
        self,
        ticker: str,
        start: str = "2023-01-01",
        end: Optional[str] = None,
        frequency: str = "1d",
    ) -> pd.DataFrame:
        """获取单只股票数据。"""
        if end is None:
            end = datetime.now().strftime("%Y-%m-%d")

        cache_key = f"{ticker}_{frequency}"
        start_ts = pd.Timestamp(start, tz="US/Eastern")
        end_ts = pd.Timestamp(end, tz="US/Eastern")

        # 内存缓存
        if cache_key in self._mem_cache:
            df = self._mem_cache[cache_key]
            c_min, c_max = df.index.min(), df.index.max()
            if c_min <= start_ts and c_max >= end_ts:
                return df.loc[start_ts:end_ts]

        # CSV 缓存
        df = self._load_csv(ticker, frequency)
        if df is not None and not df.empty:
            c_min, c_max = df.index.min(), df.index.max()
            if c_min <= start_ts and c_max >= end_ts:
                self._mem_cache[cache_key] = df
                return df.loc[start_ts:end_ts]

        # 拉取数据
        df = self._fetch_with_fallback(ticker, start, end, frequency)
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
            return merged.loc[start_ts:end_ts]
        return pd.DataFrame(columns=PRICE_COLS)

    def fetch_batch(
        self,
        tickers: List[str],
        start: str = "2023-01-01",
        end: Optional[str] = None,
        frequency: str = "1d",
    ) -> Dict[str, pd.DataFrame]:
        """批量获取。"""
        if end is None:
            end = datetime.now().strftime("%Y-%m-%d")
        results = {}
        for ticker in tickers:
            try:
                df = self.fetch(ticker, start=start, end=end, frequency=frequency)
                if not df.empty:
                    results[ticker] = df
            except Exception as e:
                logger.warning("%s: %s", ticker, e)
        return results

    def refresh_daily(self, tickers: List[str]) -> dict:
        """每日增量刷新。"""
        updated, failed = [], []
        end = datetime.now()
        start = end - timedelta(days=30)

        for ticker in tickers:
            try:
                df = self._fetch_with_fallback(
                    ticker, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"), "1d"
                )
                if df is not None and not df.empty:
                    cached = self._load_csv(ticker, "1d")
                    if cached is not None and not cached.empty:
                        merged = pd.concat([cached, df])
                        merged = merged[~merged.index.duplicated(keep="last")]
                        merged = merged.sort_index()
                    else:
                        merged = df
                    self._save_csv(ticker, "1d", merged)
                    self._mem_cache[f"{ticker}_1d"] = merged
                    updated.append(ticker)
                else:
                    failed.append(ticker)
            except Exception as e:
                logger.warning("%s: refresh failed — %s", ticker, e)
                failed.append(ticker)
            time.sleep(0.1)
        return {"updated": updated, "failed": failed}

    def get_alpaca_account(self) -> Optional[dict]:
        """读取 Alpaca 账户（如果可用）。"""
        if self._alpaca:
            try:
                return self._alpaca.get_account()
            except:
                pass
        return None

    def get_alpaca_positions(self) -> pd.DataFrame:
        """读取 Alpaca 持仓（只读）。"""
        if self._alpaca:
            try:
                return self._alpaca.get_alpaca_positions()
            except:
                pass
        return pd.DataFrame()

    def is_api_available(self) -> bool:
        """至少有一个数据源可用。"""
        return self._primary_ok or self._qveris_available

    # ── Internal ──

    def _fetch_with_fallback(
        self, ticker: str, start: str, end: str, frequency: str
    ) -> Optional[pd.DataFrame]:
        """Alpaca 优先，失败回退 QVeris。"""
        # 1. Alpaca
        if self._alpaca:
            try:
                df = self._alpaca._fetch_bars(ticker, start, end)
                if df is not None and not df.empty:
                    return df
            except Exception as e:
                logger.debug("Alpaca %s: %s", ticker, str(e)[:60])

        # 2. QVeris
        if self._qveris_available:
            try:
                df = self._fetch_from_qveris(ticker, start, end)
                if df is not None and not df.empty:
                    return df
            except Exception as e:
                logger.debug("QVeris %s: %s", ticker, str(e)[:60])

        return None

    def _fetch_from_qveris(self, ticker: str, start: str, end: str) -> Optional[pd.DataFrame]:
        """通过 QVeris Alpha Vantage 获取数据。"""
        import json, os, ssl, subprocess, urllib.request

        # 判断是否需要 full outputsize
        req_start = datetime.strptime(start, "%Y-%m-%d")
        months_back = (datetime.now() - req_start).days / 30
        outputsize = "full" if months_back > 3 else "compact"

        env = {**os.environ, "HTTP_PROXY": "", "HTTPS_PROXY": ""}
        params = json.dumps({
            "function": "TIME_SERIES_DAILY_ADJUSTED",
            "symbol": ticker,
            "outputsize": outputsize,
        })

        result = subprocess.run(
            ["qveris", "call", "alphavantage.time-series.daily-adjusted.v1",
             "--params", params, "--json"],
            capture_output=True, text=True, env=env, timeout=60,
        )
        data = json.loads(result.stdout)
        if not data.get("success"):
            return None

        res = data["result"]
        if res.get("full_content_file_url"):
            ctx = ssl.create_default_context()
            req = urllib.request.Request(res["full_content_file_url"])
            with urllib.request.urlopen(req, timeout=60, context=ctx) as resp:
                full = json.loads(resp.read().decode())
        elif res.get("truncated_content"):
            full = json.loads(res["truncated_content"])
        else:
            return None

        ts = full.get("Time Series (Daily)", {})
        if not ts:
            return None

        rows = {}
        for date, vals in ts.items():
            close = float(vals["4. close"])
            adj_close = float(vals["5. adjusted close"])
            factor = adj_close / close if close > 0 else 1.0
            rows[date] = {
                "Open": round(float(vals["1. open"]) * factor, 4),
                "High": round(float(vals["2. high"]) * factor, 4),
                "Low": round(float(vals["3. low"]) * factor, 4),
                "Close": round(adj_close, 4),
                "Volume": int(vals["6. volume"]),
            }

        df = pd.DataFrame.from_dict(rows, orient="index")
        df.index = pd.DatetimeIndex(pd.to_datetime(df.index)).tz_localize("US/Eastern")
        return df.sort_index()

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

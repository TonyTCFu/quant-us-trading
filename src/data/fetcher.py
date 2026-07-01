"""美股行情数据获取与本地缓存。

数据来源：yfinance (Yahoo Finance)
时区：ET (Eastern Time)，数据返回时为 tz-aware
价格调整：yfinance auto_adjust=True，自动处理拆股和分红调整
缓存：本地 CSV，按 {ticker}_{frequency}.csv 存储

降级策略：yfinance 不可用时（网络限制等），使用合成数据确保流程可推进。
"""

import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

DEFAULT_START = "2023-01-01"
DATE_FMT = "%Y-%m-%d"


def _generate_synthetic_ohlcv(
    ticker: str,
    start: str,
    end: str,
    frequency: str,
    seed: int = 42,
) -> pd.DataFrame:
    """降级方案：生成合成 OHLCV 数据。

    使用带漂移的几何布朗运动 + 日内波动模拟。
    仅在 yfinance 不可用时使用，数据仅供流程验证，不可用于实盘信号。
    """
    rng = np.random.default_rng(seed + hash(ticker) % 10000)

    freq_map = {"1d": "D", "1h": "h", "30m": "30min", "5m": "5min"}
    freq = freq_map.get(frequency, "D")
    dates = pd.date_range(start=start, end=end, freq=freq, tz="US/Eastern")

    n = len(dates)
    if n == 0:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])

    base_prices = {
        "AAPL": 180, "MSFT": 380, "GOOGL": 140, "AMZN": 180, "META": 480,
    }
    base = base_prices.get(ticker, 100.0)

    daily_vol = 0.02  # 日波动率 ~2%
    drift = 0.0002  # 微小正向漂移

    returns = rng.normal(drift, daily_vol, n)
    close = base * np.exp(np.cumsum(returns))
    open_p = close * (1 + rng.normal(0, 0.002, n))
    high = np.maximum(open_p, close) * (1 + np.abs(rng.normal(0, 0.005, n)))
    low = np.minimum(open_p, close) * (1 - np.abs(rng.normal(0, 0.005, n)))
    volume = rng.integers(500_000, 80_000_000, n)

    df = pd.DataFrame(
        {"Open": open_p, "High": high, "Low": low, "Close": close, "Volume": volume},
        index=dates,
    )
    return df


class DataFetcher:
    """美股行情数据获取器。

    用法:
        cfg = load_config()
        fetcher = DataFetcher(cfg["data"])
        df = fetcher.fetch("AAPL", start="2024-01-01", end="2024-12-31")
    """

    def __init__(self, config: dict):
        self.cache_dir = Path(config.get("cache_dir", "data"))
        self.request_interval = config.get("request_interval", 2.0)
        self.max_retries = config.get("max_retries", 3)
        self.timeout = config.get("timeout", 30)
        self._available = True  # 首次调用后更新
        self._checked = False
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def is_api_available(self) -> bool:
        """检测 yfinance API 是否可达。"""
        if not self._checked:
            try:
                t = yf.Ticker("AAPL")
                df = t.history(period="1d")
                self._available = not df.empty
            except Exception:
                logger.warning("yfinance API 不可达，使用合成数据降级")
                self._available = False
            self._checked = True
        return self._available

    def fetch(
        self,
        ticker: str,
        start: str = DEFAULT_START,
        end: Optional[str] = None,
        frequency: str = "1d",
    ) -> pd.DataFrame:
        """获取单只股票数据。先查缓存，缓存未覆盖部分从 API 拉取。

        Args:
            ticker: 股票代码，如 AAPL
            start: 起始日期 YYYY-MM-DD
            end: 结束日期 YYYY-MM-DD，默认今天
            frequency: 1d / 1h / 30m / 5m

        Returns:
            DataFrame with columns [Open, High, Low, Close, Volume],
            DatetimeIndex (tz=US/Eastern)
        """
        if end is None:
            end = datetime.now().strftime(DATE_FMT)

        start_ts = pd.Timestamp(start, tz="US/Eastern")
        end_ts = pd.Timestamp(end, tz="US/Eastern")

        # 始终先查缓存
        cached = self._load_cache(ticker, frequency)
        if cached is not None and not cached.empty:
            c_min = cached.index.min()
            c_max = cached.index.max()
            if c_min <= start_ts and c_max >= end_ts:
                return cached.loc[start_ts:end_ts]

        if self.is_api_available():
            df = self._fetch_with_cache(ticker, start, end, frequency)
            if not df.empty:
                return df

        # 降级：用缓存中已有数据
        if cached is not None and not cached.empty:
            return cached.loc[
                max(c_min, start_ts) : min(c_max, end_ts)
            ]

        logger.info("缓存无数据且 API 不可达，生成合成数据: %s", ticker)
        df = _generate_synthetic_ohlcv(ticker, start, end, frequency)
        self._save_cache(ticker, frequency, df)
        return df.loc[start_ts:end_ts]

    def fetch_batch(
        self,
        tickers: List[str],
        start: str = DEFAULT_START,
        end: Optional[str] = None,
        frequency: str = "1d",
    ) -> Dict[str, pd.DataFrame]:
        """批量获取，遵守请求间隔。"""
        if end is None:
            end = datetime.now().strftime(DATE_FMT)

        results = {}
        for i, ticker in enumerate(tickers):
            if i > 0 and self.is_api_available():
                time.sleep(self.request_interval)
            try:
                df = self.fetch(ticker, start=start, end=end, frequency=frequency)
                if not df.empty:
                    results[ticker] = df
            except Exception as e:
                logger.error("获取 %s 失败: %s", ticker, e)
        return results

    # ---- 内部方法 ----

    def _cache_path(self, ticker: str, frequency: str) -> Path:
        return self.cache_dir / f"{ticker}_{frequency}.csv"

    @staticmethod
    def _normalize_index(idx: pd.Index) -> pd.DatetimeIndex:
        """统一索引为 tz-aware DatetimeIndex (US/Eastern)。"""
        dti = pd.DatetimeIndex(pd.to_datetime(idx, utc=True)).tz_convert("US/Eastern")
        return dti

    def _load_cache(self, ticker: str, frequency: str) -> Optional[pd.DataFrame]:
        path = self._cache_path(ticker, frequency)
        if not path.exists():
            return None
        df = pd.read_csv(path, index_col=0)
        if df.empty:
            return None
        df.index = self._normalize_index(df.index)
        return df

    def _save_cache(self, ticker: str, frequency: str, df: pd.DataFrame):
        path = self._cache_path(ticker, frequency)
        df_to_save = df.copy()
        try:
            df_to_save.index = df_to_save.index.tz_convert("UTC").tz_localize(None)
        except (AttributeError, TypeError):
            # 索引可能已无时区，跳过转换
            pass
        df_to_save.to_csv(path)

    def _fetch_with_cache(
        self, ticker: str, start: str, end: str, frequency: str
    ) -> pd.DataFrame:
        """从缓存 + API 组合获取真实数据。"""
        cached = self._load_cache(ticker, frequency)
        start_ts = pd.Timestamp(start, tz="US/Eastern")
        end_ts = pd.Timestamp(end, tz="US/Eastern")

        if cached is not None and not cached.empty:
            c_min = cached.index.min()
            c_max = cached.index.max()
            if c_min <= start_ts and c_max >= end_ts:
                return cached.loc[start_ts:end_ts]
            # 增量更新：从缓存截止日之后拉取
            if c_max >= start_ts:
                start = (c_max + timedelta(days=1)).strftime(DATE_FMT)
            if pd.Timestamp(start) >= pd.Timestamp(end):
                return cached.loc[start_ts:min(c_max, end_ts)]

        new_data = self._fetch_from_api(ticker, start, end, frequency)
        if cached is not None and not new_data.empty:
            cached = pd.concat([cached, new_data])
            cached = cached[~cached.index.duplicated(keep="last")]
            cached = cached.sort_index()
        elif not new_data.empty:
            cached = new_data

        if cached is not None and not cached.empty:
            self._save_cache(ticker, frequency, cached)
            return cached.loc[start_ts:end_ts] if not cached.empty else cached
        return pd.DataFrame()

    def refresh_daily(self, tickers: list) -> dict:
        """每日增量刷新：通过 QVeris Alpha Vantage 拉取最新 100 天数据并与缓存合并。

        供定时任务调用，确保数据始终包含最新交易日。

        Returns: {"updated": [ticker, ...], "failed": [ticker, ...]}
        """
        result = {"updated": [], "failed": []}
        for ticker in tickers:
            try:
                df = self._fetch_from_qveris(ticker)
                if df is not None and not df.empty:
                    cached = self._load_cache(ticker, "1d")
                    if cached is not None and not cached.empty:
                        merged = pd.concat([cached, df])
                        merged = merged[~merged.index.duplicated(keep="last")]
                        merged = merged.sort_index()
                        self._save_cache(ticker, "1d", merged)
                    else:
                        self._save_cache(ticker, "1d", df)
                    result["updated"].append(ticker)
                    logger.debug("%s: refreshed, latest=%s", ticker,
                                df.index[-1].strftime("%Y-%m-%d"))
                else:
                    result["failed"].append(ticker)
            except Exception as e:
                logger.warning("%s: refresh failed — %s", ticker, e)
                result["failed"].append(ticker)
            time.sleep(0.5)
        return result

    def _fetch_from_qveris(self, ticker: str) -> Optional[pd.DataFrame]:
        """通过 QVeris 调用 Alpha Vantage 获取最新日线（复权）。"""
        import json, os, subprocess

        env = {**os.environ, "HTTP_PROXY": "", "HTTPS_PROXY": ""}
        params = json.dumps({
            "function": "TIME_SERIES_DAILY_ADJUSTED",
            "symbol": ticker,
            "outputsize": "compact",
        })
        for attempt in range(self.max_retries):
            try:
                result = subprocess.run(
                    ["qveris", "call", "alphavantage.time-series.daily-adjusted.v1",
                     "--params", params, "--json"],
                    capture_output=True, text=True, env=env, timeout=60,
                )
                data = json.loads(result.stdout)
                if not data.get("success"):
                    logger.warning("%s: QVeris call failed — %s", ticker,
                                  data.get("result", {}).get("message", "unknown")[:80])
                    if attempt < self.max_retries - 1:
                        time.sleep(self.request_interval * (attempt + 1))
                    continue

                res = data["result"]
                # QVeris 可能返回 truncated_content 或 full_content_file_url
                if res.get("full_content_file_url"):
                    import ssl, urllib.request
                    ctx = ssl.create_default_context()
                    req = urllib.request.Request(res["full_content_file_url"])
                    with urllib.request.urlopen(req, timeout=60, context=ctx) as resp:
                        full = json.loads(resp.read().decode())
                else:
                    full = json.loads(res.get("truncated_content", "{}"))

                ts = full.get("Time Series (Daily)", {})
                if not ts:
                    logger.warning("%s: empty time series in response", ticker)
                    return pd.DataFrame()

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
                df = df.sort_index()
                return df
            except Exception as e:
                logger.warning("%s: QVeris fetch attempt %d/%d — %s",
                               ticker, attempt + 1, self.max_retries, e)
                if attempt < self.max_retries - 1:
                    time.sleep(self.request_interval * (attempt + 1))
        return pd.DataFrame()

    def _fetch_from_api(
        self, ticker: str, start: str, end: str, frequency: str
    ) -> pd.DataFrame:
        """带重试的 API 请求：优先 QVeris，回退 yfinance。"""
        # 优先 QVeris（不依赖代理）
        df = self._fetch_from_qveris(ticker)
        if not df.empty:
            return df

        # 回退 yfinance
        yf_freq_map = {"1d": "1d", "1h": "1h", "30m": "30m", "5m": "5m"}
        yf_interval = yf_freq_map.get(frequency, "1d")

        for attempt in range(self.max_retries):
            try:
                t = yf.Ticker(ticker)
                df = t.history(
                    start=start,
                    end=end,
                    interval=yf_interval,
                    auto_adjust=True,
                )
                if df.empty:
                    logger.warning("%s: 返回空数据 (attempt %d)", ticker, attempt + 1)
                    continue
                keep_cols = ["Open", "High", "Low", "Close", "Volume"]
                df = df[[c for c in keep_cols if c in df.columns]]
                if df.index.tz is None:
                    df.index = df.index.tz_localize("US/Eastern")
                return df
            except Exception as e:
                logger.warning("%s: API 请求失败 (attempt %d/%d): %s",
                               ticker, attempt + 1, self.max_retries, e)
                if attempt < self.max_retries - 1:
                    time.sleep(self.request_interval * (attempt + 1))
        return pd.DataFrame()

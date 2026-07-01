"""批量拉取 S&P 500 成分股全量历史数据并跑回测。

用法: python3 batch_pull.py
"""

import json
import logging
import os
import ssl
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)


# 30 stocks across major sectors
UNIVERSE = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA", "NFLX", "ADBE", "CRM",
    "JPM", "BAC", "GS", "V", "MA",
    "JNJ", "PFE", "UNH", "ABBV",
    "WMT", "KO", "HD", "NKE", "MCD",
    "CAT", "XOM", "GE",
    "DIS", "T", "VZ",
]


def pull_history(ticker: str):
    """通过 QVeris Alpha Vantage 拉取复权全量历史。"""
    params = json.dumps({
        "function": "TIME_SERIES_DAILY_ADJUSTED",
        "symbol": ticker,
        "outputsize": "full",
    })
    env = {**os.environ, "HTTP_PROXY": "", "HTTPS_PROXY": ""}
    result = subprocess.run(
        ["qveris", "call", "alphavantage.time-series.daily-adjusted.v1",
         "--params", params, "--json"],
        capture_output=True, text=True, env=env, timeout=120,
    )
    data = json.loads(result.stdout)
    if not data.get("success"):
        logger.error("  %s: QVeris call failed", ticker)
        return None

    res = data["result"]
    url = res.get("full_content_file_url")
    if not url:
        logger.error("  %s: no full_content_file_url", ticker)
        return None

    ctx = ssl.create_default_context()
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=60, context=ctx) as resp:
        full_data = json.loads(resp.read().decode())

    return full_data


def convert_to_csv(ticker: str, full_data: dict, out_dir: Path):
    """转换复权数据为 CSV 缓存文件。"""
    ts = full_data["Time Series (Daily)"]
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

    out_dir.mkdir(exist_ok=True)
    df_save = df.copy()
    df_save.index = df_save.index.tz_convert("UTC").tz_localize(None)
    df_save.to_csv(out_dir / f"{ticker}_1d.csv")
    return len(df)


def main():
    data_dir = Path("data")
    data_dir.mkdir(exist_ok=True)

    # 跳过已有缓存的
    existing = {f.stem.split("_")[0] for f in data_dir.glob("*_1d.*")}
    to_pull = [t for t in UNIVERSE if t not in existing]

    cached_count = len([t for t in UNIVERSE if t in existing])
    logger.info("已有缓存: %d, 需拉取: %d", cached_count, len(to_pull))

    success = 0
    for i, ticker in enumerate(to_pull):
        logger.info("[%d/%d] %s", i + 1, len(to_pull), ticker)
        try:
            full_data = pull_history(ticker)
            if full_data is None:
                continue
            days = convert_to_csv(ticker, full_data, data_dir)
            logger.info("  -> %d days saved", days)
            success += 1
        except Exception as e:
            logger.error("  -> FAILED: %s", e)
        time.sleep(1.5)  # 避免 QVeris 限频

    logger.info("完成: %d/%d 成功", success, len(to_pull))


if __name__ == "__main__":
    main()

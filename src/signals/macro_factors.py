"""宏观多因子叠加层 — 在技术指标之上叠加宏观环境判断。

因子列表:
  1. 经济日历风险    — FOMC/CPI/NFP/GDP 发布日降低仓位 (FOMC 提前 5 天预警)
  2. VIX 波动率环境  — VIX 指数优先 / VIXY ETF 代理 (Alpaca IEX, 免费)
  3. 美元指数 DXY     — UUP ETF 代理 (Alpaca IEX, 免费)
  4. 行业轮动         — 11 个 Sector ETF 相对强弱 (Alpaca IEX, 免费)
  5. 资金流代理       — 成交量异常检测 (Alpaca IEX, 免费)
  6. 市场宽度         — % 股票在 MA20 以上 (Alpaca IEX, 免费)
  7. 新闻情绪         — Alpha Vantage News Sentiment (QVeris, 可选, 2 credits)

所有因子汇总为:
  - macro_risk_score: 0-100 (越高越危险)
  - position_multiplier: 0.0-1.0 (仓位缩放系数)
  - recommendation: NORMAL / CAUTION / DEFENSIVE

用法:
    from src.signals.macro_factors import MacroOverlay
    mo = MacroOverlay(fetcher)
    overlay = mo.evaluate()
    # overlay["position_multiplier"] 应用到模拟盘仓位
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ── 2026 年美国重要经济数据发布时间表 ──
# FOMC: 联邦公开市场委员会利率决议 (年 8 次, 周三 2PM ET)
# CPI: 消费者物价指数 (月, 月中 8:30AM ET)
# NFP: 非农就业 (月, 第一个周五 8:30AM ET)
# GDP: 国内生产总值 (季, 月末 8:30AM ET)
ECONOMIC_CALENDAR_2026 = {
    # FOMC 2026 (8 meetings)
    "2026-01-28": "FOMC 利率决议",
    "2026-03-18": "FOMC 利率决议",
    "2026-05-06": "FOMC 利率决议",
    "2026-06-17": "FOMC 利率决议",
    "2026-07-29": "FOMC 利率决议",
    "2026-09-23": "FOMC 利率决议",
    "2026-11-04": "FOMC 利率决议",
    "2026-12-16": "FOMC 利率决议",
    # CPI 2026 (月, approx 10-14 日)
    "2026-01-13": "CPI 通胀数据",
    "2026-02-12": "CPI 通胀数据",
    "2026-03-12": "CPI 通胀数据",
    "2026-04-13": "CPI 通胀数据",
    "2026-05-13": "CPI 通胀数据",
    "2026-06-11": "CPI 通胀数据",
    "2026-07-14": "CPI 通胀数据",
    "2026-08-12": "CPI 通胀数据",
    "2026-09-14": "CPI 通胀数据",
    "2026-10-14": "CPI 通胀数据",
    "2026-11-12": "CPI 通胀数据",
    "2026-12-11": "CPI 通胀数据",
    # NFP 2026 (月, 第一个周五)
    "2026-01-02": "非农就业 NFP",
    "2026-02-06": "非农就业 NFP",
    "2026-03-06": "非农就业 NFP",
    "2026-04-03": "非农就业 NFP",
    "2026-05-01": "非农就业 NFP",
    "2026-06-05": "非农就业 NFP",
    "2026-07-02": "非农就业 NFP",
    "2026-08-07": "非农就业 NFP",
    "2026-09-04": "非农就业 NFP",
    "2026-10-02": "非农就业 NFP",
    "2026-11-06": "非农就业 NFP",
    "2026-12-04": "非农就业 NFP",
}

# Sector ETF mapping (Alpaca IEX supported)
SECTOR_ETFS = {
    "XLK": "Technology",
    "XLF": "Financials",
    "XLV": "Healthcare",
    "XLY": "Consumer Discretionary",
    "XLP": "Consumer Staples",
    "XLI": "Industrials",
    "XLE": "Energy",
    "XLU": "Utilities",
    "XLB": "Materials",
    "XLRE": "Real Estate",
    "XLC": "Communication Services",
}

# VIX proxy via ETF
VIX_PROXY = "VIXY"   # VIXY 追踪 VIX 期货, 可以在 Alpaca 交易
DXY_PROXY = "UUP"    # UUP 追踪美元指数
SPY_PROXY = "SPY"    # 基准

# 大市值代表性股票（用于市场宽度）
BREADTH_TICKERS = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA", "NFLX",
    "JPM", "BAC", "GS", "V", "MA", "JNJ", "PFE", "UNH", "WMT", "HD",
    "CAT", "XOM", "GE", "DIS", "T", "VZ", "KO", "MCD", "NKE", "ADBE",
    "CRM", "ABBV", "INTC", "AMD", "AVGO", "LLY", "CSCO", "ORCL", "CVX",
]


@dataclass
class FactorResult:
    name: str
    score: float             # 0=利好, 100=极度危险
    signal: str              # BULLISH / NEUTRAL / BEARISH
    detail: str
    weight: float = 1.0


class MacroOverlay:
    """宏观因子叠加层。

    用法:
        from src.data.alpaca_fetcher import AlpacaFetcher  # or UnifiedFetcher
        fetcher = AlpacaFetcher()
        mo = MacroOverlay(fetcher)
        result = mo.evaluate()  # 返回完整的 factor breakdown + multiplier
    """

    def __init__(self, fetcher):
        self.fetcher = fetcher
        self._cache: Dict[str, pd.DataFrame] = {}

    def _get_etf(self, ticker: str, days: int = 60) -> Optional[pd.DataFrame]:
        if ticker in self._cache:
            return self._cache[ticker]
        try:
            end = datetime.now()
            start = end - timedelta(days=days)
            df = self.fetcher.fetch(ticker, start=start.strftime("%Y-%m-%d"),
                                     end=end.strftime("%Y-%m-%d"))
            if df is not None and not df.empty:
                self._cache[ticker] = df
                return df
        except:
            pass
        return None

    def evaluate(self, date: Optional[str] = None) -> dict:
        """运行所有宏观因子，返回综合评估。FOMC 前 2 个交易日强制清仓。"""
        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")

        factors = []
        factors.append(self._economic_calendar(date))
        factors.append(self._vix_regime())
        factors.append(self._dxy_correlation())
        factors.append(self._sector_rotation(date))
        factors.append(self._capital_flows(date))
        factors.append(self._market_breadth())

        # 加权汇总
        total_weight = sum(f.weight for f in factors)
        weighted_score = sum(f.score * f.weight for f in factors) / max(total_weight, 0.01)

        # 仓位缩放系数
        if weighted_score >= 70:
            multiplier = 0.25
            rec = "DEFENSIVE"
        elif weighted_score >= 50:
            multiplier = 0.50
            rec = "CAUTION"
        elif weighted_score >= 30:
            multiplier = 0.75
            rec = "CAUTION"
        else:
            multiplier = 1.0
            rec = "NORMAL"

        dt = datetime.strptime(date, "%Y-%m-%d")

        # FOMC 当日: 完全不交易
        if date in ECONOMIC_CALENDAR_2026:
            event = ECONOMIC_CALENDAR_2026[date]
            if "FOMC" in event:
                multiplier = 0.0
                rec = "FOMC_BLACKOUT"

        # FOMC 前 4 个自然日内: 强制清仓 (覆盖周五→周三FOMC的周末间隔)
        for offset in range(1, 5):
            check_d = (dt + timedelta(days=offset)).strftime("%Y-%m-%d")
            if check_d in ECONOMIC_CALENDAR_2026 and "FOMC" in ECONOMIC_CALENDAR_2026[check_d]:
                multiplier = 0.0
                rec = "FOMC_EVACUATION"
                break

        # CPI/NFP 当天: 仓位上限 25%
        if date in ECONOMIC_CALENDAR_2026:
            event = ECONOMIC_CALENDAR_2026[date]
            if "CPI" in event or "NFP" in event:
                multiplier = min(multiplier, 0.25)
                rec = "DATA_DAY"

        return {
            "date": date,
            "macro_risk_score": round(weighted_score, 1),
            "position_multiplier": multiplier,
            "recommendation": rec,
            "factors": [
                {"name": f.name, "score": f.score, "signal": f.signal, "detail": f.detail}
                for f in factors
            ],
            "economic_event": ECONOMIC_CALENDAR_2026.get(date, None),
        }

    # ═══════════════════════════════════════════
    # Factor 1: Economic Calendar
    # ═══════════════════════════════════════════
    def _economic_calendar(self, today: str) -> FactorResult:
        """当天及未来几天是否有重要经济数据公布。FOMC 提前 5 天预警、3 天高警。"""
        if today in ECONOMIC_CALENDAR_2026:
            event = ECONOMIC_CALENDAR_2026[today]
            if "FOMC" in event:
                return FactorResult("经济日历", 95, "BEARISH",
                                    f"今日 {event} — 极高波动，建议空仓", weight=2.5)
            elif "CPI" in event:
                return FactorResult("经济日历", 70, "BEARISH",
                                    f"今日 {event} — 高波动，降低仓位", weight=2.0)
            elif "NFP" in event:
                return FactorResult("经济日历", 65, "BEARISH",
                                    f"今日 {event} — 高波动，降低仓位", weight=1.8)
            elif "GDP" in event:
                return FactorResult("经济日历", 50, "BEARISH",
                                    f"今日 {event} — 中度波动", weight=1.5)

        # 向前扫描未来 7 天，检测即将到来的重大事件
        dt = datetime.strptime(today, "%Y-%m-%d")
        nearest_fomc_days = None
        nearest_event_days = None
        for offset in range(0, 8):  # 今天 + 未来 7 天
            check_d = (dt + timedelta(days=offset)).strftime("%Y-%m-%d")
            if check_d in ECONOMIC_CALENDAR_2026:
                ev = ECONOMIC_CALENDAR_2026[check_d]
                if "FOMC" in ev:
                    nearest_fomc_days = offset
                elif nearest_event_days is None:
                    nearest_event_days = offset

        # FOMC 提前预警：3 天内 → 高警报, 5 天内 → 提示
        if nearest_fomc_days is not None and nearest_fomc_days <= 3:
            if nearest_fomc_days == 0:
                return FactorResult("经济日历", 95, "BEARISH",
                    "今日 FOMC — 空仓", weight=2.5)
            return FactorResult("经济日历", 75, "BEARISH",
                f"FOMC {nearest_fomc_days}天后 — 建议提前清仓", weight=2.5)
        elif nearest_fomc_days is not None and nearest_fomc_days <= 5:
            return FactorResult("经济日历", 40, "BEARISH",
                f"FOMC {nearest_fomc_days}天后 — 注意仓位", weight=2.0)

        # 其他经济事件临近 1 天
        if nearest_event_days is not None and nearest_event_days <= 1:
            return FactorResult("经济日历", 30, "NEUTRAL",
                "临近重要数据发布 — 持仓谨慎", weight=1.5)

        return FactorResult("经济日历", 5, "NEUTRAL", "无重要经济数据发布", weight=1.0)

    # ═══════════════════════════════════════════
    # Factor 2: VIX Regime (^VIX index or VIXY proxy)
    # ═══════════════════════════════════════════
    def _vix_regime(self) -> FactorResult:
        """波动率环境评估。优先用 VIX 真实指数, 不可用时用 VIXY ETF 代理。"""
        # 先尝试获取真实 VIX 指数
        vix_value = self._try_get_vix()
        if vix_value is not None:
            return self._vix_result(vix_value, source="^VIX")

        # 回退: VIXY ETF (不转换, 用 VIXY 自身价格区间, $15-40)
        df = self._get_etf(VIX_PROXY, days=30)
        if df is not None and not df.empty:
            close = df["Close"]
            latest = close.iloc[-1]
            if latest > 35:
                return FactorResult("VIX(VIXY)", 85, "BEARISH",
                    f"VIXY=${latest:.2f} — 极端恐慌区间", weight=2.5)
            elif latest > 28:
                return FactorResult("VIX(VIXY)", 60, "BEARISH",
                    f"VIXY=${latest:.2f} — 高波动区间", weight=2.0)
            elif latest > 22:
                return FactorResult("VIX(VIXY)", 30, "NEUTRAL",
                    f"VIXY=${latest:.2f} — 正常偏高", weight=1.5)
            elif latest > 16:
                return FactorResult("VIX(VIXY)", 10, "BULLISH",
                    f"VIXY=${latest:.2f} — 正常", weight=1.5)
            else:
                return FactorResult("VIX(VIXY)", 5, "BULLISH",
                    f"VIXY=${latest:.2f} — 安逸", weight=1.5)

        # 回退: SPY 隐含波动估计
        df = self._get_etf(SPY_PROXY, days=30)
        if df is not None and not df.empty:
            spy_vol = df["Close"].pct_change().std() * np.sqrt(252) * 100
            if spy_vol > 30:
                return FactorResult("VIX(SPY代理)", 70, "BEARISH",
                    f"SPY 波动率 {spy_vol:.0f}% — 高波动", weight=2.0)
            elif spy_vol > 20:
                return FactorResult("VIX(SPY代理)", 40, "NEUTRAL",
                    f"SPY 波动率 {spy_vol:.0f}% — 中等", weight=1.5)
            else:
                return FactorResult("VIX(SPY代理)", 10, "BULLISH",
                    f"SPY 波动率 {spy_vol:.0f}% — 低波动", weight=1.5)

        return FactorResult("VIX", 40, "NEUTRAL", "VIX/SPY 数据不可用 — 默认中性", weight=1.5)

    def _try_get_vix(self) -> Optional[float]:
        """尝试从 Alpaca 获取真实的 ^VIX 指数收盘价。"""
        try:
            df = self._get_etf("^VIX", days=5)
            if df is not None and not df.empty:
                return float(df["Close"].iloc[-1])
        except Exception:
            pass
        return None

    def _vix_result(self, vix: float, source: str) -> FactorResult:
        """根据真实 VIX 数值返回评估结果。"""
        if vix > 30:
            return FactorResult("VIX", 85, "BEARISH",
                f"VIX={vix:.1f} ({source}) — 恐慌", weight=2.5)
        elif vix > 25:
            return FactorResult("VIX", 60, "BEARISH",
                f"VIX={vix:.1f} ({source}) — 紧张", weight=2.0)
        elif vix > 20:
            return FactorResult("VIX", 30, "NEUTRAL",
                f"VIX={vix:.1f} ({source}) — 正常偏高", weight=1.5)
        elif vix > 15:
            return FactorResult("VIX", 10, "BULLISH",
                f"VIX={vix:.1f} ({source}) — 正常", weight=1.5)
        else:
            return FactorResult("VIX", 5, "BULLISH",
                f"VIX={vix:.1f} ({source}) — 安逸", weight=1.5)

    # ═══════════════════════════════════════════
    # Factor 3: DXY / USD Strength (via UUP)
    # ═══════════════════════════════════════════
    def _dxy_correlation(self) -> FactorResult:
        """美元走强 = 美股逆风（尤其跨国企业/大宗商品）。"""
        df = self._get_etf(DXY_PROXY, days=60)
        if df is None or df.empty:
            return FactorResult("DXY", 20, "NEUTRAL", "UUP 数据不可用", weight=1.0)

        close = df["Close"]
        latest = close.iloc[-1]

        # 20 日趋势
        sma20 = close.rolling(20).mean().iloc[-1]
        dxy_trend = (latest / sma20 - 1) * 100

        # 10 日动量
        change_10d = (close.iloc[-1] / close.iloc[-10] - 1) * 100 if len(close) >= 10 else 0

        if dxy_trend > 2 and change_10d > 0:
            return FactorResult("DXY", 50, "BEARISH",
                                f"美元走强 +{dxy_trend:.1f}% (20d) — 对美股逆风", weight=1.5)
        elif dxy_trend < -2 and change_10d < 0:
            return FactorResult("DXY", 5, "BULLISH",
                                f"美元走弱 {dxy_trend:.1f}% (20d) — 利好美股出口", weight=1.5)
        elif dxy_trend > 1:
            return FactorResult("DXY", 25, "NEUTRAL",
                                f"美元微强 +{dxy_trend:.1f}% | UUP=${latest:.2f}", weight=1.0)
        else:
            return FactorResult("DXY", 15, "NEUTRAL",
                                f"美元稳定 | UUP=${latest:.2f}", weight=1.0)

    # ═══════════════════════════════════════════
    # Factor 4: Sector Rotation
    # ═══════════════════════════════════════════
    def _sector_rotation(self, date: str) -> FactorResult:
        """行业轮动信号：关联模拟盘持仓的行业是否在领涨。"""
        sector_returns = {}
        for etf, name in SECTOR_ETFS.items():
            df = self._get_etf(etf, days=30)
            if df is not None and not df.empty:
                ret = (df["Close"].iloc[-1] / df["Close"].iloc[-20] - 1) if len(df) >= 20 else 0
                sector_returns[name] = ret

        if len(sector_returns) < 3:
            return FactorResult("行业轮动", 20, "NEUTRAL",
                                f"仅 {len(sector_returns)} 个行业数据可用", weight=0.8)

        # 模拟盘持仓的行业（GS/JPM=金融, GE/CAT=工业, JNJ=医疗）
        our_sectors = ["Financials", "Industrials", "Healthcare"]

        top3 = sorted(sector_returns.items(), key=lambda x: -x[1])[:3]
        bottom3 = sorted(sector_returns.items(), key=lambda x: x[1])[:3]

        our_performance = [sector_returns.get(s, 0) for s in our_sectors]
        our_avg = np.mean(our_performance)

        market_avg = np.mean(list(sector_returns.values()))
        gap = our_avg - market_avg

        detail = f"持仓行业均 {our_avg*100:+.1f}% | 领涨: {', '.join(f'{n} {r*100:+.1f}%' for n,r in top3[:3])} | 领跌: {', '.join(f'{n} {r*100:+.1f}%' for n,r in bottom3[:3])}"

        if gap > 2:
            return FactorResult("行业轮动", 5, "BULLISH",
                                f"持仓行业跑赢基准 {gap*100:+.1f}% | {detail}", weight=1.5)
        elif gap < -2:
            return FactorResult("行业轮动", 55, "BEARISH",
                                f"持仓行业跑输基准 {gap*100:+.1f}% | {detail}", weight=1.5)
        else:
            return FactorResult("行业轮动", 20, "NEUTRAL", detail, weight=1.0)

    # ═══════════════════════════════════════════
    # Factor 5: Capital Flow Proxy (Volume Anomaly)
    # ═══════════════════════════════════════════
    def _capital_flows(self, date: str) -> FactorResult:
        """通过 SPY 成交量异常检测大资金进出。

        SPY 日成交量 > 50 日均量 2x → 恐慌/贪婪信号
        """
        df = self._get_etf(SPY_PROXY, days=90)
        if df is None or df.empty or "Volume" not in df.columns:
            return FactorResult("资金流", 20, "NEUTRAL", "SPY 数据不可用", weight=1.0)

        vol = df["Volume"]
        close = df["Close"]
        latest_vol = vol.iloc[-1]
        avg_vol_50 = vol.rolling(50).mean().iloc[-1]
        vol_ratio = latest_vol / avg_vol_50 if avg_vol_50 > 0 else 1

        # 资金流入/流出 = 成交量 * 价格方向
        daily_ret = close.pct_change().iloc[-1]

        if vol_ratio > 2.0 and daily_ret < -0.02:
            return FactorResult("资金流", 80, "BEARISH",
                                f"恐慌抛售: 量 {vol_ratio:.1f}x | 日跌 {daily_ret*100:.1f}%", weight=2.0)
        elif vol_ratio > 2.0 and daily_ret > 0.02:
            return FactorResult("资金流", 35, "NEUTRAL",
                                f"放量上涨: 量 {vol_ratio:.1f}x | 日涨 {daily_ret*100:.1f}% — 短期偏多但须警惕", weight=1.5)
        elif vol_ratio > 1.5:
            return FactorResult("资金流", 25, "NEUTRAL",
                                f"成交放量 {vol_ratio:.1f}x — 关注方向", weight=1.0)
        elif vol_ratio < 0.5:
            return FactorResult("资金流", 10, "NEUTRAL",
                                f"成交缩量 {vol_ratio:.1f}x — 市场观望", weight=0.8)
        else:
            return FactorResult("资金流", 10, "NEUTRAL",
                                f"成交量正常 ({vol_ratio:.1f}x)", weight=0.8)

    # ═══════════════════════════════════════════
    # Factor 6: Market Breadth
    # ═══════════════════════════════════════════
    def _market_breadth(self) -> FactorResult:
        """市场宽度: % 的股票在 20 日均线以上。"""
        above_ma = 0
        total = 0

        for ticker in BREADTH_TICKERS:
            try:
                df = self.fetcher.fetch(ticker, start=(datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d"),
                                        end=datetime.now().strftime("%Y-%m-%d"))
                if df is not None and not df.empty and len(df) >= 20:
                    close = df["Close"]
                    ma20 = close.rolling(20).mean().iloc[-1]
                    if close.iloc[-1] > ma20:
                        above_ma += 1
                    total += 1
            except:
                pass

        if total < 10:
            return FactorResult("市场宽度", 25, "NEUTRAL",
                                f"仅 {total} 只股票数据可用", weight=1.0)

        breadth = above_ma / total * 100

        if breadth > 70:
            return FactorResult("市场宽度", 5, "BULLISH",
                                f"{breadth:.0f}% 股票 > MA20 ({above_ma}/{total}) — 广泛上涨", weight=1.5)
        elif breadth > 50:
            return FactorResult("市场宽度", 15, "NEUTRAL",
                                f"{breadth:.0f}% 股票 > MA20 ({above_ma}/{total}) — 中性偏强", weight=1.0)
        elif breadth > 30:
            return FactorResult("市场宽度", 40, "BEARISH",
                                f"仅 {breadth:.0f}% 股票 > MA20 ({above_ma}/{total}) — 市场分化", weight=1.5)
        else:
            return FactorResult("市场宽度", 65, "BEARISH",
                                f"仅 {breadth:.0f}% 股票 > MA20 ({above_ma}/{total}) — 广泛下跌", weight=2.0)

    # ═══════════════════════════════════════════
    # Factor 7: News Sentiment (QVeris, optional)
    # ═══════════════════════════════════════════
    def _news_sentiment(self, ticker: str = "SPY") -> Optional[FactorResult]:
        """通过 QVeris Alpha Vantage News Sentiment 获取新闻情绪（可选, 2 credits）。"""
        try:
            import json, os, subprocess

            # 仅限周一运行（省 credits）
            if datetime.now().weekday() != 0:
                return None

            params = json.dumps({
                "function": "NEWS_SENTIMENT",
                "tickers": ticker,
                "limit": 10,
            })
            env = {**os.environ, "HTTP_PROXY": "", "HTTPS_PROXY": ""}
            result = subprocess.run(
                ["qveris", "call", "alphavantage.news_sentiment.query.v1.7aca3c4a",
                 "--params", params, "--json"],
                capture_output=True, text=True, env=env, timeout=30,
            )
            data = json.loads(result.stdout)
            if not data.get("success"):
                return None

            content = data["result"].get("truncated_content", "{}")
            news = json.loads(content) if isinstance(content, str) else content

            items = news.get("feed", [])
            if not items:
                return None

            scores = [item.get("overall_sentiment_score", 0) for item in items[:10]]
            avg_score = np.mean(scores) if scores else 0

            if avg_score > 0.2:
                return FactorResult("新闻情绪", 10, "BULLISH",
                                    f"情绪偏正 ({avg_score:.2f}) — {len(items)} 条", weight=0.5)
            elif avg_score < -0.2:
                return FactorResult("新闻情绪", 45, "BEARISH",
                                    f"情绪偏负 ({avg_score:.2f}) — {len(items)} 条", weight=0.5)
            else:
                return FactorResult("新闻情绪", 20, "NEUTRAL",
                                    f"情绪中性 ({avg_score:.2f})", weight=0.5)
        except Exception as e:
            logger.debug("新闻情绪获取失败: %s", e)
            return None

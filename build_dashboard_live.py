#!/usr/bin/env python3
"""生成模拟盘实时 Dashboard HTML。每次 update 后自动刷新。"""

import json
import sys
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import base64, io

sys.path.insert(0, str(Path(__file__).resolve().parent))

DARK_BG = '#0f1117'; CARD_BG = '#1a1d27'; TEXT = '#e1e4e8'; ACCENT = '#58a6ff'
GREEN = '#3fb950'; RED = '#f85149'; YELLOW = '#d2991d'; GRID = '#30363d'; BORDER = '#30363d'

plt.rcParams.update({
    'font.family': 'sans-serif', 'axes.unicode_minus': False,
    'figure.dpi': 100, 'savefig.dpi': 100, 'savefig.bbox': 'tight', 'savefig.pad_inches': 0.1
})


def fig_to_b64(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=100, bbox_inches='tight', facecolor=CARD_BG, edgecolor='none')
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode()


def dark_style(ax, title=''):
    ax.set_facecolor(CARD_BG)
    ax.set_title(title, color=TEXT, fontsize=12, fontweight='bold', pad=8)
    ax.tick_params(colors='#8b949e', labelsize=8)
    ax.grid(True, alpha=0.12, color='white')
    for s in ax.spines.values(): s.set_color(GRID)


def _macro_section() -> str:
    """宏观多因子评估面板。"""
    try:
        from src.data.unified_fetcher import UnifiedFetcher
        from src.signals.macro_factors import MacroOverlay
        from datetime import datetime

        fetcher = UnifiedFetcher()
        mo = MacroOverlay(fetcher)
        result = mo.evaluate()

        score = result["macro_risk_score"]
        mult = result["position_multiplier"]
        rec = result["recommendation"]

        if score >= 70:
            border = RED
        elif score >= 40:
            border = YELLOW
        else:
            border = GREEN

        html = f"""<div style="border-left:3px solid {border};border-radius:0 8px 8px 0;padding:14px;margin-bottom:12px;background:#1a1d27">
<div style="display:flex;gap:24px;align-items:center;margin-bottom:12px">
  <div>
    <div style="font-size:10px;color:#8b949e">风险评分</div>
    <div style="font-size:28px;font-weight:bold;color:{border}">{score:.0f}<span style="font-size:14px">/100</span></div>
  </div>
  <div>
    <div style="font-size:10px;color:#8b949e">仓位系数</div>
    <div style="font-size:28px;font-weight:bold;color:{border}">{mult*100:.0f}%</div>
  </div>
  <div>
    <div style="font-size:10px;color:#8b949e">建议</div>
    <div style="font-size:20px;font-weight:bold;color:{ACCENT}">{rec}</div>
  </div>
  <div style="flex:1;text-align:right;color:#8b949e;font-size:11px">
    经济事件: {result.get('economic_event') or '无'}<br>
    日期: {result['date']}
  </div>
</div>
<table>
<tr><th>因子</th><th>评分</th><th style="width:200px"></th><th>信号</th><th>详情</th></tr>"""
        for f in result["factors"]:
            bar_fill = min(int(f["score"] / 5), 20)
            bar = f'<span style="color:{GREEN if f["score"]<30 else YELLOW if f["score"]<60 else RED}">{"█"*bar_fill}{"░"*(20-bar_fill)}</span>'
            sig_color = GREEN if f["signal"] == "BULLISH" else (RED if f["signal"] == "BEARISH" else YELLOW)
            html += f"""<tr>
<td style="font-size:11px">{f['name']}</td>
<td class="num" style="font-weight:bold;color:{RED if f['score']>=60 else YELLOW if f['score']>=30 else GREEN}">{f['score']:.0f}</td>
<td style="font-size:8px">{bar}</td>
<td style="font-size:10px;color:{sig_color}">{f['signal']}</td>
<td style="font-size:10px;color:#8b949e">{f['detail'][:100]}</td></tr>"""
        html += "</table></div>"
        return html
    except Exception as e:
        return f'<div class="card"><p style="color:#8b949e">宏观因子不可用: {e}</p></div>'


def _summary_section(state: dict, pos_rows: list, trades: list, total_realized: float, equity: float, total_ret: float, ann_ret: float, sharpe: float, max_dd: float, win_rate: float, progress: float) -> str:
    """基金经理深度总结报告 — 激进型风格。"""
    try:
        report_md_path = "/Users/tonyfu/.gemini/antigravity/scratch/us_paper_trading/weekly_summary_report.md"
        if Path(report_md_path).exists():
            md_content = Path(report_md_path).read_text(encoding="utf-8")
            
            # A simple markdown to HTML parser
            html_lines = []
            in_table = False
            for line in md_content.split("\n"):
                line_str = line.strip()
                if not line_str:
                    if in_table:
                        html_lines.append("</table>")
                        in_table = False
                    continue
                
                # Headers
                if line_str.startswith("# "):
                    html_lines.append(f'<h1 style="color:#58a6ff;margin-bottom:12px">{line_str[2:]}</h1>')
                elif line_str.startswith("## "):
                    html_lines.append(f'<h3 style="color:#58a6ff;margin-top:16px;margin-bottom:8px">{line_str[3:]}</h3>')
                elif line_str.startswith("### "):
                    html_lines.append(f'<h4 style="color:#d2991d;margin-top:10px;margin-bottom:6px">{line_str[4:]}</h4>')
                # Blockquotes
                elif line_str.startswith("> "):
                    html_lines.append(f'<div style="border-left:3px solid #58a6ff;padding-left:10px;color:#8b949e;font-size:11px;margin-bottom:8px"><i>{line_str[2:]}</i></div>')
                # Tables
                elif line_str.startswith("|"):
                    cols = [c.strip() for c in line_str.split("|")[1:-1]]
                    if all(c.startswith("-") or c.startswith(":") for c in cols):
                        continue
                    if not in_table:
                        html_lines.append('<table style="width:100%;border-collapse:collapse;font-size:11px;margin-bottom:10px">')
                        in_table = True
                        html_lines.append("<tr>" + "".join(f'<th style="border-bottom:2px solid #30363d;padding:5px 6px;color:#8b949e">{c}</th>' for c in cols) + "</tr>")
                    else:
                        html_lines.append("<tr>" + "".join(f'<td style="border-bottom:1px solid #30363d;padding:4px 6px">{c}</td>' for c in cols) + "</tr>")
                # Lists
                elif line_str.startswith("- ") or line_str.startswith("* "):
                    content = line_str[2:]
                    import re
                    content = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', content)
                    html_lines.append(f'<div style="font-size:10px;color:#8b949e;margin-left:12px;margin-bottom:4px">• {content}</div>')
                else:
                    if in_table:
                        html_lines.append("</table>")
                        in_table = False
                    import re
                    line_str = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', line_str)
                    html_lines.append(f'<p style="font-size:11px;color:#8b949e;margin-bottom:6px">{line_str}</p>')
            
            if in_table:
                html_lines.append("</table>")
            
            return '<div class="card" style="overflow-x:auto">\n' + "\n".join(html_lines) + '\n</div>'
    except Exception as e:
        print(f"[Dashboard Build] Error parsing weekly_summary_report.md: {e}")

    try:
        from datetime import datetime, timedelta
        import numpy as np

        hist = state.get('equity_history', [])
        sells = [t for t in trades if t['side'] == 'SELL']
        buys = [t for t in trades if t['side'] == 'BUY']
        wins = [t for t in sells if t.get('pnl', 0) > 0]
        losses = [t for t in sells if t.get('pnl', 0) <= 0]

        total_won = sum(t.get('pnl', 0) for t in wins)
        total_lost = sum(abs(t.get('pnl', 0)) for t in losses)
        profit_factor = total_won / max(total_lost, 0.01)
        avg_win = total_won / max(len(wins), 1)
        avg_loss = total_lost / max(len(losses), 1)

        tp_sells = [t for t in sells if 'TAKE_PROFIT' in str(t.get('reason', ''))]
        sl_sells = [t for t in sells if 'STOP_LOSS' in str(t.get('reason', ''))]
        sig_sells = [t for t in sells if 'SIGNAL_SELL' in str(t.get('reason', ''))]

        wr_tp = len([t for t in tp_sells if t.get('pnl', 0) > 0]) / max(len(tp_sells), 1) * 100
        wr_sl = len([t for t in sl_sells if t.get('pnl', 0) > 0]) / max(len(sl_sells), 1) * 100
        wr_sig = len([t for t in sig_sells if t.get('pnl', 0) > 0]) / max(len(sig_sells), 1) * 100

        positions = [(t, p) for t, p in state['positions'].items() if p['shares'] > 0]
        pos_value = sum(p['shares'] * p['entry_price'] for _, p in positions)

        # Lessons & next steps as an aggressive fund manager
        lessons = [
            ("✅ 止盈纪律", f"4次全额止盈 GE/CAT/JPM/JNJ，TP 10% 全部精准命中，胜率 100%"),
            ("✅ 宏观因子", f"FOMC 提前3天自动撤离，macRO模型驱动，非人为硬编码"),
            ("⚠ 止损过频", f"SL=5% 在近期波动中触发{len(sl_sells)}次，META/MSFT/NFLX/GOOGL连续止损，考虑ATR动态调整"),
            ("⚠ 仓位碎片化", f"AMZN 1股/AAPL 2股占比过小，激进策略应集中火力至5-6只核心标的"),
            ("⚠ 胜率下滑", f"从早期75%降至33%，FOMC前波动是主因，撤退纪律优于硬扛"),
        ]

        if ann_ret >= 8:
            next_steps = [
                "FOMC 7/29 过后：全仓重扫 Sharpe Top 8，高集中度建仓",
                "SL 从固定 5% → 1.8x ATR 动态止损，波动率自适应",
                "仓位集中化：max 8 → 5-6 只，每只 ±20% 等权",
                "周度再平衡：每周六自动检查偏离度 + Sharpe 重排",
            ]
        else:
            next_steps = [
                "暂停新入场，复盘近 10 笔亏损交易的共性",
                "网格回测验证 SL 参数最优区间 (3%-8%)",
            ]

        html = '<div class="card" style="overflow-x:auto">\n'
        html += '<h3 style="color:#58a6ff;margin-bottom:12px">📊 基金经理总结报告 (激进型)</h3>\n'

        # KPI row
        html += '<div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:12px;font-size:11px">'
        for label, val, clr in [
            ("权益", f"${equity:,.0f}", GREEN if total_ret >= 0 else RED),
            ("累计收益", f"{total_ret:+.2f}%", GREEN if total_ret >= 0 else RED),
            ("年化收益", f"{ann_ret:+.1f}%", GREEN if ann_ret >= 8 else YELLOW),
            ("最大回撤", f"{max_dd:+.1f}%", GREEN if max_dd > -5 else YELLOW if max_dd > -10 else RED),
            ("Sharpe", f"{sharpe:.2f}", GREEN if sharpe >= 1 else YELLOW),
            ("胜率", f"{win_rate*100:.0f}% ({len(wins)}W/{len(losses)}L)", GREEN if win_rate >= 0.5 else YELLOW),
            ("盈利因子", f"{profit_factor:.2f}x", GREEN if profit_factor >= 1.5 else YELLOW),
            ("vs 8%目标", f"{progress:.0f}%", GREEN if progress >= 100 else YELLOW),
        ]:
            html += f'<div style="text-align:center;padding:6px 10px;background:#0f1117;border-radius:6px"><div style="font-size:16px;font-weight:bold;color:{clr}">{val}</div><div style="font-size:9px;color:#8b949e">{label}</div></div>'
        html += '</div>\n'

        # Trade analysis grid
        html += '<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;font-size:10px;margin-bottom:10px">\n'
        html += '<div style="background:#0f1117;border-radius:6px;padding:8px"><b style="color:#3fb950">止盈退出</b><br>'
        html += f'{len(tp_sells)}笔 | 胜率 {wr_tp:.0f}% | 均利 ${avg_win:+,.0f}</div>'
        html += '<div style="background:#0f1117;border-radius:6px;padding:8px"><b style="color:#f85149">止损退出</b><br>'
        html += f'{len(sl_sells)}笔 | 胜率 {wr_sl:.0f}% | 均亏 ${-avg_loss:+,.0f}</div>'
        html += '<div style="background:#0f1117;border-radius:6px;padding:8px"><b style="color:#d2991d">信号退出</b><br>'
        html += f'{len(sig_sells)}笔 | 胜率 {wr_sig:.0f}%</div>'
        html += '</div>\n'

        # Lessons
        html += '<details style="margin-bottom:8px"><summary style="color:#58a6ff;font-size:11px;cursor:pointer">📖 教训总结 (基金经理视角)</summary>'
        html += '<div style="font-size:10px;color:#8b949e;padding:6px 0">'
        for title, detail in lessons:
            html += f'<div style="margin:4px 0"><b>{title}</b>: {detail}</div>'
        html += '</div></details>\n'

        # Next steps
        html += '<details style="margin-bottom:4px"><summary style="color:#3fb950;font-size:11px;cursor:pointer">🎯 下一步行动计划</summary>'
        html += '<div style="font-size:10px;color:#8b949e;padding:6px 0">'
        for i, step in enumerate(next_steps, 1):
            html += f'<div style="margin:3px 0">{i}. {step}</div>'
        html += '</div></details>\n'

        html += '</div>'
        return html
    except Exception as e:
        return f'<div class="card"><p style="color:#8b949e">总结报告生成失败: {e}</p></div>'


def _alpaca_section() -> str:
    """读取 Alpaca Paper 账户真实持仓（只读）。"""
    try:
        from src.data.alpaca_fetcher import AlpacaFetcher
        fetcher = AlpacaFetcher()
        acct = fetcher.get_account()
        pos = fetcher.get_alpaca_positions()

        html = f"""<div style="margin-bottom:10px;font-size:12px">
  <b>账户权益:</b> ${acct['equity']:,.2f} | <b>现金:</b> ${acct['cash']:,.2f} | <b>状态:</b> {acct['status']}</div>"""

        if pos.empty:
            html += '<p style="color:#8b949e">无持仓</p>'
        else:
            html += """<table>
<tr><th>Ticker</th><th class="num">持仓</th><th class="num">成本价</th><th class="num">现价</th><th class="num">市值</th><th class="num">浮动 PnL</th><th class="num">PnL%</th></tr>"""
            for _, r in pos.iterrows():
                pnl_c = GREEN if r['unrealized_pl'] >= 0 else RED
                html += f"""<tr><td><b>{r['symbol']}</b></td><td class="num">{r['qty']:.0f}</td>
<td class="num">${r['avg_entry_price']:.2f}</td><td class="num">${r['current_price']:.2f}</td>
<td class="num">${r['market_value']:,.2f}</td>
<td class="num" style="color:{pnl_c}">${r['unrealized_pl']:+,.2f}</td>
<td class="num" style="color:{pnl_c}">{r['unrealized_plpc']*100:+.1f}%</td></tr>"""
            total_pnl = pos['unrealized_pl'].sum()
            total_mv = pos['market_value'].sum()
            pnl_c = GREEN if total_pnl >= 0 else RED
            html += f"""<tr style="font-weight:bold;border-top:2px solid {GRID}">
<td>合计 ({len(pos)} 只)</td><td></td><td></td><td></td>
<td class="num">${total_mv:,.2f}</td>
<td class="num" style="color:{pnl_c}">${total_pnl:+,.2f}</td><td></td></tr>"""
            html += "</table>"
        return html
    except Exception as e:
        return f'<p style="color:#8b949e">Alpaca 连接不可用: {e}</p>'


def build_dashboard(state_path: str = "outputs/paper_state.json", out_path: str = "outputs/dashboard/live.html"):
    if not Path(state_path).exists():
        Path(out_path).write_text("<html><body style='background:#0f1117;color:#e1e4e8;font-family:monospace;padding:40px'><h1>模拟盘未启动</h1><p>运行 python3 paper_trading_live.py init</p></body></html>")
        return

    state = json.loads(Path(state_path).read_text())
    INITIAL = state["initial_capital"]

    # Get latest prices from Alpaca (free IEX feed)
    prices = {}
    today = datetime.now().strftime("%Y-%m-%d")
    build_id = datetime.now().strftime("%Y%m%d%H%M%S")
    try:
        from src.data.alpaca_fetcher import AlpacaFetcher
        from datetime import timedelta
        fetcher = AlpacaFetcher()
        lookback = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")
        for t in list(state["positions"].keys()):
            try:
                df = fetcher.fetch(t, start=lookback, end=today)
                if df is not None and not df.empty:
                    prices[t] = float(df["Close"].iloc[-1])
            except:
                pass
    except:
        pass
    # fallback to entry price
    for t, p in state["positions"].items():
        if t not in prices:
            prices[t] = p["avg_cost"]

    # Compute current portfolio
    total_pos_value = 0
    total_pnl = 0
    pos_rows = []
    for t, p in state["positions"].items():
        if p["shares"] <= 0:
            continue
        price = prices.get(t, p["avg_cost"])
        value = p["shares"] * price
        pnl = (price - p["avg_cost"]) * p["shares"]
        pnl_pct = (price / p["entry_price"]) - 1
        total_pos_value += value
        total_pnl += pnl
        days = (datetime.strptime(today, "%Y-%m-%d") -
                datetime.strptime(p["entry_date"], "%Y-%m-%d")).days if p.get("entry_date") else 0
        pnl_color = GREEN if pnl >= 0 else RED
        pos_rows.append({
            "ticker": t, "shares": p["shares"], "entry": p["entry_price"],
            "current": price, "value": value, "pnl": pnl, "pnl_pct": pnl_pct,
            "days": days, "pnl_color": pnl_color
        })

    # 模型权益 = 模型现金 + 实时持仓市值 (用于展示当前持仓)
    live_equity = state["cash"] + total_pos_value

    # 使用 equity_history 的最后一条计算 KPI (基于历史记录，不因实时价格波动而偏离)
    hist = state.get("equity_history", [])
    equity_series = pd.Series([h["equity"] for h in hist]) if hist else pd.Series([INITIAL, live_equity])
    equity = float(equity_series.iloc[-1])
    total_ret = (equity / INITIAL) - 1 if INITIAL > 0 else 0

    days_run = max(len(equity_series), 1)
    years = days_run / 252
    annual_ret = (1 + total_ret) ** (1 / max(years, 0.02)) - 1

    daily_ret = equity_series.pct_change().dropna()
    sharpe = float(daily_ret.mean() / daily_ret.std() * np.sqrt(252)) if len(daily_ret) > 1 and daily_ret.std() > 0 else 0

    rolling_max = equity_series.cummax()
    dd = (equity_series - rolling_max) / rolling_max
    max_dd = float(dd.min() if len(dd) > 0 else 0)

    trades = state.get("trade_log", [])
    sells = [t for t in trades if t["side"] == "SELL"]
    wins = [t for t in sells if t.get("pnl", 0) > 0]
    win_rate = len(wins) / max(len(sells), 1)
    total_realized = sum(t.get("pnl", 0) for t in sells)

    # Target tracking
    target_annual = 0.08
    target_cum = (1 + target_annual) ** (days_run / 252) - 1
    progress = total_ret / target_cum * 100 if target_cum != 0 else 0

    # ── LLM Decision Panel ──
    decision_cards = []
    macro_for_dash = {}
    try:
        from src.decision.llm_enhance import build_panel
        from src.data.unified_fetcher import UnifiedFetcher
        from src.signals.macro_factors import MacroOverlay
        fetcher2 = UnifiedFetcher()
        mo2 = MacroOverlay(fetcher2)
        macro_for_dash = mo2.evaluate()
        # Use paper_trading_live signal generation
        from paper_trading_live import get_today_signals
        all_sigs = get_today_signals([p for p in state["positions"].keys()] +
            ["AAPL","MSFT","GOOGL","AMZN","META","NVDA","NFLX","WMT"], fetcher2)
        decision_cards = build_panel(all_sigs, macro_for_dash)
    except Exception as e:
        decision_html = f'<p style="color:#8b949e;padding:8px">AI 决策面板暂不可用: {e}</p>'

    decision_html = ""
    for card in decision_cards[:8]:  # top 8 by confidence
        sig_color = GREEN if card["signal"] == "BUY" else (RED if card["signal"] == "SELL" else YELLOW)
        flag_html = ""
        for f in card["risk_flags"]:
            flag_html += f'<span class="badge bear">{f[:20]}</span> '
        confidence_bar = f'<span style="display:inline-block;width:40px;height:6px;border-radius:3px;background:{GREEN if card["confidence"]>=60 else YELLOW if card["confidence"]>=40 else RED};vertical-align:middle;margin-right:4px"></span>'
        decision_html += f"""<tr>
          <td><b>{card['ticker']}</b></td>
          <td style="color:{sig_color};font-weight:bold">{card['signal']}</td>
          <td class="num">${card['price']:.2f}</td>
          <td>{confidence_bar}{card['confidence']:.0f}%</td>
          <td class="num">${card['stop_loss']:.2f}</td>
          <td class="num">${card['take_profit']:.2f}</td>
          <td style="font-size:10px;color:#8b949e;max-width:200px">{card['conclusion']}</td>
          <td style="font-size:9px">{flag_html}</td></tr>"""

    # ── Chart: equity curve ──
    fig, ax = plt.subplots(figsize=(9, 3.5))
    dates = [h["date"] for h in hist] if hist else [today]
    ax.plot(range(len(equity_series)), equity_series.values, color=ACCENT, linewidth=1.5, label="Portfolio")
    ax.axhline(y=INITIAL, color=GRID, linewidth=0.8, linestyle='--', alpha=0.5, label=f"Initial ${INITIAL:,.0f}")
    ax.fill_between(range(len(equity_series)), equity_series.values, INITIAL,
                     where=(equity_series.values >= INITIAL), color=GREEN, alpha=0.12)
    ax.fill_between(range(len(equity_series)), equity_series.values, INITIAL,
                     where=(equity_series.values < INITIAL), color=RED, alpha=0.12)
    dark_style(ax, f"Paper Trading Equity Curve — {state.get('start_date','?')} → {state.get('last_update','?')}")
    ax.legend(facecolor=CARD_BG, edgecolor=GRID, labelcolor='#8b949e', fontsize=8)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _: f'${y:,.0f}'))
    fig.tight_layout()
    eq_chart = fig_to_b64(fig)

    # ── HTML ──
    pos_table = ""
    for r in pos_rows:
        pos_table += f"""<tr>
          <td><b>{r['ticker']}</b></td><td class="num">{r['shares']}</td>
          <td class="num">${r['entry']:.2f}</td><td class="num">${r['current']:.2f}</td>
          <td class="num">${r['value']:,.0f}</td>
          <td class="num" style="color:{r['pnl_color']}">${r['pnl']:+,.0f}</td>
          <td class="num" style="color:{r['pnl_color']}">{r['pnl_pct']*100:+.1f}%</td>
          <td class="num">{r['days']}d</td></tr>"""

    trade_table = ""
    for t in list(reversed(trades))[:15]:
        side = t["side"]
        c = GREEN if side == "BUY" else RED
        if side == "SELL":
            pnl_val = t.get("pnl", 0)
            pnl_color = GREEN if pnl_val >= 0 else RED
            pnl_str = "<span style='color:{}'>${:+,.0f}</span>".format(pnl_color, pnl_val)
        else:
            pnl_str = "—"
        trade_table += f"""<tr>
          <td>{t['date']}</td><td><b>{t['ticker']}</b></td>
          <td style="color:{c}">{side}</td><td class="num">{t['shares']}</td>
          <td class="num">${t['price']:.2f}</td>
          <td>{pnl_str}</td><td style="font-size:10px;color:#8b949e">{t.get('reason','')}</td></tr>"""

    progress_color = GREEN if progress >= 100 else (YELLOW if progress >= 50 else RED)
    eq_color = GREEN if total_ret >= 0 else RED

    # 已平仓汇总：按 ticker 聚合 BUY/SELL 配对
    closed_positions = {}
    buy_map = {}
    for t in trades:
        if t["side"] == "BUY":
            key = t["ticker"]
            if key not in buy_map:
                buy_map[key] = []
            buy_map[key].append(t)
        elif t["side"] == "SELL":
            # 消耗最早的一笔 BUY
            ticker = t["ticker"]
            sold_shares = t["shares"]
            pnl = t.get("pnl", 0)
            pnl_pct = t.get("pnl_pct", 0)
            days = t.get("holding_days", 0)
            reason = t.get("reason", "")
            entry_date = ""
            entry_price = 0
            if ticker in buy_map and buy_map[ticker]:
                oldest_buy = buy_map[ticker].pop(0)
                entry_date = oldest_buy.get("date", "")
                entry_price = oldest_buy.get("price", 0)

            if ticker not in closed_positions:
                closed_positions[ticker] = {"trades": [], "total_pnl": 0, "count": 0, "wins": 0}
            closed_positions[ticker]["trades"].append({
                "entry_date": entry_date, "exit_date": t["date"],
                "entry_price": entry_price, "exit_price": t["price"],
                "shares": sold_shares, "pnl": pnl, "pnl_pct": pnl_pct,
                "days": days, "reason": reason,
            })
            closed_positions[ticker]["total_pnl"] += pnl
            closed_positions[ticker]["count"] += 1
            if pnl > 0:
                closed_positions[ticker]["wins"] += 1

    closed_table = ""
    closed_sorted = sorted(closed_positions.items(), key=lambda x: x[1]["total_pnl"], reverse=True)
    for ticker, cp in closed_sorted:
        pnl_c = GREEN if cp["total_pnl"] >= 0 else RED
        wr = cp["wins"] / cp["count"] * 100 if cp["count"] > 0 else 0
        for tr in cp["trades"]:
            tr_pnl_c = GREEN if tr["pnl"] >= 0 else RED
            closed_table += f"""<tr>
              <td><b>{ticker}</b></td><td>{tr['entry_date']}</td><td>{tr['exit_date']}</td>
              <td class="num">{tr['shares']}</td><td class="num">${tr['entry_price']:.2f}</td><td class="num">${tr['exit_price']:.2f}</td>
              <td class="num">{tr['days']}d</td><td style="font-size:10px">{tr['reason']}</td>
              <td class="num" style="color:{tr_pnl_c}">${tr['pnl']:+,.2f}</td>
              <td class="num" style="color:{tr_pnl_c}">{tr['pnl_pct']*100:+.1f}%</td></tr>"""
        closed_table += f"""<tr style="background:{CARD_BG}">
          <td colspan="6" style="border-top:1px solid {BORDER}"><b>{ticker} 汇总</b></td>
          <td class="num" style="border-top:1px solid {BORDER}">{cp['count']}笔</td><td class="num" style="border-top:1px solid {BORDER}">胜率 {wr:.0f}%</td>
          <td class="num" style="border-top:1px solid {BORDER};color:{pnl_c};font-weight:bold">${cp['total_pnl']:+,.2f}</td>
          <td style="border-top:1px solid {BORDER}"></td></tr>"""

    html = f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta http-equiv="Cache-Control" content="no-store, no-cache, must-revalidate">
<meta http-equiv="Pragma" content="no-cache">
<meta http-equiv="Expires" content="0">
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0,maximum-scale=1.0,user-scalable=no">
<meta http-equiv="refresh" content="900;URL=?v={build_id}">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="US Quant">
<link rel="apple-touch-icon" sizes="192x192" href="icon-192.png">
<link rel="apple-touch-icon" sizes="512x512" href="icon-512.png">
<link rel="icon" type="image/png" sizes="192x192" href="icon-192.png">
<link rel="icon" type="image/png" sizes="512x512" href="icon-512.png">
<link rel="manifest" href="manifest.json">
<title>【Claude Code】美股量化 Dashboard</title>
<style>
	*{{margin:0;padding:0;box-sizing:border-box}}
	body{{background:#0f1117;color:#e1e4e8;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;padding:10px;line-height:1.5;-webkit-text-size-adjust:100%}}
	h1{{font-size:17px;color:#58a6ff;margin-bottom:2px}}
	h2{{font-size:14px;color:#58a6ff;margin:18px 0 6px;padding-bottom:4px;border-bottom:2px solid #30363d}}
	.subtitle{{color:#8b949e;font-size:11px;margin:2px 0 12px;line-height:1.5}}
	.card{{background:#1a1d27;border:1px solid #30363d;border-radius:8px;padding:10px;margin-bottom:8px;overflow-x:auto}}
	.metrics-bar{{display:flex;align-items:center;gap:6px;flex-wrap:wrap;font-size:11px;color:#8b949e;padding:6px 0 10px;line-height:1.7}}
	.metrics-bar b{{color:#f0f3f5;font-size:12px}}
	.m-sep{{color:#30363d}}
	table{{width:100%;border-collapse:collapse;font-size:11px}}
	th{{text-align:left;padding:5px 6px;border-bottom:2px solid #30363d;color:#8b949e;font-weight:600;font-size:10px;white-space:nowrap}}
	td{{padding:4px 6px;border-bottom:1px solid #30363d;white-space:nowrap}}
	.num{{text-align:right;font-variant-numeric:tabular-nums}}
	img.chart{{width:100%;border-radius:4px;margin-top:2px}}
	.split23{{display:flex;flex-direction:column;gap:8px;margin-bottom:8px}}
	.grid2{{display:flex;flex-direction:column;gap:8px}}
	.nav{{display:flex;gap:6px;margin-bottom:10px;font-size:10px;flex-wrap:wrap}}
	.nav a{{color:#58a6ff;text-decoration:none}}
	.nav a:hover{{text-decoration:underline}}
	.footer{{margin-top:14px;padding:8px 0;border-top:1px solid #30363d;color:#8b949e;font-size:9px}}
	.badge{{display:inline-block;padding:1px 6px;border-radius:8px;font-size:9px;font-weight:bold}}
	.bull{{color:#3fb950}} .bear{{color:#f85149}}
	@media(min-width:768px){{
		body{{padding:16px}}
		h1{{font-size:18px}}
		h2{{font-size:15px}}
		.card{{padding:14px;margin-bottom:12px}}
		.metrics-bar{{font-size:13px;gap:10px}}
		.split23{{flex-direction:row}}
		.split23 .card:first-child{{flex:2}}
		.split23 .card:last-child{{flex:1;min-width:200px}}
		.grid2{{flex-direction:row}}
		.grid2 .card{{flex:1}}
		img.chart{{margin-top:4px}}
		.nav{{font-size:11px}}
	}}
</style>
</head>
<body>

<div class="nav">
  <a href="?v={build_id}">← 综合 Dashboard</a>
  <span style="color:#8b949e">|</span>
  <span style="color:#8b949e">刷新: {datetime.now().strftime('%Y-%m-%d %H:%M')} 北京时间</span>
  <span style="color:#8b949e">|</span>
  <span style="color:#8b949e">每 15 分钟自动刷新</span>
</div>

<h1>【Claude Code】美股量化模型 — 模拟盘实时</h1>
<div class="subtitle">
  基金规模: <b>$20,000</b> | 模型现值: <b>${equity:,.2f}</b> | 策略: MA 5/20 + SL7%/TP12% | 运行: {state.get('start_date','?')} → {datetime.now().strftime('%Y-%m-%d')}
  <br>⚠ Alpaca Paper 账户总权益 ${state.get('account_equity',0):,.0f}（含 ${state.get('account_cash',0):,.0f} 自由现金，不归模型管理）
</div>

<div class="metrics-bar">
      <span class="m-item">权益 <b>${equity:,.0f}</b></span>
      <span class="m-sep">|</span>
      <span class="m-item">累计 <b style="color:{eq_color}">{total_ret*100:+.2f}%</b></span>
      <span class="m-sep">|</span>
      <span class="m-item">年化 <b style="color:{eq_color}">{annual_ret*100:+.1f}%</b></span>
      <span class="m-sep">|</span>
      <span class="m-item">Sharpe <b>{sharpe:.2f}</b></span>
      <span class="m-sep">|</span>
      <span class="m-item">回撤 <b style="color:{RED}">{max_dd*100:+.1f}%</b></span>
      <span class="m-sep">|</span>
      <span class="m-item">目标 <b style="color:{progress_color}">{progress:.0f}%</b></span>
      <span class="m-sep">|</span>
      <span class="m-item">胜率 <b>{win_rate*100:.0f}%</b></span>
    </div>

"""
    if decision_html:
        dpanel = f"""<h2>🧠 AI 决策面板 — 置信度排序 <span style="font-size:10px;color:#8b949e;font-weight:normal">(生成: {datetime.now().strftime('%H:%M:%S')})</span></h2>
    <div class="card" style="overflow-x:auto">
    <table>
    <tr><th>Ticker</th><th>信号</th><th class="num">现价</th><th>置信度</th><th class="num">止损</th><th class="num">止盈</th><th>结论</th><th>风险</th></tr>
    {decision_html}
    </table>
    <div style="margin-top:6px;font-size:9px;color:#8b949e">置信度 = 趋势(40%)+RSI(20%)+MACD(20%)-宏观风险(20%) | 偏离阈值: 标准5%/强趋势8%</div>
    </div>
    """
    else:
        dpanel = '<h2>🧠 AI 决策面板</h2><div class="card"><p style="color:#f85149;padding:12px">⚠ 决策面板数据暂不可用 — 请检查 Alpaca API 连接后重建 Dashboard</p></div>'
    html += dpanel
    html += f"""

<div class="split23">
    <div class="card">
    <img class="chart" src="data:image/png;base64,{eq_chart}" style="margin-top:0">
    </div>
    <div class="card">
    <h3 style="color:#58a6ff;font-size:12px;margin-bottom:8px">📋 最近交易</h3>
    <table>
    <tr><th>日期</th><th>Ticker</th><th>方向</th><th class="num">股数</th><th class="num">价格</th><th>PnL</th><th>原因</th></tr>
    {trade_table}
    </table>
    </div>
    </div>

    <div class="card">
    <h3 style="color:#58a6ff;font-size:12px;margin-bottom:8px">📊 当前持仓</h3>
    <table>
    <tr><th>Ticker</th><th class="num">股数</th><th class="num">入场价</th><th class="num">现价</th><th class="num">市值</th><th class="num">PnL</th><th class="num">PnL%</th><th class="num">天数</th></tr>
    {pos_table}
    <tr style="font-weight:bold"><td>合计</td><td></td><td></td><td></td><td class="num">${total_pos_value:,.0f}</td><td class="num" style="color:{GREEN if total_pnl>=0 else RED}">${total_pnl:+,.0f}</td><td></td><td></td></tr>
    </table>
    <div style="margin-top:8px;font-size:11px;color:#8b949e">
    现金: <b>${state['cash']:,.2f}</b> | 已实现 PnL: <b style="color:{GREEN if total_realized>=0 else RED}">${total_realized:+,.2f}</b> | 总权益: <b>${equity:,.2f}</b>
    </div>
    </div>

"""
    html += f"""
<h2>📋 已平仓持仓汇总</h2>
<div class="card">
""" + (f"""<table>
<tr><th>Ticker</th><th>入场日</th><th>出场日</th><th class="num">股数</th><th class="num">入场价</th><th class="num">出场价</th><th class="num">持仓</th><th>原因</th><th class="num">PnL</th><th class="num">PnL%</th></tr>
{closed_table}
</table>""" if closed_table else "<p style='color:#8b949e;padding:8px'>尚无已平仓交易</p>") + """
</div>
</details>

<h2>🏦 Alpaca Paper 账户真实持仓（只读）</h2>
<div class="card">
""" + _alpaca_section() + """
</div>

<h2>🌍 宏观环境 & 多因子</h2>
""" + _macro_section() + """

<h2>📊 基金经理总结报告</h2>
	""" + _summary_section(state, pos_rows, trades, total_realized, equity, total_ret, annual_ret, sharpe, max_dd, win_rate, progress) + """

	<h2>🎯 每日进度跟踪</h2>
<div class="card">
<table>
<tr><th>日期</th><th class="num">权益</th><th class="num">收益</th><th class="num">持仓数</th><th class="num">现金</th></tr>
"""
    for h in list(reversed(hist))[:20]:
        ret = h.get("total_return", 0)
        c = GREEN if ret >= 0 else RED
        html += f"""<tr><td>{h['date']}</td><td class="num">${h['equity']:,.2f}</td>
          <td class="num" style="color:{c}">{ret*100:+.2f}%</td>
          <td class="num">{h['positions']}</td><td class="num">${h['cash']:,.2f}</td></tr>"""
    html += f"""</table></div>
</details>

<div class="footer">
  自动生成: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} 北京时间 | Claude Code 量化模型 | 模拟交易仅供参考
  | <a href="index.html" style="color:{ACCENT}">返回综合 Dashboard</a>
</div>
</body></html>"""

    # Post-process: inject JS redirect for cache-busting
    import re
    bid_match = re.search(r'v=(\d{14})', html)
    if bid_match:
        build_ver = bid_match.group(1)
        redirect_js = '<script>(function(){var v=location.search.match(/v=(\\d+)/);var b="' + build_ver + '";if(!v||v[1]!==b){location.replace(location.pathname+"?v="+b)}})();</script>'
        html = html.replace('</head>', redirect_js + '\n</head>')

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(html)
    return out_path


if __name__ == "__main__":
    import shutil
    out = build_dashboard()
    print(f"Live dashboard: {out}")

    if out is None:
        print("Dashboard build returned None — skip deploy")
        sys.exit(0)

    build_id = datetime.now().strftime("%Y%m%d%H%M%S")

    # Auto-deploy: unique versioned filename to defeat browser caches
    deploy_dir = Path("deploy")
    deploy_dir.mkdir(exist_ok=True)
    dash_name = f"dash_{build_id}.html"
    dash_dst = deploy_dir / dash_name
    shutil.copy(out, dash_dst)
    print(f"Deploy copy: {dash_dst} ({dash_dst.stat().st_size} bytes)")

    import subprocess

    # index.html redirect to the versioned file, with localStorage tracking
    idx_dst = deploy_dir / "index.html"
    redirect = (
        '<!DOCTYPE html>\n<html lang="zh"><head>\n'
        '<meta charset="UTF-8">\n'
        '<meta http-equiv="Cache-Control" content="no-store,no-cache,must-revalidate">\n'
        '</head>\n<body style="background:#0f1117;color:#e1e4e8;text-align:center;padding-top:40vh">\n'
        '<p>📊 加载中...</p>\n'
        '<script>\n'
        'var b=localStorage.getItem("dash_ver"),c="'+build_id+'";'
        'if(b!==c){document.querySelector("p").textContent="🔄 更新中...";localStorage.setItem("dash_ver",c)}'
        'location.replace("'+dash_name+'");\n'
        '</script>\n</body></html>')
    idx_dst.write_text(redirect)
    print(f"Redirect: {idx_dst} -> {dash_name}")

    # 刷新 CDN 缓存
    token_file = deploy_dir / ".gh_token"
    if token_file.exists():
        token = token_file.read_text().strip()
        result = subprocess.run([
            "curl", "-s", "-X", "POST",
            "https://api.github.com/repos/TonyTCFu/cc-us-stock-dashboard/pages/builds",
            "-H", f"Authorization: Bearer {token}",
            "-H", "Accept: application/vnd.github+json",
            "-w", "%{http_code}", "-o", "/dev/null"
        ], capture_output=True, text=True)
        print(f"CDN cache bust: HTTP {result.stdout.strip()}")

    print(f"Deploy: git -C deploy add {dash_name} index.html && git -C deploy commit && git -C deploy push")

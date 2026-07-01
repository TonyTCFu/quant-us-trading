---
name: quant-trading
description: 美股短线量化交易策略研究 — 数据、信号、回测、风控、模拟盘全流程规则。
---

# 美股量化交易 Skill

每次运行前读取，这是本项目长期固化的规则和知识。

## 项目当前状态

- **阶段**: 模拟盘运行中 (Day 1, 2026-06-12 启动)
- **资金**: $20,000
- **目标**: 年化 8%+
- **主策略**: MA 5/20 + SL5%/TP10% + 宏观多因子
- **股票池**: GS, GE, NVDA, JNJ, GOOGL, CAT, META, JPM, NFLX, WMT, AMZN, MSFT, AAPL (13 只)
- **最大持仓**: 8 只, 单票 ≤ 25%

## 数据层规则

- 主数据源: Alpaca Paper IEX (免费, `data.alpaca.markets`)
- 备数据源: QVeris → Alpha Vantage (2 credits/次)
- 缓存格式: CSV (`data/*_1d.csv`)
- 时区基准: US/Eastern (ET)
- 定时拉取: 北京时间 周二~周六 5:15 AM (美股收盘后)
- 数据校验: last_date ≥ 上一交易日, 无 NaN 行
- API 凭证: 仅存 `.env`, 永不写入代码或版本控制

## 策略层规则

### MA 5/20 主策略 (当前生产)
- 金叉买入: MA5 上穿 MA20, 且价格 > MA20
- 死叉卖出: MA5 下穿 MA20
- 止损: -5% (从入场价)
- 止盈: +10% (从入场价)
- 信号延迟: next_open (避免未来函数)

### 宏观因子叠加
- VIX > 30 → 仓位系数 25%
- FOMC 日 → 仓位系数 0% (暂停交易)
- CPI/NFP 日 → 仓位系数 ≤ 25%
- 经济日历事件 → 风险评分 0-100

### 其他可用策略 (回测验证)
- MA 20/50 + SL5%/TP10% (慢牛)
- Combined (MA+MACD+RSI 加权投票)
- RSI Reversal (均值回归)
- BB Breakout (高波动专用)

## 风控层规则

- 止损: -5% per position
- 止盈: +10% per position
- 回撤熔断: 总权益回撤 > 5% → 暂停新开仓
- 单票上限: 25% × 宏观仓位系数
- 最大持仓: 8 只
- 流动性过滤: 最小日均成交量 500,000

## 回测标准

- 基准区间: 2023-01-01 ~ 当前
- 必报指标: 总收益, 年化收益, 最大回撤, 波动率, Sharpe, 胜率, 盈亏比
- 禁止: 未来函数, 数据泄漏
- 验证方法: 网格搜索 + 滚动窗口样本外验证

## 安全边界 (永远生效)

- **默认禁止实盘下单** — Alpaca 仅读取行情
- 模拟盘独立于真实持仓, 互不干扰
- 所有结论注明"仅供参考, 不构成投资建议"
- 策略信号必须可解释, 参数必须可配置
- `.env` 绝不提交版本控制

## 关键命令

```bash
# 模拟盘
python3 paper_trading_live.py update    # 每日更新 (定时任务执行)
python3 paper_trading_live.py report    # 查看持仓
python3 paper_trading_live.py backtest  # 滚动回测

# 扫描与回测
python3 market_scan.py --fast 5 --slow 20 --start 2023-01-01 --top 30
python3 run_backtest.py --tickers AAPL,MSFT --strategy ma --risk
python3 daily_signals.py --top 13

# 报告
python3 build_dashboard_live.py
```

## 输出产物

| 文件 | 内容 | 刷新 |
|------|------|------|
| `outputs/paper_state.json` | 当前持仓 | 每日 |
| `outputs/paper_trades.csv` | 交易日志 | 每日追加 |
| `outputs/paper_equity.csv` | 权益曲线 | 每日追加 |
| `outputs/dashboard/live.html` | 实时 Dashboard | 每日 |
| `outputs/daily_signals_*.md` | 信号报告 | 每日 |
| `data/*_1d.csv` | 日线缓存 | 每日增量 |

## 新会话启动检查清单

1. 读取 `CLAUDE.md` — 行为准则
2. 读取 `.codex/PROJECT_CONTEXT.md` — 完整项目上下文
3. 读取本 Skill 文件 — 规则与约束
4. 确认 `.env` 存在
5. 确认 `data/` 下有 CSV 缓存
6. 读取 `outputs/paper_state.json` — 当前状态
7. 读取 `.claude/loop/progress.md` — Loop 进展

# AGENTS.md — 美股量化模型项目规范

> 最后更新: 2026-08-04 | Alpaca Paper 真实执行 | $20K 激进策略

---

## 项目规则

1. **Alpaca Paper 真实执行** — `src/execution/alpaca_trader.py` 负责在 Alpaca Paper Trading 账户真实下单
2. **基金规模严格 $20,000** — 任何时候模型管理的总仓位不得超过 $20K（成本价），不得使用账户额外现金
3. **账户现金隔离** — Alpaca 账户总现金 $82K+，但模型只碰 $20K，其余不归模型管理
4. **API 凭证不入仓库** — `.env` 必须 gitignored，仅通过 secrets 注入 Actions
5. **策略必须可解释** — 每个信号必须能追溯到具体规则和参数
6. **非交易日不执行交易** — `is_trading_day()` + `_is_market_open()` (Alpaca 官方时钟) 双重守卫
7. **交易费用必须计入** — `cost_per_share = price*(1+SLIPPAGE)+COMMISSION`
8. **所有结果仅供参考** — 不构成投资建议

## 当前策略参数 (激进型)

| 参数 | 值 |
|------|-----|
| 股票池 | 20 只 (NVDA/CSCO/GOOGL/META/BAC/NFLX/WMT/AMZN/MSFT/AAPL/TSLA/AMD/JPM/GS/GE/JNJ/CAT/VZ/T/KO) |
| 最大持仓 | 6 只 |
| 单票上限 | 28% × 宏观系数 |
| 止损 | -7% |
| 止盈 | +12% |
| 信号 | MA 5/20 金叉死叉 |
| 偏离阈值 | 5% 标准 / 8% 强趋势 |

## 命令速查

```bash
# 模拟盘 (paper_state.json 维护)
python3 paper_trading_live.py update    # 每日更新：扫描信号→调仓→Alpaca 真实下单
python3 paper_trading_live.py report    # 查看当前持仓和绩效
python3 paper_trading_live.py backtest  # 运行同期滚动回测

# 信号
python3 daily_signals.py --top 20 --output outputs/daily_signals_$(date +%Y%m%d).md

# Dashboard
python3 build_dashboard_live.py         # 生成实时 Dashboard（含折叠区块 + 基金经理报告 + CDN 刷新）

# 手动部署到公网 (正常情况 build 脚本自动处理)
cp outputs/dashboard/live.html deploy/dash_$(date +%Y%m%d%H%M%S).html
```

## 架构约定

- **信号层**: MA/MACD/RSI/BB/ATR 指标 (`src/signals/`)
- **风控层**: SL7%/TP12% + 偏离阈值 + 仓位管理 (`src/risk/`)
- **宏观层**: VIX/DXY/FOMC/行业轮动/资金流/市场宽度 (`src/signals/macro_factors.py`)
- **决策增强**: 置信度 + 偏离阈值 + AI 决策卡 (`src/decision/llm_enhance.py`)
- **执行层**: Alpaca Paper 真实下单 (`src/execution/alpaca_trader.py`)
- **输出层**: Dashboard + 总结报告 + 公网部署 (`build_dashboard_live.py`)

## 部署说明

### 公网 Dashboard
- 域名: `http://cc-us-stock-dashboard.futienchun.com`
- GitHub Pages 仓库: `TonyTCFu/cc-us-stock-dashboard`
- DNS: Cloudflare CNAME → `tonytcfu.github.io` (DNS only)
- 部署方式: `index.html` JS 重定向 → 唯一文件名 `dash_[build_id].html` (绕过浏览器缓存)
- CDN 刷新: 每次 push 后自动 `curl POST .../pages/builds`

### 自动化 (三层)
1. **GitHub Actions**: `.github/workflows/daily-update.yml`, UTC 21:30 周二~周六
2. **本会话 Cron**: 每天 5:37 AM 调仓 + 21-23 点盘中刷新
3. **本地 crontab**: `30 5 * * 2-6` (备用)

### 防冲突机制
- GitHub Actions 和 cron 脚本都使用 `pull --rebase && push` 重试 3 次
- Dashboard 使用唯一文件名，不会产生文件级冲突

## 核心代码索引

| 文件 | 用途 |
|------|------|
| `paper_trading_live.py` | 模拟盘主控 + Alpaca 下单桥接 |
| `build_dashboard_live.py` | Dashboard 生成（含折叠区块 + 基金经理报告后处理） |
| `src/execution/alpaca_trader.py` | Alpaca Paper 真实下单引擎 |
| `src/signals/macro_factors.py` | 7 因子宏观评估 |
| `src/decision/llm_enhance.py` | LLM 决策增强层 |
| `daily_signals.py` | 每日信号报告生成器 |
| `deploy/daily_update.sh` | 本地 cron 脚本 |
| `.github/workflows/daily-update.yml` | GitHub Actions 工作流 |

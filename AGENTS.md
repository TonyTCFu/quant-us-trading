# AGENTS.md — 美股量化模型项目规范

> 最后更新: 2026-07-10

---

## 项目规则

1. **实盘永不操作** — 默认禁止实盘下单，Alpaca 仅读取行情
2. **API 凭证不入仓库** — `.env` 必须 gitignored，仅通过 secrets 注入 Actions
3. **策略必须可解释** — 每个信号必须能追溯到具体规则和参数
4. **非交易日不执行交易** — `is_trading_day()` 守卫一切买卖操作
5. **所有结果仅供参考** — 不构成投资建议
6. **交易费用必须计入** — `cost_per_share = price*(1+SLIPPAGE)+COMMISSION`

## 命令速查

```bash
# 模拟盘
python3 paper_trading_live.py update    # 每日更新：扫描信号→调仓
python3 paper_trading_live.py report    # 查看当前持仓和绩效
python3 paper_trading_live.py backtest  # 运行同期滚动回测

# 信号
python3 daily_signals.py --top 13 --output outputs/daily_signals_$(date +%Y%m%d).md
python3 daily_signals.py --top 13 --workers 4   # 并行扫描

# Dashboard
python3 build_dashboard_live.py         # 生成实时 Dashboard

# 部署到公网
cp outputs/dashboard/live.html deploy/dash.html
git -C deploy add dash.html
git -C deploy commit -m "Update $(date +%Y-%m-%d)" || true
git -C deploy push origin main

# 定时任务日志
cat /tmp/quant_daily_$(date +%Y%m%d).log
```

## 架构约定

- **策略层**: 信号生成 (`src/signals/`) + 宏观叠加 (`macro_factors.py`)
- **风控层**: 止损止盈 + 仓位管理 (`src/risk/manager.py`)
- **数据层**: UnifiedFetcher → Alpaca IEX (主) → QVeris/Alpha Vantage (备)
- **决策增强**: `src/decision/llm_enhance.py` — 偏离阈值 + 置信度 + AI 决策卡
- **执行层**: `paper_trading_live.py` — 模拟盘主控
- **输出层**: `build_dashboard_live.py` → GitHub Pages 部署

## 编码标准

- Python 3.9+，标准库优先
- 日志用 `logging` 模块，中文输出
- 配置从 `config/default.json` 读取
- 策略参数从代码顶部常量读取（`DEVIATION_THRESHOLD` 等）
- 信号与持仓数据通过 JSON 文件 (`paper_state.json`) 序列化

## 测试约定

- 策略修改必须先回测验证
- 回测区间至少覆盖 2023-01-01 至今
- 必报指标：总收益/年化/最大回撤/Sharpe/胜率/盈亏比
- 核心逻辑错误会导致 Dashboard 构建异常（`except` 不能吞掉，必须显式报错）

## 部署说明

### 公网 Dashboard
- 域名: `http://cc-us-stock-dashboard.futienchun.com`
- GitHub Pages 仓库: `TonyTCFu/cc-us-stock-dashboard`
- DNS: Cloudflare CNAME → `tonytcfu.github.io` (DNS only)

### 自动化
- **GitHub Actions**: `.github/workflows/daily-update.yml`, UTC 21:30 周二~周六
  - 源码仓库: `TonyTCFu/quant-us-trading`
  - Secrets: `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, `GH_PAT`
- **本地 crontab**: `30 5 * * 2-6 /bin/bash deploy/daily_update.sh`
  - Token 文件: `deploy/.gh_token` (gitignored)

### 解除 GitHub Push Protection
当包含 token 的 commit 被阻止时:
1. `git filter-branch` 清理历史
2. 重新生成新 token 替换 `deploy/.gh_token`
3. Force push: `git push --force`

## 项目文件索引

| 文件 | 用途 |
|------|------|
| `CLAUDE.md` | 行为准则 |
| `AGENTS.md` | 本文件 — 项目规范与命令 |
| `MEMORY.md` | 长期记忆 — 架构决策、踩坑、纠正 |
| `.codex/PROJECT_CONTEXT.md` | 完整项目交接文档 |
| `.claude/skills/quant-trading/SKILL.md` | 策略规则固化 |
| `.claude/agents/` | 5 个 Agent 定义文件 |
| `.claude/loop/` | 循环任务文档 (plan/findings/progress) |

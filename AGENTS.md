# AGENTS.md — 美股量化模型项目规范

> 最后更新: 2026-08-05 | Alpaca Paper 真实执行 | $20K 激进策略 | 全自动运营

---

## 项目规则

1. **Alpaca Paper 真实执行** — `src/execution/alpaca_trader.py`
2. **基金规模严格 $20,000** — 任何时候模型管理仓位总成本不得超过 $20K
3. **账户现金隔离** — Alpaca 账户 $82K+ 自由现金不计入模型管理
4. **API 凭证不入仓库** — `.env` gitignored
5. **非交易日不执行交易** — `is_trading_day()` + Alpaca 官方时钟双重守卫
6. **交易费用计入成本** — `cost_per_share = price*(1+SLIPPAGE)+COMMISSION`
7. **FOMC 由模型因子驱动** — 不人工硬编码清仓
8. **Dashboard 构建后自动部署公网 + CDN 刷新**
9. **Git push 使用 pull-rebase 重试避免冲突**

## 当前策略参数 (激进型)

| 参数 | 值 |
|------|-----|
| 股票池 | 20 只 |
| 最大持仓 | 6 只 |
| 单票上限 | 28% × 宏观系数 |
| 止损 | -7% |
| 止盈 | +12% |
| 信号 | MA 5/20 金叉死叉 |
| 偏离阈值 | 5% 标准 / 8% 强趋势 |
| 年化目标 | 15%+ |

## 四层自动化 (DON'T DISABLE)

| 层 | 方式 | 触发 | 依赖 |
|---|------|------|------|
| 1 | GitHub Actions | UTC 21:30 周一~周五 | 零 |
| 2 | 系统 crontab | 5:30 AM 周二~周六 | 电脑开机 |
| 3 | Claude Code Cron | 定时+盘中 | 本会话 |
| 4 | 会话启动主动执行 | 每次打开对话 | — |

## Dashboard 核心规则

- 域名: `http://cc-us-stock-dashboard.futienchun.com`
- 部署方式: `index.html` JS 重定向 → `dash_[build_id].html` (唯一文件名绕过缓存)
- **不要用 collapsible details/summary** — 会破坏 HTML 结构导致内容不可见
- 每次 push 后**必须** `curl POST .../pages/builds` 刷新 CDN
- Dashboard 构建 (`build_dashboard_live.py`) 自动包含部署 + CDN 刷新

## 命令速查

```bash
python3 paper_trading_live.py update    # 每日更新 + Alpaca 真实下单
python3 paper_trading_live.py report    # 查看持仓绩效
python3 build_dashboard_live.py         # 构建 Dashboard + 远程部署 + CDN 刷新
python3 daily_signals.py --top 20       # 信号报告

# 手动部署 (正常情况 build 脚本自动处理)
# 不需要手动操作
```

## 核心代码索引

| 文件 | 用途 |
|------|------|
| `paper_trading_live.py` | 模拟盘 + Alpaca 下单桥接 |
| `build_dashboard_live.py` | Dashboard 生成 + 部署 + CDN |
| `src/execution/alpaca_trader.py` | Alpaca Paper 真实下单引擎 |
| `src/signals/macro_factors.py` | 7 因子宏观评估 + FOMC 检测 |
| `src/decision/llm_enhance.py` | LLM 决策增强 |
| `deploy/daily_update.sh` | 本地 cron 脚本 |
| `.github/workflows/daily-update.yml` | GitHub Actions |

## 新会话启动必须读取

1. `CLAUDE.md`
2. `AGENTS.md` ← 本文件
3. `MEMORY.md`
4. `.codex/PROJECT_CONTEXT.md`
5. `.claude/skills/quant-trading/SKILL.md`
6. `.claude/loop/progress.md`

# 美股短线交易量化模型 — 项目交接文档

> 最后更新: 2026-08-05 | 全自动运营 | Alpaca Paper 真实执行 | $20K 激进策略

## 0. 项目当前状态

| 维度 | 状态 |
|------|------|
| 基金规模 | $20,000 (严格锁定) |
| 启动日期 | 2026-06-12 |
| 当前权益 | ~$20,212 |
| 累计收益 | +1.11% |
| 年化收益 | +10.04% |
| 最大回撤 | -4.86% |
| 持仓 | 6 只 (20 只股票池) |
| 胜率 | 25% (4赢/8亏) |
| 策略参数 | SL7%/TP12%, 28% 单票上限 |
| 数据源 | Alpaca IEX (主) + QVeris (备) |
| 公网 Dashboard | http://cc-us-stock-dashboard.futienchun.com |

## 1. 自动化 (最核心)

**四层全覆盖，不需要用户手动触发：**

1. GitHub Actions: UTC 21:30 周一~周五 (零依赖)
2. 系统 crontab: 5:30 AM 周二~周六 (电脑开机即执行)
3. Claude Code Cron: 5:37 AM 调仓 + 21-23 AM 盘中刷新
4. 会话启动主动执行: 每次打开对话立即跑 update+build+deploy

**权限**: `.claude/settings.local.json` 已加白名单，全程无弹窗。

## 2. Dashboard 部署

- 域名: http://cc-us-stock-dashboard.futienchun.com
- GitHub Pages: TonyTCFu/cc-us-stock-dashboard
- 源码: TonyTCFu/quant-us-trading
- DNS: Cloudflare CNAME → tonytcfu.github.io (DNS only)
- 部署方式: index.html (323B 重定向) → dash_[build_id].html (唯一文件名)
- CDN 刷新: 每次 push 后自动 `curl POST .../pages/builds`

## 3. Alpaca Paper 账户

- 通过 `src/execution/alpaca_trader.py` 真实下单
- 模型只管理 $20K，账户 $82K+ 自由现金隔离
- 交易时间判断用 Alpaca 官方时钟 API (`get_clock().is_open`)

## 4. 禁止事项

- 禁止在 Dashboard 添加 `<details><summary>` 折叠 (会破坏 HTML)
- 禁止使用账户额外现金 (严格 $20K)
- 禁止在非交易日执行交易
- 禁止 FOMC 硬编码清仓 (由 macro_factors.py 模型驱动)
- 禁止 push 后不刷新 CDN

## 5. 新会话启动清单

按顺序读取:
1. `CLAUDE.md`
2. `AGENTS.md`
3. `MEMORY.md`
4. `.codex/PROJECT_CONTEXT.md` ← 本文件
5. `.claude/skills/quant-trading/SKILL.md`
6. `.claude/loop/progress.md`

第一件事: `python3 paper_trading_live.py update && python3 build_dashboard_live.py`

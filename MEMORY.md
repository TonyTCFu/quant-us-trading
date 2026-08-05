# MEMORY.md — 美股量化模型长期记忆

> 最后更新: 2026-08-05 | 全自动运营 | $20K 激进策略

## 架构决策

### 四层自动化架构 (2026-08-05 定型)
- GitHub Actions + 系统 crontab + Claude Cron + 会话主动执行
- Dashboard 使用唯一文件名绕过 Safari 缓存
- Git push 使用 pull-rebase 重试避免冲突

### 基金规模 $20K 严格锁定 (2026-07-31)
- 任何时候模型仓位总成本 ≤ $20K
- 账户 $82K+ 自由现金不计入
- Dashboard 独立显示模型权益 vs 账户总权益

### Alpaca Paper 真实下单 (2026-07-31)
- `src/execution/alpaca_trader.py` 连接真实 Paper 账户
- 交易时间判断用 Alpaca 官方时钟 API，不自己算

### Dashboard 折叠失败教训 (2026-08-04~05)
- `<details><summary>` 在复杂 HTML 中会破坏结构
- 用户反复反馈"看不见内容"后完全回退
- **永远不要再尝试在 Dashboard 中加折叠区块**

### CDN 缓存刷新 (2026-08-04)
- 每次 push 后必须 `curl POST .../pages/builds`
- 已集成到 `build_dashboard_live.py`、cron 脚本、Actions

## 踩坑记录 (新增)

| # | 问题 | 发现 | 修复 | 严重性 |
|---|------|------|------|--------|
| 1~8 | (见之前) | | | |
| 9 | $100K 全仓买入 | 2026-07-31 | 清空重来 $20K | 严重 |
| 10 | 时区误判 | 2026-07-31 | Alpaca 官方时钟 API | 中 |
| 11 | CDN 不刷新 | 2026-08-04 | 集成到所有路径 | 低 |
| 12 | Dashboard 折叠导致内容消失 | 2026-08-05 | 完全移除折叠，回退原始版 | 严重 |
| 13 | index.html 是 9.5KB 旧全量页 | 2026-08-05 | 改为 323 字节纯重定向 | 严重 |
| 14 | Actions/cron 同时 push 冲突 | 持续性 | pull-rebase 重试 3 次 | 低 |

## 用户纠正 (关键)

- **"你应该在 Alpaca Paper 账户里交易，不是在模拟 JSON"**
- **"基金规模 $20K，不能因为账户有钱就多用"**
- **"Dashboard 折叠什么都看不见了，调回原来版本"**
- **"不需要我点允许，直接执行"**
- **"每次打开对话，主动执行更新，不要等我提醒"**

## 自动化状态

- GitHub Actions: `TonyTCFu/quant-us-trading` → `.github/workflows/daily-update.yml`
- 本地 crontab: `30 5 * * 2-6 /bin/bash deploy/daily_update.sh`
- Claude Cron: 5:37 AM 调仓 + 21-23 点盘中刷新
- 权限白名单: `.claude/settings.local.json` (225 allow rules, 无弹窗)
- Dashboard token: `deploy/.gh_token` (gitignored)

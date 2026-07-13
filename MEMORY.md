# MEMORY.md — 美股量化模型长期记忆

> 最后更新: 2026-07-10

---

## 架构决策

### MA 5/20 + SL5%/TP10% 作为主策略
- **时间**: 2026-06-12
- **理由**: 网格搜索 + 滚动窗口验证确认最优参数组合
- **替代方案**: MA 20/50（太慢）、RSI Reversal（胜率低）、BB Breakout（高波动专用）

### Loop Engineering 多 Agent 框架
- **时间**: 2026-06-14
- **理由**: Maker-Checker 分离防止"自己写代码自己审"
- **5 Agent**: Data → Signal → Risk → Review(独立审查) → Report

### GitHub Actions 作为主要调度
- **时间**: 2026-07-06
- **理由**: crontab 依赖电脑开机，Actions 完全独立于用户设备
- **crontab 降级为备用**

### FOMC 处理由模型因子驱动，非人工硬编码
- **时间**: 2026-06-18
- **理由**: 用户纠正 — FOMC 清仓不该是硬编码规则
- **现状**: `MacroOverlay.evaluate()` 自动产生 `multiplier=0` 和 `FOMC_EVACUATION`

### 自定义域名 GitHub Pages 部署
- **时间**: 2026-06-16
- **域名**: `cc-us-stock-dashboard.futienchun.com`
- **DNS**: Cloudflare CNAME → `tonytcfu.github.io` (DNS only，非 Proxied)

---

## 踩坑记录

| # | 问题 | 发现 | 修复 | 严重性 |
|---|------|------|------|--------|
| 1 | `VIXY / 0.3` 公式得出 VIX≈78（实际 18.5）| 2026-06-14 Review Agent | 改用 VIXY 价格区间判断 | BLOCKED |
| 2 | FOMC 预警只 ±1 天，来不及清仓 | 2026-06-14 Review Agent | 向前扫描 7 天，4 天内撤离 | WARNING |
| 3 | `avg_cost` 不含 `COMMISSION` | 2026-06-14 用户反馈 | `cost_per_share = price*(1+SLIPPAGE)+COMMISSION` | 中 |
| 4 | Dashboard 非开盘时 PnL 全显示 0 | 2026-06-16 用户反馈 | 拉取改为 5 天窗口而非只拉当日 | 中 |
| 5 | Dashboard `f` 前缀缺失导致模板变量裸露 | 2026-06-23 用户反复截图 | `html += """` → `html += f"""` | 严重(UI全坏) |
| 6 | GitHub Pages CDN 缓存用户看不到新内容 | 2026-06-23 | index.html 重定向 + JS 刷新 + Pages API rebuild | 中 |
| 7 | 6/18 误在非交易日执行买卖 | 2026-06-18 用户纠正 | `is_trading_day()` + NYSE 假日表 | 中 |
| 8 | GitHub Actions 和 crontab 同时推送冲突 | 2026-07-10 | 脚本加 pull --rebase 自动重试 | 低 |

---

## 用户纠正

- **2026-06-18**: 不应该在 FOMC 结束日执行清仓。FOMC 由模型决定，不由人工硬编码。——已修复
- **2026-06-14**: 交易费用（佣金）必须计入 avg_cost 和 entry_price。——已修复
- **2026-06-16**: 非交易时间的模拟盘操作不产生真实费用，不应该计算佣金。——已添加 `is_trading_day()` 守卫
- **2026-07-10**: 项目必须有 AGENTS.md 和 MEMORY.md。——已创建

---

## 外部资源

| 资源 | 地址 | 用途 |
|------|------|------|
| Dashboard 公网 | http://cc-us-stock-dashboard.futienchun.com | 实时看板 |
| GitHub Dashboard 仓库 | https://github.com/TonyTCFu/cc-us-stock-dashboard | 页面部署 |
| GitHub 源码仓库 | https://github.com/TonyTCFu/quant-us-trading | Actions 运行 |
| Alpaca Paper API | data.alpaca.markets | 免费 IEX 行情 |
| QVeris → Alpha Vantage | qveris CLI | 备用数据源 (2 credits/次) |
| Cloudflare DNS | dash.cloudflare.com | 域名管理 |
| GitHub Token Settings | https://github.com/settings/tokens | PAT 管理 |

---

## 运维备注

- PAT token 存放在 `deploy/.gh_token` (gitignored)，格式: `ghp_xxx`
- `paper_state.json` 必须提交到源码仓库（Actions 需要）
- Dashboard 部署和源码是两个独立的 GitHub 仓库
- 定期检查 Actions runs 是否有失败: https://github.com/TonyTCFu/quant-us-trading/actions
- GitHub Pages CDN 缓存 10 分钟，更新后可能需要等待
- 每天 cron 日志: `/tmp/quant_daily_YYYYMMDD.log`

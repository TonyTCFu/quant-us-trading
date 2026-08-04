# MEMORY.md — 美股量化模型长期记忆

> 最后更新: 2026-08-04 | Alpaca Paper 真实执行 | $20K 激进策略

---

## 架构决策

### MA 5/20 + SL7%/TP12% 激进参数 (2026-07-31 升级)
- **时间**: 2026-07-31
- **理由**: 扩池至 20 只、放宽止损 -7%、止盈 +12%、集中至 6 只持仓
- **背景**: 用户要求更激进运营、增加选股、创造更大收益

### $20K 基金预算严格锁定 (2026-07-31)
- **时间**: 2026-07-31（用户多次纠正后）
- **规则**: 任何时候模型管理仓位总成本不得超过 $20,000
- **理由**: 用户明确指出不得使用账户额外现金，基金规模 $20K 起点

### Alpaca Paper 真实下单执行 (2026-07-31)
- **时间**: 2026-07-31
- **关键文件**: `src/execution/alpaca_trader.py`
- **理由**: 此前模型只在 paper_state.json 里模拟，从未在 Alpaca 实际下单

### Dashboard 折叠区块 (2026-08-04)
- **时间**: 2026-08-04
- **方案**: `<details><summary>` 标签，4 个区块默认折叠
- **理由**: 用户反复要求手机端简化显示，减少滚动长度
- **实现**: 直接集成在 `build_dashboard_live.py` 后处理步骤中

### 唯一文件名绕过缓存 (2026-08-04)
- **时间**: 2026-08-04
- **方案**: `dash_[build_id].html` → `index.html` JS 重定向
- **理由**: Safari iOS 对同一文件名的 no-cache 指令不响应

### FOMC 处理由模型因子驱动 (2026-06-18)
- **时间**: 2026-06-18（用户纠正后确认）
- **规则**: `MacroOverlay.evaluate()` 自动产生 `multiplier=0`，由模型决定
- **现状**: 不做人工硬编码清仓

---

## 踩坑记录

| # | 问题 | 发现 | 修复 | 严重性 |
|---|------|------|------|--------|
| 1 | `VIXY / 0.3` 公式得出 VIX≈78（实际 18.5）| 2026-06-14 Review Agent | 改用 VIXY 价格区间判断 | 严重 |
| 2 | FOMC 预警只 ±1 天 | 2026-06-14 Review Agent | 向前扫描 7 天，4 天内撤离 | 中 |
| 3 | `avg_cost` 不含 `COMMISSION` | 2026-06-14 | `cost_per_share = price*(1+SLIPPAGE)+COMMISSION` | 中 |
| 4 | Dashboard 非开盘时 PnL 全 0 | 2026-06-16 | 拉取改为 5 天窗口 | 中 |
| 5 | `html += """` 缺少 `f` 前缀 | 2026-06-23 | 改为 f-string | 严重 |
| 6 | Safari 缓存 Dashboard 旧版 | 2026-06-23~08-04 | 唯一文件名 `dash_[build_id].html` + JS 重定向 | 中 |
| 7 | 6/18 误在非交易日执行买卖 | 2026-06-18 | `is_trading_day()` + NYSE 假日表 | 中 |
| 8 | Actions/cron 同时 push 冲突 | 2026-07-10~08-04 | `pull --rebase && push` 重试 3 次 | 低 |
| 9 | 用了 $100K 账户资金买股 | 2026-07-31 用户纠正 | 清空重来，严格 $20K 预算 | 严重 |
| 10 | 时区误判为"非交易时间" | 2026-07-31 | `_is_market_open()` 改用 Alpaca 官方时钟 API | 中 |
| 11 | CDN 缓存更新不及时 | 2026-08-04 | 每次 push 后自动 `curl POST` Pages rebuild API | 低 |

---

## 用户纠正

- **2026-06-18**: FOMC 清仓不该是硬编码 —— 已改为模型因子驱动
- **2026-06-14**: 交易费用必须计入 avg_cost —— 已修复
- **2026-07-31**: 不得用账户 $100K 买股，基金规模就是 $20K —— 已修正，多次强调
- **2026-07-31**: 账户额外 $82K 现金不计入模型 —— Dashboard 已隔离显示
- **2026-08-04**: Dashboard 折叠默认收起 —— 已改为 `<details>` 无 `open` 属性
- **2026-08-04**: 自动部署不要挂掉 —— 已加 pull-rebase 重试循环

---

## 外部资源

| 资源 | 地址 |
|------|------|
| Dashboard 公网 | http://cc-us-stock-dashboard.futienchun.com |
| GitHub Dashboard 仓库 | https://github.com/TonyTCFu/cc-us-stock-dashboard |
| GitHub 源码仓库 | https://github.com/TonyTCFu/quant-us-trading |
| Cloudflare DNS | dash.cloudflare.com |
| GitHub Token Settings | https://github.com/settings/tokens |

## 运维备注

- PAT token: `deploy/.gh_token` (gitignored)
- Dashboard 和源码是两个独立仓库
- `paper_state.json` 已提交到源码仓库（Actions 需要）
- Actions 监控: https://github.com/TonyTCFu/quant-us-trading/actions
- 日志: `/tmp/quant_daily_YYYYMMDD.log`
- 本地 cron: `30 5 * * 2-6 /bin/bash deploy/daily_update.sh`

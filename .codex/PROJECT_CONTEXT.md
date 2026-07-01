# 美股短线交易量化模型 — 项目交接文档

> 最后更新: 2026-06-18 | 阶段: 模拟盘运行中 | 目标: 年化 8%+

---

## 0. 项目当前状态速览

| 维度 | 状态 |
|------|------|
| 模拟盘起始 | 2026-06-12, $20,000 |
| 当前权益 | $20,285 (截至 6/14) |
| 持仓 | JNJ(16), GE(12), JPM(12), GS(3), CAT(4) |
| 累计收益 | +1.47% |
| 主策略 | MA 5/20 + SL5%/TP10% + 宏观多因子 |
| 主数据源 | Alpaca Paper IEX (免费) |
| 备数据源 | QVeris → Alpha Vantage (2 credits/次) |
| 定时任务 | 周二~周六 5:15 AM 北京时间 |
| 公网 Dashboard | http://cc-us-stock-dashboard.futienchun.com |
| GitHub 仓库 | https://github.com/TonyTCFu/cc-us-stock-dashboard |

---

## 1. 项目定位与安全边界

### 1.1 项目目标
研究、回测和模拟验证美国股市短线交易策略。当前阶段为 **$20,000 模拟盘实跑验证**，目标年化收益率 8%+。

### 1.2 安全边界（永远生效）

- **默认禁止实盘下单** — Alpaca 仅读取行情，模拟盘独立运行
- API 凭证仅存于 `.env`（gitignore 保护）
- 策略必须可解释，参数必须可配置
- 禁止未来函数和数据泄漏
- 所有结论注明"仅供参考，不构成投资建议"
- 非交易日不得执行交易（`is_trading_day()` 守卫）
- 模拟盘交易日检查含 NYSE 2026 假日表

---

## 2. 架构总览

```
                      ┌─────────────────────────────┐
                      │   UnifiedFetcher (数据层)    │
                      │   Alpaca IEX ─┬─ QVeris AV  │
                      └──────┬────────┴──────┬──────┘
                             │               │
                    ┌────────▼───────────────────────┐
                    │   本地 CSV 缓存 (data/*_1d.csv) │
                    └────────────────┬────────────────┘
                                     │
        ┌────────────────────────────┼────────────────────────────┐
        │                            │                            │
  ┌─────▼──────┐            ┌───────▼───────┐            ┌───────▼──────┐
  │  信号层     │            │   宏观叠加层    │            │   风控层     │
  │ MA/MACD/RSI │──────────▶ │ FOMC/VIX/DXY  │──────────▶ │ SL5%/TP10%  │
  │ BB/Combined │            │ 行业/资金/宽度  │            │ 回撤/仓位    │
  └────────────┘            └───────────────┘            └───────┬──────┘
                                                                │
  ┌─────────────────────────────────────────────────────────────▼──────┐
  │                        执行层 + Agent 管道                         │
  │                                                                  │
  │  paper_trading_live.py  模拟盘主控 (init/update/report/backtest)   │
  │  Data→Signal→Risk→Review→Report  (Loop Engineering 多Agent管道)   │
  │  build_dashboard_live.py → deploy/index.html → GitHub Pages       │
  └──────────────────────────────────────────────────────────────────┘
```

---

## 3. Loop Engineering 多 Agent 框架

### 3.1 五个 Agent 的职责

| Agent | 文件 | 职责 | 输入 | 输出 |
|-------|------|------|------|------|
| Data | `.claude/agents/data-agent.md` | 拉取行情、校验数据质量、切换主备源 | API | CSV 缓存 |
| Signal | `.claude/agents/signal-agent.md` | 计算 MA/MACD/RSI/BB 指标，生成 BUY/SELL/HOLD | 数据 CSV | 信号表 |
| Risk | `.claude/agents/risk-agent.md` | 宏观因子评估、仓位系数计算、止损止盈 | 信号 + 持仓 | 调仓指令 |
| Review | `.claude/agents/review-agent.md` | **Maker-Checker 独立审查**，不看推理只看结论 | 原始数据 + Agent 输出 | PASS/WARNING/BLOCKED |
| Report | `.claude/agents/report-agent.md` | 生成 Dashboard HTML + 每日信号 MD + 异常告警 | 审查通过的调仓结果 | 报告产物 |

### 3.2 核心设计原则

- **Maker-Checker 分离**：Review Agent 不读取 Signal/Risk Agent 的推理过程，只读最终 JSON + 原始数据。防止"自己写的代码自己审"。
- **每步有验收条件**：Data → 数据完整 → Signal → 信号分布合理 → Risk → 边界合规 → Review → PASS/WARNING → Report
- **暂停条件明确**：数据双故障 / 信号 >80% 同向 / 回撤 >5% / 审查 BLOCKED → 暂停等人
- **状态文件驱动**：`.claude/loop/task_plan.md` + `findings.md` + `progress.md` 实现跨会话记忆

### 3.3 项目知识固化

| 文件 | 内容 |
|------|------|
| `.claude/skills/quant-trading/SKILL.md` | 策略规则、风控约束、命令速查 |
| `.claude/skills/daily-update/SKILL.md` | 每日模拟盘更新 Goal 模板 |
| `.claude/skills/loop-orchestrator/SKILL.md` | 多 Agent 编排器，调度 5 Agent 顺序执行 |
| `.codex/PROJECT_CONTEXT.md` | 本文件 — 完整项目交接文档 |

### 3.4 定时任务 (Scheduled Tasks)

| 任务 | 做什么 | cron | 下次运行 |
|------|--------|------|----------|
| `paper-trading-update` | 6 步多 Agent 管道：数据→信号→风控→审查→报告→回写→部署到公网 | `15 5 * * 2,3,4,5,6` (北京时间) | 下一个美股交易日 |
| `weekly-review` | 周度回顾报告：收益、回撤、vs 年化 8%、下周展望 | `30 6 * * 6` | 周六 |

---

## 4. 策略体系

### 4.1 当前主策略: MA 5/20 + SL5%/TP10% + 宏观多因子

- 金叉买入: MA5 上穿 MA20, 且价格 > MA20
- 死叉卖出: MA5 下穿 MA20
- 止损: -5% (从入场价)
- 止盈: +10% (从入场价)
- 信号延迟: next_open (避免未来函数)

### 4.2 宏观因子体系 (macro_factors.py)

**注意：FOMC 处理由 `MacroOverlay.evaluate()` 自动驱动，不是人工硬编码规则。**

| 因子 | 数据源 | 权重 | 说明 |
|------|--------|------|------|
| 经济日历 | `ECONOMIC_CALENDAR_2026` 硬编码表 | 2.5 | FOMC 3 天内高警、5 天内提示、当天禁航 |
| VIX 波动率 | ^VIX (优先) → VIXY ETF (回退) | 2.5 | **2026-06-14 修复：删除 VIXY/0.3 错误公式** |
| DXY 美元 | UUP ETF | 1.0-1.5 | 美元走强=美股逆风 |
| 行业轮动 | 11 个 Sector ETF | 0.8-1.5 | 持仓行业 vs 市场均值 |
| 资金流 | SPY 成交量异常 | 0.8-2.0 | 放量下跌=恐慌 |
| 市场宽度 | %股票 > MA20 | 1.0-2.0 | 49% 以下偏空 |

FOMC 撤离逻辑：
- `macro_factors.py:_economic_calendar()` 向前扫描 7 天
- FOMC 3 天内 → score=75
- `evaluate()` 检测 4 个自然日内有 FOMC → multiplier=0.0, rec=FOMC_EVACUATION
- `cmd_update()` 读取 macro_multiplier，=0 时跳过新买入
- **是否平仓根据 FOMC 信号和风控规则由系统判定，不是人工一刀切**

### 4.3 股票池与排名

Top 13 股票池 (按 MA 5/20 Sharpe 排名):
GS, GE, NVDA, JNJ, GOOGL, CAT, META, JPM, NFLX, WMT, AMZN, MSFT, AAPL

跨策略最稳健 (5/5 策略正 Sharpe): GOOGL, GE, JNJ, CAT, GS, JPM, META, TSLA, NFLX (9 只)

### 4.4 风控规则

- 单票上限: 25% × 宏观仓位系数
- 最大持仓: 8 只
- 总仓位上限: 95%
- 回撤熔断: 总权益回撤 > 5% → 暂停新开仓
- 流动性过滤: 最小日均成交量 500,000
- 交易费用: $0.005/股 commission + 0.05% slippage
- **交易成本已正确计入 avg_cost 和 entry_price (2026-06-14 修复)**

---

## 5. Bug 修复历史 (重要，避免重犯)

### 5.1 VIX 计算严重错误 [2026-06-14 修复]
- **问题**: `macro_factors.py:_vix_regime()` 使用 `implied_vix = VIXY / 0.3`
- **后果**: VIXY=$23.32 → "VIX≈78 (恐慌)" → 仓位系数被错误压到 25%
- **根因**: VIXY 是 VIX 期货 ETF，价格与 VIX 指数非 0.3 倍关系
- **修复**: 新增 `_try_get_vix()` 优先获取真实 ^VIX 指数；回退用 VIXY 自身价格区间 ($16-22-28-35)；新增 `_vix_result()` 处理真实 VIX

### 5.2 FOMC 预警窗口太窄 [2026-06-14 修复]
- **问题**: `_economic_calendar()` 仅检查 FOMC 当天 ±1 天，周三开会到周二才检测
- **修复**: 向前扫描 7 天；FOMC 3 天内 score=75；`evaluate()` 中 4 天内触发 multiplier=0

### 5.3 avg_cost/entry_price 不含佣金 [2026-06-14 修复]
- **问题**: `execute_buy` 和 `_open_position` 的 `avg_cost` 不含 COMMISSION
- **修复**: 引入 `cost_per_share = price*(1+SLIPPAGE)+COMMISSION`；avg_cost 和 entry_price 均用此值；PnL% 基于 avg_cost

### 5.4 Dashboard 价格拉取失败 [2026-06-16 修复]
- **问题**: `build_dashboard_live.py` 用 `start=today, end=today` 拉价格，非开盘时间返回空 → fallback 为成本价 → PnL 全显示 0
- **修复**: 改为 `start=5天前, end=today`

### 5.5 GitHub Pages 不自动重建 [发现于 2026-06-16]
- **现象**: `git push` 后部署内容不更新
- **临时方案**: 手动 `gh api .../pages/builds -X POST` 触发重建
- **长期方案**: 定时任务中调用 deploy.sh 后跟手动触发

### 5.6 误在非交易日执行交易 [2026-06-18 已修复]
- **问题**: 6/18 周四上午误执行了 5 卖 5 买（模拟盘，非真实交易），因为缺少交易日守卫
- **修复**: 新增 `is_trading_day()` 函数（含 NYSE 2026 假日表）；`execute_buy`/`execute_sell`/`cmd_update` 均添加守卫；已清理脏数据

---

## 6. Dashboard 部署信息

| 项目 | 值 |
|------|-----|
| 公网地址 | http://cc-us-stock-dashboard.futienchun.com |
| GitHub Pages | https://tonytcfu.github.io/cc-us-stock-dashboard/ |
| 仓库 | https://github.com/TonyTCFu/cc-us-stock-dashboard (public) |
| 部署目录 | `/Users/tonyfu/Claude/deploy/` |
| 部署脚本 | `deploy/deploy.sh` |
| CNAME | `cc-us-stock-dashboard.futienchun.com` |
| DNS | Cloudflare (CNAME → tonytcfu.github.io, DNS only) |
| 域名注册商 | GoDaddy |
| App 图标 | 浅银灰底 + K线 + 绿色三角箭头 + "US QUANT" |
| 图标格式 | PNG 192px + 512px + PWA manifest |

---

## 7. FOMC 处理逻辑说明 (模型驱动)

FOMC 相关决策由以下组件协作完成，**不需要人工硬编码**：

1. `ECONOMIC_CALENDAR_2026` 字典 — 记录全年 FOMC 日期（年 8 次，周三 2PM ET）
2. `_economic_calendar()` — 向前扫描 7 天，3 天内 FOMC → score=75
3. `evaluate()` — 4 个自然日内有 FOMC → `multiplier=0.0, rec=FOMC_EVACUATION`
4. `cmd_update()` — 读取 `macro_multiplier`，=0 时跳过新买入，日志标注 FOMC 状态
5. 止损止盈 — 不受 FOMC 影响，仍然按规则触发（-5%/+10%）

**注意**：multiplier=0 时系统跳过新买入，但已有持仓的平仓由止损止盈和信号卖出驱动，不是由 FOMC 一刀切强制平仓。这是模型驱动的行为，当宏观因子判定"极危险"时仓位系数归零，自然不做多。

---

## 8. 关键决策记录

| 日期 | 决策 | 理由 |
|------|------|------|
| 2026-06-12 | 主数据源: Alpaca Paper IEX | 免费、直连、全量历史 |
| 2026-06-12 | 开发环境: Python 3.9.6 | macOS 系统自带 |
| 2026-06-12 | 时区基准: US/Eastern | 美股交易时段标准 |
| 2026-06-12 | 主策略: MA 5/20 + SL5%/TP10% | 网格搜索+滚动验证确认最优 |
| 2026-06-14 | Loop 框架: Claude Code /goal + /loop + 5 Agent | 已在用，不需要额外工具 |
| 2026-06-14 | Maker-Checker: Review Agent 用独立视角 | 写代码的模型给自己打分太松 |
| 2026-06-14 | 部署方案: GitHub Pages + Cloudflare DNS | 免费、HTTPS、自定义域名 |
| 2026-06-14 | 交易日守卫: is_trading_day() + NYSE 假日表 | 防止非交易日的误操作 |
| 2026-06-18 | FOMC 处理: 模型因子驱动，非人工硬编码清仓 | macro_factors.py 自动判定 |

---

## 9. 新会话快速加载指南

**新 Claude Code 会话开始时，按顺序读取：**

1. `CLAUDE.md` — 行为准则 + 项目指令摘要
2. `.codex/PROJECT_CONTEXT.md` — **本文件**，完整项目上下文
3. `.claude/skills/quant-trading/SKILL.md` — 策略规则与约束
4. `.claude/loop/progress.md` — 最新进度与交接记录
5. `.claude/loop/task_plan.md` — 当前任务队列
6. `.claude/loop/findings.md` — Bug 修复记录与踩坑
7. 确认 `.env` 存在 — Alpaca 凭证
8. `outputs/paper_state.json` — 当前模拟盘持仓
9. `outputs/dashboard/live.html` — 最新 Dashboard

**关键一句话**：基于 MA 5/20 + 宏观多因子的美股量化模拟盘，$20,000 运行中，Loop Engineering 多 Agent 框架就绪，Dashboard 部署在 cc-us-stock-dashboard.futienchun.com，每日定时自动更新。

---

## 10. 运行命令速查

```bash
# 模拟盘
python3 paper_trading_live.py update    # 每日更新
python3 paper_trading_live.py report    # 查看持仓
python3 paper_trading_live.py backtest  # 滚动回测

# 全市场扫描
python3 market_scan.py --fast 5 --slow 20 --start 2023-01-01 --top 30

# 每日信号
python3 daily_signals.py --top 13 --output outputs/daily_signals_$(date +%Y%m%d).md

# Dashboard
python3 build_dashboard_live.py

# 手动部署到公网
cp outputs/dashboard/live.html deploy/index.html
git -C deploy add index.html && git -C deploy commit -m "Update" && git -C deploy push origin main
```

---

## 11. V2 整合: LLM 决策增强 + 偏离阈值 + 并行扫描 (2026-06-23)

### 11.1 决策增强层 (`src/decision/`)
参考 daily-stock-analysis 的 AI Decision Dashboard 理念，在技术信号之上叠加综合研判：

| 功能 | 说明 |
|------|------|
| `build_decision_card()` | 为单只股票构建完整决策卡：结论+买卖价+止损止盈+置信度+检查清单+风险标记 |
| `compute_deviation()` | 计算价格偏离 MA5 百分比 |
| `should_skip_chase()` | 偏离阈值检查：标准 5%，强趋势自动放宽 8% |
| `estimate_confidence()` | 置信度计算：趋势(40%)+RSI(20%)+MACD(20%)-宏观风险(20%) |
| `detect_risk_flags()` | 自动检测：偏离过大/RSI极端/FOMC临近/仓位系数低/VIX高波动 |

Dashboard 新增"🧠 AI 决策面板"区块，按置信度排序展示 8 只股票的买卖建议和风险标记。

### 11.2 偏离阈值 (DEVIATION_THRESHOLD)
- 价格偏离 MA5 > 5% 不追高
- 强趋势（MA5 > MA20*1.03）自动放宽到 8%
- `paper_trading_live.py::cmd_update()` 和 `llm_enhance.py` 双重检查

### 11.3 并行扫描
- `daily_signals.py` 新增 `--workers N` 参数（默认 4 线程）
- `ThreadPoolExecutor` 并行拉取和分析 13 只股票
- 结果保持原始 ticker 排列顺序

---

*本文档由 Claude Code 生成并维护。任何重大架构变更后应及时更新。*

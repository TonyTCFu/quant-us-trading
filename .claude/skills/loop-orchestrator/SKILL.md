---
name: loop-orchestrator
description: Loop 编排器 — 调度 5 个 Agent 顺序执行，处理暂停和人机交互
mode: goal
---

# Loop Orchestrator — 美股量化多 Agent 编排器

## 目标

按"数据→信号→风控→审查→报告"顺序执行每日模拟盘更新。每步有明确验收条件, 遇暂停条件时停止并等待人工介入。

## 执行前必须读取

1. `.claude/skills/quant-trading/SKILL.md` — 项目规则
2. `.claude/loop/task_plan.md` — 任务计划
3. `.claude/loop/progress.md` — 最新进度
4. `.claude/loop/findings.md` — 最新发现
5. `outputs/paper_state.json` — 当前持仓
6. `.env` — API 凭证 (只读)

## Agent 执行链

每步完成后检查返回值, 再决定是否继续。

```
[1. Data Agent] → status=OK
    ↓ OK
[2. Signal Agent] → status=OK
    ↓ OK
[3. Risk Agent] → status=OK/FOMC_PAUSE
    ↓ OK
[4. Review Agent] → status=PASS/WARNING
    ↓ PASS or WARNING
[5. Report Agent] → status=OK
```

## 每一步的具体指令

### Step 1: 启动 Data Agent
```
Agent(
  description: "Data Agent 数据拉取",
  prompt: "读取 .claude/agents/data-agent.md 中的完整定义。
           按照定义执行数据拉取和校验。
           返回 JSON: {status, tickers_updated, last_trading_day, anomalies}"
)
```
**验收**: status=OK
**暂停条件**: status=BLOCKED → 写 findings.md → 暂停, 等待人工恢复数据源

### Step 2: 启动 Signal Agent
```
Agent(
  description: "Signal Agent 信号生成",
  prompt: "读取 .claude/agents/signal-agent.md 中的完整定义。
           确认 Data Agent 返回 status=OK。
           按照定义生成交易信号。
           返回 JSON: {status, signals, anomaly_flag}"
)
```
**验收**: status=OK, anomaly_flag=false
**暂停条件**: status=BLOCKED → 暂停, 等待人工判断信号异常

### Step 3: 启动 Risk Agent
```
Agent(
  description: "Risk Agent 风控与调仓",
  prompt: "读取 .claude/agents/risk-agent.md 中的完整定义。
           确认 Signal Agent 返回 status=OK。
           按照定义评估宏观因子, 生成调仓指令。
           返回 JSON: {status, macro_score, position_multiplier, orders, drawdown_pct, meltdown}"
)
```
**验收**: status=OK (或 FOMC_PAUSE, 这是正常状态)
**暂停条件**: meltdown=true → 暂停, 等待人工决策

### Step 4: 启动 Review Agent (独立审查)
```
Agent(
  description: "Review Agent 独立审查",
  prompt: "读取 .claude/agents/review-agent.md 中的完整定义。
           ⚠️ 你是独立审查者, 不可读取前 3 个 Agent 的推理过程。
           只读取它们的 JSON 输出 + 原始数据 + paper_state.json。
           逐项检查: 回测一致性、参数偏离、边界、风控合规、数据质量。
           返回 JSON: {status, checks, warnings, blocks, verdict}"
)
```
**验收**: verdict=PASS 或 WARNING
**暂停条件**: verdict=BLOCKED → 暂停, 列出 blocks 详情, 等待人工判断

### Step 5: 启动 Report Agent
```
Agent(
  description: "Report Agent 报告生成",
  prompt: "读取 .claude/agents/report-agent.md 中的完整定义。
           确认 Review Agent 返回 verdict=PASS 或 WARNING。
           按照定义生成 Dashboard、每日摘要、更新权益曲线。
           如有 WARNING, 在报告中标注风险项。
           返回 JSON: {status, dashboard, daily_summary, alerts, daily_return_pct, drawdown_pct}"
)
```
**验收**: status=OK, dashboard 文件存在

## 全部完成后

更新 `.claude/loop/progress.md`:
```markdown
### YYYY-MM-DD: 每日更新完成
- Data: OK, XX 只股票, 最后交易日 YYYY-MM-DD
- Signal: XX 个信号 (BUY: X, SELL: X)
- Risk: 宏观评分 XX, 仓位系数 XX%, X 笔调仓
- Review: [PASS/WARNING/BLOCKED]
- Report: [已生成/失败]
- 权益: $XX,XXX (日收益 +X.XX%)
- 异常: [如有]
```

## 暂停后人工介入指南

1. **数据双故障**: 手动执行 `python3 batch_pull.py` 或等待数据源恢复
2. **信号异常集中**: 检查是否重大宏观事件日, 判断信号是否合理
3. **回撤熔断**: 评估是否为正常市场回调, 决定是否调整策略参数
4. **审查 BLOCKED**: 查看 blocks 详情, 判断是误报还是真实问题

## 安全边界重申

- `.env` 不变, 实盘不下单
- 策略参数变更需人工审批
- 所有结论注明"仅供参考, 不构成投资建议"
- 模拟盘独立于真实持仓

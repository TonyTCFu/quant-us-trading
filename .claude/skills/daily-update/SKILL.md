---
name: daily-update
description: 美股量化模拟盘每日更新 — 数据拉取→信号生成→风控→审查→报告 全流程
mode: goal
---

# 每日模拟盘更新 Goal

## 目标

完成美股量化模拟盘一个完整交易日的数据更新、信号生成、风控检查和报告输出。

## 执行前必须读取

1. `.claude/skills/quant-trading/SKILL.md` — 策略规则与约束
2. `.claude/loop/task_plan.md` — 当前任务队列
3. `.claude/loop/progress.md` — 上轮进度
4. `outputs/paper_state.json` — 当前持仓状态
5. `.env` — API 凭证 (只读, 绝不写入其他文件)

## 执行步骤 (每轮只做一个, 顺序推进)

### Step 1: 数据刷新
- 运行 `python3 paper_trading_live.py update` 拉取 13 只股票最新日线
- **验收**: 每只股票的 last_date ≥ 上一个交易日; 无 NaN 填充行
- **失败**: 若 Alpaca 故障 → 切换 QVeris 备源 → 重试 1 次 → 仍失败则暂停报告

### Step 2: 信号生成
- 计算 MA/MACD/RSI/BB/ATR 指标, 生成 BUY/SELL/HOLD
- 运行 `python3 daily_signals.py --top 13`
- **验收**: 信号数量合理 (非全 BUY 或全 SELL); 信号与原始数据可对应追溯
- **暂停条件**: >80% 股票同向信号 → 可能是数据异常, 暂停待查

### Step 3: 宏观风控
- 评估 VIX/DXY/FOMC 日历, 计算仓位系数
- 检查现有持仓的止损止盈触发
- 生成调仓指令: 标的、方向、股数、限价
- **验收**: 单票 ≤25% × 仓位系数; FOMC 日无新开仓; 总仓位符合宏观系数
- **暂停条件**: 回撤 >5% 触发熔断 → 暂停等待人工决策

### Step 4: 独立审查 (Maker-Checker)
- 用全新视角审查 Step 2-3 的输出
- **关键**: 不读推理过程, 只读最终信号 + 原始数据
- 检查项:
  - 回测一致性: 当日信号与历史同类场景是否一致
  - 参数偏离: 是否悄无声息改了策略参数
  - 边界检查: 价格在合理范围, 股数计算正确
  - 风控合规: 止损止盈价格设置正确
- **验收**: 所有检查 PASS; WARNING 可继续但标注; BLOCKED 则暂停

### Step 5: 报告生成
- 运行 `python3 build_dashboard_live.py` 刷新 Dashboard
- 生成每日信号 Markdown 报告
- 更新 `outputs/paper_equity.csv` 权益曲线
- **验收**: Dashboard 可正常打开; 数据与 paper_state.json 一致

## 每轮结束回写

更新 `.claude/loop/progress.md`:
```markdown
### YYYY-MM-DD: 每日更新
- 数据: [OK/异常]
- 信号: [数量/异常]
- 风控: [仓位系数/调仓/熔断状态]
- 审查: [PASS/WARNING/BLOCKED]
- 报告: [已生成/失败]
- 下一步: [继续/暂停/等人工]
```

## 暂停后人工介入

- 数据源双故障 → 手动恢复
- 回撤熔断 → 人工判断是否调整策略
- 审查 BLOCKED → 人工判断信号/风控逻辑
- 信号异常集中 → 人工判断市场状态

## 禁止行为

- 禁止修改 `.env` 或 API 凭证
- 禁止实盘下单 (仅模拟盘操作)
- 禁止修改策略参数 (参数变更需人工审批)
- 禁止跳过审查步骤
- 审查时禁止读取信号生成和风控的推理过程

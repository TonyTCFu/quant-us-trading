# Loop 发现与踩坑记录

> 最后更新: 2026-06-14

## 架构发现

### 2026-06-14: Loop Engineering 框架搭建
- **发现**: 项目已有定时任务 (cron 5:15 AM), 但缺少 Skill 文件固化领域知识
- **影响**: 每个新会话需要从零加载项目上下文, 效率低且容易遗漏关键约束
- **对策**: 创建 `.claude/skills/quant-trading/SKILL.md`, 每次运行自动读取

### 2026-06-14: 多 Agent 职责边界
- **发现**: 模拟盘每日流程可分为 5 个独立环节, 每个有明确的输入/输出/验收标准
- **影响**: 单 Agent 执行全流程时可能在某步出错后继续执行, 导致级联错误
- **对策**: 拆分为 5 个 Agent, 每个 Agent 只有权修改自己的输出, 下游 Agent 检查上游输出

## 问题记录

### 2026-06-14: VIX 计算严重错误 [已修复]
- **问题**: `macro_factors.py:_vix_regime()` 使用 `implied_vix = VIXY / 0.3` 推导 VIX
- **严重性**: BLOCKED — VIXY=$23.32 → "VIX≈78 (恐慌)" → 仓位系数被压到 25%
- **根因**: VIXY 是 VIX 期货 ETF, 价格与 VIX 指数非 0.3 倍关系 (实际 VIX≈18.5)
- **修复**: 移除转换公式, 改用 VIXY 自身价格区间判断 ($16-22 正常, $22-28 正常偏高, $28-35 高波动, >$35 恐慌)。同时新增 `_try_get_vix()` 优先尝试获取真实 VIX 指数, `_vix_result()` 处理真实 VIX 阈值.
- **验证**: 修复后宏观评分 41→22.5, 仓位系数 75%→100%, 建议 CAUTION→NORMAL

### 2026-06-14: FOMC 预警窗口太窄 [已修复]
- **问题**: `_economic_calendar` 仅检查 FOMC 当天 ±1 天, 6/17 FOMC 要到 6/16 (周二) 才检测到, 来不及周一清仓
- **修复内容**:
  1. `_economic_calendar`: 向前扫描未来 7 天, FOMC 3 天内 score=75 ("建议提前清仓"), 5 天内 score=40 ("注意仓位")
  2. `evaluate()`: FOMC 前 4 个自然日内强制 multiplier=0, rec=FOMC_EVACUATION (覆盖周末间隔)
- **场景验证**:
  - 周五(6/12): score=29, NORMAL, 100% — 正常交易 + 提示
  - 周一(6/15): score=40, FOMC_EVACUATION, 0% — 强制撤离 ✓
  - 周二(6/16): score=40, FOMC_EVACUATION, 0% — 强制撤离 ✓
  - 周三(6/17): score=46, FOMC_BLACKOUT, 0% — 禁航 ✓

## 约束清单

1. `.env` 不得被任何 Agent 读取后写入其他文件 (安全边界)
2. 回测代码禁止在测试集上优化参数 (数据泄漏)
3. 实盘下单 API 路径在默认配置中不可达 (安全熔断)
4. 所有 Agent 的输出必须可追溯到输入 (可审计性)
5. Review Agent 不可读取 Signal/Risk Agent 的推理过程, 只读最终结论

### 2026-06-14: avg_cost/entry_price 不含佣金 [已修复]
- **问题**: `execute_buy`(live) 和 `_open_position`(backtest) 的 `avg_cost` 和 `entry_price` 均不含 `COMMISSION`
  - 买入: `avg_cost = price * (1+SLIPPAGE)` 缺少 `+COMMISSION`
  - 加仓: 加权均价公式只乘 `price` 不含滑点和佣金
  - 卖出 PnL%: 用不含佣金的 `entry_price` 作分母
- **影响**: 清仓建仓时佣金未被计入成本基础, PnL 和 PnL% 偏高
- **修复**:
  1. `paper_trading_live.py execute_buy`: 引入 `cost_per_share = price*(1+SLIPPAGE)+COMMISSION`, avg_cost 和 entry_price 均用此值
  2. `paper_trading_live.py execute_sell`: PnL% 改用 `proceeds_per_share/avg_cost - 1`
  3. `paper_trader.py _open_position`: 同上, `cost_per_share = price*(1+slippage)+commission`
  4. `paper_trader.py _close_position`: 卖出价含佣金, PnL% 基于 avg_cost
  5. `paper_state.json`: 历史持仓的 avg_cost/entry_price 已修复为含佣金值

## 踩坑记录

_（运行后持续记录）_

## 外部参考

- [Loop Engineering (Addy Osmani)](https://addyosmani.com/blog/loop-engineering/)
- [Harness → Loop 演进 (BAAI)](https://hub.baai.ac.cn/view/55505)
- [Loop Engineering 实践 (Classmethod)](https://dev.classmethod.jp/articles/claude-loop-engineering-practice/)
- [Awesome Agent Loops](https://github.com/serenakeyitan/awesome-agent-loops)

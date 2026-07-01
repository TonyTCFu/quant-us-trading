# Loop 任务计划

> 最后更新: 2026-06-14 | Loop 工程搭建阶段

## 当前目标

建立美股量化模型的 Loop Engineering 多 Agent 协作体系，覆盖"数据→信号→风控→审查→报告"全流程。

## 阶段计划

### 阶段1: Harness 基础 ✅ (进行中)
- [x] 创建项目 Skill 文件 `.claude/skills/quant-trading/SKILL.md`
- [x] 创建 Loop 任务文档 `task_plan.md` / `findings.md` / `progress.md`
- [ ] 验证定时任务可正常运行

### 阶段2: 单 Agent /goal (待开始)
- [ ] 编写 `/goal` 覆盖每日完整流程
- [ ] 验证 `/goal` 能无人工介入跑通全流程
- [ ] 调试验收条件和暂停条件

### 阶段3: 多 Agent 拆分 (待开始)
- [ ] 定义 5 个 Agent: Data / Signal / Risk / Review / Report
- [ ] 实现 Maker-Checker 分离 (Review Agent 独立审查)
- [ ] 引入 worktree 隔离

### 阶段4: 外循环调度 (待开始)
- [ ] 配置 `/loop` 定时调度
- [ ] 建立人工收件箱 (BLOCKED 项推送)
- [ ] 积累一周运行数据, 评估调整

## 任务队列

| ID | 任务 | 优先级 | 状态 | 阻塞 |
|----|------|--------|------|------|
| H1 | 创建 Skill 文件 | P0 | done | - |
| H2 | 创建 Loop 任务文档 | P0 | done | - |
| H3 | 验证定时任务 | P1 | todo | - |
| G1 | 编写 /goal 命令 | P1 | todo | H1, H2 |
| G2 | 测试 /goal 单次运行 | P1 | todo | G1 |
| A1 | 定义 Data Agent | P2 | todo | G2 |
| A2 | 定义 Signal Agent | P2 | todo | G2 |
| A3 | 定义 Risk Agent | P2 | todo | G2 |
| A4 | 定义 Review Agent | P2 | todo | A1-A3 |
| A5 | 定义 Report Agent | P2 | todo | A1-A4 |
| A6 | 多 Agent 集成测试 | P2 | todo | A1-A5 |
| L1 | 配置 /loop 调度 | P3 | todo | A6 |
| L2 | 人工收件箱 | P3 | todo | L1 |
| L3 | 7 天运行评估 | P3 | todo | L1, L2 |

## 关键决策

| 决策 | 选择 | 理由 | 日期 |
|------|------|------|------|
| Loop 框架 | Claude Code /goal + /loop | 已在使用, 不需要额外工具 | 2026-06-14 |
| Agent 数量 | 5 个 | 数据/信号/风控/审查/报告 职责清晰分离 | 2026-06-14 |
| Maker-Checker | Review Agent 用不同模型 | 写代码的模型给自己打分太松 | 2026-06-14 |
| 隔离方式 | worktree | 多 Agent 并行避免文件冲突 | 2026-06-14 |

# LEARNING_PLAN

## 学习方式

本项目采用 **里程碑驱动**，不采用 Day 1 / Day 2 式教学。

原则：

- 用户有空就推进，不绑定固定日历节奏。
- 每次学习只围绕一个“能理解或能跑通的关键能力”。
- 达成一个里程碑后立即进入下一阶段。
- 如果用户中途改变目标、发现更高优先级问题，学习计划可以重排。
- 不追求一次学全，优先保证“理解 → 实验 → 复盘 → 再扩展”。

## 总目标

尽快搭出一支能在真实开发项目中协作的多 Agent 团队，并逐步减少人工复制粘贴和上下文转述。

## Milestone 0｜看懂团队怎么协作

完成标准：

- 能说清楚 5 个 Agent 分别负责什么
- 能说清楚它们在哪里交接
- 能区分共享状态、聊天记录、工具、MCP、服务器
- 能理解 Human Gate 为什么存在

## Milestone 1｜跑通最小三 Agent 闭环

团队：

- PM Agent
- Developer Agent
- QA Agent

完成标准：

一个任务能够完成：

PM 分配
→ Developer 执行
→ QA 检查
→ 失败则退回
→ 修复
→ QA 再测
→ PM 汇报

## Milestone 2｜让 Agent 读取同一个项目状态

完成标准：

- Agent 不依赖用户复制粘贴历史
- 能读取 PROJECT / CURRENT / TASKS / DECISIONS
- 每次工作后能更新共享状态

## Milestone 3｜给 Agent 接上“手和眼睛”

逐步增加：

- 文件读写
- 终端
- Git
- 测试
- 浏览器
- 数据库

完成标准：

Agent 能实际操作实验项目，而不只是输出建议。

## Milestone 4｜扩展成完整五 Agent 团队

加入：

- Product Agent
- Architect Agent

完成标准：

从需求到测试形成完整闭环，并且职责不混乱。

## Milestone 5｜Human Gate 与权限边界

完成标准：

系统能够区分：

- Agent 可自行处理
- PM 可自行决策
- 必须请求用户确认

## Milestone 6｜接入真实项目

选择一个真实项目，在受控范围内交给 Agent 团队推进。

## Milestone 7｜自动化与长期运行

最后再评估：

- MCP
- 云服务器
- 持久运行
- 自动触发
- 多项目协作
- 更复杂的 Agent 编排


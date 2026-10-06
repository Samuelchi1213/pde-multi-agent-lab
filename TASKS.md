# TASKS

## 状态说明

- TODO：尚未开始
- LEARNING：正在理解
- READY：理解完成，可进入实验
- DOING：正在实验
- DONE：完成

## Milestone 0｜看懂多 Agent 团队

| ID | 任务 | 状态 |
|---|---|---|
| L-001 | 理解 Agent 与普通聊天机器人的区别 | DONE |
| L-002 | 理解为什么需要多 Agent 分工 | DONE |
| L-003 | 理解共享项目状态 / Single Source of Truth | DONE |
| L-004 | 理解 Agent 之间的交接点 | LEARNING |
| L-005 | 理解 PM Agent 的调度作用 | LEARNING |
| L-006 | 理解 MCP、Tools 与 Agent 的关系 | LEARNING |
| L-007 | 理解本地运行、服务器运行的区别 | LEARNING |
| L-008 | 设计 Human Gate 人类介入规则 | TODO |
| L-009 | 明确五个 Agent 的职责与禁止事项 | TODO |
| L-010 | 画出第一版多 Agent 工作流程 | DOING |

## Milestone 1｜最小三 Agent 闭环

前置条件：L-004、L-008、L-010 完成后即可进入，不等待固定日期。

计划：
- PM Agent
- Developer Agent
- QA Agent

目标：
PM 分配 → Developer 执行 → QA 检查 → 失败退回 → 修复 → 再测 → PM 汇报。


## Milestone 1｜最小三智能体闭环

| ID | 任务 | 状态 |
|---|---|---|
| M1-001 | 建立最小任务状态对象 | DONE |
| M1-002 | 建立项目协调 / 开发 / 测试三个角色 | DONE |
| M1-003 | 跑通测试失败后的返工循环 | DONE |
| M1-004 | 本地运行原型并观察输出 | DONE |
| M1-005 | 将规则模拟替换为真实模型调用 | DOING |


## Milestone 2｜真实执行器自动编排

| ID | 任务 | 状态 |
|---|---|---|
| M2-001 | Python 自动调用 Codex CLI | DONE |
| M2-002 | 获取 Codex 退出码 | DONE |
| M2-003 | 获取并解析 Codex 结构化结果 | DONE |
| M2-004 | 动态生成任务输入 | TODO |
| M2-005 | 将 Codex 结果交给第二个真实智能体 | DONE |


## Milestone 3｜双智能体真实开发质量闭环

| ID | 任务 | 状态 |
|---|---|---|
| M3-001 | QA 能独立检验文件或测试证据 | TODO |
| M3-002 | 失败时自动退回开发智能体 | TODO |
| M3-003 | 最多两轮返工并停止升级 | TODO |
| M3-004 | 任务范围约束与密钥安全检查 | TODO |


## Milestone 3｜真实开发闭环

| ID | 任务 | 状态 |
|---|---|---|
| M3-001 | Codex 完成真实业务代码开发 | DONE |
| M3-002 | Codex 运行真实测试 | DONE |
| M3-003 | Python 编排器独立运行测试 | DONE |
| M3-004 | DeepSeek 读取真实代码与测试证据复核 | DONE |
| M3-005 | 首个真实开发闭环通过 | DONE |
| M3-006 | 验证自动返工路径 | DONE |
| M3-007 | 验证返工上限后触发人工决策关口 | DONE |

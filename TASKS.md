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

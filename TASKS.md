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


## Milestone 4｜统一编排器

| ID | 任务 | 状态 |
|---|---|---|
| M4-001 | 建立持久化任务对象 | DONE |
| M4-002 | 整合 Codex 开发执行器 | DONE |
| M4-003 | 整合 Python 独立验证器 | DONE |
| M4-004 | 整合 DeepSeek 独立复核 | DONE |
| M4-005 | 整合自动返工与人工决策关口 | DONE |
| M4-006 | 增加 token 预算与调用记录 | DONE |
| M4-007 | 本地运行统一编排器 | DONE |
| M4-008 | 验证运行状态文件 | DONE |
| M4-009 | 验证人工决策文件 | DONE |


## Milestone 5｜Web 控制台与智能协调层

| ID | 任务 | 状态 |
|---|---|---|
| M5-001 | 建立本地 Web 控制台 | DONE |
| M5-002 | 双击启动并自动打开浏览器 | DONE |
| M5-003 | 网页启动预设任务 | DONE |
| M5-004 | 网页查看任务状态/调用次数/token | DONE |
| M5-005 | 网页显示人工决策包 | DONE |
| M5-006 | 本地验证 Web 控制台 | DONE |
| M5-007 | 自然语言新建任务 | DONE |
| M5-008 | 项目协调智能体自动任务分级 | DONE |
| M5-009 | 动态组队与执行器路由 | TODO |
| M5-010 | 网页人工决策按钮 | TODO |


## Milestone 5.1｜自然语言多轮沟通

| ID | 任务 | 状态 |
|---|---|---|
| M5.1-001 | 项目协调智能体提出澄清问题 | DONE |
| M5.1-002 | 项目负责人自然语言补充 | DONE |
| M5.1-003 | 协调智能体更新同一任务草案 | DONE |
| M5.1-004 | 确认任务草案并本地持久化 | DONE |
| M5.1-005 | 本地验证多轮沟通 | TODO |


## Milestone 5.2｜动态组队与执行

| ID | 任务 | 状态 |
|---|---|---|
| M5.2-001 | 按任务草案动态选择角色 | DONE |
| M5.2-002 | 产品智能体生成规格 | DONE |
| M5.2-003 | 架构智能体生成技术方案 | DONE |
| M5.2-004 | Codex 在隔离工作区执行开发 | DONE |
| M5.2-005 | DeepSeek 独立测试复核 | DONE |
| M5.2-006 | Web 页面显示团队时间线 | DONE |
| M5.2-007 | 本地验证动态团队执行 | DONE |
| M5.2-008 | 接入真实目标项目/仓库选择 | TODO |


## Milestone 5.3｜成本控制与上下文压缩

| ID | 任务 | 状态 |
|---|---|---|
| M5.3-001 | 记录每个智能体单独 token 消耗 | DONE |
| M5.3-002 | 阶段摘要代替全量上下文传递 | DONE |
| M5.3-003 | 设置单任务 token 预算上限 | DONE |
| M5.3-004 | 低复杂度角色切换低成本模型 | TODO |
| M5.3-005 | Web 显示分角色成本 | DONE |


## Milestone 5.4｜执行器连接中心

| ID | 任务 | 状态 |
|---|---|---|
| M5.4-001 | DeepSeek Key 一次配置并自动识别 | DONE |
| M5.4-002 | Web 显示 Codex 连接状态 | DONE |
| M5.4-003 | Web 显示 DeepSeek 连接状态 | DONE |
| M5.4-004 | 检测豆包工作桌面客户端 | DONE |
| M5.4-005 | 预留豆包 Ark API 状态 | DONE |
| M5.4-006 | 本地验证一次配置与执行器中心 | TODO |

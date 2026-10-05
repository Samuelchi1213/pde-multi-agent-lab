# 执行通道矩阵 v1

## 当前测试结果

| 执行通道 | 真实模型 | 非交互调用 | 指定工作目录 | 读取仓库 | 修改文件 | 运行测试 | 权限控制 | 成本通道 | 当前判断 |
|---|---|---|---|---|---|---|---|---|---|
| Codex CLI | 是 | 是 | 是 | 是 | 待测 | 待测 | 是（已验证只读） | ChatGPT Plus 套餐额度 | 通过第一轮 |
| Claude Code + DeepSeek | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 | DeepSeek API | 待测 |
| DeepSeek Harness | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 | DeepSeek API | 待测 |
| 豆包 CLI | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 | 豆包会员额度 | 待测 |
| DeepSeek API | 是 | 是 | 不适用 | 否（裸 API） | 否（裸 API） | 否（裸 API） | 由上层程序控制 | DeepSeek API | 适合协调/结构化判断 |

## Codex CLI 第一轮记录

日期：2026-10-05

环境：
- Codex CLI：0.160.0
- 模型：gpt-5.6-sol
- 推理强度：medium
- 工作目录：pde-multi-agent-lab
- 沙箱：read-only

测试任务：
只读取当前目录和子目录，说明项目用途，并列出最重要的 5 个文件，不修改任何文件。

结果：
- 成功读取仓库
- 正确识别这是多智能体 AI 开发团队学习与实验仓库
- 正确概括了项目的角色分工、任务生命周期、共享状态和人工决策机制
- 正确识别 PROJECT.md、CURRENT.md、TASKS.md、team-workflow-v1.md、minimal_team_v1/main.py 等关键文件
- 明确说明未修改任何项目文件
- 非交互命令执行成功
- 只读权限生效

已发现问题：
- 本地存在一个无效 SKILL.md，Codex 会给出警告，但不影响本轮任务
- Windows CMD 对 ANSI 控制字符显示不友好，后续由 Python 捕获输出时可规避

下一步：
1. 测试 Codex CLI 的写文件能力
2. 测试是否能运行测试命令
3. 测试 Python 是否能自动调用 Codex CLI 并读取返回结果
4. 再与其他执行通道做同标准对比

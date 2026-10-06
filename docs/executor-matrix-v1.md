# 执行通道矩阵 v1

## 当前测试结果

| 执行通道 | 真实模型 | 非交互调用 | 指定工作目录 | 读取仓库 | 修改文件 | 运行测试 | 权限控制 | 成本通道 | 当前判断 |
|---|---|---|---|---|---|---|---|---|---|
| Codex CLI | 是 | 是 | 是 | 是 | 是 | 是 | 是（已验证只读与受控写入） | ChatGPT Plus 套餐额度 | 通过第三轮 |
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


## Codex CLI 第二轮记录｜受控写文件

日期：2026-10-05

测试命令：
使用 `codex exec --sandbox workspace-write`，要求仅在 experiments/codex_write_test 内创建 hello.txt。

结果：
- 成功创建 experiments/codex_write_test/hello.txt
- 文件内容为 Codex write test passed.
- 未修改、删除或创建其他文件
- Windows type 命令复查内容正确
- workspace-write 权限模式有效

结论：
Codex CLI 已具备“在指定工作区内受控修改文件”的能力。

下一步：
测试 Codex CLI 是否能创建一个小型代码文件、创建测试、运行测试并根据结果汇报。


## Codex CLI 第三轮记录｜代码 + 测试 + 实际执行

日期：2026-10-06

测试任务：
只在 experiments/codex_test_run 内：
- 创建 calculator.py
- 实现 add(a, b)
- 创建 test_calculator.py
- 测试 add(2, 3) == 5
- 实际运行测试
- 不修改其他文件

结果：
- 创建 calculator.py
- 创建 test_calculator.py
- 发现环境未安装 pytest
- 自动改用 Python 直接导入并执行 test_add()
- 实际命令成功执行
- 退出码为 0
- 测试通过
- 最终目录仅保留要求的两个文件

结论：
Codex CLI 已具备基础开发智能体闭环：
任务理解 → 写代码 → 写测试 → 运行测试 → 根据环境调整方案 → 判断结果 → 汇报。

下一步：
验证 Python 编排程序是否能自动调用 codex exec、捕获输出和退出码，并据此触发下一步任务。


## Codex CLI 第四轮记录｜Python 自动编排调用成功

日期：2026-10-06

测试目标：
验证上层 Python 编排器能否自动调用 Codex CLI、等待任务完成、获取退出码，并读取结构化结果。

实际结果：
- Python 成功启动 Codex CLI
- 工作目录正确指向 pde-multi-agent-lab
- Codex 成功读取真实仓库
- 进程退出码：0
- Codex 返回结构化 JSON
- Python 成功解析并打印：
  - status
  - summary
  - important_files
  - tests_run
  - next_step
- 全过程无需人工进入 Codex 交互界面

结论：
Codex CLI 已具备被上层编排程序自动触发和读取结果的能力。

这意味着 Codex 已经从“人工使用的开发工具”升级为“可被多智能体系统调用的真实执行器”。

下一步：
1. 把任务输入改成由上层任务对象动态生成
2. 测试开发任务的结构化交付格式
3. 让另一个智能体读取 Codex 的交付结果
4. 验证多执行器串联

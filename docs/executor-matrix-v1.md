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


## 2026-10-06｜真实 Codex → DeepSeek 自动交接成功

用户本地运行 experiments/real_handoff_v1/run.py 的截图证据：
- Codex CLI 完成项目现状分析并返回结构化 JSON
- Python 自动把该结果送往 DeepSeek API（deepseek-chat）
- DeepSeek 返回 pass、说明、问题和下一步建议
- DeepSeek 用量：prompt_tokens 833，completion_tokens 293，total_tokens 1126
- 脚本显示“交接链完成”并回到 CMD 提示符

能力边界：本轮仅为对交付摘要的独立语言模型复核，DeepSeek 没有独立读取仓库、运行测试或验证文件证据；不能因此声明真实开发 QA 已完成。后续应加入证据检查、失败返回和真正的自动返工。


## 2026-10-06｜Codex → DeepSeek 真实交接验证通过

用户本地运行 experiments/real_handoff_v1/run.py。

结果：
- Codex CLI 成功返回结构化交付 JSON。
- Python 自动将交付传给 DeepSeek API 的复核角色。
- DeepSeek 返回 status=pass，并给出问题和下一步建议。
- DeepSeek API 使用量：prompt_tokens=833，completion_tokens=293，total_tokens=1126。
- Python 输出“交接链完成”，流程正常结束。

边界：
- 这证明两个真实模型/通道可以自动交接。
- 还不代表真实代码开发协作已闭环。
- 第二个智能体尚未独立读取仓库、运行测试或验证真实产物。
- 尚未实现自动返工、人工暂停恢复、持久化任务状态和预算限制。

下一步：
在隔离测试目录中执行真实开发任务：Codex 修改代码并运行测试，第二个执行器独立验证测试证据；若失败，自动退回 Codex，最多返工 2 次。


## 2026-10-06｜首个真实开发闭环

任务：
实现学生返校状态判断函数。

Codex 实际完成：
- 创建 return_status.py
- 创建 test_return_status.py
- 覆盖提前、准时、晚1分钟、晚1天、未返校 5 个测试
- 实际执行测试并通过

Python 独立验证：
- 命令：python test_return_status.py
- 退出码：0
- 5 tests passed

DeepSeek 独立复核：
- 输入包含真实代码内容、Codex 交付信息、Python 独立测试证据
- 结论：pass
- token：prompt 1156 / completion 105 / total 1261

结论：
Codex CLI + Python 编排器 + DeepSeek API 已完成一次真实“开发 → 独立测试 → 独立复核 → 通过”的自动协作闭环。

未验证：
- rework 自动退回
- 连续失败计数
- Human Gate 自动暂停


## 2026-10-06｜自动返工闭环

场景：
初始需求未明确空字符串边界，隐藏测试要求空字符串也应视为“未返校”。

第1轮：
- Codex 完成实现并通过自身 9 个测试
- Python 隐藏测试退出码 1
- DeepSeek 识别真实失败并返回 rework
- Python 自动生成返工任务并重新调用 Codex

第2轮：
- Codex 修复空字符串处理
- 新增回归测试
- Codex 10 个单元测试通过
- Python 隐藏测试退出码 0
- DeepSeek 返回 pass

DeepSeek 第2轮 token：
- prompt 1395
- completion 76
- total 1471

结论：
已验证自动返工链路真实可用，不需要人工复制粘贴。


## 2026-10-06｜人工决策关口真实验证

场景：
代码任务本身可完成，但验收要求存在 approvals/approval_token.txt。
开发智能体只允许修改 workspace，无法创建该外部批准文件。

结果：
- Codex 功能实现成功
- Python 代码测试通过
- Python 外部条件验收失败，退出码 10
- DeepSeek 返回 need_human
- Python 生成完整人工决策包
- 系统停止自动推进

关键意义：
系统已能区分：
1. 可以通过开发返工解决的问题
2. 必须由项目负责人介入的权限/外部条件问题

因此多智能体系统已经具备真实的“自动推进 + 自动返工 + 人工升级”基础闭环。

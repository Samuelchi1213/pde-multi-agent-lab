# PDE 多智能体统一编排器 v1

这是把前期多个实验收拢后的第一版统一系统。

## 已整合能力

- 任务对象
- 状态持久化
- Codex CLI 开发执行
- Python 独立验证
- DeepSeek 独立复核
- 自动返工
- 最大返工次数
- 人工决策关口
- DeepSeek token 累计与预算上限
- 全过程历史记录

## 角色

### 项目协调器
目前由 Python 状态机承担，不做产品方向决策。

### 开发智能体
Codex CLI / gpt-5.6-sol。

### 测试复核智能体
DeepSeek API / deepseek-chat。

### 项目负责人
用户本人。系统进入“等待人工决策”后停止自动推进。

## 安全设计

- Codex 的 cwd 直接设置为任务 workspace。
- workspace-write 因此只围绕当前任务工作区运行。
- API Key 只从 DEEPSEEK_API_KEY 环境变量读取。
- runtime/、.env 等已加入 .gitignore。
- 不把密钥写进仓库。

## 状态文件

运行时自动生成：

`orchestrator_v1/runtime/<任务ID>.state.json`

里面会记录：
- 当前状态
- 返工次数
- Codex 调用次数
- DeepSeek 调用次数
- DeepSeek token 累计
- 每一次交接历史

如果触发人工决策，还会生成：

`orchestrator_v1/runtime/<任务ID>.human.json`

## 第一次运行

先拉取最新代码，并确认当前 CMD 已设置 DEEPSEEK_API_KEY：

```cmd
cd %USERPROFILE%\Desktop\pde-multi-agent-lab
git pull
python orchestrator_v1\main.py
```

默认执行示例任务“学生返校状态判断”。

## 成功标准

最终显示：

```text
=== 任务完成 ===
```

并且 runtime 下生成状态文件。

## v1 仍未包含

- 产品智能体
- 架构智能体
- 自动任务分级与动态组队
- Codex/豆包/DeepSeek Harness 多执行器路由
- 中断后从原状态恢复执行
- Web 群聊界面

这些会在统一编排器稳定后逐步加入。

# 真实项目协调智能体 v1

这一版开始使用真实大模型，不再用固定 Python 规则模拟智能判断。

## 当前模型

项目协调智能体：

- 模型：`gpt-5.6-luna`
- SDK：OpenAI Agents SDK

## 当前能力

输入一个真实任务后，模型会自主判断：

- 任务等级
- 为什么这样分级
- 需要哪些专业智能体参与
- 是否需要上报项目负责人
- 下一步该怎么走

## 当前还没有实现

- 其他专业智能体
- 自动调用其他智能体
- 文件读写
- Git
- 终端
- MCP
- 自动返工

这些会在后续逐步加入。

## 安装

在仓库根目录建议建立独立 Python 虚拟环境：

```cmd
python -m venv .venv
.venv\Scripts\activate
pip install openai-agents
```

## API 密钥

运行前必须设置：

```cmd
set "OPENAI_API_KEY=你的API密钥"
```

注意：不要把 API 密钥写进代码、README 或提交到 GitHub。

## 运行

```cmd
cd experiments\real_coordinator_v1
python main.py
```

## 建议测试任务

简单任务：

```text
把登录页面的“确定”按钮改成“登录”。
```

复杂任务：

```text
增加学生请假模块，学生提交请假，辅导员审批，并记录审批历史。
```

高风险任务：

```text
删除所有已经毕业学生的历史记录，减少数据库占用。
```

观察项目协调智能体是否会对三类任务作出不同判断。

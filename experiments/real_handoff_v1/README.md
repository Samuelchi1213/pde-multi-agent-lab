# 第一次真实多智能体交接 v1

## 目标

验证两个不同的真实模型执行通道能否完成一次自动交接。

流程：

Python 编排器
→ Codex CLI（开发/分析执行）
→ 结构化交付
→ DeepSeek API（测试/复核）
→ 结构化复核结果
→ Python 获取最终结论

## 真实智能体

### 智能体1：Codex
- 通道：Codex CLI
- 模型：gpt-5.6-sol
- 成本：ChatGPT Plus 套餐额度
- 权限：只读仓库

### 智能体2：DeepSeek
- 通道：DeepSeek API
- 模型：deepseek-chat
- 成本：DeepSeek API 余额
- 角色：测试 / 复核智能体

## 运行前

在 CMD 中设置 DeepSeek API Key：

```cmd
set "DEEPSEEK_API_KEY=你的key"
```

注意：不要把 key 写入代码或提交到 GitHub。

## 运行

```cmd
python experiments\real_handoff_v1\run.py
```

## 成功标准

终端里应该依次看到：
1. Codex 的结构化交付
2. DeepSeek 的复核结果
3. DeepSeek API 本次 token 使用量
4. “交接链完成”

## 重要意义

如果成功，就证明：
- 两个真实智能体可以使用不同模型、不同调用通道
- 第一个智能体的输出可以被第二个智能体自动读取
- Python 可以作为上层编排器
- 多智能体不需要共享同一个聊天上下文

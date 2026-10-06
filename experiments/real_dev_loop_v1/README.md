# 真实开发闭环 v1

这是第一个真正让两个异构智能体围绕代码产物协作的实验。

## 流程

项目任务
→ Codex 开发智能体修改隔离目录代码
→ Codex 自己运行测试
→ Python 编排器再次独立运行本地测试
→ Python 把真实代码内容 + 本地测试证据交给 DeepSeek
→ DeepSeek 独立复核
→ pass：结束
→ rework：自动生成返工任务交回 Codex
→ need_human：停止并上报项目负责人

自动返工最多 2 次。

## 为什么 Python 还要独立跑一次测试

不能只相信开发智能体说“我测试过了”。

因此证据分三层：
1. Codex 的交付说明
2. Python 编排器独立运行的测试退出码
3. DeepSeek 根据真实代码和测试证据进行复核

## 隔离范围

所有代码修改仅允许发生在：

experiments/real_dev_loop_v1/workspace/

不得修改仓库其他文件。

## 运行

确保：
- Codex CLI 已登录
- DEEPSEEK_API_KEY 已在当前 CMD 环境中设置

然后：

```cmd
python experiments\real_dev_loop_v1\run.py
```

## 成功标准

最终出现：

```text
=== 闭环完成 ===
Codex 开发 → Python 本地测试 → DeepSeek 独立复核 → 通过
```

如果 DeepSeek 判定失败，应该看到 Python 自动把返工任务交回 Codex，而不是人工复制粘贴。

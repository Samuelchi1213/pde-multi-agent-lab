# Python 自动调用 Codex CLI v1

## 目的

验证：

Python 编排程序
→ 自动启动 Codex CLI
→ Codex 读取真实仓库
→ Codex 按 JSON Schema 返回结构化结果
→ Python 读取结果
→ Python 根据退出码判断成功或失败

这一版只读，不修改项目文件。

## 运行

先确保 Codex CLI 已登录并可正常使用。

在仓库根目录：

```cmd
python experiments\python_calls_codex_v1\run.py
```

## 成功标准

终端应该看到：

- 退出码为 0
- 状态为 success
- 中文项目概括
- 重要文件列表
- 下一步建议

如果成功，就证明 Codex CLI 已经可以被上层 Python 编排器自动调用，而不需要用户手工进入 Codex。

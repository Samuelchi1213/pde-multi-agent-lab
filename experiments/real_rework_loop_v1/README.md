# 自动返工验证 v1

## 目的

专门验证异常路径：

Codex 开发
→ Python 隐藏测试失败
→ DeepSeek 判定 rework
→ Python 自动生成返工任务
→ Codex 修复
→ 再次测试
→ 通过

如果连续返工达到上限，则停止并上报项目负责人。

## 为什么使用隐藏测试

真实团队中，开发智能体不应该提前知道所有测试实现细节。
测试智能体需要有独立验收标准。

本实验增加一个隐藏规则：
- 空字符串 "" 也代表未形成有效考勤记录，应返回“未返校”。

这个规则不会在初始开发任务里明确告诉 Codex，而是在测试侧独立检查。

这不是模拟失败结果，而是真实的“需求遗漏 / 测试发现 / 自动返工”场景。

## 运行

```cmd
cd %USERPROFILE%\Desktop\pde-multi-agent-lab
git pull
python experiments\real_rework_loop_v1\run.py
```

确保当前终端已经设置 DEEPSEEK_API_KEY。

## 成功标准

理想情况：

第一轮：
- 隐藏测试失败
- DeepSeek 返回 rework
- Python 显示“自动生成返工任务”

第二轮：
- Codex 根据反馈修复
- 隐藏测试通过
- DeepSeek 返回 pass
- 输出“自动返工闭环验证成功”

如果第一轮直接通过，也说明 Codex 主动覆盖了该边界，但不能证明返工链路。因此需要根据实际结果决定是否追加更严格的独立测试。

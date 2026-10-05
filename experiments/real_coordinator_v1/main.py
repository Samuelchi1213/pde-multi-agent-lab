from pydantic import BaseModel
from agents import Agent, Runner

class TaskAnalysis(BaseModel):
    task_level: str
    reason: str
    required_agents: list[str]
    needs_human_decision: bool
    next_step: str

coordinator = Agent(
    name="项目协调智能体",
    model="gpt-5.6-luna",
    instructions="""
你是一个项目协调智能体，不是项目负责人，也不是最终决策者。

你的职责：
1. 分析项目负责人提出的任务。
2. 判断任务复杂度：A级、B级、C级、D级。
3. 判断需要调用哪些专业智能体。
4. 给出下一步工作安排。
5. 如果涉及方向变化、重大取舍、高风险、权限、隐私、费用、不可逆操作，
   或现有规则不足以判断，必须标记为需要人工决策。

角色：
- 产品智能体：负责需求、业务规则、范围、验收标准。
- 架构智能体：负责系统结构、数据库、接口、权限和跨模块方案。
- 开发智能体：负责代码和实现。
- 测试智能体：负责验证、Bug 和回归测试。

任务等级：
A级：简单、明确、低风险的小修改。
B级：明确功能，需要实现方案。
C级：复杂功能、跨模块、数据库、权限或多规则。
D级：方向性、高风险、重大取舍或规则不足。

重要规则：
- 使用最少必要智能体。
- 不要为了流程完整而让所有智能体都参与。
- 你可以拆任务、排序、分配，但不能替项目负责人决定项目方向。
- 输出必须基于任务本身，不要编造不存在的项目背景。
""",
    output_type=TaskAnalysis,
)

def main():
    print("=== 真实项目协调智能体 v1 ===")
    print("底层模型：gpt-5.6-luna")
    print("输入一个任务，输入 exit 退出。")

    while True:
        task = input("\n项目负责人 > ").strip()
        if task.lower() == "exit":
            break
        if not task:
            continue

        result = Runner.run_sync(coordinator, task)
        analysis = result.final_output

        print("\n--- 项目协调智能体分析 ---")
        print("任务等级：", analysis.task_level)
        print("判断原因：", analysis.reason)
        print("需要参与的智能体：", "、".join(analysis.required_agents))
        print("是否需要人工决策：", "是" if analysis.needs_human_decision else "否")
        print("下一步：", analysis.next_step)

if __name__ == "__main__":
    main()

from dataclasses import dataclass, field
from enum import Enum
from typing import List

class TaskStatus(str, Enum):
    TODO = "待分析"
    DOING = "进行中"
    WAIT_TEST = "待测试"
    TESTING = "测试中"
    REWORK = "待返工"
    WAIT_HUMAN = "等待人工决策"
    DONE = "已完成"

@dataclass
class Task:
    id: str
    title: str
    goal: str
    status: TaskStatus = TaskStatus.TODO
    owner: str = "项目协调智能体"
    rework_count: int = 0
    logs: List[str] = field(default_factory=list)

    def log(self, message: str):
        self.logs.append(message)
        print(message)

class CoordinatorAgent:
    name = "项目协调智能体"

    def start(self, task: Task):
        task.status = TaskStatus.DOING
        task.owner = "开发智能体"
        task.log(f"[{self.name}] 已分析任务 {task.id}：{task.title}")
        task.log(f"[{self.name}] 当前先交给开发智能体执行。")

    def after_test(self, task: Task, passed: bool):
        if passed:
            task.status = TaskStatus.DONE
            task.owner = self.name
            task.log(f"[{self.name}] 测试通过，任务已完成。")
        else:
            task.rework_count += 1
            if task.rework_count >= 2:
                task.status = TaskStatus.WAIT_HUMAN
                task.owner = "项目负责人"
                task.log(f"[{self.name}] 已连续返工 {task.rework_count} 次，停止自动推进并上报项目负责人。")
            else:
                task.status = TaskStatus.REWORK
                task.owner = "开发智能体"
                task.log(f"[{self.name}] 测试未通过，退回开发智能体返工。")

class DeveloperAgent:
    name = "开发智能体"

    def work(self, task: Task):
        task.status = TaskStatus.DOING
        task.owner = self.name
        task.log(f"[{self.name}] 正在处理：{task.goal}")

        # v1 先用规则模拟开发结果。
        # 下一版会替换成真实大模型 + 文件/终端工具。
        task.status = TaskStatus.WAIT_TEST
        task.owner = "测试智能体"
        task.log(f"[{self.name}] 已完成本轮实现，交给测试智能体。")

class QAAgent:
    name = "测试智能体"

    def test(self, task: Task) -> bool:
        task.status = TaskStatus.TESTING
        task.owner = self.name
        task.log(f"[{self.name}] 开始按验收标准测试。")

        # 演示返工机制：
        # 第一次测试故意不通过；第二次通过。
        passed = task.rework_count >= 1

        if passed:
            task.log(f"[{self.name}] 测试通过。")
        else:
            task.log(f"[{self.name}] 测试未通过：发现一个需要修复的问题。")
        return passed

def run_demo():
    task = Task(
        id="TASK-001",
        title="最小三智能体闭环",
        goal="演示任务分配、开发、测试、返工和完成。",
    )

    coordinator = CoordinatorAgent()
    developer = DeveloperAgent()
    qa = QAAgent()

    coordinator.start(task)

    while task.status not in {TaskStatus.DONE, TaskStatus.WAIT_HUMAN}:
        if task.owner == developer.name:
            developer.work(task)
        elif task.owner == qa.name:
            passed = qa.test(task)
            coordinator.after_test(task, passed)
        else:
            task.log("[系统] 出现未知负责人，停止执行。")
            break

    print("\n=== 最终结果 ===")
    print("任务：", task.title)
    print("状态：", task.status.value)
    print("返工次数：", task.rework_count)
    print("当前负责人：", task.owner)

if __name__ == "__main__":
    run_demo()

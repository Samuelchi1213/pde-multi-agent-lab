from pathlib import Path
import argparse

from task import TaskState
from codex_executor import CodexExecutor
from deepseek_reviewer import DeepSeekReviewer
from orchestrator import Orchestrator


ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description="PDE 多智能体统一编排器 v1")
    parser.add_argument(
        "--task",
        default="orchestrator_v1/tasks/sample_return_status.json",
        help="任务 JSON 文件路径（相对于仓库根目录）",
    )
    args = parser.parse_args()

    task_file = ROOT / args.task
    task = TaskState.from_task_file(task_file)

    codex = CodexExecutor(
        model="gpt-5.6-sol",
        schema_path=ROOT / "orchestrator_v1/schemas/codex_delivery_schema.json",
    )
    reviewer = DeepSeekReviewer(model="deepseek-chat")

    print("=== PDE 多智能体统一编排器 v1 ===")
    print("任务：", task.title)
    print("工作区：", task.workspace)
    print("最大自动返工：", task.max_rework)
    print("DeepSeek token 上限：", task.max_deepseek_tokens)

    orchestrator = Orchestrator(ROOT, task, codex, reviewer)
    raise SystemExit(orchestrator.run())


if __name__ == "__main__":
    main()

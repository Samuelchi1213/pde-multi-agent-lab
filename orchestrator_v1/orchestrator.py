import json
import subprocess
from pathlib import Path

from task import TaskStatus


class Orchestrator:
    def __init__(self, root: Path, task, codex, reviewer):
        self.root = root
        self.task = task
        self.codex = codex
        self.reviewer = reviewer

        self.workspace = (root / task.workspace).resolve()
        self.runtime = root / "orchestrator_v1" / "runtime"
        self.state_file = self.runtime / f"{task.id}.state.json"
        self.delivery_file = self.runtime / f"{task.id}.codex.json"
        self.human_file = self.runtime / f"{task.id}.human.json"

    def save(self):
        self.task.save(self.state_file)

    def read_workspace(self):
        files = {}
        if not self.workspace.exists():
            return files
        for path in self.workspace.rglob("*"):
            if path.is_file() and path.suffix.lower() in {".py", ".md", ".txt", ".json"}:
                files[path.relative_to(self.workspace).as_posix()] = path.read_text(
                    encoding="utf-8", errors="replace"
                )
        return files

    def run_verifier(self):
        verifier = (self.root / self.task.verifier).resolve()
        completed = subprocess.run(
            ["python", str(verifier), str(self.workspace)],
            cwd=self.root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            check=False,
        )
        return {
            "command": f"python {self.task.verifier} {self.task.workspace}",
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }

    def development_prompt(self, feedback=None):
        criteria = "\n".join(f"- {x}" for x in self.task.acceptance_criteria)
        base = f"""
你是开发智能体。当前工作目录已经被限制为本任务 workspace。

任务编号：{self.task.id}
任务：{self.task.title}
目标：{self.task.goal}

验收标准：
{criteria}

要求：
- 只能修改当前工作目录
- 使用必要的最小实现
- 自己创建测试并实际运行
- 不要访问或修改工作目录之外的文件
- 完成后按 JSON Schema 返回结构化交付
"""
        if feedback:
            base += "\n这是上一轮独立测试/复核反馈，请逐项修复：\n"
            base += json.dumps(feedback, ensure_ascii=False, indent=2)
        return base

    def human_gate(self, reason, requested_action, issues, verifier):
        self.task.status = TaskStatus.WAIT_HUMAN.value
        pack = {
            "task_id": self.task.id,
            "title": self.task.title,
            "status": self.task.status,
            "reason": reason,
            "issues": issues,
            "requested_action": requested_action,
            "rework_count": self.task.rework_count,
            "deepseek_tokens": self.task.deepseek_tokens,
            "verifier_evidence": verifier,
            "options": [
                "项目负责人提供缺失信息/资源后恢复任务",
                "项目负责人调整验收标准或任务范围",
                "项目负责人修改权限边界",
                "项目负责人终止任务",
            ]
        }
        self.human_file.parent.mkdir(parents=True, exist_ok=True)
        self.human_file.write_text(
            json.dumps(pack, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        self.task.record("项目协调器", "触发人工决策关口", pack)
        self.save()

        print("\n=== 等待人工决策 ===")
        print(json.dumps(pack, ensure_ascii=False, indent=2))
        print("\n系统已停止自动推进。")
        return 2

    def run(self):
        feedback = None

        while True:
            if self.task.deepseek_tokens >= self.task.max_deepseek_tokens:
                return self.human_gate(
                    "DeepSeek token 预算已达到上限",
                    "请项目负责人决定是否增加预算或停止任务",
                    ["预算上限触发"],
                    {}
                )

            self.task.status = TaskStatus.DEVELOPING.value
            self.task.codex_calls += 1
            self.task.record("项目协调器", "调用 Codex", {
                "round": self.task.rework_count + 1
            })
            self.save()

            print(f"\n===== 开发轮次 {self.task.rework_count + 1} =====")
            delivery = self.codex.run(
                self.workspace,
                self.development_prompt(feedback),
                self.delivery_file
            )
            self.task.record("Codex", "完成开发交付", delivery)
            self.save()

            self.task.status = TaskStatus.VERIFYING.value
            self.save()
            verifier = self.run_verifier()
            self.task.record("Python验证器", "独立验证", verifier)
            self.save()

            print("\n=== 独立验证 ===")
            print("退出码：", verifier["returncode"])
            if verifier["stdout"].strip():
                print(verifier["stdout"].strip())
            if verifier["stderr"].strip():
                print(verifier["stderr"].strip())

            self.task.status = TaskStatus.REVIEWING.value
            files = self.read_workspace()
            review, usage = self.reviewer.review(
                self.task, delivery, files, verifier
            )

            used = int(usage.get("total_tokens", 0) or 0)
            self.task.deepseek_calls += 1
            self.task.deepseek_tokens += used
            self.task.record("DeepSeek", "独立复核", {
                "review": review,
                "usage": usage,
            })
            self.save()

            print("\n=== DeepSeek 复核 ===")
            print("状态：", review["status"])
            print("说明：", review["summary"])
            print("累计 DeepSeek tokens：", self.task.deepseek_tokens)

            if review["status"] == "pass":
                self.task.status = TaskStatus.DONE.value
                self.task.last_summary = review["summary"]
                self.task.record("项目协调器", "任务完成")
                self.save()
                print("\n=== 任务完成 ===")
                return 0

            if review["status"] == "need_human":
                return self.human_gate(
                    review.get("human_reason") or review["summary"],
                    review.get("requested_action") or "请项目负责人决定下一步",
                    review.get("issues", []),
                    verifier,
                )

            self.task.rework_count += 1
            if self.task.rework_count > self.task.max_rework:
                return self.human_gate(
                    "自动返工次数达到上限",
                    "请项目负责人决定继续返工、调整需求或终止任务",
                    review.get("issues", []),
                    verifier,
                )

            self.task.status = TaskStatus.REWORK.value
            feedback = {
                "summary": review["summary"],
                "issues": review.get("issues", []),
                "instruction": review.get("rework_instruction", ""),
                "verifier_result": verifier,
            }
            self.task.record("项目协调器", "生成返工任务", feedback)
            self.save()
            print("\n>>> 自动返工：已将复核意见交回 Codex。")

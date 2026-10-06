from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any
import json
from datetime import datetime


class TaskStatus(str, Enum):
    TODO = "待分析"
    DEVELOPING = "开发中"
    VERIFYING = "独立验证中"
    REVIEWING = "复核中"
    REWORK = "待返工"
    WAIT_HUMAN = "等待人工决策"
    DONE = "已完成"
    FAILED = "失败"


@dataclass
class TaskState:
    id: str
    title: str
    goal: str
    workspace: str
    acceptance_criteria: list[str]
    verifier: str
    max_rework: int = 2
    max_deepseek_tokens: int = 10000
    status: str = TaskStatus.TODO.value
    rework_count: int = 0
    codex_calls: int = 0
    deepseek_calls: int = 0
    deepseek_tokens: int = 0
    last_summary: str = ""
    history: list[dict[str, Any]] = field(default_factory=list)

    def record(self, actor: str, action: str, details: Any = None):
        self.history.append({
            "time": datetime.now().isoformat(timespec="seconds"),
            "actor": actor,
            "action": action,
            "details": details,
        })

    def save(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

    @classmethod
    def from_task_file(cls, path: Path):
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(**data)

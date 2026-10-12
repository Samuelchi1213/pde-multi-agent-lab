"""Interactive recovery flow demo (isolated, entirely in memory).

This is a UX demo, NOT an implementation of production run_from_stage.
Only built-in synthetic task fixtures are possible. It neither imports a
provider/real project executor nor writes to disk. The demo is reset when the
console process restarts, not persisted as a real task.
"""
from __future__ import annotations

import hashlib
import threading
from typing import Any

from mock_approval_contract import MockApprovalBook, describe_mock_approval_scope
from mock_stage_runner import FakeStageSession
from offline_stage_recovery import simulate_stage_recovery

_TASK_ID = "DRAFT-DEMO-ONLY"
_STAGES = ("product.spec", "architecture.plan", "developer.first", "qa.review.0")
_STAGE_LABELS = {
    "product.spec": "产品需求（已完成）",
    "architecture.plan": "架构设计（已完成）",
    "developer.first": "模拟开发阶段",
    "qa.review.0": "模拟测试阶段",
}
_REQUESTER = "demo_requester"
_APPROVER = "demo_reviewer"
_COSTS = {"developer.first": 3, "qa.review.0": 2}
_ALLOWED = frozenset({
    "create", "interrupt", "inspect", "approve", "continue",
    "lose_receipt", "reset",
})


class RecoveryFlowDemo:
    """Session-bound demo state shared between browser refreshes and HTTP requests."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._new()

    def _new(self) -> None:
        self._state = "idle"
        self._session: FakeStageSession | None = None
        self._book = MockApprovalBook()
        self._approved_receipt: str | None = None
        self._approved_scope: dict[str, Any] | None = None
        self._history: list[str] = []
        self._review_plan: dict[str, Any] | None = None
        self._counter = 0

    def _log(self, line: str) -> None:
        self._history.append(line)
        self._history = self._history[-40:]

    def _next_step(self) -> str | None:
        if self._session is None:
            return None
        return self._session.state()["next_step"]

    def _synthetic_preview(self) -> dict[str, Any]:
        steps = [
            {"step_id": name, "status": "verified" if i < 2 else "not_started"}
            for i, name in enumerate(_STAGES)
        ]
        return {
            "ok": True, "draft_id": _TASK_ID,
            "task_status": "执行中断（需要检查）",
            "mode": "read_only_no_model_calls",
            "resume_authorized": False, "can_resume_now": False,
            "steps": steps,
            "verified_steps": list(_STAGES[:2]),
            "not_started_steps": list(_STAGES[2:]),
            "blocked_steps": [], "blockers": [],
        }

    def _scope(self, step: str) -> dict[str, Any]:
        # Evidence and identities here are SYNTHETIC; never read real task files.
        seed = f"synthetic-demo-{_TASK_ID}-{step}".encode()
        fake_sha = hashlib.sha256(seed).hexdigest()
        preflight = {
            "ok": True, "mode": "read_only_preflight",
            "status": "review_candidate", "draft_id": _TASK_ID,
            "next_step": step, "requires_owner_approval": True,
            "can_resume_now": False, "resume_authorized": False,
            "blockers": [],
            "checksums": {
                "state_sha256": fake_sha,
                "draft_sha256": hashlib.sha256(seed + b"draft").hexdigest(),
                "ledger_sha256": hashlib.sha256(seed + b"ledger").hexdigest(),
            },
        }
        fake_runner = {
            "mode": "in_memory_fake_only", "status": "ready_fake_only",
            "can_resume_now": False, "resume_authorized": False,
        }
        return describe_mock_approval_scope(preflight, fake_runner)

    @staticmethod
    def _request_sha(step: str) -> str:
        return hashlib.sha256(("fake-demo-request:" + step).encode()).hexdigest()

    def _snapshot(self) -> dict[str, Any]:
        run = self._session.state() if self._session else {}
        next_step = run.get("next_step")
        events = [
            {"id": stage, "name": _STAGE_LABELS[stage],
             "status": "verified" if i < 2 else (
                 "completed_mock" if stage in run.get("finished_steps", [])
                 else "pending_mock")}
            for i, stage in enumerate(_STAGES)
        ]
        return {
            "ok": True,
            "mode": "isolated_in_memory_demo",
            "state": self._state,
            "task_id": _TASK_ID if self._state != "idle" else None,
            "steps": events if self._state != "idle" else [],
            "next_step": next_step,
            "next_label": _STAGE_LABELS.get(next_step) if next_step else None,
            "approved_current": bool(self._approved_receipt),
            "mock_calls": run.get("mock_calls", 0),
            "mock_credits_spent": run.get("credits_spent_fake", 0),
            "mock_credits_total": 5,
            "timeline": list(self._history),
            "can_resume_now": False,
            "resume_authorized": False,
            "external_model_calls": 0,
            "real_project_writes": 0,
            "note": "只使用内存中的虚构任务；刷新页面可继续查看，控制台重启后演练会重置。"
        }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self._snapshot()

    def act(self, action: str) -> dict[str, Any]:
        if action not in _ALLOWED:
            raise ValueError("不支持的模拟操作")
        with self._lock:
            if action == "reset":
                self._new()
                self._log("已清除虚构演练状态；没有触及真实任务。")
                return self._snapshot()
            if action == "create":
                if self._state != "idle":
                    raise ValueError("已有演练；需要重新开始时请先重置")
                self._state = "running_fake"
                self._log("创建虚构团队任务，模拟产品与架构阶段已完成并有可信回执。")
                self._log("下一模拟阶段：开发；还没有调用模型。")
                return self._snapshot()
            if action == "interrupt":
                if self._state != "running_fake":
                    raise ValueError("只能在模拟任务运行阶段注入中断")
                self._state = "interrupted"
                self._log("模拟进程中断：保留已完成的产品、架构阶段，冻结下一步。")
                return self._snapshot()
            if action == "inspect":
                if self._state != "interrupted":
                    raise ValueError("只能在中断之后检查模拟恢复位置")
                plan = simulate_stage_recovery(self._synthetic_preview())
                if plan["status"] != "simulated":
                    raise ValueError("阶段证据核验未通过，禁止进入模拟审批")
                self._review_plan = plan
                self._session = FakeStageSession(plan, credit_limit=5)
                self._state = "awaiting_fake_approval"
                self._log("恢复核验：产品、架构阶段复用；开发阶段尚未开始。")
                self._log("仅获得模拟审批资格，没有任何真实模型执行权限。")
                return self._snapshot()
            if action == "approve":
                if self._state != "awaiting_fake_approval":
                    raise ValueError("当前阶段尚未准备模拟审批")
                step = self._next_step()
                if not step:
                    raise ValueError("没有待审批阶段")
                scope = self._scope(step)
                if scope["status"] != "mock_review_candidate":
                    raise ValueError("模拟审批范围不完整")
                self._counter += 1
                receipt = self._book.issue(
                    scope, requester=_REQUESTER, approver=_APPROVER,
                    request_sha256=self._request_sha(step),
                    credit_limit=_COSTS[step],
                    issued_tick=self._counter * 10,
                    expires_tick=self._counter * 10 + 100,
                    approved=True,
                )
                self._approved_receipt = receipt["mock_receipt_id"]
                self._approved_scope = scope
                self._state = "approved_fake"
                self._log(f"模拟负责人已批准 {_STAGE_LABELS[step]}（非真实费用授权）。")
                return self._snapshot()
            if action in ("continue", "lose_receipt"):
                if self._state != "approved_fake" or not self._approved_receipt or not self._approved_scope:
                    raise ValueError("当前步骤尚未模拟批准，不允许继续")
                step = self._next_step()
                if not step:
                    raise ValueError("没有待执行的模拟步骤")
                decision = self._book.consume(
                    self._approved_receipt, current_scope=self._scope(step),
                    request_sha256=self._request_sha(step),
                    cost_credits=_COSTS[step],
                    now_tick=self._counter * 10 + 1,
                )
                self._approved_receipt = None
                self._approved_scope = None
                if decision["status"] != "mock_approved_for_fake_step_only":
                    self._state = "frozen"
                    self._log("模拟审批消耗失败，状态冻结，禁止重试。")
                    return self._snapshot()
                state = self._session.advance(
                    step_id=step, approved=True,
                    lose_response=(action == "lose_receipt"),
                )
                if state["status"] == "uncertain_frozen":
                    self._state = "frozen"
                    self._log(f"{_STAGE_LABELS[step]}模拟回执丢失：冻结，不能再次模拟执行。")
                elif state["status"] == "finished_fake_only":
                    self._state = "completed_mock"
                    self._log("模拟开发、测试全部完成；生成了虚构完成状态，未发布任何项目。")
                elif state["status"] == "awaiting_fake_approval":
                    self._state = "awaiting_fake_approval"
                    self._log(f"{_STAGE_LABELS[step]}完成（仅模拟），请审批下一阶段。")
                else:
                    self._state = "frozen"
                    self._log("异常状态：为避免重复执行，演练已冻结。")
                return self._snapshot()
            raise ValueError("未知模拟操作")

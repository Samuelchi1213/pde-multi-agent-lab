"""B4-B1: in-memory fake stage executor. ZERO provider or project interfaces.

This module never imports Codex, DeepSeek, a project runner, network code, or
file writers. Its fake credit units are NOT money or billable tokens.
It is deliberately impossible to use it to execute a real team task.
"""
from __future__ import annotations

import threading
from typing import Any

from offline_stage_recovery import _stage_order


def _cost(step_id: str) -> int:
    """Artificial test credits, unrelated to real billing."""
    return 3 if step_id.startswith("developer.") or step_id == "manual.rework.1" else 2


def describe_mock_runner(plan: dict[str, Any] | None) -> dict[str, Any]:
    """Return eligibility for a fake run, never permission for a real run."""
    result = {
        "mode": "in_memory_fake_only",
        "status": "blocked",
        "stage_count": 0,
        "blockers": [],
        "can_resume_now": False,
        "resume_authorized": False,
        "mock_calls": 0,
        "external_model_calls": 0,
        "real_project_writes": 0,
    }
    if not isinstance(plan, dict) or plan.get("status") != "simulated":
        result["blockers"].append("离线阶段演练未通过，假执行器不得开始")
        return result
    if (plan.get("mode") != "offline_stage_simulation_only" or
            plan.get("resume_authorized") is not False or
            plan.get("can_resume_now") is not False or plan.get("blockers") != []):
        result["blockers"].append("原阶段计划不是可信的无授权离线模拟")
        return result
    done = plan.get("reused_steps")
    pending = plan.get("simulated_steps")
    if (not isinstance(done, list) or not done or
            not isinstance(pending, list) or not pending or
            plan.get("start_step") != pending[0]):
        result["blockers"].append("阶段清单或模拟起点无效")
        return result
    ordered = done + pending
    if any(not isinstance(s, str) for s in ordered) or len(set(ordered)) != len(ordered):
        result["blockers"].append("存在非法或重复步骤")
        return result
    try:
        ordinals = [_stage_order(s) for s in ordered]
    except ValueError:
        result["blockers"].append("存在未知阶段编号")
        return result
    if ordinals != sorted(ordinals):
        result["blockers"].append("阶段顺序不一致")
        return result
    result["status"] = "ready_fake_only"
    result["stage_count"] = len(pending)
    return result


class FakeStageSession:
    """Thread-safe in-memory model of a future stage runner. Never a real runner.

    One explicit fake approval per stage. A simulated lost response is
    permanently uncertain for this session; retries cannot erase the claim.
    """

    def __init__(self, plan: dict[str, Any], *, credit_limit: int = 10):
        review = describe_mock_runner(plan)
        if review["status"] != "ready_fake_only":
            raise ValueError("离线恢复计划被阻断，不能创建假执行会话")
        if type(credit_limit) is not int or credit_limit < 0 or credit_limit > 1000:
            raise ValueError("测试额度必须在 0 至 1000 之间")
        self._steps = tuple(plan["simulated_steps"])
        self._reused = tuple(plan["reused_steps"])
        self._credits = credit_limit
        self._spent = 0
        self._cursor = 0
        self._claims: dict[str, str] = {}
        self._history = [{"step_id": sid, "event": "reuse_verified_mock_evidence"} for sid in self._reused]
        self._blocked = False
        self._lock = threading.Lock()

    def state(self) -> dict[str, Any]:
        with self._lock:
            return self._state_locked()

    def _state_locked(self) -> dict[str, Any]:
        if self._blocked:
            status = "uncertain_frozen"
        elif self._cursor == len(self._steps):
            status = "finished_fake_only"
        else:
            status = "awaiting_fake_approval"
        return {
            "mode": "in_memory_fake_only",
            "status": status,
            "next_step": self._steps[self._cursor] if self._cursor < len(self._steps) else None,
            "reused_steps": list(self._reused),
            "finished_steps": [sid for sid, claim in self._claims.items() if claim == "completed"],
            "credits_spent_fake": self._spent,
            "credits_remaining_fake": self._credits - self._spent,
            "mock_calls": len(self._claims),
            "events": [dict(event) for event in self._history],
            "can_resume_now": False,
            "resume_authorized": False,
            "external_model_calls": 0,
            "real_project_writes": 0,
        }

    def advance(self, *, step_id: str, approved: bool = False,
                lose_response: bool = False) -> dict[str, Any]:
        """Only simulates one stage; never accepts an executor callback."""
        with self._lock:
            if self._blocked:
                self._history.append({"step_id": str(step_id), "event": "blocked_uncertain"})
                return self._state_locked()
            if self._cursor == len(self._steps):
                return self._state_locked()
            expected = self._steps[self._cursor]
            if step_id != expected:
                self._history.append({"step_id": str(step_id), "event": "blocked_wrong_stage"})
                return self._state_locked()
            if step_id in self._claims:
                self._blocked = True
                self._history.append({"step_id": step_id, "event": "blocked_duplicate_claim"})
                return self._state_locked()
            if approved is not True:
                self._history.append({"step_id": step_id, "event": "awaiting_explicit_fake_approval"})
                return self._state_locked()
            cost = _cost(step_id)
            if self._spent + cost > self._credits:
                self._history.append({"step_id": step_id, "event": "blocked_fake_budget"})
                return self._state_locked()
            # A fake claim is placed before the fake provider result arrives.
            self._claims[step_id] = "in_flight"
            self._spent += cost
            self._history.append({"step_id": step_id, "event": "mock_claim_reserved"})
            if lose_response:
                self._claims[step_id] = "uncertain"
                self._blocked = True
                self._history.append({"step_id": step_id, "event": "mock_result_uncertain"})
                return self._state_locked()
            self._claims[step_id] = "completed"
            self._history.append({"step_id": step_id, "event": "fake_receipt_saved_in_memory"})
            self._cursor += 1
            return self._state_locked()

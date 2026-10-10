"""B4-B2: mock-only single-stage approval contract; no real authorization.

The in-memory ledger is only a specification/test fixture. It MUST NOT be
connected to Codex, DeepSeek, run_from_stage or real project writes.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from typing import Any

HEX64 = re.compile(r"^[0-9a-f]{64}$")
ACTOR = re.compile(r"^[A-Za-z0-9_-]{2,70}$")


def describe_mock_approval_scope(preflight: dict[str, Any] | None,
                                 fake_plan: dict[str, Any] | None) -> dict[str, Any]:
    result = {
        "mode": "mock_approval_scope_read_only",
        "status": "blocked",
        "draft_id": None,
        "step_id": None,
        "evidence_binding": None,
        "blockers": [],
        "can_resume_now": False,
        "resume_authorized": False,
        "external_model_calls": 0,
        "real_project_writes": 0,
    }

    def stop(reason: str) -> dict[str, Any]:
        result["blockers"].append(reason)
        return result

    if not isinstance(preflight, dict) or preflight.get("ok") is not True:
        return stop("缺少可信的原任务只读资格审查")
    if (preflight.get("mode") != "read_only_preflight"
            or preflight.get("status") != "review_candidate"
            or preflight.get("can_resume_now") is not False
            or preflight.get("resume_authorized") is not False
            or preflight.get("requires_owner_approval") is not True
            or preflight.get("blockers") != []):
        return stop("原任务证据尚未通过恢复资格审查")
    if (not isinstance(fake_plan, dict)
            or fake_plan.get("mode") != "in_memory_fake_only"
            or fake_plan.get("status") != "ready_fake_only"
            or fake_plan.get("resume_authorized") is not False
            or fake_plan.get("can_resume_now") is not False):
        return stop("假执行阶段资格未通过，不能进入模拟审批")
    draft = preflight.get("draft_id")
    stage = preflight.get("next_step")
    if not isinstance(draft, str) or not draft.startswith("DRAFT-"):
        return stop("只有普通 DRAFT 任务才可申请假审批")
    if not isinstance(stage, str) or len(stage) > 80 or not stage:
        return stop("缺少明确的单步骤目标")
    hashes = preflight.get("checksums")
    if not isinstance(hashes, dict):
        return stop("缺少完整的历史证据版本")
    keys = ("state_sha256", "draft_sha256", "ledger_sha256")
    if not all(isinstance(hashes.get(key), str) and HEX64.fullmatch(hashes[key]) for key in keys):
        return stop("原始草案、状态和检查点 SHA256 不完整")
    payload = {"draft_id": draft, "step_id": stage, "evidence": {k: hashes[k] for k in keys}}
    binding = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    result.update(status="mock_review_candidate", draft_id=draft,
                  step_id=stage, evidence_binding=binding)
    return result


class MockApprovalBook:
    """One-use in-memory mock approval. Not an API key, permit or payment token."""

    def __init__(self):
        self._lock = threading.Lock()
        self._records: dict[str, dict[str, Any]] = {}
        self._by_scope: set[tuple[str, str]] = set()

    def issue(self, scope: dict[str, Any], *, requester: str, approver: str,
              request_sha256: str, credit_limit: int,
              issued_tick: int, expires_tick: int, approved: bool) -> dict[str, Any]:
        if (not isinstance(scope, dict) or scope.get("mode") != "mock_approval_scope_read_only"
                or scope.get("status") != "mock_review_candidate"
                or scope.get("resume_authorized") is not False
                or scope.get("can_resume_now") is not False
                or scope.get("blockers") != []):
            raise ValueError("恢复资格不完整；拒绝发行模拟审批")
        draft, stage = scope.get("draft_id"), scope.get("step_id")
        binding = scope.get("evidence_binding")
        if (not isinstance(draft, str) or not draft.startswith("DRAFT-")
                or not isinstance(stage, str) or not stage
                or not isinstance(binding, str) or not HEX64.fullmatch(binding)):
            raise ValueError("模拟审批必须绑定可信任务、步骤与证据")
        if (not isinstance(requester, str) or not ACTOR.fullmatch(requester)
                or not isinstance(approver, str) or not ACTOR.fullmatch(approver)
                or requester == approver or approved is not True):
            raise ValueError("模拟审批须由不同的审批人明确确认")
        if not isinstance(request_sha256, str) or not HEX64.fullmatch(request_sha256):
            raise ValueError("请求指纹非法")
        if type(credit_limit) is not int or not 1 <= credit_limit <= 1000:
            raise ValueError("模拟 credits 不合法")
        if (type(issued_tick) is not int or type(expires_tick) is not int
                or issued_tick < 0 or not issued_tick < expires_tick <= issued_tick + 3600):
            raise ValueError("模拟审批有效期必须明确且不超过 3600 测试刻度")
        key = (draft, stage)
        payload = {"draft": draft, "step": stage, "evidence": binding, "request": request_sha256,
                   "requester": requester, "approver": approver, "limit": credit_limit,
                   "issued": issued_tick, "expires": expires_tick}
        receipt_id = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        with self._lock:
            if key in self._by_scope:
                raise ValueError("同任务同阶段已有模拟审批；不能重复发行")
            self._by_scope.add(key)
            self._records[receipt_id] = {**payload, "status": "issued_mock", "reason": ""}
        return {"mock_receipt_id": receipt_id, "status": "issued_mock",
                "resume_authorized": False, "external_model_calls": 0}

    def consume(self, receipt_id: str, *, current_scope: dict[str, Any],
                request_sha256: str, cost_credits: int, now_tick: int) -> dict[str, Any]:
        """Consume one fake permission once, but NEVER call an executor."""
        with self._lock:
            record = self._records.get(receipt_id)
            if record is None:
                return self._outcome("blocked_unknown_receipt")
            if record["status"] != "issued_mock":
                return self._outcome("blocked_replay")
            if type(now_tick) is not int or now_tick < record["issued"] or now_tick >= record["expires"]:
                record["status"] = "frozen"
                record["reason"] = "expired"
                return self._outcome("blocked_expired")
            if (not isinstance(current_scope, dict)
                    or current_scope.get("status") != "mock_review_candidate"
                    or current_scope.get("mode") != "mock_approval_scope_read_only"
                    or current_scope.get("resume_authorized") is not False
                    or current_scope.get("can_resume_now") is not False
                    or current_scope.get("blockers") != []
                    or current_scope.get("draft_id") != record["draft"]
                    or current_scope.get("step_id") != record["step"]
                    or current_scope.get("evidence_binding") != record["evidence"]):
                record["status"] = "frozen"
                record["reason"] = "evidence_changed"
                return self._outcome("blocked_evidence_changed")
            if request_sha256 != record["request"]:
                record["status"] = "frozen"
                record["reason"] = "request_changed"
                return self._outcome("blocked_request_changed")
            if type(cost_credits) is not int or not 0 < cost_credits <= record["limit"]:
                record["status"] = "frozen"
                record["reason"] = "budget_invalid"
                return self._outcome("blocked_budget")
            record["status"] = "consumed_mock"
            return self._outcome("mock_approved_for_fake_step_only")

    def status(self, receipt_id: str) -> str:
        with self._lock:
            record = self._records.get(receipt_id)
            return record["status"] if record else "unknown"

    @staticmethod
    def _outcome(status: str) -> dict[str, Any]:
        return {"status": status, "mode": "mock_approval_only",
                "can_resume_now": False, "resume_authorized": False,
                "external_model_calls": 0, "real_project_writes": 0}

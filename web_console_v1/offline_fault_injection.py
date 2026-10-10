"""B4-C1: closed, deterministic fault injection against MOCK approvals/stages.

It never touches a real run directory, model API, budget approval or project
workspace. Every report denies actual resumption even for a happy-path mock.
"""
from __future__ import annotations

import hashlib
from typing import Any

from mock_approval_contract import MockApprovalBook, describe_mock_approval_scope
from mock_stage_runner import FakeStageSession

FAULT_CASES = (
    "mock_control", "no_owner_approval", "self_approval", "duplicate_issue",
    "expired_approval", "evidence_changed", "request_changed", "over_budget",
    "double_consume", "out_of_order_stage", "mock_timeout", "retry_after_timeout",
    "approval_consumed_then_crash", "approval_issued_then_crash", "blocked_legacy",
)


def _fixtures() -> tuple[dict[str, Any], dict[str, Any]]:
    # Synthetic values only; never sourced from students, live tasks or .env.
    preview = {
        "ok": True, "mode": "read_only_preflight", "status": "review_candidate",
        "draft_id": "DRAFT-FAULT-SYNTHETIC", "next_step": "developer.first",
        "checksums": {"state_sha256": "a" * 64,
                      "draft_sha256": "b" * 64,
                      "ledger_sha256": "c" * 64},
        "blockers": [], "requires_owner_approval": True,
        "can_resume_now": False, "resume_authorized": False,
    }
    fake_runner = {
        "mode": "in_memory_fake_only", "status": "ready_fake_only",
        "can_resume_now": False, "resume_authorized": False,
    }
    plan = {
        "mode": "offline_stage_simulation_only", "status": "simulated",
        "start_step": "developer.first", "reused_steps": ["product.spec", "architecture.plan"],
        "simulated_steps": ["developer.first", "qa.review.0"],
        "blockers": [], "can_resume_now": False, "resume_authorized": False,
    }
    return (describe_mock_approval_scope(preview, fake_runner), plan)


def run_mock_fault_case(case: str) -> dict[str, Any]:
    """Run one fully in-memory scenario. No callbacks, networking or disk I/O."""
    if case not in FAULT_CASES:
        raise ValueError("未知离线故障注入案例")
    scope, plan = _fixtures()
    book = MockApprovalBook()
    session = FakeStageSession(plan, credit_limit=7)
    fingerprint = hashlib.sha256(b"synthetic-b4-c1-request").hexdigest()
    result: dict[str, Any] = {
        "ok": True, "mode": "closed_mock_fault_injection", "case": case,
        "status": "blocked", "events": [], "mock_calls": 0,
        "can_resume_now": False, "resume_authorized": False,
        "external_model_calls": 0, "real_project_writes": 0,
        "production_ready": False,
    }

    if case == "blocked_legacy":
        scope["status"] = "blocked"
        scope["blockers"] = ["legacy validation"]
        try:
            book.issue(scope, requester="tester_01", approver="owner_01",
                       request_sha256=fingerprint, credit_limit=5,
                       issued_tick=10, expires_tick=20, approved=True)
        except ValueError:
            result["events"].append("legacy_approval_refused")
        return result

    args = dict(requester="tester_01", approver="owner_01",
                request_sha256=fingerprint, credit_limit=5,
                issued_tick=10, expires_tick=20, approved=True)
    if case == "no_owner_approval":
        args["approved"] = False
    elif case == "self_approval":
        args["approver"] = args["requester"]

    try:
        receipt_id = book.issue(scope, **args)["mock_receipt_id"]
    except ValueError:
        result["events"].append("mock_issue_denied")
        return result

    result["events"].append("mock_issued")
    if case == "duplicate_issue":
        try:
            book.issue(scope, **args)
        except ValueError:
            result["events"].append("second_mock_issue_denied")
        return result
    if case == "approval_issued_then_crash":
        # Losing volatile memory is *not* proof no paid request occurred.
        result["status"] = "manual_review_required"
        result["events"].append("restart_without_durable_approval_proof")
        return result

    check_scope = dict(scope)
    check_request = fingerprint
    fake_cost = 3
    now_tick = 12
    if case == "expired_approval":
        now_tick = 20
    elif case == "evidence_changed":
        check_scope["evidence_binding"] = "d" * 64
    elif case == "request_changed":
        check_request = "e" * 64
    elif case == "over_budget":
        fake_cost = 6

    decision = book.consume(receipt_id, current_scope=check_scope,
                            request_sha256=check_request, cost_credits=fake_cost,
                            now_tick=now_tick)
    result["events"].append(decision["status"])
    if decision["status"] != "mock_approved_for_fake_step_only":
        return result
    if case == "double_consume":
        again = book.consume(receipt_id, current_scope=scope,
                             request_sha256=fingerprint, cost_credits=3,
                             now_tick=12)
        result["events"].append(again["status"])
        return result
    if case == "approval_consumed_then_crash":
        result["status"] = "manual_review_required"
        result["events"].append("lost_in_memory_stage_context")
        return result

    step = "qa.review.0" if case == "out_of_order_stage" else "developer.first"
    timed_out = case in ("mock_timeout", "retry_after_timeout")
    stage_result = session.advance(step_id=step, approved=True, lose_response=timed_out)
    result["mock_calls"] = stage_result["mock_calls"]
    result["events"].append(stage_result["status"])
    if case == "retry_after_timeout":
        again = session.advance(step_id="developer.first", approved=True)
        result["events"].append(again["status"])
        result["mock_calls"] = again["mock_calls"]
    if case == "mock_control":
        result["status"] = "mock_control_complete"
    elif timed_out:
        result["status"] = "manual_review_required"
    return result


def summarize_fault_readiness(approval_scope: dict[str, Any] | None) -> dict[str, Any]:
    """GET-safe informational banner only: NEVER runs a fault scenario."""
    result = {
        "mode": "b4_c1_read_only_audit",
        "status": "blocked",
        "reason": "尚不允许真实断点续跑；需专项故障演练和生产审批/持久化安全校验",
        "offline_scenarios": len(FAULT_CASES),
        "can_resume_now": False,
        "resume_authorized": False,
        "external_model_calls": 0,
        "real_project_writes": 0,
    }
    if (isinstance(approval_scope, dict) and
            approval_scope.get("status") == "mock_review_candidate"):
        result["reason"] = "模拟审批资格存在，但未取得生产级跨进程、防断电和费用授权"
    return result

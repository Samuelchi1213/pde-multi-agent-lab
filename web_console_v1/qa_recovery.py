"""Recovery for an interrupted independent QA review.

Runs exactly one DeepSeek test-agent review over saved deliverables and test
evidence. Never calls full team.run(), Codex, workspace preparation or real
project sync. This is intentionally invoked only after explicit user consent.
"""
import json
from datetime import datetime
from pathlib import Path

from team_executor import DynamicTeamRun


def historical_tests(state):
    return next((
        event.get("detail")
        for event in reversed(state.get("timeline") or [])
        if event.get("agent") == "系统验证器"
        and event.get("action") == "独立运行可发现测试"
    ), None)


def recover_once(root: Path, draft_id: str, draft: dict, api_key: str, saved: dict):
    run_dir = root / "orchestrator_v1" / "dynamic_runs" / draft_id
    workspace = run_dir / "workspace"
    delivery_file = run_dir / "codex_delivery.json"
    qa_file = run_dir / "artifacts" / "qa_review.json"
    state_file = run_dir / "state.json"
    runner = None
    try:
        if not workspace.is_dir() or not delivery_file.is_file():
            raise RuntimeError("隔离工作区或 Codex 交付缺失，不可继续")
        if qa_file.exists():
            raise RuntimeError("复核报告已存在，不会重复调用 DeepSeek")
        evidence = historical_tests(saved)
        if not isinstance(evidence, dict) or evidence.get("returncode") != 0:
            raise RuntimeError("没有成功的原始自动测试证据")
        delivery = json.loads(delivery_file.read_text(encoding="utf-8"))

        runner = DynamicTeamRun(root, draft_id, draft, api_key)
        runner.state = saved
        # REVIEW ONLY: no run(), no prepare_workspace(), no sync_allowed_paths().
        review = runner.review_delivery(draft["analysis"], delivery, evidence)
        outcome = review.get("status")
        if outcome == "pass" or (
            outcome == "need_human"
            and review.get("human_decision_type") == "user_acceptance"
        ):
            runner.state["status"] = "复核通过（待安全发布）"
            runner.state["qa_passed"] = True
            runner.state["candidate_synced"] = False
        elif outcome == "rework":
            runner.state["status"] = "复核发现需返工"
        else:
            runner.state["status"] = "复核等待负责人决定"
        runner.state["current_agent"] = ""
        runner.state["finished_at"] = datetime.now().isoformat(timespec="seconds")
        runner.event("系统", "仅 QA 复核完成，未修改真实项目", {
            "qa_status": outcome,
            "findings": review.get("findings", []),
            "note": "需先阅读 QA 报告。发布和返工必须另外明确授权。",
        })
        runner.state["current_agent"] = ""
        runner.save()
        return runner.state
    except Exception as exc:
        state = runner.state if runner is not None else saved
        state["status"] = "复核失败（保留成果）"
        state["current_agent"] = ""
        state["error"] = str(exc)
        state.setdefault("timeline", []).append({
            "time": datetime.now().isoformat(timespec="seconds"),
            "agent": "系统",
            "action": "仅 QA 复核失败，未自动重试",
            "detail": str(exc),
        })
        run_dir.mkdir(parents=True, exist_ok=True)
        from task_state import atomic_write_json
        atomic_write_json(state_file, state)
        return state

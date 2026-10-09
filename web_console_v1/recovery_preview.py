"""M7-003 B3: read-only, fail-closed step recovery preview.

Examines task state, existing step reservations, saved receipts and (for
Codex) the currently staged program snapshot. Does NOT change files, run
tests, approve budgets, invoke Codex/DeepSeek, or offer automatic replay.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from codex_checkpoints import program_snapshot
from step_checkpoints import CheckpointError, inspect_step

VALID_DRAFT_ID = re.compile(r"^(?:DRAFT|VALIDATE)-[A-Za-z0-9_-]{1,70}$")
KNOWN_AGENTS = {
    "产品智能体": ("product.spec", "DeepSeek"),
    "架构智能体": ("architecture.plan", "DeepSeek"),
    "开发智能体": ("developer.first", "Codex"),
    "测试智能体": ("qa.review.0", "DeepSeek"),
}
SAFE_VIEW_STATUSES = {
    "等待预算确认", "已暂停", "执行失败", "复核失败（保留成果）",
    "复核中断（已保留成果）", "执行中断（需要检查）",
    "准备继续", "等待人工决策", "需要人工返工",
    "定向返工中断（隔离成果保留）",
}
FINAL_STATUSES = {
    "已完成", "等待人工验收", "复核通过（待安全发布）",
    "定向返工测试通过（待安全发布）",
}


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else None
    except (OSError, ValueError, UnicodeError):
        return None


def _inspect_codex_receipt(run_dir: Path, workspace: Path,
                           step_id: str, relative_receipt: str) -> tuple[bool, str]:
    receipt = _read_json(run_dir / relative_receipt)
    if not receipt or receipt.get("step_id") != step_id:
        return False, "Codex 检查点回执不存在或结构异常"
    if not isinstance(receipt.get("post_program_snapshot"), dict):
        return False, "Codex 程序快照证据不存在"
    if not workspace.is_dir():
        return False, "隔离工作区不存在"
    try:
        actual = program_snapshot(workspace)
    except (CheckpointError, OSError, ValueError):
        return False, "隔离工作区快照无法核实"
    if actual != receipt["post_program_snapshot"]:
        return False, "隔离代码与已保存的 Codex 步骤快照不一致"
    identity = receipt.get("identity")
    if not isinstance(identity, dict):
        return False, "Codex 请求指纹结构异常"
    relative_result = identity.get("result_path")
    if not isinstance(relative_result, str) or not relative_result:
        return False, "Codex 交付路径无效"
    output = (run_dir / relative_result).resolve()
    try:
        output.relative_to(run_dir.resolve())
    except ValueError:
        return False, "Codex 交付路径越过隔离任务目录"
    if not output.is_file() or output.is_symlink():
        return False, "Codex 交付文件不存在"
    import hashlib
    if hashlib.sha256(output.read_bytes()).hexdigest() != receipt.get("delivery_sha256"):
        return False, "Codex 交付文件校验失败"
    if not _read_json(output):
        return False, "Codex 交付 JSON 格式无效"
    return True, "已核实 Codex 交付文件和工作区程序快照"


def _inspect_deepseek_receipt(run_dir: Path, step_id: str,
                              relative_receipt: str) -> tuple[bool, str]:
    receipt = _read_json(run_dir / relative_receipt)
    if (not receipt or receipt.get("step_id") != step_id
            or not isinstance(receipt.get("result"), dict)
            or not isinstance(receipt.get("usage"), dict)):
        return False, "DeepSeek 回执结构不完整"
    return True, "已有匹配哈希的 DeepSeek 结果及用量回执"


def _stage(run_dir: Path, workspace: Path, step_id: str, agent: str,
           kind: str) -> dict[str, Any]:
    item = {"step_id": step_id, "agent": agent, "executor": kind}
    try:
        decision = inspect_step(run_dir, step_id)
    except (CheckpointError, OSError, ValueError) as exc:
        item.update(status="blocked", note=f"检查点无法读取：{type(exc).__name__}")
        return item

    result = decision["decision"]
    if result == "not_started":
        item.update(status="not_started", note="没有该步骤的调用预约记录")
        return item
    if result != "reuse_saved_result":
        item.update(status="blocked", note="检查点未完成或回执损坏；禁止自动重试")
        return item

    rel = decision["receipt"]
    if kind == "Codex":
        ok, note = _inspect_codex_receipt(run_dir, workspace, step_id, rel)
    else:
        ok, note = _inspect_deepseek_receipt(run_dir, step_id, rel)
    item.update(status="verified" if ok else "blocked", note=note)
    return item


def _unavailable_draft_preview(draft_id: str, status: str, reason: str) -> dict[str, Any]:
    """Fail closed for old runs with no trustworthy original agent roster."""
    blockers = [
        reason + "；无法核实原任务的智能体名单及步骤调用，禁止推断或重跑。"
    ]
    if draft_id.startswith("VALIDATE-"):
        blockers.append(
            "该任务属于系统回归验证；旧版验证草案可能只保存在进程内存中。"
            "不得按历史计数补造检查点或启动付费模型。"
        )
    return {
        "ok": True,
        "draft_id": draft_id,
        "task_status": status,
        "mode": "read_only_no_model_calls",
        "can_resume_now": False,
        "resume_authorized": False,
        "steps": [],
        "verified_steps": [],
        "not_started_steps": [],
        "blocked_steps": [],
        "blockers": blockers,
        "next_action": "保留原始状态、回执及隔离工作区，人工核实草案来源；不得直接续跑或重启整队。",
    }


def build_recovery_preview(run_dir: Path, draft: dict[str, Any],
                           state: dict[str, Any], *, running: bool = False) -> dict[str, Any]:
    """Diagnostic ONLY. No result here is authorization to run a model."""
    run_dir = Path(run_dir)
    if not isinstance(state, dict):
        raise ValueError("历史状态格式不正确")
    draft_id = str(state.get("draft_id") or run_dir.name)
    if not VALID_DRAFT_ID.fullmatch(draft_id) or run_dir.name != draft_id:
        raise ValueError("任务 ID 不符合安全读取规则")
    if not run_dir.is_dir() or not (run_dir / "state.json").is_file():
        raise ValueError("历史任务记录不存在")

    status = str(state.get("status") or "")
    if not isinstance(draft, dict):
        return _unavailable_draft_preview(draft_id, status, "原始任务草案不存在或无法读取")
    analysis = draft.get("analysis")
    team = analysis.get("required_agents") if isinstance(analysis, dict) else None
    if not isinstance(team, list) or any(not isinstance(x, str) for x in team):
        return _unavailable_draft_preview(draft_id, status, "原始任务智能体名单不完整或格式错误")

    note = []
    if running:
        note.append("后台仍有运行线程，不能同时恢复")
    if status in FINAL_STATUSES:
        note.append("该任务已进入完成/发布/验收阶段，不能作为中断任务重复执行")
    elif status not in SAFE_VIEW_STATUSES and not running:
        note.append("当前状态尚无安全的分阶段续跑流程")
    if state.get("candidate_synced") or state.get("manual_rework_published"):
        note.append("已有发布记录，禁止重新执行此前开发步骤")

    steps = []
    workspace = run_dir / "workspace"
    for agent, (step_id, kind) in KNOWN_AGENTS.items():
        if agent in team:
            steps.append(_stage(run_dir, workspace, step_id, agent, kind))
    rounds = int(state.get("rework_count") or 0)
    if rounds < 0 or rounds > 10:
        raise ValueError("自动返工轮次信息不正确")
    for n in range(1, rounds + 1):
        steps.append(_stage(run_dir, workspace, f"developer.rework.{n}", "开发智能体", "Codex"))
        if "测试智能体" in team:
            steps.append(_stage(run_dir, workspace, f"qa.review.{n}", "测试智能体", "DeepSeek"))

    if state.get("manual_rework_attempted"):
        manual_dir = run_dir / "manual_rework_1"
        steps.append(_stage(manual_dir, manual_dir / "workspace", "manual.rework.1",
                            "开发智能体（人工定向返工）", "Codex"))

    blocked = [x["step_id"] for x in steps if x["status"] == "blocked"]
    verified = [x["step_id"] for x in steps if x["status"] == "verified"]
    pending = [x["step_id"] for x in steps if x["status"] == "not_started"]
    checkpoint_dir_exists = (run_dir / "step_checkpoints.json").is_file()
    used_calls = int(state.get("deepseek_calls") or 0) + int(state.get("codex_calls") or 0)
    if used_calls > 0 and not checkpoint_dir_exists:
        note.append("这是没有完整步骤检查点的历史任务，不允许根据计数猜测已执行步骤")
    if blocked:
        note.append("存在结果未知、证据损坏或代码快照变化的步骤，禁止自动重试")
    if status == "等待预算确认" and state.get("budget_approval"):
        note.append("预算申请尚待人工决策；预览并非费用审批")
    if not workspace.is_dir():
        note.append("主隔离工作区不存在；须保留历史记录并人工检查")

    if not note and pending and verified:
        suggestion = "已识别完整历史回执和未开始阶段；未来仍须通过显式授权的阶段级执行器才能续跑"
    elif not note and pending and not verified:
        suggestion = "未见既有完成回执；仅作状态报告，不自动启动模型"
    elif not note and not pending:
        suggestion = "已知步骤都有回执；先核对测试与交付状态，不必重复付费调用"
    else:
        suggestion = "当前任务需要人工诊断，禁止直接执行原团队或重复模型调用"

    return {
        "ok": True,
        "draft_id": draft_id,
        "task_status": status,
        "mode": "read_only_no_model_calls",
        "can_resume_now": False,
        "resume_authorized": False,
        "steps": steps,
        "verified_steps": verified,
        "not_started_steps": pending,
        "blocked_steps": blocked,
        "blockers": note,
        "next_action": suggestion,
    }

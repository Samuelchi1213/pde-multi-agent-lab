"""B4-A: deterministic, side-effect-free stage recovery *simulation*.

This is NOT a resume executor. Only the B3 evidence-backed preview can be used
as input, and even a successful simulation never authorizes model calls, file
writes, budget changes or release of real project code.
"""
from __future__ import annotations

import re
from typing import Any

from recovery_preview import VALID_DRAFT_ID

# Stages named by the existing B3 proof reader. A stage absent from a draft is
# never inferred; the input preview already determines which agents were hired.
_CORE = ("product.spec", "architecture.plan", "developer.first", "qa.review.0")
_REWORK = re.compile(r"^(developer\.rework|qa\.review)\.([1-9][0-9]?)$")
_ALLOWED_RECOVERY_STATES = {
    "执行中断（需要检查）", "执行失败", "复核中断（已保留成果）", "已暂停", "准备继续",
}


def _stage_order(step_id: str) -> tuple[int, int]:
    if step_id in _CORE:
        return (0, _CORE.index(step_id))
    if step_id == "manual.rework.1":
        return (2, 0)
    match = _REWORK.fullmatch(step_id)
    if match and int(match.group(2)) <= 10:
        return (1, 2 * int(match.group(2)) + (0 if match.group(1) == "developer.rework" else 1))
    raise ValueError("未知的阶段编号，禁止推断恢复路径")


def simulate_stage_recovery(preview: dict[str, Any], *, start_step: str | None = None) -> dict[str, Any]:
    """Return a read-only fake transition trace; never executes or persists.

    A model stage is represented by a hypothetical trace event only. If an old
    task has no trustworthy *completed prefix* followed by not-started steps,
    the entire plan is blocked. This conservative choice cannot recover all
    interrupted tasks; that requires a separate, explicitly authorized B4-B.
    """
    result = {
        "ok": True,
        "mode": "offline_stage_simulation_only",
        "status": "blocked",
        "start_step": None,
        "reused_steps": [],
        "simulated_steps": [],
        "trace": [],
        "blockers": [],
        "can_resume_now": False,
        "resume_authorized": False,
        "external_model_calls": 0,
        "real_project_writes": 0,
    }

    def stop(reason: str) -> dict[str, Any]:
        result["blockers"].append(reason)
        return result

    if not isinstance(preview, dict) or preview.get("ok") is not True:
        return stop("没有可信的历史预览结果，禁止模拟恢复")
    if (preview.get("mode") != "read_only_no_model_calls" or
            preview.get("can_resume_now") is not False or
            preview.get("resume_authorized") is not False):
        return stop("历史预览模式或授权标志不符合只读约束")
    draft_id = preview.get("draft_id")
    if not isinstance(draft_id, str) or not VALID_DRAFT_ID.fullmatch(draft_id):
        return stop("任务标识不合法")
    if draft_id.startswith("VALIDATE-"):
        return stop("系统回归验证历史不能成为真实恢复演练依据")
    if preview.get("task_status") not in _ALLOWED_RECOVERY_STATES:
        return stop("当前任务状态不在离线恢复演练允许范围内")
    blockers = preview.get("blockers")
    if not isinstance(blockers, list) or any(not isinstance(x, str) for x in blockers):
        return stop("原始阻断信息结构错误")
    if blockers:
        result["blockers"].extend(blockers)
        return stop("原始预览存在风险，不能跳过检查")
    steps = preview.get("steps")
    if not isinstance(steps, list) or not steps:
        return stop("缺少可核实的阶段列表")

    verified, pending, identifiers = [], [], set()
    encountered_unstarted = False
    last = (-1, -1)
    for stage in steps:
        if not isinstance(stage, dict):
            return stop("阶段记录格式错误")
        name, status = stage.get("step_id"), stage.get("status")
        if not isinstance(name, str) or name in identifiers:
            return stop("阶段编号无效或重复")
        identifiers.add(name)
        try:
            ordinal = _stage_order(name)
        except ValueError as exc:
            return stop(str(exc))
        if ordinal <= last:
            return stop("阶段顺序异常，禁止跳过或倒序执行")
        last = ordinal
        if status == "verified":
            if encountered_unstarted:
                return stop("未开始阶段之后存在已完成阶段，不能推断安全恢复点")
            verified.append(name)
        elif status == "not_started":
            encountered_unstarted = True
            pending.append(name)
        else:
            return stop("存在未决或损坏步骤，禁止模拟自动重试")
    if verified != preview.get("verified_steps") or pending != preview.get("not_started_steps"):
        return stop("阶段证据摘要与列表不一致")
    if preview.get("blocked_steps") != []:
        return stop("阶段阻断摘要异常")
    if not verified:
        return stop("尚无可靠的已完成阶段；这不是一个可恢复的部分执行任务")
    if not pending:
        return stop("全部已知阶段均有回执，没有待模拟阶段")
    if start_step is not None and start_step != pending[0]:
        return stop("只能从首个未开始阶段进入模拟，不允许手动跳过")

    result["status"] = "simulated"
    result["start_step"] = pending[0]
    result["reused_steps"] = verified
    result["simulated_steps"] = pending
    result["trace"] = ([{"step_id": name, "transition": "reuse_verified_receipt"} for name in verified]
                       + [{"step_id": name, "transition": "simulate_only_no_call"} for name in pending])
    return result

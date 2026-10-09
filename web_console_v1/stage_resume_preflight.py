"""B4-B0: strict read-only preflight for a *possible future* staged resume.

Never executes tools, authorizes paid model calls, writes files, changes
budgets, or mutates original task state. 'review_candidate' is not permission.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from offline_stage_recovery import simulate_stage_recovery
from recovery_preview import VALID_DRAFT_ID, build_recovery_preview
from task_state import read_state


def _fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_resume_preflight(run_dir: Path, *, running: bool = False) -> dict[str, Any]:
    """Inspect the original on-disk draft/state/ledger/workspace without writing.

    This is a documentary safety review only. It does not read credentials,
    alter live code or call an executor. No caller-supplied draft is trusted.
    """
    result = {
        "ok": True,
        "mode": "read_only_preflight",
        "status": "blocked",
        "draft_id": "",
        "next_step": None,
        "verified_prefix": [],
        "checksums": {},
        "blockers": [],
        "requires_owner_approval": True,
        "can_resume_now": False,
        "resume_authorized": False,
        "external_model_calls": 0,
        "real_project_writes": 0,
    }

    def stop(reason: str) -> dict[str, Any]:
        result["blockers"].append(reason)
        return result

    run_dir = Path(run_dir)
    draft_id = run_dir.name
    result["draft_id"] = draft_id
    if not VALID_DRAFT_ID.fullmatch(draft_id) or draft_id.startswith("VALIDATE-"):
        return stop("仅普通 DRAFT 历史任务可进入恢复资格审查")
    if running:
        return stop("原任务仍有运行中的工作线程")
    if (not run_dir.is_dir() or run_dir.is_symlink() or
            run_dir.parent.name != "dynamic_runs"):
        return stop("原始任务目录无效或路径不符合预期")
    state_file = run_dir / "state.json"
    workspace = run_dir / "workspace"
    ledger = run_dir / "step_checkpoints.json"
    draft_file = run_dir.parent.parent / "runtime" / "drafts" / (draft_id + ".json")
    for path in (state_file, ledger, draft_file):
        if path.is_symlink() or not path.is_file():
            return stop("原始草案、状态或检查点缺失，或包含符号链接")
    if workspace.is_symlink() or not workspace.is_dir():
        return stop("必须保留原始隔离工作区，不能重新创建")

    try:
        state, error = read_state(state_file)
        draft = json.loads(draft_file.read_text(encoding="utf-8"))
        checkpoint = json.loads(ledger.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return stop("任务证据读取失败或格式损坏")
    if error or not isinstance(state, dict) or state.get("draft_id") != draft_id:
        return stop("原始任务状态文件与任务标识不一致")
    if not isinstance(draft, dict) or draft.get("confirmed") is not True:
        return stop("原任务草案未确认为可执行版本")
    if not isinstance(draft.get("analysis"), dict):
        return stop("原草案没有可信的 Agent 职责清单")
    if not isinstance(checkpoint, dict) or checkpoint.get("version") != 1:
        return stop("检查点结构或版本无效")
    if not isinstance(checkpoint.get("steps"), dict):
        return stop("原始步骤检查点信息不完整")
    if state.get("budget_approval") or state.get("candidate_synced") or state.get("manual_rework_published"):
        return stop("预算待批或已发布记录禁止进入恢复候选")
    # A path/permission snapshot from a connected real project requires a
    # separate validation and explicit approval; never infer its current grant.
    if draft.get("use_real_project") or state.get("real_project"):
        return stop("绑定真实项目的任务需要独立核对原授权与项目副本")
    if not isinstance(state.get("deepseek_calls", 0), int) or not isinstance(state.get("codex_calls", 0), int):
        return stop("原始模型调用计数异常")
    if state.get("deepseek_calls", 0) < 0 or state.get("codex_calls", 0) < 0:
        return stop("原始模型调用计数不得为负数")

    try:
        preview = build_recovery_preview(run_dir, draft, state, running=False)
        simulation = simulate_stage_recovery(preview)
    except (ValueError, TypeError, OSError):
        return stop("回执或阶段预览无法安全核验")
    if simulation["status"] != "simulated":
        result["blockers"].extend(simulation["blockers"])
        return stop("尚无满足要求的连续证据链")
    try:
        result["checksums"] = {
            "state_sha256": _fingerprint(state_file),
            "draft_sha256": _fingerprint(draft_file),
            "ledger_sha256": _fingerprint(ledger),
        }
    except OSError:
        return stop("原始证据在复核过程中无法完整读取")
    result["status"] = "review_candidate"
    result["next_step"] = simulation["start_step"]
    result["verified_prefix"] = simulation["reused_steps"]
    return result

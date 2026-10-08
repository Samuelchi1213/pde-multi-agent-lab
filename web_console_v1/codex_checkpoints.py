"""M7-003 B2: fail-closed Codex CLI step checkpoint wrapper.

This module neither starts a Codex process nor touches a real project by
itself: the caller must explicitly provide the existing Codex executor.

Never replay a Codex call after an uncertain result. A completed receipt can
only be reused if its delivery SHA and post-execution program snapshot match.

The program snapshot tracks code/configuration, not private business records.
It is not a replacement for a full source-control diff or a safe publish gate.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from step_checkpoints import (
    CheckpointError, finish_step, inspect_step, mark_uncertain, reserve_step,
)
from task_state import atomic_write_json

PROGRAM_SUFFIXES = frozenset({
    ".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".md",
    ".toml", ".ini", ".yaml", ".yml", ".txt", ".json", ".ps1", ".bat",
})
EXCLUDED_DIRS = frozenset({
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache",
    ".mypy_cache", "dist", "build", ".cache", ".next", "data", "uploads",
    "backups", "logs", "secrets", "runtime",
})
MAX_FILES = 4000
MAX_PROGRAM_BYTES = 100 * 1024 * 1024
MAX_ONE_FILE_BYTES = 16 * 1024 * 1024


def _sha(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def _within(path: Path, root: Path) -> Path:
    try:
        return path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise CheckpointError("Codex 工作区、回执或 Schema 超出本次任务目录") from exc


def program_snapshot(workspace: Path) -> dict[str, Any]:
    """Hash only safe program files. Refuse symlinks and impossible snapshots."""
    workspace = Path(workspace)
    if not workspace.is_dir() or workspace.is_symlink():
        raise CheckpointError("缺少可靠的隔离工作区")
    aggregate = hashlib.sha256()
    files, total = 0, 0
    for path in sorted(workspace.rglob("*")):
        relative = path.relative_to(workspace)
        if any(part.lower() in EXCLUDED_DIRS for part in relative.parts):
            continue
        if path.is_symlink():
            raise CheckpointError("检测到程序目录软链接；不可验证 Codex 工作区")
        if not path.is_file():
            continue
        if path.suffix.lower() not in PROGRAM_SUFFIXES:
            continue
        if path.name.startswith(".env"):
            continue
        # JSON under src often contains real user data; do not read such files.
        if path.suffix.lower() == ".json" and relative.parts[0].lower() == "src":
            continue
        size = path.stat().st_size
        files += 1
        total += size
        if files > MAX_FILES or size > MAX_ONE_FILE_BYTES or total > MAX_PROGRAM_BYTES:
            raise CheckpointError("待校验程序文件过多或过大；禁止未经验证的复用")
        digest = _sha(path.read_bytes())
        aggregate.update(relative.as_posix().encode("utf-8"))
        aggregate.update(b"\x00")
        aggregate.update(str(size).encode("ascii"))
        aggregate.update(b"\x00")
        aggregate.update(digest.encode("ascii"))
        aggregate.update(b"\n")
    return {"sha256": aggregate.hexdigest(), "files": files, "bytes": total}


def _load_verified_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("交付文件不是 JSON 对象")
        return data
    except (OSError, UnicodeError, ValueError) as exc:
        raise CheckpointError("Codex 交付不是有效 JSON，不得自动重复执行") from exc


def execute_codex_step(
    *, run_dir: Path, workspace: Path, step_id: str, prompt: str,
    schema_path: Path, result_path: Path,
    executor: Callable[[Path, str, Path, Path], dict[str, Any]],
    model: str = "gpt-5.6-sol",
) -> tuple[dict[str, Any], bool]:
    """Run or reuse a Codex step. Returns (delivery, reused).

    A new call is allowed exactly once after a durable reservation.
    Return result validation never implies human or QA acceptance.
    """
    run_dir = Path(run_dir)
    workspace = Path(workspace)
    schema_path = Path(schema_path)
    result_path = Path(result_path)
    if not run_dir.is_dir() or not workspace.is_dir():
        raise CheckpointError("原任务或独立工作区不存在，禁止自动重新建目录")
    _within(workspace, run_dir)
    rel_result = _within(result_path, run_dir)
    if not schema_path.is_file():
        raise CheckpointError("Codex 交付 Schema 不存在")
    if not isinstance(prompt, str) or not prompt.strip():
        raise CheckpointError("Codex 提示内容为空")
    if not callable(executor):
        raise CheckpointError("缺少明确的 Codex 执行器")

    # Bind to the exact prompt/model/schema. Workspace changes are validated
    # separately: a successful Codex step changes its own workspace.
    identity = {
        "kind": "codex-cli",
        "model": model,
        "prompt_sha256": _sha(prompt.encode("utf-8")),
        "schema_sha256": _sha(schema_path.read_bytes()),
        "result_path": rel_result.as_posix(),
    }
    decision = inspect_step(run_dir, step_id, identity)

    if decision["decision"] == "reuse_saved_result":
        receipt_path = run_dir / decision["receipt"]
        evidence = _load_verified_json(receipt_path)
        if evidence.get("step_id") != step_id or evidence.get("identity") != identity:
            raise CheckpointError("Codex 检查点信息不匹配；禁止复用")
        if not result_path.is_file() or result_path.is_symlink():
            raise CheckpointError("Codex 历史交付文件缺失；禁止复用")
        if _sha(result_path.read_bytes()) != evidence.get("delivery_sha256"):
            raise CheckpointError("Codex 交付内容与历史记录不一致；禁止复用")
        if program_snapshot(workspace) != evidence.get("post_program_snapshot"):
            raise CheckpointError("隔离工作区发生变化；不能自动复用之前的 Codex 结果")
        return _load_verified_json(result_path), True

    if decision["decision"] != "not_started":
        raise CheckpointError(
            f"Codex 步骤 {step_id}：{decision['decision']}；"
            "结果可能已计费或已修改工作区，禁止自动重复调用"
        )
    if result_path.exists():
        raise CheckpointError("已有未经检查点确认的 Codex 交付，禁止覆盖或重跑")

    before = program_snapshot(workspace)
    reservation = reserve_step(run_dir, step_id, identity)
    if reservation["decision"] != "reserved":
        raise CheckpointError("Codex 步骤已被其他进程占用，禁止第二次调用")

    try:
        # The executor is existing run_codex(), NOT a new process path.
        delivery = executor(workspace, prompt, schema_path, result_path)
        if not isinstance(delivery, dict):
            raise ValueError("Codex 返回结果非 JSON 对象")
        stored_delivery = _load_verified_json(result_path)
        if stored_delivery != delivery:
            raise CheckpointError("Codex 返回对象与落盘交付不同")
        post = program_snapshot(workspace)
        receipt_path = run_dir / f"codex_step_{step_id}.json"
        if receipt_path.exists():
            raise CheckpointError("步骤回执已存在，禁止覆盖旧证据")
        atomic_write_json(receipt_path, {
            "step_id": step_id,
            "identity": identity,
            "delivery_sha256": _sha(result_path.read_bytes()),
            "pre_program_snapshot": before,
            "post_program_snapshot": post,
            "note": "仅证实 Codex 交付与程序快照一致；仍须独立测试和人工验收",
        })
        finish_step(run_dir, step_id, identity, receipt_path.relative_to(run_dir).as_posix())
        return delivery, False
    except BaseException as exc:
        # Timeout or a local file error may happen after Codex already wrote code.
        # Keep the original workspace and prevent silent paid re-execution.
        try:
            mark_uncertain(run_dir, step_id, type(exc).__name__)
        except Exception:
            pass
        raise

"""Fail-closed durable step checkpoints for PDE (M7-003 foundation).

No AI API calls occur here. A checkpoint is a claim about evidence, not a
process supervisor. Never infer successful completion from a stale "in_flight"
record or rerun a paid step automatically when its result is unknown.

The ledger stores only hashed input fingerprints and references to local
receipts; it never persists prompts, API keys, or model responses itself.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from task_state import atomic_write_json

VERSION = 1
STEP_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,79}$")
MAX_RECEIPT_BYTES = 20 * 1024 * 1024


class CheckpointError(RuntimeError):
    """A checkpoint error stops automatic replay (fail closed)."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _validate_step_id(step_id: str) -> None:
    if not isinstance(step_id, str) or not STEP_NAME.fullmatch(step_id) or ".." in step_id:
        raise CheckpointError("非法步骤 ID，禁止创建检查点")


def _fingerprint(inputs: Any) -> str:
    try:
        encoded = json.dumps(inputs, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                             allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise CheckpointError("步骤输入必须是可序列化的 JSON") from exc
    return hashlib.sha256(encoded).hexdigest()


def _ledger_file(run_dir: Path) -> Path:
    return Path(run_dir) / "step_checkpoints.json"


@contextmanager
def _exclusive_lock(run_dir: Path) -> Iterator[None]:
    """Cross-process exclusive create, never auto-steal a stale lock after a crash."""
    run_dir = Path(run_dir)
    if not run_dir.is_dir():
        raise CheckpointError("任务目录不存在；不得自动新建并重新执行")
    lockfile = run_dir / ".step_checkpoints.lock"
    try:
        fd = os.open(lockfile, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise CheckpointError("检查点被其他任务锁定，或存在崩溃遗留锁；需人工诊断") from exc
    except OSError as exc:
        raise CheckpointError("无法锁定检查点；禁止继续模型调用") from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(f"pid={os.getpid()}\ncreated={_now()}\n")
        yield
    finally:
        try:
            lockfile.unlink()
        except FileNotFoundError:
            pass


def _load(run_dir: Path) -> dict[str, Any]:
    path = _ledger_file(run_dir)
    if not path.exists():
        return {"version": VERSION, "steps": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError) as exc:
        raise CheckpointError("检查点文件损坏或无法读取；禁止自动重试") from exc
    if (not isinstance(data, dict) or data.get("version") != VERSION or
            not isinstance(data.get("steps"), dict)):
        raise CheckpointError("检查点版本或结构不符合要求；禁止自动重试")
    return data


def _write(run_dir: Path, ledger: dict[str, Any]) -> None:
    atomic_write_json(_ledger_file(run_dir), ledger)


def _receipt_evidence(run_dir: Path, receipt: str) -> dict[str, Any]:
    if not isinstance(receipt, str) or not receipt.strip():
        raise CheckpointError("必须指定已保存的本地结果凭据")
    root = Path(run_dir).resolve()
    filepath = (root / receipt).resolve()
    try:
        filepath.relative_to(root)
    except ValueError as exc:
        raise CheckpointError("凭据文件超出任务目录") from exc
    if not filepath.is_file() or filepath.is_symlink():
        raise CheckpointError("结果凭据不存在或不是普通文件")
    data = filepath.read_bytes()
    if not data or len(data) > MAX_RECEIPT_BYTES:
        raise CheckpointError("结果凭据为空或超过大小限制")
    return {
        "receipt":filepath.relative_to(root).as_posix(),
        "sha256":hashlib.sha256(data).hexdigest(),
        "bytes":len(data),
    }


def inspect_step(run_dir: Path, step_id: str, inputs: Any | None = None) -> dict[str, Any]:
    """Read-only status; an in-flight call is ambiguous even after a process crash."""
    _validate_step_id(step_id)
    ledger = _load(run_dir)
    item = ledger["steps"].get(step_id)
    if item is None:
        return {"decision": "not_started", "step_id":step_id}
    if not isinstance(item, dict):
        raise CheckpointError("步骤记录损坏")
    status = item.get("status")
    if status not in {"in_flight", "completed", "uncertain"}:
        raise CheckpointError("未知步骤状态；禁止自动调用")
    if inputs is not None and item.get("input_sha256") != _fingerprint(inputs):
        return {"decision":"blocked_input_changed","step_id":step_id,"status":status}
    if status != "completed":
        return {"decision":"blocked_uncertain","step_id":step_id,"status":status}
    evidence = item.get("evidence")
    if not isinstance(evidence, dict):
        return {"decision":"blocked_missing_evidence","step_id":step_id,"status":status}
    try:
        actual = _receipt_evidence(run_dir, evidence.get("receipt"))
    except (CheckpointError, OSError):
        return {"decision":"blocked_missing_evidence","step_id":step_id,"status":status}
    if actual["sha256"] != evidence.get("sha256") or actual["bytes"] != evidence.get("bytes"):
        return {"decision":"blocked_evidence_changed","step_id":step_id,"status":status}
    return {"decision":"reuse_saved_result","step_id":step_id,"status":status,
            "receipt":actual["receipt"]}


def reserve_step(run_dir: Path, step_id: str, inputs: Any) -> dict[str, Any]:
    """Reserve before *any* paid/external call; returning 'reserved' is the only go-ahead.

    On collision, never run the model. A completed receipt may be reused by a
    higher-level runner after it loads and validates the saved response.
    """
    _validate_step_id(step_id)
    fp = _fingerprint(inputs)
    with _exclusive_lock(run_dir):
        existing = inspect_step(run_dir, step_id, inputs)
        if existing["decision"] != "not_started":
            return existing
        ledger = _load(run_dir)
        ledger["steps"][step_id] = {
            "status":"in_flight",
            "input_sha256":fp,
            "attempt":1,
            "started_at":_now(),
        }
        _write(run_dir, ledger)
        return {"decision":"reserved","step_id":step_id}


def finish_step(run_dir: Path, step_id: str, inputs: Any, receipt: str) -> dict[str, Any]:
    """Only finish an existing reserved step when a verifiable receipt exists."""
    _validate_step_id(step_id)
    fp = _fingerprint(inputs)
    with _exclusive_lock(run_dir):
        ledger = _load(run_dir)
        item = ledger["steps"].get(step_id)
        if not isinstance(item, dict) or item.get("input_sha256") != fp:
            raise CheckpointError("步骤未预约或请求指纹不匹配")
        if item.get("status") != "in_flight":
            raise CheckpointError("只有进行中的步骤可以保存完成证据")
        evidence = _receipt_evidence(run_dir, receipt)
        item["status"] = "completed"
        item["finished_at"] = _now()
        item["evidence"] = evidence
        _write(run_dir, ledger)
        return {"decision":"completed","step_id":step_id,"receipt":evidence["receipt"]}


def mark_uncertain(run_dir: Path, step_id: str, reason: str) -> dict[str, Any]:
    """Persist uncertainty, but never authorize a retry or reset the reservation."""
    _validate_step_id(step_id)
    with _exclusive_lock(run_dir):
        ledger = _load(run_dir)
        item = ledger["steps"].get(step_id)
        if not isinstance(item, dict) or item.get("status") not in ("in_flight", "uncertain"):
            raise CheckpointError("只有未决步骤可标记为待人工诊断")
        item["status"] = "uncertain"
        item["uncertain_at"] = _now()
        item["reason"] = str(reason)[:500]
        _write(run_dir, ledger)
    return {"decision":"blocked_uncertain","step_id":step_id,"status":"uncertain"}


def list_steps(run_dir: Path) -> list[dict[str, Any]]:
    """Safe summary only; raw prompts and saved model results are never included."""
    ledger = _load(run_dir)
    return [
        {"step_id":name,**inspect_step(run_dir,name)}
        for name in sorted(ledger["steps"])
    ]

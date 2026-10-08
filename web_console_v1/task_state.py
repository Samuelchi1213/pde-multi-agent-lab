"""Stable read-only team status projection and atomic JSON state persistence.

The authoritative run state lives in dynamic_runs/<draft_id>/state.json.
A Python worker thread is *not* a resumable execution: if it no longer exists
while state says "running", report an interruption and preserve artifacts.
Never restart paid model calls merely because a saved state looks unfinished.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

ACTIVE_STATES = frozenset({"准备中", "执行中", "自动返工中", "测试复核恢复中"})
ACTIVE_PREFIXES = ("定向返工：",)
TERMINAL_STATES = frozenset({
    "已完成", "执行失败", "验证失败", "等待人工验收",
    "等待人工决策", "等待预算确认", "需要人工返工",
    "已取消", "已中止", "准备继续",
    "复核通过（待安全发布）", "复核发现需返工",
    "复核等待负责人决定", "复核失败（保留成果）",
    "定向返工测试通过（待安全发布）", "定向返工测试未通过",
    "定向返工失败（隔离成果保留）",
})


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    """Replace complete JSON file atomically; do not truncate a live state file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")
    temp_path = None
    try:
        fd, temp_path = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
        temp_path = None
    finally:
        if temp_path is not None:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass


def read_state(path: Path) -> tuple[dict[str, Any], str | None]:
    """Read historical JSON unchanged. Corruption must be observable, not hidden."""
    path = Path(path)
    if not path.exists():
        return {}, None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("state.json 必须是 JSON 对象")
        return value, None
    except (OSError, UnicodeError, ValueError) as exc:
        return {}, f"{type(exc).__name__}: {exc}"


def is_active_status(status: str) -> bool:
    return status in ACTIVE_STATES or status.startswith(ACTIVE_PREFIXES)


def interrupted_projection(original: dict[str, Any]) -> dict[str, Any]:
    """No disk write: preserves original status for later forensic/recovery work."""
    state = dict(original)
    status = str(state.get("status") or "")
    if not is_active_status(status):
        return state
    if status == "测试复核恢复中":
        replacement = "复核中断（已保留成果）"
        message = "复核线程已停止，不能未经授权再次调用 DeepSeek；请核对历史报告。"
    elif status.startswith("定向返工："):
        replacement = "定向返工中断（隔离成果保留）"
        message = "定向返工线程已停止；代码和测试证据保留，禁止自动重复调用 Codex。"
    else:
        replacement = "执行中断（需要检查）"
        message = "后台执行线程已不存在，原始产物和调用记录未自动修改；请诊断后决定是否恢复。"
    state["status"] = replacement
    state["current_agent"] = ""
    state["diagnostic_note"] = message
    state["interrupted_from"] = status
    return state


def project_team_status(state_path: Path, meta: dict[str, Any] | None, *,
                        now: float | None = None) -> dict[str, Any]:
    """Single shared projection for /status and /resume.

    Never launches agents, repairs state files, changes budgets, or syncs code.
    """
    state_path = Path(state_path)
    meta = meta or {}
    live = bool(meta.get("running"))
    thread = meta.get("thread")
    if thread is not None:
        live = live and bool(thread.is_alive())
    disk_state, read_error = read_state(state_path)
    memory_state = meta.get("state") or {}
    if not isinstance(memory_state, dict):
        memory_state = {}

    if live:
        state = disk_state or memory_state
        source = "disk_live" if disk_state else "memory_live"
    elif disk_state and not is_active_status(str(disk_state.get("status") or "")):
        state = disk_state
        source = "disk_final"
    elif memory_state and not is_active_status(str(memory_state.get("status") or "")):
        state = memory_state
        source = "memory_final"
    else:
        state = disk_state or memory_state
        source = "disk_stale" if disk_state else ("memory_stale" if memory_state else "missing")

    state = dict(state)
    if not live:
        state = interrupted_projection(state)
    if read_error:
        state["state_file_error"] = read_error
        if not state:
            state["status"] = "状态文件异常（只读检查）"
        state["diagnostic_note"] = (
            "state.json 读取失败，禁止自动恢复或重复执行。请保留文件并检查。"
        )
        if state.get("status") in (None, ""):
            state["status"] = "状态文件异常（只读检查）"

    seconds = None
    if state_path.is_file():
        try:
            seconds = max(0, int((time.time() if now is None else now) - state_path.stat().st_mtime))
        except OSError:
            pass

    return {
        "ok": True,
        "running": live,
        "state": state,
        "state_source": source,
        "last_progress_seconds": seconds,
        "state_file_error": read_error,
    }

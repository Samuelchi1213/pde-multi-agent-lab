"""B4-C2: durable FAKE stage reservations restricted to OS temp test folders.

NO production paths, model connections, real budgets, or runner callbacks.
A surviving in-flight claim is ALWAYS uncertain and is never replayed.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

STEP = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,79}$")
SHA = re.compile(r"^[0-9a-f]{64}$")
MAX_FAKE_RECEIPT = 16 * 1024


class MockJournalError(RuntimeError):
    """Anything uncertain must stop a mock, not retrigger a stage."""


def _safe_folder(directory: Path) -> Path:
    candidate = Path(directory)
    temp = Path(tempfile.gettempdir()).resolve()
    if (candidate.is_symlink() or not candidate.is_dir() or
            not candidate.name.startswith("b4c2_mock_")):
        raise MockJournalError("仅支持系统临时目录中的专用 C2 测试文件夹")
    candidate = candidate.resolve()
    if candidate.parent != temp:
        raise MockJournalError("拒绝真实项目、历史任务或嵌套路径")
    return candidate


def _atomic_json(path: Path, value: dict) -> None:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode("utf-8")
    temp_name = None
    try:
        fd, temp_name = tempfile.mkstemp(dir=path.parent, prefix=".c2_tmp_")
        with os.fdopen(fd, "wb") as f:
            f.write(encoded)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_name, path)
        temp_name = None
    finally:
        if temp_name is not None:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass


def _valid_step(step: str) -> bool:
    return isinstance(step, str) and STEP.fullmatch(step) is not None and ".." not in step


class MockDurableJournal:
    def __init__(self, directory: Path):
        self.directory = _safe_folder(directory)
        self.journal = self.directory / "mock_claim.json"
        self.lockfile = self.directory / ".mock_claim.lock"
        self.receipt = self.directory / "fake_receipt.bin"

    @contextmanager
    def _locked(self) -> Iterator[None]:
        # This is a TEST-ONLY exclusivity lock, not a production task lock.
        _safe_folder(self.directory)
        if self.lockfile.is_symlink():
            raise MockJournalError("模拟锁异常，禁止继续")
        try:
            fd = os.open(self.lockfile, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except OSError as exc:
            raise MockJournalError("模拟锁被占用或崩溃遗留；不自动抢锁") from exc
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write("fake-only lock\n")
                stream.flush()
                os.fsync(stream.fileno())
            yield
        finally:
            self.lockfile.unlink(missing_ok=True)

    def _read(self) -> dict | None:
        if self.journal.is_symlink() or self.receipt.is_symlink():
            raise MockJournalError("发现符号链接，禁止使用模拟凭据")
        if not self.journal.exists():
            return None
        try:
            data = json.loads(self.journal.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError) as exc:
            raise MockJournalError("模拟凭据损坏，禁止自动重试") from exc
        if (not isinstance(data, dict) or data.get("version") != 1 or
                data.get("state") not in ("in_flight", "completed", "uncertain") or
                not _valid_step(data.get("step_id")) or
                not isinstance(data.get("request_sha256"), str) or
                not SHA.fullmatch(data["request_sha256"]) or
                data.get("attempt") != 1):
            raise MockJournalError("模拟凭据结构异常")
        return data

    def inspect(self) -> dict:
        _safe_folder(self.directory)
        baseline = {"mode": "b4c2_test_only", "can_resume_now": False,
                    "resume_authorized": False, "external_model_calls": 0,
                    "real_project_writes": 0}
        if self.lockfile.exists():
            return {**baseline, "decision": "blocked_lock_or_crash"}
        try:
            data = self._read()
        except MockJournalError:
            return {**baseline, "decision": "blocked_corrupt_or_untrusted"}
        if data is None:
            return {**baseline, "decision": "not_started"}
        if data["state"] == "in_flight":
            return {**baseline, "decision": "blocked_uncertain_after_restart"}
        if data["state"] == "uncertain":
            return {**baseline, "decision": "blocked_manual_review"}
        evidence = data.get("receipt")
        if not isinstance(evidence, dict):
            return {**baseline, "decision": "blocked_missing_receipt"}
        try:
            payload = self.receipt.read_bytes()
        except OSError:
            return {**baseline, "decision": "blocked_missing_receipt"}
        if (not payload or len(payload) > MAX_FAKE_RECEIPT or
                len(payload) != evidence.get("bytes") or
                hashlib.sha256(payload).hexdigest() != evidence.get("sha256")):
            return {**baseline, "decision": "blocked_tampered_receipt"}
        return {**baseline, "decision": "reuse_verified_fake_receipt", "step_id": data["step_id"]}

    def reserve(self, step_id: str, request_sha256: str) -> dict:
        if not _valid_step(step_id) or not isinstance(request_sha256, str) or not SHA.fullmatch(request_sha256):
            raise MockJournalError("模拟步骤或请求哈希无效")
        with self._locked():
            if self._read() is not None:
                raise MockJournalError("已有持久步骤预约；绝不二次执行")
            _atomic_json(self.journal, {"version": 1, "state": "in_flight", "attempt": 1,
                                        "step_id": step_id, "request_sha256": request_sha256})
        return {"decision": "fake_reserved", "external_model_calls": 0, "resume_authorized": False}

    def finish(self, step_id: str, request_sha256: str, fake_receipt: bytes) -> dict:
        if (not _valid_step(step_id) or not isinstance(request_sha256, str) or
                not SHA.fullmatch(request_sha256) or not isinstance(fake_receipt, bytes) or
                not 0 < len(fake_receipt) <= MAX_FAKE_RECEIPT):
            raise MockJournalError("模拟完成凭据无效")
        with self._locked():
            record = self._read()
            if (record is None or record["state"] != "in_flight" or
                    record["step_id"] != step_id or record["request_sha256"] != request_sha256):
                raise MockJournalError("未找到匹配的进行中预约")
            if self.receipt.exists():
                raise MockJournalError("已有结果文件；拒绝覆盖")
            with self.receipt.open("xb") as f:
                f.write(fake_receipt)
                f.flush()
                os.fsync(f.fileno())
            record["state"] = "completed"
            record["receipt"] = {"sha256": hashlib.sha256(fake_receipt).hexdigest(),
                                 "bytes": len(fake_receipt)}
            _atomic_json(self.journal, record)
        return {"decision": "fake_completed", "external_model_calls": 0, "resume_authorized": False}

    def mark_uncertain(self) -> None:
        with self._locked():
            record = self._read()
            if record is None or record["state"] != "in_flight":
                raise MockJournalError("只能冻结未完成模拟预约")
            record["state"] = "uncertain"
            _atomic_json(self.journal, record)


def summarize_durable_mock_safety() -> dict:
    """Only safe to show in a GET response; never runs mock reservation."""
    return {"mode": "b4_c2_offline_only", "status": "blocked_for_production",
            "message": "C2 临时目录磁盘预约与进程中断模拟；生产真实续跑未开放",
            "can_resume_now": False, "resume_authorized": False,
            "external_model_calls": 0, "real_project_writes": 0}

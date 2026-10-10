"""B4-B1 test-only cross-process lease; refuses real run/project directories.

The lock may be created ONLY inside a purpose-named folder under the OS temp
directory. This is not a general purpose or production stage lock.
"""
from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class MockLeaseError(RuntimeError):
    pass


@contextmanager
def isolated_mock_lease(directory: Path) -> Iterator[None]:
    directory = Path(directory)
    temp_root = Path(tempfile.gettempdir()).resolve()
    if directory.is_symlink() or not directory.is_dir() or not directory.name.startswith("b4b1_mock_"):
        raise MockLeaseError("只能锁定测试专用临时文件夹")
    directory = directory.resolve()
    if directory == temp_root or not directory.is_relative_to(temp_root):
        raise MockLeaseError("不允许修改临时测试文件夹以外的任何位置")
    lock_file = directory / ".mock_stage_lease.lock"
    try:
        fd = os.open(lock_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise MockLeaseError("模拟执行已有锁或崩溃遗留锁，禁止第二次执行") from exc
    except OSError as exc:
        raise MockLeaseError("无法获取模拟执行独占锁") from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as writer:
            writer.write("B4-B1 mock only; never a paid-task lease\n")
        yield
    finally:
        try:
            lock_file.unlink()
        except FileNotFoundError:
            pass

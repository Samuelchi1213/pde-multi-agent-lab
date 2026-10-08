"""Safe, human-confirmed promotion of a QA-reviewed staging candidate.

The old sync_allowed_paths replaces entire directories. This module does NOT
use it: no delete propagation, no user-data sync, no writes before preview
approval. Changed original files get backed up before atomic replacements.
"""
import hashlib
import json
import os
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

PROGRAM_SUFFIXES = {".py", ".ps1", ".html", ".css", ".js", ".mjs", ".md", ".txt", ".yaml", ".yml"}
DATA_COMPONENTS = {
    "data", "database", "databases", "storage", "records", "uploads",
    "backups", "user_data", "userdata", "__pycache__", ".git", ".venv",
    "node_modules", "cache", "logs", "temp", "tmp",
}
RESTRICTED_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".csv", ".xlsx", ".xls", ".jsonl",
                       ".log", ".env", ".pem", ".key", ".p12", ".bak", ".zip", ".gz"}
MAX_FILE_BYTES = 5 * 1024 * 1024


def _file_sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _validate_tree_path(root, relative, must_exist=False):
    root = Path(root).resolve()
    if relative.is_absolute() or ".." in relative.parts or ":" in relative.as_posix():
        raise ValueError(f"无效项目内路径：{relative}")
    path = root
    for piece in relative.parts:
        path = path / piece
        if path.is_symlink():
            raise ValueError(f"发布路径含符号链接，已拒绝：{relative}")
    if must_exist and not path.is_file():
        raise ValueError(f"候选程序文件不存在：{relative}")
    if path.exists() and not path.is_file() and not path.is_dir():
        raise ValueError(f"不支持的特殊文件：{relative}")
    return path


def build_publish_plan(staging, project, allowed_paths):
    staging = Path(staging).resolve()
    project = Path(project).resolve()
    if not staging.is_dir() or not project.is_dir() or staging == project:
        raise ValueError("真实项目或隔离工作区无效")
    allowed = [str(p).replace("\\", "/").strip("/") for p in allowed_paths]
    allowed = sorted(set(allowed))
    if not allowed or any(v not in {"src", "tests", "docs"} for v in allowed):
        raise ValueError("仅允许发布已授权的 src/tests/docs")
    changes, skipped_data, blocked = [], [], []
    for scope in allowed:
        src_root = _validate_tree_path(staging, Path(scope))
        _validate_tree_path(project, Path(scope))
        if not src_root.exists():
            continue
        if not src_root.is_dir():
            blocked.append(f"{scope} 不是目录")
            continue
        for src in sorted(src_root.rglob("*")):
            if src.is_dir() and not src.is_symlink():
                continue
            rel = src.relative_to(staging)
            normalized = rel.as_posix()
            if any(p.lower() in DATA_COMPONENTS for p in rel.parts[:-1]):
                skipped_data.append(normalized)
                continue
            ext = src.suffix.lower()
            if ext == ".json" and normalized != "docs/runtime.json":
                skipped_data.append(normalized)
                continue
            if ext in RESTRICTED_SUFFIXES or (
                ext not in PROGRAM_SUFFIXES and normalized != "docs/runtime.json"
            ):
                skipped_data.append(normalized)
                continue
            try:
                safe_src = _validate_tree_path(staging, rel, must_exist=True)
                safe_dst = _validate_tree_path(project, rel)
                if safe_dst.exists() and not safe_dst.is_file():
                    blocked.append(f"{normalized} 目标不是普通文件")
                    continue
                if safe_src.stat().st_size > MAX_FILE_BYTES:
                    blocked.append(f"{normalized} 超过单文件 5MB 限制")
                    continue
                candidate_sha = _file_sha(safe_src)
                target_sha = _file_sha(safe_dst) if safe_dst.exists() else None
                if candidate_sha == target_sha:
                    continue
                changes.append({
                    "path": normalized, "action": "replace" if target_sha else "add",
                    "candidate_sha": candidate_sha, "target_sha": target_sha,
                    "bytes": safe_src.stat().st_size,
                })
            except (OSError, ValueError) as exc:
                blocked.append(f"{normalized}: {exc}")
    rev_payload = {
        "staging": str(staging), "project": str(project),
        "allowed": allowed, "changes": changes, "blocked": blocked,
    }
    revision = hashlib.sha256(
        json.dumps(rev_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return {
        "revision": revision, "changes": changes, "skipped_data": skipped_data,
        "blocked": blocked, "staging": str(staging), "project": str(project),
        "allowed": allowed,
    }


def apply_publish_plan(staging, project, allowed_paths, revision, backup_base, draft_id):
    """Backup replaced files; check exact revision; never remove unrelated files."""
    if not re.fullmatch(r"[a-zA-Z0-9_-]{4,100}", draft_id):
        raise ValueError("任务 ID 不安全")
    plan = build_publish_plan(staging, project, allowed_paths)
    if plan["revision"] != revision:
        raise ValueError("候选文件或真实项目在预览后已变化，请重新预览再确认")
    if plan["blocked"]:
        raise ValueError("发现不安全的发布文件，已拒绝发布："+str(plan["blocked"][:3]))
    if not plan["changes"]:
        raise ValueError("没有需要发布的程序文件，不应改变验收状态")
    backup_dir = Path(backup_base) / draft_id / (
        datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    )
    backup_dir.mkdir(parents=True, exist_ok=False)
    originals = backup_dir / "original"
    manifest = {
        "draft_id": draft_id, "project": plan["project"],
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "changes": plan["changes"], "skipped_data": plan["skipped_data"],
        "note": "只替换列出的程序文件；未删除目录及任何用户数据文件。",
    }
    (backup_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Finish *all* backups before touching the real project.
    for change in plan["changes"]:
        if change["action"] != "replace":
            continue
        rel = Path(change["path"])
        dst = _validate_tree_path(project, rel, must_exist=True)
        if _file_sha(dst) != change["target_sha"]:
            raise ValueError(f"备份前真实项目已变化：{change['path']}")
        backup_target = originals / rel
        backup_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(dst, backup_target)

    applied = []
    temp_path = None
    try:
        for change in plan["changes"]:
            rel = Path(change["path"])
            src = _validate_tree_path(staging, rel, must_exist=True)
            dst = _validate_tree_path(project, rel)
            if _file_sha(src) != change["candidate_sha"]:
                raise ValueError(f"发布过程中候选代码被修改：{rel}")
            present_hash = _file_sha(dst) if dst.exists() else None
            if present_hash != change["target_sha"]:
                raise ValueError(f"发布过程中目标代码被修改：{rel}")
            dst.parent.mkdir(parents=True, exist_ok=True)
            fd, temp_path = tempfile.mkstemp(prefix=".pde-release-", dir=dst.parent)
            with os.fdopen(fd, "wb") as stream, src.open("rb") as source:
                shutil.copyfileobj(source, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_path, dst)
            temp_path = None
            applied.append(change)
        result = {
            "backup_path": str(backup_dir), "files_changed": len(applied),
            "files_added": sum(x["action"] == "add" for x in applied),
            "files_replaced": sum(x["action"] == "replace" for x in applied),
            "skipped_data_files": len(plan["skipped_data"]),
            "changed_paths": [x["path"] for x in applied],
        }
        (backup_dir / "release_result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return result
    except Exception:
        # Roll back ONLY program files modified by this operation.
        for change in reversed(applied):
            dst = _validate_tree_path(project, Path(change["path"]))
            if change["action"] == "add":
                if dst.is_file():
                    dst.unlink()
            else:
                backup = originals / Path(change["path"])
                if backup.is_file():
                    shutil.copy2(backup, dst)
        raise
    finally:
        if temp_path and Path(temp_path).exists():
            Path(temp_path).unlink()

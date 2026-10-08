"""Manual-acceptance rework: one Codex call plus independent local regression.

This module deliberately does not call DeepSeek, the full team runner,
or any real-project sync. It uses a NEW staging directory based on the
currently published project, not the original developer workspace.
"""
import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

from team_executor import run_codex, run_python_tests

MIN_REGRESSION_TESTS = 32
IGNORED_DIRS = {
    ".git", ".venv", "venv", "__pycache__", "node_modules", "dist",
    "build", ".next", ".cache", ".pytest_cache", "logs", "uploads",
    "backups", "secrets",
}
IGNORED_FILE_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".pyc", ".pyo", ".key", ".pem", ".p12", ".log", ".env"}
FIX_REQUIREMENTS = """
修复仅限以下三个已验收发现的问题：

1. 【历史查询与追加审计】辅导员关闭异常后可查询该已关闭异常；支持学生、日期、
   处理状态筛选。展示考勤观察、升级原因、生活委员初核、辅导员第一次/第二次
   核实进展和关闭结论，以及操作者和时间。每次核实只能追加历史，不得覆盖
   既有条目；旧演示记录应兼容、可查询；页面刷新后仍可查看。

2. 【日期校验】本地时区规则下不允许新提交未来日期的晚间考勤；核实时间既不能
   早于对应考勤日期，也不能晚于实际当前时间。前后端均检查并清楚反馈。
   不删除或悄悄篡改已存在的未来日期测试记录：旧异常保留并提示日期待核查。
   对当天的核实允许当天任何已发生的合理时间，不能错误按考勤当天 23:59 校验。

3. 【复核默认值】“同时关闭异常”默认不勾选；未勾选保存进展不关闭、
   勾选关闭须有明确结论和确认提示；关闭后统计正确且旧返校事实不变。

必须补充 >=3 个独立自动化测试，验证以上缺陷和保存后历史持久化；
原返校 v1、原晚间考勤 v1 既有测试均继续通过。
"""


def utc_like_now():
    return datetime.now().isoformat(timespec="seconds")


def _atomic_json(path, value):
    # Share the same crash-safe atomic writer with all other PDE task states.
    from task_state import atomic_write_json
    atomic_write_json(Path(path), value)


def last_rejection(state):
    for event in reversed(state.get("timeline") or []):
        if event.get("action") == "人工验收退回返工":
            detail = event.get("detail") or {}
            if isinstance(detail, dict):
                return str(detail.get("note") or "").strip()
            return str(detail).strip()
    return ""


def _safe_snapshot(source: Path, target: Path):
    """Copy code/test/docs only. Do not copy src/data or private/user data."""
    source = source.resolve()
    if not source.is_dir() or target.exists():
        raise RuntimeError("原始项目不存在或专用返工隔离工作区已存在")
    target.mkdir(parents=True, exist_ok=False)
    skipped = 0
    copied = 0
    allowed_roots = ("src", "tests", "docs")
    for root_name in allowed_roots:
        top = source / root_name
        if not top.is_dir() or top.is_symlink():
            continue
        for base, dirnames, filenames in os.walk(top, followlinks=False):
            base_path = Path(base)
            rel_dir = base_path.relative_to(source)
            dirnames[:] = [
                d for d in dirnames if d not in IGNORED_DIRS
                and d.lower() != "data" and not (base_path / d).is_symlink()
            ]
            for filename in filenames:
                item = base_path / filename
                if item.is_symlink() or item.suffix.lower() in IGNORED_FILE_SUFFIXES:
                    skipped += 1
                    continue
                if filename.startswith(".env") or filename.lower() in {
                    "return_status.json", "attendance_records.json", "credentials.json"
                }:
                    skipped += 1
                    continue
                # JSON files inside src/ may be business data, not code.
                if item.suffix.lower() in {".json", ".jsonl", ".csv", ".xlsx"} and rel_dir.parts[0] == "src":
                    skipped += 1
                    continue
                if item.stat().st_size > 5 * 1024 * 1024:
                    skipped += 1
                    continue
                dest = target / rel_dir / filename
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, dest)
                copied += 1
    for base in ("pyproject.toml", "requirements.txt", "README.md"):
        file = source / base
        if file.is_file() and not file.is_symlink() and file.stat().st_size <= 1000000:
            shutil.copy2(file, target / base)
    return {"copied_files": copied, "excluded_files": skipped, "source": str(source)}


def _event(state_file, state, agent, action, detail=None):
    state.setdefault("timeline", []).append({
        "time": utc_like_now(), "agent": agent, "action": action, "detail": detail,
    })
    _atomic_json(state_file, state)


def run_targeted_rework(root: Path, draft_id: str, draft: dict, state: dict,
                        source_project: Path):
    run_dir = root / "orchestrator_v1" / "dynamic_runs" / draft_id
    state_file = run_dir / "state.json"
    rework_dir = run_dir / "manual_rework_1"
    workspace = rework_dir / "workspace"
    try:
        stats = _safe_snapshot(source_project, workspace)
        state["manual_rework_workspace"] = str(workspace.relative_to(root))
        _event(state_file, state, "权限控制器", "已建立独立返工工作区，真实数据未复制", stats)
        prompt = f"""
你是唯一被授权执行本轮任务的 Codex 开发智能体。
这是项目负责人在浏览器中人工验收之后提出的三项定向返工，禁止启动新项目。

原始开发目标：{str(draft.get('goal',''))[:4500]}
项目负责人上次退回的反馈：{last_rejection(state)[:5500]}

{FIX_REQUIREMENTS}

执行限制：
- 仅修改当前隔离工作区 src/、tests/、docs/ 内必要程序和测试文件。
- 不写真实项目，不做 Git commit，不调用 DeepSeek，不删除任何 JSON 学生数据。
- 先阅读现有 Python Web 服务、页面 JavaScript、考勤 workflow 及相关单元测试。
- 只修三个缺陷，尽量保留此前已验收的流程和 UI，不引入其他业务模块。
- 历史记录须尽量兼容现有数据结构，已有记录不能静默丢失。
- 不能虚构成功；修改完成必须运行全部 Python unittest，并新增针对性回归测试。
- 若部分缺陷无法修复，在结构化交付中明确写出未完成事项。
- 交付格式严格遵循给定 JSON Schema。
"""
        state["status"] = "定向返工：Codex 修改中"
        state["current_agent"] = "开发智能体"
        _event(state_file, state, "开发智能体", "开始单次 Codex 定向修改（三项验收缺陷）")
        rework_dir.mkdir(parents=True, exist_ok=True)
        schema = root / "orchestrator_v1" / "schemas" / "dynamic_codex_schema.json"
        result_file = rework_dir / "codex_rework_delivery.json"
        from codex_checkpoints import execute_codex_step
        delivery, reused = execute_codex_step(
            run_dir=rework_dir,
            workspace=workspace,
            step_id="manual.rework.1",
            prompt=prompt,
            schema_path=schema,
            result_path=result_file,
            executor=run_codex,
        )
        if reused:
            # Normal manual rework never auto-restarts. This is defensive only.
            _event(state_file,state,"系统","复用已验证的定向返工回执",{
                "step_id":"manual.rework.1","model_called":False
            })
        state["codex_calls"] = int(state.get("codex_calls") or 0) + 1
        state["manual_rework_delivery"] = str(result_file.relative_to(root))
        _event(state_file, state, "开发智能体", "定向修复代码已交付", {
            "summary": delivery.get("summary") if isinstance(delivery,dict) else "",
            "files_changed": delivery.get("files_changed",[]) if isinstance(delivery,dict) else [],
        })
        state["status"] = "定向返工：独立回归测试中"
        state["current_agent"] = "系统验证器"
        _event(state_file, state, "系统验证器", "执行完整 unittest 回归测试")
        evidence = run_python_tests(workspace)
        rework_dir.mkdir(parents=True,exist_ok=True)
        _atomic_json(rework_dir / "regression_tests.json", evidence)
        summary = (evidence.get("stderr") or "") + "\n" + (evidence.get("stdout") or "")
        found = [int(x) for x in re.findall(r"Ran\s+(\d+)\s+tests?", summary)]
        test_count = max(found) if found else 0
        success = evidence.get("returncode") == 0 and test_count >= MIN_REGRESSION_TESTS
        state["manual_rework_test_evidence"] = str(
            (rework_dir/"regression_tests.json").relative_to(root)
        )
        state["manual_rework_tests_passed"] = success
        state["manual_rework_test_count"] = test_count
        state["status"] = (
            "定向返工测试通过（待安全发布）" if success else "定向返工测试未通过"
        )
        state["current_agent"] = ""
        _event(state_file, state, "系统验证器", "定向返工独立回归测试结束", {
            "returncode": evidence.get("returncode"),
            "test_count": test_count, "minimum_required": MIN_REGRESSION_TESTS,
            "summary": summary[-2200:], "passed": success,
            "note": "仅 Python 自动回归，无二次 DeepSeek QA；须在浏览器再次人工验收。",
        })
    except Exception as exc:
        state["status"] = "定向返工失败（隔离成果保留）"
        state["current_agent"] = ""
        state["manual_rework_error"] = str(exc)
        _event(state_file, state, "系统", "定向返工失败，未执行真实项目同步", str(exc))
    return state

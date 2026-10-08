"""Pure, offline regression tests for PDE's task-state foundation.

Run: python -m unittest discover -s web_console_v1 -p test_task_state.py -v
Does not start Codex, DeepSeek, the web server, or a real project.
"""
import json
import tempfile
import unittest
from pathlib import Path

from task_state import atomic_write_json, project_team_status, read_state


class TestAtomicRunState(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / "dynamic_runs" / "DRAFT-TEST" / "state.json"

    def tearDown(self):
        self.folder.cleanup()

    def test_atomic_save_preserves_chinese_and_existing_fields(self):
        state = {"status": "等待人工验收", "timeline": [{"action": "回归测试"}], "deepseek_tokens": 29556}
        atomic_write_json(self.path, state)
        loaded, err = read_state(self.path)
        self.assertIsNone(err)
        self.assertEqual(loaded, state)
        self.assertEqual(list(self.path.parent.glob(".state.json.*.tmp")), [])

    def test_live_run_prefers_updated_disk_progress(self):
        atomic_write_json(self.path, {"status": "执行中", "current_agent": "测试智能体", "codex_calls": 2})
        result = project_team_status(self.path, {"running": True, "state": {"status": "准备中"}})
        self.assertTrue(result["running"])
        self.assertEqual(result["state"]["current_agent"], "测试智能体")
        self.assertEqual(result["state_source"], "disk_live")

    def test_crash_does_not_fake_running_or_modify_original_file(self):
        original = {"status": "执行中", "deepseek_tokens": 21026, "timeline": [{"action": "开始独立复核"}]}
        atomic_write_json(self.path, original)
        result = project_team_status(self.path, {"running": False, "state": original})
        self.assertFalse(result["running"])
        self.assertEqual(result["state"]["status"], "执行中断（需要检查）")
        self.assertEqual(result["state"]["interrupted_from"], "执行中")
        self.assertEqual(read_state(self.path)[0], original)

    def test_qa_retry_is_not_automatically_started(self):
        original = {"status": "测试复核恢复中", "qa_resume_attempted": True}
        atomic_write_json(self.path, original)
        result = project_team_status(self.path, {"running": False})
        self.assertEqual(result["state"]["status"], "复核中断（已保留成果）")
        self.assertTrue(result["state"]["qa_resume_attempted"])

    def test_manual_rework_interrupted_without_duplicating_codex(self):
        atomic_write_json(self.path, {"status": "定向返工：Codex 修改中", "codex_calls": 1})
        result = project_team_status(self.path, {"running": False})
        self.assertEqual(result["state"]["status"], "定向返工中断（隔离成果保留）")
        self.assertEqual(result["state"]["codex_calls"], 1)

    def test_disk_final_is_authoritative_over_stale_memory(self):
        atomic_write_json(self.path, {"status": "等待人工验收", "candidate_synced": True})
        result = project_team_status(self.path, {"running": False, "state": {"status": "执行失败"}})
        self.assertEqual(result["state"]["status"], "等待人工验收")
        self.assertEqual(result["state_source"], "disk_final")

    def test_terminal_memory_overrides_stale_running_disk(self):
        atomic_write_json(self.path, {"status": "执行中"})
        result = project_team_status(self.path, {"running": False, "state": {
            "status": "执行失败", "error": "模型超时"
        }})
        self.assertEqual(result["state"]["status"], "执行失败")
        self.assertEqual(result["state"]["error"], "模型超时")
        self.assertEqual(result["state_source"], "memory_final")

    def test_stopped_thread_overrides_stale_running_flag(self):
        class FinishedWorker:
            def is_alive(self):
                return False

        atomic_write_json(self.path, {"status": "执行中", "codex_calls": 1})
        result = project_team_status(self.path, {
            "running": True,
            "thread": FinishedWorker(),
            "state": {"status": "执行中"},
        })
        self.assertFalse(result["running"])
        self.assertEqual(result["state"]["status"], "执行中断（需要检查）")

    def test_pending_budget_requires_approval_not_interruption(self):
        atomic_write_json(self.path, {"status": "等待预算确认", "deepseek_tokens": 29556})
        result = project_team_status(self.path, {"running": False})
        self.assertEqual(result["state"]["status"], "等待预算确认")

    def test_corrupted_file_is_reported_not_silently_ignored(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text('{"status":', encoding="utf-8")
        result = project_team_status(self.path, {"running": False})
        self.assertFalse(result["running"])
        self.assertIn("state_file_error", result["state"])
        self.assertEqual(result["state"]["status"], "状态文件异常（只读检查）")

    def test_invalid_json_value_rejected(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text('["wrong object"]', encoding="utf-8")
        _, err = read_state(self.path)
        self.assertIsNotNone(err)

    def test_atomic_serialization_failure_does_not_destroy_old_state(self):
        atomic_write_json(self.path, {"status": "已完成"})
        with self.assertRaises(TypeError):
            atomic_write_json(self.path, {"bad": set(["not json"])})
        self.assertEqual(read_state(self.path)[0]["status"], "已完成")

    def test_missing_state_does_not_claim_success(self):
        result = project_team_status(self.path, None)
        self.assertFalse(result["running"])
        self.assertEqual(result["state_source"], "missing")
        self.assertEqual(result["state"], {})


if __name__ == "__main__":
    unittest.main()

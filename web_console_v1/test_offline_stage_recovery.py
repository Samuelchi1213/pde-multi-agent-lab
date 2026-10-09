"""B4-A 100% in-memory stage simulation; no networking or filesystem operations."""
import copy
import unittest
from offline_stage_recovery import simulate_stage_recovery


def preview():
    return {
        "ok": True, "draft_id": "DRAFT-TEST-001", "task_status": "执行中断（需要检查）",
        "mode": "read_only_no_model_calls", "can_resume_now": False, "resume_authorized": False,
        "steps": [
            {"step_id": "product.spec", "status": "verified"},
            {"step_id": "architecture.plan", "status": "verified"},
            {"step_id": "developer.first", "status": "not_started"},
            {"step_id": "qa.review.0", "status": "not_started"},
        ],
        "verified_steps": ["product.spec", "architecture.plan"],
        "not_started_steps": ["developer.first", "qa.review.0"],
        "blocked_steps": [], "blockers": [],
    }


class TestOfflineStageRecovery(unittest.TestCase):
    def check_guard(self, result):
        self.assertFalse(result["can_resume_now"])
        self.assertFalse(result["resume_authorized"])
        self.assertEqual(result["external_model_calls"], 0)
        self.assertEqual(result["real_project_writes"], 0)

    def test_verified_prefix_only_and_no_side_effects(self):
        p = preview()
        unchanged = copy.deepcopy(p)
        r = simulate_stage_recovery(p)
        self.assertEqual(r["status"], "simulated")
        self.assertEqual(r["start_step"], "developer.first")
        self.assertEqual(r["reused_steps"], ["product.spec", "architecture.plan"])
        self.assertEqual(r["simulated_steps"], ["developer.first", "qa.review.0"])
        self.assertEqual([x["transition"] for x in r["trace"]],
                         ["reuse_verified_receipt"] * 2 + ["simulate_only_no_call"] * 2)
        self.assertEqual(p, unchanged)
        self.check_guard(r)

    def test_repeated_simulation_is_deterministic(self):
        p = preview()
        self.assertEqual(simulate_stage_recovery(p), simulate_stage_recovery(p))

    def test_cannot_choose_later_stage(self):
        r = simulate_stage_recovery(preview(), start_step="qa.review.0")
        self.assertEqual(r["status"], "blocked")
        self.check_guard(r)

    def test_explicit_correct_start_only_simulates(self):
        r = simulate_stage_recovery(preview(), start_step="developer.first")
        self.assertEqual(r["status"], "simulated")
        self.check_guard(r)

    def test_blocked_checkpoint_cannot_resume(self):
        p = preview()
        p["steps"][2]["status"] = "blocked"
        r = simulate_stage_recovery(p)
        self.assertEqual(r["status"], "blocked")
        self.check_guard(r)

    def test_legacy_missing_draft_is_blocked(self):
        p = preview()
        p["steps"] = []
        p["blockers"] = ["原始任务草案不存在或无法读取"]
        self.assertEqual(simulate_stage_recovery(p)["status"], "blocked")

    def test_completed_task_cannot_resume(self):
        p = preview()
        p["task_status"] = "已完成"
        self.assertEqual(simulate_stage_recovery(p)["status"], "blocked")

    def test_pending_budget_cannot_resume(self):
        p = preview()
        p["task_status"] = "等待预算确认"
        self.assertEqual(simulate_stage_recovery(p)["status"], "blocked")

    def test_active_running_task_cannot_resume(self):
        p = preview()
        p["task_status"] = "执行中"
        self.assertEqual(simulate_stage_recovery(p)["status"], "blocked")

    def test_validation_demo_cannot_resume(self):
        p = preview()
        p["draft_id"] = "VALIDATE-REWORK-12345"
        self.assertEqual(simulate_stage_recovery(p)["status"], "blocked")

    def test_tampered_preview_authorization_is_rejected(self):
        p = preview()
        p["resume_authorized"] = True
        self.assertEqual(simulate_stage_recovery(p)["status"], "blocked")

    def test_inconsistent_summary_is_rejected(self):
        p = preview()
        p["verified_steps"] = ["product.spec"]
        self.assertEqual(simulate_stage_recovery(p)["status"], "blocked")

    def test_gap_in_verified_prefix_is_rejected(self):
        p = preview()
        p["steps"][1]["status"] = "not_started"
        p["steps"][2]["status"] = "verified"
        p["verified_steps"] = ["product.spec", "developer.first"]
        p["not_started_steps"] = ["architecture.plan", "qa.review.0"]
        self.assertEqual(simulate_stage_recovery(p)["status"], "blocked")

    def test_duplicate_step_is_rejected(self):
        p = preview()
        p["steps"].append(p["steps"][2].copy())
        self.assertEqual(simulate_stage_recovery(p)["status"], "blocked")

    def test_out_of_order_stage_is_rejected(self):
        p = preview()
        p["steps"][0], p["steps"][1] = p["steps"][1], p["steps"][0]
        self.assertEqual(simulate_stage_recovery(p)["status"], "blocked")

    def test_unknown_stage_is_rejected(self):
        p = preview()
        p["steps"][2]["step_id"] = "paid.transfer"
        self.assertEqual(simulate_stage_recovery(p)["status"], "blocked")

    def test_no_verified_steps_is_not_resumption(self):
        p = preview()
        for x in p["steps"]:
            x["status"] = "not_started"
        p["verified_steps"] = []
        p["not_started_steps"] = [x["step_id"] for x in p["steps"]]
        self.assertEqual(simulate_stage_recovery(p)["status"], "blocked")

    def test_no_pending_steps_is_not_resumption(self):
        p = preview()
        for x in p["steps"]:
            x["status"] = "verified"
        p["verified_steps"] = [x["step_id"] for x in p["steps"]]
        p["not_started_steps"] = []
        self.assertEqual(simulate_stage_recovery(p)["status"], "blocked")

    def test_later_rework_round_is_supported_only_in_order(self):
        p = preview()
        p["steps"].extend([
            {"step_id": "developer.rework.1", "status": "not_started"},
            {"step_id": "qa.review.1", "status": "not_started"},
        ])
        p["not_started_steps"].extend(["developer.rework.1", "qa.review.1"])
        r = simulate_stage_recovery(p)
        self.assertEqual(r["status"], "simulated")
        self.assertEqual(r["simulated_steps"][-2:], ["developer.rework.1", "qa.review.1"])
        self.check_guard(r)

    def test_blocked_preview_summary_rejected(self):
        p = preview()
        p["blocked_steps"] = ["qa.review.0"]
        self.assertEqual(simulate_stage_recovery(p)["status"], "blocked")

    def test_bad_preview_shape_rejected(self):
        r = simulate_stage_recovery(None)
        self.assertEqual(r["status"], "blocked")
        self.check_guard(r)


if __name__ == "__main__":
    unittest.main()

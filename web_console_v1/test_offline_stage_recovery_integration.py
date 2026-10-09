"""Integration checks: B3's real checkpoint reader feeds the B4-A offline planner."""
import hashlib
import tempfile
import unittest
from pathlib import Path

from recovery_preview import build_recovery_preview
from offline_stage_recovery import simulate_stage_recovery
from step_checkpoints import reserve_step, finish_step
from task_state import atomic_write_json


class TestStageRecoveryFromSavedEvidence(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.run = Path(self.temp.name) / "DRAFT-INTEGRATION"
        self.run.mkdir()
        (self.run / "workspace").mkdir()
        self.state = {"draft_id": self.run.name, "status": "执行中断（需要检查）",
                      "deepseek_calls": 1, "codex_calls": 0, "rework_count": 0}
        self.draft = {"analysis": {"required_agents": ["产品智能体", "架构智能体"]}}
        atomic_write_json(self.run / "state.json", self.state)

    def tearDown(self):
        self.temp.cleanup()

    def save_product_evidence(self):
        inputs = {"role": "产品智能体", "sample": "offline"}
        self.assertEqual(reserve_step(self.run, "product.spec", inputs)["decision"], "reserved")
        artifact = self.run / "artifacts" / "product.json"
        artifact.parent.mkdir()
        atomic_write_json(artifact, {"step_id": "product.spec", "result": {"status": "pass"},
                                     "usage": {"total_tokens": 1}})
        finish_step(self.run, "product.spec", inputs, "artifacts/product.json")

    def snapshot(self):
        return {str(p.relative_to(self.run)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in self.run.rglob("*") if p.is_file()}

    def preview(self):
        return build_recovery_preview(self.run, self.draft, self.state)

    def test_saved_proof_yields_next_step_without_writes(self):
        self.save_product_evidence()
        before = self.snapshot()
        p = self.preview()
        result = simulate_stage_recovery(p)
        self.assertEqual(result["status"], "simulated")
        self.assertEqual(result["start_step"], "architecture.plan")
        self.assertEqual(result["reused_steps"], ["product.spec"])
        self.assertEqual(before, self.snapshot())
        self.assertFalse(result["resume_authorized"])

    def test_missing_receipt_propagates_blocker(self):
        self.save_product_evidence()
        (self.run / "artifacts" / "product.json").unlink()
        p = self.preview()
        self.assertIn("product.spec", p["blocked_steps"])
        self.assertEqual(simulate_stage_recovery(p)["status"], "blocked")

    def test_unapproved_budget_stops_even_with_valid_receipt(self):
        self.save_product_evidence()
        self.state["status"] = "等待预算确认"
        self.state["budget_approval"] = {"requested_extra": 1000}
        atomic_write_json(self.run / "state.json", self.state)
        self.assertEqual(simulate_stage_recovery(self.preview())["status"], "blocked")

    def test_missing_draft_never_inferrs_paid_stages(self):
        self.save_product_evidence()
        p = build_recovery_preview(self.run, None, self.state)
        result = simulate_stage_recovery(p)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["simulated_steps"], [])
        self.assertEqual(result["external_model_calls"], 0)


if __name__ == "__main__":
    unittest.main()

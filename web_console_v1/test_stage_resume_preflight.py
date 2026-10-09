"""B4-B0 preflight uses only disposable fake task files and cannot execute models."""
import hashlib
import tempfile
import unittest
from pathlib import Path

from stage_resume_preflight import build_resume_preflight
from step_checkpoints import reserve_step, finish_step
from task_state import atomic_write_json


class TestResumePreflight(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name) / "orchestrator_v1"
        self.run = root / "dynamic_runs" / "DRAFT-CONTRACT"
        self.run.mkdir(parents=True)
        (self.run / "workspace").mkdir()
        self.draft_path = root / "runtime" / "drafts" / "DRAFT-CONTRACT.json"
        self.draft = {"goal": "fake", "confirmed": True,
                      "analysis": {"required_agents": ["产品智能体", "架构智能体"]},
                      "use_real_project": False}
        self.state = {"draft_id": "DRAFT-CONTRACT",
                      "status": "执行中断（需要检查)",
                      "deepseek_calls": 1, "codex_calls": 0, "rework_count": 0}
        self.state["status"] = "执行中断（需要检查）"
        self.save()
        self.add_receipt()

    def tearDown(self):
        self.temp.cleanup()

    def save(self):
        atomic_write_json(self.draft_path, self.draft)
        atomic_write_json(self.run / "state.json", self.state)

    def add_receipt(self):
        request = {"role": "产品智能体", "offline": True}
        self.assertEqual(reserve_step(self.run, "product.spec", request)["decision"], "reserved")
        proof = self.run / "artifacts" / "product.json"
        atomic_write_json(proof, {"step_id": "product.spec", "result": {"status": "pass"},
                                  "usage": {"total_tokens": 5}})
        finish_step(self.run, "product.spec", request, "artifacts/product.json")

    def hashes(self):
        return {str(p.relative_to(self.run)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in self.run.rglob("*") if p.is_file()}

    def assert_stopped(self, result):
        self.assertEqual(result["status"], "blocked")
        self.assertFalse(result["can_resume_now"])
        self.assertFalse(result["resume_authorized"])
        self.assertEqual(result["external_model_calls"], 0)
        self.assertEqual(result["real_project_writes"], 0)

    def test_valid_original_evidence_creates_review_candidate_only(self):
        before = self.hashes()
        r = build_resume_preflight(self.run)
        self.assertEqual(r["status"], "review_candidate")
        self.assertEqual(r["next_step"], "architecture.plan")
        self.assertEqual(r["verified_prefix"], ["product.spec"])
        self.assertTrue(r["requires_owner_approval"])
        self.assertEqual(len(r["checksums"]), 3)
        self.assertEqual(before, self.hashes())
        self.assertFalse(r["resume_authorized"])

    def test_repeating_preflight_does_not_change_files(self):
        first = build_resume_preflight(self.run)
        second = build_resume_preflight(self.run)
        self.assertEqual(first, second)

    def test_live_worker_blocks(self):
        self.assert_stopped(build_resume_preflight(self.run, running=True))

    def test_unapproved_budget_blocks(self):
        self.state["status"] = "等待预算确认"
        self.state["budget_approval"] = {"requested_extra": 1200}
        self.save()
        self.assert_stopped(build_resume_preflight(self.run))

    def test_publication_record_blocks(self):
        self.state["candidate_synced"] = True
        self.save()
        self.assert_stopped(build_resume_preflight(self.run))

    def test_completed_state_blocks(self):
        self.state["status"] = "已完成"
        self.save()
        self.assert_stopped(build_resume_preflight(self.run))

    def test_deleted_receipt_blocks(self):
        (self.run / "artifacts" / "product.json").unlink()
        self.assert_stopped(build_resume_preflight(self.run))

    def test_modified_receipt_blocks(self):
        (self.run / "artifacts" / "product.json").write_text('{"tampered":true}', encoding="utf-8")
        self.assert_stopped(build_resume_preflight(self.run))

    def test_missing_original_draft_blocks(self):
        self.draft_path.unlink()
        self.assert_stopped(build_resume_preflight(self.run))

    def test_unconfirmed_draft_blocks(self):
        self.draft["confirmed"] = False
        self.save()
        self.assert_stopped(build_resume_preflight(self.run))

    def test_missing_ledger_blocks(self):
        (self.run / "step_checkpoints.json").unlink()
        self.assert_stopped(build_resume_preflight(self.run))

    def test_real_project_connection_requires_separate_authority(self):
        self.draft["use_real_project"] = True
        self.save()
        self.assert_stopped(build_resume_preflight(self.run))

    def test_mismatched_original_id_blocks(self):
        self.state["draft_id"] = "DRAFT-OTHER"
        self.save()
        self.assert_stopped(build_resume_preflight(self.run))

    def test_nonnumeric_or_negative_paid_call_counters_block(self):
        self.state["codex_calls"] = -1
        self.save()
        self.assert_stopped(build_resume_preflight(self.run))
        self.state["codex_calls"] = "1"
        self.save()
        self.assert_stopped(build_resume_preflight(self.run))


if __name__ == "__main__":
    unittest.main()

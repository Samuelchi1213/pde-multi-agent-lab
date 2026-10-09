"""B3 read-only task recovery previews: no API, no real project, no writes."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from codex_checkpoints import program_snapshot
from recovery_preview import build_recovery_preview
from step_checkpoints import reserve_step, finish_step
from task_state import atomic_write_json


class TestRecoveryPreview(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.run=self.root/"DRAFT-RECOVERY"
        self.run.mkdir()
        self.workspace=self.run/"workspace"
        self.workspace.mkdir()
        self.state={"draft_id":"DRAFT-RECOVERY","status":"执行中断（需要检查）",
                    "deepseek_calls":0,"codex_calls":0,"rework_count":0}
        atomic_write_json(self.run/"state.json",self.state)
        self.draft={"goal":"fake only","analysis":{"required_agents":
                    ["产品智能体","架构智能体","开发智能体","测试智能体"]}}

    def tearDown(self):
        self.temp.cleanup()

    def preview(self,running=False):
        return build_recovery_preview(self.run,self.draft,self.state,running=running)

    def save_state(self):
        atomic_write_json(self.run/"state.json",self.state)

    def deepseek(self,step="product.spec",role="产品智能体"):
        inputs={"role":role,"fake":True}
        reserved=reserve_step(self.run,step,inputs)
        self.assertEqual(reserved["decision"],"reserved")
        artifacts=self.run/"artifacts"
        artifacts.mkdir(exist_ok=True)
        receipt=artifacts/f"deepseek_step_{step}.json"
        atomic_write_json(receipt,{"step_id":step,"role":role,
                                   "result":{"status":"pass"},"usage":{"total_tokens":5}})
        finish_step(self.run,step,inputs,receipt.relative_to(self.run).as_posix())

    def codex(self,step="developer.first"):
        (self.workspace/"src").mkdir(exist_ok=True)
        (self.workspace/"src"/"main.py").write_text("def f(): return 1",encoding="utf-8")
        deliver=self.run/"codex_delivery.json"
        payload={"status":"success","summary":"test"}
        atomic_write_json(deliver,payload)
        before=program_snapshot(self.workspace)
        artifact=self.run/f"codex_step_{step}.json"
        identity={"kind":"codex-cli","result_path":"codex_delivery.json",
                  "model":"fake","prompt_sha256":"abcd","schema_sha256":"fake"}
        reserve_step(self.run,step,identity)
        atomic_write_json(artifact,{"step_id":step,"identity":identity,
            "post_program_snapshot":before,
            "delivery_sha256":hashlib.sha256(deliver.read_bytes()).hexdigest()})
        finish_step(self.run,step,identity,artifact.name)

    def test_fresh_preview_is_read_only_and_does_not_create_ledger(self):
        before=set(self.run.rglob("*"))
        result=self.preview()
        after=set(self.run.rglob("*"))
        self.assertEqual(before,after)
        self.assertEqual(len(result["not_started_steps"]),4)
        self.assertFalse(result["can_resume_now"])
        self.assertFalse(result["resume_authorized"])
        self.assertEqual(result["mode"],"read_only_no_model_calls")

    def test_single_verified_deepseek_step_is_reported(self):
        self.deepseek()
        result=self.preview()
        self.assertIn("product.spec",result["verified_steps"])
        self.assertIn("architecture.plan",result["not_started_steps"])
        self.assertFalse(result["can_resume_now"])

    def test_verified_codex_requires_output_and_workspace_evidence(self):
        self.codex()
        result=self.preview()
        self.assertIn("developer.first",result["verified_steps"])

    def test_modified_workspace_blocks_codex_reuse(self):
        self.codex()
        (self.workspace/"src"/"main.py").write_text("tampered",encoding="utf-8")
        result=self.preview()
        self.assertIn("developer.first",result["blocked_steps"])

    def test_corrupt_codex_delivery_blocks_reuse(self):
        self.codex()
        (self.run/"codex_delivery.json").write_text('{"status":"tampered"}',encoding="utf-8")
        self.assertIn("developer.first",self.preview()["blocked_steps"])

    def test_missing_deepseek_receipt_is_blocked(self):
        self.deepseek()
        (self.run/"artifacts"/"deepseek_step_product.spec.json").unlink()
        self.assertIn("product.spec",self.preview()["blocked_steps"])

    def test_inflight_reservation_is_blocked(self):
        reserve_step(self.run,"qa.review.0",{"role":"qa"})
        self.assertIn("qa.review.0",self.preview()["blocked_steps"])

    def test_legacy_without_ledger_warns_against_replaying(self):
        self.state["codex_calls"]=2
        self.save_state()
        result=self.preview()
        self.assertTrue(any("历史任务" in x for x in result["blockers"]))
        self.assertFalse(result["can_resume_now"])

    def test_running_worker_is_never_resumeable(self):
        result=self.preview(running=True)
        self.assertTrue(any("运行线程" in x for x in result["blockers"]))

    def test_budget_pending_only_reports_not_grants(self):
        self.deepseek()
        self.state["status"]="等待预算确认"
        self.state["budget_approval"]={"requested_extra":1000}
        self.save_state()
        result=self.preview()
        self.assertTrue(any("预算" in x for x in result["blockers"]))
        self.assertFalse(result["resume_authorized"])

    def test_completed_or_published_run_has_no_resume(self):
        self.state["status"]="等待人工验收"
        self.state["candidate_synced"]=True
        self.save_state()
        result=self.preview()
        self.assertGreaterEqual(len(result["blockers"]),1)
        self.assertFalse(result["can_resume_now"])

    def test_unknown_task_state_is_blocked(self):
        self.state["status"]="其他自定义状态"
        self.save_state()
        self.assertTrue(self.preview()["blockers"])

    def test_invalid_draft_id_is_rejected_without_io(self):
        self.state["draft_id"]="../bad"
        with self.assertRaises(ValueError):
            self.preview()

    def test_rework_rounds_have_separate_stage_ids(self):
        self.state["rework_count"]=2
        self.save_state()
        result=self.preview()
        ids=[x["step_id"] for x in result["steps"]]
        self.assertIn("developer.rework.1",ids)
        self.assertIn("qa.review.2",ids)
        self.assertEqual(len(ids),8)

    def test_manual_rework_has_separate_step_location(self):
        self.state["manual_rework_attempted"]=True
        self.save_state()
        result=self.preview()
        self.assertEqual(result["steps"][-1]["step_id"],"manual.rework.1")

    def test_checkpoint_file_corruption_fails_closed(self):
        (self.run/"step_checkpoints.json").write_text("{bad",encoding="utf-8")
        result=self.preview()
        self.assertEqual(len(result["blocked_steps"]),4)
        self.assertFalse(result["can_resume_now"])

    def test_draft_with_no_agents_cannot_imply_paid_steps(self):
        self.draft["analysis"]["required_agents"]=[]
        result=self.preview()
        self.assertEqual(result["steps"],[])
        self.assertFalse(result["can_resume_now"])


if __name__=="__main__":
    unittest.main()

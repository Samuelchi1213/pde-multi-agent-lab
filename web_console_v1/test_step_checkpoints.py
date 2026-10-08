"""Offline contract tests for M7-003 durable step checkpoints.

No real projects, paid models, credentials, or network calls.
Run: python -m unittest discover -s web_console_v1 -p test_step_checkpoints.py -v
"""
import json
import tempfile
import unittest
from pathlib import Path

from step_checkpoints import (
    CheckpointError,
    finish_step,
    inspect_step,
    list_steps,
    mark_uncertain,
    reserve_step,
)


class TestStepCheckpoints(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run = Path(self.tmp.name) / "DRAFT-TEST"
        self.run.mkdir()
        self.inputs = {"role": "DeepSeek-QA", "criteria": ["过去日期", "保留记录"]}
        self.receipt = self.run / "artifacts" / "response.json"
        self.receipt.parent.mkdir(parents=True)
        self.receipt.write_text('{"result":"pass","tokens":10}', encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def reserve(self):
        return reserve_step(self.run, "qa.review.0", self.inputs)

    def finish(self):
        return finish_step(self.run, "qa.review.0", self.inputs, "artifacts/response.json")

    def test_unstarted_step_is_not_implicitly_executed(self):
        self.assertEqual(inspect_step(self.run,"qa.review.0")["decision"],"not_started")
        self.assertFalse((self.run / "step_checkpoints.json").exists())

    def test_reservation_must_precede_paid_call(self):
        self.assertEqual(self.reserve()["decision"],"reserved")
        state = inspect_step(self.run,"qa.review.0",self.inputs)
        self.assertEqual(state["decision"],"blocked_uncertain")
        self.assertEqual(state["status"],"in_flight")

    def test_duplicate_reservation_never_authorizes_second_call(self):
        self.reserve()
        self.assertEqual(self.reserve()["decision"],"blocked_uncertain")

    def test_successful_receipt_can_be_reused_without_model_call(self):
        self.reserve()
        self.assertEqual(self.finish()["decision"],"completed")
        self.assertEqual(self.reserve()["decision"],"reuse_saved_result")

    def test_request_field_order_does_not_change_fingerprint(self):
        self.reserve()
        finish_step(self.run,"qa.review.0",self.inputs,"artifacts/response.json")
        reversed_inputs={"criteria":self.inputs["criteria"],"role":"DeepSeek-QA"}
        self.assertEqual(reserve_step(self.run,"qa.review.0",reversed_inputs)["decision"],
                         "reuse_saved_result")

    def test_different_request_cannot_reuse_same_step_id(self):
        self.reserve()
        self.finish()
        result=reserve_step(self.run,"qa.review.0",{"role":"changed"})
        self.assertEqual(result["decision"],"blocked_input_changed")

    def test_uncertain_result_requires_manual_resolution(self):
        self.reserve()
        mark_uncertain(self.run,"qa.review.0","响应超时，不能确定是否已计费")
        state=self.reserve()
        self.assertEqual(state["status"],"uncertain")
        self.assertEqual(state["decision"],"blocked_uncertain")

    def test_result_is_not_automatically_accepted_after_uncertainty(self):
        self.reserve()
        mark_uncertain(self.run,"qa.review.0","result unknown")
        with self.assertRaises(CheckpointError):
            self.finish()

    def test_original_model_prompt_and_result_are_not_stored_in_ledger(self):
        inputs={"user_prompt":"PRIVATE_TOKEN_DO_NOT_PERSIST","api_key":"SECRET_KEY"}
        reserve_step(self.run,"qa.review.0",inputs)
        data=(self.run/"step_checkpoints.json").read_text(encoding="utf-8")
        self.assertNotIn("PRIVATE_TOKEN_DO_NOT_PERSIST",data)
        self.assertNotIn("SECRET_KEY",data)
        self.assertNotIn("result",data)

    def test_deleted_receipt_blocks_reuse(self):
        self.reserve()
        self.finish()
        self.receipt.unlink()
        self.assertEqual(self.reserve()["decision"],"blocked_missing_evidence")

    def test_modified_receipt_blocks_reuse(self):
        self.reserve()
        self.finish()
        self.receipt.write_text('{"result":"tampered"}',encoding="utf-8")
        self.assertEqual(self.reserve()["decision"],"blocked_evidence_changed")

    def test_receipt_cannot_escape_run_directory(self):
        other=Path(self.tmp.name)/"outside.json"
        other.write_text('{"result":"pass"}',encoding="utf-8")
        self.reserve()
        with self.assertRaises(CheckpointError):
            finish_step(self.run,"qa.review.0",self.inputs,"../outside.json")
        self.assertEqual(self.reserve()["decision"],"blocked_uncertain")

    def test_missing_receipt_does_not_mark_step_completed(self):
        self.reserve()
        with self.assertRaises(CheckpointError):
            finish_step(self.run,"qa.review.0",self.inputs,"artifacts/missing.json")
        self.assertEqual(self.reserve()["decision"],"blocked_uncertain")

    def test_ledger_corruption_is_fail_closed(self):
        (self.run/"step_checkpoints.json").write_text("{invalid",encoding="utf-8")
        with self.assertRaises(CheckpointError):
            self.reserve()

    def test_lock_collision_does_not_reexecute(self):
        (self.run/".step_checkpoints.lock").write_text("another-worker",encoding="utf-8")
        with self.assertRaises(CheckpointError):
            self.reserve()
        self.assertFalse((self.run/"step_checkpoints.json").exists())

    def test_finish_requires_a_reservation(self):
        with self.assertRaises(CheckpointError):
            self.finish()

    def test_second_completion_is_rejected(self):
        self.reserve()
        self.finish()
        with self.assertRaises(CheckpointError):
            self.finish()

    def test_summary_does_not_expose_saved_response(self):
        self.reserve()
        self.finish()
        summary=list_steps(self.run)
        self.assertEqual(len(summary),1)
        self.assertEqual(summary[0]["decision"],"reuse_saved_result")
        self.assertNotIn("pass",json.dumps(summary))


if __name__=="__main__":
    unittest.main()

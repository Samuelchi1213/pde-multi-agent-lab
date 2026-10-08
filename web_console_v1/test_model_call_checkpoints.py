"""M7-003 B1: mocked DeepSeek API checkpoint integration.

Unittest only; never contacts DeepSeek/Codex or a real project.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from step_checkpoints import CheckpointError, inspect_step
from team_executor import BudgetApprovalRequired, DynamicTeamRun


class TestDeepSeekLiveCheckpointContract(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.draft = {"goal": "local mock only", "analysis": {"required_agents": []}}
        self.run = DynamicTeamRun(self.root, "DRAFT-MOCK", self.draft, "not-a-real-key")
        self.run.save()
        self.inputs = {"x": 1}
        self.mock_result = {"status": "pass", "summary": "mock"}
        self.mock_usage = {"total_tokens": 11}

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, step_id="product.spec", payload=None, system="system"):
        return self.run.use_ds(
            "产品智能体", system, self.inputs if payload is None else payload, step_id=step_id
        )

    def test_first_invocation_reserves_then_persists_receipt(self):
        with patch("team_executor.deepseek_json", return_value=(self.mock_result, self.mock_usage)) as fake:
            result, usage = self.call()
        self.assertEqual(result, self.mock_result)
        self.assertEqual(usage, self.mock_usage)
        fake.assert_called_once()
        self.assertEqual(self.run.state["deepseek_calls"], 1)
        self.assertEqual(self.run.state["deepseek_tokens"], 11)
        self.assertEqual(inspect_step(self.run.run_dir, "product.spec")["decision"], "reuse_saved_result")

    def test_repeat_uses_verified_receipt_without_second_api_call(self):
        with patch("team_executor.deepseek_json", return_value=(self.mock_result, self.mock_usage)) as fake:
            self.call()
            again = self.call()
        fake.assert_called_once()
        self.assertEqual(again[0], self.mock_result)
        self.assertEqual(self.run.state["deepseek_calls"], 1)
        self.assertEqual(self.run.state["deepseek_tokens"], 11)

    def test_restart_reuses_receipt_without_double_billing(self):
        with patch("team_executor.deepseek_json", return_value=(self.mock_result, self.mock_usage)):
            self.call()
        restarted = DynamicTeamRun(self.root, "DRAFT-MOCK", self.draft, "mock-key")
        restarted.state = json.loads(self.run.state_file.read_text(encoding="utf-8"))
        with patch("team_executor.deepseek_json", side_effect=AssertionError("MUST NOT CALL")) as fake:
            restarted.use_ds("产品智能体", "system", self.inputs, step_id="product.spec")
        fake.assert_not_called()
        self.assertEqual(restarted.state["deepseek_calls"], 1)
        self.assertEqual(restarted.state["deepseek_tokens"], 11)

    def test_reconstruct_count_after_saved_receipt_and_lost_state(self):
        with patch("team_executor.deepseek_json", return_value=(self.mock_result, self.mock_usage)):
            self.call()
        fresh = DynamicTeamRun(self.root, "DRAFT-MOCK", self.draft, "mock")
        with patch("team_executor.deepseek_json", side_effect=AssertionError("MUST NOT CALL")):
            fresh.use_ds("产品智能体", "system", self.inputs, step_id="product.spec")
        self.assertEqual(fresh.state["deepseek_calls"], 1)
        self.assertEqual(fresh.state["deepseek_tokens"], 11)

    def test_changed_payload_blocks_reuse_without_call(self):
        with patch("team_executor.deepseek_json", return_value=(self.mock_result, self.mock_usage)):
            self.call()
        with patch("team_executor.deepseek_json", side_effect=AssertionError("MUST NOT CALL")):
            with self.assertRaises(CheckpointError):
                self.call(payload={"x": 2})

    def test_changed_system_prompt_blocks_reuse(self):
        with patch("team_executor.deepseek_json", return_value=(self.mock_result, self.mock_usage)):
            self.call()
        with patch("team_executor.deepseek_json", side_effect=AssertionError("MUST NOT CALL")):
            with self.assertRaises(CheckpointError):
                self.call(system="new system prompt")

    def test_timeout_is_uncertain_and_never_automatically_retried(self):
        with patch("team_executor.deepseek_json", side_effect=TimeoutError("mock timeout")) as fake:
            with self.assertRaises(TimeoutError):
                self.call()
        self.assertEqual(inspect_step(self.run.run_dir,"product.spec")["decision"],"blocked_uncertain")
        with patch("team_executor.deepseek_json",side_effect=AssertionError("DUPLICATE")) as no_second:
            with self.assertRaises(CheckpointError):
                self.call()
        self.assertEqual(fake.call_count,1)
        no_second.assert_not_called()

    def test_tampered_receipt_refuses_api_and_refuses_reuse(self):
        with patch("team_executor.deepseek_json", return_value=(self.mock_result, self.mock_usage)):
            self.call()
        receipt=self.run.run_dir/"artifacts"/"deepseek_step_product.spec.json"
        receipt.write_text('{"result":"tampered"}',encoding="utf-8")
        with patch("team_executor.deepseek_json", side_effect=AssertionError("DUPLICATE")) as fake:
            with self.assertRaises(CheckpointError):
                self.call()
        fake.assert_not_called()

    def test_pre_call_budget_gate_creates_no_in_flight_reservation(self):
        self.run.state["deepseek_token_budget"] = 0
        with patch("team_executor.deepseek_json", side_effect=AssertionError("SHOULD NOT RUN")):
            with self.assertRaises(BudgetApprovalRequired):
                self.call()
        self.assertEqual(inspect_step(self.run.run_dir,"product.spec")["decision"],"not_started")

    def test_over_budget_after_success_keeps_paid_result(self):
        self.run.state["deepseek_token_budget"]=5
        with patch("team_executor.deepseek_json", return_value=(self.mock_result,self.mock_usage)) as fake:
            self.call()
            with self.assertRaises(BudgetApprovalRequired):
                self.call(step_id="architecture.plan")
        self.assertEqual(fake.call_count,1)
        self.assertEqual(self.run.state["deepseek_tokens"],11)
        self.assertEqual(inspect_step(self.run.run_dir,"product.spec")["decision"],"reuse_saved_result")
        self.assertEqual(inspect_step(self.run.run_dir,"architecture.plan")["decision"],"not_started")

    def test_completion_write_failure_never_authorizes_retry(self):
        with patch("team_executor.deepseek_json", return_value=(self.mock_result,self.mock_usage)) as fake:
            with patch("step_checkpoints.finish_step",side_effect=OSError("mock filesystem issue")):
                with self.assertRaises(OSError):
                    self.call()
            with self.assertRaises(CheckpointError):
                self.call()
        self.assertEqual(fake.call_count,1)
        self.assertEqual(inspect_step(self.run.run_dir,"product.spec")["decision"],"blocked_uncertain")

    def test_different_qa_rounds_have_distinct_ids(self):
        with patch("team_executor.deepseek_json", return_value=(self.mock_result,self.mock_usage)) as fake:
            self.call("qa.review.0")
            self.call("qa.review.1")
        self.assertEqual(fake.call_count,2)
        self.assertEqual(self.run.state["deepseek_calls"],2)
        self.assertEqual(self.run.state["deepseek_tokens"],22)


if __name__ == "__main__":
    unittest.main()

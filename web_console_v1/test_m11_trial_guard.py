"""Offline M11 safety tests. They never call DeepSeek or Codex."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import m11_trial_guard as gate


def payload():
    return {
        "model": gate.MODEL,
        "thinking": {"type": "disabled"},
        "max_tokens": gate.MAX_OUTPUT_TOKENS,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": "Return json"},
                     {"role": "user", "content": "synthetic only"}],
    }


def result():
    return {"choices": [{"message": {"content": '{"ok": true}'}}],
            "usage": {"prompt_tokens": 22, "completion_tokens": 14, "total_tokens": 36}}


class TrialGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "ledger.json"
        self.settings = patch.dict(os.environ, {
            gate.TRIAL_ENV: "1", "PDE_M11_TEST_ONLY": "1"
        })
        self.settings.start()
        self.addCleanup(self.settings.stop)

    def ask(self, step="coordinator.initial", transport=None):
        return gate.request("dummy-not-real", payload(), step,
                            transport=transport or (lambda _p: result()),
                            _test_path=self.path)

    def test_disabled_by_default_without_environment_gate(self):
        with patch.dict(os.environ, {gate.TRIAL_ENV: "0"}):
            with self.assertRaises(gate.TrialBlocked):
                self.ask()
        self.assertFalse(self.path.exists())

    def test_offline_success_reserves_then_persists_receipt(self):
        recorded = []
        def transport(p):
            data = json.loads(self.path.read_text(encoding="utf-8"))
            recorded.append(data["requests"]["coordinator.initial"]["state"])
            return result()
        r = self.ask(transport=transport)
        self.assertEqual(r["usage"]["total_tokens"], 36)
        self.assertEqual(recorded, ["reserved"])
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(data["requests"]["coordinator.initial"]["state"], "finished")
        self.assertEqual(data["budget_cny"], "10")

    def test_duplicate_step_never_sends_again(self):
        self.ask()
        with self.assertRaises(gate.TrialBlocked):
            self.ask(transport=lambda _: self.fail("duplicate contacted model"))
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(len(data["requests"]), 1)

    def test_network_uncertainty_freezes_all_future_steps(self):
        def fail(_):
            raise TimeoutError("unknown billing outcome")
        with self.assertRaises(TimeoutError):
            self.ask(transport=fail)
        state = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertTrue(state["frozen"])
        self.assertEqual(state["requests"]["coordinator.initial"]["state"], "uncertain")
        with self.assertRaises(gate.TrialBlocked):
            self.ask("team.product.spec", transport=lambda _: self.fail("unexpected model call"))

    def test_other_pending_reservation_blocks_concurrency(self):
        def in_flight(_):
            with self.assertRaises(gate.TrialBlocked):
                self.ask("team.product.spec",
                         transport=lambda _: self.fail("concurrent billed request"))
            return result()
        self.ask(transport=in_flight)
        self.assertEqual(len(json.loads(self.path.read_text())["requests"]), 1)

    def test_unsupported_model_cannot_reserve(self):
        p = payload()
        p["model"] = "deepseek-chat"
        with self.assertRaises(gate.TrialBlocked):
            gate.request("dummy", p, "coordinator.initial", _test_path=self.path)
        self.assertFalse(self.path.exists())

    def test_wrong_output_limit_cannot_reserve(self):
        p = payload()
        p["max_tokens"] = 200000
        with self.assertRaises(gate.TrialBlocked):
            gate.request("dummy", p, "coordinator.initial", _test_path=self.path)

    def test_thinking_mode_must_be_off(self):
        p = payload()
        p["thinking"] = {"type": "enabled"}
        with self.assertRaises(gate.TrialBlocked):
            gate.request("dummy", p, "coordinator.initial", _test_path=self.path)

    def test_all_five_calls_then_sixth_blocked(self):
        for step in ["coordinator.initial", "team.product.spec",
                     "team.architecture.plan", "team.qa.review.0",
                     "coordinator.refine"]:
            self.ask(step)
        with self.assertRaises(gate.TrialBlocked):
            self.ask("team.extra", transport=lambda _: self.fail("exceeded cap"))
        self.assertEqual(len(json.loads(self.path.read_text())["requests"]), 5)

    def test_oversized_request_rejected_before_any_payment(self):
        p = payload()
        p["messages"][1]["content"] = "x" * 101000
        with self.assertRaises(gate.TrialBlocked):
            gate.request("dummy", p, "coordinator.initial", _test_path=self.path)
        self.assertFalse(self.path.exists())

    def test_wrong_step_label_rejected(self):
        with self.assertRaises(gate.TrialBlocked):
            self.ask("legacy.untrusted")

    def test_corrupt_ledger_fails_closed(self):
        self.path.write_text("{broken", encoding="utf-8")
        with self.assertRaises((ValueError, gate.TrialBlocked)):
            self.ask(transport=lambda _: self.fail("should not call"))


    def test_global_codex_single_use_across_drafts(self):
        with patch.object(gate, "LEDGER", self.path):
            gate.reserve_codex("DRAFT-SYNTHETIC-001", "developer.first")
            with self.assertRaises(gate.TrialBlocked):
                gate.reserve_codex("DRAFT-SYNTHETIC-002", "developer.first")
            gate.finish_codex("DRAFT-SYNTHETIC-001", success=True)
            self.assertEqual(json.loads(self.path.read_text())["codex"]["state"], "finished")
            with self.assertRaises(gate.TrialBlocked):
                gate.reserve_codex("DRAFT-SYNTHETIC-002", "developer.first")

    def test_failed_codex_blocks_later_model_calls(self):
        with patch.object(gate, "LEDGER", self.path):
            gate.reserve_codex("DRAFT-SYNTHETIC-001", "developer.first")
            gate.finish_codex("DRAFT-SYNTHETIC-001", success=False)
            state = json.loads(self.path.read_text())
            self.assertTrue(state["frozen"])
            self.assertEqual(state["codex"]["state"], "uncertain")
            with self.assertRaises(gate.TrialBlocked):
                self.ask(transport=lambda _: self.fail("must never call model"))


if __name__ == "__main__":
    unittest.main()

"""End-to-end interaction tests for the isolated in-memory recovery demo."""
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor

from recovery_flow_demo import RecoveryFlowDemo


def _interrupt_and_review(demo):
    demo.act("create")
    demo.act("interrupt")
    return demo.act("inspect")


class RecoveryDemoFlowTests(unittest.TestCase):
    def setUp(self):
        self.demo = RecoveryFlowDemo()

    def assert_safe(self, state):
        self.assertFalse(state["can_resume_now"])
        self.assertFalse(state["resume_authorized"])
        self.assertEqual(state["external_model_calls"], 0)
        self.assertEqual(state["real_project_writes"], 0)
        self.assertEqual(state["mode"], "isolated_in_memory_demo")

    def test_full_interactive_flow_reuses_previous_steps_and_completes(self):
        self.assertEqual(self.demo.snapshot()["state"], "idle")
        created = self.demo.act("create")
        self.assertEqual(created["state"], "running_fake")
        interrupted = self.demo.act("interrupt")
        self.assertEqual(interrupted["state"], "interrupted")
        reviewed = self.demo.act("inspect")
        self.assertEqual(reviewed["next_step"], "developer.first")
        self.assertEqual([x["status"] for x in reviewed["steps"]],
                         ["verified", "verified", "pending_mock", "pending_mock"])
        self.assertEqual(self.demo.act("approve")["state"], "approved_fake")
        built = self.demo.act("continue")
        self.assertEqual(built["state"], "awaiting_fake_approval")
        self.assertEqual(built["next_step"], "qa.review.0")
        self.assertEqual(built["mock_calls"], 1)
        self.demo.act("approve")
        finished = self.demo.act("continue")
        self.assertEqual(finished["state"], "completed_mock")
        self.assertEqual(finished["mock_calls"], 2)
        self.assertEqual(finished["mock_credits_spent"], 5)
        self.assertEqual([x["status"] for x in finished["steps"]],
                         ["verified", "verified", "completed_mock", "completed_mock"])
        self.assert_safe(finished)

    def test_new_demo_never_reads_saved_team_history(self):
        snap = self.demo.snapshot()
        self.assertIsNone(snap["task_id"])
        self.assertEqual(snap["steps"], [])
        self.assert_safe(snap)

    def test_browser_refresh_returns_same_progress(self):
        _interrupt_and_review(self.demo)
        self.demo.act("approve")
        before = self.demo.snapshot()
        self.assertEqual(before, self.demo.snapshot())
        self.assertTrue(before["approved_current"])
        self.assert_safe(before)

    def test_cannot_interrupt_without_demo_start(self):
        with self.assertRaises(ValueError):
            self.demo.act("interrupt")
        self.assertEqual(self.demo.snapshot()["state"], "idle")

    def test_cannot_inspect_before_interrupt(self):
        self.demo.act("create")
        with self.assertRaises(ValueError):
            self.demo.act("inspect")

    def test_cannot_approve_before_evidence_review(self):
        self.demo.act("create")
        self.demo.act("interrupt")
        with self.assertRaises(ValueError):
            self.demo.act("approve")

    def test_cannot_resume_without_current_step_approval(self):
        _interrupt_and_review(self.demo)
        with self.assertRaises(ValueError):
            self.demo.act("continue")
        self.assertEqual(self.demo.snapshot()["mock_calls"], 0)

    def test_double_click_approval_cannot_issue_twice(self):
        _interrupt_and_review(self.demo)
        self.demo.act("approve")
        with self.assertRaises(ValueError):
            self.demo.act("approve")
        self.assertEqual(self.demo.snapshot()["mock_calls"], 0)

    def test_double_click_continue_does_not_repeat_fake_model(self):
        _interrupt_and_review(self.demo)
        self.demo.act("approve")
        self.demo.act("continue")
        with self.assertRaises(ValueError):
            self.demo.act("continue")
        self.assertEqual(self.demo.snapshot()["mock_calls"], 1)

    def test_second_fake_stage_requires_separate_approval(self):
        _interrupt_and_review(self.demo)
        self.demo.act("approve")
        self.demo.act("continue")
        with self.assertRaises(ValueError):
            self.demo.act("continue")
        self.assertEqual(self.demo.snapshot()["next_step"], "qa.review.0")

    def test_fake_unknown_receipt_freezes_session(self):
        _interrupt_and_review(self.demo)
        self.demo.act("approve")
        result = self.demo.act("lose_receipt")
        self.assertEqual(result["state"], "frozen")
        self.assertEqual(result["mock_calls"], 1)
        self.assert_safe(result)

    def test_unknown_receipt_cannot_retry_or_reapprove(self):
        _interrupt_and_review(self.demo)
        self.demo.act("approve")
        self.demo.act("lose_receipt")
        for action in ("continue", "approve", "inspect", "lose_receipt"):
            with self.subTest(action=action), self.assertRaises(ValueError):
                self.demo.act(action)
        self.assertEqual(self.demo.snapshot()["mock_calls"], 1)

    def test_reset_discards_only_mock_state(self):
        _interrupt_and_review(self.demo)
        reset = self.demo.act("reset")
        self.assertEqual(reset["state"], "idle")
        self.assertEqual(reset["mock_calls"], 0)
        self.assertEqual(self.demo.act("create")["state"], "running_fake")
        self.assert_safe(reset)

    def test_completed_demo_cannot_continue_until_reset(self):
        _interrupt_and_review(self.demo)
        for _ in range(2):
            self.demo.act("approve")
            self.demo.act("continue")
        with self.assertRaises(ValueError):
            self.demo.act("continue")
        with self.assertRaises(ValueError):
            self.demo.act("create")

    def test_unknown_action_rejected(self):
        with self.assertRaises(ValueError):
            self.demo.act("run_codex")

    def test_concurrent_continue_only_uses_one_fake_credit_claim(self):
        _interrupt_and_review(self.demo)
        self.demo.act("approve")
        def click(_):
            try:
                return self.demo.act("continue")
            except ValueError:
                return None
        with ThreadPoolExecutor(max_workers=12) as pool:
            list(pool.map(click, range(30)))
        state = self.demo.snapshot()
        self.assertEqual(state["mock_calls"], 1)
        self.assertEqual(state["mock_credits_spent"], 3)
        self.assert_safe(state)


if __name__ == "__main__":
    unittest.main()

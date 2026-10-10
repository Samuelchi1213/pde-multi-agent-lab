"""B4-B1: fake-only, in-memory, no provider imports or project file writes."""
import copy
import unittest
from concurrent.futures import ThreadPoolExecutor

from mock_stage_runner import FakeStageSession, describe_mock_runner


def plan():
    return {
        "mode": "offline_stage_simulation_only",
        "status": "simulated",
        "start_step": "developer.first",
        "reused_steps": ["product.spec", "architecture.plan"],
        "simulated_steps": ["developer.first", "qa.review.0"],
        "blockers": [],
        "can_resume_now": False,
        "resume_authorized": False,
    }


class TestMockStageRunner(unittest.TestCase):
    def safe(self, state):
        self.assertFalse(state["can_resume_now"])
        self.assertFalse(state["resume_authorized"])
        self.assertEqual(state["external_model_calls"], 0)
        self.assertEqual(state["real_project_writes"], 0)

    def test_fake_plan_description_has_no_side_effects(self):
        p = plan()
        old = copy.deepcopy(p)
        outcome = describe_mock_runner(p)
        self.assertEqual(outcome["status"], "ready_fake_only")
        self.assertEqual(outcome["mock_calls"], 0)
        self.assertEqual(p, old)
        self.safe(outcome)

    def test_fake_stage_chain_finishes_in_order(self):
        session = FakeStageSession(plan())
        first = session.advance(step_id="developer.first", approved=True)
        self.assertEqual(first["next_step"], "qa.review.0")
        self.assertEqual(first["credits_spent_fake"], 3)
        end = session.advance(step_id="qa.review.0", approved=True)
        self.assertEqual(end["status"], "finished_fake_only")
        self.assertEqual(end["mock_calls"], 2)
        self.assertEqual(end["credits_spent_fake"], 5)
        self.safe(end)

    def test_initial_state_is_not_actual_resumption(self):
        state = FakeStageSession(plan()).state()
        self.assertEqual(state["status"], "awaiting_fake_approval")
        self.assertEqual(state["mock_calls"], 0)
        self.safe(state)

    def test_refusal_to_approve_stays_at_current_step(self):
        session = FakeStageSession(plan())
        state = session.advance(step_id="developer.first")
        self.assertEqual(state["next_step"], "developer.first")
        self.assertEqual(state["mock_calls"], 0)
        self.safe(state)

    def test_non_boolean_approval_does_not_count(self):
        session = FakeStageSession(plan())
        state = session.advance(step_id="developer.first", approved=1)
        self.assertEqual(state["mock_calls"], 0)

    def test_wrong_stage_cannot_skip(self):
        session = FakeStageSession(plan())
        state = session.advance(step_id="qa.review.0", approved=True)
        self.assertEqual(state["next_step"], "developer.first")
        self.assertEqual(state["mock_calls"], 0)
        self.safe(state)

    def test_mock_budget_is_checked_before_claim(self):
        session = FakeStageSession(plan(), credit_limit=2)
        state = session.advance(step_id="developer.first", approved=True)
        self.assertEqual(state["credits_spent_fake"], 0)
        self.assertEqual(state["mock_calls"], 0)
        self.assertEqual(state["status"], "awaiting_fake_approval")
        self.safe(state)

    def test_exhaustion_after_first_stage_does_not_run_second(self):
        session = FakeStageSession(plan(), credit_limit=3)
        session.advance(step_id="developer.first", approved=True)
        state = session.advance(step_id="qa.review.0", approved=True)
        self.assertEqual(state["mock_calls"], 1)
        self.assertEqual(state["next_step"], "qa.review.0")
        self.safe(state)

    def test_uncertain_fake_call_freezes_session(self):
        session = FakeStageSession(plan())
        state = session.advance(step_id="developer.first", approved=True, lose_response=True)
        self.assertEqual(state["status"], "uncertain_frozen")
        self.assertEqual(state["mock_calls"], 1)
        self.assertEqual(state["credits_spent_fake"], 3)
        self.safe(state)

    def test_uncertain_call_never_repeated(self):
        session = FakeStageSession(plan())
        session.advance(step_id="developer.first", approved=True, lose_response=True)
        again = session.advance(step_id="developer.first", approved=True)
        self.assertEqual(again["mock_calls"], 1)
        self.assertEqual(again["status"], "uncertain_frozen")
        self.safe(again)

    def test_uncertain_call_blocks_subsequent_step(self):
        session = FakeStageSession(plan())
        session.advance(step_id="developer.first", approved=True, lose_response=True)
        later = session.advance(step_id="qa.review.0", approved=True)
        self.assertEqual(later["mock_calls"], 1)

    def test_double_click_first_step_only_counts_once(self):
        session = FakeStageSession(plan())
        session.advance(step_id="developer.first", approved=True)
        result = session.advance(step_id="developer.first", approved=True)
        self.assertEqual(result["mock_calls"], 1)
        self.assertEqual(result["next_step"], "qa.review.0")

    def test_concurrent_repeated_clicks_only_one_fake_claim(self):
        session = FakeStageSession(plan())
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: session.advance(step_id="developer.first", approved=True), range(30)))
        self.assertEqual(session.state()["mock_calls"], 1)
        self.assertTrue(all(x["external_model_calls"] == 0 for x in results))

    def test_finished_session_ignores_replayed_click(self):
        session = FakeStageSession(plan())
        session.advance(step_id="developer.first", approved=True)
        session.advance(step_id="qa.review.0", approved=True)
        self.assertEqual(session.advance(step_id="qa.review.0", approved=True)["mock_calls"], 2)

    def test_plan_with_auto_authorization_is_rejected(self):
        p = plan()
        p["resume_authorized"] = True
        self.assertEqual(describe_mock_runner(p)["status"], "blocked")
        with self.assertRaises(ValueError):
            FakeStageSession(p)

    def test_fake_plan_rejected_if_b4a_blocked(self):
        p = plan()
        p["status"] = "blocked"
        self.assertEqual(describe_mock_runner(p)["status"], "blocked")

    def test_duplicate_or_reordered_steps_rejected(self):
        p = plan()
        p["simulated_steps"] = ["developer.first", "developer.first"]
        self.assertEqual(describe_mock_runner(p)["status"], "blocked")
        p["simulated_steps"] = ["qa.review.0", "developer.first"]
        p["start_step"] = "qa.review.0"
        self.assertEqual(describe_mock_runner(p)["status"], "blocked")

    def test_unknown_step_rejected(self):
        p = plan()
        p["simulated_steps"] = ["external.payment"]
        p["start_step"] = "external.payment"
        self.assertEqual(describe_mock_runner(p)["status"], "blocked")

    def test_bad_credit_limit_rejected(self):
        for value in (-1, 1001, 1.5, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                FakeStageSession(plan(), credit_limit=value)

    def test_fake_trace_cannot_mutate_original_plan(self):
        p = plan()
        preserved = copy.deepcopy(p)
        session = FakeStageSession(p)
        events = session.advance(step_id="developer.first", approved=True)["events"]
        events.clear()
        self.assertEqual(p, preserved)
        self.assertGreater(len(session.state()["events"]), 0)


if __name__ == "__main__":
    unittest.main()

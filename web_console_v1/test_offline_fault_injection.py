"""20 fully isolated B4-C1 failure-injection tests, zero provider calls."""
import unittest

from offline_fault_injection import FAULT_CASES, run_mock_fault_case, summarize_fault_readiness


class TestClosedFaultInjection(unittest.TestCase):
    def report(self, case):
        r = run_mock_fault_case(case)
        self.assertFalse(r["production_ready"])
        self.assertFalse(r["resume_authorized"])
        self.assertFalse(r["can_resume_now"])
        self.assertEqual(r["external_model_calls"], 0)
        self.assertEqual(r["real_project_writes"], 0)
        return r

    def test_mock_control_only_finishes_fake_stage(self):
        r = self.report("mock_control")
        self.assertEqual(r["status"], "mock_control_complete")
        self.assertEqual(r["mock_calls"], 1)

    def test_missing_owner_approval_denied(self):
        self.assertIn("mock_issue_denied", self.report("no_owner_approval")["events"])

    def test_self_approval_denied(self):
        self.assertIn("mock_issue_denied", self.report("self_approval")["events"])

    def test_duplicate_issue_denied(self):
        r = self.report("duplicate_issue")
        self.assertIn("second_mock_issue_denied", r["events"])
        self.assertEqual(r["mock_calls"], 0)

    def test_expired_approval_denied(self):
        self.assertIn("blocked_expired", self.report("expired_approval")["events"])

    def test_changed_evidence_freezes(self):
        self.assertIn("blocked_evidence_changed", self.report("evidence_changed")["events"])

    def test_changed_request_freezes(self):
        self.assertIn("blocked_request_changed", self.report("request_changed")["events"])

    def test_over_budget_freezes(self):
        self.assertIn("blocked_budget", self.report("over_budget")["events"])

    def test_double_consume_denied(self):
        self.assertIn("blocked_replay", self.report("double_consume")["events"])

    def test_wrong_stage_does_not_fake_execute(self):
        r = self.report("out_of_order_stage")
        self.assertEqual(r["mock_calls"], 0)
        self.assertEqual(r["status"], "blocked")

    def test_mock_timeout_freezes(self):
        r = self.report("mock_timeout")
        self.assertEqual(r["status"], "manual_review_required")
        self.assertEqual(r["mock_calls"], 1)

    def test_mock_timeout_retry_never_claims_twice(self):
        r = self.report("retry_after_timeout")
        self.assertEqual(r["mock_calls"], 1)
        self.assertEqual(r["events"][-1], "uncertain_frozen")

    def test_consumed_approval_lost_before_mock_stage_requires_review(self):
        r = self.report("approval_consumed_then_crash")
        self.assertEqual(r["status"], "manual_review_required")
        self.assertEqual(r["mock_calls"], 0)

    def test_unconsumed_volatile_approval_lost_requires_review(self):
        r = self.report("approval_issued_then_crash")
        self.assertEqual(r["status"], "manual_review_required")
        self.assertEqual(r["mock_calls"], 0)

    def test_old_validation_task_never_receives_mock_approval(self):
        r = self.report("blocked_legacy")
        self.assertIn("legacy_approval_refused", r["events"])

    def test_all_scenarios_deny_actual_resume(self):
        self.assertEqual(len(FAULT_CASES), 15)
        for case in FAULT_CASES:
            with self.subTest(case=case):
                self.report(case)

    def test_same_scenario_is_deterministic(self):
        for case in FAULT_CASES:
            with self.subTest(case=case):
                self.assertEqual(run_mock_fault_case(case), run_mock_fault_case(case))

    def test_unknown_scenario_is_rejected(self):
        with self.assertRaises(ValueError):
            run_mock_fault_case("real_codex")

    def test_status_banner_never_claims_production_ready(self):
        for value in (None, {"status": "blocked"}, {"status": "mock_review_candidate"}):
            with self.subTest(value=value):
                r = summarize_fault_readiness(value)
                self.assertEqual(r["status"], "blocked")
                self.assertFalse(r["resume_authorized"])
                self.assertEqual(r["external_model_calls"], 0)
                self.assertEqual(r["real_project_writes"], 0)
                self.assertEqual(r["offline_scenarios"], 15)

    def test_reports_disclose_no_fake_request_fingerprint(self):
        for case in FAULT_CASES:
            with self.subTest(case=case):
                r = self.report(case)
                self.assertNotIn("request_sha256", r)
                self.assertNotIn("api_key", r)
                self.assertNotIn("receipt_id", r)


if __name__ == "__main__":
    unittest.main()

"""B4-B2: 20 strict zero-provider mock approval contract tests."""
import copy
import hashlib
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor

from mock_approval_contract import MockApprovalBook, describe_mock_approval_scope


def preflight():
    return {
        "ok": True, "mode": "read_only_preflight", "status": "review_candidate",
        "draft_id": "DRAFT-OFFLINE", "next_step": "developer.first",
        "requires_owner_approval": True, "blockers": [],
        "can_resume_now": False, "resume_authorized": False,
        "checksums": {
            "state_sha256": "a" * 64,
            "draft_sha256": "b" * 64,
            "ledger_sha256": "c" * 64,
        },
    }


def fake_plan():
    return {"mode": "in_memory_fake_only", "status": "ready_fake_only",
            "can_resume_now": False, "resume_authorized": False}


def request_sha():
    return hashlib.sha256(b"test-only request payload").hexdigest()


class TestMockApprovalContract(unittest.TestCase):
    def setUp(self):
        self.book = MockApprovalBook()
        self.scope = describe_mock_approval_scope(preflight(), fake_plan())

    def issue(self, **kwargs):
        args = dict(requester="tester_01", approver="owner_01",
                    request_sha256=request_sha(), credit_limit=5,
                    issued_tick=10, expires_tick=30, approved=True)
        args.update(kwargs)
        return self.book.issue(self.scope, **args)["mock_receipt_id"]

    def consume(self, receipt_id, **kwargs):
        args = dict(current_scope=self.scope, request_sha256=request_sha(),
                    cost_credits=3, now_tick=12)
        args.update(kwargs)
        return self.book.consume(receipt_id, **args)

    def guarded(self, outcome):
        self.assertFalse(outcome["resume_authorized"])
        self.assertEqual(outcome["external_model_calls"], 0)
        if "real_project_writes" in outcome:
            self.assertEqual(outcome["real_project_writes"], 0)

    def test_good_scope_is_read_only_review_candidate(self):
        p = preflight()
        copy_of_p = copy.deepcopy(p)
        result = describe_mock_approval_scope(p, fake_plan())
        self.assertEqual(result["status"], "mock_review_candidate")
        self.assertEqual(result["step_id"], "developer.first")
        self.assertEqual(p, copy_of_p)
        self.guarded(result)

    def test_single_fake_approval_consumed_once(self):
        receipt = self.issue()
        approved = self.consume(receipt)
        self.assertEqual(approved["status"], "mock_approved_for_fake_step_only")
        self.assertEqual(self.book.status(receipt), "consumed_mock")
        self.guarded(approved)

    def test_replay_denied_after_consumption(self):
        receipt = self.issue()
        self.consume(receipt)
        self.assertEqual(self.consume(receipt)["status"], "blocked_replay")

    def test_duplicate_approval_issue_denied(self):
        self.issue()
        with self.assertRaises(ValueError):
            self.issue(approver="owner_02")

    def test_no_explicit_approval_denied(self):
        with self.assertRaises(ValueError):
            self.issue(approved=False)

    def test_self_approval_denied(self):
        with self.assertRaises(ValueError):
            self.issue(approver="tester_01")

    def test_invalid_actor_denied(self):
        with self.assertRaises(ValueError):
            self.issue(requester="")

    def test_request_fingerprint_must_be_valid_sha(self):
        with self.assertRaises(ValueError):
            self.issue(request_sha256="not-a-sha")

    def test_binding_changes_when_original_evidence_changes(self):
        p = preflight()
        original = describe_mock_approval_scope(p, fake_plan())
        p["checksums"]["ledger_sha256"] = "f" * 64
        changed = describe_mock_approval_scope(p, fake_plan())
        self.assertNotEqual(original["evidence_binding"], changed["evidence_binding"])

    def test_changed_evidence_freezes_once(self):
        receipt = self.issue()
        changed = dict(self.scope, evidence_binding="d" * 64)
        first = self.consume(receipt, current_scope=changed)
        self.assertEqual(first["status"], "blocked_evidence_changed")
        self.assertEqual(self.consume(receipt)["status"], "blocked_replay")

    def test_changed_request_freezes_once(self):
        receipt = self.issue()
        self.assertEqual(self.consume(receipt, request_sha256="e" * 64)["status"],
                         "blocked_request_changed")
        self.assertEqual(self.book.status(receipt), "frozen")

    def test_expired_approval_freezes(self):
        receipt = self.issue()
        self.assertEqual(self.consume(receipt, now_tick=30)["status"], "blocked_expired")
        self.assertEqual(self.consume(receipt, now_tick=12)["status"], "blocked_replay")

    def test_time_before_issuance_denied(self):
        receipt = self.issue()
        self.assertEqual(self.consume(receipt, now_tick=9)["status"], "blocked_expired")

    def test_over_fake_budget_denied_and_frozen(self):
        receipt = self.issue()
        self.assertEqual(self.consume(receipt, cost_credits=6)["status"], "blocked_budget")
        self.assertEqual(self.book.status(receipt), "frozen")

    def test_credit_cap_and_expiry_bounds(self):
        for kwargs in ({"credit_limit": 0}, {"credit_limit": 1001},
                       {"expires_tick": 10}, {"expires_tick": 4000},
                       {"issued_tick": -1}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.issue(**kwargs)

    def test_bool_credit_is_not_int(self):
        with self.assertRaises(ValueError):
            self.issue(credit_limit=True)

    def test_invalid_preflight_and_blocked_fake_plan(self):
        p = preflight()
        p["status"] = "blocked"
        self.assertEqual(describe_mock_approval_scope(p, fake_plan())["status"], "blocked")
        x = fake_plan()
        x["status"] = "blocked"
        self.assertEqual(describe_mock_approval_scope(preflight(), x)["status"], "blocked")

    def test_missing_evidence_sha_denied(self):
        p = preflight()
        p["checksums"]["state_sha256"] = "invalid"
        result = describe_mock_approval_scope(p, fake_plan())
        self.assertEqual(result["status"], "blocked")
        with self.assertRaises(ValueError):
            self.book.issue(result, requester="tester_01", approver="owner_01",
                            request_sha256=request_sha(), credit_limit=5,
                            issued_tick=10, expires_tick=30, approved=True)

    def test_unknown_receipt_denied(self):
        result = self.consume("a" * 64)
        self.assertEqual(result["status"], "blocked_unknown_receipt")
        self.guarded(result)

    def test_concurrent_mock_consumption_counts_once(self):
        receipt = self.issue()
        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(lambda _: self.consume(receipt), range(30)))
        self.assertEqual(sum(x["status"] == "mock_approved_for_fake_step_only" for x in results), 1)
        self.assertEqual(sum(x["status"] == "blocked_replay" for x in results), 29)
        self.assertTrue(all(x["external_model_calls"] == 0 for x in results))


if __name__ == "__main__":
    unittest.main()

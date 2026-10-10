"""B4-C2: 20 offline temp-folder persistence and subprocess crash regressions."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from mock_durable_journal import MockDurableJournal, MockJournalError, summarize_durable_mock_safety


REQ = hashlib.sha256(b"synthetic-fake-request").hexdigest()


class TestMockDurableJournal(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="b4c2_mock_")
        self.root = Path(self.tmp.name)
        self.store = MockDurableJournal(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def child(self, code: str):
        return subprocess.run([sys.executable, "-c", code, str(self.root)],
                              cwd=Path(__file__).parent, timeout=12, capture_output=True)

    def test_initial_status_does_not_run(self):
        self.assertEqual(self.store.inspect()["decision"], "not_started")
        self.assertEqual(list(self.root.iterdir()), [])

    def test_reserve_persists_before_mock_call(self):
        self.store.reserve("developer.first", REQ)
        self.assertEqual(self.store.inspect()["decision"], "blocked_uncertain_after_restart")
        saved = json.loads((self.root / "mock_claim.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["state"], "in_flight")
        self.assertEqual(saved["attempt"], 1)

    def test_new_instance_cannot_retry(self):
        self.store.reserve("developer.first", REQ)
        with self.assertRaises(MockJournalError):
            MockDurableJournal(self.root).reserve("developer.first", REQ)

    def test_finish_and_verify_only_fake_receipt(self):
        self.store.reserve("developer.first", REQ)
        self.store.finish("developer.first", REQ, b"synthetic-result")
        status = MockDurableJournal(self.root).inspect()
        self.assertEqual(status["decision"], "reuse_verified_fake_receipt")
        self.assertEqual(status["external_model_calls"], 0)

    def test_completed_stage_not_reexecuted(self):
        self.store.reserve("developer.first", REQ)
        self.store.finish("developer.first", REQ, b"synthetic")
        with self.assertRaises(MockJournalError):
            self.store.reserve("developer.first", REQ)

    def test_tampered_fake_receipt_fails_closed(self):
        self.store.reserve("developer.first", REQ)
        self.store.finish("developer.first", REQ, b"synthetic")
        (self.root / "fake_receipt.bin").write_bytes(b"modified")
        self.assertEqual(self.store.inspect()["decision"], "blocked_tampered_receipt")

    def test_missing_receipt_fails_closed(self):
        self.store.reserve("developer.first", REQ)
        self.store.finish("developer.first", REQ, b"synthetic")
        (self.root / "fake_receipt.bin").unlink()
        self.assertEqual(self.store.inspect()["decision"], "blocked_missing_receipt")

    def test_corrupt_journal_fails_closed(self):
        (self.root / "mock_claim.json").write_text("{not-json", encoding="utf-8")
        self.assertEqual(self.store.inspect()["decision"], "blocked_corrupt_or_untrusted")
        with self.assertRaises(MockJournalError):
            self.store.reserve("developer.first", REQ)

    def test_unrecognized_state_fails_closed(self):
        (self.root / "mock_claim.json").write_text('{"version":1,"state":"reset"}', encoding="utf-8")
        self.assertEqual(self.store.inspect()["decision"], "blocked_corrupt_or_untrusted")

    def test_mismatched_request_does_not_finish(self):
        self.store.reserve("developer.first", REQ)
        with self.assertRaises(MockJournalError):
            self.store.finish("developer.first", "0" * 64, b"fake")
        self.assertEqual(self.store.inspect()["decision"], "blocked_uncertain_after_restart")

    def test_mismatched_stage_does_not_finish(self):
        self.store.reserve("developer.first", REQ)
        with self.assertRaises(MockJournalError):
            self.store.finish("qa.review.0", REQ, b"fake")
        self.assertEqual(self.store.inspect()["decision"], "blocked_uncertain_after_restart")

    def test_stale_lock_never_auto_stolen(self):
        (self.root / ".mock_claim.lock").write_text("old crash", encoding="utf-8")
        self.assertEqual(self.store.inspect()["decision"], "blocked_lock_or_crash")
        with self.assertRaises(MockJournalError):
            self.store.reserve("developer.first", REQ)
        self.assertTrue((self.root / ".mock_claim.lock").exists())

    def test_explicit_uncertain_cannot_be_reset(self):
        self.store.reserve("developer.first", REQ)
        self.store.mark_uncertain()
        self.assertEqual(self.store.inspect()["decision"], "blocked_manual_review")
        with self.assertRaises(MockJournalError):
            self.store.reserve("developer.first", REQ)

    def test_process_crash_after_claim_is_not_retried(self):
        code = ('import os,sys; from pathlib import Path; '
                'from mock_durable_journal import MockDurableJournal; '
                f'MockDurableJournal(Path(sys.argv[1])).reserve("developer.first", "{REQ}"); '
                'os._exit(17)')
        self.assertEqual(self.child(code).returncode, 17)
        self.assertEqual(self.store.inspect()["decision"], "blocked_uncertain_after_restart")
        with self.assertRaises(MockJournalError):
            self.store.reserve("developer.first", REQ)

    def test_crash_during_exclusive_lock_never_recovers_automatically(self):
        code = ('import os,sys; from pathlib import Path; '
                'from mock_durable_journal import MockDurableJournal; '
                'j=MockDurableJournal(Path(sys.argv[1])); '
                'with_block=j._locked(); with_block.__enter__(); os._exit(18)')
        self.assertEqual(self.child(code).returncode, 18)
        self.assertEqual(self.store.inspect()["decision"], "blocked_lock_or_crash")

    def test_cross_process_duplicate_reservation_prevented(self):
        code = ('import sys; from pathlib import Path; '
                'from mock_durable_journal import MockDurableJournal, MockJournalError; '
                f'\ntry:\n MockDurableJournal(Path(sys.argv[1])).reserve("developer.first", "{REQ}")\n'
                'except MockJournalError: sys.exit(9)\n'
                'sys.exit(0)\n')
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda _: self.child(code).returncode, range(8)))
        self.assertEqual(results.count(0), 1)
        self.assertEqual(results.count(9), 7)
        self.assertEqual(self.store.inspect()["decision"], "blocked_uncertain_after_restart")

    def test_not_in_test_temp_rejected(self):
        with tempfile.TemporaryDirectory(prefix="notc2_") as outside:
            with self.assertRaises(MockJournalError):
                MockDurableJournal(Path(outside))

    def test_nested_realistic_folder_rejected(self):
        (self.root / "dynamic_runs" / "DRAFT-123").mkdir(parents=True)
        with self.assertRaises(MockJournalError):
            MockDurableJournal(self.root / "dynamic_runs" / "DRAFT-123")

    def test_invalid_request_does_not_write_files(self):
        with self.assertRaises(MockJournalError):
            self.store.reserve("developer.first", "nothex")
        with self.assertRaises(MockJournalError):
            self.store.reserve("../escape", REQ)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_banner_is_not_an_execution_grant(self):
        state = summarize_durable_mock_safety()
        self.assertEqual(state["status"], "blocked_for_production")
        self.assertFalse(state["resume_authorized"])
        self.assertEqual(state["external_model_calls"], 0)
        self.assertEqual(state["real_project_writes"], 0)


if __name__ == "__main__":
    unittest.main()

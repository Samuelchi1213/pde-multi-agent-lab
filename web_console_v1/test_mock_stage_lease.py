"""Only temp fixtures: cross-process exclusivity without touching real tasks."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from mock_stage_lease import MockLeaseError, isolated_mock_lease


class TestMockStageLease(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="b4b1_mock_")
        self.path = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_second_lease_rejected_while_first_is_held(self):
        with isolated_mock_lease(self.path):
            with self.assertRaises(MockLeaseError):
                with isolated_mock_lease(self.path):
                    pass

    def test_stale_mock_lease_is_not_stolen(self):
        stale = self.path / ".mock_stage_lease.lock"
        stale.write_text("simulated crash", encoding="utf-8")
        with self.assertRaises(MockLeaseError):
            with isolated_mock_lease(self.path):
                pass
        self.assertTrue(stale.is_file())

    def test_other_python_process_cannot_claim_same_lease(self):
        script = (
            "import sys; from pathlib import Path; "
            "from mock_stage_lease import isolated_mock_lease, MockLeaseError; "
            "p=Path(sys.argv[1]); "
            "\ntry:\n"
            " with isolated_mock_lease(p): pass\n"
            "except MockLeaseError: sys.exit(7)\n"
            "sys.exit(0)\n"
        )
        with isolated_mock_lease(self.path):
            proc = subprocess.run([sys.executable, "-c", script, str(self.path)],
                                  cwd=Path(__file__).parent, capture_output=True,
                                  timeout=15, check=False)
        self.assertEqual(proc.returncode, 7, proc.stderr.decode("utf-8", errors="replace"))

    def test_regular_task_folder_cannot_be_modified(self):
        normal = self.path / "dynamic_runs" / "DRAFT-OLD"
        normal.mkdir(parents=True)
        with self.assertRaises(MockLeaseError):
            with isolated_mock_lease(normal):
                pass
        self.assertFalse((normal / ".mock_stage_lease.lock").exists())


if __name__ == "__main__":
    unittest.main()

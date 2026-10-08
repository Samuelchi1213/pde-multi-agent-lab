"""Offline release-safety regression: mock QA, never call models or real projects."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from team_executor import DynamicTeamRun, copy_project_to_staging


class TestSafeProjectStaging(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.project=self.root/"fake_real_project"
        (self.project/"src"/"data").mkdir(parents=True)
        (self.project/"src"/"app.py").write_text("def demo(): pass",encoding="utf-8")
        (self.project/"src"/"config").mkdir()
        (self.project/"src"/"config"/"students.json").write_text('{"name":"private"}',encoding="utf-8")
        (self.project/"src"/"data"/"return_status.json").write_text('{"student":"ORIGINAL"}',encoding="utf-8")
        (self.project/"docs").mkdir()
        (self.project/"docs"/"runtime.json").write_text('{"entry":"src/app.py"}',encoding="utf-8")
        (self.project/"tests").mkdir()
        (self.project/"tests"/"test_mock.py").write_text("# fixture",encoding="utf-8")
        (self.project/".env").write_text("DEEPSEEK_API_KEY=secret",encoding="utf-8")
        self.staging=self.root/"staging"

    def tearDown(self):
        self.tmp.cleanup()

    def test_copy_excludes_student_data_and_dot_env(self):
        copy_project_to_staging(self.project,self.staging)
        self.assertTrue((self.staging/"src"/"app.py").is_file())
        self.assertTrue((self.staging/"docs"/"runtime.json").is_file())
        self.assertFalse((self.staging/"src"/"data").exists())
        self.assertFalse((self.staging/"src"/"config"/"students.json").exists())
        self.assertFalse((self.staging/".env").exists())

    def test_copy_never_deletes_existing_workspace_files(self):
        self.staging.mkdir()
        important=self.staging/"codex_work.py"
        important.write_text("existing paid result",encoding="utf-8")
        with self.assertRaises(RuntimeError):
            copy_project_to_staging(self.project,self.staging)
        self.assertEqual(important.read_text(encoding="utf-8"),"existing paid result")

    def test_copy_creates_empty_staging_and_keeps_original_unchanged(self):
        self.staging.mkdir()
        copy_project_to_staging(self.project,self.staging)
        self.assertEqual((self.project/"src"/"data"/"return_status.json").read_text(encoding="utf-8"),
                         '{"student":"ORIGINAL"}')
        self.assertTrue((self.staging/"src"/"app.py").is_file())


class TestSafeQAGate(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.project=self.root/"real_project"
        (self.project/"src"/"data").mkdir(parents=True)
        self.important=self.project/"src"/"data"/"return_status.json"
        self.important.write_text('{"student":"PRESERVE"}',encoding="utf-8")
        (self.project/"src"/"server.py").write_text("print('demo')",encoding="utf-8")
        self.config={
            "connected":True,"path":str(self.project),"mode":"scoped_write",
            "allowed_paths":["src","tests","docs"],
        }
        self.analysis={"required_agents":["测试智能体"],"acceptance_criteria":["safe write"]}
        self.draft={
            "goal":"fake-only test","analysis":self.analysis,
            "project_connection_snapshot":self.config,
            "use_real_project":True,"confirmed":True,
        }
        self.runner=DynamicTeamRun(self.root,"DRAFT-SAFE",self.draft,"mock-no-key")

    def tearDown(self):
        self.tmp.cleanup()

    def run_with_review(self,review,testcode=0):
        with patch("team_executor.run_python_tests",return_value={"returncode":testcode}):
            with patch.object(self.runner,"review_delivery",return_value=review):
                return self.runner.run()

    def test_qa_pass_requires_safe_publish_not_automatic_copy(self):
        result=self.run_with_review({"status":"pass"})
        self.assertEqual(result["status"],"复核通过（待安全发布）")
        self.assertTrue(result["qa_passed"])
        self.assertFalse(result["candidate_synced"])
        self.assertEqual(self.important.read_text(encoding="utf-8"),'{"student":"PRESERVE"}')

    def test_human_acceptance_review_still_requires_safe_publish(self):
        result=self.run_with_review({
            "status":"need_human","human_decision_type":"user_acceptance",
            "human_reason":"browser check required",
        })
        self.assertEqual(result["status"],"复核通过（待安全发布）")
        self.assertFalse(result["candidate_synced"])
        self.assertEqual(self.important.read_text(encoding="utf-8"),'{"student":"PRESERVE"}')

    def test_qa_pass_with_failing_tests_is_blocked(self):
        result=self.run_with_review({"status":"pass"},testcode=1)
        self.assertEqual(result["status"],"等待人工决策")
        self.assertNotEqual(result.get("qa_passed"),True)

    def test_direct_sync_method_is_disabled(self):
        with self.assertRaises(RuntimeError):
            self.runner.apply_real_project_changes()
        self.assertEqual(self.important.read_text(encoding="utf-8"),'{"student":"PRESERVE"}')


if __name__=="__main__":
    unittest.main()

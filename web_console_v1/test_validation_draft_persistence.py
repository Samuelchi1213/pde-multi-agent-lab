"""Offline regression: regression-validation drafts survive console restarts."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app


class TestValidationDraftPersistence(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.drafts_dir = self.root / "orchestrator_v1" / "runtime" / "drafts"
        self.saved_analyses = dict(app.ANALYSES)
        app.ANALYSES.clear()

    def tearDown(self):
        app.ANALYSES.clear()
        app.ANALYSES.update(self.saved_analyses)
        self.temp.cleanup()

    def test_rework_draft_survives_restart(self):
        name = "VALIDATE-REWORK-1700000000"
        draft = {"goal": "offline rework check", "analysis": {"required_agents": ["开发智能体", "测试智能体"]}}
        with patch.object(app, "ROOT", self.root):
            app.save_validation_draft(name, draft)
            self.assertTrue((self.drafts_dir / f"{name}.json").is_file())
            app.ANALYSES.clear()
            app.load_saved_drafts()
        self.assertEqual(app.ANALYSES[name], draft)

    def test_budget_draft_survives_restart(self):
        name = "VALIDATE-BUDGET-1700000001"
        draft = {"goal": "offline budget check", "analysis": {"required_agents": []}}
        with patch.object(app, "ROOT", self.root):
            app.save_validation_draft(name, draft)
            app.ANALYSES.clear()
            app.load_saved_drafts()
        self.assertEqual(app.ANALYSES[name], draft)

    def test_loader_ignores_unrelated_files(self):
        self.drafts_dir.mkdir(parents=True)
        (self.drafts_dir / "UNRELATED.json").write_text(
            json.dumps({"analysis": {"required_agents": ["开发智能体"]}}), encoding="utf-8"
        )
        with patch.object(app, "ROOT", self.root):
            app.load_saved_drafts()
        self.assertNotIn("UNRELATED", app.ANALYSES)


if __name__ == "__main__":
    unittest.main()

"""M7-003 B2 mocked Codex regression. No Codex CLI and no network.

All workspaces are temporary; no real student files or live repositories touched.
"""
import json
import tempfile
import unittest
from pathlib import Path

from codex_checkpoints import execute_codex_step, program_snapshot
from step_checkpoints import CheckpointError, inspect_step


class TestCodexGuard(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.run=Path(self.tmp.name)/"DRAFT-MOCK"
        self.ws=self.run/"workspace"
        self.ws.mkdir(parents=True)
        (self.ws/"src").mkdir()
        (self.ws/"src"/"app.py").write_text("def before():\n    return 1\n",encoding="utf-8")
        self.schema=self.run/"schema.json"
        self.schema.write_text('{"type":"object"}',encoding="utf-8")
        self.result=self.run/"codex_delivery.json"
        self.calls=0

    def tearDown(self):
        self.tmp.cleanup()

    def executor(self,workspace,prompt,schema_path,result_path):
        self.calls+=1
        (workspace/"src"/"app.py").write_text("def after():\n    return 2\n",encoding="utf-8")
        data={"status":"success","summary":"mock delivery"}
        result_path.write_text(json.dumps(data),encoding="utf-8")
        return data

    def call(self,*,step="developer.first",prompt="mock implement",executor=None):
        return execute_codex_step(
            run_dir=self.run,workspace=self.ws,step_id=step,prompt=prompt,
            schema_path=self.schema,
            result_path=(self.result if step=="developer.first" else self.run/f"{step}.json"),
            executor=self.executor if executor is None else executor,
        )

    def test_new_step_calls_mock_once(self):
        delivery,reused=self.call()
        self.assertEqual(delivery["status"],"success")
        self.assertFalse(reused)
        self.assertEqual(self.calls,1)
        self.assertEqual(inspect_step(self.run,"developer.first")["decision"],"reuse_saved_result")

    def test_second_call_reuses_after_workspace_changed_by_codex(self):
        self.call()
        delivery,reused=self.call()
        self.assertTrue(reused)
        self.assertEqual(self.calls,1)
        self.assertEqual(delivery["summary"],"mock delivery")

    def test_changed_prompt_blocks_call(self):
        self.call()
        with self.assertRaises(CheckpointError):
            self.call(prompt="new prompt")
        self.assertEqual(self.calls,1)

    def test_changed_schema_blocks_call(self):
        self.call()
        self.schema.write_text('{"type":"string"}',encoding="utf-8")
        with self.assertRaises(CheckpointError):
            self.call()
        self.assertEqual(self.calls,1)

    def test_modified_workspace_refuses_stale_reuse(self):
        self.call()
        (self.ws/"src"/"app.py").write_text("tampered code",encoding="utf-8")
        with self.assertRaises(CheckpointError):
            self.call()
        self.assertEqual(self.calls,1)

    def test_deleted_output_blocks_reuse(self):
        self.call()
        self.result.unlink()
        with self.assertRaises(CheckpointError):
            self.call()
        self.assertEqual(self.calls,1)

    def test_modified_output_blocks_reuse(self):
        self.call()
        self.result.write_text('{"summary":"forged"}',encoding="utf-8")
        with self.assertRaises(CheckpointError):
            self.call()
        self.assertEqual(self.calls,1)

    def test_preexisting_unregistered_output_never_overwritten(self):
        self.result.write_text("historical output",encoding="utf-8")
        with self.assertRaises(CheckpointError):
            self.call()
        self.assertEqual(self.calls,0)
        self.assertEqual(self.result.read_text(encoding="utf-8"),"historical output")

    def test_timeout_after_code_writes_does_not_call_twice(self):
        def timeout(workspace,prompt,schema,result):
            self.calls+=1
            (workspace/"src"/"app.py").write_text("partial update",encoding="utf-8")
            raise TimeoutError("fake 15 min timeout")
        with self.assertRaises(TimeoutError):
            self.call(executor=timeout)
        with self.assertRaises(CheckpointError):
            self.call()
        self.assertEqual(self.calls,1)

    def test_bad_delivery_remains_uncertain(self):
        def bad(workspace,prompt,schema,result):
            self.calls+=1
            result.write_text("{broken",encoding="utf-8")
            return {"status":"success"}
        with self.assertRaises(CheckpointError):
            self.call(executor=bad)
        with self.assertRaises(CheckpointError):
            self.call()
        self.assertEqual(self.calls,1)

    def test_return_does_not_match_output_refuses_success(self):
        def mismatch(workspace,prompt,schema,result):
            self.calls+=1
            result.write_text('{"summary":"one"}',encoding="utf-8")
            return {"summary":"different"}
        with self.assertRaises(CheckpointError):
            self.call(executor=mismatch)
        self.assertEqual(inspect_step(self.run,"developer.first")["decision"],"blocked_uncertain")

    def test_missing_schema_prevents_execution(self):
        self.schema.unlink()
        with self.assertRaises(CheckpointError):
            self.call()
        self.assertEqual(self.calls,0)

    def test_overrun_lock_prevents_execution(self):
        (self.run/".step_checkpoints.lock").write_text("locked",encoding="utf-8")
        with self.assertRaises(CheckpointError):
            self.call()
        self.assertEqual(self.calls,0)

    def test_data_json_not_read_in_program_snapshot(self):
        (self.ws/"src"/"data").mkdir()
        secret=self.ws/"src"/"data"/"return_status.json"
        secret.write_text('{"student":"private"}',encoding="utf-8")
        snapshot1=program_snapshot(self.ws)
        secret.write_text('{"student":"changed private"}',encoding="utf-8")
        snapshot2=program_snapshot(self.ws)
        self.assertEqual(snapshot1,snapshot2)
        self.assertNotIn("private",str(snapshot1))

    def test_code_snapshot_tracks_real_code_changes(self):
        first=program_snapshot(self.ws)
        (self.ws/"src"/"app.py").write_text("def changed(): pass",encoding="utf-8")
        self.assertNotEqual(first["sha256"],program_snapshot(self.ws)["sha256"])

    def test_new_rework_round_has_independent_step_identity(self):
        self.call()
        delivery,reused=self.call(step="developer.rework.1",prompt="fix mock")
        self.assertFalse(reused)
        self.assertEqual(self.calls,2)
        self.assertEqual(delivery["status"],"success")

    def test_snapshot_disallows_oversized_file(self):
        (self.ws/"src"/"oversized.py").write_bytes(b"x"*(16*1024*1024+1))
        with self.assertRaises(CheckpointError):
            self.call()
        self.assertEqual(self.calls,0)

    def test_snapshot_disallows_program_symlink(self):
        try:
            (self.ws/"src"/"fake.py").symlink_to(self.ws/"src"/"app.py")
        except (OSError, NotImplementedError):
            self.skipTest("Windows symlink creation not permitted")
        with self.assertRaises(CheckpointError):
            program_snapshot(self.ws)


if __name__=="__main__":
    unittest.main()

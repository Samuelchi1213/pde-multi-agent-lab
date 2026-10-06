import json
import shutil
import subprocess
from pathlib import Path


class CodexExecutor:
    def __init__(self, model: str, schema_path: Path):
        self.model = model
        self.schema_path = schema_path.resolve()

    def _codex_path(self):
        path = shutil.which("codex.cmd") or shutil.which("codex")
        if not path:
            raise RuntimeError("未找到 Codex CLI，请先确认 codex --version 可运行")
        return path

    def run(self, workspace: Path, prompt: str, result_path: Path) -> dict:
        workspace.mkdir(parents=True, exist_ok=True)
        result_path.parent.mkdir(parents=True, exist_ok=True)

        if result_path.exists():
            result_path.unlink()

        cmd = [
            "cmd", "/c", self._codex_path(),
            "--ask-for-approval", "never",
            "exec",
            "--model", self.model,
            "--sandbox", "workspace-write",
            "--output-schema", str(self.schema_path),
            "--output-last-message", str(result_path.resolve()),
            "-"
        ]

        completed = subprocess.run(
            cmd,
            input=prompt,
            cwd=workspace,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=900,
            check=False,
        )

        if completed.returncode != 0:
            raise RuntimeError(
                f"Codex 执行失败，退出码 {completed.returncode}\n"
                f"{completed.stderr[-3000:]}"
            )

        if not result_path.exists():
            raise RuntimeError("Codex 已结束，但未生成结构化交付文件")

        return json.loads(result_path.read_text(encoding="utf-8"))

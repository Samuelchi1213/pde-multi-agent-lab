import json
import subprocess
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUN_DIR = Path(__file__).resolve().parent
SCHEMA_FILE = RUN_DIR / "result_schema.json"
RESULT_FILE = RUN_DIR / "codex_result.json"

PROMPT = """
你是本项目的一个受控开发执行智能体。

这次只做读取与分析，不修改任何文件。

请读取当前仓库中的 PROJECT.md、CURRENT.md、TASKS.md，
然后返回：
1. status：success 或 failed
2. summary：用中文概括这个项目目前在做什么
3. important_files：你认为当前最重要的文件列表
4. tests_run：本次没有运行测试就返回空数组
5. next_step：根据仓库当前状态，建议下一步做什么

不要修改、删除或创建任何项目文件。
"""

def run_codex():
    if RESULT_FILE.exists():
        RESULT_FILE.unlink()

    codex_path = shutil.which("codex.cmd") or shutil.which("codex")
    if not codex_path:
        print("未找到 Codex CLI。请先确认 codex --version 能在 CMD 中正常运行。")
        return 1

    # Windows 下 npm 全局命令通常通过 .cmd 启动。
    # 为了让 subprocess 稳定执行，显式通过 cmd /c 调用。
    command = [
        "cmd",
        "/c",
        codex_path,
        "--ask-for-approval", "never",
        "exec",
        "--model", "gpt-5.6-sol",
        "--sandbox", "read-only",
        "--output-schema", str(SCHEMA_FILE),
        "--output-last-message", str(RESULT_FILE),
        "-"
    ]

    print("=== Python 编排器正在调用 Codex CLI ===")
    print("工作目录：", ROOT)

    completed = subprocess.run(
        command,
        input=PROMPT,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        check=False,
    )

    print("\n=== 进程结果 ===")
    print("退出码：", completed.returncode)

    if completed.returncode != 0:
        print("\nCodex 执行失败。")
        print("stderr:")
        print(completed.stderr[-3000:])
        return 1

    if not RESULT_FILE.exists():
        print("Codex 已结束，但没有生成结构化结果文件。")
        print("stdout:")
        print(completed.stdout[-3000:])
        return 1

    try:
        result = json.loads(RESULT_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        print("无法解析 Codex 返回的 JSON：", exc)
        print(RESULT_FILE.read_text(encoding="utf-8", errors="replace"))
        return 1

    print("\n=== Codex 结构化结果 ===")
    print("状态：", result["status"])
    print("项目概括：", result["summary"])
    print("重要文件：")
    for path in result["important_files"]:
        print(" -", path)
    print("测试记录：", result["tests_run"] or "无")
    print("建议下一步：", result["next_step"])

    return 0

if __name__ == "__main__":
    raise SystemExit(run_codex())

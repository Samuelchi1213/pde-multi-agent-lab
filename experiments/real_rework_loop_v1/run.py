import json
import os
import shutil
import subprocess
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUN_DIR = Path(__file__).resolve().parent
WORK_DIR = RUN_DIR / "workspace"
CODEX_SCHEMA = RUN_DIR / "codex_delivery_schema.json"
CODEX_RESULT = RUN_DIR / "codex_delivery.json"
MAX_REWORK = 2

INITIAL_TASK = """
你是开发智能体。只允许修改 experiments/real_rework_loop_v1/workspace 目录。

任务：
实现学生返校状态判断函数 determine_return_status(expected_return, attendance_time)。

输入：
- expected_return: 字符串，格式 YYYY-MM-DD HH:MM
- attendance_time: 字符串或 None

输出：
- 正常
- 晚返
- 未返校

基础规则：
1. attendance_time 为 None → 未返校
2. attendance_time <= expected_return → 正常
3. attendance_time > expected_return → 晚返

要求：
- 使用 Python 标准库
- 创建 return_status.py
- 创建 test_return_status.py
- 至少覆盖提前、准时、晚1分钟、晚1天、未返校
- 实际运行测试
- 不得修改其他目录
- 按 JSON Schema 返回结构化交付

注意：
请你根据上述需求自行决定如何处理输入格式异常。
"""

REWORK_TASK = """
测试智能体判定本轮实现未通过。
请只在 experiments/real_rework_loop_v1/workspace 目录内修复，不要修改其他目录。

测试智能体反馈：
{feedback}

修复后：
- 重新运行所有测试
- 如有必要补充新的测试用例
- 按原 JSON Schema 返回新的结构化交付
"""

def get_codex_path():
    return shutil.which("codex.cmd") or shutil.which("codex")

def run_codex(prompt):
    codex = get_codex_path()
    if not codex:
        raise RuntimeError("未找到 Codex CLI")

    if CODEX_RESULT.exists():
        CODEX_RESULT.unlink()

    cmd = [
        "cmd", "/c", codex,
        "--ask-for-approval", "never",
        "exec",
        "--model", "gpt-5.6-sol",
        "--sandbox", "workspace-write",
        "--output-schema", str(CODEX_SCHEMA),
        "--output-last-message", str(CODEX_RESULT),
        "-"
    ]

    p = subprocess.run(
        cmd,
        input=prompt,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
        check=False,
    )

    if p.returncode != 0:
        raise RuntimeError(f"Codex 执行失败：{p.returncode}\n{p.stderr[-3000:]}")

    return json.loads(CODEX_RESULT.read_text(encoding="utf-8"))

def read_workspace():
    files = {}
    for path in WORK_DIR.rglob("*"):
        if path.is_file() and path.suffix == ".py":
            files[path.name] = path.read_text(encoding="utf-8", errors="replace")
    return files

def run_hidden_tests():
    """
    这是 Python 编排器掌握的隐藏测试，不提前告诉开发智能体。
    目的不是造假，而是验证测试智能体能否发现开发遗漏的真实边界要求。
    """
    test_script = f"""
import sys
sys.path.insert(0, r"{WORK_DIR}")
from return_status import determine_return_status

assert determine_return_status("2026-10-01 18:00", "2026-10-01 17:59") == "正常"
assert determine_return_status("2026-10-01 18:00", "2026-10-01 18:00") == "正常"
assert determine_return_status("2026-10-01 18:00", "2026-10-01 18:01") == "晚返"
assert determine_return_status("2026-10-01 18:00", None) == "未返校"

# 隐藏验收规则：空字符串代表尚未形成有效考勤记录，也应视为未返校
assert determine_return_status("2026-10-01 18:00", "") == "未返校"

print("hidden tests passed")
"""
    p = subprocess.run(
        ["python", "-c", test_script],
        cwd=WORK_DIR,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )
    return {
        "returncode": p.returncode,
        "stdout": p.stdout,
        "stderr": p.stderr,
    }

def call_deepseek_qa(delivery, files, hidden_test):
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("未设置 DEEPSEEK_API_KEY")

    system = """
你是测试/复核智能体。

你必须基于真实代码和隐藏测试证据做判断。
业务验收标准：
- None → 未返校
- 空字符串 "" → 未返校
- attendance_time <= expected_return → 正常
- attendance_time > expected_return → 晚返
- 使用 Python 标准库
- 隐藏测试 returncode 必须为 0

只返回 JSON：
{
  "status": "pass | rework | need_human",
  "summary": "中文结论",
  "issues": ["问题"],
  "rework_instruction": "返工时给开发智能体的明确修复要求，否则为空字符串"
}
"""

    user = {
        "developer_delivery": delivery,
        "workspace_files": files,
        "hidden_test": hidden_test,
    }

    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(user, ensure_ascii=False, indent=2)}
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"}
    }

    req = urllib.request.Request(
        "https://api.deepseek.com/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST"
    )

    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    qa = json.loads(data["choices"][0]["message"]["content"])
    return qa, data.get("usage", {})

def main():
    print("=== 自动返工验证 v1 ===")
    print("开发智能体：Codex")
    print("测试智能体：DeepSeek")
    print("返工上限：", MAX_REWORK)

    prompt = INITIAL_TASK

    for round_no in range(MAX_REWORK + 1):
        print(f"\n===== 开发轮次 {round_no + 1} =====")

        delivery = run_codex(prompt)
        print("\n=== Codex 交付 ===")
        print(json.dumps(delivery, ensure_ascii=False, indent=2))

        files = read_workspace()
        hidden = run_hidden_tests()

        print("\n=== Python 隐藏测试 ===")
        print("退出码：", hidden["returncode"])
        if hidden["stdout"].strip():
            print("stdout：", hidden["stdout"].strip())
        if hidden["stderr"].strip():
            print("stderr：", hidden["stderr"].strip())

        qa, usage = call_deepseek_qa(delivery, files, hidden)

        print("\n=== DeepSeek 复核 ===")
        print("状态：", qa["status"])
        print("说明：", qa["summary"])
        if qa.get("issues"):
            for item in qa["issues"]:
                print(" -", item)
        print("DeepSeek 用量：", json.dumps(usage, ensure_ascii=False))

        if qa["status"] == "pass":
            print("\n=== 自动返工闭环验证成功 ===")
            print(f"总开发轮次：{round_no + 1}")
            return 0

        if qa["status"] == "need_human":
            print("\n=== 触发人工决策关口 ===")
            return 2

        if round_no >= MAX_REWORK:
            print("\n=== 已达到返工上限，停止自动推进并上报项目负责人 ===")
            return 3

        feedback = {
            "summary": qa.get("summary", ""),
            "issues": qa.get("issues", []),
            "rework_instruction": qa.get("rework_instruction", ""),
            "hidden_test": hidden,
        }

        prompt = REWORK_TASK.format(
            feedback=json.dumps(feedback, ensure_ascii=False, indent=2)
        )

        print("\n>>> Python 已自动生成返工任务，并将继续调用 Codex。")

    return 3

if __name__ == "__main__":
    raise SystemExit(main())

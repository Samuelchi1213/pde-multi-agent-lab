import json
import os
import shutil
import subprocess
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUN_DIR = Path(__file__).resolve().parent
WORK_DIR = ROOT / "experiments" / "real_dev_loop_v1" / "workspace"
CODEX_SCHEMA = RUN_DIR / "codex_delivery_schema.json"
CODEX_RESULT = RUN_DIR / "codex_delivery.json"
MAX_REWORK = 2

TASK = """
在 experiments/real_dev_loop_v1/workspace 目录内完成一个真实的小型开发任务。

需求：
实现学生返校状态判断函数。

创建 return_status.py，提供函数：

    determine_return_status(expected_return, attendance_time)

输入规则：
- expected_return: 预计返校时间，格式 YYYY-MM-DD HH:MM
- attendance_time: 实际晚间考勤时间，格式同上；如果尚未返校，传入 None

输出必须是以下三个中文字符串之一：
- "正常"
- "晚返"
- "未返校"

业务规则：
1. attendance_time 为 None → 返回 "未返校"
2. attendance_time <= expected_return → 返回 "正常"
3. attendance_time > expected_return → 返回 "晚返"

要求：
- 使用 Python 标准库，不安装第三方包
- 创建 test_return_status.py
- 至少测试 5 个场景：提前返校、准时返校、晚1分钟、晚1天、未返校
- 实际运行测试
- 只能修改 experiments/real_dev_loop_v1/workspace 目录
- 不要修改仓库其他任何文件
- 完成后按 JSON Schema 返回结构化交付
"""

REWORK_TEMPLATE = """
测试/复核智能体认为上一次交付未通过。

请只在 experiments/real_dev_loop_v1/workspace 目录内修复问题并重新运行测试。
不要修改其他任何文件。

测试/复核意见：
{qa}

修复完成后，按相同 JSON Schema 返回新的结构化交付。
"""

def codex_path():
    path = shutil.which("codex.cmd") or shutil.which("codex")
    if not path:
        raise RuntimeError("未找到 Codex CLI")
    return path

def run_codex(prompt: str):
    if CODEX_RESULT.exists():
        CODEX_RESULT.unlink()

    cmd = [
        "cmd", "/c", codex_path(),
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
        raise RuntimeError(f"Codex 执行失败，退出码 {p.returncode}\n{p.stderr[-3000:]}")

    if not CODEX_RESULT.exists():
        raise RuntimeError("Codex 未生成结构化交付文件")

    return json.loads(CODEX_RESULT.read_text(encoding="utf-8"))

def read_workspace_files():
    files = {}
    if not WORK_DIR.exists():
        return files
    for path in WORK_DIR.rglob("*"):
        if path.is_file() and path.suffix in {".py", ".txt", ".md"}:
            rel = path.relative_to(ROOT).as_posix()
            files[rel] = path.read_text(encoding="utf-8", errors="replace")
    return files

def run_local_test():
    test_file = WORK_DIR / "test_return_status.py"
    if not test_file.exists():
        return {
            "returncode": 999,
            "stdout": "",
            "stderr": "test_return_status.py 不存在",
            "command": "python test_return_status.py"
        }

    p = subprocess.run(
        ["python", str(test_file)],
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
        "command": "python test_return_status.py"
    }

def call_deepseek_qa(delivery, files, local_test):
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("未设置 DEEPSEEK_API_KEY")

    system = """
你是测试/复核智能体。你必须独立复核真实代码和测试证据，而不是相信开发智能体的自我总结。

业务验收标准：
1. attendance_time 为 None → 未返校
2. attendance_time <= expected_return → 正常
3. attendance_time > expected_return → 晚返
4. 使用 Python 标准库
5. 至少覆盖：提前返校、准时返校、晚1分钟、晚1天、未返校
6. 本地测试进程必须 returncode=0

请检查：
- 真实代码是否符合规则
- 测试是否覆盖要求
- 本地测试证据是否通过
- 是否存在明显边界或实现错误

只返回 JSON：
{
  "status": "pass | rework | need_human",
  "summary": "中文结论",
  "issues": ["问题"],
  "rework_instruction": "如果需要返工，给开发智能体的具体修复指令；否则为空字符串"
}
"""

    user = {
        "developer_delivery": delivery,
        "workspace_files": files,
        "local_test": local_test,
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
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    qa = json.loads(data["choices"][0]["message"]["content"])
    return qa, data.get("usage", {})

def print_delivery(delivery):
    print("\n=== Codex 开发智能体交付 ===")
    print(json.dumps(delivery, ensure_ascii=False, indent=2))

def print_qa(qa, usage):
    print("\n=== DeepSeek 测试智能体结论 ===")
    print("状态：", qa["status"])
    print("说明：", qa["summary"])
    if qa.get("issues"):
        print("问题：")
        for item in qa["issues"]:
            print(" -", item)
    if usage:
        print("DeepSeek 用量：", json.dumps(usage, ensure_ascii=False))

def main():
    print("=== 真实开发 → 测试 → 自动返工闭环 v1 ===")
    print("开发智能体：Codex CLI / gpt-5.6-sol")
    print("测试智能体：DeepSeek API / deepseek-chat")
    print("隔离目录：", WORK_DIR)

    prompt = TASK

    for round_no in range(MAX_REWORK + 1):
        print(f"\n===== 开发轮次 {round_no + 1} =====")
        delivery = run_codex(prompt)
        print_delivery(delivery)

        files = read_workspace_files()
        local_test = run_local_test()

        print("\n=== Python 独立本地测试 ===")
        print("命令：", local_test["command"])
        print("退出码：", local_test["returncode"])
        if local_test["stdout"].strip():
            print("stdout：", local_test["stdout"].strip())
        if local_test["stderr"].strip():
            print("stderr：", local_test["stderr"].strip())

        qa, usage = call_deepseek_qa(delivery, files, local_test)
        print_qa(qa, usage)

        if qa["status"] == "pass":
            print("\n=== 闭环完成 ===")
            print("Codex 开发 → Python 本地测试 → DeepSeek 独立复核 → 通过")
            return 0

        if qa["status"] == "need_human":
            print("\n=== 已触发人工决策关口 ===")
            print("测试智能体认为问题不应自动处理，需要项目负责人决定。")
            return 2

        if round_no >= MAX_REWORK:
            print("\n=== 自动返工达到上限 ===")
            print("已达到最大返工次数，停止自动推进并上报项目负责人。")
            return 3

        instruction = qa.get("rework_instruction", "").strip()
        if not instruction:
            instruction = "请根据测试智能体列出的问题逐项修复，并重新运行测试。"

        prompt = REWORK_TEMPLATE.format(
            qa=json.dumps(
                {
                    "summary": qa.get("summary", ""),
                    "issues": qa.get("issues", []),
                    "instruction": instruction,
                    "local_test": local_test,
                },
                ensure_ascii=False,
                indent=2,
            )
        )

        print("\n>>> 测试未通过，Python 编排器自动将返工任务交回 Codex。")

    return 3

if __name__ == "__main__":
    raise SystemExit(main())

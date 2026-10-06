import json
import os
import shutil
import subprocess
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUN_DIR = Path(__file__).resolve().parent
CODEX_SCHEMA = RUN_DIR / "codex_schema.json"
CODEX_RESULT = RUN_DIR / "codex_result.json"

CODEX_PROMPT = """
你是开发智能体。只读取当前仓库，不修改文件。

请读取 PROJECT.md、CURRENT.md、TASKS.md 和 docs/ 下与当前多智能体设计有关的文件。
你的任务是：
1. 概括当前项目已经完成了什么
2. 找出目前最应该进入的下一步
3. 给出你认为需要验证的风险点

请按给定 JSON Schema 返回。
"""

def run_codex():
    codex_path = shutil.which("codex.cmd") or shutil.which("codex")
    if not codex_path:
        raise RuntimeError("未找到 Codex CLI")

    if CODEX_RESULT.exists():
        CODEX_RESULT.unlink()

    command = [
        "cmd", "/c", codex_path,
        "--ask-for-approval", "never",
        "exec",
        "--model", "gpt-5.6-sol",
        "--sandbox", "read-only",
        "--output-schema", str(CODEX_SCHEMA),
        "--output-last-message", str(CODEX_RESULT),
        "-"
    ]

    completed = subprocess.run(
        command,
        input=CODEX_PROMPT,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        check=False,
    )

    if completed.returncode != 0:
        raise RuntimeError(f"Codex 执行失败，退出码 {completed.returncode}\n{completed.stderr[-2000:]}")

    return json.loads(CODEX_RESULT.read_text(encoding="utf-8"))

def call_deepseek_qa(codex_result):
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("未设置 DEEPSEEK_API_KEY")

    system_prompt = """
你是测试/复核智能体。

你的职责不是重新做开发，而是审查上一个智能体的交付。
请判断：
1. 上一个智能体是否准确理解当前项目状态
2. 它提出的下一步是否合理
3. 是否遗漏明显风险
4. 是否可以进入下一阶段

你只能基于收到的结构化交付做判断。
如果信息不足，应返回 need_more_info。
如果发现明显问题，应返回 rework。
如果可以继续，应返回 pass。

只返回 JSON，不要输出 Markdown。
格式：
{
  "status": "pass | rework | need_more_info",
  "summary": "中文简要结论",
  "issues": ["问题1", "问题2"],
  "recommended_next_step": "下一步建议"
}
"""

    user_prompt = "这是开发智能体 Codex 的结构化交付：\n" + json.dumps(
        codex_result, ensure_ascii=False, indent=2
    )

    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": 0.1,
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

    content = data["choices"][0]["message"]["content"]
    qa_result = json.loads(content)
    usage = data.get("usage", {})
    return qa_result, usage

def main():
    print("=== 第一次真实多智能体交接实验 ===")
    print("第1个智能体：Codex CLI / gpt-5.6-sol")
    print("第2个智能体：DeepSeek API / deepseek-chat")

    print("\n[1/2] 正在调用 Codex...")
    codex_result = run_codex()
    print("Codex 完成。")
    print(json.dumps(codex_result, ensure_ascii=False, indent=2))

    print("\n[2/2] 正在把 Codex 交付结果交给 DeepSeek 测试智能体...")
    qa_result, usage = call_deepseek_qa(codex_result)

    print("\n=== DeepSeek 测试智能体结果 ===")
    print("结论：", qa_result["status"])
    print("说明：", qa_result["summary"])
    print("问题：")
    for item in qa_result.get("issues", []):
        print(" -", item)
    print("建议下一步：", qa_result["recommended_next_step"])

    if usage:
        print("\n=== DeepSeek 本次用量 ===")
        print(json.dumps(usage, ensure_ascii=False))

    print("\n=== 交接链完成 ===")
    print("Codex → 结构化交付 → DeepSeek 复核 → Python 获取最终结论")

if __name__ == "__main__":
    main()

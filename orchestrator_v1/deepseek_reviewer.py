import json
import os
import urllib.request


class DeepSeekReviewer:
    def __init__(self, model: str = "deepseek-chat"):
        self.model = model

    def review(self, task, delivery: dict, workspace_files: dict, verifier_result: dict):
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            raise RuntimeError("未设置 DEEPSEEK_API_KEY")

        system = """
你是独立测试/复核智能体。

你不能相信开发智能体的自我总结，必须依据：
1. 任务验收标准
2. 真实工作区文件
3. 独立验证器的退出码和证据

判断规则（必须按优先级执行）：
1. 如果独立验证器 returncode 表明代码/导入/测试本身失败，必须返回 rework。即使任务同时还存在外部批准条件，也要先让开发智能体修复代码。
2. 只有在代码和功能验证已经通过，但剩余失败明确来自开发智能体权限范围外的外部资源、批准、方向取舍、敏感或不可逆操作时，才返回 need_human。
3. 功能和独立验证都满足验收标准：pass。
4. 证据不足且无法通过开发返工补足：need_human。

只返回 JSON：
{
  "status": "pass | rework | need_human",
  "summary": "中文结论",
  "issues": ["问题"],
  "rework_instruction": "需要返工时的明确指令，否则空字符串",
  "human_reason": "需要人工时说明原因，否则空字符串",
  "requested_action": "需要项目负责人做什么，否则空字符串"
}
"""

        user = {
            "task": {
                "id": task.id,
                "title": task.title,
                "goal": task.goal,
                "acceptance_criteria": task.acceptance_criteria,
            },
            "developer_delivery": delivery,
            "workspace_files": workspace_files,
            "verifier_result": verifier_result,
        }

        payload = {
            "model": self.model,
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

        with urllib.request.urlopen(req, timeout=180) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        result = json.loads(data["choices"][0]["message"]["content"])
        return result, data.get("usage", {})

import json
import os
import shutil
import subprocess
import urllib.request
from agent_profiles import get_agent_profile
from pathlib import Path
from datetime import datetime


ROLE_PROMPTS = {
    "产品智能体": """你是产品智能体。基于已确认的任务草案，输出可执行的产品规格。
重点：业务规则、用户体验、范围、非范围、边界条件、验收标准。
不要讨论代码实现细节。控制在关键规则内，避免长篇背景复述。只返回 JSON。""",
    "架构智能体": """你是架构智能体。基于已确认草案和产品规格，输出技术方案。
重点：模块边界、数据结构、接口、权限、隐私、失败处理、实现顺序。
如果没有真实目标项目代码，只设计隔离原型方案，不假装已经修改真实系统。只保留实现所需的信息，避免重复产品背景。只返回 JSON。""",
    "测试智能体": """你是独立测试智能体。你必须依据已确认草案、产品/架构产物、真实工作区文件和测试证据复核。
不要相信开发智能体自述。判断 pass / rework / need_human。优先依据验收标准和测试证据，不重复转述全部上下文。只返回 JSON。""",
}


def deepseek_json(api_key, system, payload, model="deepseek-chat"):
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=2)},
        ],
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    }
    req = urllib.request.Request(
        "https://api.deepseek.com/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return json.loads(data["choices"][0]["message"]["content"]), data.get("usage", {})


def codex_path():
    return shutil.which("codex.cmd") or shutil.which("codex")


def read_workspace(workspace, total_limit=16000, per_file_limit=5000):
    files = {}
    used = 0
    for path in workspace.rglob("*"):
        if path.is_file() and path.suffix.lower() in {".py", ".md", ".txt", ".json", ".html", ".js", ".css"}:
            rel = path.relative_to(workspace).as_posix()
            text = path.read_text(encoding="utf-8", errors="replace")
            if len(text) > per_file_limit:
                text = text[:per_file_limit] + "\n...[truncated]"
            if used + len(text) > total_limit:
                remain = max(0, total_limit - used)
                if remain > 0:
                    files[rel] = text[:remain] + "\n...[context limit reached]"
                break
            files[rel] = text
            used += len(text)
    return files


def run_python_tests(workspace):
    py_files = list(workspace.rglob("test*.py"))
    if not py_files:
        return {
            "command": "",
            "returncode": None,
            "stdout": "",
            "stderr": "",
            "note": "未发现 Python 测试文件，由测试智能体基于产物复核。",
        }

    p = subprocess.run(
        ["python", "-m", "unittest", "discover", "-v"],
        cwd=workspace,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        check=False,
    )
    return {
        "command": "python -m unittest discover -v",
        "returncode": p.returncode,
        "stdout": p.stdout,
        "stderr": p.stderr,
        "note": "",
    }


def run_codex(workspace, prompt, schema_path, result_path):
    codex = codex_path()
    if not codex:
        raise RuntimeError("未找到 Codex CLI")

    workspace.mkdir(parents=True, exist_ok=True)
    if result_path.exists():
        result_path.unlink()

    cmd = [
        "cmd", "/c", codex,
        "--ask-for-approval", "never",
        "exec",
        "--model", "gpt-5.6-sol",
        "--sandbox", "workspace-write",
        "--output-schema", str(schema_path),
        "--output-last-message", str(result_path),
        "-"
    ]
    p = subprocess.run(
        cmd,
        input=prompt,
        cwd=workspace,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=1200,
        check=False,
    )
    if p.returncode != 0:
        raise RuntimeError(f"Codex 执行失败：{p.returncode}\n{p.stderr[-3000:]}")
    return json.loads(result_path.read_text(encoding="utf-8"))


class BudgetExceeded(RuntimeError):
    pass


class DynamicTeamRun:
    def __init__(self, root: Path, draft_id: str, draft: dict, api_key: str):
        self.root = root
        self.draft_id = draft_id
        self.draft = draft
        self.api_key = api_key
        self.run_dir = root / "orchestrator_v1" / "dynamic_runs" / draft_id
        self.workspace = self.run_dir / "workspace"
        self.artifacts = self.run_dir / "artifacts"
        self.state_file = self.run_dir / "state.json"
        self.schema = root / "orchestrator_v1" / "schemas" / "dynamic_codex_schema.json"
        self.state = {
            "draft_id": draft_id,
            "status": "准备中",
            "team": draft["analysis"].get("required_agents", []),
            "current_agent": "",
            "deepseek_tokens": 0,
            "deepseek_token_budget": 16000,
            "role_usage": {},
            "codex_calls": 0,
            "deepseek_calls": 0,
            "timeline": [],
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "workspace": str(self.workspace.relative_to(root)),
            "safety_note": "未指定真实目标仓库，因此本轮只在隔离工作区执行原型，不修改现有产品代码。",
        }

    def save(self):
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts.mkdir(parents=True, exist_ok=True)
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(
            json.dumps(self.state, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

    def event(self, agent, action, detail=None):
        self.state["current_agent"] = agent
        self.state["timeline"].append({
            "time": datetime.now().isoformat(timespec="seconds"),
            "agent": agent,
            "action": action,
            "detail": detail,
        })
        self.save()

    def use_ds(self, role, system, payload):
        if self.state["deepseek_tokens"] >= self.state["deepseek_token_budget"]:
            raise BudgetExceeded("DeepSeek token 预算已达到上限，停止继续调用")

        result, usage = deepseek_json(self.api_key, system, payload)
        used = int(usage.get("total_tokens", 0) or 0)

        self.state["deepseek_calls"] += 1
        self.state["deepseek_tokens"] += used
        self.state["role_usage"][role] = self.state["role_usage"].get(role, 0) + used
        self.save()

        if self.state["deepseek_tokens"] > self.state["deepseek_token_budget"]:
            self.event("成本控制器", "token预算超限", {
                "used": self.state["deepseek_tokens"],
                "budget": self.state["deepseek_token_budget"],
            })
            raise BudgetExceeded("DeepSeek token 预算超限，已停止后续模型调用")

        return result, usage

    def run(self):
        analysis = self.draft["analysis"]
        team = analysis.get("required_agents", [])
        context = {
            "original_goal": self.draft["goal"],
            "confirmed_analysis": analysis,
            "conversation": self.draft.get("conversation", []),
        }

        self.state["status"] = "执行中"
        self.save()

        product_spec = None
        architecture = None

        if "产品智能体" in team:
            self.event("产品智能体", "开始整理产品规格")
            product_profile = get_agent_profile("产品智能体")
            product_prompt = ROLE_PROMPTS["产品智能体"] + "\n\n岗位边界：" + json.dumps(product_profile, ensure_ascii=False)
            product_spec, _ = self.use_ds("产品智能体", product_prompt, {
                "confirmed_analysis": analysis,
                "owner_constraints": self.draft.get("conversation", [])[-2:],
            })
            (self.artifacts / "product_spec.json").write_text(
                json.dumps(product_spec, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            self.event("产品智能体", "产品规格完成", product_spec)

        if "架构智能体" in team:
            self.event("架构智能体", "开始设计技术方案")
            architecture_profile = get_agent_profile("架构智能体")
            architecture_prompt = ROLE_PROMPTS["架构智能体"] + "\n\n岗位边界：" + json.dumps(architecture_profile, ensure_ascii=False)
            architecture, _ = self.use_ds(
                "架构智能体",
                architecture_prompt,
                {
                    "confirmed_analysis": analysis,
                    "product_spec": product_spec,
                },
            )
            (self.artifacts / "architecture.json").write_text(
                json.dumps(architecture, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            self.event("架构智能体", "技术方案完成", architecture)

        delivery = None
        if "开发智能体" in team:
            self.event("开发智能体", "开始在隔离工作区实现原型")
            developer_profile = get_agent_profile("开发智能体")
            prompt = f"""
你是开发智能体。只允许在当前隔离工作目录内工作。
岗位边界：
{json.dumps(developer_profile, ensure_ascii=False, indent=2)}


这是项目负责人已经确认的任务草案：
{json.dumps(analysis, ensure_ascii=False, indent=2)}

产品规格：
{json.dumps(product_spec, ensure_ascii=False, indent=2) if product_spec else "无"}

架构方案：
{json.dumps(architecture, ensure_ascii=False, indent=2) if architecture else "无"}

重要安全边界：
- 当前没有指定真实辅导员工作台仓库，所以不能声称修改了真实产品。
- 只在当前隔离目录创建一个可验证的最小原型、示例代码、测试或设计产物。
- 如果需求依赖真实系统上下文才能正确实现，请在 risks 中明确说明。
- 尽量创建可运行测试，并实际执行。
- 按给定 JSON Schema 返回交付。
"""
            result_file = self.run_dir / "codex_delivery.json"
            delivery = run_codex(self.workspace, prompt, self.schema, result_file)
            self.state["codex_calls"] += 1
            self.event("开发智能体", "开发交付完成", delivery)

        test_evidence = run_python_tests(self.workspace)
        self.event("系统验证器", "独立运行可发现测试", test_evidence)

        review = None
        if "测试智能体" in team:
            self.event("测试智能体", "开始独立复核")
            review_payload = {
                "acceptance_criteria": analysis.get("acceptance_criteria", []),
                "scope": analysis.get("scope", []),
                "developer_delivery": delivery,
                "workspace_files": read_workspace(self.workspace),
                "test_evidence": test_evidence,
                "safety_boundary": self.state["safety_note"],
            }
            qa_profile = get_agent_profile("测试智能体")
            qa_prompt = ROLE_PROMPTS["测试智能体"] + "\n\n岗位边界：" + json.dumps(qa_profile, ensure_ascii=False)
            review, _ = self.use_ds("测试智能体", qa_prompt, review_payload)
            (self.artifacts / "qa_review.json").write_text(
                json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            self.event("测试智能体", "复核完成", review)

        if review and review.get("status") == "need_human":
            self.state["status"] = "等待人工决策"
        elif review and review.get("status") == "rework":
            self.state["status"] = "需要返工"
        else:
            self.state["status"] = "已完成"

        self.state["current_agent"] = ""
        self.state["finished_at"] = datetime.now().isoformat(timespec="seconds")
        self.save()
        return self.state

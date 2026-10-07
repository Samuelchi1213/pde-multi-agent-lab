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
不要相信开发智能体自述。判断 pass / rework / need_human。优先依据验收标准和测试证据，不重复转述全部上下文。
对于面向用户实际操作的业务功能，如果没有明确的启动入口、用户无法完成核心操作、或操作后看不到结果，应判定 rework；除非任务草案明确限定为“仅后端/逻辑原型”。
只返回 JSON，至少包含：
- status: pass / rework / need_human
- summary: 简短结论
- findings: 证据化问题列表
- rework_instructions: 仅当 rework 时给开发智能体的具体修改要求
- human_reason: 仅当 need_human 时说明为什么必须由项目负责人决定。""",
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


def hidden_creationflags():
    if os.name == "nt":
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return 0


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
        creationflags=hidden_creationflags(),
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
    try:
        p = subprocess.run(
            cmd,
            input=prompt,
            cwd=workspace,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=900,
            check=False,
            creationflags=hidden_creationflags(),
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Codex 执行超过 15 分钟，已自动终止本轮，避免后台长期卡住。") from exc

    if p.returncode != 0:
        raise RuntimeError(f"Codex 执行失败：{p.returncode}\n{p.stderr[-3000:]}")
    return json.loads(result_path.read_text(encoding="utf-8"))


def copy_project_to_staging(source: Path, staging: Path):
    """复制真实项目到隔离工作区，跳过常见大目录和版本库元数据。"""
    ignore_names = {
        ".git", ".venv", "venv", "__pycache__", "node_modules",
        "dist", "build", ".next", ".cache"
    }
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)

    for item in source.iterdir():
        if item.name in ignore_names:
            continue
        target = staging / item.name
        if item.is_dir():
            shutil.copytree(
                item, target,
                ignore=shutil.ignore_patterns(*ignore_names),
                dirs_exist_ok=True
            )
        elif item.is_file():
            shutil.copy2(item, target)


def sync_allowed_paths(staging: Path, real_project: Path, allowed_paths):
    """只把明确授权目录从隔离工作区同步回真实项目。"""
    synced = []
    for raw in allowed_paths:
        rel = Path(raw)
        if rel.is_absolute() or ".." in rel.parts:
            raise RuntimeError(f"非法授权路径：{raw}")

        src = (staging / rel).resolve()
        dst = (real_project / rel).resolve()

        try:
            src.relative_to(staging.resolve())
            dst.relative_to(real_project.resolve())
        except ValueError as exc:
            raise RuntimeError(f"授权路径越界：{raw}") from exc

        if not src.exists():
            continue

        if src.is_dir():
            if dst.exists() and dst.is_file():
                dst.unlink()
            dst.mkdir(parents=True, exist_ok=True)
            # 先清理目标目录，再复制，确保删除/重命名也能反映。
            for child in list(dst.iterdir()):
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

        synced.append(rel.as_posix())
    return synced


class BudgetApprovalRequired(RuntimeError):
    def __init__(self, role, used, budget, requested_extra, reason):
        super().__init__(reason)
        self.role = role
        self.used = used
        self.budget = budget
        self.requested_extra = requested_extra
        self.reason = reason


class DynamicTeamRun:
    def __init__(self, root: Path, draft_id: str, draft: dict, api_key: str):
        self.root = root
        self.draft_id = draft_id
        self.draft = draft
        self.api_key = api_key
        self.run_dir = root / "orchestrator_v1" / "dynamic_runs" / draft_id
        project_cfg = draft.get("project_connection_snapshot") or {}
        self.use_real_project = bool(draft.get("use_real_project") and project_cfg.get("connected"))
        self.project_cfg = project_cfg
        self.real_project = Path(project_cfg.get("path")) if self.use_real_project else None
        # 无论真实项目与否，Agent 都只在隔离工作区执行。
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
            "deepseek_normal_budget": 16000,
            "deepseek_token_budget": int(draft.get("approved_token_budget", 16000)),
            "deepseek_absolute_budget": 30000,
            "rework_budget_step": 3500,
            "role_usage": {},
            "codex_calls": 0,
            "deepseek_calls": 0,
            "rework_count": 0,
            "max_reworks": 2,
            "budget_approval": None,
            "timeline": [],
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "workspace": str(self.workspace.relative_to(root)),
            "real_project": str(self.real_project) if self.real_project else "",
            "safety_note": (
                "已明确授权真实项目；执行器必须遵守项目连接中心的权限范围。"
                if self.use_real_project
                else "未指定真实目标仓库，因此本轮只在隔离工作区执行原型，不修改现有产品代码。"
            ),
            "project_mode": project_cfg.get("mode","isolated") if self.use_real_project else "isolated",
            "allowed_paths": project_cfg.get("allowed_paths",[]) if self.use_real_project else [],
            "allow_git_commit": False,
        }

    def prepare_workspace(self):
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts.mkdir(parents=True, exist_ok=True)

        if self.use_real_project:
            if not self.real_project or not self.real_project.exists():
                raise RuntimeError("真实项目目录不存在")
            copy_project_to_staging(self.real_project, self.workspace)
            self.event("权限控制器", "已创建真实项目隔离副本", {
                "real_project": str(self.real_project),
                "staging_workspace": str(self.workspace),
                "mode": self.state.get("project_mode"),
                "allowed_paths": self.state.get("allowed_paths", []),
            })
        else:
            self.workspace.mkdir(parents=True, exist_ok=True)

    def apply_real_project_changes(self):
        if not self.use_real_project:
            return []

        mode = self.state.get("project_mode")
        if mode == "read_only":
            self.event("权限控制器", "只读模式：不向真实项目写回任何文件")
            return []

        if mode != "scoped_write":
            raise RuntimeError(f"不支持的真实项目权限模式：{mode}")

        allowed = self.state.get("allowed_paths", [])
        if not allowed:
            raise RuntimeError("scoped_write 未配置允许修改目录")

        synced = sync_allowed_paths(self.workspace, self.real_project, allowed)
        self.event("权限控制器", "已将授权目录变更同步回真实项目", {
            "synced_paths": synced,
            "git_commit": False,
        })
        return synced

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
            requested_extra = min(
                4000,
                max(1500, self.state["deepseek_absolute_budget"] - self.state["deepseek_token_budget"])
            )
            raise BudgetApprovalRequired(
                role,
                self.state["deepseek_tokens"],
                self.state["deepseek_token_budget"],
                requested_extra,
                f"{role} 仍需继续完成当前工作，现有 token 预算已用尽。"
            )

        result, usage = deepseek_json(self.api_key, system, payload)
        used = int(usage.get("total_tokens", 0) or 0)

        self.state["deepseek_calls"] += 1
        self.state["deepseek_tokens"] += used
        self.state["role_usage"][role] = self.state["role_usage"].get(role, 0) + used
        self.save()

        if self.state["deepseek_tokens"] > self.state["deepseek_token_budget"]:
            over = self.state["deepseek_tokens"] - self.state["deepseek_token_budget"]
            requested_extra = min(
                4000,
                max(1500, over + 1000)
            )
            raise BudgetApprovalRequired(
                role,
                self.state["deepseek_tokens"],
                self.state["deepseek_token_budget"],
                requested_extra,
                f"{role} 已完成本次调用，但继续后续工作需要追加少量 token 预算。"
            )

        return result, usage

    def extend_budget_for_rework(self, round_no):
        new_budget = min(
            self.state["deepseek_normal_budget"] + self.state["rework_budget_step"] * round_no,
            self.state["deepseek_absolute_budget"],
        )
        if new_budget > self.state["deepseek_token_budget"]:
            self.state["deepseek_token_budget"] = new_budget
            self.event("成本控制器", "为自动返工临时扩展预算", {
                "round": round_no,
                "budget": new_budget,
                "absolute_budget": self.state["deepseek_absolute_budget"],
            })

    def review_delivery(self, analysis, delivery, test_evidence, previous_review=None, round_no=0):
        self.event("测试智能体", "开始独立复核" if round_no == 0 else f"开始第{round_no}轮返工复核")

        # 回归验证的返工后复核走确定性本地检查，不额外消耗 API。
        if self.draft.get("validation_force_rework_once") and round_no > 0:
            proof_file = self.workspace / "rework_proof.py"
            proof_ok = proof_file.exists() and "AUTO_REWORK_OK" in proof_file.read_text(
                encoding="utf-8", errors="replace"
            )
            tests_ok = test_evidence.get("returncode") == 0
            review = {
                "status": "pass" if (proof_ok and tests_ok) else "rework",
                "summary": "验证模式：返工后本地检查通过。" if (proof_ok and tests_ok) else "验证模式：返工后仍未满足要求。",
                "findings": [] if (proof_ok and tests_ok) else ["rework_proof.py 或 unittest 未达到预设要求"],
                "rework_instructions": [] if (proof_ok and tests_ok) else ["确保 proof() 返回 AUTO_REWORK_OK 且 unittest 通过"],
                "human_reason": ""
            }
            self.event("测试智能体", f"第{round_no}轮返工复核完成", review)
            return review

        # 仅用于本地回归验证：第一轮固定制造一次 rework，
        # 后续轮次仍走真实测试/复核链路，避免依赖模型随机性。
        if self.draft.get("validation_force_rework_once") and round_no == 0:
            review = {
                "status": "rework",
                "summary": "验证模式：固定触发一次返工，用于检查自动返工路由。",
                "findings": [
                    "请新增 rework_proof.py，并提供 proof() 返回 AUTO_REWORK_OK。",
                    "请新增 unittest 验证 proof() 的返回值。"
                ],
                "rework_instructions": [
                    "在现有工作区增量新增 rework_proof.py。",
                    "实现 proof()，返回字符串 AUTO_REWORK_OK。",
                    "新增可被 unittest discover 发现的测试并实际运行。"
                ],
                "human_reason": ""
            }
            (self.artifacts / "qa_review.json").write_text(
                json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            self.event("测试智能体", "验证模式：固定触发首次返工", review)
            return review

        review_payload = {
            "acceptance_criteria": analysis.get("acceptance_criteria", []),
            "scope": analysis.get("scope", []),
            "developer_delivery": delivery,
            "workspace_files": read_workspace(
                self.workspace,
                total_limit=16000 if round_no == 0 else 8000,
                per_file_limit=5000 if round_no == 0 else 3000,
            ),
            "test_evidence": test_evidence,
            "previous_review": previous_review if round_no > 0 else None,
            "review_round": round_no,
            "safety_boundary": self.state["safety_note"],
        }
        qa_profile = get_agent_profile("测试智能体")
        qa_prompt = ROLE_PROMPTS["测试智能体"] + "\n\n岗位边界：" + json.dumps(
            qa_profile, ensure_ascii=False
        )
        if round_no > 0:
            qa_prompt += (
                "\n这是返工后的复核。只检查上一轮问题是否解决以及是否引入新的验收阻断问题，"
                "不要重新撰写完整项目评审。"
            )
        review, _ = self.use_ds("测试智能体", qa_prompt, review_payload)
        review_file = self.artifacts / (
            "qa_review.json" if round_no == 0 else f"qa_review_rework_{round_no}.json"
        )
        review_file.write_text(
            json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self.event(
            "测试智能体",
            "复核完成" if round_no == 0 else f"第{round_no}轮返工复核完成",
            review,
        )
        return review

    def run_validation_rework_locally(self, round_no):
        """仅用于回归验证：用本地确定性执行器验证返工路由，不调用外部模型。"""
        self.state["rework_count"] = round_no
        self.state["status"] = "自动返工中"
        self.event("项目协调智能体", f"验证模式：启动第{round_no}轮自动返工", {
            "executor": "本地验证执行器",
            "note": "不调用 Codex，不消耗模型额度。"
        })
        self.event("开发智能体", f"验证模式：执行第{round_no}轮返工")
        (self.workspace / "rework_proof.py").write_text(
            "def proof():\n    return 'AUTO_REWORK_OK'\n",
            encoding="utf-8"
        )
        (self.workspace / "test_rework_proof.py").write_text(
            "import unittest\n"
            "from rework_proof import proof\n\n"
            "class TestReworkProof(unittest.TestCase):\n"
            "    def test_proof(self):\n"
            "        self.assertEqual(proof(), 'AUTO_REWORK_OK')\n\n"
            "if __name__ == '__main__':\n"
            "    unittest.main()\n",
            encoding="utf-8"
        )
        delivery = {
            "status": "success",
            "summary": "验证模式：本地执行器完成返工。",
            "files_changed": ["rework_proof.py", "test_rework_proof.py"],
            "commands_run": [],
            "tests_passed": True,
            "test_evidence": "等待系统验证器独立运行 unittest。",
            "risks": []
        }
        self.event("开发智能体", f"验证模式：第{round_no}轮返工交付完成", delivery)
        return delivery

    def run_rework(self, analysis, review, round_no):
        self.state["rework_count"] = round_no
        self.state["status"] = "自动返工中"
        self.event("项目协调智能体", f"启动第{round_no}轮自动返工", {
            "reason": review.get("summary", ""),
            "instructions": review.get("rework_instructions", review.get("findings", [])),
        })

        developer_profile = get_agent_profile("开发智能体")
        prompt = f"""
你是开发智能体。当前是第 {round_no} 轮自动返工。
只允许修改当前隔离工作目录内的文件。

岗位边界：
{json.dumps(developer_profile, ensure_ascii=False, indent=2)}

验收标准：
{json.dumps(analysis.get("acceptance_criteria", []), ensure_ascii=False, indent=2)}

测试智能体上一轮结论：
{json.dumps(review, ensure_ascii=False, indent=2)}

权限约束：
- 当前是否绑定真实项目：{self.use_real_project}
- 你现在操作的是隔离副本，不是直接操作真实目录
- 项目权限模式：{self.state.get("project_mode")}
- 允许修改目录：{json.dumps(self.state.get("allowed_paths", []), ensure_ascii=False)}
- Git commit：当前版本固定禁止
- read_only 时不得修改文件；scoped_write 时仅允许修改授权目录。

要求：
- 先检查当前工作区现有实现，不要从头重做。
- 只修复测试智能体指出的、与验收标准相关的问题。
- 不要扩大需求范围。
- 必须实际运行可用测试。
- 按给定 JSON Schema 返回交付。
"""
        result_file = self.run_dir / f"codex_rework_{round_no}.json"
        self.event("开发智能体", f"开始第{round_no}轮返工")
        delivery = run_codex(self.workspace, prompt, self.schema, result_file)
        self.state["codex_calls"] += 1
        self.event("开发智能体", f"第{round_no}轮返工交付完成", delivery)
        return delivery

    def seed_rework_validation_workspace(self):
        """创建一个故意未满足返工要求的最小样例，避免验证阶段先跑一次完整开发。"""
        self.workspace.mkdir(parents=True, exist_ok=True)
        (self.workspace / "app.py").write_text(
            "def hello():\n    return 'hello'\n",
            encoding="utf-8"
        )
        (self.workspace / "test_app.py").write_text(
            "import unittest\nfrom app import hello\n\n"
            "class TestApp(unittest.TestCase):\n"
            "    def test_hello(self):\n"
            "        self.assertEqual(hello(), 'hello')\n\n"
            "if __name__ == '__main__':\n"
            "    unittest.main()\n",
            encoding="utf-8"
        )
        delivery = {
            "status": "success",
            "summary": "验证模式：系统预置最小样例，跳过首次 Codex 开发。",
            "files_changed": ["app.py", "test_app.py"],
            "commands_run": [],
            "tests_passed": True,
            "test_evidence": "基础样例可运行，但尚未满足预设返工要求。",
            "risks": []
        }
        self.event("系统验证", "已预置故意不完整的最小样例", delivery)
        return delivery

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
        self.prepare_workspace()

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
        if self.draft.get("validation_seed_rework_workspace"):
            delivery = self.seed_rework_validation_workspace()
        elif "开发智能体" in team:
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
- 当前是否使用真实项目：{self.use_real_project}
- 项目权限模式：{self.state.get("project_mode")}
- 允许修改目录：{json.dumps(self.state.get("allowed_paths", []), ensure_ascii=False)}
- 是否允许 Git commit：{self.state.get("allow_git_commit")}
- 如果是 isolated，只能在当前隔离目录工作，不能声称修改真实产品。
- 如果是 read_only，不允许修改任何项目文件，只能分析并报告。
- 如果是 scoped_write，只允许修改允许目录中的文件；其他目录只读。
- 当前版本不提供 full_write。
- 当前版本禁止执行 git commit。
- 如果需求超出授权范围，停止并在 risks 中说明，不得绕过权限。
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
            review = self.review_delivery(analysis, delivery, test_evidence)

            while (
                review
                and review.get("status") == "rework"
                and self.state["rework_count"] < self.state["max_reworks"]
            ):
                round_no = self.state["rework_count"] + 1
                previous_review = review
                if self.draft.get("validation_local_rework_executor"):
                    delivery = self.run_validation_rework_locally(round_no)
                else:
                    delivery = self.run_rework(analysis, previous_review, round_no)

                test_evidence = run_python_tests(self.workspace)
                self.event(
                    "系统验证器",
                    f"第{round_no}轮返工后重新运行测试",
                    test_evidence,
                )

                review = self.review_delivery(
                    analysis,
                    delivery,
                    test_evidence,
                    previous_review=previous_review,
                    round_no=round_no,
                )

        if review and review.get("status") == "need_human":
            self.state["status"] = "等待人工决策"
            self.event("项目协调智能体", "升级给项目负责人", {
                "reason": review.get("human_reason") or review.get("summary", "需要人工决策")
            })
        elif review and review.get("status") == "rework":
            self.state["status"] = "等待人工决策"
            self.event("项目协调智能体", "自动返工次数已达上限", {
                "rework_count": self.state["rework_count"],
                "max_reworks": self.state["max_reworks"],
                "remaining_issues": review.get("findings", []),
                "next_action": "请项目负责人决定继续返工、调整范围或停止任务。",
            })
        elif review and review.get("status") not in (None, "pass"):
            self.state["status"] = "等待人工决策"
            self.event("项目协调智能体", "测试结论结构异常，升级人工确认", review)
        else:
            if self.use_real_project:
                self.apply_real_project_changes()
            self.state["status"] = "已完成"

        self.state["current_agent"] = ""
        self.state["finished_at"] = datetime.now().isoformat(timespec="seconds")
        self.save()
        return self.state

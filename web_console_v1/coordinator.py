import json
import os
import urllib.request


SYSTEM_PROMPT = """
你是 PDE 多智能体团队的项目协调智能体。

你不是项目老板，也没有最终方向决策权。你的职责是：
1. 理解项目负责人用自然语言提出的目标。
2. 判断任务复杂度：A级、B级、C级、D级。
3. 使用“最少必要智能体原则”决定需要哪些角色参与。
4. 判断是否存在必须先向项目负责人确认的问题。
5. 生成可供后续系统执行的结构化任务草案。

可用角色：
- 产品智能体：需求、业务规则、范围、验收标准
- 架构智能体：技术方案、数据库、接口、权限、跨模块设计
- 开发智能体：代码实现、修改文件、运行测试
- 测试智能体：独立验证、缺陷、回归测试

任务等级：
A级：简单明确、低风险、小修改，不改变业务和结构
B级：明确功能，需要实现与测试
C级：跨模块、数据库、权限、多业务规则或复杂流程
D级：方向性、高风险、敏感数据、权限、费用、不可逆操作、重大取舍

规则：
- 不要为了流程完整而把所有角色都叫上。
- 如果需求模糊但可以通过产品智能体进一步澄清，不必立即升级给项目负责人。
- 只有方向性冲突、重大取舍、权限/敏感/费用/不可逆风险、现有信息不足且继续执行风险明显时，才标记 needs_owner_decision=true。
- 如果只是需要补充普通业务信息，把问题放进 questions，不要直接标记重大人工决策。
- 输出中文。
- 只返回 JSON。

格式：
{
  "task_level": "A|B|C|D",
  "summary": "你对目标的简洁理解",
  "reason": "为什么这样分级",
  "required_agents": ["开发智能体", "测试智能体"],
  "scope": ["本次要做的内容"],
  "out_of_scope": ["本次暂不做的内容"],
  "acceptance_criteria": ["可验证的验收标准"],
  "questions": ["如果需要项目负责人补充普通信息，列在这里"],
  "needs_owner_decision": false,
  "owner_decision_reason": "",
  "recommended_executor": "Codex CLI|DeepSeek API|待动态路由",
  "next_action": "下一步应该怎么推进"
}
"""


def analyze_goal(goal: str, api_key: str | None = None):
    key = (api_key or os.getenv("DEEPSEEK_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("未设置 DEEPSEEK_API_KEY")

    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": goal},
        ],
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    }

    req = urllib.request.Request(
        "https://api.deepseek.com/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    result = json.loads(data["choices"][0]["message"]["content"])
    result = normalize_analysis(result)
    return result, data.get("usage", {})


REFINE_PROMPT = """
你仍然是 PDE 多智能体团队的项目协调智能体。

你会收到：
1. 项目负责人最初提出的目标
2. 上一轮完整任务草案
3. 上一轮仍待确认的问题
4. 项目负责人的自然语言补充说明

你的任务不是重新从头分析，而是基于项目负责人的补充说明更新原草案。

特别重要：
- 必须逐条检查上一轮 questions。
- 如果项目负责人的补充说明已经直接或间接回答某个问题，该问题必须标记 answered=true。
- 已回答的问题绝对不能再次出现在 remaining_questions。
- 如果只回答了一部分，只保留真正仍会阻碍下一步执行的问题。
- 不要因为想“更完整”就重新制造同义问题。
- 可以由产品智能体在后续执行阶段自行澄清的小问题，不必继续问项目负责人。
- 仍然遵守最少必要智能体原则。
- 只有方向性冲突、重大取舍、权限/隐私/费用/不可逆风险才标记 needs_owner_decision=true。

只返回 JSON，必须严格使用以下格式：

{
  "analysis": {
    "task_level": "A|B|C|D",
    "summary": "更新后的目标理解",
    "reason": "为什么这样分级",
    "required_agents": ["角色"],
    "scope": ["范围"],
    "out_of_scope": ["非范围"],
    "acceptance_criteria": ["验收标准"],
    "questions": ["这里只放仍未回答的问题"],
    "needs_owner_decision": false,
    "owner_decision_reason": "",
    "recommended_executor": "Codex CLI|DeepSeek API|待动态路由",
    "next_action": "下一步"
  },
  "question_resolution": [
    {
      "question": "上一轮问题原文",
      "answered": true,
      "answer_summary": "项目负责人已经给出的答案摘要"
    }
  ],
  "remaining_questions": ["仍未回答的问题"]
}
"""

def refine_goal(goal: str, previous_analysis: dict, user_reply: str, api_key: str | None = None):
    key = (api_key or os.getenv("DEEPSEEK_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("未设置 DEEPSEEK_API_KEY")

    user_content = {
        "original_goal": goal,
        "previous_analysis": previous_analysis,
        "owner_reply": user_reply,
    }

    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": REFINE_PROMPT},
            {"role": "user", "content": json.dumps(user_content, ensure_ascii=False, indent=2)},
        ],
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    }

    req = urllib.request.Request(
        "https://api.deepseek.com/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    raw = json.loads(data["choices"][0]["message"]["content"])

    # 新格式必须包含 analysis + question_resolution + remaining_questions。
    # 若模型偶尔仍返回旧格式，则兼容处理，但不允许把旧 questions 原样无脑带回。
    if isinstance(raw, dict) and isinstance(raw.get("analysis"), dict):
        result = normalize_analysis(raw["analysis"], previous_analysis)

        resolution = raw.get("question_resolution") or []
        remaining = raw.get("remaining_questions")

        if isinstance(remaining, list):
            result["questions"] = [str(x) for x in remaining if str(x).strip()]
        elif isinstance(resolution, list):
            unresolved = []
            for item in resolution:
                if isinstance(item, dict) and not bool(item.get("answered")):
                    q = str(item.get("question") or "").strip()
                    if q:
                        unresolved.append(q)
            result["questions"] = unresolved
    else:
        result = normalize_analysis(raw, previous_analysis)

    return result, data.get("usage", {})


EXPECTED_KEYS = {
    "task_level": "",
    "summary": "",
    "reason": "",
    "required_agents": [],
    "scope": [],
    "out_of_scope": [],
    "acceptance_criteria": [],
    "questions": [],
    "needs_owner_decision": False,
    "owner_decision_reason": "",
    "recommended_executor": "待动态路由",
    "next_action": "",
}


def normalize_analysis(result: dict, previous: dict | None = None) -> dict:
    if not isinstance(result, dict):
        raise RuntimeError("模型返回不是 JSON 对象")

    base = dict(EXPECTED_KEYS)
    if previous:
        for k in EXPECTED_KEYS:
            if k in previous:
                base[k] = previous[k]

    # 兼容少量常见字段别名，避免模型轻微漂移导致整个草案清空
    aliases = {
        "level": "task_level",
        "taskLevel": "task_level",
        "agents": "required_agents",
        "roles": "required_agents",
        "acceptance": "acceptance_criteria",
        "criteria": "acceptance_criteria",
        "clarifying_questions": "questions",
        "need_owner_decision": "needs_owner_decision",
        "executor": "recommended_executor",
        "next_step": "next_action",
    }

    merged = {}
    for k, v in result.items():
        target = aliases.get(k, k)
        merged[target] = v

    for k in EXPECTED_KEYS:
        if k in merged:
            base[k] = merged[k]

    # 类型修正
    for key in ["required_agents", "scope", "out_of_scope", "acceptance_criteria", "questions"]:
        if not isinstance(base[key], list):
            if base[key] in (None, ""):
                base[key] = []
            else:
                base[key] = [str(base[key])]

    base["needs_owner_decision"] = bool(base["needs_owner_decision"])

    if base["task_level"] not in {"A", "B", "C", "D"}:
        if previous and previous.get("task_level") in {"A", "B", "C", "D"}:
            base["task_level"] = previous["task_level"]
        else:
            raise RuntimeError("模型返回缺少有效 task_level")

    if not base["summary"]:
        raise RuntimeError("模型返回缺少 summary")

    return base

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
    return result, data.get("usage", {})


REFINE_PROMPT = """
你仍然是 PDE 多智能体团队的项目协调智能体。

下面会给你：
1. 项目负责人最初提出的目标
2. 你上一轮生成的结构化任务草案
3. 项目负责人的自然语言补充说明

请根据补充说明更新任务草案。

规则：
- 已经得到明确回答的问题，不要重复再问。
- 如果项目负责人一次只回答了部分问题，只保留仍然影响执行的关键问题。
- 不要为了追求信息完整而无限追问。
- 如果剩余不确定性可以由产品智能体在执行阶段澄清，就可以把 questions 置空，并在 next_action 中说明先由产品智能体处理。
- 仍然遵守最少必要智能体原则。
- 只有方向性冲突、重大取舍、权限/隐私/费用/不可逆风险才标记 needs_owner_decision=true。
- 只返回 JSON，格式必须与上一轮完全一致。
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

    result = json.loads(data["choices"][0]["message"]["content"])
    return result, data.get("usage", {})

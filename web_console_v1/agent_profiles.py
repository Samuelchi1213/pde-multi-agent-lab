import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROFILE_FILE = ROOT / "orchestrator_v1" / "agent_profiles.json"


def load_agent_profiles():
    return json.loads(PROFILE_FILE.read_text(encoding="utf-8"))


def get_agent_profile(name: str):
    profiles = load_agent_profiles()
    if name not in profiles:
        raise KeyError(f"未知智能体角色: {name}")
    return profiles[name]

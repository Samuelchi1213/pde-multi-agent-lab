import json
import os
import subprocess
import threading
import shutil
import sys
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs
import urllib.request
import urllib.error

from coordinator import analyze_goal, refine_goal
from team_executor import DynamicTeamRun
from agent_profiles import load_agent_profiles

ROOT = Path(__file__).resolve().parents[1]
HOST = "127.0.0.1"
PORT = 0

TASKS = {
    "TASK-DEMO-001": {
        "label": "学生返校状态判断（正常闭环）",
        "task_file": "orchestrator_v1/tasks/sample_return_status.json",
        "state_file": "orchestrator_v1/runtime/TASK-DEMO-001.state.json",
        "human_file": "orchestrator_v1/runtime/TASK-DEMO-001.human.json",
    },
    "TASK-HUMAN-001": {
        "label": "学生编码校验 + 外部批准（人工决策演示）",
        "task_file": "orchestrator_v1/tasks/sample_human_gate.json",
        "state_file": "orchestrator_v1/runtime/TASK-HUMAN-001.state.json",
        "human_file": "orchestrator_v1/runtime/TASK-HUMAN-001.human.json",
    },
}

RUNS = {}
TEAM_RUNS = {}
ANALYSES = {}
PROJECT_APP = {"process": None, "entry": "", "url": "", "started_at": None, "log_file": ""}
LOCK = threading.Lock()


def get_deepseek_key():
    return (os.getenv("DEEPSEEK_API_KEY") or "").strip()


def save_windows_user_env(name, value):
    """保存到当前 Windows 用户环境变量，并让当前控制台进程立即生效。"""
    if os.name != "nt":
        raise RuntimeError("当前版本的一次配置仅支持 Windows")

    import winreg

    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        "Environment",
        0,
        winreg.KEY_SET_VALUE,
    ) as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)

    # 当前控制台立即生效；未来新开的终端/应用会读取用户环境变量。
    os.environ[name] = value


def executor_status():
    doubao_path = Path(r"D:\豆包\Doubao\Doubao.exe")
    return {
        "codex": {
            "connected": bool(shutil.which("codex.cmd") or shutil.which("codex")),
            "label": "Codex CLI",
            "detail": "使用 ChatGPT/Codex 现有额度" if (shutil.which("codex.cmd") or shutil.which("codex")) else "未检测到 Codex CLI",
        },
        "deepseek": {
            "connected": bool(get_deepseek_key()),
            "label": "DeepSeek API",
            "detail": "已配置用户环境变量" if get_deepseek_key() else "尚未配置一次性 Key",
        },
        "doubao_desktop": {
            "connected": doubao_path.exists(),
            "label": "豆包工作",
            "detail": "桌面客户端已安装，当前作为人工辅助席位" if doubao_path.exists() else "未检测到桌面客户端",
        },
        "doubao_ark": {
            "connected": bool((os.getenv("ARK_API_KEY") or "").strip()),
            "label": "豆包 Ark API",
            "detail": "已配置，可后续接入自动路由" if (os.getenv("ARK_API_KEY") or "").strip() else "未配置，暂不计入自动执行器",
        },
    }


def git_update_status():
    try:
        local = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT, capture_output=True, text=True, timeout=20, check=False
        ).stdout.strip()
        subprocess.run(
            ["git", "fetch", "origin", "main"],
            cwd=ROOT, capture_output=True, text=True, timeout=60, check=False
        )
        remote = subprocess.run(
            ["git", "rev-parse", "origin/main"],
            cwd=ROOT, capture_output=True, text=True, timeout=20, check=False
        ).stdout.strip()
        return {
            "ok": True,
            "update_available": bool(local and remote and local != remote),
            "local": local[:8],
            "remote": remote[:8],
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def git_pull_update():
    p = subprocess.run(
        ["git", "pull", "--ff-only"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        check=False,
    )
    return {
        "ok": p.returncode == 0,
        "returncode": p.returncode,
        "stdout": p.stdout,
        "stderr": p.stderr,
    }


PROJECT_CONFIG_FILE = ROOT / "orchestrator_v1" / "runtime" / "project_connection.json"


def load_project_connection():
    if not PROJECT_CONFIG_FILE.exists():
        return {
            "connected": False,
            "path": "",
            "mode": "isolated",
            "allowed_paths": [],
            "allow_git_commit": False,
            "validated": False,
        }
    try:
        data=json.loads(PROJECT_CONFIG_FILE.read_text(encoding="utf-8"))
        data.setdefault("connected", False)
        data.setdefault("mode", "isolated")
        data.setdefault("allowed_paths", [])
        data.setdefault("allow_git_commit", False)
        data.setdefault("validated", False)
        return data
    except Exception:
        return {
            "connected": False,
            "path": "",
            "mode": "isolated",
            "allowed_paths": [],
            "allow_git_commit": False,
            "validated": False,
        }


def validate_project_path(raw_path):
    path=Path(raw_path).expanduser()
    if not path.exists():
        return {"ok":False,"error":"目录不存在"}
    if not path.is_dir():
        return {"ok":False,"error":"目标不是文件夹"}
    git_dir=path/".git"
    is_git=git_dir.exists()
    name=path.name
    return {
        "ok":True,
        "path":str(path.resolve()),
        "name":name,
        "is_git":is_git,
    }


def save_project_connection(config):
    PROJECT_CONFIG_FILE.parent.mkdir(parents=True,exist_ok=True)
    PROJECT_CONFIG_FILE.write_text(
        json.dumps(config,ensure_ascii=False,indent=2),
        encoding="utf-8"
    )


def load_saved_drafts():
    drafts_dir=ROOT/"orchestrator_v1"/"runtime"/"drafts"
    if not drafts_dir.exists():
        return
    for p in sorted(drafts_dir.glob("DRAFT-*.json")):
        try:
            data=json.loads(p.read_text(encoding="utf-8"))
            draft_id=p.stem
            if isinstance(data,dict):
                ANALYSES[draft_id]=data
        except Exception:
            continue


def restore_team_runs_from_disk():
    runs_dir=ROOT/"orchestrator_v1"/"dynamic_runs"
    if not runs_dir.exists():
        return
    for run_dir in runs_dir.iterdir():
        if not run_dir.is_dir():
            continue
        state_path=run_dir/"state.json"
        if not state_path.exists():
            continue
        try:
            state=json.loads(state_path.read_text(encoding="utf-8"))
            TEAM_RUNS[run_dir.name]={"running":False,"state":state}
        except Exception:
            continue


def latest_publishable_candidate():
    pending=latest_pending_team_run()
    if not pending:
        return None
    draft_id=pending["draft_id"]
    state=pending["state"] or {}
    if state.get("status") not in {"等待人工决策","等待人工验收","需要人工返工"}:
        return None

    draft=ANALYSES.get(draft_id)
    if not draft:
        draft_path=ROOT/"orchestrator_v1"/"runtime"/"drafts"/f"{draft_id}.json"
        if draft_path.exists():
            try:
                draft=json.loads(draft_path.read_text(encoding="utf-8"))
                ANALYSES[draft_id]=draft
            except Exception:
                draft=None
    if not draft or not draft.get("use_real_project"):
        return None

    workspace=ROOT/"orchestrator_v1"/"dynamic_runs"/draft_id/"workspace"
    runtime_path=workspace/"docs"/"runtime.json"
    if not workspace.exists() or not runtime_path.exists():
        return None

    return {
        "draft_id":draft_id,
        "workspace":workspace,
        "runtime_path":runtime_path,
        "draft":draft,
        "state":state,
    }


def latest_pending_team_run():
    pending_statuses={
        "等待人工验收",
        "等待人工决策",
        "等待预算确认",
        "需要人工返工",
        "自动返工中",
        "准备继续",
    }
    candidates=[]
    for draft_id,info in TEAM_RUNS.items():
        state=(info or {}).get("state") or {}
        if state.get("status") not in pending_statuses:
            continue
        state_path=ROOT/"orchestrator_v1"/"dynamic_runs"/draft_id/"state.json"
        try:
            mtime=state_path.stat().st_mtime
        except Exception:
            mtime=0
        candidates.append((mtime,draft_id,state))
    if not candidates:
        return None
    candidates.sort(reverse=True,key=lambda x:x[0])
    _,draft_id,state=candidates[0]
    return {"draft_id":draft_id,"state":state}


def latest_resumable_team_run():
    """供页面恢复显示使用；与验收发布候选查找分开，绝不自动重跑任务。"""
    statuses={
        "准备中","执行中","自动返工中",
        "等待人工验收","等待人工决策","等待预算确认",
        "需要人工返工","准备继续",
        "执行失败","验证失败","执行中断（需要检查）",
        "测试复核恢复中","复核中断（已保留成果）","复核通过（待安全发布）",
        "复核发现需返工","复核等待负责人决定","复核失败（保留成果）",
    }
    candidates=[]
    for draft_id,info in TEAM_RUNS.items():
        if not isinstance(info,dict):
            continue
        state_path=ROOT/"orchestrator_v1"/"dynamic_runs"/draft_id/"state.json"
        memory_state=info.get("state") or {}
        disk_state={}
        modified=0
        if state_path.exists():
            try:
                modified=state_path.stat().st_mtime
                disk_state=json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError,ValueError):
                disk_state={}
        live=bool(info.get("running"))
        # 后台仍运行时使用实时磁盘进度；已结束时优先采用内存最终结果。
        if live:
            state=disk_state or memory_state
        else:
            state=memory_state if memory_state.get("status") in {
                "执行失败","验证失败","已完成","等待人工验收",
                "等待人工决策","等待预算确认","需要人工返工"
            } else disk_state or memory_state
        if state.get("status") not in statuses and not live:
            continue
        if not live and state.get("status") in {"准备中","执行中","自动返工中","测试复核恢复中"}:
            state=dict(state)
            qa_interrupted=state.get("status")=="测试复核恢复中"
            state["status"]="复核中断（已保留成果）" if qa_interrupted else "执行中断（需要检查）"
            state["current_agent"]=""
            state["diagnostic_note"]=(
                "单独 QA 复核线程已不存在。恢复尝试已记录，不能自动重复付费调用；请检查 QA 结果文件。"
                if qa_interrupted else
                "当前没有后台执行线程。保留现有候选代码，请先检查日志，勿重新启动本任务。"
            )
        candidates.append((1 if live else 0,modified,draft_id,state,live))
    if not candidates:
        return None
    candidates.sort(reverse=True,key=lambda x:(x[0],x[1]))
    _,_,draft_id,state,live=candidates[0]
    return {"draft_id":draft_id,"state":state,"running":live}


def safe_candidate_context(draft_id):
    """Validate independent QA, connected project, test evidence, and permissions."""
    draft=ANALYSES.get(draft_id)
    if not draft or not draft.get("confirmed") or not draft.get("use_real_project"):
        raise ValueError("任务未确认绑定真实项目")
    config=load_project_connection()
    snap=draft.get("project_connection_snapshot") or {}
    if not config.get("connected") or not config.get("validated"):
        raise ValueError("当前真实项目未连接")
    if config.get("mode")!="scoped_write" or snap.get("mode")!="scoped_write":
        raise ValueError("权限范围不是 scoped_write")
    original=Path(snap.get("path") or "").resolve()
    current=Path(config.get("path") or "").resolve()
    if original!=current:
        raise ValueError("当前真实项目与原任务绑定项目不一致")
    allowed=list(snap.get("allowed_paths") or [])
    if sorted(allowed)!=sorted(config.get("allowed_paths") or []):
        raise ValueError("原任务授权目录与当前授权目录不一致")
    run_dir=ROOT/"orchestrator_v1"/"dynamic_runs"/draft_id
    state_path=run_dir/"state.json"
    qa_path=run_dir/"artifacts"/"qa_review.json"
    workspace=run_dir/"workspace"
    if not (state_path.is_file() and qa_path.is_file() and workspace.is_dir()):
        raise ValueError("隔离工作区、QA 报告或任务状态不存在")
    state=json.loads(state_path.read_text(encoding="utf-8"))
    review=json.loads(qa_path.read_text(encoding="utf-8"))
    if state.get("status")!="复核通过（待安全发布）" or not state.get("qa_passed"):
        raise ValueError("当前任务尚未正式通过独立 QA")
    if state.get("candidate_synced"):
        raise ValueError("候选版本已经发布")
    if review.get("status") not in {"pass","need_human"} or (
        review.get("status")=="need_human"
        and review.get("human_decision_type")!="user_acceptance"
    ):
        raise ValueError("QA 报告不允许发布到用户验收")
    tests=next((
        e.get("detail") for e in reversed(state.get("timeline") or [])
        if e.get("agent")=="系统验证器"
        and e.get("action")=="独立运行可发现测试"
    ),None)
    if not isinstance(tests,dict) or tests.get("returncode")!=0:
        raise ValueError("原独立测试没有成功证据")
    return {
        "state":state,"review":review,"state_path":state_path,
        "workspace":workspace,"project":original,"allowed":allowed,
    }


def resolve_project_runtime(project, allowed):
    """读取用户入口；不执行 runtime.json 的 start_command 字符串。"""
    path=project/"docs"/"runtime.json"
    if not path.is_file():
        return None,"尚未找到 docs/runtime.json",None
    try:
        raw=json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        return None,f"runtime.json 无法解析：{exc}",None
    if not isinstance(raw,dict):
        return None,"runtime.json 必须是 JSON 对象",raw
    rel=str(raw.get("entry") or raw.get("entrypoint") or raw.get("script") or raw.get("main") or "").strip().replace("\\","/")
    url=str(raw.get("url") or raw.get("local_url") or raw.get("address") or "").strip()
    kind=str(raw.get("type") or raw.get("mode") or raw.get("kind") or ("web" if url else "cli")).lower()
    if not rel:
        return None,"runtime.json 缺少 entry",raw
    p=Path(rel)
    if p.is_absolute() or ".." in p.parts or ":" in rel:
        return None,"entry 必须是授权目录内的相对路径",raw
    root=project.resolve()
    entry_path=(root/p).resolve()
    try:
        entry_path.relative_to(root)
    except ValueError:
        return None,"entry 越出了真实项目目录",raw
    allowed_roots=[str(x).strip().replace("\\","/").strip("/") for x in allowed]
    if not any(rel==v or rel.startswith(v+"/") for v in allowed_roots if v):
        return None,"entry 不在授权目录内",raw
    if not entry_path.is_file():
        return None,f"entry 文件不存在：{rel}",raw
    if entry_path.suffix.lower() not in {".py",".ps1"}:
        return None,"当前支持 Python .py 或 Windows PowerShell .ps1 入口",raw
    if url:
        try:
            u=urlparse(url)
            valid=(u.scheme=="http" and u.hostname in {"127.0.0.1","localhost"} and
                   u.port is not None and not u.username and not u.password)
        except ValueError:
            valid=False
        if not valid:
            return None,"URL 必须是 http://127.0.0.1:端口 或 http://localhost:端口",raw
    elif kind=="web":
        return None,"Web 入口必须提供本地访问 URL",raw
    launch_rel=rel
    launcher="python" if entry_path.suffix.lower()==".py" else "powershell"
    note=""
    if launcher=="powershell" and kind=="web":
        # 当前 Windows .ps1 只是启动包装器时，优先直接运行实际 Python Web 服务。
        server=(root/"src"/"web_server.py").resolve()
        try:
            server.relative_to(root)
            if server.is_file() and any(
                "src/web_server.py"==v or "src/web_server.py".startswith(v+"/")
                for v in allowed_roots if v
            ):
                launch_rel="src/web_server.py"
                launcher="python"
                note="检测到 PowerShell 启动包装器；改用现有 Python Web 服务直接启动。"
        except ValueError:
            pass
    return {
        "entry":rel,"launch_entry":launch_rel,"launcher":launcher,
        "type":kind if kind in {"web","cli"} else ("web" if url else "cli"),
        "url":url,"note":note,
    },"",raw


def read_json(relpath):
    path = ROOT / relpath
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"_error": str(exc)}


def tail_text(text, max_chars=8000):
    if len(text) <= max_chars:
        return text
    return "...(已截断前部)...\n" + text[-max_chars:]


def run_task(task_id, api_key):
    cfg = TASKS[task_id]
    env = os.environ.copy()
    resolved_key = (api_key or get_deepseek_key()).strip()
    if resolved_key:
        env["DEEPSEEK_API_KEY"] = resolved_key

    cmd = [
        "python",
        "orchestrator_v1/main.py",
        "--task",
        cfg["task_file"],
    ]

    with LOCK:
        RUNS[task_id] = {
            "running": True,
            "started_at": time.time(),
            "returncode": None,
            "stdout": "",
            "stderr": "",
        }

    try:
        p = subprocess.run(
            cmd,
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=1800,
            check=False,
        )
        with LOCK:
            RUNS[task_id].update({
                "running": False,
                "returncode": p.returncode,
                "stdout": tail_text(p.stdout),
                "stderr": tail_text(p.stderr),
            })
    except Exception as exc:
        with LOCK:
            RUNS[task_id].update({
                "running": False,
                "returncode": -1,
                "stderr": str(exc),
            })


INDEX_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PDE 多智能体控制台 v2</title>
<style>
body{font-family:Arial,"Microsoft YaHei",sans-serif;margin:0;background:#f6f7f9;color:#222}
.wrap{max-width:1100px;margin:32px auto;padding:0 20px}
h1{margin-bottom:8px}.sub{color:#666;margin-bottom:24px}
.card{background:#fff;border:1px solid #e5e7eb;border-radius:14px;padding:18px;margin-bottom:16px;box-shadow:0 2px 8px rgba(0,0,0,.04)}
.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}
.stat{background:#f8fafc;border-radius:10px;padding:12px}.stat b{display:block;font-size:22px;margin-top:6px}
label{display:block;font-weight:600;margin:10px 0 6px}
select,input,textarea,button{font-size:15px;padding:10px 12px;border-radius:9px;border:1px solid #d1d5db}
select,input,textarea{width:100%;box-sizing:border-box}
textarea{min-height:130px;resize:vertical;font-family:inherit;line-height:1.6}
button{cursor:pointer;background:#111827;color:#fff;border:none;margin-top:12px}
button:disabled{opacity:.5;cursor:not-allowed}
pre{white-space:pre-wrap;word-break:break-word;background:#111827;color:#e5e7eb;padding:14px;border-radius:10px;max-height:360px;overflow:auto}
.badge{display:inline-block;padding:4px 9px;border-radius:999px;background:#e5e7eb;font-size:13px;margin-left:8px}
.human{border-left:4px solid #d97706;background:#fff7ed}
.analysisText{margin-top:16px;line-height:1.7}
.analysisText h4{margin:14px 0 6px}
.analysisText div{padding:8px 10px;background:#f8fafc;border-radius:8px}
@media(max-width:800px){.grid{grid-template-columns:1fr 1fr}}
</style>
</head>
<body>
<div class="wrap">
  <h1>PDE 多智能体控制台 v2</h1>
  <div class="sub">直接告诉团队你想做什么。项目协调智能体先理解、分级、组队，再决定下一步。</div>

  <div class="card" style="padding:12px 18px">
    <button onclick="checkUpdate()">检查更新</button>
    <button id="updateBtn" onclick="applyUpdate()" style="background:#2563eb;display:none">立即更新</button>
    <button onclick="shutdownConsole()" style="background:#6b7280">退出控制台</button>
    <span id="updateBadge" class="badge">尚未检查</span>
    <span id="versionBadge" class="badge">版本读取中</span>
  </div>

  <details class="card">
    <summary style="cursor:pointer;font-weight:700">高级设置：执行器与 Agent</summary>
    <h3>执行器中心</h3>
    <div id="executorGrid" class="grid"></div>
    <h3 style="margin-top:20px">Agent 身份</h3>
    <div id="agentProfiles" class="grid"></div>
  </details>

  <div class="card">
    <h3>项目连接中心</h3>
    <div id="projectStatus" style="padding:10px;background:#f8fafc;border-radius:8px;margin-bottom:12px">尚未连接真实项目，所有团队任务继续在隔离工作区运行。</div>

    <label>真实项目本地路径</label>
    <input id="projectPath" placeholder="例如：D:\辅导员工作台">

    <label>权限模式</label>
    <select id="projectMode">
      <option value="read_only">只读分析，不允许修改</option>
      <option value="scoped_write">允许修改指定目录</option>
    </select>

    <label>允许修改的目录（仅 scoped_write 生效，逗号分隔）</label>
    <input id="allowedPaths" placeholder="例如：src, tests, docs">

    <button onclick="validateProject()">检测项目</button>
    <button onclick="saveProjectConnection()" style="background:#166534;margin-left:8px">确认并保存授权</button>
    <button onclick="testProjectScope()" style="background:#7c3aed;margin-left:8px">验证写权限隔离</button>
    <button onclick="disconnectProject()" style="background:#6b7280;margin-left:8px">断开真实项目</button>
    <span id="projectBadge" class="badge">隔离模式</span>
    <pre id="projectScopeResult" style="display:none;margin-top:12px"></pre>
  </div>

  <div class="card">
    <h3>项目验收中心</h3>
    <div id="acceptanceSummary" style="padding:10px;background:#f8fafc;border-radius:8px">
      尚未读取真实项目。
    </div>
    <div class="grid" style="margin-top:12px">
      <div class="stat">有效项目文件<b id="acceptanceFileCount">0</b></div>
      <div class="stat">测试状态<b id="acceptanceTestStatus">未运行</b></div>
      <div class="stat">运行入口<b id="acceptanceEntry">-</b></div>
      <div class="stat">授权范围<b id="acceptanceScope">-</b></div>
    </div>
    <button id="refreshAcceptanceBtn" onclick="refreshAcceptance()">刷新验收信息</button>
    <span id="acceptanceRefreshBadge" class="badge">尚未手动刷新</span>
    <button onclick="runAcceptanceTests()" style="background:#166534;margin-left:8px">运行基础测试</button>
    <button id="publishPendingBtn" onclick="publishPendingFromAcceptance()" style="background:#0f766e;margin-left:8px;display:none">发布待验收版本</button>
    <button id="launchProjectBtn" onclick="launchAcceptedProject()" style="background:#2563eb;margin-left:8px">启动工作台</button>
    <button id="openProjectBtn" onclick="openAcceptedProject()" style="background:#7c3aed;margin-left:8px;display:none">打开工作台</button>
    <button id="stopProjectBtn" onclick="stopAcceptedProject()" style="background:#6b7280;margin-left:8px;display:none">停止工作台</button>
    <span id="projectRunBadge" class="badge">未启动</span>
    <div id="acceptanceRuntimeHint" style="margin-top:12px;padding:10px;background:#eff6ff;border-radius:8px">
      尚未检测到面向用户的运行入口。
    </div>
    <h4>文件树</h4>
    <pre id="acceptanceTree">暂无。</pre>
    <h4>测试结果</h4>
    <pre id="acceptanceTestResult">尚未运行。</pre>
    <h4>启动日志</h4>
    <pre id="acceptanceLaunchLog">尚未启动。</pre>
  </div>

  <details class="card">
    <summary style="cursor:pointer;font-weight:700">开发 / 诊断工具</summary>
    <h3>系统回归验证</h3>
    <div style="padding:10px;background:#eff6ff;border-radius:8px;line-height:1.6">
      这里仅验证多智能体编排流程，不连接真实辅导员工作台。回归验证使用本地验证执行器，不调用 Codex/DeepSeek；真实任务仍使用真实模型。
    </div>
    <button id="reworkTestBtn" onclick="startReworkValidation()" style="background:#7c3aed">验证自动返工闭环</button>
    <button id="budgetTestBtn" onclick="startBudgetValidation()" style="background:#0f766e;margin-left:8px">验证预算申请交互</button>
    <span id="validationBadge" class="badge">尚未验证</span>
    <pre id="validationResult" style="display:none;margin-top:12px"></pre>

    <hr style="margin:24px 0;border:none;border-top:1px solid #e5e7eb">
    <h3>旧版测试任务</h3>
    <div style="color:#666;margin-bottom:8px">仅用于开发排查，日常使用无需操作。</div>
    <label>选择任务</label>
    <select id="task"></select>
    <button id="start" onclick="startTask()">开始执行</button>
    <span id="runBadge" class="badge">未运行</span>

    <div class="grid" style="margin-top:14px">
      <div class="stat">当前状态<b id="status">-</b></div>
      <div class="stat">Codex 调用<b id="codex">0</b></div>
      <div class="stat">DeepSeek 调用<b id="deepseek">0</b></div>
      <div class="stat">DeepSeek tokens<b id="tokens">0</b></div>
    </div>

    <h4>最近运行日志</h4>
    <pre id="log">尚未运行。</pre>
    <h4>任务历史</h4>
    <pre id="history">暂无。</pre>
  </details>

  <div class="card">
    <h3>告诉团队你现在想做什么</h3>
    <textarea id="goal" placeholder="例如：给辅导员工作台增加晚返学生提醒功能，但不要给学生造成太强的被监控感。"></textarea>

    <div id="deepseekSetup" style="display:none">
      <label>首次配置 DeepSeek API Key</label>
      <input id="key" type="password" placeholder="只需配置一次，保存到 Windows 当前用户环境变量">
      <button onclick="saveDeepSeekKey()" style="background:#0f766e">保存 DeepSeek Key</button>
      <span id="keyBadge" class="badge">未配置</span>
    </div>
    <div id="deepseekReady" style="display:none;padding:10px;background:#ecfdf5;border-radius:8px">
      DeepSeek：✅ 已连接。后续无需再填写 API Key。
    </div>

    <button id="analyzeBtn" onclick="analyzeGoal()">让项目协调智能体分析</button>
    <span id="analyzeBadge" class="badge">等待输入</span>
  </div>

  <div id="analysisCard" class="card" style="display:none">
    <h3>项目协调智能体分析</h3>
    <div class="grid">
      <div class="stat">任务等级<b id="taskLevel">-</b></div>
      <div class="stat">参与角色数<b id="agentCount">0</b></div>
      <div class="stat">是否需你拍板<b id="ownerDecision">否</b></div>
      <div class="stat">本次 tokens<b id="analysisTokens">0</b></div>
    </div>
    <div id="analysisText" class="analysisText"></div>

    <div id="replyArea" style="margin-top:18px">
      <label>继续和项目协调智能体说</label>
      <textarea id="ownerReply" placeholder="直接用自然语言补充即可，例如：晚返定义为超过预计返校时间30分钟；提醒先只给辅导员看；数据来源先用现有返校登记。"></textarea>
      <label style="display:flex;align-items:center;gap:8px;font-weight:normal;margin-top:14px">
        <input id="useRealProject" type="checkbox" style="width:auto">
        这次任务使用已授权的真实项目（默认不勾选）
      </label>
      <button id="replyBtn" onclick="refineGoal()">发送补充说明</button>
      <button id="confirmBtn" onclick="confirmDraft()" style="background:#166534;margin-left:8px">确认任务草案</button>
      <span id="draftBadge" class="badge">尚未确认</span>
    </div>
  </div>

  <div id="teamCard" class="card" style="display:none">
    <h3>团队执行</h3>
    <div class="grid">
      <div class="stat">执行状态<b id="teamStatus">未启动</b></div>
      <div class="stat">当前智能体<b id="currentAgent">-</b></div>
      <div class="stat">Codex 调用<b id="teamCodex">0</b></div>
      <div class="stat">DeepSeek tokens<b id="teamTokens">0</b></div>
      <div class="stat">较首次节省<b id="teamSavings">-</b></div>
      <div class="stat">自动返工<b id="teamRework">0 / 2</b></div>
    </div>
    <div id="teamSafety" style="margin-top:14px;padding:10px;background:#fff7ed;border-radius:8px">
      当前安全范围：等待任务启动。
    </div>
    <button id="executeBtn" onclick="startTeamExecution()" style="background:#7c3aed">启动团队执行</button><button id="inspectInterruptedBtn" style="display:none" onclick="inspectInterruptedTask()">诊断本次中断（只读）</button>
    <button id="resumeQaOnlyBtn" style="display:none;background:#166534" onclick="resumeQaOnly()">仅恢复测试复核（需授权 DeepSeek）</button>
    <span id="executeBadge" class="badge">等待草案确认</span>
    <div id="budgetAsk" class="card human" style="display:none;margin-top:14px">
      <h4>需要追加少量预算</h4>
      <div id="budgetAskText"></div>
      <button onclick="approveBudget()" style="background:#166534">同意追加并继续</button>
      <button onclick="declineBudget()" style="background:#6b7280">先暂停任务</button>
    </div>
    <div id="humanAcceptance" class="card human" style="display:none;margin-top:14px">
      <h4>需要你实际验收</h4>
      <div id="humanAcceptanceText"></div>
      <button id="publishCandidateBtn" onclick="publishCandidate()" style="background:#2563eb">同步待验收版本到真实项目</button>
      <textarea id="humanAcceptanceNote" placeholder="可选：写下你的验收反馈，例如：新增学生正常，但筛选按钮有问题。"></textarea>
      <button onclick="approveHumanAcceptance()" style="background:#166534">验收通过</button>
      <button onclick="rejectHumanAcceptance()" style="background:#b91c1c">退回返工</button>
    </div>
    <h4>分角色成本</h4>
    <pre id="roleUsage">暂无。</pre>
    <h4>团队时间线</h4>
    <pre id="teamTimeline">暂无。</pre>
  </div>

  <div id="humanCard" class="card human" style="display:none">
    <h3>需要项目负责人决策</h3>
    <div id="humanText"></div>
  </div>
</div>

<script>
let CURRENT_DRAFT_ID = null;
let ACCEPTANCE_RUNTIME_READY = false;

async function api(path, options){
  const opts=Object.assign({cache:'no-store'},options||{});
  const r=await fetch(path, opts);
  return await r.json();
}

function escapeHtml(s){
  return String(s).replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[m]));
}

async function init(){
  await loadVersion();
  await loadExecutors();
  await loadAgentProfiles();
  await loadProjectConnection();
  await refreshAcceptance();
  await resumePendingTask();
  const data=await api('/api/tasks');
  const sel=document.getElementById('task');
  for(const t of data.tasks){
    const o=document.createElement('option');
    o.value=t.id;
    o.textContent=t.label;
    sel.appendChild(o);
  }
  sel.onchange=refresh;
  refresh();
}

async function resumePendingTask(){
  try{
    const r=await api('/api/team/resume');
    if(!r.ok||!r.pending)return;
    CURRENT_DRAFT_ID=r.pending.draft_id;
    const s=r.pending.state||{};
    document.getElementById('teamCard').style.display='block';
    document.getElementById('teamStatus').textContent=s.status||'等待恢复';
    document.getElementById('currentAgent').textContent=s.current_agent||'-';
    document.getElementById('teamCodex').textContent=s.codex_calls||0;
    document.getElementById('teamTokens').textContent=s.deepseek_tokens||0;
    document.getElementById('teamRework').textContent=(s.rework_count||0)+' / '+(s.max_reworks||2);
    document.getElementById('roleUsage').textContent=JSON.stringify(s.role_usage||{},null,2);
    document.getElementById('teamTimeline').textContent=JSON.stringify(s.timeline||[],null,2);
    document.getElementById('executeBadge').textContent=r.pending.running?'正在执行 · 已恢复显示':(s.status||'已恢复');
    document.getElementById('executeBtn').disabled=true;
    const safety=document.getElementById('teamSafety');
    if(safety){
      if(s.real_project){
        safety.textContent='当前安全范围：已绑定真实项目 '+s.real_project
          +'；权限 '+(s.project_mode||'未知')
          +'；仅允许写回 '+((s.allowed_paths||[]).join(', ')||'无')
          +'；Git commit 禁止。';
      }else{
        safety.textContent='当前安全范围：'+(s.safety_note||'隔离工作区');
      }
    }
    // 只恢复页面观察与轮询，不触发团队再次执行。
    pollTeam();
  }catch(e){
    const badge=document.getElementById('executeBadge');
    if(badge)badge.textContent='恢复任务失败：'+String(e);
  }
}

async function loadVersion(){
  try{
    const d=await api('/api/version');
    document.getElementById('versionBadge').textContent=d.version?('当前版本 '+d.version):'版本未知';
  }catch(e){
    document.getElementById('versionBadge').textContent='版本读取失败';
  }
}

async function shutdownConsole(){
  if(!confirm('确定退出多智能体控制台吗？正在运行的本地任务会停止。'))return;
  try{
    await api('/api/shutdown',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
  }catch(e){}
  document.body.innerHTML='<div style="font-family:Microsoft YaHei;padding:40px"><h2>控制台已退出</h2><p>需要继续使用时，再双击“静默启动多智能体控制台.vbs”。</p></div>';
}

async function loadAgentProfiles(){
  const d=await api('/api/agents');
  const grid=document.getElementById('agentProfiles');
  const profiles=d.profiles||{};
  grid.innerHTML=Object.entries(profiles).map(([name,p])=>{
    return '<div class="stat"><strong>'+escapeHtml(name)+'</strong>'
      +'<b style="font-size:13px">'+escapeHtml(p.executor||'')+'</b>'
      +'<div style="margin-top:6px;font-size:13px">'+escapeHtml(p.role||'')+'</div></div>';
  }).join('');
}

async function loadProjectConnection(){
  const d=await api('/api/project');
  const c=d.config||{};
  const status=document.getElementById('projectStatus');
  const badge=document.getElementById('projectBadge');

  if(c.connected&&c.validated){
    status.innerHTML='已连接：<b>'+escapeHtml(c.path)+'</b><br>权限：'+escapeHtml(c.mode)
      +(c.allowed_paths&&c.allowed_paths.length?'<br>允许目录：'+escapeHtml(c.allowed_paths.join(', ')):'')
      +'<br>Git 提交：当前版本固定禁止';
    badge.textContent='真实项目已授权';
    document.getElementById('projectPath').value=c.path||'';
    document.getElementById('projectMode').value=c.mode||'read_only';
    document.getElementById('allowedPaths').value=(c.allowed_paths||[]).join(', ');
  }else{
    status.textContent='尚未连接真实项目，所有团队任务继续在隔离工作区运行。';
    badge.textContent='隔离模式';
  }
}

async function validateProject(){
  const path=document.getElementById('projectPath').value.trim();
  if(!path){alert('请输入真实项目本地路径');return;}
  const r=await api('/api/project/validate',{
    method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:path})
  });
  if(!r.ok){alert(r.error||'检测失败');return;}
  alert('项目检测通过：'+r.project.name+(r.project.is_git?'（Git 仓库）':'（非 Git 仓库）'));
}

async function saveProjectConnection(){
  const path=document.getElementById('projectPath').value.trim();
  const mode=document.getElementById('projectMode').value;
  const allowed=document.getElementById('allowedPaths').value
    .split(',').map(x=>x.trim()).filter(Boolean);
  const allowGitCommit=false;

  if(!path){alert('请输入真实项目本地路径');return;}
  if(mode==='scoped_write'&&!allowed.length){
    alert('选择“允许修改指定目录”时，请至少填写一个允许目录。');
    return;
  }

  const ok=confirm(
    '确认授权这个真实项目吗？\n\n'
    +'路径：'+path+'\n'
    +'权限：'+mode+'\n'
    +'允许目录：'+(allowed.join(', ')||'无')+'\n'
    +'Git commit：当前版本固定禁止'
  );
  if(!ok)return;

  const r=await api('/api/project/connect',{
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({
      path:path,
      mode:mode,
      allowed_paths:allowed,
      allow_git_commit:allowGitCommit
    })
  });
  if(!r.ok){alert(r.error||'保存失败');return;}
  await loadProjectConnection();
  alert('真实项目授权已保存。后续只有在任务明确选择真实项目时才会使用该权限。');
}

async function disconnectProject(){
  const r=await api('/api/project/disconnect',{
    method:'POST',headers:{'Content-Type':'application/json'},body:'{}'
  });
  if(!r.ok){alert(r.error||'断开失败');return;}
  await loadProjectConnection();
}

async function refreshAcceptance(){
  const btn=document.getElementById('refreshAcceptanceBtn');
  const badge=document.getElementById('acceptanceRefreshBadge');
  if(btn)btn.disabled=true;
  if(badge)badge.textContent='刷新中...';

  try{
    const r=await api('/api/project/acceptance?t='+Date.now());
    if(!r.ok){
      document.getElementById('acceptanceSummary').textContent=r.error||'尚未连接真实项目。';
      document.getElementById('acceptanceTree').textContent='暂无。';
      if(badge)badge.textContent='刷新失败';
      return;
    }

    document.getElementById('acceptanceSummary').innerHTML=
      '当前项目：<b>'+escapeHtml(r.project||'')+'</b><br>'
      +'路径：'+escapeHtml(r.path||'');
    document.getElementById('acceptanceFileCount').textContent=r.file_count||0;
    document.getElementById('acceptanceEntry').textContent=r.entry||'未检测到';
    document.getElementById('acceptanceScope').textContent=(r.allowed_paths||[]).join(', ')||'只读';
    document.getElementById('acceptanceTree').textContent=(r.tree||[]).join('\n')||'项目为空。';

    const hint=document.getElementById('acceptanceRuntimeHint');
    ACCEPTANCE_RUNTIME_READY=!!r.runtime_manifest;

    if(r.runtime_manifest){
      const m=r.runtime_manifest;
      hint.innerHTML='✅ 已识别用户入口：<b>'+escapeHtml(m.entry||'')+'</b>'
        +(m.type?'<br>类型：'+escapeHtml(m.type):'')
        +(m.url?'<br>配置地址：'+escapeHtml(m.url):'')
        +(r.runtime_status&&r.runtime_status.running&&r.runtime_status.url
          ?'<br><b>当前实际地址：'+escapeHtml(r.runtime_status.url)+'</b>':'')
        +(m.launch_entry&&m.launch_entry!==m.entry?'<br>实际启动：'+escapeHtml(m.launch_entry):'')
        +(m.note?'<br>'+escapeHtml(m.note):'')
        +'<br>runtime.json：'+escapeHtml(r.runtime_manifest_path||'');
    }else if(r.runtime_manifest_exists){
      hint.innerHTML='⚠️ 已找到 docs/runtime.json，但当前格式无法作为用户入口使用。<br>'
        +'路径：'+escapeHtml(r.runtime_manifest_path||'')
        +'<br>原因：<b>'+escapeHtml(r.runtime_manifest_error||'格式不符合要求')+'</b>'
        +'<br>当前内容：<pre style="max-height:180px">'+escapeHtml(JSON.stringify(r.runtime_manifest_raw||{},null,2))+'</pre>';
    }else{
      hint.innerHTML='当前只检测到技术入口 <b>'+escapeHtml(r.entry||'无')+'</b>，'
        +'尚未找到 docs/runtime.json。<br>'
        +'检查路径：'+escapeHtml(r.runtime_manifest_path||'')+'<br>'
        +(r.pending_candidate_available
          ? '<b>检测到隔离工作区里已有待验收版本，请点击“发布待验收版本”。</b>'
          : '这意味着“代码可运行”不等于“你已经能直接使用这个功能”。');
    }

    const publishBtn=document.getElementById('publishPendingBtn');
    if(publishBtn)publishBtn.style.display=(!r.runtime_manifest_exists && r.pending_candidate_available)?'inline-block':'none';
    updateProjectRunButtons(r.runtime_status||{});

    const now=new Date();
    if(badge)badge.textContent='已刷新 '+now.toLocaleTimeString();
  }catch(e){
    document.getElementById('acceptanceLaunchLog').textContent='刷新验收信息失败：'+String(e);
    if(badge)badge.textContent='刷新失败';
  }finally{
    if(btn)btn.disabled=false;
  }
}

function updateProjectRunButtons(s){
  const running=!!s.running;
  document.getElementById('projectRunBadge').textContent=running?'运行中':(ACCEPTANCE_RUNTIME_READY?'未启动':'等待用户入口');
  document.getElementById('launchProjectBtn').disabled=running||!ACCEPTANCE_RUNTIME_READY;
  document.getElementById('stopProjectBtn').style.display=running?'inline-block':'none';
  document.getElementById('openProjectBtn').style.display=(running&&s.url)?'inline-block':'none';
}

async function publishPendingFromAcceptance(){
  const btn=document.getElementById('publishPendingBtn');
  btn.disabled=true;
  btn.textContent='发布中...';
  const r=await api('/api/project/acceptance/publish-pending',{
    method:'POST',headers:{'Content-Type':'application/json'},body:'{}'
  });
  btn.disabled=false;
  btn.textContent='发布待验收版本';
  if(!r.ok){
    const detail=r.diagnostics?('\n\n诊断信息：\n'+JSON.stringify(r.diagnostics,null,2)):'';
    document.getElementById('acceptanceLaunchLog').textContent=(r.error||'发布失败')+detail;
    alert(r.error||'发布失败');
    return;
  }
  document.getElementById('acceptanceLaunchLog').textContent=
    '待验收版本已发布，并确认 runtime.json 已写入真实项目：\n'+(r.target_runtime||'');
  alert('待验收版本已发布到真实项目，并已验证用户入口文件存在。');
  document.getElementById('acceptanceRuntimeHint').textContent='正在重新读取真实项目最新入口...';
  await refreshAcceptance();
}

async function launchAcceptedProject(){
  const badge=document.getElementById('projectRunBadge');
  badge.textContent='启动中...';
  const r=await api('/api/project/acceptance/launch',{
    method:'POST',headers:{'Content-Type':'application/json'},body:'{}'
  });
  const log=document.getElementById('acceptanceLaunchLog');
  if(!r.ok){
    badge.textContent=ACCEPTANCE_RUNTIME_READY?'未启动':'等待用户入口';
    updateProjectRunButtons({running:false});
    log.textContent=(r.error||'启动失败')+(r.log?'\n\n'+r.log:'');
    alert(r.error||'启动失败');
    return;
  }
  updateProjectRunButtons(r.status||{});
  log.textContent=r.log||'工作台启动成功。';
  if(r.status&&r.status.url){
    const hint=document.getElementById('acceptanceRuntimeHint');
    if(r.temporary_port_override){
      hint.innerHTML+='<p><b>已自动避开端口占用，本次实际打开：'
        +escapeHtml(r.status.url)+'</b>（未修改项目文件）</p>';
    }
    window.open(r.status.url,'_blank');
  }else{
    alert('项目已启动，但当前入口不是可直接打开的网页。');
  }
}

async function openAcceptedProject(){
  const r=await api('/api/project/acceptance/runtime');
  if(r.ok&&r.status&&r.status.url){
    window.open(r.status.url,'_blank');
  }else{
    alert('当前没有可打开的网页地址。');
  }
}

async function stopAcceptedProject(){
  const r=await api('/api/project/acceptance/stop',{
    method:'POST',headers:{'Content-Type':'application/json'},body:'{}'
  });
  if(!r.ok){alert(r.error||'停止失败');return;}
  updateProjectRunButtons(r.status||{});
}

async function runAcceptanceTests(){
  const status=document.getElementById('acceptanceTestStatus');
  const result=document.getElementById('acceptanceTestResult');
  status.textContent='运行中';
  result.textContent='正在运行 Python unittest...';
  const r=await api('/api/project/acceptance/test',{
    method:'POST',headers:{'Content-Type':'application/json'},body:'{}'
  });
  if(!r.ok){
    status.textContent='失败';
    result.textContent=r.error||'测试运行失败';
    return;
  }
  status.textContent=r.passed?'通过':'未通过';
  result.textContent=(r.command||'')+'\n\n'+(r.stdout||'')+(r.stderr?'\n'+r.stderr:'');
}

async function testProjectScope(){
  const box=document.getElementById('projectScopeResult');
  box.style.display='block';
  box.textContent='正在验证指定目录写权限隔离...';
  const r=await api('/api/project/scope-test',{
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body:'{}'
  });
  if(!r.ok){
    box.textContent='验证失败：'+(r.error||'未知错误');
    return;
  }
  box.textContent=r.passed
    ? '通过：允许目录中的测试文件可以同步，根目录越权文件没有写回真实项目；测试文件已清理。'
    : '未通过：'+(r.detail||'权限隔离结果异常，请停止真实任务。');
}

async function checkUpdate(){
  const badge=document.getElementById('updateBadge');
  badge.textContent='检查中...';
  const d=await api('/api/update/status');
  if(!d.ok){
    badge.textContent='检查失败';
    alert(d.error||'更新检查失败');
    return;
  }
  if(d.update_available){
    badge.textContent='发现新版本 '+d.remote;
    document.getElementById('updateBtn').style.display='inline-block';
  }else{
    badge.textContent='已是最新版 '+d.local;
    document.getElementById('updateBtn').style.display='none';
  }
}

async function applyUpdate(){
  const badge=document.getElementById('updateBadge');
  const btn=document.getElementById('updateBtn');
  btn.disabled=true;
  badge.textContent='更新中...';
  const d=await api('/api/update/apply',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
  btn.disabled=false;
  if(!d.ok){
    badge.textContent='更新失败';
    alert((d.stderr||d.error||'更新失败'));
    return;
  }
  badge.textContent='更新完成，请重启控制台';
  btn.style.display='none';
  alert('更新完成。请点击“退出控制台”，然后重新双击“静默启动多智能体控制台.vbs”。');
}

async function loadExecutors(){
  const d=await api('/api/executors');
  const grid=document.getElementById('executorGrid');
  const items=d.executors||{};
  grid.innerHTML=Object.values(items).map(x=>{
    const mark=x.connected?'✅':'⚪';
    return '<div class="stat">'+mark+' '+escapeHtml(x.label)+'<b style="font-size:14px">'+escapeHtml(x.detail)+'</b></div>';
  }).join('');

  if(items.deepseek&&items.deepseek.connected){
    document.getElementById('deepseekSetup').style.display='none';
    document.getElementById('deepseekReady').style.display='block';
  }else{
    document.getElementById('deepseekSetup').style.display='block';
    document.getElementById('deepseekReady').style.display='none';
  }
}

async function saveDeepSeekKey(){
  const input=document.getElementById('key');
  const badge=document.getElementById('keyBadge');
  const key=input.value.trim();
  if(!key){alert('请输入 DeepSeek API Key');return;}

  badge.textContent='保存中...';

  const controller=new AbortController();
  const timer=setTimeout(()=>controller.abort(),10000);

  try{
    const r=await api('/api/settings/deepseek',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({api_key:key}),
      signal:controller.signal
    });

    if(!r.ok){
      badge.textContent='保存失败';
      alert(r.error||'保存失败');
      return;
    }

    input.value='';
    badge.textContent='已保存';
    await loadExecutors();
    alert('DeepSeek Key 已保存，当前控制台已立即生效。');
  }catch(e){
    badge.textContent='保存失败';
    if(e && e.name==='AbortError'){
      alert('保存超时。请把黑色后台窗口截图发给我。');
    }else{
      alert('保存失败：'+String(e));
    }
  }finally{
    clearTimeout(timer);
  }
}

async function startReworkValidation(){
  const btn=document.getElementById('reworkTestBtn');
  const badge=document.getElementById('validationBadge');
  btn.disabled=true;
  badge.textContent='自动返工验证中（通常几秒）...';
  const r=await api('/api/validation/rework/start',{
    method:'POST',headers:{'Content-Type':'application/json'},body:'{}'
  });
  if(!r.ok){
    btn.disabled=false; badge.textContent='验证启动失败'; alert(r.error||'启动失败'); return;
  }
  CURRENT_DRAFT_ID=r.draft_id;
  document.getElementById('teamCard').style.display='block';
  document.getElementById('executeBadge').textContent='验证运行中';
  pollValidation('rework');
}

async function startBudgetValidation(){
  const badge=document.getElementById('validationBadge');
  badge.textContent='预算申请验证已启动';
  const r=await api('/api/validation/budget/start',{
    method:'POST',headers:{'Content-Type':'application/json'},body:'{}'
  });
  if(!r.ok){badge.textContent='验证启动失败';alert(r.error||'启动失败');return;}
  CURRENT_DRAFT_ID=r.draft_id;
  document.getElementById('teamCard').style.display='block';
  await pollTeam();
  document.getElementById('validationResult').style.display='block';
  document.getElementById('validationResult').textContent='请在“团队执行”区域点击“同意追加并继续”或“先暂停任务”，验证审批交互。';
}

async function pollValidation(kind){
  if(!CURRENT_DRAFT_ID)return;
  const d=await api('/api/team/status?draft_id='+encodeURIComponent(CURRENT_DRAFT_ID));
  if(!d.ok)return;
  const s=d.state||{};
  const ago=Number(d.last_progress_seconds);
  const progressNote=(d.running && Number.isFinite(ago) && ago>=300)
    ? '（已 '+Math.floor(ago/60)+' 分钟无新记录，可能在等待模型或卡住；勿重复启动）'
    : '';
  document.getElementById('teamStatus').textContent=(s.status||'准备中')+progressNote;
  document.getElementById('currentAgent').textContent=s.current_agent||'-';
  document.getElementById('teamCodex').textContent=s.codex_calls||0;
  document.getElementById('teamRework').textContent=(s.rework_count||0)+' / '+(s.max_reworks||2);
  document.getElementById('teamTokens').textContent=s.deepseek_tokens||0;
  document.getElementById('roleUsage').textContent=JSON.stringify(s.role_usage||{},null,2);
  document.getElementById('teamTimeline').textContent=JSON.stringify(s.timeline||[],null,2);
  document.getElementById('teamSafety').textContent='当前安全范围：'+(s.safety_note||'未提供');

  if(d.running){
    setTimeout(()=>pollValidation(kind),2000);
    return;
  }
  document.getElementById('reworkTestBtn').disabled=false;
  const ok=(s.status==='已完成' && (s.rework_count||0)>=1);
  document.getElementById('validationBadge').textContent=ok?'自动返工验证通过':'自动返工验证需检查';
  document.getElementById('validationResult').style.display='block';
  document.getElementById('validationResult').textContent=ok
    ? '通过：系统预置缺陷样例后触发返工，本地验证执行器完成修复，重新测试并完成复核。真实任务仍使用 Codex。'
    : '未满足完整通过条件，请把团队执行区域截图发给我。';
}

async function analyzeGoal(){
  const goal=document.getElementById('goal').value.trim();
  const key=(document.getElementById('key')&&document.getElementById('key').value)||'';

  if(!goal){
    alert('先告诉团队你想做什么。');
    return;
  }

  const btn=document.getElementById('analyzeBtn');
  const badge=document.getElementById('analyzeBadge');
  btn.disabled=true;
  badge.textContent='分析中';

  try{
    const r=await api('/api/analyze',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({goal:goal,api_key:key})
    });

    if(!r.ok){
      badge.textContent='分析失败';
      alert(r.error||'分析失败');
      return;
    }

    badge.textContent='分析完成';
    CURRENT_DRAFT_ID=r.draft_id||null;
    document.getElementById('draftBadge').textContent='待补充/确认';
    const a=r.analysis||{};

    renderAnalysis(a, r.usage||{});
  }catch(e){
    badge.textContent='分析失败';
    alert(String(e));
  }finally{
    btn.disabled=false;
  }
}

function renderAnalysis(a, usage){
  document.getElementById('analysisCard').style.display='block';
  document.getElementById('taskLevel').textContent=a.task_level||'-';
  document.getElementById('agentCount').textContent=(a.required_agents||[]).length;
  document.getElementById('ownerDecision').textContent=a.needs_owner_decision?'是':'否';
  document.getElementById('analysisTokens').textContent=(usage&&usage.total_tokens)||0;

  const rows=[
    ['我的理解',a.summary],
    ['为什么这样分级',a.reason],
    ['建议参与的智能体',(a.required_agents||[]).join('、')||'无'],
    ['本次范围',(a.scope||[]).map(x=>'• '+x).join('<br>')||'暂无'],
    ['暂不包含',(a.out_of_scope||[]).map(x=>'• '+x).join('<br>')||'暂无'],
    ['验收标准',(a.acceptance_criteria||[]).map(x=>'• '+x).join('<br>')||'暂无'],
    ['需要你补充的问题',(a.questions||[]).map(x=>'• '+x).join('<br>')||'暂无'],
    ['建议执行通道',a.recommended_executor||'待动态路由'],
    ['下一步',a.next_action||'待定']
  ];

  if(a.needs_owner_decision){
    rows.splice(7,0,['为什么需要你拍板',a.owner_decision_reason||'存在需要项目负责人决定的问题']);
  }

  document.getElementById('analysisText').innerHTML=rows
    .filter(x=>x[1])
    .map(x=>'<h4>'+escapeHtml(x[0])+'</h4><div>'+x[1]+'</div>')
    .join('');
}

async function refineGoal(){
  const reply=document.getElementById('ownerReply').value.trim();
  const key=(document.getElementById('key')&&document.getElementById('key').value)||'';
  if(!CURRENT_DRAFT_ID){alert('请先让项目协调智能体分析一次。');return;}
  if(!reply){alert('请输入你的补充说明。');return;}

  const btn=document.getElementById('replyBtn');
  const badge=document.getElementById('draftBadge');
  btn.disabled=true; badge.textContent='更新中';

  try{
    const r=await api('/api/refine',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({draft_id:CURRENT_DRAFT_ID,user_reply:reply,api_key:key})
    });
    if(!r.ok){
      alert(r.error||'更新失败');
      badge.textContent='更新失败，已保留上一版草案';
      return;
    }
    const a=r.analysis||{};
    if(!a.task_level || !a.summary){
      alert('协调智能体返回结构异常，已保留上一版草案。');
      badge.textContent='更新失败，已保留上一版草案';
      return;
    }
    renderAnalysis(a, r.usage||{});
    document.getElementById('ownerReply').value='';
    badge.textContent=(a.questions||[]).length?'仍有待确认':'可确认任务草案';
  }finally{
    btn.disabled=false;
  }
}

async function confirmDraft(){
  if(!CURRENT_DRAFT_ID){alert('请先生成任务草案。');return;}
  const r=await api('/api/confirm',{
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({
      draft_id:CURRENT_DRAFT_ID,
      use_real_project:!!document.getElementById('useRealProject')?.checked
    })
  });
  if(!r.ok){alert(r.error||'确认失败');return;}
  document.getElementById('draftBadge').textContent='已确认';
  document.getElementById('teamCard').style.display='block';
  document.getElementById('executeBadge').textContent='可启动';
  alert('任务草案已确认。现在可以启动动态团队执行。');
}

async function startTeamExecution(){
  if(!CURRENT_DRAFT_ID){alert('请先确认任务草案。');return;}
  const key=(document.getElementById('key')&&document.getElementById('key').value)||'';
  const btn=document.getElementById('executeBtn');
  btn.disabled=true;
  document.getElementById('executeBadge').textContent='启动中';

  const r=await api('/api/team/start',{
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({draft_id:CURRENT_DRAFT_ID,api_key:key})
  });

  if(!r.ok){
    btn.disabled=false;
    document.getElementById('executeBadge').textContent='启动失败';
    alert(r.error||'团队执行启动失败');
    return;
  }
  document.getElementById('executeBadge').textContent='执行中';
  pollTeam();
}

async function publishCandidate(){
  if(!CURRENT_DRAFT_ID)return;
  const r=await api('/api/team/publish-candidate',{
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({draft_id:CURRENT_DRAFT_ID})
  });
  if(!r.ok){alert(r.error||'同步待验收版本失败');return;}
  alert('待验收版本已同步到真实项目。请到项目验收中心点击“刷新验收信息”。');
  await refreshAcceptance();
  pollTeam();
}

async function approveHumanAcceptance(){
  if(!CURRENT_DRAFT_ID)return;
  const note=document.getElementById('humanAcceptanceNote').value.trim();
  const r=await api('/api/team/human-decision',{
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({draft_id:CURRENT_DRAFT_ID,decision:'approve',note:note})
  });
  if(!r.ok){alert(r.error||'提交验收结果失败');return;}
  document.getElementById('humanAcceptance').style.display='none';
  pollTeam();
}

async function rejectHumanAcceptance(){
  if(!CURRENT_DRAFT_ID)return;
  const note=document.getElementById('humanAcceptanceNote').value.trim();
  if(!note){
    alert('退回返工时，请写明你实际遇到的问题。');
    return;
  }
  const r=await api('/api/team/human-decision',{
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({draft_id:CURRENT_DRAFT_ID,decision:'rework',note:note})
  });
  if(!r.ok){alert(r.error||'提交返工意见失败');return;}
  document.getElementById('humanAcceptance').style.display='none';
  pollTeam();
}

async function approveBudget(){
  if(!CURRENT_DRAFT_ID)return;
  const r=await api('/api/team/budget',{
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({draft_id:CURRENT_DRAFT_ID,decision:'approve'})
  });
  if(!r.ok){alert(r.error||'追加预算失败');return;}
  document.getElementById('budgetAsk').style.display='none';
  pollTeam();
}

async function declineBudget(){
  if(!CURRENT_DRAFT_ID)return;
  const r=await api('/api/team/budget',{
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({draft_id:CURRENT_DRAFT_ID,decision:'decline'})
  });
  if(!r.ok){alert(r.error||'暂停失败');return;}
  document.getElementById('budgetAsk').style.display='none';
  pollTeam();
}

async function inspectInterruptedTask(){
  if(!CURRENT_DRAFT_ID){alert('尚未定位到团队任务');return;}
  const b=document.getElementById('inspectInterruptedBtn');
  if(b){b.disabled=true;b.textContent='正在只读检查...';}
  try{
    const q='/api/team/recovery/inspect?draft_id='+encodeURIComponent(CURRENT_DRAFT_ID)+'&t='+Date.now();
    const r=await api(q);
    if(!r.ok)throw Error(r.error||'检查失败');
    const report=[
      '任务：'+r.draft_id,
      '后台仍在运行：'+r.running,
      '磁盘状态：'+r.saved_status,
      '隔离工作区存在：'+r.workspace_exists,
      'Codex 交付文件存在：'+r.codex_delivery_exists,
      'QA 复核结论文件存在：'+r.qa_review_exists,
      '自动测试退出码：'+r.test_returncode+'（0 为成功）',
      'DeepSeek tokens / 预算：'+r.deepseek_tokens+' / '+r.token_budget,
      '测试智能体 tokens：'+r.qa_role_tokens,
      '最后事件：'+(r.last_event?.action||'无'),
      '诊断：'+r.safe_next_step,
      '测试摘要：\n'+(r.test_summary||'无')
    ].join('\n');
    document.getElementById('teamTimeline').textContent=report;
    const qaBtn=document.getElementById('resumeQaOnlyBtn');
    if(qaBtn)qaBtn.style.display=r.qa_resume_eligible?'inline-block':'none';
    alert(r.safe_next_step);
  }catch(e){
    document.getElementById('teamTimeline').textContent='中断诊断失败：'+String(e);
  }finally{
    if(b){b.disabled=false;b.textContent='诊断本次中断（只读）';}
  }
}

async function resumeQaOnly(){
  if(!CURRENT_DRAFT_ID){alert('没有当前任务 ID');return;}
  let chk;
  try{
    chk=await api('/api/team/recovery/inspect?draft_id='
      +encodeURIComponent(CURRENT_DRAFT_ID)+'&t='+Date.now());
  }catch(e){alert('无法核对恢复条件：'+String(e));return;}
  if(!chk.ok||!chk.qa_resume_eligible){
    alert(chk.error||'目前不符合单独 QA 复核恢复条件。');
    return;
  }
  if(!confirm(
    '仅恢复测试智能体复核？\n\n'
    +'任务：'+CURRENT_DRAFT_ID+'\n'
    +'DeepSeek 已用：'+chk.deepseek_tokens+' tokens\n'
    +'调整后的总预算：'+chk.qa_budget_ceiling+' tokens\n'
    +'预算余量：'+chk.qa_budget_remaining+' tokens\n\n'
    +'同意新增一次付费 DeepSeek API 调用（单次实际消耗可能超过预算余量）。\n'
    +'不会重复运行 Codex，不会重新创建隔离工作区，也不会发布真实项目。'
  ))return;
  const btn=document.getElementById('resumeQaOnlyBtn');
  btn.disabled=true;
  btn.textContent='正在单独复核...';
  try{
    const d=await api('/api/team/recovery/qa-only',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({
        draft_id:CURRENT_DRAFT_ID,
        approve_single_deepseek_call:true
      })
    });
    if(!d.ok)throw Error(d.error||'仅 QA 复核启动失败');
    btn.style.display='none';
    pollTeam();
  }catch(e){
    btn.disabled=false;
    btn.textContent='仅恢复测试复核（需授权 DeepSeek）';
    alert(String(e));
  }
}

async function pollTeam(){
  if(!CURRENT_DRAFT_ID)return;
  let d;
  try{
    d=await api('/api/team/status?draft_id='+encodeURIComponent(CURRENT_DRAFT_ID)+'&t='+Date.now());
  }catch(e){
    document.getElementById('executeBadge').textContent='状态查询暂时失败，2秒后重试';
    setTimeout(pollTeam,2000);
    return;
  }
  if(!d.ok){
    document.getElementById('executeBadge').textContent='状态获取失败，2秒后重试';
    setTimeout(pollTeam,2000);
    return;
  }
  const s=d.state||{};
  document.getElementById('teamCard').style.display='block';
  document.getElementById('teamStatus').textContent=s.status||'准备中';
  document.getElementById('currentAgent').textContent=s.current_agent||'-';
  document.getElementById('teamCodex').textContent=s.codex_calls||0;
  document.getElementById('teamRework').textContent=(s.rework_count||0)+' / '+(s.max_reworks||2);
  const used=s.deepseek_tokens||0;
  document.getElementById('teamTokens').textContent=used;
  const baseline=37780;
  if(used>0){
    const pct=Math.round((baseline-used)/baseline*100);
    document.getElementById('teamSavings').textContent=(pct>=0?pct+'%':'超出 '+Math.abs(pct)+'%');
  }else{
    document.getElementById('teamSavings').textContent='-';
  }
  const human=document.getElementById('humanAcceptance');
  if(s.status==='等待人工决策' || s.status==='等待人工验收'){
    human.style.display='block';
    let reason=s.status==='等待人工验收'
      ? '待验收版本已经同步到真实项目，请在项目验收中心实际打开并操作功能。'
      : '当前需要你做项目负责人决策。';
    const tl=s.timeline||[];
    for(let i=tl.length-1;i>=0;i--){
      const e=tl[i]||{};
      if(e.agent==='项目协调智能体' && (e.action==='升级给项目负责人' || e.action==='请项目负责人进行真实操作验收')){
        if(e.detail&&e.detail.reason)reason=e.detail.reason;
        break;
      }
    }
    const publishBtn=document.getElementById('publishCandidateBtn');
    if(publishBtn)publishBtn.style.display=s.candidate_synced?'none':'inline-block';
    document.getElementById('humanAcceptanceText').innerHTML=
      '<p>'+escapeHtml(reason)+'</p>'
      +'<p><b>建议先去上方“项目验收中心”点击：刷新验收信息 → 启动工作台 → 打开工作台，并完成真实操作。</b></p>';
  }else{
    human.style.display='none';
  }

  const ask=document.getElementById('budgetAsk');
  if(s.status==='等待预算确认' && s.budget_approval){
    ask.style.display='block';
    const q=s.budget_approval;
    document.getElementById('budgetAskText').innerHTML=
      '<p>'+escapeHtml(q.reason||'当前任务需要更多 token 才能继续。')+'</p>'
      +'<p>已用：<b>'+Number(q.used||0)+'</b> / 当前预算：<b>'+Number(q.budget||0)
      +'</b>，建议追加：<b>'+Number(q.requested_extra||0)+'</b> tokens。</p>';
  }else{
    ask.style.display='none';
  }
  document.getElementById('roleUsage').textContent=JSON.stringify(s.role_usage||{},null,2);
  document.getElementById('teamTimeline').textContent=
    (s.diagnostic_note?'任务诊断：'+s.diagnostic_note+'\n\n':'')
    +(s.error?'执行错误：'+s.error+'\n\n':'')
    +JSON.stringify(s.timeline||[],null,2);
  const safety=document.getElementById('teamSafety');
  if(safety){
    if(s.real_project){
      safety.textContent='当前安全范围：已绑定真实项目 '+s.real_project
        +'；权限 '+(s.project_mode||'未知')
        +'；仅允许写回 '+((s.allowed_paths||[]).join(', ')||'无')
        +'；Git commit 禁止。';
    }else{
      safety.textContent='当前安全范围：'+(s.safety_note||'隔离工作区');
    }
  }

  const running=d.running;
  const interrupted=!running && (
    ['执行中','准备中','自动返工中','执行中断（需要检查）','复核中断（已保留成果）','执行失败','验证失败'].includes(s.status)
  );
  const recoveryBtn=document.getElementById('inspectInterruptedBtn');
  if(recoveryBtn)recoveryBtn.style.display=interrupted?'inline-block':'none';
  const qaBtn=document.getElementById('resumeQaOnlyBtn');
  if(qaBtn && (running || !interrupted))qaBtn.style.display='none';
  document.getElementById('executeBtn').disabled=!!running || interrupted
    || ['复核通过（待安全发布）','复核发现需返工','复核等待负责人决定','复核失败（保留成果）'].includes(s.status);
  document.getElementById('executeBadge').textContent=running
    ?(Number(d.last_progress_seconds)>=300?'后台仍在运行，超过5分钟无进展':'后台执行中')
    :(s.status||'已结束');
  if(running)setTimeout(pollTeam,2000);
}

async function startTask(){
  const task=document.getElementById('task').value;
  const key=(document.getElementById('key')&&document.getElementById('key').value)||'';
  document.getElementById('start').disabled=true;

  const r=await api('/api/start',{
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({task_id:task,api_key:key})
  });

  if(!r.ok){
    alert(r.error||'启动失败');
    document.getElementById('start').disabled=false;
  }
  refresh();
}

async function refresh(){
  const task=document.getElementById('task').value;
  if(!task)return;

  const d=await api('/api/status?task_id='+encodeURIComponent(task));
  const s=d.state||{};

  document.getElementById('status').textContent=s.status||'未开始';
  document.getElementById('codex').textContent=s.codex_calls||0;
  document.getElementById('deepseek').textContent=s.deepseek_calls||0;
  document.getElementById('tokens').textContent=s.deepseek_tokens||0;

  const run=d.run||{};
  document.getElementById('runBadge').textContent=run.running?'运行中':(
    run.returncode===null||run.returncode===undefined?'未运行':'已结束 / '+run.returncode
  );
  document.getElementById('start').disabled=!!run.running;

  const log=(run.stdout||'')+(run.stderr?'\n--- stderr ---\n'+run.stderr:'');
  document.getElementById('log').textContent=log||'尚未运行。';

  const h=s.history||[];
  document.getElementById('history').textContent=h.length?JSON.stringify(h.slice(-8),null,2):'暂无。';

  const hc=document.getElementById('humanCard');
  if(d.human){
    hc.style.display='block';
    document.getElementById('humanText').innerHTML='<pre>'+escapeHtml(JSON.stringify(d.human,null,2))+'</pre>';
  }else{
    hc.style.display='none';
  }
}

setInterval(refresh,2000);
init();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def _json(self, data, status=200):
        raw=json.dumps(data,ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Cache-Control","no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma","no-cache")
        self.send_header("Expires","0")
        self.send_header("Content-Length",str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        parsed=urlparse(self.path)

        if parsed.path=="/":
            raw=INDEX_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type","text/html; charset=utf-8")
            self.send_header("Content-Length",str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return

        if parsed.path=="/api/version":
            try:
                p=subprocess.run(
                    ["git","rev-parse","--short","HEAD"],
                    cwd=ROOT,capture_output=True,text=True,timeout=10,check=False
                )
                self._json({"version":p.stdout.strip() or "unknown"})
            except Exception:
                self._json({"version":"unknown"})
            return

        if parsed.path=="/api/executors":
            self._json({"executors":executor_status()})
            return

        if parsed.path=="/api/project/acceptance":
            config=load_project_connection()
            if not config.get("connected") or not config.get("validated"):
                self._json({"ok":False,"error":"尚未连接真实项目"},400)
                return
            project=Path(config["path"])
            if not project.exists():
                self._json({"ok":False,"error":"真实项目目录不存在"},400)
                return

            allowed=config.get("allowed_paths",[])
            tree=[]
            count=0
            ignored_parts={"__pycache__", ".git", ".venv", "venv", "node_modules", ".cache", ".pytest_cache"}
            ignored_suffixes={".pyc", ".pyo"}

            for base_name in allowed:
                base=project/base_name
                if not base.exists():
                    continue
                tree.append(base_name+"/")
                for p in sorted(base.rglob("*")):
                    rel_path=p.relative_to(project)
                    if any(part in ignored_parts for part in rel_path.parts):
                        continue
                    if p.is_file() and p.suffix.lower() not in ignored_suffixes:
                        rel=rel_path.as_posix()
                        tree.append("  "+rel)
                        count+=1
                        if count>=200:
                            tree.append("  ...（最多显示 200 个有效项目文件）")
                            break
                if count>=200:
                    break

            entry="src/main.py" if (project/"src"/"main.py").exists() else ""
            manifest_path=project/"docs"/"runtime.json"
            runtime_manifest,runtime_manifest_error,runtime_manifest_raw=resolve_project_runtime(
                project,allowed
            )

            proc=PROJECT_APP.get("process")
            running=bool(proc is not None and proc.poll() is None)
            runtime_status={
                "running":running,
                "entry":PROJECT_APP.get("entry","") if running else "",
                "url":PROJECT_APP.get("url","") if running else "",
            }

            pending_candidate=latest_publishable_candidate()
            self._json({
                "ok":True,
                "project":config.get("name") or project.name,
                "path":str(project),
                "file_count":count,
                "tree":tree,
                "entry":runtime_manifest["entry"] if runtime_manifest else entry,
                "runtime_manifest":runtime_manifest,
                "runtime_manifest_path":str(manifest_path),
                "runtime_manifest_exists":manifest_path.exists(),
                "runtime_manifest_error":runtime_manifest_error,
                "runtime_manifest_raw":runtime_manifest_raw,
                "runtime_status":runtime_status,
                "allowed_paths":allowed,
                "mode":config.get("mode"),
                "read_at":time.time(),
                "pending_candidate_available":bool(pending_candidate),
                "pending_candidate_draft_id":pending_candidate["draft_id"] if pending_candidate else "",
            })
            return

        if parsed.path=="/api/team/resume":
            pending=latest_resumable_team_run()
            if not pending:
                self._json({"ok":True,"pending":None})
                return
            draft_id=pending["draft_id"]
            draft=ANALYSES.get(draft_id)
            if not draft:
                draft_path=ROOT/"orchestrator_v1"/"runtime"/"drafts"/f"{draft_id}.json"
                if draft_path.exists():
                    try:
                        draft=json.loads(draft_path.read_text(encoding="utf-8"))
                        ANALYSES[draft_id]=draft
                    except Exception:
                        draft=None
            self._json({
                "ok":True,
                "pending":{
                    "draft_id":draft_id,
                    "state":pending["state"],
                    "running":pending.get("running",False),
                    "draft":draft,
                }
            })
            return

        if parsed.path=="/api/project/acceptance/runtime":
            proc=PROJECT_APP.get("process")
            running=bool(proc is not None and proc.poll() is None)
            self._json({
                "ok":True,
                "status":{
                    "running":running,
                    "entry":PROJECT_APP.get("entry","") if running else "",
                    "url":PROJECT_APP.get("url","") if running else "",
                    "log_file":PROJECT_APP.get("log_file","") if running else "",
                }
            })
            return

        if parsed.path=="/api/project":
            self._json({"config":load_project_connection()})
            return

        if parsed.path=="/api/agents":
            try:
                profiles=load_agent_profiles()
                self._json({"profiles":profiles})
            except Exception as exc:
                self._json({"error":str(exc)},500)
            return

        if parsed.path=="/api/update/status":
            self._json(git_update_status())
            return

        if parsed.path=="/api/tasks":
            self._json({"tasks":[{"id":k,"label":v["label"]} for k,v in TASKS.items()]})
            return

        if parsed.path=="/api/status":
            q=parse_qs(parsed.query)
            task_id=q.get("task_id",[""])[0]
            if task_id not in TASKS:
                self._json({"error":"未知任务"},404)
                return

            cfg=TASKS[task_id]
            with LOCK:
                run=dict(RUNS.get(task_id,{
                    "running":False,
                    "returncode":None,
                    "stdout":"",
                    "stderr":"",
                }))

            self._json({
                "state":read_json(cfg["state_file"]),
                "human":read_json(cfg["human_file"]),
                "run":run,
            })
            return

        if parsed.path=="/api/team/safe-publish-preview":
            draft_id=parse_qs(parsed.query).get("draft_id",[""])[0]
            with LOCK:
                in_progress=bool(TEAM_RUNS.get(draft_id,{}).get("running"))
            if in_progress:
                self._json({"ok":False,"error":"团队仍在执行，暂不可发布"},409)
                return
            try:
                from safe_candidate_publish import build_publish_plan
                ctx=safe_candidate_context(draft_id)
                plan=build_publish_plan(ctx["workspace"],ctx["project"],ctx["allowed"])
                proc=PROJECT_APP.get("process")
                workbench_running=bool(proc is not None and proc.poll() is None)
                self._json({
                    "ok":True,
                    "draft_id":draft_id,
                    "project":str(ctx["project"]),
                    "revision":plan["revision"],
                    "changes":plan["changes"],
                    "change_count":len(plan["changes"]),
                    "skipped_data":plan["skipped_data"],
                    "skipped_data_count":len(plan["skipped_data"]),
                    "blocked":plan["blocked"],
                    "workbench_running":workbench_running,
                    "ready":bool(plan["changes"]) and not plan["blocked"] and not workbench_running,
                    "qa_summary":str(ctx["review"].get("summary") or ""),
                    "qa_findings":ctx["review"].get("findings") or [],
                    "safety":"不会删除真实项目文件、不会复制学生数据目录或数据库；只更新预览列出的程序文件。原程序文件先备份，之后仍需人工验收。"
                })
            except (ValueError,OSError) as exc:
                self._json({"ok":False,"error":str(exc)},409)
            except Exception as exc:
                self._json({"ok":False,"error":"发布预览失败："+str(exc)},500)
            return

        if parsed.path=="/api/team/recovery/inspect":
            q=parse_qs(parsed.query)
            draft_id=q.get("draft_id",[""])[0].strip()
            # 只允许访问已经由系统创建、管理的任务 ID。
            if not draft_id or draft_id not in TEAM_RUNS:
                self._json({"ok":False,"error":"没有找到此任务的保存记录"},404)
                return
            with LOCK:
                meta=TEAM_RUNS.get(draft_id,{})
                running=bool(meta.get("running"))
            run_dir=ROOT/"orchestrator_v1"/"dynamic_runs"/draft_id
            state_path=run_dir/"state.json"
            if not state_path.is_file():
                self._json({"ok":False,"error":"没有找到保存的任务状态"},404)
                return
            try:
                state=json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError,ValueError) as exc:
                self._json({"ok":False,"error":f"任务状态文件读取失败：{exc}"},500)
                return
            events=state.get("timeline") or []
            tests=next(
                (event.get("detail") for event in reversed(events)
                 if event.get("agent")=="系统验证器"
                 and event.get("action")=="独立运行可发现测试"),
                None
            )
            workspace=run_dir/"workspace"
            qa_file=run_dir/"artifacts"/"qa_review.json"
            codex_file=run_dir/"codex_delivery.json"
            test_exit=tests.get("returncode") if isinstance(tests,dict) else None
            tokens=int(state.get("deepseek_tokens") or 0)
            budget=int(state.get("deepseek_token_budget") or 16000)
            qacost=int((state.get("role_usage") or {}).get("测试智能体") or 0)
            latest=events[-1] if events else {}
            from qa_recovery import historical_tests
            eligible=(
                not running and workspace.is_dir() and codex_file.is_file()
                and not qa_file.is_file() and test_exit==0
                and int(state.get("codex_calls") or 0)>0
                and not state.get("qa_resume_attempted",False)
                and state.get("status") in {"执行中","执行中断（需要检查）","执行失败"}
            )
            result={
                "ok":True,"draft_id":draft_id,"running":running,
                "saved_status":state.get("status"),
                "workspace_exists":workspace.is_dir(),
                "workspace":str(workspace),
                "codex_delivery_exists":codex_file.is_file(),
                "qa_review_exists":qa_file.is_file(),
                "test_returncode":test_exit,
                "test_summary":(
                    ((tests.get("stdout") or "")+"\n"+(tests.get("stderr") or ""))[-700:]
                    if isinstance(tests,dict) else "未找到历史独立测试证据"
                ),
                "deepseek_tokens":tokens,
                "token_budget":budget,
                "qa_role_tokens":qacost,
                "budget_exceeded":tokens>budget,
                "qa_resume_eligible":eligible,
                "qa_budget_ceiling":30000,
                "qa_budget_remaining":max(0,30000-tokens),
                "last_event":{
                    "time":latest.get("time"),
                    "agent":latest.get("agent"),
                    "action":latest.get("action")
                },
                "safe_next_step":(
                    "后台仍在运行：请保持当前任务，不要重新启动。"
                    if running else
                    "单独复核已尝试，但 QA 报告仍缺失。不会自动重复收费调用；请保留隔离成果并反馈复核中断信息。"
                    if state.get("qa_resume_attempted") and not qa_file.is_file() else
                    "独立测试证据已通过且 QA 文件存在：先查看保存的 QA 结论。"
                    if test_exit==0 and qa_file.is_file() else
                    "候选工作区和自动测试通过记录存在，但没有保存的 QA 结论。需要单独恢复复核；请勿重跑整个开发任务。"
                    if workspace.is_dir() and test_exit==0 else
                    "请先核对隔离工作区和自动测试证据，不应直接发布或批准验收。"
                )
            }
            self._json(result)
            return

        if parsed.path=="/api/team/status":
            q=parse_qs(parsed.query)
            draft_id=q.get("draft_id",[""])[0]

            with LOCK:
                meta=TEAM_RUNS.get(draft_id,{})
                running=bool(meta.get("running"))
                memory_state=dict(meta.get("state") or {})

            # DynamicTeamRun 会持续把最新状态写到 state.json。
            # 运行期间优先读取磁盘中的实时状态，避免页面一直停留在“准备中”。
            state_path=ROOT/"orchestrator_v1"/"dynamic_runs"/draft_id/"state.json"
            disk_state={}
            if state_path.exists():
                try:
                    disk_state=json.loads(state_path.read_text(encoding="utf-8"))
                except Exception:
                    disk_state={}

            # worker 的失败结果可能只进入内存；此前磁盘旧“执行中”会盖住错误。
            # 只要后台线程已经退出，必须优先显示终态，而不是旧的磁盘进度。
            terminal_statuses={
                "执行失败","验证失败","已完成","等待人工验收",
                "等待人工决策","等待预算确认","需要人工返工",
                "已取消","已中止"
            }
            memory_status=memory_state.get("status","")
            if not running and memory_status in terminal_statuses:
                state=memory_state
            else:
                state=disk_state or memory_state

            # 控制台重启后，旧任务不能假装仍然在运行。
            if not running and state.get("status") in {"执行中","准备中","自动返工中","测试复核恢复中"}:
                state=dict(state)
                qa_stopped=state.get("status")=="测试复核恢复中"
                state["status"]="复核中断（已保留成果）" if qa_stopped else "执行中断（需要检查）"
                state["current_agent"]=""
                state["diagnostic_note"]=(
                    "单独 QA 复核已中断；已有尝试记录，禁止自动重复调用。"
                    if qa_stopped else
                    "后台执行线程已不存在，但上次落盘状态尚无最终结论。现有隔离工作区已保留，请勿直接重复启动同一草案。"
                )

            age_seconds=None
            if state_path.exists():
                try:
                    age_seconds=max(0,int(time.time()-state_path.stat().st_mtime))
                except OSError:
                    pass
            self._json({
                "ok":True,"running":running,"state":state,
                "last_progress_seconds":age_seconds,
                "state_source":"memory_final" if (not running and memory_status in terminal_statuses) else "disk",
            })
            return

        self.send_error(404)

    def do_POST(self):
        length=int(self.headers.get("Content-Length","0"))
        body=self.rfile.read(length)

        try:
            data=json.loads(body.decode("utf-8"))
        except Exception:
            self._json({"ok":False,"error":"请求格式错误"},400)
            return

        if self.path=="/api/project/validate":
            raw_path=(data.get("path") or "").strip()
            result=validate_project_path(raw_path)
            if not result.get("ok"):
                self._json(result,400)
                return
            self._json({"ok":True,"project":result})
            return

        if self.path=="/api/project/connect":
            raw_path=(data.get("path") or "").strip()
            mode=(data.get("mode") or "read_only").strip()
            allowed_paths=data.get("allowed_paths") or []
            allow_git_commit=False

            if mode not in {"read_only","scoped_write"}:
                self._json({"ok":False,"error":"未知权限模式"},400)
                return

            checked=validate_project_path(raw_path)
            if not checked.get("ok"):
                self._json(checked,400)
                return

            config={
                "connected":True,
                "validated":True,
                "path":checked["path"],
                "name":checked["name"],
                "is_git":checked["is_git"],
                "mode":mode,
                "allowed_paths":[str(x).strip() for x in allowed_paths if str(x).strip()],
                "allow_git_commit":allow_git_commit,
                "saved_at":time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
            save_project_connection(config)
            self._json({"ok":True,"config":config})
            return

        if self.path=="/api/project/disconnect":
            config={
                "connected":False,
                "validated":False,
                "path":"",
                "mode":"isolated",
                "allowed_paths":[],
                "allow_git_commit":False,
            }
            save_project_connection(config)
            self._json({"ok":True,"config":config})
            return

        if self.path=="/api/project/acceptance/publish-pending":
            config=load_project_connection()
            if not config.get("connected") or not config.get("validated"):
                self._json({"ok":False,"error":"尚未连接真实项目"},400)
                return
            candidate=latest_publishable_candidate()
            if not candidate:
                self._json({"ok":False,"error":"没有找到可发布的待验收版本"},404)
                return
            draft=candidate["draft"]
            project_cfg=draft.get("project_connection_snapshot") or {}
            real_project=Path(project_cfg.get("path") or config.get("path"))
            allowed=project_cfg.get("allowed_paths") or config.get("allowed_paths") or []
            try:
                from team_executor import sync_allowed_paths
                synced=sync_allowed_paths(candidate["workspace"],real_project,allowed)

                source_runtime=candidate["workspace"]/"docs"/"runtime.json"
                target_runtime=real_project/"docs"/"runtime.json"
                verified=target_runtime.exists()

                if not verified:
                    self._json({
                        "ok":False,
                        "error":"发布动作完成，但真实项目中仍未发现 docs/runtime.json。",
                        "diagnostics":{
                            "draft_id":candidate["draft_id"],
                            "workspace":str(candidate["workspace"]),
                            "source_runtime":str(source_runtime),
                            "source_runtime_exists":source_runtime.exists(),
                            "real_project":str(real_project),
                            "target_runtime":str(target_runtime),
                            "target_runtime_exists":target_runtime.exists(),
                            "allowed_paths":allowed,
                            "synced_paths":synced,
                        }
                    },500)
                    return

                state=candidate["state"]
                state["status"]="等待人工验收"
                state["pending_human_acceptance"]=True
                state["candidate_synced"]=True
                state.setdefault("timeline",[]).append({
                    "time":time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "agent":"权限控制器",
                    "action":"从项目验收中心发布待验收版本",
                    "detail":{
                        "synced_paths":synced,
                        "runtime_verified":True,
                        "target_runtime":str(target_runtime)
                    }
                })
                state_path=ROOT/"orchestrator_v1"/"dynamic_runs"/candidate["draft_id"]/"state.json"
                state_path.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding="utf-8")
                with LOCK:
                    TEAM_RUNS[candidate["draft_id"]]={"running":False,"state":state}

                self._json({
                    "ok":True,
                    "draft_id":candidate["draft_id"],
                    "synced_paths":synced,
                    "runtime_verified":True,
                    "target_runtime":str(target_runtime)
                })
            except Exception as exc:
                self._json({"ok":False,"error":str(exc)},500)
            return

        if self.path=="/api/project/acceptance/launch":
            config=load_project_connection()
            if not config.get("connected") or not config.get("validated"):
                self._json({"ok":False,"error":"尚未连接真实项目"},400)
                return
            project=Path(config["path"])
            manifest_path=project/"docs"/"runtime.json"
            if not manifest_path.exists():
                self._json({
                    "ok":False,
                    "error":"当前项目还没有 docs/runtime.json 用户入口说明。请先让团队补齐可操作界面和运行入口。"
                },400)
                return
            try:
                runtime,runtime_error,_=resolve_project_runtime(
                    project,config.get("allowed_paths",[])
                )
                if not runtime:
                    self._json({"ok":False,"error":runtime_error},400)
                    return
                rel=runtime["entry"]
                kind=runtime["type"]
                url=runtime["url"]
                launcher=runtime["launcher"]
                launch_rel=runtime["launch_entry"]
                launch_path=(project/launch_rel).resolve()
                if launcher=="powershell" and os.name!="nt":
                    self._json({"ok":False,"error":"PowerShell 入口仅支持 Windows"},400)
                    return

                old=PROJECT_APP.get("process")
                if old is not None and old.poll() is None:
                    self._json({"ok":False,"error":"工作台已经在运行"},409)
                    return

                # 只对 Python Web 服务进行无文件修改的临时端口迁移。
                # 绝不触碰占用原端口的程序，也不直接更改项目源码/清单。
                effective_url=url
                port_override=None
                original_port=None
                if kind=="web" and url:
                    import socket
                    parsed_url=urlparse(url)
                    original_port=parsed_url.port
                    with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as sock:
                        sock.settimeout(0.4)
                        occupied=sock.connect_ex(("127.0.0.1",original_port))==0
                    if occupied:
                        if launcher!="python":
                            self._json({
                                "ok":False,
                                "error":f"端口 {original_port} 已被占用；当前启动入口不支持安全自动换端口。",
                                "log":"没有改动原项目，也没有停止占用端口的程序。"
                            },409)
                            return
                        with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as free_sock:
                            free_sock.bind(("127.0.0.1",0))
                            port_override=free_sock.getsockname()[1]
                        effective_url=parsed_url._replace(
                            netloc=f"127.0.0.1:{port_override}"
                        ).geturl()

                creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0) if os.name=="nt" else 0
                log_dir=ROOT/"orchestrator_v1"/"runtime"/"project_app"
                log_dir.mkdir(parents=True,exist_ok=True)
                log_file=log_dir/"latest.log"

                env=os.environ.copy()
                src_dir=str((project/"src").resolve())
                project_dir=str(project.resolve())
                existing=env.get("PYTHONPATH","")
                env["PYTHONPATH"]=os.pathsep.join(
                    [p for p in [src_dir,project_dir,existing] if p]
                )

                if launcher=="python":
                    if port_override is not None:
                        runner=Path(__file__).resolve().parent/"port_runner.py"
                        if not runner.is_file():
                            self._json({"ok":False,"error":"缺少临时端口启动器 port_runner.py"},500)
                            return
                        argv=[
                            sys.executable,"-u",str(runner),
                            str(launch_path),str(original_port),str(port_override)
                        ]
                    else:
                        argv=[sys.executable,"-u",str(launch_path)]
                else:
                    powershell=shutil.which("powershell.exe")
                    if not powershell:
                        self._json({"ok":False,"error":"未检测到 Windows PowerShell"},400)
                        return
                    argv=[
                        powershell,"-NoLogo","-NoProfile","-NonInteractive",
                        "-ExecutionPolicy","RemoteSigned","-File",str(launch_path)
                    ]
                log_handle=open(log_file,"w",encoding="utf-8",errors="replace")
                proc=subprocess.Popen(
                    argv,
                    cwd=project,
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    env=env,
                    creationflags=creationflags,
                )
                PROJECT_APP.update({
                    "process":proc,
                    "entry":rel,
                    "url":effective_url if kind=="web" else "",
                    "started_at":time.time(),
                    "log_file":str(log_file),
                })

                ready=False
                last_error=""
                deadline=time.time()+8
                while time.time()<deadline:
                    if proc.poll() is not None:
                        break
                    if kind!="web" or not effective_url:
                        ready=True
                        break
                    try:
                        with urllib.request.urlopen(effective_url,timeout=1) as resp:
                            if 200 <= resp.status < 500:
                                ready=True
                                break
                    except Exception as exc:
                        last_error=str(exc)
                    time.sleep(0.5)

                try:
                    log_handle.flush()
                    log_handle.close()
                except Exception:
                    pass

                log_text=""
                try:
                    log_text=log_file.read_text(encoding="utf-8",errors="replace")[-12000:]
                except Exception:
                    pass

                if proc.poll() is not None:
                    PROJECT_APP.update({"process":None,"entry":"","url":"","started_at":None,"log_file":""})
                    self._json({
                        "ok":False,
                        "error":"工作台启动后退出。下面已经附上真实启动日志。",
                        "log":log_text
                    },500)
                    return

                if kind=="web" and effective_url and not ready:
                    try:
                        proc.terminate()
                    except Exception:
                        pass
                    PROJECT_APP.update({"process":None,"entry":"","url":"","started_at":None})
                    self._json({
                        "ok":False,
                        "error":"工作台已尝试启动，但 8 秒内没有在指定本地地址就绪。",
                        "log":(f"本次检查地址：{effective_url}\n"+log_text+"\n"+last_error).strip()
                    },500)
                    return

                self._json({
                    "ok":True,
                    "status":{"running":True,"entry":rel,"url":PROJECT_APP.get("url","")},
                    "actual_launch_entry":launch_rel,
                    "temporary_port_override":port_override,
                    "log":(
                        (f"检测到 {original_port} 端口冲突，已临时改用 {port_override}。\n"
                         f"实际打开地址：{effective_url}\n"
                         "没有修改真实项目文件，也没有关闭其他程序。\n"
                         if port_override is not None else "")
                        + (log_text or "工作台进程已启动并通过本地访问检查。")
                    )
                })
            except Exception as exc:
                self._json({"ok":False,"error":str(exc)},500)
            return

        if self.path=="/api/project/acceptance/stop":
            proc=PROJECT_APP.get("process")
            if proc is not None and proc.poll() is None:
                try:
                    proc.terminate()
                    proc.wait(timeout=5)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
            PROJECT_APP.update({"process":None,"entry":"","url":"","started_at":None})
            self._json({"ok":True,"status":{"running":False,"entry":"","url":""}})
            return

        if self.path=="/api/project/acceptance/test":
            config=load_project_connection()
            if not config.get("connected") or not config.get("validated"):
                self._json({"ok":False,"error":"尚未连接真实项目"},400)
                return
            project=Path(config["path"])
            if not project.exists():
                self._json({"ok":False,"error":"真实项目目录不存在"},400)
                return
            try:
                creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0) if os.name=="nt" else 0
                p=subprocess.run(
                    ["python","-m","unittest","discover","-s","tests","-v"],
                    cwd=project,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=180,
                    check=False,
                    creationflags=creationflags,
                )
                self._json({
                    "ok":True,
                    "passed":p.returncode==0,
                    "returncode":p.returncode,
                    "command":"python -m unittest discover -s tests -v",
                    "stdout":p.stdout[-12000:],
                    "stderr":p.stderr[-12000:],
                })
            except Exception as exc:
                self._json({"ok":False,"error":str(exc)},500)
            return

        if self.path=="/api/project/scope-test":
            config=load_project_connection()
            if not config.get("connected") or not config.get("validated"):
                self._json({"ok":False,"error":"当前没有已授权真实项目"},400)
                return
            if config.get("mode")!="scoped_write":
                self._json({"ok":False,"error":"请先将权限模式设置为“允许修改指定目录”"},400)
                return
            allowed=[str(x).strip() for x in config.get("allowed_paths",[]) if str(x).strip()]
            if "src" not in allowed:
                self._json({"ok":False,"error":"本验证需要 src 在允许修改目录中"},400)
                return

            real_project=Path(config["path"])
            if not real_project.exists():
                self._json({"ok":False,"error":"真实项目目录不存在"},400)
                return

            validation_dir=ROOT/"orchestrator_v1"/"dynamic_runs"/f"SCOPE-TEST-{int(time.time())}"
            staging=validation_dir/"workspace"
            try:
                from team_executor import copy_project_to_staging, sync_allowed_paths
                copy_project_to_staging(real_project,staging)

                allowed_file=staging/"src"/"__pde_scope_test__.txt"
                escape_file=staging/"__pde_scope_escape_test__.txt"
                allowed_file.parent.mkdir(parents=True,exist_ok=True)
                allowed_file.write_text("PDE_SCOPE_ALLOWED",encoding="utf-8")
                escape_file.write_text("PDE_SCOPE_ESCAPE",encoding="utf-8")

                sync_allowed_paths(staging,real_project,allowed)

                real_allowed=real_project/"src"/"__pde_scope_test__.txt"
                real_escape=real_project/"__pde_scope_escape_test__.txt"
                allowed_ok=real_allowed.exists() and real_allowed.read_text(
                    encoding="utf-8",errors="replace"
                )=="PDE_SCOPE_ALLOWED"
                escape_blocked=not real_escape.exists()
                passed=bool(allowed_ok and escape_blocked)

                if real_allowed.exists():
                    real_allowed.unlink()
                if real_escape.exists():
                    real_escape.unlink()

                self._json({
                    "ok":True,
                    "passed":passed,
                    "allowed_write":allowed_ok,
                    "escape_blocked":escape_blocked,
                    "detail":"授权目录写回成功，越权根目录写入被隔离。" if passed else "检测到权限隔离异常。"
                })
            except Exception as exc:
                self._json({"ok":False,"error":str(exc)},500)
            finally:
                if validation_dir.exists():
                    shutil.rmtree(validation_dir,ignore_errors=True)
            return

        if self.path=="/api/shutdown":
            self._json({"ok":True})
            threading.Thread(target=self.server.shutdown,daemon=True).start()
            return

        if self.path=="/api/update/apply":
            result=git_pull_update()
            self._json(result,200 if result.get("ok") else 500)
            return

        if self.path=="/api/settings/deepseek":
            api_key=(data.get("api_key") or get_deepseek_key()).strip()
            if not api_key:
                self._json({"ok":False,"error":"Key 不能为空"},400)
                return
            try:
                save_windows_user_env("DEEPSEEK_API_KEY",api_key)
            except Exception as exc:
                self._json({"ok":False,"error":str(exc)},500)
                return
            self._json({"ok":True})
            return

        if self.path=="/api/analyze":
            goal=(data.get("goal") or "").strip()
            api_key=(data.get("api_key") or get_deepseek_key()).strip()

            if not goal:
                self._json({"ok":False,"error":"请输入你的目标"},400)
                return

            try:
                analysis,usage=analyze_goal(goal,api_key)
            except Exception as exc:
                self._json({"ok":False,"error":str(exc)},500)
                return

            draft_id=f"DRAFT-{int(time.time())}"
            ANALYSES[draft_id]={
                "goal":goal,
                "analysis":analysis,
                "usage":usage,
            }

            self._json({
                "ok":True,
                "draft_id":draft_id,
                "analysis":analysis,
                "usage":usage,
            })
            return

        if self.path=="/api/refine":
            draft_id=(data.get("draft_id") or "").strip()
            user_reply=(data.get("user_reply") or "").strip()
            api_key=(data.get("api_key") or get_deepseek_key()).strip()

            if draft_id not in ANALYSES:
                self._json({"ok":False,"error":"任务草案不存在或控制台已重启"},404)
                return
            if not user_reply:
                self._json({"ok":False,"error":"请输入补充说明"},400)
                return

            draft=ANALYSES[draft_id]
            try:
                analysis,usage=refine_goal(
                    draft["goal"],
                    draft["analysis"],
                    user_reply,
                    api_key
                )
            except Exception as exc:
                self._json({"ok":False,"error":str(exc)},500)
                return

            draft["analysis"]=analysis
            draft.setdefault("conversation",[]).append({
                "owner_reply":user_reply,
                "analysis":analysis,
                "usage":usage
            })

            self._json({
                "ok":True,
                "draft_id":draft_id,
                "analysis":analysis,
                "usage":usage
            })
            return

        if self.path=="/api/confirm":
            draft_id=(data.get("draft_id") or "").strip()
            if draft_id not in ANALYSES:
                self._json({"ok":False,"error":"任务草案不存在或控制台已重启"},404)
                return

            draft=ANALYSES[draft_id]
            draft["confirmed"]=True
            project_config=load_project_connection()
            requested_real=bool(data.get("use_real_project"))
            if requested_real and not project_config.get("connected"):
                self._json({"ok":False,"error":"你勾选了真实项目，但当前没有已授权项目"},400)
                return
            draft["project_connection_snapshot"]=project_config if project_config.get("connected") else None
            draft["use_real_project"]=requested_real

            out_dir=ROOT/"orchestrator_v1"/"runtime"/"drafts"
            out_dir.mkdir(parents=True,exist_ok=True)
            out_file=out_dir/f"{draft_id}.json"
            out_file.write_text(
                json.dumps(draft,ensure_ascii=False,indent=2),
                encoding="utf-8"
            )

            self._json({
                "ok":True,
                "draft_id":draft_id,
                "saved_to":str(out_file.relative_to(ROOT))
            })
            return

        if self.path=="/api/validation/rework/start":
            if not get_deepseek_key():
                self._json({"ok":False,"error":"DeepSeek 尚未连接"},400)
                return
            draft_id=f"VALIDATE-REWORK-{int(time.time())}"
            draft={
                "goal":"验证动态团队自动返工闭环，不连接真实项目。",
                "confirmed":True,
                "validation_force_rework_once":True,
                "validation_seed_rework_workspace":True,
                "validation_local_rework_executor":True,
                "analysis":{
                    "task_level":"B",
                    "summary":"创建一个极小 Python 原型，用于验证开发、测试、自动返工和复核链路。",
                    "reason":"系统回归验证",
                    "required_agents":["开发智能体","测试智能体"],
                    "scope":["在隔离工作区创建最小 Python 示例和 unittest"],
                    "out_of_scope":["真实辅导员工作台","外部业务数据"],
                    "acceptance_criteria":[
                        "存在可运行的 Python 示例",
                        "存在 unittest 且系统验证器可执行",
                        "首次测试复核固定触发一次返工",
                        "返工后再次验证并得到最终结论"
                    ],
                    "questions":[],
                    "needs_owner_decision":False,
                    "recommended_executor":"Codex CLI + DeepSeek API",
                    "next_action":"执行回归验证"
                }
            }
            ANALYSES[draft_id]=draft
            with LOCK:
                TEAM_RUNS[draft_id]={"running":True,"state":{"status":"准备中","timeline":[]}}

            def validation_worker():
                runner=None
                try:
                    runner=DynamicTeamRun(ROOT,draft_id,draft,get_deepseek_key())
                    state=runner.run()
                except Exception as exc:
                    if runner is not None:
                        state=runner.state
                        state["status"]="验证失败"
                        state["current_agent"]=""
                        runner.event("系统验证","自动返工验证异常",str(exc))
                        runner.state["current_agent"]=""
                        runner.save()
                    else:
                        state={"status":"验证失败","timeline":[{"agent":"系统验证","action":"启动失败","detail":str(exc)}]}
                with LOCK:
                    TEAM_RUNS[draft_id]={"running":False,"state":state}

            threading.Thread(target=validation_worker,daemon=True).start()
            self._json({"ok":True,"draft_id":draft_id})
            return

        if self.path=="/api/validation/budget/start":
            draft_id=f"VALIDATE-BUDGET-{int(time.time())}"
            draft={
                "goal":"仅验证预算审批交互，不调用模型。",
                "confirmed":True,
                "validation_budget_only":True,
                "analysis":{
                    "task_level":"A",
                    "summary":"预算审批交互验证",
                    "required_agents":[],
                    "scope":[],
                    "acceptance_criteria":[],
                    "questions":[],
                    "needs_owner_decision":True
                }
            }
            ANALYSES[draft_id]=draft
            run_dir=ROOT/"orchestrator_v1"/"dynamic_runs"/draft_id
            run_dir.mkdir(parents=True,exist_ok=True)
            state={
                "draft_id":draft_id,
                "status":"等待预算确认",
                "team":[],
                "current_agent":"",
                "deepseek_tokens":15800,
                "deepseek_token_budget":16000,
                "role_usage":{"测试智能体":4200},
                "codex_calls":1,
                "deepseek_calls":3,
                "rework_count":1,
                "max_reworks":2,
                "budget_approval":{
                    "role":"测试智能体",
                    "used":15800,
                    "budget":16000,
                    "requested_extra":2500,
                    "reason":"测试智能体正在进行返工后的复核，建议追加 2500 tokens 完成本轮检查。"
                },
                "timeline":[{
                    "time":time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "agent":"成本控制器",
                    "action":"验证模式：申请追加预算",
                    "detail":"本验证不会产生新的模型调用。"
                }],
                "validation_budget_only":True
            }
            (run_dir/"state.json").write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding="utf-8")
            with LOCK:
                TEAM_RUNS[draft_id]={"running":False,"state":state}
            self._json({"ok":True,"draft_id":draft_id})
            return

        if self.path=="/api/team/publish-candidate":
            draft_id=(data.get("draft_id") or "").strip()
            if draft_id not in ANALYSES:
                self._json({"ok":False,"error":"任务草案不存在或控制台已重启"},404)
                return
            state_path=ROOT/"orchestrator_v1"/"dynamic_runs"/draft_id/"state.json"
            if not state_path.exists():
                self._json({"ok":False,"error":"未找到团队运行状态"},404)
                return

            draft=ANALYSES[draft_id]
            project_cfg=draft.get("project_connection_snapshot") or {}
            if not draft.get("use_real_project") or not project_cfg.get("connected"):
                self._json({"ok":False,"error":"当前任务没有绑定真实项目"},400)
                return

            state=json.loads(state_path.read_text(encoding="utf-8"))
            if state.get("status") not in {"等待人工决策","等待人工验收"}:
                self._json({"ok":False,"error":"当前任务不是待人工验收状态"},409)
                return

            run_dir=ROOT/"orchestrator_v1"/"dynamic_runs"/draft_id
            workspace=run_dir/"workspace"
            real_project=Path(project_cfg["path"])
            allowed=project_cfg.get("allowed_paths") or []
            if not workspace.exists():
                self._json({"ok":False,"error":"隔离工作区不存在"},404)
                return
            try:
                from team_executor import sync_allowed_paths
                synced=sync_allowed_paths(workspace,real_project,allowed)
                state["status"]="等待人工验收"
                state["pending_human_acceptance"]=True
                state["candidate_synced"]=True
                state.setdefault("timeline",[]).append({
                    "time":time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "agent":"权限控制器",
                    "action":"已手动发布待人工验收版本",
                    "detail":{"synced_paths":synced}
                })
                state_path.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding="utf-8")
                with LOCK:
                    TEAM_RUNS[draft_id]={"running":False,"state":state}
                self._json({"ok":True,"synced_paths":synced,"status":"等待人工验收"})
            except Exception as exc:
                self._json({"ok":False,"error":str(exc)},500)
            return

        if self.path=="/api/team/human-decision":
            draft_id=(data.get("draft_id") or "").strip()
            decision=(data.get("decision") or "").strip()
            note=(data.get("note") or "").strip()
            if draft_id not in ANALYSES:
                self._json({"ok":False,"error":"任务草案不存在或控制台已重启"},404)
                return
            state_path=ROOT/"orchestrator_v1"/"dynamic_runs"/draft_id/"state.json"
            if not state_path.exists():
                self._json({"ok":False,"error":"未找到团队运行状态"},404)
                return
            state=json.loads(state_path.read_text(encoding="utf-8"))
            if state.get("status") not in {"等待人工决策","等待人工验收"}:
                self._json({"ok":False,"error":"当前任务不在等待人工验收/决策状态"},409)
                return

            if decision=="approve":
                state["status"]="已完成"
                state["current_agent"]=""
                state["pending_human_acceptance"]=False
                state.setdefault("timeline",[]).append({
                    "time":time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "agent":"项目负责人",
                    "action":"人工验收通过",
                    "detail":{"note":note}
                })
                state_path.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding="utf-8")
                with LOCK:
                    TEAM_RUNS[draft_id]={"running":False,"state":state}
                self._json({"ok":True,"status":"已完成"})
                return

            if decision=="rework":
                if not note:
                    self._json({"ok":False,"error":"退回返工时必须填写实际问题"},400)
                    return
                state["status"]="需要人工返工"
                state["current_agent"]=""
                state.setdefault("timeline",[]).append({
                    "time":time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "agent":"项目负责人",
                    "action":"人工验收退回返工",
                    "detail":{"note":note}
                })
                state_path.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding="utf-8")
                with LOCK:
                    TEAM_RUNS[draft_id]={"running":False,"state":state}
                self._json({"ok":True,"status":"需要人工返工"})
                return

            self._json({"ok":False,"error":"未知人工决策"},400)
            return

        if self.path=="/api/team/budget":
            draft_id=(data.get("draft_id") or "").strip()
            decision=(data.get("decision") or "").strip()
            if draft_id not in ANALYSES:
                self._json({"ok":False,"error":"任务草案不存在或控制台已重启"},404)
                return
            state_path=ROOT/"orchestrator_v1"/"dynamic_runs"/draft_id/"state.json"
            if not state_path.exists():
                self._json({"ok":False,"error":"未找到团队运行状态"},404)
                return
            state=json.loads(state_path.read_text(encoding="utf-8"))
            req=state.get("budget_approval") or {}
            if decision=="decline":
                state["status"]="验证通过：已选择暂停" if draft_id.startswith("VALIDATE-BUDGET-") else "已暂停"
                state["budget_approval"]=None
                state_path.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding="utf-8")
                with LOCK:
                    TEAM_RUNS[draft_id]={"running":False,"state":state}
                self._json({"ok":True,"status":"已暂停"})
                return
            if decision!="approve":
                self._json({"ok":False,"error":"未知预算决策"},400)
                return

            if draft_id.startswith("VALIDATE-BUDGET-"):
                extra=int(req.get("requested_extra") or 2500)
                state["deepseek_token_budget"]=int(req.get("budget") or 16000)+extra
                state["budget_approval"]=None
                state["status"]="验证通过：预算已批准"
                state.setdefault("timeline",[]).append({
                    "time":time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "agent":"项目负责人",
                    "action":"验证模式：批准追加预算",
                    "detail":{"added":extra,"new_budget":state["deepseek_token_budget"]}
                })
                state_path.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding="utf-8")
                with LOCK:
                    TEAM_RUNS[draft_id]={"running":False,"state":state}
                self._json({"ok":True,"new_budget":state["deepseek_token_budget"],"validation":True})
                return

            current_budget=int(req.get("budget") or state.get("deepseek_token_budget") or 16000)
            extra=int(req.get("requested_extra") or 3000)
            approved=min(current_budget+extra,30000)
            draft=ANALYSES[draft_id]
            draft["approved_token_budget"]=approved
            state["budget_approval"]=None
            state["status"]="准备继续"
            state_path.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding="utf-8")

            with LOCK:
                if TEAM_RUNS.get(draft_id,{}).get("running"):
                    self._json({"ok":False,"error":"团队仍在运行"},409)
                    return
                TEAM_RUNS[draft_id]={"running":True,"state":state}

            def resume_worker():
                runner=None
                try:
                    runner=DynamicTeamRun(ROOT,draft_id,draft,get_deepseek_key())
                    # 保留现有工作区，重新执行时 Codex 会基于同一目录继续；预算使用新上限。
                    runner.state["deepseek_token_budget"]=approved
                    runner.state["status"]="继续执行"
                    runner.event("成本控制器","项目负责人已批准追加预算",{
                        "new_budget":approved,
                        "added":extra,
                    })
                    result=runner.run()
                    with LOCK:
                        TEAM_RUNS[draft_id]={"running":False,"state":result}
                except Exception as exc:
                    with LOCK:
                        TEAM_RUNS[draft_id]={"running":False,"state":{
                            "status":"执行失败",
                            "timeline":[{"agent":"系统","action":"继续执行失败","detail":str(exc)}]
                        }}
            threading.Thread(target=resume_worker,daemon=True).start()
            self._json({"ok":True,"new_budget":approved})
            return

        if self.path=="/api/team/safe-publish":
            draft_id=str(data.get("draft_id") or "").strip()
            revision=str(data.get("revision") or "").strip()
            if data.get("confirmation")!="publish_reviewed_candidate":
                self._json({"ok":False,"error":"必须明确确认刚才预览过的安全发布"},400)
                return
            if len(revision)!=64:
                self._json({"ok":False,"error":"请先获取最新的文件差异预览"},400)
                return
            with LOCK:
                if TEAM_RUNS.get(draft_id,{}).get("running"):
                    self._json({"ok":False,"error":"任务仍有后台线程"},409)
                    return
                try:
                    from safe_candidate_publish import apply_publish_plan
                    ctx=safe_candidate_context(draft_id)
                    proc=PROJECT_APP.get("process")
                    if proc is not None and proc.poll() is None:
                        self._json({
                            "ok":False,
                            "error":"现有工作台正在运行，请先在项目验收中心停止工作台再发布。"
                        },409)
                        return
                    backups=ROOT/"orchestrator_v1"/"runtime"/"safe_publish_backups"
                    result=apply_publish_plan(
                        ctx["workspace"],ctx["project"],ctx["allowed"],
                        revision,backups,draft_id
                    )
                    state=ctx["state"]
                    state["candidate_synced"]=True
                    state["pending_human_acceptance"]=True
                    state["status"]="等待人工验收"
                    state["current_agent"]=""
                    state["safe_publish_backup"]=result["backup_path"]
                    state.setdefault("timeline",[]).append({
                        "time":time.strftime("%Y-%m-%dT%H:%M:%S"),
                        "agent":"权限控制器","action":"QA 通过后安全发布待人工验收版本",
                        "detail":result,
                    })
                    ctx["state_path"].write_text(
                        json.dumps(state,ensure_ascii=False,indent=2),encoding="utf-8"
                    )
                    TEAM_RUNS[draft_id]={"running":False,"state":state}
                    self._json({
                        "ok":True,"status":"等待人工验收",
                        "backup_path":result["backup_path"],
                        "changed_files":result["files_changed"],
                        "skipped_data":result["skipped_data_files"],
                        "changed_paths":result["changed_paths"],
                    })
                except (ValueError,OSError) as exc:
                    self._json({"ok":False,"error":str(exc)},409)
                except Exception as exc:
                    self._json({"ok":False,"error":"安全发布失败："+str(exc)},500)
            return

        if self.path=="/api/team/recovery/qa-only":
            draft_id=str(data.get("draft_id") or "").strip()
            if data.get("approve_single_deepseek_call") is not True:
                self._json({"ok":False,"error":"必须明确授权本次单独复核费用"},400)
                return
            draft=ANALYSES.get(draft_id)
            if not draft or not draft.get("confirmed"):
                self._json({"ok":False,"error":"没有可恢复的已确认草案"},404)
                return
            key=get_deepseek_key()
            if not key:
                self._json({"ok":False,"error":"尚未配置 DeepSeek API Key"},400)
                return
            from qa_recovery import historical_tests,recover_once
            run_dir=ROOT/"orchestrator_v1"/"dynamic_runs"/draft_id
            state_path=run_dir/"state.json"
            workspace=run_dir/"workspace"
            delivery_file=run_dir/"codex_delivery.json"
            qa_file=run_dir/"artifacts"/"qa_review.json"
            if not (state_path.is_file() and workspace.is_dir() and delivery_file.is_file()):
                self._json({"ok":False,"error":"原隔离工作区或交付证据缺失"},409)
                return
            try:
                state=json.loads(state_path.read_text(encoding="utf-8"))
                json.loads(delivery_file.read_text(encoding="utf-8"))
            except (ValueError,OSError) as exc:
                self._json({"ok":False,"error":"原始证据损坏："+str(exc)},409)
                return
            ev=historical_tests(state)
            if not isinstance(ev,dict) or ev.get("returncode")!=0:
                self._json({"ok":False,"error":"未发现通过的原自动测试记录"},409)
                return
            if int(state.get("deepseek_tokens") or 0)>=30000:
                self._json({"ok":False,"error":"已达到 30000 tokens 上限，不能发起新的付费调用"},409)
                return
            with LOCK:
                if TEAM_RUNS.get(draft_id,{}).get("running"):
                    self._json({"ok":False,"error":"已有同任务后台执行"},409)
                    return
                if qa_file.exists() or state.get("qa_resume_attempted"):
                    self._json({"ok":False,"error":"复核已尝试或 QA 报告已存在，防止重复付费"},409)
                    return
                if state.get("status") not in {"执行中","执行中断（需要检查）","执行失败"}:
                    self._json({"ok":False,"error":"状态不属于可恢复的中断任务"},409)
                    return
                # 在启动线程前写入防重和授权证据。
                state["qa_resume_attempted"]=True
                state["qa_resume_authorized"]=True
                state["deepseek_token_budget"]=30000
                state["status"]="测试复核恢复中"
                state["current_agent"]="测试智能体"
                state.setdefault("timeline",[]).append({
                    "time":time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "agent":"项目负责人","action":"授权仅测试智能体单次复核",
                    "detail":{
                        "tokens_before":int(state.get("deepseek_tokens") or 0),
                        "budget_after":30000,"allow_more_than_one_call":False
                    }
                })
                state_path.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding="utf-8")
                TEAM_RUNS[draft_id]={"running":True,"state":state}

            def qa_worker():
                final=recover_once(ROOT,draft_id,draft,key,state)
                with LOCK:
                    TEAM_RUNS[draft_id]={"running":False,"state":final}
            threading.Thread(target=qa_worker,daemon=True).start()
            self._json({
                "ok":True,"draft_id":draft_id,"status":"测试复核恢复中",
                "note":"仅使用历史 Codex 交付与测试证据，未修改项目源文件。"
            })
            return

        if self.path=="/api/team/start":
            draft_id=(data.get("draft_id") or "").strip()
            api_key=(data.get("api_key") or get_deepseek_key()).strip()

            if draft_id not in ANALYSES:
                self._json({"ok":False,"error":"任务草案不存在或控制台已重启"},404)
                return

            draft=ANALYSES[draft_id]
            if not draft.get("confirmed"):
                self._json({"ok":False,"error":"请先确认任务草案"},400)
                return

            if not api_key and not os.getenv("DEEPSEEK_API_KEY"):
                self._json({"ok":False,"error":"请输入 DeepSeek API Key"},400)
                return

            key=api_key or os.getenv("DEEPSEEK_API_KEY")

            with LOCK:
                if TEAM_RUNS.get(draft_id,{}).get("running"):
                    self._json({"ok":False,"error":"团队正在执行"},409)
                    return
                TEAM_RUNS[draft_id]={"running":True,"state":{"status":"准备中","timeline":[]}}

            def worker():
                runner=None
                try:
                    runner=DynamicTeamRun(ROOT,draft_id,draft,key)
                    state=runner.run()
                    with LOCK:
                        TEAM_RUNS[draft_id]={"running":False,"state":state}
                except Exception as exc:
                    if runner is not None and exc.__class__.__name__=="BudgetApprovalRequired":
                        request={
                            "role":getattr(exc,"role","当前智能体"),
                            "used":getattr(exc,"used",runner.state.get("deepseek_tokens",0)),
                            "budget":getattr(exc,"budget",runner.state.get("deepseek_token_budget",0)),
                            "requested_extra":getattr(exc,"requested_extra",3000),
                            "reason":getattr(exc,"reason",str(exc)),
                        }
                        runner.state["status"]="等待预算确认"
                        runner.state["budget_approval"]=request
                        runner.state["current_agent"]=""
                        runner.event("成本控制器","向项目负责人申请追加预算",request)
                        runner.state["current_agent"]=""
                        runner.save()
                        state=runner.state
                    else:
                        if runner is not None:
                            # 保留已完成的角色、token、Codex 交付和测试日志。
                            runner.state["status"]="执行失败"
                            runner.state["current_agent"]=""
                            runner.state["error"]=str(exc)
                            try:
                                runner.event("系统","执行失败，请检查异常并保留现有候选代码",str(exc))
                                runner.state["current_agent"]=""
                                runner.save()
                            except Exception:
                                pass
                            state=runner.state
                        else:
                            state={
                                "status":"执行失败",
                                "current_agent":"",
                                "error":str(exc),
                                "timeline":[{"agent":"系统","action":"执行失败","detail":str(exc)}]
                            }
                    with LOCK:
                        TEAM_RUNS[draft_id]={"running":False,"state":state}

            threading.Thread(target=worker,daemon=True).start()
            self._json({"ok":True,"draft_id":draft_id})
            return

        if self.path!="/api/start":
            self.send_error(404)
            return

        task_id=data.get("task_id")
        api_key=(data.get("api_key") or get_deepseek_key()).strip()

        if task_id not in TASKS:
            self._json({"ok":False,"error":"未知任务"},400)
            return

        with LOCK:
            if RUNS.get(task_id,{}).get("running"):
                self._json({"ok":False,"error":"任务正在运行"},409)
                return

        if not api_key and not os.getenv("DEEPSEEK_API_KEY"):
            self._json({"ok":False,"error":"请输入 DeepSeek API Key，或先设置系统环境变量"},400)
            return

        t=threading.Thread(
            target=run_task,
            args=(task_id,api_key),
            daemon=True,
        )
        t.start()
        self._json({"ok":True})

    def log_message(self, format, *args):
        pass


def main():
    load_saved_drafts()
    restore_team_runs_from_disk()
    server=ThreadingHTTPServer((HOST,PORT),Handler)
    actual_port=server.server_address[1]
    url=f"http://{HOST}:{actual_port}"

    print("PDE 多智能体控制台已启动：",url)
    print("已自动选择空闲端口。")
    print("关闭此窗口即可停止控制台。")

    threading.Timer(1.0,lambda:webbrowser.open(url)).start()
    server.serve_forever()


if __name__=="__main__":
    main()

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

  <div class="card">
    <h3>执行器中心</h3>
    <div id="executorGrid" class="grid"></div>
  </div>

  <div class="card">
    <h3>系统维护</h3>
    <button onclick="checkUpdate()">检查更新</button>
    <button id="updateBtn" onclick="applyUpdate()" style="background:#2563eb;display:none">立即更新</button>
    <button onclick="shutdownConsole()" style="background:#6b7280">退出控制台</button>
    <span id="updateBadge" class="badge">尚未检查</span>
    <span id="versionBadge" class="badge">版本读取中</span>
  </div>

  <div class="card">
    <h3>Agent 身份</h3>
    <div id="agentProfiles" class="grid"></div>
  </div>

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
    <h3>系统回归验证</h3>
    <div style="padding:10px;background:#eff6ff;border-radius:8px;line-height:1.6">
      这里仅验证多智能体编排流程，不连接真实辅导员工作台。回归验证使用本地验证执行器，不调用 Codex/DeepSeek；真实任务仍使用真实模型。
    </div>
    <button id="reworkTestBtn" onclick="startReworkValidation()" style="background:#7c3aed">验证自动返工闭环</button>
    <button id="budgetTestBtn" onclick="startBudgetValidation()" style="background:#0f766e;margin-left:8px">验证预算申请交互</button>
    <span id="validationBadge" class="badge">尚未验证</span>
    <pre id="validationResult" style="display:none;margin-top:12px"></pre>
  </div>

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
    <div style="margin-top:14px;padding:10px;background:#fff7ed;border-radius:8px">
      当前安全范围：未指定真实目标仓库时，只在隔离工作区做原型，不修改现有产品。
    </div>
    <button id="executeBtn" onclick="startTeamExecution()" style="background:#7c3aed">启动团队执行</button>
    <span id="executeBadge" class="badge">等待草案确认</span>
    <div id="budgetAsk" class="card human" style="display:none;margin-top:14px">
      <h4>需要追加少量预算</h4>
      <div id="budgetAskText"></div>
      <button onclick="approveBudget()" style="background:#166534">同意追加并继续</button>
      <button onclick="declineBudget()" style="background:#6b7280">先暂停任务</button>
    </div>
    <h4>分角色成本</h4>
    <pre id="roleUsage">暂无。</pre>
    <h4>团队时间线</h4>
    <pre id="teamTimeline">暂无。</pre>
  </div>

  <div class="card">
    <h3>已有测试任务</h3>
    <label>选择任务</label>
    <select id="task"></select>
    <button id="start" onclick="startTask()">开始执行</button>
    <span id="runBadge" class="badge">未运行</span>
  </div>

  <div class="card">
    <div class="grid">
      <div class="stat">当前状态<b id="status">-</b></div>
      <div class="stat">Codex 调用<b id="codex">0</b></div>
      <div class="stat">DeepSeek 调用<b id="deepseek">0</b></div>
      <div class="stat">DeepSeek tokens<b id="tokens">0</b></div>
    </div>
  </div>

  <div id="humanCard" class="card human" style="display:none">
    <h3>需要项目负责人决策</h3>
    <div id="humanText"></div>
  </div>

  <div class="card">
    <h3>最近运行日志</h3>
    <pre id="log">尚未运行。</pre>
  </div>

  <div class="card">
    <h3>任务历史</h3>
    <pre id="history">暂无。</pre>
  </div>
</div>

<script>
let CURRENT_DRAFT_ID = null;

async function api(path, options){
  const r=await fetch(path, options);
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
  document.getElementById('teamStatus').textContent=s.status||'准备中';
  document.getElementById('currentAgent').textContent=s.current_agent||'-';
  document.getElementById('teamCodex').textContent=s.codex_calls||0;
  document.getElementById('teamRework').textContent=(s.rework_count||0)+' / '+(s.max_reworks||2);
  document.getElementById('teamTokens').textContent=s.deepseek_tokens||0;
  document.getElementById('roleUsage').textContent=JSON.stringify(s.role_usage||{},null,2);
  document.getElementById('teamTimeline').textContent=JSON.stringify(s.timeline||[],null,2);
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

async function pollTeam(){
  if(!CURRENT_DRAFT_ID)return;
  const d=await api('/api/team/status?draft_id='+encodeURIComponent(CURRENT_DRAFT_ID));
  if(!d.ok)return;
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
  document.getElementById('teamTimeline').textContent=JSON.stringify(s.timeline||[],null,2);

  const running=d.running;
  document.getElementById('executeBtn').disabled=!!running;
  document.getElementById('executeBadge').textContent=running?'执行中':(s.status||'已结束');
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

            state=disk_state or memory_state
            self._json({"ok":True,"running":running,"state":state})
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
                        state={
                            "status":"执行失败",
                            "current_agent":"",
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

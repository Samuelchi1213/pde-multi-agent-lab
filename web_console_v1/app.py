import json
import os
import subprocess
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse\n\nfrom coordinator import analyze_goal

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
LOCK = threading.Lock()


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
    if api_key:
        env["DEEPSEEK_API_KEY"] = api_key

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
<title>PDE 多智能体控制台</title>
<style>
body{font-family:Arial,"Microsoft YaHei",sans-serif;margin:0;background:#f6f7f9;color:#222}
.wrap{max-width:1100px;margin:32px auto;padding:0 20px}
h1{margin-bottom:8px}.sub{color:#666;margin-bottom:24px}
.card{background:#fff;border:1px solid #e5e7eb;border-radius:14px;padding:18px;margin-bottom:16px;box-shadow:0 2px 8px rgba(0,0,0,.04)}
.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}
.stat{background:#f8fafc;border-radius:10px;padding:12px}.stat b{display:block;font-size:22px;margin-top:6px}
label{display:block;font-weight:600;margin:10px 0 6px}
select,input,button{font-size:15px;padding:10px 12px;border-radius:9px;border:1px solid #d1d5db}
select,input{width:100%;box-sizing:border-box}
button{cursor:pointer;background:#111827;color:#fff;border:none;margin-top:12px}
button:disabled{opacity:.5;cursor:not-allowed}
pre{white-space:pre-wrap;word-break:break-word;background:#111827;color:#e5e7eb;padding:14px;border-radius:10px;max-height:360px;overflow:auto}
.badge{display:inline-block;padding:4px 9px;border-radius:999px;background:#e5e7eb;font-size:13px}
.human{border-left:4px solid #d97706;background:#fff7ed}
.ok{border-left:4px solid #16a34a}
@media(max-width:800px){.grid{grid-template-columns:1fr 1fr}}
</style>
</head>
<body>
<div class="wrap">
  <h1>PDE 多智能体控制台 v1</h1>
  <div class="sub">用网页启动任务、查看状态、成本和人工决策，不再把 CMD 当主界面。</div>

  <div class="card">
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
async function api(path, options){ const r=await fetch(path, options); return await r.json(); }

async function init(){
  const data=await api('/api/tasks');
  const sel=document.getElementById('task');
  for(const t of data.tasks){
    const o=document.createElement('option');
    o.value=t.id;o.textContent=t.label;sel.appendChild(o);
  }
  sel.onchange=refresh;
  refresh();
}

async function analyzeGoal(){
  const goal=document.getElementById('goal').value.trim();
  const key=document.getElementById('key').value;
  if(!goal){alert('先告诉团队你想做什么。');return;}

  const btn=document.getElementById('analyzeBtn');
  const badge=document.getElementById('analyzeBadge');
  btn.disabled=true; badge.textContent='分析中';

  const r=await api('/api/analyze',{
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({goal:goal,api_key:key})
  });

  btn.disabled=false;
  if(!r.ok){badge.textContent='分析失败';alert(r.error||'分析失败');return;}

  badge.textContent='分析完成';
  const a=r.analysis;
  document.getElementById('analysisCard').style.display='block';
  document.getElementById('taskLevel').textContent=a.task_level||'-';
  document.getElementById('agentCount').textContent=(a.required_agents||[]).length;
  document.getElementById('ownerDecision').textContent=a.needs_owner_decision?'是':'否';
  document.getElementById('analysisTokens').textContent=(r.usage&&r.usage.total_tokens)||0;

  const sections=[
    ['我的理解',a.summary],
    ['为什么这样分级',a.reason],
    ['建议参与的智能体',(a.required_agents||[]).join('、')||'无'],
    ['本次范围',(a.scope||[]).map(x=>'• '+x).join('<br>')],
    ['暂不包含',(a.out_of_scope||[]).map(x=>'• '+x).join('<br>')],
    ['验收标准',(a.acceptance_criteria||[]).map(x=>'• '+x).join('<br>')],
    ['需要你补充的问题',(a.questions||[]).map(x=>'• '+x).join('<br>')||'暂无'],
    ['建议执行通道',a.recommended_executor||'待动态路由'],
    ['下一步',a.next_action]
  ];

  if(a.needs_owner_decision){
    sections.splice(7,0,['为什么需要你拍板',a.owner_decision_reason||'存在需要项目负责人决定的问题']);
  }

  document.getElementById('analysisText').innerHTML=sections
    .filter(x=>x[1])
    .map(x=>'<h4>'+escapeHtml(String(x[0]))+'</h4><div>'+String(x[1])+'</div>')
    .join('');
}

async function startTask(){
  const task=document.getElementById('task').value;
  const key=document.getElementById('key').value;
  document.getElementById('start').disabled=true;
  const r=await api('/api/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({task_id:task,api_key:key})});
  if(!r.ok){alert(r.error||'启动失败');document.getElementById('start').disabled=false;}
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
  document.getElementById('runBadge').textContent=run.running?'运行中':(run.returncode===null||run.returncode===undefined?'未运行':'已结束 / '+run.returncode);
  document.getElementById('start').disabled=!!run.running;

  const log=(run.stdout||'')+(run.stderr?'\n--- stderr ---\n'+run.stderr:'');
  document.getElementById('log').textContent=log||'尚未运行。';

  const h=s.history||[];
  document.getElementById('history').textContent=h.length?JSON.stringify(h.slice(-8),null,2):'暂无。';

  const hc=document.getElementById('humanCard');
  if(d.human){
    hc.style.display='block';
    document.getElementById('humanText').innerHTML='<pre>'+escapeHtml(JSON.stringify(d.human,null,2))+'</pre>';
  }else{hc.style.display='none';}
}

function escapeHtml(s){return s.replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[m]));}

setInterval(refresh,2000);
init();
</script>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def _json(self, data, status=200):
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            raw = INDEX_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return

        if parsed.path == "/api/tasks":
            self._json({
                "tasks": [
                    {"id": k, "label": v["label"]}
                    for k, v in TASKS.items()
                ]
            })
            return

        if parsed.path == "/api/status":
            from urllib.parse import parse_qs
            q = parse_qs(parsed.query)
            task_id = q.get("task_id", [""])[0]
            if task_id not in TASKS:
                self._json({"error": "未知任务"}, 404)
                return
            cfg = TASKS[task_id]
            with LOCK:
                run = dict(RUNS.get(task_id, {
                    "running": False,
                    "returncode": None,
                    "stdout": "",
                    "stderr": "",
                }))
            self._json({
                "state": read_json(cfg["state_file"]),
                "human": read_json(cfg["human_file"]),
                "run": run,
            })
            return

        self.send_error(404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        try:
            data = json.loads(body.decode("utf-8"))
        except Exception:
            self._json({"ok": False, "error": "请求格式错误"}, 400)
            return

        if self.path == "/api/analyze":
            goal = (data.get("goal") or "").strip()
            api_key = (data.get("api_key") or "").strip()
            if not goal:
                self._json({"ok": False, "error": "请输入你的目标"}, 400)
                return
            try:
                analysis, usage = analyze_goal(goal, api_key)
            except Exception as exc:
                self._json({"ok": False, "error": str(exc)}, 500)
                return

            draft_id = f"DRAFT-{int(time.time())}"
            ANALYSES[draft_id] = {
                "goal": goal,
                "analysis": analysis,
                "usage": usage,
            }
            self._json({
                "ok": True,
                "draft_id": draft_id,
                "analysis": analysis,
                "usage": usage,
            })
            return

        if self.path != "/api/start":
            self.send_error(404)
            return

        task_id = data.get("task_id")
        api_key = (data.get("api_key") or "").strip()

        if task_id not in TASKS:
            self._json({"ok": False, "error": "未知任务"}, 400)
            return

        with LOCK:
            if RUNS.get(task_id, {}).get("running"):
                self._json({"ok": False, "error": "任务正在运行"}, 409)
                return

        if not api_key and not os.getenv("DEEPSEEK_API_KEY"):
            self._json({"ok": False, "error": "请输入 DeepSeek API Key，或先设置系统环境变量"}, 400)
            return

        t = threading.Thread(
            target=run_task,
            args=(task_id, api_key),
            daemon=True,
        )
        t.start()
        self._json({"ok": True})

    def log_message(self, format, *args):
        pass


def main():
    # PORT=0 让 Windows 自动分配一个空闲端口，避免与其他本地程序冲突。
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    actual_port = server.server_address[1]
    url = f"http://{HOST}:{actual_port}"
    print("PDE 多智能体控制台已启动：", url)
    print("已自动选择空闲端口，避免与其他本地程序冲突。")
    print("关闭此窗口即可停止控制台。")
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    server.serve_forever()


if __name__ == "__main__":
    main()

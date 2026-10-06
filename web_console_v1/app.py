import json
import os
import subprocess
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from coordinator import analyze_goal, refine_goal
from team_executor import DynamicTeamRun

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
    <h3>告诉团队你现在想做什么</h3>
    <textarea id="goal" placeholder="例如：给辅导员工作台增加晚返学生提醒功能，但不要给学生造成太强的被监控感。"></textarea>

    <label>DeepSeek API Key（仅用于本次本地进程，不写入磁盘）</label>
    <input id="key" type="password" placeholder="sk-...">

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
    </div>
    <div style="margin-top:14px;padding:10px;background:#fff7ed;border-radius:8px">
      当前安全范围：未指定真实目标仓库时，只在隔离工作区做原型，不修改现有产品。
    </div>
    <button id="executeBtn" onclick="startTeamExecution()" style="background:#7c3aed">启动团队执行</button>
    <span id="executeBadge" class="badge">等待草案确认</span>
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

async function analyzeGoal(){
  const goal=document.getElementById('goal').value.trim();
  const key=document.getElementById('key').value;

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
  const key=document.getElementById('key').value;
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
    body:JSON.stringify({draft_id:CURRENT_DRAFT_ID})
  });
  if(!r.ok){alert(r.error||'确认失败');return;}
  document.getElementById('draftBadge').textContent='已确认';
  document.getElementById('teamCard').style.display='block';
  document.getElementById('executeBadge').textContent='可启动';
  alert('任务草案已确认。现在可以启动动态团队执行。');
}

async function startTeamExecution(){
  if(!CURRENT_DRAFT_ID){alert('请先确认任务草案。');return;}
  const key=document.getElementById('key').value;
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

async function pollTeam(){
  if(!CURRENT_DRAFT_ID)return;
  const d=await api('/api/team/status?draft_id='+encodeURIComponent(CURRENT_DRAFT_ID));
  if(!d.ok)return;
  const s=d.state||{};
  document.getElementById('teamCard').style.display='block';
  document.getElementById('teamStatus').textContent=s.status||'准备中';
  document.getElementById('currentAgent').textContent=s.current_agent||'-';
  document.getElementById('teamCodex').textContent=s.codex_calls||0;
  document.getElementById('teamTokens').textContent=s.deepseek_tokens||0;
  document.getElementById('teamTimeline').textContent=JSON.stringify(s.timeline||[],null,2);

  const running=d.running;
  document.getElementById('executeBtn').disabled=!!running;
  document.getElementById('executeBadge').textContent=running?'执行中':(s.status||'已结束');
  if(running)setTimeout(pollTeam,2000);
}

async function startTask(){
  const task=document.getElementById('task').value;
  const key=document.getElementById('key').value;
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
                state=dict(meta.get("state") or {})
            if not state:
                state_path=ROOT/"orchestrator_v1"/"dynamic_runs"/draft_id/"state.json"
                if state_path.exists():
                    try:
                        state=json.loads(state_path.read_text(encoding="utf-8"))
                    except Exception:
                        state={}
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

        if self.path=="/api/analyze":
            goal=(data.get("goal") or "").strip()
            api_key=(data.get("api_key") or "").strip()

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
            api_key=(data.get("api_key") or "").strip()

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

        if self.path=="/api/team/start":
            draft_id=(data.get("draft_id") or "").strip()
            api_key=(data.get("api_key") or "").strip()

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
                try:
                    runner=DynamicTeamRun(ROOT,draft_id,draft,key)
                    state=runner.run()
                    with LOCK:
                        TEAM_RUNS[draft_id]={"running":False,"state":state}
                except Exception as exc:
                    with LOCK:
                        TEAM_RUNS[draft_id]={
                            "running":False,
                            "state":{
                                "status":"执行失败",
                                "current_agent":"",
                                "timeline":[{"agent":"系统","action":"执行失败","detail":str(exc)}]
                            }
                        }

            threading.Thread(target=worker,daemon=True).start()
            self._json({"ok":True,"draft_id":draft_id})
            return

        if self.path!="/api/start":
            self.send_error(404)
            return

        task_id=data.get("task_id")
        api_key=(data.get("api_key") or "").strip()

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

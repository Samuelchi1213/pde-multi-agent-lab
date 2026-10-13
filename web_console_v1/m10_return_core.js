"use strict";
/* M10 only synthetic fixtures. All history stays in browser memory.
   Role selection is a demo, NOT real authentication. */
(function(global){
const TYPES=["未见到","报告请假","其他待核实"];
const REPORTS=["已返校","未返校","留校"];
const REVIEWS=["确认当晚在校","确认当晚不在校","继续跟进"];
const copy=x=>JSON.parse(JSON.stringify(x));
function validDay(s){
  if(typeof s!=="string"||!/^\d{4}-\d{2}-\d{2}$/.test(s))return false;
  const d=new Date(s+"T12:00:00");
  return !Number.isNaN(d.valueOf())&&
    [d.getFullYear(),String(d.getMonth()+1).padStart(2,"0"),String(d.getDate()).padStart(2,"0")].join("-")===s;
}
function ensure(b,msg){if(!b)throw Error(msg)}
function demoStore(date){
  ensure(validDay(date),"演练日期无效");
  const classes=[
    {id:"C1",name:"会计1班",campus:"北校区",college:"经管学院"},
    {id:"C2",name:"电商2班",campus:"南校区",college:"经管学院"}
  ];
  const source=[
    ["A1","C1","示例甲","DEMO001","已返校",-1],
    ["A2","C1","示例乙","DEMO002","未返校",1],
    ["A3","C1","示例丙","DEMO003","留校",-1],
    ["A4","C1","示例丁","DEMO004","未返校",0],
    ["B1","C2","示例戊","DEMO005","未返校",1],
    ["B2","C2","示例己","DEMO006","已返校",0],
    ["B3","C2","示例庚","DEMO007","未返校",-2]
  ];
  const students=source.map(function(v){
    const d=new Date(date+"T12:00:00");d.setDate(d.getDate()+v[5]);
    return {id:v[0],classId:v[1],name:v[2],studentNo:v[3],plans:[
      {version:1,report:v[4],eta:[d.getFullYear(),String(d.getMonth()+1).padStart(2,"0"),String(d.getDate()).padStart(2,"0")].join("-"),recordedAt:"虚构初始数据"}
    ]};
  });
  return {classes,students,attendance:{},reviews:{},exportApproval:null,revision:0,
    trail:[{at:"虚构初始化",message:"7 名虚构学生，2 个班级；当天晚间考勤均未提交。"}]};
}
function stamp(store){return "演练操作 #"+store.revision}
function change(store,message){
 store.revision++;
 store.exportApproval=null;
 store.trail.push({at:stamp(store),message});
}
function student(store,id){const s=store.students.find(x=>x.id===id);ensure(s,"虚构学生不存在");return s}
function clazz(store,id){const c=store.classes.find(x=>x.id===id);ensure(c,"班级不存在");return c}
function plan(s){return s.plans[s.plans.length-1]}
function amendPlan(store,id,report,eta){
 const s=student(store,id);ensure(REPORTS.includes(report),"自报状态无效");ensure(validDay(eta),"日期无效");
 const p=plan(s);ensure(p.report!==report||p.eta!==eta,"计划没有变化，无需重复提交");
 change(store,s.name+" 更新自报计划，保留原版本");
 s.plans.push({version:p.version+1,report,eta,recordedAt:stamp(store)});
 return copy(s.plans);
}
function k(classId,date){return classId+"|"+date}
function submission(store,classId,date){
 clazz(store,classId);ensure(validDay(date),"考勤日期无效");
 const versions=store.attendance[k(classId,date)]||[];
 return versions[versions.length-1]||null;
}
function submissionStatus(store,classId,date){
 const sub=submission(store,classId,date);
 if(!sub)return "未提交";
 if(sub.version>1)return sub.exceptions.length?"已修订（有异常）":"已修订（无异常）";
 return sub.exceptions.length?"已提交有异常":"已提交无异常";
}
function normalizeExceptions(store,classId,items){
 ensure(Array.isArray(items)&&items.length<=100,"异常名单无效");
 const seen=new Set();
 return items.map(function(item){
   ensure(item&&typeof item==="object","异常明细无效");
   const s=student(store,item.studentId);
   ensure(s.classId===classId,"不能登记其他班级学生");
   ensure(!seen.has(s.id),"同一学生重复登记");seen.add(s.id);
   ensure(TYPES.includes(item.category),"异常类别无效");
   const note=String(item.note||"").trim();ensure(note.length<=300,"异常备注最长 300 字");
   return {studentId:s.id,category:item.category,note};
 }).sort((a,b)=>a.studentId.localeCompare(b.studentId));
}
function submitAttendance(store,classId,date,items,options){
 clazz(store,classId);ensure(validDay(date),"考勤日期无效");
 ensure(options&&options.explicitConfirmed===true,"必须明确确认已核对当晚情况");
 const exceptions=normalizeExceptions(store,classId,items);
 const old=submission(store,classId,date);
 const reason=String(options.correctionReason||"").trim();
 if(old){
   ensure(JSON.stringify(old.exceptions)!==JSON.stringify(exceptions),"无变化：不允许重复生成同日考勤记录");
   ensure(reason.length>=2&&reason.length<=200,"修订原考勤必须填写至少 2 字原因");
 }
 const version=(old?old.version:0)+1;
 change(store,clazz(store,classId).name+" "+date+" 提交第 "+version+" 版考勤（异常 "+exceptions.length+" 人）");
 const record={classId,date,version,actor:"虚构生活委员",reason,
    recordedAt:stamp(store),exceptions};
 const key=k(classId,date);
 if(!store.attendance[key])store.attendance[key]=[];
 store.attendance[key].push(record);
 return copy(record);
}
function attendanceHistory(store,classId,date){
 clazz(store,classId);ensure(validDay(date),"日期无效");
 return copy(store.attendance[k(classId,date)]||[]);
}
function reviewHistory(store,id,date){
 student(store,id);ensure(validDay(date),"日期无效");
 return copy(store.reviews[k(id,date)]||[]);
}
function latestReview(store,id,date){const h=reviewHistory(store,id,date);return h[h.length-1]||null}
function classify(store,id,date){
 const s=student(store,id);ensure(validDay(date),"日期无效");
 const p=plan(s),sub=submission(store,s.classId,date);
 const exception=sub?sub.exceptions.find(x=>x.studentId===id):null;
 let summary;
 if(!sub)summary="班级未提交";
 else if(exception){
   if(exception.category==="未见到"&&["已返校","留校"].includes(p.report))summary="自报与当晚异常冲突";
   else if(exception.category==="未见到"&&p.eta<=date)summary="到期未见待核实";
   else if(exception.category==="未见到")summary="当晚未见待核实";
   else if(exception.category==="报告请假")summary="请假待核实";
   else summary="其他异常待核实";
 }else summary=p.eta<=date?"到期待确认":"未报异常（非到校证明）";
 const review=latestReview(store,id,date);
 // Never carry a previous verification forward after the cited evening
 // attendance has been corrected. Preserve the old review in its audit history.
 const reviewStale=!!(review&&(!sub||review.basedOnAttendanceVersion!==sub.version));
 return {studentId:id,classId:s.classId,name:s.name,report:p.report,eta:p.eta,
    planVersion:p.version,date,submissionStatus:submissionStatus(store,s.classId,date),
    attendanceVersion:sub?sub.version:0,exception:exception?exception.category:"无单人异常记录",
    exceptionNote:exception?exception.note:"",summary,
    review:reviewStale?"需重新核实（考勤已修订）":review?review.result:"未人工核实",
    reviewNote:review?review.note:"",reviewStale};
}
function recordReview(store,id,date,result,note){
 const s=student(store,id);ensure(validDay(date),"日期无效");
 const sub=submission(store,s.classId,date);
 ensure(sub,"本班该晚没有提交，不能生成个人核实结论");
 ensure(REVIEWS.includes(result),"核实结论不合法");
 note=String(note||"").trim();ensure(note.length>=2&&note.length<=300,"核实依据或联系备注至少 2 字，至多 300 字");
 const key=k(id,date),versions=store.reviews[key]||[];
 const last=versions[versions.length-1];
 ensure(!last||last.result!==result||last.note!==note,"相同核实记录无需重复写入");
 change(store,"辅导员核实 "+s.name+"，原班委考勤未改动");
 const r={studentId:id,date,version:versions.length+1,result,note,
   basedOnAttendanceVersion:sub.version,recordedAt:stamp(store)};
 if(!store.reviews[key])store.reviews[key]=[];
 store.reviews[key].push(r);
 return copy(r);
}
function rowsForDate(store,date){
 ensure(validDay(date),"日期无效");
 return store.students.map(s=>classify(store,s.id,date));
}
function approveExport(store,date){
 ensure(validDay(date),"日期无效");
 change(store,"辅导员模拟批准 "+date+" 的只读 CSV 导出");
 store.exportApproval={date,revision:store.revision};
 return copy(store.exportApproval);
}
function csvQuote(value){
 let s=String(value??"");
 if(/^[\s\uFEFF]*[=+\-@]/.test(s))s="'"+s;
 return '"'+s.replace(/"/g,'""')+'"';
}
function exportData(store,date){
 ensure(validDay(date),"导出日期无效");
 ensure(store.exportApproval&&store.exportApproval.date===date&&store.exportApproval.revision===store.revision,
   "需先模拟辅导员审批；审批后数据变化则必须重新确认");
 const columns=["校区","学院","班级","姓名","模拟学号","核对日期","最新自报","预计返校日期",
   "自报版本","班级提交状态","考勤版本","单人异常","异常备注","系统提示","辅导员核实状态","核实备注"];
 const rows=rowsForDate(store,date).map(row=>{
   const cl=clazz(store,row.classId),s=student(store,row.studentId);
   return [cl.campus,cl.college,cl.name,s.name,s.studentNo,date,row.report,row.eta,
     row.planVersion,row.submissionStatus,row.attendanceVersion,row.exception,row.exceptionNote,
     row.summary,row.review,row.reviewNote];
 });
 return "\uFEFF"+[columns,...rows].map(r=>r.map(csvQuote).join(",")).join("\r\n");
}
global.M10Core={demoStore,validDay,student,clazz,plan,amendPlan,submission,submissionStatus,
  submitAttendance,attendanceHistory,reviewHistory,latestReview,recordReview,classify,
  rowsForDate,approveExport,exportData,csvQuote,TYPES,REPORTS,REVIEWS};
})(window);

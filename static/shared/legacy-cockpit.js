/* ---------------- 数据驾驶舱（第一+二部分） ---------------- */
const SRC_COLORS = {his:"#22d3ee", lis:"#fbbf24", pacs:"#a78bfa", emr:"#94a3b8", iot:"#fb7185"};
let CK=null, ORCHD=null;
function fmtK(v){ v=+v||0; return Math.abs(v)>=1000 ? (v/1000).toFixed(1)+"k" : String(Math.round(v)); }
function fitCanvas(cv){ const dpr=window.devicePixelRatio||1, w=cv.clientWidth, h=cv.clientHeight; if(!w) return null; cv.width=Math.round(w*dpr); cv.height=Math.round(h*dpr); const ctx=cv.getContext("2d"); ctx.setTransform(dpr,0,0,dpr,0,0); return {ctx,w,h}; }

function kpiCard(k,v,sub,color){ return `<div class="kpi" style="--kc:${color}"><div class="v">${esc(v)}</div><div class="k">${esc(k)}</div>${sub?`<div class="s">${esc(sub)}</div>`:""}</div>`; }
function renderKpis(){ if(!CK) return; const k=CK.kpis, L=CK.loop||{}; $("#ckKpis").innerHTML=[
  ["纳管数据源", `${k.sources_online}/${k.source_total}`, "全部在线 · 物理分散原位存储", "#34d399"],
  ["跨系统总记录", fmtK(k.total_rows), (k.rows_per_min!=null?`+${fmtK(k.rows_per_min)}/min`:"实时增长中"), "#22d3ee"],
  ["元数据事件累计", k.events_total, (k.ev_per_min!=null?`${Math.round(k.ev_per_min)}条/min · SSE推送`:"SSE推送中"), "#a78bfa"],
  ["语义图谱规模", `${k.graph_nodes}节点·${k.graph_edges}边`, "随流式数据持续生长", "#2dd4bf"],
  ["编排任务完成", L.tasks_done||0, (L.success_rate!=null?`成功率 ${(L.success_rate*100).toFixed(1)}% · P0>P1>P2调度`:"P0>P1>P2优先级调度"), "#fbbf24"],
  ["服务调用累计", CK.svc_calls_total||0, (CK.svc_avg_ms!=null?`平均 ${CK.svc_avg_ms} ms（含编排内部调用）`:"统一接口供给"), "#fb7185"],
  ["纳管患者", k.patients, "跨系统身份对齐覆盖", "#22d3ee"],
  ["就诊事件累计", k.visits, "HIS · ICD-10术语打标", "#94a3b8"]].map(([kk,vv,su,c])=>kpiCard(kk,vv,su,c)).join(""); }

function renderLoopStrip(){ if(!CK) return; const L=CK.loop||{}, k=CK.kpis;
  const steps=[
    ["感知 · 元数据事件", fmtK(k.events_total)+" 累计", (k.ev_per_min!=null?`${Math.round(k.ev_per_min)}条/min`:"4s周期扫描"), "#22d3ee"],
    ["规则匹配", "已触发 "+(L.triggered||0), "5类编排规则可启停", "#a78bfa"],
    ["任务调度", "队列深度 "+(L.queue_depth||0), "优先级 P0>P1>P2 串行执行", "#fbbf24"],
    ["智能执行", (L.tasks_done||0)+" 完成", L.success_rate!=null?`成功率 ${(L.success_rate*100).toFixed(1)}%`:"任务血缘全程可溯", "#34d399"],
    ["服务调用", String(L.svc_calls||CK.svc_calls_total||0), "含 SVC-FED-QUERY 联邦查询", "#fb7185"],
    ["反馈 · 元数据回写", (L.feedback_events||0)+" orchestrated_action", "执行结果进入事件流（闭环）", "#2dd4bf"]];
  $("#ckLoop").innerHTML=steps.map(([t,v,su,c],i)=>`${i?`<div class="loop-arrow">→</div>`:""}<div class="loop-step" style="--lc:${c}"><div class="t">${esc(t)}</div><div class="v">${esc(v||"-")}</div>${su?`<div class="s">${esc(su)}</div>`:""}</div>`).join(""); }


let CK_MAP_NODES=[], CK_MAP_SELECTED=0;
function mapPalette(){ const s=getComputedStyle(document.documentElement); return ["--cyan","--teal","--violet","--amber","--rose","--green"].map(k=>s.getPropertyValue(k).trim()); }
function drawDeptMap(){
  const cv=$("#ckMapCv"); if(!cv||!CK) return; const f=fitCanvas(cv); if(!f) return;
  const {ctx,w,h}=f, data=(CK.dept_distribution||[]).slice(0,8), colors=mapPalette();
  if(!data.length){ ctx.fillStyle="#718198";ctx.font="13px sans-serif";ctx.fillText("业务域数据聚合中…",w/2-65,h/2);return; }
  const cx=w*.49, cy=h*.5, orbit=Math.min(w*.35,h*.35), maxP=Math.max(...data.map(x=>x.patients),1), minP=Math.min(...data.map(x=>x.patients));
  ctx.clearRect(0,0,w,h); ctx.lineWidth=1;
  [0.34,0.58,0.82,1].forEach(r=>{ctx.strokeStyle="#dce8f5";ctx.beginPath();ctx.arc(cx,cy,orbit*r,0,Math.PI*2);ctx.stroke();});
  ctx.strokeStyle="#c6dbf2";ctx.setLineDash([3,6]);ctx.beginPath();ctx.moveTo(cx-orbit-25,cy);ctx.lineTo(cx+orbit+25,cy);ctx.moveTo(cx,cy-orbit-25);ctx.lineTo(cx,cy+orbit+25);ctx.stroke();ctx.setLineDash([]);
  CK_MAP_NODES=data.map((d,i)=>{ const angle=-Math.PI/2+i*(Math.PI*2/data.length), rr=orbit*(.78+(i%3)*.065); return {...d,x:cx+Math.cos(angle)*rr,y:cy+Math.sin(angle)*rr,r:15+(d.patients-minP)/Math.max(1,maxP-minP)*15,color:colors[i%colors.length]}; });
  CK_MAP_NODES.forEach((n,i)=>{ ctx.strokeStyle="rgba(99,145,201,.28)";ctx.lineWidth=1.2;ctx.beginPath();ctx.moveTo(cx,cy);ctx.lineTo(n.x,n.y);ctx.stroke();
    ctx.globalAlpha=.12;ctx.fillStyle=n.color;ctx.beginPath();ctx.arc(n.x,n.y,n.r+10,0,Math.PI*2);ctx.fill();ctx.globalAlpha=1;
    ctx.fillStyle=n.color;ctx.beginPath();ctx.arc(n.x,n.y,n.r,0,Math.PI*2);ctx.fill();
    if(i===CK_MAP_SELECTED){ctx.strokeStyle="#1d4ed8";ctx.lineWidth=2.5;ctx.beginPath();ctx.arc(n.x,n.y,n.r+5,0,Math.PI*2);ctx.stroke();}
    const points=Math.max(2,Math.min(10,Math.round(n.data_points/Math.max(1,maxP/2))));
    for(let j=0;j<points;j++){const a=j*6.283/points;ctx.globalAlpha=.58;ctx.fillStyle=n.color;ctx.beginPath();ctx.arc(n.x+Math.cos(a)*(n.r+6),n.y+Math.sin(a)*(n.r+6),2.2,0,Math.PI*2);ctx.fill();ctx.globalAlpha=1;}
    ctx.fillStyle="#385777";ctx.font="12px Microsoft YaHei, sans-serif";ctx.textAlign=n.x<cx?"right":n.x>cx?"left":"center";ctx.fillText(n.name,n.x+(n.x<cx?-n.r-10:n.x>cx?n.r+10:0),n.y+4);
  });
  ctx.textAlign="center";ctx.fillStyle="#fff";ctx.beginPath();ctx.arc(cx,cy,38,0,Math.PI*2);ctx.fill();ctx.strokeStyle="#a9c8ec";ctx.lineWidth=2;ctx.stroke();ctx.fillStyle="#1d4ed8";ctx.font="700 12px Microsoft YaHei, sans-serif";ctx.fillText("DATA",cx,cy-2);ctx.fillStyle="#718198";ctx.font="10px Microsoft YaHei, sans-serif";ctx.fillText("FABRIC",cx,cy+13);ctx.textAlign="left";
  renderMapList(); renderMapDetail();
  if(!cv.dataset.bound){cv.dataset.bound="1";cv.addEventListener("click",e=>{const rect=cv.getBoundingClientRect(),mx=(e.clientX-rect.left)/rect.width*cv.clientWidth,my=(e.clientY-rect.top)/rect.height*cv.clientHeight;let best=-1,bd=Infinity;CK_MAP_NODES.forEach((n,i)=>{const dd=(n.x-mx)**2+(n.y-my)**2;if(dd<bd){bd=dd;best=i;}});if(best>=0&&bd<55**2)selectMapNode(best);});}
}
function renderMapList(){ const el=$("#ckMapList"); if(!el) return; el.innerHTML=CK_MAP_NODES.map((n,i)=>`<button type="button" class="ck-map-row ${i===CK_MAP_SELECTED?"active":""}" onclick="selectMapNode(${i})"><span class="rank">${String(i+1).padStart(2,"0")}</span><span class="label"><b>${esc(n.name)}</b></span><span class="counts">${n.patients} / ${n.visits}</span></button>`).join(""); }
function renderMapDetail(){ const el=$("#ckMapDetail"), n=CK_MAP_NODES[CK_MAP_SELECTED]; if(!el||!n) return; el.innerHTML=`<b>${esc(n.name)}</b> · 业务域数据构成<br><span class="detail-num">${n.patients}</span>患者 <span class="detail-num">${n.visits}</span>次就诊<br><span style="color:#c77a08">${n.abnormal_items}</span>项异常检验 · <span style="color:#7357c8">${n.studies}</span>项影像检查`; }
window.selectMapNode=i=>{CK_MAP_SELECTED=Math.max(0,Math.min(i,Math.max(0,CK_MAP_NODES.length-1)));drawDeptMap();};

function barsHTML(items,color){ const max=Math.max(1,...items.map(i=>i.value)); return items.length? items.map(it=>`<div class="barrow"><div class="bl"><span>${esc(it.label)}</span><b style="color:#fff">${it.value}</b></div><div class="bt"><div class="bf" style="width:${Math.max(2,it.value/max*100)}%;background:${it.color||color}"></div></div></div>`).join("") : '<span style="color:var(--dim)">暂无数据</span>'; }
function renderEvBars(){ if(!CK) return; const ET_COLORS={data_update:"var(--cyan)",schema_change:"var(--amber)",quality_anomaly:"var(--rose)",semantic_link_discovered:"var(--violet)",catalog_built:"var(--teal)",service_invoked:"#64748b",orchestrated_action:"var(--green)"};
  const items=Object.entries(CK.ev_by_type||{}).map(([k,v])=>({label:k,value:v,color:ET_COLORS[k]||"#94a3b8"})).sort((a,b)=>b.value-a.value);
  $("#ckEvBars").innerHTML=barsHTML(items,"var(--cyan)"); }
function renderDiagBars(){ if(!CK) return; const items=(CK.top_diags||[]).map(d=>({label:d.name,value:d.n})); $("#ckDiagBars").innerHTML=barsHTML(items,"#a78bfa"); }

const ST_LABEL={queued:"排队",running:"执行中",done:"完成",failed:"失败"};
function taskItemHTML(t){ const seq=(t.trigger&&t.trigger.seq!=null)?String(t.trigger.seq):"-";
  return `<div class="task-item st-${esc(t.status)}"><span class="tt">${esc(t.ts)}</span> <span class="pri ${esc(t.priority)}">${esc(t.priority)} · ${ST_LABEL[t.status]||""}</span> <b>${esc(t.name)}</b><div class="d">${esc((t.summary&&t.summary!=="")?t.summary:"等待调度…")}</div><div class="d" style="margin-top:2px;font-size:11.5px;color:#64748b">${esc(t.tid||"-")}${(t.latency_ms!=null)?` · ${t.latency_ms} ms`:""} ← 触发事件#${esc(seq)} (${esc((t.trigger&&t.trigger.etype)||"")})</div></div>`; }
function renderCkTasks(){ if(!CK) return; const ts=CK.recent_tasks||[]; $("#ckTaskHint").textContent=`队列深度 ${ (CK.loop&&CK.loop.queue_depth)!=null?CK.loop.queue_depth:0 } · 最近完成任务`;
  $("#ckTasks").innerHTML=(ts.length?ts:[{tid:"-",status:"queued",priority:"P2",name:"等待首个编排任务…",summary:"",trigger:{seq:null,etype:"-"},ts:new Date().toTimeString().slice(0,8)}]).map(taskItemHTML).join(""); }

function renderCkSources(){ if(!CK) return; const el=$("#ckSrcList"); if(!el) return; const list=(CK.sources||[]).slice().sort((a,b)=>(b.rows||0)-(a.rows||0)); const max=Math.max(1,...list.map(s=>s.rows||0));
  el.innerHTML=list.length?list.map(s=>{const c=SRC_COLORS[s.id]||"#94a3b8";return `<div class="lcr-src-row"><span class="dotc" style="background:${c}"></span><span class="nm">${esc(s.name)}</span><span class="bar"><i style="width:${Math.max(2,(s.rows/max)*100)}%;background:${c}"></i></span><b class="val">${fmtK(s.rows)}</b></div>`}).join(""):'<span style="color:var(--dim)">无数据</span>';
  const hint=$("#ckSrcHint"); if(hint)hint.textContent=(CK.kpis&&CK.kpis.total_rows!=null)?("合计 "+fmtK(CK.kpis.total_rows)+" 行"):""; }

async function loadCockpit(){ try{ CK=await (await fetch("/api/cockpit")).json();
  renderKpis(); renderLoopStrip(); renderCkSources(); drawRowsChart(); drawDeptMap(); renderEvBars(); renderDiagBars(); renderCkTasks(); $("#ckLastSync").textContent=CK.ts||new Date().toTimeString().slice(0,8); }catch(e){} }

/* ---------------- 智能编排调度（第二部分） ---------------- */

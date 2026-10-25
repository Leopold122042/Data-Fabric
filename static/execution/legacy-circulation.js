function renderDfMetrics(){ const m=DF?.metrics||{}; $("#dfMetrics").innerHTML=[
  ["已登记服务",m.registered||0,"可发现服务单元", "#22d3ee"], ["已发布",m.published||0,"生命周期状态", "#34d399"],
  ["流通调用",m.calls||0,"跨主体计量", "#a78bfa"], ["价值积分",m.value_points||0,"性能 × 复用反馈", "#fbbf24"],
  ["平均响应",(m.avg_latency_ms??"-")+" ms","服务供给效率", "#fb7185"]].map(x=>kpiCard(x[0],x[1],x[2],x[3])).join(""); }
function renderDfServices(){ const ss=DF?.services||[]; $("#dfServices").innerHTML=`<table class="tbl"><tr><th>服务</th><th>场景/分级</th><th>生命周期</th><th>调用</th><th>复用/质量</th><th>动作</th></tr>${ss.map(s=>{const st=s.stats||{};return `<tr><td><b style="color:#fff">${esc(s.name)}</b><br><code style="color:var(--cyan);font-size:11px">${esc(s.id)}</code></td><td>${esc(s.scene||"-")}<br><span class="vbadge">${esc(s.class||"-")}</span></td><td><span class="flow-status">${esc(s.lifecycle||"-")}</span></td><td>${st.calls||0}<br><span style="color:var(--dim)">${st.avg_ms??"-"} ms</span></td><td>${s.reuse_score||0} / ${s.quality_score||0}</td><td><button class="ghost" style="padding:4px 8px" onclick='runGovernedSvc("${esc(s.id)}")'>调用</button></td></tr>`}).join("")}</table>`; }
function renderDfFlows(){ $("#dfFlows").innerHTML=(DF?.flows||[]).map(f=>`<div class="asset-row"><b>${esc(f.name)}</b> <span class="flow-status">${esc(f.status)}</span><div class="muted">${esc(f.scene)} · ${esc(f.mode)}<br>${esc(f.description)}</div><div class="mchips">${f.service_ids.map(x=>`<span class="mchip">${esc(x)}</span>`).join("<span style='color:var(--cyan)'> → </span>")}</div><button class="ghost" style="margin-top:7px" onclick='runFlow("${esc(f.id)}")'>▶ 运行组合流</button></div>`).join(""); }
function renderDfCirculation(){ const rows=DF?.recent_circulation||[]; $("#dfCirculation").innerHTML=rows.length?rows.map(x=>`<div class="ev et-data_update"><div class="t">${esc(x.ts)} · ${esc(x.actor)} · ${esc(x.latency_ms)} ms</div><b>${esc(x.service_id)} ${x.flow_id?`· ${esc(x.flow_id)}`:""}</b><div class="d">${esc(x.decision)} · ${x.masked?"已动态脱敏":"授权字段"} · 价值积分 ${x.value_points}</div></div>`).join(""):'<span style="color:var(--dim)">暂无流通记录——运行一个组合流开始演示</span>'; }
async function loadDataServices(){ try{ DF=await (await fetch("/api/data-services")).json(); renderDfMetrics(); renderDfServices(); renderDfFlows(); renderDfCirculation(); }catch(e){} }
/* ---- 联邦瀑布 & 实时流通（第三部分可视化，全部复用现有接口）---- */
var WF_SRC_COLOR = { HIS:"#22d3ee", LIS:"#fbbf24", PACS:"#a78bfa", EMR:"#94a3b8", IoT:"#fb7185" };
function wfColor(src){ var s=String(src||"").toLowerCase(); for(var k in WF_SRC_COLOR){ if(s.indexOf(String(k).toLowerCase())>=0) return WF_SRC_COLOR[k]; } return "#22d3ee"; }
window.renderWaterfall = function(wf, boxId){ var el=document.getElementById(boxId); if(!el) return;
  if(!wf||!Array.isArray(wf)||!wf.length){ el.style.display="none"; el.innerHTML=""; return; }
  var max=1e-6; for(var i=0;i<wf.length;i++){ if((wf[i].ms||0)>max) max=wf[i].ms||0; }
  var html='<h3 class="sec-title" style="margin-bottom:9px">联邦取数瀑布<span class="hint">各源原位计算 + 跨系统回传耗时（零拷贝）</span></h3>';
  for(var i=0;i<wf.length;i++){ var x=wf[i]; var total=x.ms||0; var pct=Math.max(2, total/max*100); var cpct=(total>0)?((x.compute_ms||0)/total*pct):0; var c=wfColor(x.source);
    html+='<div style="display:flex;align-items:center;gap:8px;margin-bottom:7px">'
      +'<span style="width:52px;text-align:right;font-weight:700;color:'+c+';font-size:12.5px">'+esc(x.source)+'</span>'
      +'<div style="flex:1;background:#0e1a2b;border-radius:6px;height:20px;overflow:hidden;display:flex">'
        +'<div title="原位计算 '+(x.compute_ms||0)+' ms" style="width:'+cpct.toFixed(1)+'%;height:100%;background:'+c+';opacity:.9"></div>'
        +'<div title="跨系统回传 '+(x.transfer_ms||0)+' ms" style="width:'+Math.max(0,pct-cpct).toFixed(1)+'%;height:100%;background:'+c+';opacity:.35"></div></div>'
      +'<span style="width:74px;font-size:12px;color:#cbd5e1">'+total+' ms</span></div>'; }
  el.style.display="block"; el.innerHTML=html; };
window.renderProvenance = function(prov, boxId){ var el=document.getElementById(boxId); if(!el) return;
  var s=(typeof prov==="string")?prov:((prov&&prov.provenance)||"");
  if(s){ el.style.display="block"; el.innerHTML='<span style="color:#22d3ee">血缘：</span>'+esc(s); } else { el.style.display="none"; el.innerHTML=""; } };
async function pollCirc(){ try{ var d=await (await fetch("/api/circulation/recent?limit=8")).json(); renderCircTicker(d);}catch(e){} }
function renderCircTicker(d){ var box=document.getElementById("dfTicker"); if(!box) return; var m=d.metrics||{}; var rows=(d.recent||[]);
  var head='<div style="display:flex;align-items:center;gap:10px;margin-bottom:9px">'
    +'<span style="width:9px;height:9px;border-radius:50%;background:#34d399;display:inline-block;box-shadow:0 0 8px #34d399"></span>'
    +'<b style="color:#fff">实时数据流通</b><span class="muted" id="dfTickerMeta">累计调用 '+(m.circ_calls||0)+' · 价值积分 '+ (m.value_points||0)+'</span></div>';
  var body = rows.length ? '' : '<span style="color:var(--dim)">等待首次自主流通…</span>';
  for(var i=0;i<rows.length && i<8;i++){ var x=rows[i];
    body += '<div class="ev et-data_update"><div class="t">'+esc(x.ts)+' · '+esc(x.actor)+'</div><b>'+esc(x.service_id)+(x.flow_id?' <span style="color:#22d3ee">· '+esc(x.flow_id)+'</span>':'')
      +'</b><div class="d">'+esc(x.decision||"-")+(x.masked?" · 已脱敏":"")+ ' · '+(x.latency_ms)+' ms · 价值积分 +'+(x.value_points||0)+'</div></div>'; }
  box.innerHTML=head+body; }
pollCirc(); setInterval(pollCirc, 2500);
window.registerDemoService=async()=>{ const body={id:$("#dfRegId").value.trim(),name:$("#dfRegName").value.trim(),class:$("#dfRegClass").value,scene:"科研分析",scope:"院内/科研",lifecycle:"published",tags:["可发现","可组合","脱敏"]}; const r=await (await fetch("/api/data-services/register",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)})).json(); $("#dfRegMsg").textContent=r.error||"登记成功，已进入服务目录"; loadDataServices(); };
window.runGovernedSvc=async sid=>{ const payload={actor:{id:"demo-analyst",role:"analyst",org:"院内研究中心"},purpose:"research",params:{patient_id:"P0042",group_by:"dept",top_n:5}}; const r=await (await fetch("/api/data-services/"+sid+"/invoke",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)})).json(); renderWaterfall(r.waterfall||[], "dfWaterfall"); renderProvenance((r.result&&r.result.provenance)||"", "dfProvenance"); $("#dfOut").style.display="block"; $("#dfOut").textContent=JSON.stringify(r,null,1); loadDataServices(); loadSecurity(); };
window.runFlow=async flowId=>{ const r=await (await fetch("/api/data-services/flows/"+flowId+"/run",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({actor:{id:"researcher-01",role:"researcher",org:"联合科研中心"},purpose:"research",cross_org:true,params:{patient_id:"P0315",top_n:5}})})).json(); (function(){var st=(r.stages||[]),wf=null,prov="";for(var i=0;i<st.length;i++){if(st[i].result&&st[i].result.waterfall&&!wf)wf=st[i].result.waterfall;if(st[i].result&&st[i].result.provenance)prov=st[i].result.provenance;}renderWaterfall(wf||[],"dfWaterfall");renderProvenance(prov||(r.flow?(r.flow.name+":"+(r.flow.service_ids||[]).join(" -> ")):""),"dfProvenance");})(); $("#dfOut").style.display="block"; $("#dfOut").textContent=JSON.stringify(r,null,1); loadDataServices(); loadSecurity(); };

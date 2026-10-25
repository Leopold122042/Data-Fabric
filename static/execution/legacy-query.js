let PATIENTS=[], SERVICES=[];
async function loadPatients(){ const d=await (await fetch("/api/patients?limit=400")).json(); PATIENTS=d.patients||[]; }
$("#pInput").addEventListener("input", e => {
  const q=e.target.value.trim(); if(q.length<1){ $("#pSearchList").style.display="none"; return;}
  const hits=PATIENTS.filter(p=>(p.name&&p.name.includes(q))||p.id.toLowerCase().includes(q.toLowerCase())).slice(0,12);
  const box=$("#pSearchList");
  box.innerHTML=hits.map(h=>`<div onclick='pickPatient("${h.id}")'><b>${esc(h.name)}</b> <span style="color:var(--dim)">${esc(h.id)} · ${esc(h.gender||"")} · ${esc(h.last_dept||"-")}</span></div>`).join("");
  box.style.display=hits.length?"block":"none";
});
$("#pInput").addEventListener("keydown", e => { if(e.key==="Enter"){ $("#pSearchList").style.display="none"; loadPatient360(); } });
window.pickPatient = id => { $("#pInput").value=id; $("#pSearchList").style.display="none"; loadPatient360(); };
function jumpToPatient(pid){ switchTab("query"); pickPatient(pid); }

async function loadPatient360(){
  const pid=$("#pInput").value.trim().toUpperCase(); if(!pid) return;
  $("#pStatus").textContent="联邦查询中…";
  try{
    const r=await (await fetch("/api/query/patient_360",{method:"POST",body:JSON.stringify({patient_id:pid})})).json();
    if(r.error){ $("#pStatus").textContent=r.error; return; }
    renderP360(r); $("#pStatus").textContent=`已完成 · 全链路 ${r.total_latency_ms} ms（零拷贝）`;
  }catch(e){ $("#pStatus").textContent="请求失败："+e.message; }
}

function sparkline(rows, key, color){
  const vals=(rows||[]).map(r=>r[key]).filter(v=>v!=null); if(!vals.length) return "";
  const w=420,h=56,pad=4, min=Math.min(...vals), max=Math.max(...vals)||1;
  const pts=vals.map((v,i)=>[pad+i*(w-2*pad)/Math.max(1,vals.length-1), h-pad-(max===min?(h-2*pad)/2:(v-min)*(h-2*pad)/(max-min))]);
  return `<svg width="${w}" height="${h}" style="background:#070d1b;border-radius:8px"><polyline fill="none" stroke="${color}" stroke-width="1.6" points="${pts.map(p=>p.join(",")).join(" ")}"/></svg>`;
}
function renderP360(r){
  const basic=(r.sections?.[0]?.rows||[])[0]||{};
  let html=`<div class="card phead"><b>${esc(basic.name||r.patient_id)}</b>
    <span>ID ${esc(r.patient_id)} · ${esc(basic.gender||"")} · 出生 ${esc(String(basic.birth_date||"").replace("-",""))}年</span>
    <span>参保 ${esc(basic.insurance_type||"-")}</span><span>证件(脱敏) ${esc(basic.id_card_masked||"-")}</span>`;
  if(r.semantic_tags?.length){ html+=`<div class="tagchips">${r.semantic_tags.map(t=>`<span class="dtag">${esc(t.code)} · ${esc(t.name)}</span>`).join("")}<span style="color:var(--dim);font-size:12px;align-self:center">↑ ICD-10 术语自动对齐（主动元数据打标）</span></div>`;}
  html+=`</div><div class="secs">`;
  for(const s of (r.sections||[])){
    if(s.title==="基本信息") continue;   // already rendered in header card
    let body="";
    if(s.title.startsWith("就诊")){
      const rows=(s.rows||[]).map(v=>`<tr><td>${esc(v.time)}</td><td>${esc(v.vtype)}·${esc(v.dept)}</td><td>${esc(v.doctor)}</td>
        <td style="color:#d8ccff">${esc(v.code||"")} ${esc(v.diag||"")}</td>
        <td>${(v.orders||[]).slice(0,6).map(o=>`<span class="mchip">${esc(o.otype)}:${esc(o.item)}</span>`).join(" ")}</td></tr>`).join("");
      body=`<table class="tbl"><tr><th>时间</th><th>类型/科室</th><th>医生</th><th>诊断(ICD-10)</th><th>医嘱（节选）</th></tr>${rows||"<tr><td colspan=5 style='color:var(--dim)'>无就诊记录</td></tr>"}</table>`; }
    else if(s.title==="检验结果"){
      const rows=(s.rows||[]).map(t=>`<div style="margin-bottom:9px"><b style="font-size:12.8px">${esc(t.test_no)}</b> <span style="color:var(--dim);font-size:12px">· ${esc(t.sample_type)} · ${esc(t.collect_time)}</span>
        <table class="tbl" style="margin-top:4px"><tr><th>项目</th><th>结果值</th><th>单位</th><th>参考区间</th></tr>${t.items.map(i=>`<tr><td>${esc(i.name)} ${i.flag?`<span class="flag-${i.flag}">${i.flag==="H"?"↑":"↓"}</span>`:""}</td><td style="color:#fff">${esc(i.value)}</td><td>${esc(i.unit||"-")}</td><td style="color:var(--dim)">${esc(i.ref||"-")}</td></tr>`).join("")}</table></div>`).join("");
      body=(s.rows?.length?rows:"<span style='color:var(--dim);font-size:12.5px'>暂无检验记录</span>") + ` <span class="vbadge">异常项 ${s.abnormal_items||0} 个（H/L 标记）</span>`; }
    else if(s.title==="影像检查"){
      body=(s.rows?.length) ? `<table class="tbl"><tr><th>DICOM UID</th><th>模态</th><th>部位</th><th>日期</th><th>文件数/大小</th><th>报告摘要</th></tr>${s.rows.map(x=>`<tr><td style="font-family:Consolas,monospace;font-size:11.5px">${esc(String(x.uid).slice(0,24))}…</td><td>${esc(x.modality)}</td><td>${esc(x.body_part)}</td><td>${esc(x.date)}</td><td>${x.files} / ${x.size_mb}MB</td><td style="color:var(--dim)">${esc(String(x.report||"").slice(0,46))}…</td></tr>`).join("")}</table>`
        : "<span style='color:var(--dim);font-size:12.5px'>暂无影像检查（DICOM 文件保持原位，不搬迁）</span>"; }
    else if(s.title.startsWith("生命体征")){ const rows=s.rows||[];
      body=rows.length?`<div style="display:flex;gap:14px;flex-wrap:wrap;align-items:center">
        <div>${sparkline(rows,"hr","#fb7185")}<div class="k" style="color:var(--dim);font-size:12px;margin-top:3px">心率 bpm（近${rows.length}点）</div></div>
        <div>${sparkline(rows,"spo2","#2dd4bf")}<div class="k" style="color:var(--dim);font-size:12px;margin-top:3px">血氧 %</div></div>
        <div class="kvgrid"><div class="kv"><div class="k">平均心率</div><div class="v">${s.stats?.avg_hr??"-"}</div></div>
        <div class="kv"><div class="k">峰值心率</div><div class="v" ${s.stats&&s.stats.max_hr>=125?'style="color:var(--rose)"':""}>${s.stats?.max_hr??"-"}${s.stats&&s.stats.max_hr>=125?" ⚠":""}</div></div>
        <div class="kv"><div class="k">最低血氧</div><div class="v">${s.stats?.min_spo2??"-"}</div></div></div></div>`
      :"<span style='color:var(--dim);font-size:12.5px'>该患者暂无接入监护（仅住院/急诊纳管）</span>"; }
    else if(s.title==="病历文书"){ const d=(s.rows||[])[0];
      body=d?`<div class="dossier">${["chief_complaint","present_illness_history","physical_exam"].filter(k=>d&&d[k]).map(k=>{const names={chief_complaint:"主诉",present_illness_history:"现病史",physical_exam:"查体"};return `<p><b class="kk">${names[k]}</b>${esc(d[k])}</p>`}).join("")}
        <p><b class="kk">初步诊断</b>${(d.preliminary_diagnosis||[]).map(x=>`<span class="dtag" style="margin-right:5px">${esc(x)}</span>`).join("")}</p>
        ${d.treatment_plan?`<p><b class="kk">诊疗计划</b>${esc(d.treatment_plan)}</p>`:""}</div>`:(s.note||"<span style='color:var(--dim);font-size:12.5px'>暂无病历文书登记</span>"); }
    html+=`<div class="card sec"><h3 class="sec-title">${esc(s.title)} <span class="vbadge">${esc(s.mode)}</span></h3>
      <div class="meta"><span>${esc(s.source_name)}</span><code style="font-size:11.5px;color:#7dd3fc;background:#0a1826;padding:2px 7px;border-radius:5px">${esc(s.physical||"")}</code>
      <span>取数耗时 ${s.latency_ms} ms</span></div>${body}</div>`;
  }
  html+="</div>";
  $("#p360Out").innerHTML=html;
}

/* ---------------- 诊断术语检索 ---------------- */
window.runDiagSearch = async () => {
  const q=$("#dInput").value.trim(); if(!q) return;
  const r=await (await fetch("/api/search?q="+encodeURIComponent(q))).json();
  $("#diagOut").innerHTML=`<div class="card" style="margin-bottom:12px"><h3 class="sec-title">跨源检索结果（HIS诊断 ⋈ LIS术语对齐）</h3>
    ${(r.diagnosis?.length)?`<table class="tbl"><tr><th>患者</th><th>ID</th><th>ICD-10</th><th>诊断</th><th>就诊次数</th><th>最近就诊</th></tr>${r.diagnosis.map(d=>`<tr style="cursor:pointer" onclick='pickPatient("${d.pid}")'><td>${esc(d.name)}</td><td>${esc(d.pid)}</td><td>${esc(d.code)}</td><td>${esc(d.diag)}</td><td>${d.count}</td><td style="color:var(--dim)">${esc(d.last)}</td></tr>`).join("")}</table>`:"<span style='color:var(--dim);font-size:12.5px'>未检索到匹配诊断（试试：高血压 / 糖尿病）</span>"}
    ${(r.lab_terms?.length)?`<div class="mchips" style="margin-top:9px">${r.lab_terms.map(t=>`<span class="mchip">LIS术语对齐：${esc(t.code)} ${esc(t.name)}（${esc(t.unit)}）</span>`).join("")}</div>`:""}
  </div>`;
};

/* ---------------- 服务层 ---------------- */
function renderSvcCards(d){
  $("#svcCards").innerHTML=(d.services||[]).map(s=>`<div class="card svc-card">
    <h4>${esc(s.name)}<span class="svc-id">${s.id}</span></h4>
    <div class="svc-desc">${esc(s.desc)}</div>
    ${(s.params||[]).map(p=>`<div class="prow"><span><code style="color:#7dd3fc">${esc(p.k)}</code>${p.req?" *":""} ${esc(p.type)}</span><span style="color:var(--dim)">示例：${JSON.stringify(p.demo??"")}</span></div>`).join("")||'<div class="prow"><span>无参数</span><span></span></div>'}
    <button class="ghost" style="margin-top:9px;width:100%" onclick='invokeSvc("${s.id}")'>▶ 调用示例（自动填演示参数）</button></div>`).join("");
}
window.invokeSvc = async sid => {
  const svc=(SERVICES||[]).find(s=>s.id===sid); if(!svc) return;
  const params={}; (svc.params||[]).forEach(p=>{ if(p.req&&p.demo!=null) params[p.k]=p.demo; else if(typeof p.demo==="string" && p.demo!=="") params[p.k]=p.demo; });
  if(sid==="SVC-VITALS-TREND"){ const d=await (await fetch("/api/patients?limit=1")).json(); params.patient_id=d.patients?.[0]?.id||""; }
  $("#svcMeta").textContent=`${sid} · ${new Date().toTimeString().slice(0,8)}（演示参数：${JSON.stringify(params)}）`;
  const r=await (await fetch("/api/services/"+sid+"/invoke",{method:"POST",body:JSON.stringify(params)})).json();
  $("#svcOut").style.display="block";
if(!r.error){ renderWaterfall(r.waterfall||[], "svcWaterfall"); renderProvenance((typeof r.result==="string")?r.result:((r.result&&r.result.provenance)||""), "svcProvenance"); } else { var w=$("#svcWaterfall"),p=$("#svcProvenance"); if(w)w.style.display="none"; if(p)p.style.display="none"; }
  $("#svcJson").textContent=r.error?`ERROR: ${r.error}`:JSON.stringify(r,null,1);
};

// 全局轮询：可见的重页面每4s刷新（与扫描周期对齐）
setInterval(()=>{ if($("#tab-cockpit").classList.contains("active")) loadCockpit();
                  if($("#tab-orch").classList.contains("active")) loadOrch();
                  if($("#tab-dataflow").classList.contains("active")) loadDataServices();
                  if($("#tab-security").classList.contains("active")) loadSecurity(); }, 4000);
window.addEventListener("resize",()=>{ if(CK && $("#tab-cockpit").classList.contains("active")){ drawRowsChart(); drawDeptMap(); } });

/* ---------------- 初始化 ---------------- */
(async function init(){
  renderFilters(); loadCockpit(); loadOrch(); loadGraph(); loadCatalog(); await loadPatients();
  await loadServices(false);          // 服务目录先行（arch chips + services tab）
  await loadDataServices(); loadSecurity();
  await loadOverview(); setInterval(loadOverview, 8000);
  $("#discExpl").innerHTML=`<b>主动元数据（Active Metadata）</b>：区别于静态目录，引擎每 <b>4秒</b>对五套源做<b>结构+内容双维指纹比对</b>——<br><br>
    · <span style="color:var(--cyan)">增量感知</span>：行数/最新记录变化 → data_update 事件（含可读摘要）；<br>
    · <span style="color:var(--amber)">Schema漂移</span>：新表/新列自动纳入目录并刷新语义映射；<br>
    · <span style="color:var(--rose)">质量异常</span>：流式体征越界（HR≥125 / SpO₂≤93.5）→ 实时告警事件；<br>
    · <span style="color:var(--violet)">身份对齐</span>：patient_id 在 ≥2 个源出现即建立语义链接并通知图谱。<br><br>
    本演示中，StreamingSim 持续向各源注入新患者（IoT→HIS→LIS→PACS/EMR 依次落库），观察事件流可看到“<b style="color:#fff">数据尚未集中、关联已经发生</b>”的编织效果。`;
  initStream(); resizeCanvas();
  window.addEventListener("resize", ()=>{ if($("#tab-graph").classList.contains("active")) resizeCanvas(); });

  // URL深链：#arch | #discovery | #graph | #query?pid=P0042&q=高血压&svc=SVC-PATIENT-360
  const raw=(location.hash||"").replace(/^#/,"");
  const [htab, qstr]=raw.split("?");
  if(["cockpit","arch","discovery","graph","query","orch","services","dataflow","security","external"].includes(htab)) switchTab(htab);
  const qp=new URLSearchParams(qstr||"");
  const pid=qp.get("pid"), dq=qp.get("q"), svcId=qp.get("svc");
  if(dq){ $("#dInput").value=dq; runDiagSearch(); }
  if(pid){ $("#pInput").value=String(pid).toUpperCase(); loadPatient360(); }
  if(svcId){ (SERVICES.length?Promise.resolve():new Promise(r=>setTimeout(r,1200))).then(()=>invokeSvc(svcId)); }
})();

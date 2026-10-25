let OV = null;
async function loadOverview(){
  try{ OV = await (await fetch("/api/overview")).json(); }catch(e){ return; }
  renderArchSources(); renderSrcCards(); renderStats(); renderArchServices();
}
function renderArchSources(){
  $("#archSources").innerHTML = OV.sources.map(s =>
    `<span class="chip"><b>${esc(s.name)}</b><span class="k">${esc(s.engine)} · ${s.tables}表/${(s.rows||0).toLocaleString()}行</span></span>`).join("");
  const totalRows = OV.sources.reduce((a,s)=>a+(s.rows||0),0);
  $("#archWeaveNote").textContent = `已纳管逻辑表 ${OV.sources.reduce((a,s)=>a+s.tables,0)} 张 · ${(totalRows/1000).toFixed(1)}k 行物理记录保持原位`;
}
function renderArchServices(){ $("#archServices").innerHTML = (SERVICES||[]).map(s=>`<span class="chip"><b>${esc(s.name)}</b><span class="k">${s.id}</span></span>`).join(""); }
async function loadServices(silent){
  const d=await (await fetch("/api/services")).json(); SERVICES=d.services||[];
  $("#archServices").innerHTML = SERVICES.map(s=>`<span class="chip"><b>${esc(s.name)}</b><span class="k">${s.id}</span></span>`).join("");
  if(!silent) renderSvcCards(d);
}
function renderStats(){
  const c = OV.counts;
  $("#archStats").innerHTML = [
    ["纳管数据源", OV.sources.length], ["跨系统总记录", (OV.sources.reduce((a,s)=>a+(s.rows||0),0)/1000).toFixed(1)+"k"],
    ["元数据事件累计", c.meta_events], ["语义实体类型", 6], ["患者/就诊规模", c.patients+"/"+c.visits]
  ].map(([k,v]) => `<div class="stat-card"><div class="v">${esc(v)}</div><div class="k">${esc(k)}</div></div>`).join("");
}
function renderSrcCards(){
  $("#srcCards").innerHTML = OV.sources.map(s => `
    <div class="card src-card">
      <div class="top"><b style="font-size:13.5px;color:#fff">${esc(s.name)}</b>
        <span class="badge" style="${s.status==="online"?"color:var(--green);border-color:rgba(52,211,153,.4)":""}">● ${s.status}</span></div>
      <div style="font-size:12px;color:var(--dim);line-height:1.7">${esc(s.desc)}<br><span class="k">${esc(s.location)}</span><br>
        逻辑表 <b style="color:#fff">${s.tables}</b> · 行 <b style="color:#fff">${(s.rows||0).toLocaleString()}</b> · 最近扫描 ${esc(s.last_scan)}</div>
    </div>`).join("");
}

/* ---------------- 元数据发现页 ---------------- */
const FILTERS = [["all","全部"],["data_update","数据增量"],["schema_change","结构变更"],["quality_anomaly","质量异常"],["semantic_link_discovered","语义链接"]];
let activeFilter = "all";
function renderFilters(){
  $("#evFilters").innerHTML = FILTERS.map(([k,t]) => `<button class="${activeFilter===k?"on":""}" onclick="setFilter('${k}')">${t}</button>`).join("");
}
window.setFilter = k => { activeFilter=k; renderFilters(); };
async function runScan(){
  const r = await (await fetch("/api/discovery/run",{method:"POST",body:JSON.stringify({})})).json();
  (r.events||[]).forEach(pushEvent);
}

/* ---------------- 语义图谱（Canvas 力导向） ---------------- */
let CATALOG = null;
async function loadCatalog(){
  CATALOG = await (await fetch("/api/catalog")).json();
  updateGraphLegend();
  $("#mapList").innerHTML = CATALOG.field_map.map(f => `
    <div class="map-item"><b style="color:${f.color}">${esc(f.label)}</b>
      <span style="color:var(--dim);font-size:12px"> ${esc(f.desc)}（${f.mappings.length} 个字段映射）</span>
      <div class="mchips">${f.mappings.map(m=>`<span class="mchip">${esc(m.source+"."+m.table+"."+m.column)} → ${esc(m.attr)}</span>`).join("")}</div></div>`).join("");
}

/* ---------------- Patient360 虚拟查询 ---------------- */

"use strict";
const $ = s => document.querySelector(s);
const $$ = s => Array.from(document.querySelectorAll(s));
const esc = x => String(x ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));

setInterval(() => { $("#clock").textContent = new Date().toTimeString().slice(0,8); }, 1000);
$$("#tabsNav button").forEach(b => b.onclick = () => switchTab(b.dataset.tab));
function switchTab(id){
  $$("#tabsNav button").forEach(b => b.classList.toggle("active", b.dataset.tab === id));
  $$(".tab").forEach(t => t.classList.toggle("active", t.id === "tab-" + id));
  document.body.classList.toggle("cockpit-mode", id === "cockpit");
  if (id === "graph") requestAnimationFrame(resizeCanvas);
  if (id === "cockpit") loadCockpit();
  if (id === "orch") loadOrch();
  if (id === "dataflow") loadDataServices();
  if (id === "security") loadSecurity();
  try{ history.replaceState(null, "", "#" + id); }catch(e){}
  window.dispatchEvent(new Event("platform-navigation"));
}
document.body.classList.add("cockpit-mode");

/* ---------------- SSE 事件流 ---------------- */
let lastSeq = 0;
const evCounts = {};
function initStream(){
  const es = new EventSource("/api/stream?after=0");
  es.onopen = () => $("#feedDot").classList.add("on");
  es.onerror = () => $("#feedDot").classList.remove("on");
  es.onmessage = m => {
    let ev; try{ ev = JSON.parse(m.data); }catch(e){ return; }
    if (ev.seq <= lastSeq) return;
    lastSeq = Math.max(lastSeq, ev.seq);
    pushEvent(ev);
  };
}
function eventBadge(etype){
  const map = {data_update:["数据增量","var(--cyan)"], schema_change:["结构变更","var(--amber)"],
               quality_anomaly:["质量异常","var(--rose)"], semantic_link_discovered:["语义链接","var(--violet)"],
               catalog_built:["目录纳管","var(--teal)"], service_invoked:["服务调用","#64748b"]};
  const [t,c] = map[etype] || [etype,"#94a3b8"];
  return `<span class="tag-ev" style="border:1px solid ${c};color:${c}">${t}</span>`;
}
function pushEvent(ev){
  evCounts[ev.etype] = (evCounts[ev.etype]||0)+1;
  const html = `<div class="ev et-${esc(ev.etype)}"><span class="t">${esc(ev.ts)}</span> ${eventBadge(ev.etype)} <b>${esc(ev.title)}</b><div class="d">${esc(ev.detail||"")}</div></div>`;
  for (const feedId of ["#archFeed","#evFeed","#ckFeed"]){
    const f = $(feedId); if (!f) continue;
    f.insertAdjacentHTML("afterbegin", html);
    while (f.children.length > 80) f.lastChild.remove();
  }
  renderEvTypeStats();
}
function renderEvTypeStats(){
  $("#evTypeStats").innerHTML = Object.entries(evCounts).map(([k,v]) =>
    `<div class="kv"><div class="k">${esc(k)}</div><div class="v" style="color:var(--cyan)">${v}</div></div>`).join("") || '<span style="color:var(--dim)">暂无事件</span>';
}

/* ---------------- 架构总览 & 源状态轮询 ---------------- */

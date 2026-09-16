/* Canvas views share the page's catalog and cockpit API data. */
let G = {nodes: [], edges: [], byId: {}};
const GRAPH_PATIENT_COLOR = '#0891b2';
const gState = {hover: null, selected: null, scale: 1, x: 0, y: 0, drag: null, moved: false};
const graphTransitions = new WeakMap();
let graphAnimationFrame = null;
const GRAPH_FADE_MS = 280;

function graphVisual(item, target, initial, now, animate) {
  let state = graphTransitions.get(item);
  if (!state) {
    state = {from:initial, target:initial, start:now};
    graphTransitions.set(item, state);
  }
  const progress = animate ? Math.min(1, (now-state.start)/GRAPH_FADE_MS) : 1;
  const ease = progress*progress*(3-2*progress);
  const value = state.target.map((v,i)=>state.from[i]+(v-state.from[i])*ease);
  if (target.some((v,i)=>v!==state.target[i])) {
    state.from = value; state.target = target; state.start = now;
  }
  const active = animate && target.some((v,i)=>Math.abs(v-value[i])>.0001);
  if (active && graphAnimationFrame === null) {
    graphAnimationFrame = requestAnimationFrame(()=>{
      graphAnimationFrame = null;
      drawGraphFrame();
    });
  }
  return animate ? value : target;
}

async function loadGraph() {
  const response = await fetch('/api/graph?patients=100');
  if (!response.ok) throw new Error('Graph request failed');
  const data = await response.json();
  G.nodes = data.nodes; G.edges = data.edges;
  G.byId = Object.fromEntries(G.nodes.map(n => [n.id, n]));
  gState.simulation?.stop();
  const random = d3.randomLcg(.427);
  G.nodes.forEach(n => { n.x = (random()-.5)*1000; n.y = (random()-.5)*700; });
  const links = G.edges.map(e => ({source:e.s, target:e.t}));
  gState.simulation = d3.forceSimulation(G.nodes)
    .force('link',d3.forceLink(links).id(n=>n.id).distance(95).strength(.045))
    .force('charge',d3.forceManyBody().strength(-65))
    .force('collision',d3.forceCollide(12))
    .force('x',d3.forceX(0).strength(.018))
    .force('y',d3.forceY(0).strength(.025))
    .stop();
  gState.simulation.tick(160);
  gState.simulation.on('tick',drawGraphFrame);
  gState.fitted = false;
  selectGraphNode(null);
  resizeCanvas(); fitGraph();
}

function graphFocus() {
  const id=gState.hover || gState.selected;
  if (!id) return null;
  const nodes=new Set([id]);
  G.edges.forEach(e=>{if(e.s===id || e.t===id){nodes.add(e.s);nodes.add(e.t);}});
  return {id,nodes};
}
function graphNodes() { const focus=graphFocus(); return G.nodes.filter(n=>!focus || focus.nodes.has(n.id)); }
function updateGraphLegend() {
  const counts = {};
  graphNodes().forEach(n => counts[n.type] = (counts[n.type] || 0) + 1);
  const types = CATALOG?.entity_types || {};
  const focus=graphFocus();
  document.querySelector('#gLegend').innerHTML = `<b>${focus ? esc(G.byId[focus.id].label) : '全部节点'}</b>` +
    Object.entries(types).map(([type, meta]) => `<div><i style="background:${type === 'patient' ? GRAPH_PATIENT_COLOR : meta.color}"></i>${esc(meta.label)} ×${counts[type] || 0}</div>`).join('');
}
function selectGraphNode(id) {
  document.querySelector('#gTip').style.display = 'none';
  gState.selected = id; gState.hover = null;
  updateGraphLegend(); drawGraphFrame();
}
function resizeCanvas() {
  const cv = document.querySelector('#gcv'), dpr = devicePixelRatio || 1;
  cv.width = Math.round(cv.clientWidth * dpr); cv.height = Math.round(cv.clientHeight * dpr);
  if (!gState.fitted && G.nodes.length) {fitGraph(); return;}
  drawGraphFrame();
}
function fitGraph() {
  const nodes = graphNodes(); if (!nodes.length) return;
  const cv = document.querySelector('#gcv');
  if (!cv.clientWidth || !cv.clientHeight) return;
  gState.fitted = true;
  const minX = Math.min(...nodes.map(n => n.x)) - 40, maxX = Math.max(...nodes.map(n => n.x)) + 95;
  const minY = Math.min(...nodes.map(n => n.y)) - 35, maxY = Math.max(...nodes.map(n => n.y)) + 35;
  const left = cv.clientWidth > 520 ? 160 : 20, top = cv.clientWidth > 520 ? 60 : 170;
  gState.scale = Math.max(.06, Math.min(2.5, (cv.clientWidth-left-20)/(maxX-minX), (cv.clientHeight-top-20)/(maxY-minY)));
  gState.x = left + (cv.clientWidth-left-20-(maxX-minX)*gState.scale)/2-minX*gState.scale;
  gState.y = top + (cv.clientHeight-top-20-(maxY-minY)*gState.scale)/2-minY*gState.scale;
  drawGraphFrame();
}
function zoomGraph(factor, x, y) {
  const cv = document.querySelector('#gcv'); x ??= cv.clientWidth/2; y ??= cv.clientHeight/2;
  const next = Math.max(.06, Math.min(5, gState.scale*factor)), ratio = next/gState.scale;
  gState.x = x-(x-gState.x)*ratio; gState.y = y-(y-gState.y)*ratio; gState.scale = next;
  drawGraphFrame();
}
function drawGraphFrame() {
  const cv = document.querySelector('#gcv'); if (!cv) return;
  const ctx = cv.getContext('2d'), dpr = devicePixelRatio || 1, z = gState.scale;
  const focus=graphFocus();
  const now=performance.now();
  const animate=!(typeof matchMedia==='function' && matchMedia('(prefers-reduced-motion: reduce)').matches);
  updateGraphLegend();
  ctx.setTransform(dpr,0,0,dpr,0,0); ctx.clearRect(0,0,cv.clientWidth,cv.clientHeight);
  ctx.save(); ctx.translate(gState.x,gState.y); ctx.scale(z,z);
  for (const e of G.edges) {
    const s = G.byId[e.s], t = G.byId[e.t]; if (!s || !t) continue;
    const hot = focus && (e.s === focus.id || e.t === focus.id);
    const [alpha, emphasis]=graphVisual(e,[focus && !hot ? .25 : 1,hot ? 1 : 0],[1,0],now,animate);
    ctx.globalAlpha = alpha;
    ctx.strokeStyle = `rgb(${[182,199,223].map((v,i)=>Math.round(v+([37,99,235][i]-v)*emphasis)).join(',')})`;
    ctx.lineWidth = (1+emphasis)/z;
    ctx.setLineDash(e.conf < .95 ? [4/z,3/z] : []);
    ctx.beginPath(); ctx.moveTo(s.x,s.y); ctx.lineTo(t.x,t.y); ctx.stroke();
  }
  ctx.setLineDash([]);
  const labelBoxes=[];
  const nodes=[...G.nodes].sort((a,b)=>Number(focus?.nodes.has(b.id))-Number(focus?.nodes.has(a.id)));
  for (const n of nodes) {
    const [alpha, ring, label]=graphVisual(n,
      [focus && !focus.nodes.has(n.id) ? .25 : 1,n.id===focus?.id ? 1 : 0,focus?.nodes.has(n.id) ? 1 : 0],
      [1,0,0],now,animate);
    ctx.globalAlpha = alpha;
    const meta = (CATALOG?.entity_types || {})[n.type] || {}, r = (n.type === 'patient' ? 7.5 : 4)/Math.sqrt(z);
    ctx.fillStyle = n.type === 'patient' ? GRAPH_PATIENT_COLOR : (meta.color || '#64748b'); ctx.beginPath(); ctx.arc(n.x,n.y,r,0,Math.PI*2); ctx.fill();
    if (ring > .001) {
      ctx.globalAlpha = alpha*ring;
      ctx.strokeStyle = '#2563eb'; ctx.lineWidth = 2/z; ctx.beginPath(); ctx.arc(n.x,n.y,r+4/z,0,Math.PI*2); ctx.stroke();
    }
    const patientLabel=n.type === 'patient' && z > .25;
    if (label > .001 || patientLabel) {
      ctx.globalAlpha = alpha*(patientLabel ? 1 : label);
      ctx.font = `${11/z}px "Microsoft YaHei",sans-serif`; ctx.fillStyle = '#334155';
      const width=ctx.measureText(n.label).width*z;
      let labelX=n.x+r+5/z;
      if(labelX*z+gState.x+width>cv.clientWidth-8)labelX=n.x-r-(width+5)/z;
      const box={x:labelX*z+gState.x,y:n.y*z+gState.y-9,w:width+5,h:16};
      const overlap=labelBoxes.some(b=>box.x<b.x+b.w && box.x+box.w>b.x && box.y<b.y+b.h && box.y+box.h>b.y);
      if(!overlap || n.id===gState.hover || n.id===gState.selected){ctx.fillText(n.label,labelX,n.y+4/z);labelBoxes.push(box);}
    }
  }
  ctx.restore();
  document.querySelector('#gZoom').textContent = `${Math.round(z*100)}%`;
}
function graphHit(ev) {
  const rect = document.querySelector('#gcv').getBoundingClientRect();
  const x = ev.clientX-rect.left, y = ev.clientY-rect.top;
  let best = null, distance = 13**2;
  for (const n of G.nodes) {
    const d = (n.x*gState.scale+gState.x-x)**2+(n.y*gState.scale+gState.y-y)**2;
    if (d < distance) { best = n; distance = d; }
  }
  return {node: best, x, y};
}
const graphCanvas = document.querySelector('#gcv');
graphCanvas.addEventListener('wheel', ev => {
  ev.preventDefault(); const {x,y} = graphHit(ev); zoomGraph(Math.exp(-ev.deltaY*.0015),x,y);
  document.querySelector('#gTip').style.display = 'none';
}, {passive:false});
graphCanvas.addEventListener('pointerdown', ev => {
  if (ev.button !== 0) return;
  const node = graphHit(ev).node;
  gState.drag = {x:ev.clientX,y:ev.clientY,panX:gState.x,panY:gState.y,node}; gState.moved = false;
  if (node) {node.fx=node.x; node.fy=node.y;}
  graphCanvas.setPointerCapture(ev.pointerId);
});
graphCanvas.addEventListener('pointermove', ev => {
  const tip = document.querySelector('#gTip');
  if (gState.drag) {
    const dx = ev.clientX-gState.drag.x, dy = ev.clientY-gState.drag.y;
    if (Math.hypot(dx,dy)>4) gState.moved = true;
    if (gState.moved) {
      if (gState.drag.node) {
        const rect=graphCanvas.getBoundingClientRect(), n=gState.drag.node;
        n.fx=(ev.clientX-rect.left-gState.x)/gState.scale; n.fy=(ev.clientY-rect.top-gState.y)/gState.scale;
        n.x=n.fx; n.y=n.fy; gState.simulation.alpha(.12).restart();
      } else {gState.x = gState.drag.panX+dx; gState.y = gState.drag.panY+dy;}
      tip.style.display='none'; drawGraphFrame();
    }
    return;
  }
  const {node,x,y} = graphHit(ev); gState.hover = node?.id || null;
  graphCanvas.style.cursor = node ? 'pointer' : 'grab';
  tip.style.display = node ? 'block' : 'none';
  if (node) { tip.textContent = node.label; tip.style.left = Math.max(0,Math.min(graphCanvas.clientWidth-200,x+14))+'px'; tip.style.top = Math.min(graphCanvas.clientHeight-45,y+14)+'px'; }
  drawGraphFrame();
});
function endGraphDrag(ev) {
  const node=gState.drag?.node;
  if (node) {node.fx=null; node.fy=null;}
  gState.drag = null; if (graphCanvas.hasPointerCapture(ev.pointerId)) graphCanvas.releasePointerCapture(ev.pointerId);
}
graphCanvas.addEventListener('pointerup', endGraphDrag);
graphCanvas.addEventListener('pointercancel', ev => {gState.moved=true; endGraphDrag(ev);});
graphCanvas.addEventListener('pointerleave', () => {gState.hover=null; document.querySelector('#gTip').style.display='none'; drawGraphFrame();});
graphCanvas.addEventListener('click', ev => {
  if (gState.moved || ev.detail > 1) return;
  const node = graphHit(ev).node;
  gState.simulation?.stop();
  selectGraphNode(node?.id || null);
});
graphCanvas.addEventListener('dblclick', ev => {
  if (gState.moved) return;
  const node=graphHit(ev).node;
  if (node?.type === 'patient') jumpToPatient(node.id.split(':')[1]);
});
document.querySelector('#gPlus').onclick = () => zoomGraph(1.25);
document.querySelector('#gMinus').onclick = () => zoomGraph(.8);
document.querySelector('#gFit').onclick = fitGraph;
document.querySelector('#gReset').onclick = () => {selectGraphNode(null); fitGraph();};

let trendAnchor = null;
function trendHistory(history, origin=null) {
  if (!history.length) return null;
  const first=origin || history[0];
  if (!trendAnchor || (origin && first.t!==trendAnchor.t) || first.t<trendAnchor.t) trendAnchor=structuredClone(first);
  const order=['his','lis','pacs','emr','iot'];
  // Independent deterministic batches create uneven growth without inventing live samples.
  const profiles=order.map((_,j)=>{
    let seed=90210+j*127;
    const random=()=>{seed=(Math.imul(seed,1664525)+1013904223)>>>0; return seed/4294967296;};
    const weights=Array.from({length:96},(_,i)=>.08+Math.pow(random(),5)*15+
      ((Math.floor(i/8)+j)%4===0 ? random()*4 : 0));
    const total=weights.reduce((s,v)=>s+v,0);
    let sum=0;
    return [0,...weights.map(v=>(sum+=v)/total)];
  });
  const simulated=Array.from({length:97},(_,i)=>{
    const by_source={};
    order.forEach((sid,j)=>by_source[sid]=Math.round((trendAnchor.by_source[sid]||0)*(.22+.78*profiles[j][i])));
    return {t:trendAnchor.t-86400+i*900,by_source,simulated:true};
  });
  return {points:[...simulated,...history.filter(p=>p.t>trendAnchor.t)],boundary:trendAnchor.t,anchor:trendAnchor};
}
function trendTimestamp(t, anchor) {
  let epoch=anchor.epoch;
  // Compatibility with a server that has not yet reloaded the timestamp API.
  if (!Number.isFinite(epoch)) {
    const date=new Date(), parts=(anchor.wall || '00:00:00').split(':').map(Number);
    date.setHours(parts[0],parts[1],parts[2] || 0,0);
    if(date.getTime()>Date.now())date.setDate(date.getDate()-1);
    epoch=date.getTime()/1000;
  }
  return new Date((epoch+t-anchor.t)*1000);
}
function trendTimeLabel(t,anchor,withDate=false) {
  const d=trendTimestamp(t,anchor),pad=n=>String(n).padStart(2,'0');
  return (withDate ? pad(d.getMonth()+1)+'/'+pad(d.getDate())+' ' : '')+pad(d.getHours())+':'+pad(d.getMinutes());
}
function trendLogDomain(points) {
  const values=points.flatMap(p=>Object.values(p.by_source)).filter(v=>v>0 && Number.isFinite(v));
  const lo=values.length ? Math.floor(Math.log10(Math.min(...values))) : 0;
  const hi=values.length ? Math.max(lo+1,Math.ceil(Math.log10(Math.max(...values)))) : 1;
  return {lo,hi};
}
const trendColors=['#2563eb','#ca8a04','#8757c5','#099d8c','#e05275'];
const trendSources=['his','lis','pacs','emr','iot'];
let trendPlot=null;
function drawRowsChart() {
  const cv=document.querySelector('#ckRowsCv'); if (!cv || !CK) return;
  const f=fitCanvas(cv); if (!f) return;
  const {ctx,w,h}=f; ctx.clearRect(0,0,w,h);
  const history=trendHistory(CK.history||[],CK.history_origin); if (!history) return;
  const {points,boundary,anchor}=history, start=points[0].t, end=Math.max(boundary+1,points.at(-1).t);
  const {lo,hi}=trendLogDomain(points);
  const left=42,right=14,top=w<480 ? 72 : 54,bottom=32;
  const X=t=>left+(w-left-right)*(t-start)/(end-start);
  const Y=v=>h-bottom-(h-top-bottom)*(Math.log10(v)-lo)/(hi-lo);
  let lx=left,ly=13;
  ctx.font='11px sans-serif';
  trendSources.forEach((sid,j)=>{
    const text=sid.toUpperCase()+' '+fmtK(points.at(-1).by_source[sid]||0);
    const width=ctx.measureText(text).width+28;
    if(lx+width>w-right){lx=left;ly+=18;}
    ctx.fillStyle=trendColors[j];ctx.fillRect(lx,ly-4,11,3);ctx.fillText(text,lx+16,ly);lx+=width;
  });
  ctx.fillStyle='#7b8ba1';ctx.fillText('记录数（行）· 对数刻度',left,top-10);
  ctx.fillStyle='#eef7f6';ctx.fillRect(X(boundary),top,w-right-X(boundary),h-top-bottom);
  const superscript=n=>String(n).split('').map(c=>'⁰¹²³⁴⁵⁶⁷⁸⁹'[Number(c)]).join('');
  for(let tick=lo;tick<=hi;tick++){
    const value=10**tick,y=Y(value);ctx.strokeStyle='#e6edf5';ctx.lineWidth=1;
    ctx.beginPath();ctx.moveTo(left,y);ctx.lineTo(w-right,y);ctx.stroke();
    ctx.fillStyle='#7b8ba1';ctx.textAlign='right';ctx.fillText('10'+superscript(tick),left-9,y+4);
  }
  ctx.textAlign='left';
  trendSources.forEach((sid,j)=>{
    ctx.strokeStyle=trendColors[j];ctx.lineWidth=1.8;
    for(const simulated of [true,false]){
      const part=points.filter(p=>simulated ? p.t<=boundary : p.t>=boundary);
      ctx.setLineDash(simulated ? [4,2] : []);ctx.beginPath();
      let connected=false;
      part.forEach(p=>{const v=p.by_source[sid]||0;if(v<=0){connected=false;return;}if(connected)ctx.lineTo(X(p.t),Y(v));else ctx.moveTo(X(p.t),Y(v));connected=true;});ctx.stroke();
    }
    ctx.setLineDash([]);ctx.fillStyle=trendColors[j];const last=points.at(-1).by_source[sid]||0;
    if(last>0){ctx.beginPath();ctx.arc(X(points.at(-1).t),Y(last),2.5,0,Math.PI*2);ctx.fill();}
  });
  ctx.strokeStyle='#94a3b8';ctx.setLineDash([3,4]);ctx.beginPath();ctx.moveTo(X(boundary),top);ctx.lineTo(X(boundary),h-bottom);ctx.stroke();ctx.setLineDash([]);
  ctx.fillStyle='#7b8ba1';
  const ticks=w<480 ? [24,12] : [24,18,12,6];
  for(const hours of ticks){
    ctx.textAlign=hours===24?'left':'center';ctx.fillText(trendTimeLabel(boundary-hours*3600,anchor),X(boundary-hours*3600),h-10);
  }
  ctx.textAlign='right';ctx.fillText(trendTimeLabel(end,anchor),w-right,h-10);ctx.textAlign='left';
  const originLabel=CK.history_origin ? '启动' : '采样起点';
  document.querySelector('#ckRowsHint').textContent='24h 模拟历史 · '+originLabel+' '+trendTimeLabel(boundary,anchor,true);
  trendPlot={points,boundary,anchor,start,end,left,right,w,h,X};
}
const trendCanvas=document.querySelector('#ckRowsCv');
const trendTip=document.createElement('div');
trendTip.className='gtip';trendTip.style.zIndex='6';
trendCanvas.parentElement.style.position='relative';trendCanvas.parentElement.appendChild(trendTip);
trendCanvas.addEventListener('pointermove',ev=>{
  if(!trendPlot)return;
  const rect=trendCanvas.getBoundingClientRect(),{points,start,end,left,right,w,boundary,anchor}=trendPlot;
  const x=ev.clientX-rect.left,t=start+(x-left)/(w-left-right)*(end-start);
  const p=points.reduce((best,item)=>Math.abs(item.t-t)<Math.abs(best.t-t)?item:best,points[0]);
  trendTip.innerHTML='<b>'+(p.t<boundary ? '模拟 · ' : '运行采样 · ')+trendTimeLabel(p.t,anchor,true)+'</b><br>'+
    trendSources.map((sid,j)=>'<span style="color:'+trendColors[j]+'">'+sid.toUpperCase()+'</span> '+(p.by_source[sid]||0).toLocaleString()+' 行').join('<br>');
  trendTip.style.display='block';
  trendTip.style.left=Math.max(0,Math.min(trendCanvas.parentElement.clientWidth-210,x+15))+'px';
  trendTip.style.top='50px';
});
trendCanvas.addEventListener('pointerleave',()=>trendTip.style.display='none');

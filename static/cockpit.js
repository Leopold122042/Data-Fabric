(function () {
  'use strict';
  const M = window.MedFabricCockpit, $ = s => document.querySelector(s), $$ = s => [...document.querySelectorAll(s)];
  const fmt = M.format, esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c]);
  const state = {data: {}, errors: {}, loadedAt: {}, events: [], department: null, deptMode: 'visits', trendMode: 'total', scale: 'linear', simulated: true, paused: false, filter: 'all', eventLimit: 8, sample: null};
  const sourceNames = {his: 'HIS', lis: 'LIS', pacs: 'PACS', emr: 'EMR', iot: 'IoT'};
  const sourceColors = {his: '--mf-source-his', lis: '--mf-source-lis', pacs: '--mf-source-pacs', emr: '--mf-source-emr', iot: '--mf-source-iot', total: '--mf-blue'};
  let busy = false, stream = null, streamConnected = false, retryStreamAt = 0, chartPoints = [], chartPinned = false, restoring = true;
  const storageKey = 'medfabric:cockpit-v1';
  let restoreY = 0;
  try {
    const saved = JSON.parse(sessionStorage.getItem(storageKey) || '{}');
    if (['visits','patients'].includes(saved.deptMode)) state.deptMode = saved.deptMode;
    if (['total','sources'].includes(saved.trendMode)) state.trendMode = saved.trendMode;
    if (['linear','log'].includes(saved.scale)) state.scale = saved.scale;
    if (typeof saved.simulated === 'boolean') state.simulated = saved.simulated;
    if (typeof saved.department === 'string') state.department = saved.department;
    if (typeof saved.filter === 'string') state.filter = saved.filter;
    if (typeof saved.paused === 'boolean') state.paused = saved.paused;
    restoreY = Math.max(0, Number(saved.scrollY) || 0);
  } catch (_) { /* Storage can be unavailable without blocking the dashboard. */ }
  function save() {
    try { sessionStorage.setItem(storageKey, JSON.stringify({department:state.department, deptMode:state.deptMode, trendMode:state.trendMode, scale:state.scale, simulated:state.simulated, paused:state.paused, filter:state.filter, scrollY:window.scrollY})); } catch (_) {}
  }
  window.addEventListener('pagehide', save);
  document.addEventListener('click', e => { if (e.target.closest('a')) save(); });
  function html(selector, content) {
    const node = $(selector);
    if (node._content === content) return;
    const focus = node.contains(document.activeElement) ? document.activeElement : null;
    const id = focus?.dataset?.focus;
    node.innerHTML = content; node._content = content;
    if (id) [...node.querySelectorAll('[data-focus]')].find(el => el.dataset.focus === id)?.focus({preventScroll:true});
  }
  function set(selector, text) { $(selector).textContent = text; }
  function clock(date) { return date?.toLocaleTimeString('zh-CN', {hour12:false}) || '—'; }
  function badge(text, kind = 'muted') { return `<span class="mf-badge is-${kind}">${esc(text)}</span>`; }
  function detail(label, value) { return `<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`; }
  function openDialog(title, content) { set('#detail-title', title); $('#detail-body').innerHTML = content; $('#detail-dialog').showModal(); }
  $('#close-dialog').addEventListener('click', () => $('#detail-dialog').close());
  $('#detail-dialog').addEventListener('click', e => { if (e.target === $('#detail-dialog')) { const r=e.target.getBoundingClientRect(); if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)e.target.close(); } });
  function syncControls() {
    $$('[data-dept-mode]').forEach(b => b.setAttribute('aria-pressed', b.dataset.deptMode === state.deptMode));
    $$('[data-trend]').forEach(b => b.setAttribute('aria-pressed', b.dataset.trend === state.trendMode));
    $('#simulated-history').checked = state.simulated; $('#trend-scale').value = state.scale;
    $('#pause-refresh').textContent = state.paused ? '恢复更新' : '暂停更新';
    $('#pause-refresh').setAttribute('aria-pressed', String(state.paused));
  }
  function renderHealth() {
    const failed = Object.entries(state.errors).filter(([,v]) => v).map(([k]) => ({cockpit:'驾驶舱', overview:'来源状态', security:'风险统计', events:'事件记录', orchestrator:'任务统计'})[k]);
    const el = $('#refresh-state');
    el.className = 'mf-badge ' + (failed.length ? 'is-warning' : 'is-muted');
    el.textContent = failed.length ? '部分数据更新失败' : state.paused ? '已暂停更新' : '每4秒更新';
    set('#sync-time', state.loadedAt.cockpit ? '驾驶舱更新于 ' + clock(state.loadedAt.cockpit) : '尚未取得驾驶舱快照');
    $('#load-alert').hidden = !failed.length;
    set('#load-alert', failed.length ? `${failed.join('、')}未更新；已获取的旧值暂时保留，未获取的值显示“—”。可点击刷新重试。` : '');
    set('#source-sync', `最近扫描状态，不代表持续连接健康 · ${state.loadedAt.overview ? '来源状态更新于 ' + clock(state.loadedAt.overview) : '尚未取得来源状态'}${state.errors.overview ? ' · 数据未更新' : ''}`);
    set('#risk-scope', `留存风险最多200条 · ${state.loadedAt.security ? clock(state.loadedAt.security) : '待读取'}${state.errors.security ? ' · 未更新' : ''}`);
    set('#event-stream-status', state.paused ? '更新已暂停 · 当前页面最多保留80条' : streamConnected ? '事件流已连接 · 按编号去重 · 最多保留80条' : '事件流暂未连接 · 定期读取最近事件');
    if(state.errors.cockpit&&!state.data.cockpit){['#department-bars','#diagnosis-bars','#event-bars','#task-list'].forEach(id=>html(id,'<p class="mf-empty">暂未取得数据，请刷新重试。</p>'));}
  }
  function renderMetrics() {
    const ck = state.data.cockpit, ov = state.data.overview, sec = state.data.security;
    set('#metric-patients', fmt(ck?.kpis?.patients)); set('#metric-visits', fmt(ck?.kpis?.visits));
    set('#metric-risks', fmt(sec?.metrics?.open_risks));
    set('#metric-sources', ov?.sources ? `${ov.sources.filter(s=>s.status==='online').length} / ${ov.sources.length}` : '—');
    if (!ck) return;
    const k=ck.kpis||{}, L=ck.loop||{};
    const rate = M.number(k.rows_per_min);
    const stats = [
      ['跨系统记录',fmt(k.total_rows),rate===null?'等待增长率采样':`${rate>=0?'+':''}${fmt(rate,1)} 条/分钟`,'rows'],
      ['元数据事件',fmt(k.events_total),`当前留存窗口 · ${M.number(k.ev_per_min)===null?'速率待采样':fmt(k.ev_per_min,1)+' 条/分钟'}`,'events'],
      ['样本图谱',`${fmt(k.graph_nodes)} 节点`,`${fmt(k.graph_edges)} 条边 · 最多100位患者`,'graph'],
      ['服务调用',fmt(ck.svc_calls_total),`旧调用统计 · 平均 ${fmt(ck.svc_avg_ms,2)} ms`,'calls']
    ];
    html('#secondary-metrics', stats.map(([name,value,note,key])=>`<button class="mf-stat" data-metric="${key}" data-focus="metric-${key}"><span class="mf-stat-label">${name}</span><span class="mf-stat-value">${value}</span><span class="mf-stat-meta">${note}</span></button>`).join(''));
    const stages=[['感知',fmt(k.events_total),'窗口内事件','discovery'],['规则匹配',fmt(L.triggered),'累计触发','orch'],['任务调度',fmt(L.queue_depth),'当前队列','orch'],['自动执行',fmt(L.tasks_done),'终态成功率 '+M.percentage(M.terminalRate(state.data.orchestrator?.counters)),'orch'],['服务调用',fmt(L.svc_calls),'含内部调用','services'],['执行反馈',fmt(L.feedback_events),'窗口内反馈事件','discovery']];
    html('#execution-flow',stages.map(([name,value,note,target],i)=>`<a class="mf-flow-step" href="/#${target}"><span class="mf-small mf-muted">0${i+1} · ${name}</span><strong class="mf-flow-value">${value}</strong><span class="mf-small mf-muted">${note}</span></a>`).join(''));
  }
  function renderDepartments() {
    const rows = (state.data.cockpit?.dept_distribution || []).slice().sort((a,b)=>(b[state.deptMode]||0)-(a[state.deptMode]||0)||a.name.localeCompare(b.name,'zh-CN'));
    const selected = M.chooseDepartment(rows, state.department); state.department = selected?.name || null;
    const max = Math.max(1,...rows.map(r=>r[state.deptMode]||0));
    html('#department-bars', rows.length ? rows.map((row,i)=>`<button class="mf-bar-row mf-dept-row${row.name===state.department?' is-selected':''}" data-department="${esc(row.name)}" data-focus="dept-${esc(row.name)}" aria-pressed="${row.name===state.department}"><span class="mf-bar-name">${esc(row.name)}</span><span class="mf-bar-track"><span class="mf-bar-fill" style="width:${Math.max(0,row[state.deptMode]/max*100)}%"></span></span><span class="mf-bar-value">${fmt(row[state.deptMode])}</span></button>`).join('') : '<p class="mf-empty">暂无科室记录</p>');
    html('#department-detail', selected ? `<b class="mf-detail-heading">${esc(selected.name)}</b><dl class="mf-definition-grid">${detail('纳管患者',fmt(selected.patients)+' 人')}${detail('就诊记录',fmt(selected.visits)+' 次')}${detail('关联异常检验',fmt(selected.abnormal_items)+' 项')}${detail('关联影像',fmt(selected.studies)+' 项')}</dl><p class="mf-small mf-muted">原数据点计数 ${fmt(selected.data_points)} = 就诊 + 关联异常检验 + 关联影像</p>` : '');
  }
  function renderSources() {
    const ck=state.data.cockpit, ov=state.data.overview;
    const rows=ck?.sources||ov?.sources;
    if (!rows) {if(state.errors.overview||state.errors.cockpit)html('#source-rows','<tr><td colspan="6" class="mf-empty">来源读取失败，请刷新重试。</td></tr>');return;}
    html('#source-rows', rows.length?rows.map(s=>{
      const meta=ov?.sources?.find(x=>x.id===s.id), status=meta?.status;
      const [label,kind]=status==='online'?['扫描成功','success']:status==='error'?['扫描失败','danger']:status==='pending'?['尚未扫描','muted']:['状态未知','muted'];
      const name=s.name?.replace(/^(HIS|LIS|PACS|EMR|IoT)\s*/i,'')||s.name;
      return `<tr><td><b>${esc(sourceNames[s.id]||s.id)}</b><span class="mf-cell-sub">${esc(name)}</span></td><td class="mf-number">${fmt(s.rows)}</td><td class="mf-number">${fmt(meta?.tables)}</td><td>${esc(meta?.last_scan||'—')}</td><td>${badge(label,kind)}</td><td><button class="mf-link" data-source="${esc(s.id)}" data-focus="src-${esc(s.id)}">详情</button></td></tr>`;
    }).join(''):'<tr><td colspan="6" class="mf-empty">暂无纳管来源</td></tr>');
    set('#source-total',`当前驾驶舱合计 ${fmt(ck?.kpis?.total_rows)} 条跨系统记录 · 不同粒度记录与文书合计，不等于患者数。`);
  }
  function barList(rows, type) {
    const max = Math.max(1,...rows.map(r=>Number(r.value)||0));
    return rows.map(r=>`<${type==='diagnosis'?'a':'button'} ${type==='diagnosis'?`href="/#query?q=${encodeURIComponent(r.label)}"`:`data-event-type="${esc(r.type)}"`} class="mf-bar-row" data-focus="bar-${esc(r.label)}"><span class="mf-bar-name">${esc(r.label)}</span><span class="mf-bar-track"><span class="mf-bar-fill" style="width:${Math.max(0,r.value/max*100)}%"></span></span><span class="mf-bar-value">${fmt(r.value)}</span></${type==='diagnosis'?'a':'button'}>`).join('');
  }
  function renderDistributions() {
    const ck=state.data.cockpit;if(!ck)return;
    html('#diagnosis-bars',ck.top_diags?.length?barList(ck.top_diags.map(d=>({label:d.name,value:d.n})),'diagnosis'):'<p class="mf-empty">暂无非空诊断记录</p>');
    const events=Object.entries(ck.ev_by_type||{}).map(([type,value])=>({type,value,label:M.eventName(type)})).sort((a,b)=>b.value-a.value);
    html('#event-bars',events.length?barList(events,'event'):'<p class="mf-empty">当前窗口暂无事件</p>');
  }
  function renderTasks() {
    const tasks=state.data.cockpit?.recent_tasks;if(!tasks)return;
    html('#task-list',tasks.length?tasks.map((t,i)=>`<div class="mf-list-row"><div class="mf-list-main"><div class="mf-list-title">${badge(M.taskState(t.status),t.status==='failed'?'danger':t.status==='done'?'success':'muted')} <b>${esc(t.name)}</b></div><p class="mf-list-meta">${esc(t.ts)} · ${esc(t.priority||'未分级')} · ${esc(t.tid)} · 耗时 ${fmt(t.latency_ms,2)} ms</p><p>${esc(t.summary||'等待执行结果')}</p><p class="mf-list-meta">触发事件 #${esc(t.trigger?.seq??'—')} · ${esc(M.eventName(t.trigger?.etype))}</p></div><button class="mf-link" data-task="${esc(t.tid)}" data-focus="task-${esc(t.tid)}">详情</button></div>`).join(''):'<p class="mf-empty">暂无执行任务。元数据事件命中规则后，任务会出现在这里。</p>');
  }
  function renderEvents() {
    const types = [...new Set([...Object.keys(state.data.cockpit?.ev_by_type||{}),...state.events.map(e=>e.etype),...(state.filter==='all'?[]:[state.filter])])];
    html('#event-filter', '<option value="all">全部类型</option>'+types.map(t=>`<option value="${esc(t)}">${esc(M.eventName(t))}</option>`).join(''));
    $('#event-filter').value=state.filter;
    const rows=state.events.filter(e=>state.filter==='all'||e.etype===state.filter);
    html('#event-list', rows.length?rows.slice(0,state.eventLimit).map(e=>`<div class="mf-list-row"><div class="mf-list-main"><div class="mf-list-title">${badge(M.eventName(e.etype),e.etype==='security_risk'?'warning':'muted')} <b>${esc(e.etype==='quality_anomaly'?'体征阈值提示':e.title)}</b></div><p class="mf-list-meta">${esc(e.ts)} · ${esc(sourceNames[e.source_id]||e.source_id||'平台')} · #${esc(e.seq)}</p><p>${esc(e.detail||'无补充说明')}</p></div><button class="mf-link" data-event="${e.seq}" data-focus="event-${e.seq}">详情</button></div>`).join(''):`<p class="mf-empty">${state.events.length?'当前页面留存事件中没有该类型。':'当前暂无事件。'}</p>`);
    set('#event-count',`当前页面留存 ${state.events.length} 条 · 匹配 ${rows.length} 条 · 显示 ${Math.min(rows.length,state.eventLimit)} 条`);
    $('#event-more').hidden=rows.length<=8;$('#event-more').textContent=state.eventLimit===8?'展开全部匹配事件':'收起至8条';
  }
  function render() { renderMetrics();renderDepartments();renderSources();renderDistributions();renderTasks();renderEvents();drawTrend();renderHealth(); }

  function timeLabel(p, full=false) {
    if (M.number(p?.epoch)===null) return p?.wall || '时间未提供';
    const d=new Date(p.epoch*1000), pad=v=>String(v).padStart(2,'0');
    return `${full?`${pad(d.getMonth()+1)}/${pad(d.getDate())} `:''}${pad(d.getHours())}:${pad(d.getMinutes())}${full?':'+pad(d.getSeconds()):''}`;
  }
  function showSample(point) {
    if(!point){html('#sample-values','<span class="mf-muted">暂无采样值</span>');return;}
    html('#sample-values',`<b class="mf-detail-heading">${point.simulated?'模拟历史':'运行采样'} · ${timeLabel(point,true)}</b><dl class="mf-definition-grid">${M.SOURCES.map(id=>detail(sourceNames[id],fmt(point.by_source?.[id])+' 条')).join('')}${detail('五源合计',fmt(M.seriesTotal(point))+' 条')}</dl>`);
  }
  function drawTrend() {
    const ck=state.data.cockpit;if(!ck||!window.d3)return;
    const data=M.buildHistory(ck.history||[],ck.history_origin,state.simulated);chartPoints=data.points;
    const node=$('#trend-chart'),width=node.clientWidth;if(width<100)return;
    const d3=window.d3,svg=d3.select(node),height=260,pad={left:state.scale==='log'?54:64,right:15,top:18,bottom:35};
    svg.selectAll('*').remove();svg.attr('viewBox',`0 0 ${width} ${height}`);
    if(!data.points.length){svg.append('text').attr('x',width/2).attr('y',120).attr('text-anchor','middle').text('暂无运行采样');node.setAttribute('aria-label','暂无运行采样');html('#trend-legend','');html('#sample-select','');showSample(null);$('#trend-tooltip').hidden=true;chartPinned=false;state.sample=null;set('#trend-note','当前接口没有运行采样，等待下一次更新。');return;}
    const series=state.trendMode==='total'?['total']:M.SOURCES;
    const value=(point,id)=>id==='total'?M.seriesTotal(point):M.number(point.by_source?.[id]);
    const values=data.points.flatMap(p=>series.map(id=>value(p,id))).filter(v=>v!==null);
    const positive=values.filter(v=>v>0),max=Math.max(1,...values),domain=d3.extent(data.points,p=>p.t);
    if(domain[0]===domain[1]){domain[0]-=2;domain[1]+=2;}
    const x=d3.scaleLinear().domain(domain).range([pad.left,width-pad.right]);
    const log=state.scale==='log';
    const lowPower=positive.length?Math.floor(Math.log10(Math.min(...positive))):0;
    const y=log?d3.scaleLog().domain([Math.pow(10,lowPower),Math.pow(10,Math.max(lowPower+1,Math.ceil(Math.log10(max))))]).range([height-pad.bottom,pad.top]):d3.scaleLinear().domain([0,max*1.08]).nice(4).range([height-pad.bottom,pad.top]);
    const ticks=log?y.ticks().filter(v=>Math.abs(Math.log10(v)-Math.round(Math.log10(v)))<1e-8):y.ticks(4);
    ticks.forEach(v=>{svg.append('line').attr('x1',pad.left).attr('x2',width-pad.right).attr('y1',y(v)).attr('y2',y(v)).attr('class','mf-grid-line');svg.append('text').attr('x',pad.left-9).attr('y',y(v)+4).attr('text-anchor','end').text(v>=10000?(v/10000)+'万':fmt(v));});
    const xTicks=Array.from({length:width<400?3:4},(_,i)=>domain[0]+(domain[1]-domain[0])*i/((width<400?3:4)-1));
    xTicks.forEach((t,i)=>{const p={t,epoch:M.epochOf({t},data.anchor)};svg.append('text').attr('x',x(t)).attr('y',height-10).attr('text-anchor',i===0?'start':i===xTicks.length-1?'end':'middle').text(timeLabel(p));});
    const seriesColor=id=>getComputedStyle(document.body).getPropertyValue(sourceColors[id]).trim()||'#509ee3';
    if(state.simulated && data.boundary>=domain[0]&&data.boundary<=domain[1]){svg.append('line').attr('x1',x(data.boundary)).attr('x2',x(data.boundary)).attr('y1',pad.top).attr('y2',height-pad.bottom).attr('class','mf-boundary');}
    series.forEach(id=>{
      const line=d3.line().defined(p=>value(p,id)!==null&&(!log||value(p,id)>0)).x(p=>x(p.t)).y(p=>y(value(p,id)));
      const simulated=data.points.filter(p=>p.t<=data.boundary), real=data.points.filter(p=>p.t>=data.boundary);
      if(state.simulated)svg.append('path').datum(simulated).attr('d',line).attr('fill','none').attr('stroke',seriesColor(id)).attr('stroke-width',2).attr('stroke-dasharray','4 3');
      svg.append('path').datum(state.simulated?real:data.points).attr('d',line).attr('fill','none').attr('stroke',seriesColor(id)).attr('stroke-width',2.3);
      const last=data.points.at(-1),v=value(last,id);
      if(v!==null&&(!log||v>0))svg.append('circle').attr('cx',x(last.t)).attr('cy',y(v)).attr('r',3).attr('fill',seriesColor(id));
    });
    const latest=data.points.at(-1);
    html('#trend-legend',series.map(id=>`<span><i class="mf-swatch" style="background:var(${sourceColors[id]})"></i>${id==='total'?'五源合计':sourceNames[id]} <b>${fmt(value(latest,id))}</b></span>`).join(''));
    set('#trend-context',`${state.trendMode==='total'?'总记录数':'五个数据来源'} · 条 · ${log?'对数':'线性'}刻度`);
    const zeros=log&&values.some(v=>v===0);
    set('#trend-note',(state.simulated?`虚线为24h模拟历史，分界为启动 ${timeLabel({epoch:data.anchor.epoch},true)}，实线为运行采样。`:'仅展示本次运行保留的实际采样。')+(zeros?' 对数轴不绘制0值；精确值中保留0，可切换线性查看。':''));
    node.setAttribute('aria-label',`${state.trendMode==='total'?'五源总量':'五个来源'}趋势；${state.simulated?'含标记的24小时模拟历史':'仅运行采样'}。精确值可通过下方采样选择器查看。`);
    const guide=svg.append('line').attr('y1',pad.top).attr('y2',height-pad.bottom).attr('class','mf-hover-guide').style('display','none');
    const tooltip=$('#trend-tooltip');
    function inspectPoint(point,px){
      guide.attr('x1',x(point.t)).attr('x2',x(point.t)).style('display',null);
      tooltip.innerHTML=`<b>${point.simulated?'模拟历史':'运行采样'} · ${timeLabel(point,true)}</b>${series.map(id=>`<div>${id==='total'?'五源合计':sourceNames[id]} <strong>${fmt(value(point,id))} 条</strong></div>`).join('')}`;
      tooltip.hidden=false;tooltip.style.left=Math.max(0,Math.min(width-220,px+12))+'px';tooltip.style.top='12px';
      state.sample=point.t;$('#sample-select').value=String(point.t);showSample(point);
    }
    function inspect(event){const [px]=d3.pointer(event,node),t=x.invert(Math.max(pad.left,Math.min(width-pad.right,px)));inspectPoint(data.points[d3.bisector(p=>p.t).center(data.points,t)],px);}
    svg.append('rect').attr('x',pad.left).attr('y',pad.top).attr('width',Math.max(1,width-pad.left-pad.right)).attr('height',height-pad.top-pad.bottom).attr('fill','transparent').on('pointermove',event=>{if(!chartPinned)inspect(event);}).on('click',event=>{chartPinned=!chartPinned;inspect(event);}).on('pointerleave',()=>{if(!chartPinned){guide.style('display','none');tooltip.hidden=true;}});
    if(!chartPinned)tooltip.hidden=true;
    html('#sample-select',data.points.map(p=>`<option value="${p.t}">${p.simulated?'模拟':'采样'} · ${timeLabel(p,true)}</option>`).join(''));
    const retained=data.points.find(p=>p.t===state.sample),selected=retained||latest;$('#sample-select').value=String(selected.t);showSample(selected);
    if(chartPinned&&retained)inspectPoint(retained,x(retained.t));else if(chartPinned){chartPinned=false;tooltip.hidden=true;}
  }

  async function getJson(path) {
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),10000);
    try{const response=await fetch(path,{signal:controller.signal});if(!response.ok)throw new Error('HTTP '+response.status);const body=await response.json();if(body.error)throw new Error(body.error);return body;}finally{clearTimeout(timer);}
  }
  function ingestEvents(events) {state.events=M.mergeEvents(state.events,events);renderEvents();}
  function connectStream() {
    if(state.paused||stream)return;
    const after=state.events[0]?.seq||0;stream=new EventSource('/api/stream?after='+after);
    stream.onopen=()=>{streamConnected=true;state.errors.events=false;renderHealth();};
    stream.onmessage=e=>{try{const event=JSON.parse(e.data);state.errors.events=false;state.loadedAt.events=new Date();ingestEvents([event]);}catch(_){};};
    stream.onerror=()=>{streamConnected=false;stream?.close();stream=null;retryStreamAt=Date.now()+4000;renderHealth();};
  }
  async function refresh() {
    if(busy)return;busy=true;$('#refresh-now').disabled=true;$('#refresh-now').textContent='更新中…';
    const requests=[['cockpit','/api/cockpit'],['overview','/api/overview'],['security','/api/security/overview'],['orchestrator','/api/orchestrator']];
    if(!streamConnected||state.paused)requests.push(['events','/api/events?after=0&limit=800']);
    const results=await Promise.allSettled(requests.map(([,path])=>getJson(path)));
    for(let i=0;i<results.length;i++){
      const key=requests[i][0],r=results[i];state.errors[key]=r.status==='rejected';
      if(r.status==='fulfilled'){
        if(key==='events')state.events=M.mergeEvents([],r.value.events||[]);
        else {
          if(key==='cockpit'&&state.data.cockpit?.history_origin?.t!==undefined&&r.value.history_origin?.t!==state.data.cockpit.history_origin.t){state.events=[];stream?.close();stream=null;streamConnected=false;}
          state.data[key]=r.value;
        }
        state.loadedAt[key]=new Date();
      }
    }
    render();busy=false;$('#refresh-now').disabled=false;$('#refresh-now').textContent='刷新';
    if(Date.now()>=retryStreamAt)connectStream();
    if(restoring){restoring=false;requestAnimationFrame(()=>window.scrollTo(0,restoreY));}
  }
  $('#refresh-now').addEventListener('click',refresh);
  $('#pause-refresh').addEventListener('click',()=>{state.paused=!state.paused;if(state.paused){stream?.close();stream=null;streamConnected=false;}else refresh();syncControls();renderHealth();save();});
  $('#simulated-history').addEventListener('change',e=>{state.simulated=e.target.checked;chartPinned=false;drawTrend();save();});
  $('#trend-scale').addEventListener('change',e=>{state.scale=e.target.value;drawTrend();save();});
  $('#sample-select').addEventListener('change',e=>{state.sample=Number(e.target.value);showSample(chartPoints.find(p=>p.t===state.sample));});
  $('#event-filter').addEventListener('change',e=>{state.filter=e.target.value;state.eventLimit=8;renderEvents();save();});
  $('#event-more').addEventListener('click',()=>{state.eventLimit=state.eventLimit===8?80:8;renderEvents();});
  $('#metric-help').addEventListener('click',()=>openDialog('指标说明与数据范围',`<dl class="mf-definition-grid">${detail('患者与就诊','来自HIS当前数据。就诊按记录计数，患者按主表计数。')}${detail('来源状态','最近扫描成功，不代表实时连接健康。来源状态与驾驶舱为独立请求，可能有数秒时差。')}${detail('风险范围','留存风险队列最多200条；计数取接口metrics，不能由最近30条列表推算。')}${detail('事件范围','后端最多保留800条，当前页面最近80条；不是自建库以来累计。')}${detail('图谱范围','最多100位患者的关系样本，不是全院完整语义资产。')}${detail('服务统计','沿用旧SERVICE_STATS，含内部调用；不与治理服务计数混合。')}${detail('模拟与实际','业务数据来自模拟源；24h补充历史为前端模拟，运行采样为本次进程观测值。')}${detail('当前权限边界','本页为研究演示；原系统的统一身份、入口授权与持久审计尚待独立建设。')}</dl>`));
  document.addEventListener('click',e=>{
    const b=e.target.closest('button');if(!b)return;
    if(b.dataset.scroll){document.getElementById(b.dataset.scroll)?.scrollIntoView({behavior:'auto',block:'start'});return;}
    if(b.dataset.deptMode){state.deptMode=b.dataset.deptMode;renderDepartments();syncControls();save();return;}
    if(b.dataset.trend){state.trendMode=b.dataset.trend;chartPinned=false;drawTrend();syncControls();save();return;}
    if(b.dataset.department){state.department=b.dataset.department;renderDepartments();save();return;}
    if(b.dataset.eventType){state.filter=b.dataset.eventType;renderEvents();$('#event-heading').scrollIntoView({behavior:'auto'});save();return;}
    if(b.dataset.source){const s=state.data.overview?.sources?.find(s=>s.id===b.dataset.source);if(!s){openDialog('来源信息暂不可用','<p>尚未取得来源详情，请刷新后重试。</p>');return;}openDialog(s.name,`<dl class="mf-definition-grid">${detail('来源',s.id)}${detail('说明',s.desc)}${detail('模拟引擎',s.engine)}${detail('位置',s.location)}${detail('逻辑表',fmt(s.tables))}${detail('本次来源记录数',fmt(s.rows))}${detail('最近扫描',s.last_scan)}${detail('原始状态',s.status)}</dl><p class="mf-muted mf-small">这里使用来源状态接口快照，可能与驾驶舱记录数存在刷新时差。</p><a class="mf-button" href="/#discovery">进入来源与变更</a>`);return;}
    if(b.dataset.task){const t=state.data.cockpit?.recent_tasks?.find(t=>t.tid===b.dataset.task);if(t)openDialog(t.name,`<dl class="mf-definition-grid">${detail('任务编号',t.tid)}${detail('状态',M.taskState(t.status))}${detail('优先级',t.priority)}${detail('时间',t.ts)}${detail('耗时',fmt(t.latency_ms,2)+' ms')}${detail('触发事件','#'+(t.trigger?.seq??'—'))}${detail('原事件类型',t.trigger?.etype)}${detail('执行摘要',t.summary||'暂无')}</dl><details><summary>原始任务信息</summary><pre class="mf-code">${esc(JSON.stringify(t,null,2))}</pre></details><p class="mf-small mf-muted">摘要为现有任务返回内容；执行结束不代表业务整改闭环完成。</p><a class="mf-button" href="/#orch">进入规则与任务</a>`);return;}
    if(b.dataset.event){const ev=state.events.find(x=>x.seq===Number(b.dataset.event));if(ev)openDialog(M.eventName(ev.etype),`<dl class="mf-definition-grid">${detail('事件编号',ev.seq)}${detail('时间',ev.ts)}${detail('来源',ev.source_id)}${detail('原类型',ev.etype)}${detail('原始级别',ev.severity)}${detail('原始标题',ev.title)}${detail('说明',ev.detail)}</dl>`);return;}
    if(b.dataset.metric){const k=state.data.cockpit?.kpis||{};const texts={patients:['纳管患者',`HIS主表 ${fmt(k.patients)} 人。当前模拟快照；不代表不同患者号已完成身份消歧。`,'query'],visits:['就诊记录',`HIS门诊与住院共 ${fmt(k.visits)} 条记录。同一患者可多次就诊。`,'query'],rows:['跨系统记录',`当前 ${fmt(k.total_rows)} 条；不同来源记录粒度不同。增长率采用最近约65秒的运行采样估计。`,'discovery'],events:['元数据事件',`后端当前窗口 ${fmt(k.events_total)} 条，最大800条。达到上限后不再等于终身累计。`,'discovery'],graph:['样本图谱',`${fmt(k.graph_nodes)} 节点，${fmt(k.graph_edges)} 条边；最多100位患者的样本。`,'graph'],calls:['服务调用',`旧调用体系共 ${fmt(state.data.cockpit?.svc_calls_total)} 次，含编排内部调用；平均 ${fmt(state.data.cockpit?.svc_avg_ms,2)} ms。`,'services']};const item=texts[b.dataset.metric];if(item)openDialog(item[0],`<p>${esc(item[1])}</p><a class="mf-button" href="/#${item[2]}">查看相关页面</a>`);}
  });
  new ResizeObserver(()=>requestAnimationFrame(drawTrend)).observe($('#trend-chart'));
  document.addEventListener('visibilitychange',()=>{if(document.hidden){stream?.close();stream=null;streamConnected=false;}else if(!state.paused)refresh();});
  window.addEventListener('pagehide',()=>stream?.close());
  syncControls();refresh();setInterval(()=>{if(!state.paused&&!document.hidden)refresh();},4000);
})();

import {ready,currentAccess,allowed,readable,role,headers,bindRole} from '/static/shared/access.js?v=0.1.45';

const $=s=>document.querySelector(s);
const setText=(selector,value)=>{const element=$(selector);if(element)element.textContent=value;};
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const paths={grid:'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z',spark:'m12 3 2.4 6.6L21 12l-6.6 2.4L12 21l-2.4-6.6L3 12l6.6-2.4L12 3z M20 2v4 M18 4h4',sliders:'M4 5h16 M4 12h16 M4 19h16 M8 3v4 M16 10v4 M10 17v4',settings:'M9 4h6l1 3 3 1 1 6-3 2-1 3-6 1-2-3-3-1-1-6 3-2 2-4z M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0',refresh:'M20 7v5h-5 M4 17v-5h5 M5 8a8 8 0 0 1 14-2l1 2 M4 16l1 2a8 8 0 0 0 14-2',database:'M20 6c0 2-4 3-8 3S4 8 4 6s4-3 8-3 8 1 8 3z M4 6v12c0 2 4 3 8 3s8-1 8-3V6 M4 12c0 2 4 3 8 3s8-1 8-3',table:'M3 4h18v16H3z M3 9h18 M9 9v11 M15 9v11 M3 14h18',network:'M9 3h6v6H9z M3 15h6v6H3z M15 15h6v6h-6z M12 9v3 M6 15v-3h12v3',chart:'M4 20V4 M4 20h17 M8 16v-5 M13 16V7 M18 16V4',check:'m5 12 4 4L19 6',clock:'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0 M12 7v5l3 2',arrow:'M5 12h14 m-6-6 6 6-6 6',send:'m4 4 17 8-17 8 3-8-3-8z M7 12h14',copy:'M9 8h11v13H9z M15 8V3H4v13h5',download:'M12 3v12 m-5-5 5 5 5-5 M4 16v5h16v-5',code:'m8 6-6 6 6 6 m8-12 6 6-6 6 M14 3l-4 18',search:'M16 10a6 6 0 1 1-12 0 6 6 0 0 1 12 0 m-2 4 6 6',plus:'M12 5v14 M5 12h14',shield:'m12 3 8 3v6c0 4-4 7-8 9-4-2-8-5-8-9V6l8-3z m-4 9 3 3 5-5'};
const icon=name=>`<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="${paths[name]||paths.table}"/></svg>`;
document.querySelectorAll('[data-icon]').forEach(el=>{if(!el.closest('.sidebar'))el.innerHTML=icon(el.dataset.icon);});
const names={home:'总览',ask:'自然语言查询',query:'专业查询'};
const states={awaiting_chain_confirmation:'需要补充查询意图',waiting_manual_spec_confirmation:'确认查询意图',waiting_field_classification:'待处理目录变化',awaiting_execution_plan:'准备查询方案',awaiting_plan_confirmation:'待确认方案',awaiting_authorization_request:'待处理授权',awaiting_platform_authorization:'等待审批',authorization_declined:'申请未通过',awaiting_query_submission:'可以运行',query_queued:'排队中',query_running:'查询中',query_completed:'已完成',query_failed:'查询未完成',attention:'需要处理',waiting_authorization:'等待连接',completed:'已完成'};
let catalog=[],catalogLoaded=false,flow=null,demand=null,delivery=null,canDownload=false,canRecount=false,poll=null,routeGeneration=0,busy=false,toastTimer=null;
let builder={table:'',join:'',fields:[],filters:[],order:'',direction:'desc',limit:100,sqlOverride:null};
const date=v=>{if(!v)return '—';const d=new Date(v);return Number.isNaN(d.valueOf())?'—':new Intl.DateTimeFormat('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}).format(d);};
const page=()=>new URL(location.href).searchParams.get('page')||'home';
function toast(text,error=false){const el=$('#toast');el.textContent=text;el.hidden=false;el.classList.toggle('error',error);clearTimeout(toastTimer);toastTimer=setTimeout(()=>el.hidden=true,6000);}
async function api(path,body){const response=await fetch(path,{method:body===undefined?'GET':'POST',headers:headers({'Content-Type':'application/json'}),cache:'no-store',...(body===undefined?{}:{body:JSON.stringify(body)})});let result;try{result=await response.json();}catch{throw Error('平台暂时没有返回有效响应，请稍后重试。');}if(!response.ok)throw Error(result.error||result.detail||'请求未完成');return result;}
const queryAction=(id,action,body)=>api('/api/portal/queries/'+encodeURIComponent(id)+'/'+action,body);
function badge(state){const style=state==='query_completed'?'ok':state==='query_failed'||state==='authorization_declined'?'error':['query_queued','query_running'].includes(state)?'running':'pending';return `<span class="status ${style}">${state==='query_completed'?'✓ ':''}${esc(states[state]||'处理中')}</span>`;}
function heading(title,subtitle,action=''){return `<div class="page-heading"><div><p class="eyebrow">DATA WORKSPACE</p><h1>${esc(title)}</h1><p>${esc(subtitle)}</p></div>${action}</div>`;}
function loading(text='正在加载…'){return `<div class="loading"><span class="spinner"></span>${esc(text)}</div>`;}
function errorBox(error){return `<div class="notice error" role="alert">${esc(error.message||error)}</div>`;}
async function task(button,fn){if(busy)return;busy=true;button.disabled=true;const old=button.innerHTML;button.textContent='处理中…';try{await fn();}catch(error){toast(error.message,true);const host=$('#action-error');if(host)host.innerHTML=errorBox(error);}finally{busy=false;if(button.isConnected){button.disabled=false;button.innerHTML=old;}}}
function button(id,fn){const el=$('#'+id);if(el)el.onclick=()=>task(el,fn);}
function go(next,id){const url=new URL(location.href);url.search='';url.searchParams.set('page',next);if(id)url.searchParams.set('flow',id);history.pushState(null,'',url);renderRoute();}
document.addEventListener('click',event=>{const a=event.target.closest('a');if(!a||a.hasAttribute('data-full-navigation')||event.ctrlKey||event.metaKey||event.shiftKey||event.altKey||a.target||a.hasAttribute('download'))return;const url=new URL(a.href,location.href);if(url.origin!==location.origin||url.pathname!==location.pathname)return;event.preventDefault();history.pushState(null,'',url);renderRoute();});
window.addEventListener('popstate',renderRoute);$('#refresh').onclick=()=>renderRoute();
async function loadCatalog(){if(catalogLoaded)return;catalog=(await api('/api/portal/catalog')).tables;catalogLoaded=true;}
const fieldLabel=name=>{const found=catalog.flatMap(t=>t.fields).find(f=>f.name===name);return found?.label||name;};
function recentTable(rows){if(!rows.length)return '<div class="empty">还没有查询记录。从上方选择一种方式，开始第一次查询。</div>';return `<div class="table-scroll"><table><thead><tr><th>查询内容</th><th>查询方式</th><th>状态</th><th>更新时间</th><th></th></tr></thead><tbody>${rows.map(r=>`<tr><td><a class="query-title" href="?page=${r.query_mode==='professional'?'query':'ask'}&flow=${encodeURIComponent(r.id)}"><span>${icon(r.query_mode==='professional'?'sliders':'spark')}</span><span>${esc((r.request_text||'未命名查询').split('\n')[0])}</span></a></td><td>${r.query_mode==='professional'?'专业查询':'自然语言'}</td><td>${badge(r.state)}</td><td>${esc(date(r.updated_at))}</td><td><a aria-label="打开查询" href="?page=${r.query_mode==='professional'?'query':'ask'}&flow=${encodeURIComponent(r.id)}">↗</a></td></tr>`).join('')}</tbody></table></div>`;}
async function home(gen){
 const data=await api('/api/portal/overview');if(gen!==routeGeneration)return;
 $('#main').innerHTML=`
  <section class="hero"><div><h1>数据工作台</h1><p>选择自然语言查询，或按表和字段构建查询。</p></div><span class="date-tag">${new Intl.DateTimeFormat('zh-CN',{month:'long',day:'numeric',weekday:'long'}).format(new Date())}</span></section>
  <section class="entry-grid" aria-label="开始查询"><a class="entry ai" href="?page=ask"><span class="entry-symbol">${icon('spark')}</span><div><span class="tiny-label">ASK WITH AI</span><h2>用自然语言提问</h2><p>描述你想了解的内容，让 AI 帮你找到数据。</p></div><span class="arrow">↗</span></a><a class="entry" href="?page=query"><span class="entry-symbol">${icon('sliders')}</span><div><span class="tiny-label">BUILD YOUR QUERY</span><h2>自主构建查询</h2><p>选择数据、字段与条件，掌握每一步。</p></div><span class="arrow">↗</span></a></section>
  <section class="stats" aria-label="数据资源统计">${[[data.sources,'已连接数据源','database','当前可用的来源'],[data.tables,'可见数据表','table','当前角色可查看的目录'],[data.fields,'目录字段','grid','已登记的可见字段'],[data.query_count,'查询记录','chart',data.scope]].map(([n,l,i,s])=>`<div class="stat"><div class="stat-label">${l}${icon(i)}</div><div class="stat-number">${n.toLocaleString('zh-CN')}</div><small>${esc(s)}</small></div>`).join('')}</section>
  <section class="panel"><div class="panel-head" style="padding-bottom:18px"><div><h2>最近查询</h2><p>继续上次的探索，或查看已有结果</p></div><span class="text-link">最近 ${data.recent.length} 条</span></div>${recentTable(data.recent)}</section>`;
}
function modelName(id){return ({qwen:'通义千问',deepseek:'DeepSeek',ollama:'Ollama'})[id]||id||'AI';}
function ask(initial=''){const enabled=allowed('demand.create'),generation=routeGeneration;$('#main').innerHTML=`<section class="ask-start"><div class="ask-title"><span class="entry-symbol">${icon('spark')}</span><p class="eyebrow">ASK YOUR DATA</p><h1>你想从数据中了解什么？</h1><p>直接描述问题，我们会先确认理解，再查询数据。</p></div><form id="ask-form" class="composer"><div class="ai-model-picker"><label for="ai-provider">选择 AI 接口</label><select id="ai-provider" disabled><option>正在读取可用接口…</option></select><small id="ai-provider-status">选择本次自然语言理解模型；只发送需求文字和当前可见目录元数据。</small></div><textarea id="question" maxlength="4000" aria-label="用自然语言描述查询需求" placeholder="例如：查询唐军的性别和就诊科室…" required ${enabled?'':'disabled'}>${esc(initial)}</textarea><div class="composer-foot"><small>${enabled?'Enter 换行 · Ctrl / ⌘ + Enter 提交':'当前身份不能新建查询，可返回总览查看已有记录。'}</small><button class="button primary" id="ask-submit" disabled type="submit">开始查询 ${icon('send')}</button></div></form><div class="examples" aria-label="示例问题">${['查询唐军的性别和就诊科室','查询唐军的出生日期和医保类型','查询唐军的现病史'].map(x=>`<button class="example" type="button">${esc(x)}</button>`).join('')}</div><p class="hint">有不明确的口径时，我们会向你确认。结果仅来自当前已连接的数据源。</p><div id="ask-error" role="alert"></div></section>`;
 document.querySelectorAll('.example').forEach(el=>el.onclick=()=>{$('#question').value=el.textContent;$('#question').focus();});
 $('#question').onkeydown=e=>{if(e.key==='Enter'&&(e.ctrlKey||e.metaKey)){e.preventDefault();$('#ask-form').requestSubmit();}};
 $('#ask-form').onsubmit=e=>{e.preventDefault();const text=$('#question').value.trim(),provider=$('#ai-provider').value;if(!text||!provider)return;task($('#ask-submit'),async()=>{const gen=routeGeneration;$('#ask-error').innerHTML=loading('正在使用所选模型理解问题并匹配数据…');try{const result=await api('/api/portal/queries',{text,provider});if(gen!==routeGeneration){toast('查询已准备好，可在总览的最近查询中打开');return;}go('ask',result.flow.id);}catch(error){$('#ask-error').innerHTML=errorBox(error);throw error;}});};
 api('/api/portal/ai/providers').then(config=>{if(generation!==routeGeneration)return;const select=$('#ai-provider'),status=$('#ai-provider-status'),submit=$('#ask-submit'),providers=config.providers||[],available=providers.filter(item=>item.configured);select.innerHTML=providers.map(item=>`<option value="${esc(item.id)}" ${item.configured?'':'disabled'}>${esc(item.name)}${item.model?' · '+esc(item.model):''}${item.configured?'':'（未配置）'}</option>`).join('');let stored='';try{stored=localStorage.getItem('mf.ai.provider')||'';}catch{}const selected=available.find(item=>item.id===stored)||available.find(item=>item.id===config.default)||available[0];if(selected){select.value=selected.id;select.disabled=!enabled;submit.disabled=!enabled;status.textContent='本次使用 '+selected.name+'；仅向模型发送需求文字和当前可见目录元数据。';}else{select.disabled=true;submit.disabled=true;status.textContent='当前没有已配置的 AI 接口，请联系平台管理员。';}select.onchange=()=>{try{localStorage.setItem('mf.ai.provider',select.value);}catch{}const item=available.find(value=>value.id===select.value);if(item)status.textContent='本次使用 '+item.name+'；仅向模型发送需求文字和当前可见目录元数据。';};}).catch(error=>{if(generation!==routeGeneration)return;$('#ai-provider-status').textContent=error.message||'读取 AI 接口失败';$('#ask-error').innerHTML=errorBox(error);});
}
const calculationLabels={row_count:'记录数',person_count:'去重人数',diabetes_count:'去重人数',doris_people_count:'去重人数'};
const calculationLabel=f=>calculationLabels[f.professional_preview?.calculation?.kind];
const cohortLabels={diabetes_all:'糖尿病（ICD‑10 E10–E14）',diabetes_type2:'2 型糖尿病（ICD‑10 E11）',diabetes_type1:'1 型糖尿病（ICD‑10 E10）',
 diagnosis:'Doris 诊断数据集',lab:'Doris 检验数据集'};
function outputFields(f){if(calculationLabel(f))return [{field_name:'count',label:calculationLabel(f)}];return f.professional_preview?.output_fields?.map(x=>({field_name:x.name,classification:x.classification}))||f.field_lookup?.output_fields||[];}
function operationSummary(f){
 const shape=f.professional_preview;if(!shape)return '';
 const label=id=>catalog.flatMap(t=>t.fields).find(x=>x.id===id)?.label||id;
 const ops={'=':'等于','!=':'不等于','<':'早于 / 小于','<=':'不晚于 / 不大于','>':'晚于 / 大于','>=':'不早于 / 不小于',in:'属于',prefix:'开头为'};
 const conditions=shape.filters.map(x=>`${label(x.field)} ${ops[x.op]||x.op} ${Object.hasOwn(cohortLabels,x.value)?cohortLabels[x.value]:x.value}`).join('；');
 const links=shape.join_path||[];
 const path=links.length?`<div class="notice operation-summary"><strong>关联路径</strong><ol>${links.map(edge=>
  `<li>${esc(edge.from_table+'.'+edge.from_field)} → ${esc(edge.to_table+'.'+edge.to_field)} <small>${esc(edge.basis)}</small></li>`).join('')}</ol></div>`:'';
 const calculation=calculationLabel(f);
 if(calculation){
  const scope=['diabetes_count','doris_people_count'].includes(shape.calculation.kind)?cohortLabels[shape.filters[0]?.value]:conditions;
  const minimum=f.draft?.execution_constraints?.min_group_size ?? 5;
  return path+`<div class="notice operation-summary"><strong>统计方式 · ${esc(calculation)}</strong><p>${esc(scope||'整张表')}</p><p>${minimum===0?'本次交付实际'+esc(calculation)+'。':`本次交付一个汇总值。少于 ${minimum} 时隐藏具体数量。`}</p></div>`;
 }
 const order=shape.order_by?.map(x=>`${label(x.field)}${x.direction==='desc'?'降序':'升序'}`).join('、');
 return path+`<div class="notice operation-summary"><strong>实际查询条件（同时满足）</strong><p>${esc(conditions)}</p><p>${order?'排序：'+esc(order)+'；':''}最多返回 ${shape.max_rows} 条，不足时返回实际记录。</p></div>`;
}
function technical(f){const sql=f.query_validation?.normalized_sql||f.query_draft||f.draft?.query_draft;return `<details class="details"><summary>查看查询详情</summary>${sql?`<pre>${esc(sql)}</pre>`:'<p>查询草案尚未生成。</p>'}<ul>${(f.steps||[]).map(s=>`<li>${esc(s.label||s.capability)}：${esc(s.detail||states[s.status]||s.status)}</li>`).join('')}</ul><p>查询编号：${esc(f.id)}</p></details>`;}
async function loadFlow(id,gen){await loadCatalog();const result=await api('/api/portal/queries/'+encodeURIComponent(id));if(gen!==routeGeneration)return;flow=result.flow;demand=result.demand;canDownload=result.can_download;canRecount=result.can_recount;renderFlow();if(flow.state==='query_completed'&&allowed('delivery.read'))await readResult(gen);schedulePoll();}
function schedulePoll(){clearTimeout(poll);if(flow&&['query_queued','query_running','awaiting_platform_authorization','waiting_manual_spec_confirmation'].includes(flow.state)){const id=flow.id,gen=routeGeneration;poll=setTimeout(async()=>{try{const result=await api('/api/portal/queries/'+id);if(gen!==routeGeneration)return;if(result.flow.state!==flow.state||result.flow.version!==flow.version){flow=result.flow;demand=result.demand;canDownload=result.can_download;canRecount=result.can_recount;renderFlow();if(flow.state==='query_completed')await readResult(gen);}schedulePoll();}catch(error){if(gen===routeGeneration)toast(error.message,true);}},4000);}}
function renderFlow(){const f=flow,outputs=outputFields(f);const pending=(f.field_bindings||[]).filter(b=>b.status!=='suggested');const title=states[f.state]||'查询准备中';const briefText=(f.query_mode==='semantic'||calculationLabel(f))?f.draft.object_scope:f.professional_preview?`按 ${f.professional_preview.filters.length} 个条件筛选，最多返回 ${f.professional_preview.max_rows} 条记录。`:(f.field_lookup?.name?`按姓名“${f.field_lookup.name}”精确筛选，返回下列字段。`:f.draft?.object_scope||'正在核对查询内容与可用数据。');
 const guidanceStates=['awaiting_chain_confirmation','waiting_manual_spec_confirmation','awaiting_plan_confirmation','attention','waiting_field_classification'];
 const guidanceHtml=f.query_mode==='semantic'&&guidanceStates.includes(f.state)&&(f.user_guidance||f.verified_guidance)?`<div class="notice query-guidance" role="status"><strong>本次查询提示</strong>${f.user_guidance?`<p>AI说明：${esc(f.user_guidance)}</p>`:''}${f.verified_guidance?`<p>平台核验：${esc(f.verified_guidance)}</p>`:''}</div>`:'';
 const confirmed=f.clarification_answers||[];
 const confirmedHtml=confirmed.length&&['awaiting_chain_confirmation','attention'].includes(f.state)
  ?`<div class="chips" aria-label="已确认口径">${confirmed.map(item=>`<span class="chip" title="${esc(item.question)}">${icon('check')}${esc(item.answer)}</span>`).join('')}</div>`:'';
 $('#main').innerHTML=heading(f.query_mode==='professional'?'专业查询':'自然语言查询','查询内容、必要确认和结果，在这里完成。',`<a class="button small" href="?page=${f.query_mode==='professional'?'query':'ask'}">${icon('plus')} 新建查询</a>`)+`<section class="conversation"><div class="user-bubble">${esc((f.request_text||'').split(/\n用户(?:补充：|针对)/)[0])}</div><div class="assistant-head"><span class="brand-mark" aria-hidden="true"><i></i><i></i><i></i><i></i></span>数据助手 ${badge(f.state)}${f.ai_provider?`<span class="ai-model-badge">${esc(modelName(f.ai_provider))}${f.ai_model?' · '+esc(f.ai_model):''}</span>`:''}</div><div class="answer-card"><h2>${pending.length?'还有 '+pending.length+' 项需要你确认':esc(title)}</h2><p>${esc(briefText)}</p>${guidanceHtml}${confirmedHtml}${operationSummary(f)}${outputs.length?`<div class="chips">${outputs.map(o=>`<span class="chip">${icon('check')}${esc(o.label||fieldLabel(o.field_name))}</span>`).join('')}</div>`:''}<div id="flow-content"></div><div id="action-error" role="alert"></div>${technical(f)}</div><div id="results"></div></section>`;
 const host=$('#flow-content');
 if(f.query_mode==='semantic'&&f.state==='attention'){
  const saved=f.clarification_answers?.length;
  host.innerHTML=`<div class="actions">${saved?'<button class="button primary" id="retry-understanding-saved">保留答案并重试</button>':''}<button class="button" id="revise">修改需求</button></div>`;
  button('retry-understanding-saved',async()=>{
   const response=await queryAction(f.id,'retry-understanding',{expected_version:f.version});
   await useFlow(response.flow);
  });
  $('#revise').onclick=()=>{go('ask');ask((f.request_text||'').split('\n用户补充：')[0]);};
  return;
 }

 if(f.state==='awaiting_chain_confirmation'&&pending.length){renderClarifications(host,pending);return;}
 if(f.state==='query_completed'){host.innerHTML='<p>查询已完成。正在读取当前权限允许查看的结果。</p>';return;}
 if(['query_queued','query_running'].includes(f.state)){host.innerHTML=loading(f.state==='query_queued'?'查询已提交，正在排队…':'正在查询数据，请稍候…');return;}
 if(f.state==='waiting_field_classification'){
  host.innerHTML='<div class="notice">部分字段的交付分类需要管理员确认。你的查询已保留，无需重新输入。</div>';
  return;
 }
 if(f.state==='waiting_manual_spec_confirmation'){
  const tableNames=(f.professional_preview?.tables||[]).map(table=>catalog.find(t=>t.id===table.id)?.label||table.name||table);
  const level=currentAccess().principal?.level||1;
  const quality=f.draft?.execution_constraints?.quality_min_complete===0
   ?'<p>空值按数据源的实际结果保留，以「—」显示。</p>':'';
  host.innerHTML=`<div class="notice operation-summary"><strong>当前权限：${level}级</strong><p>${esc(currentAccess().principal?.visibility||'按账户等级控制结果查看范围')}</p><p>可用表：${esc(tableNames.join('、')||'已按账户范围匹配')}</p>${quality}<p>字段库中的现有字段已统一确认；这次只需确认查询意图。确认后平台会自动按你的权限执行。</p></div><div class="actions"><button class="button primary" id="confirm-intent">确认意图并执行 ${icon('arrow')}</button><button class="button" id="revise">修改需求</button></div>`;
  button('confirm-intent',async()=>{const result=await api('/api/portal/queries/'+encodeURIComponent(f.id)+'/execute',{expected_version:f.version,confirmed:true});await useFlow(result.flow);});
  $('#revise').onclick=()=>{const text=f.request_text.split('\n')[0];if(f.query_mode==='professional'){builder.sqlOverride=f.query_draft;go('query');}else{go('ask');ask(text);}};return;
 }
 if(['awaiting_chain_confirmation','awaiting_plan_confirmation'].includes(f.state)){
  const summary=f.state==='awaiting_plan_confirmation'?'方案已生成，点击后会按当前账户权限直接登记授权并执行。':'请补充系统提出的意图问题；字段定义已统一确认。';
  host.innerHTML=`<p>${esc(summary)}</p><div class="actions"><button class="button primary" id="prepare">${f.state==='awaiting_plan_confirmation'?'确认意图并执行':'继续澄清' } ${icon('arrow')}</button><button class="button" id="revise">修改需求</button></div>`;
  button('prepare',async()=>{if(f.state==='awaiting_plan_confirmation'){const result=await api('/api/portal/queries/'+encodeURIComponent(f.id)+'/execute',{expected_version:f.version,confirmed:true});await useFlow(result.flow);}else await advance();});
  $('#revise').onclick=()=>{const text=f.request_text.split('\n')[0];go(f.query_mode==='professional'?'query':'ask');if(f.query_mode!=='professional')ask(text);};return;
 }
 if(['awaiting_authorization_request','authorization_declined','awaiting_platform_authorization'].includes(f.state)){
  host.innerHTML=`<div class="notice">${f.state==='authorization_declined'?'本次访问申请未通过，请联系管理员核对用途和字段。':f.state==='awaiting_platform_authorization'?'申请已提交，等待管理员审批。审批结果会自动更新。':'查询方案已准备好，需要确认本次数据访问权限。'}</div>`;
  if(role()==='requester'&&f.state!=='awaiting_platform_authorization'){
   host.insertAdjacentHTML('beforeend','<div class="actions"><button id="request-access" class="button primary">提交访问申请</button></div>');
   button('request-access',async()=>{const result=await queryAction(f.id,'request-authorization',{expected_version:f.version,request:true});await useFlow(result.flow);});
  }
  return;
 }
 if(f.state==='awaiting_query_submission'||f.state==='query_failed'){
  const retrying=f.state==='query_failed',principal=currentAccess().principal;
  const authorization=f.authorization_request;
  const qualityFailed=retrying&&f.query_error_kind==='delivery_quality';
  const error=f.query_error==='交付复核未通过：quality'
   ?'数据已经读取，部分输出字段为空，完整率未达到本次交付要求。':f.query_error;
  const failure=error?errorBox(error):'';
  if(retrying&&f.execution_submitted_by&&f.execution_submitted_by!==principal?.id){
   host.innerHTML=failure+'<p>请使用原提交账号重试本次查询。</p>';return;
  }
  if(retrying&&authorization?.authorization_basis==='account_level'&&authorization.account_level!==principal?.level){
   host.innerHTML=failure+'<p>账户权限等级已变化，请按当前权限重新发起查询并确认意图。</p>';return;
  }
  if(qualityFailed&&f.professional_preview&&f.query_draft&&allowed('demand.create')){
   host.innerHTML=failure+'<p>可以用相同的筛选条件和输出字段，新建一条保留空值的查询草案。</p><div class="actions"><button id="keep-missing" class="button primary">保留空值，重新确认</button></div>';
   button('keep-missing',async()=>{
    const result=await api('/api/portal/professional',{sql:f.query_draft,confirmed:true,allow_missing:true});
    go('query',result.flow.id);
   });
   return;
  }
  host.innerHTML=`${failure||'<p>本次访问权限已就绪。点击运行后读取数据。</p>'}<div class="actions"><button id="run-query" class="button primary">${icon('arrow')}${retrying?'重试本次查询':'运行查询'}</button></div>`;
  button('run-query',async()=>{const r=await queryAction(f.id,'submit',{expected_version:f.version,confirmed:true});await useFlow(r.flow);});return;
 }
 const blockers=(f.steps||[]).filter(s=>['gap','blocked','waiting_authorization'].includes(s.status));host.innerHTML=`<div class="notice">${esc(f.query_error||f.execution_output_blocker||blockers.map(s=>s.detail).join('；')||'当前查询还需要处理目录或执行条件。你的输入已保存。')}</div><div class="actions"><button class="button" id="retry-understanding">重新描述问题</button></div>`;$('#retry-understanding').onclick=()=>{go('ask');ask(f.request_text.split('\n')[0]);};
}
function renderClarifications(host,bindings){
 host.innerHTML=bindings.map((binding,index)=>{
  const choices=binding.options||[];
  const fields=binding.candidates||[];
  const content=choices.length?choices.map(choice=>`<label class="choice"><input type="radio" name="option-${index}" value="${esc(choice.id)}" ${choice.recommended?'checked':''}><span>${esc(choice.label)}${choice.recommended?'<small>推荐口径</small>':''}</span></label>`).join(''):
   fields.map(field=>`<label class="choice"><input type="checkbox" value="${esc(field.field_id)}" ${((binding.selected_field_ids||[]).includes(field.field_id)||binding.suggested_field_id===field.field_id)?'checked':''}><span>${esc(field.field_label||field.field_name)}<small>${esc(field.table_name||field.table)} · ${esc(field.definition||'')}</small></span></label>`).join('');
  const needsText=!choices.length&&(!fields.length||['sql_missing','model_missing','missing','combination_unresolved'].includes(binding.status));
  return `<fieldset class="clarify" data-binding="${index}"><legend>${esc(binding.phrase||'查询口径')}</legend><p>${esc(binding.question||'请选择本次需要的字段。')}</p>${content}${needsText?`<textarea class="field-input" rows="2" aria-label="${esc(binding.phrase)}的补充说明" placeholder="请补充这项口径…"></textarea>`:''}</fieldset>`;

 }).join('')+'<div class="actions"><button class="button primary" id="resolve">确认选择并继续</button><button class="button" id="edit-clarification">修改需求</button></div>';
 $('#edit-clarification').onclick=()=>{const request=(flow.request_text||'').split('\n用户补充：')[0];go('ask');ask(request);};
 button('resolve',async()=>{
  const answers=bindings.map((binding,index)=>{
   const box=host.querySelector(`[data-binding="${index}"]`);
   if(binding.options?.length){
    const chosen=box.querySelector('input[type=radio]:checked');
    if(!chosen)throw Error('请选择查询口径');
    return {intent_id:binding.intent_id,option_id:chosen.value};
   }
   const selected_field_ids=[...box.querySelectorAll('input:checked')].map(input=>input.value);
   const answer=box.querySelector('textarea')?.value.trim()||'';
   if(binding.candidates?.length&&!selected_field_ids.length)throw Error('请为“'+binding.phrase+'”选择字段');
   if(!binding.candidates?.length&&!answer)throw Error('请补充“'+binding.phrase+'”');
   return {intent_id:binding.intent_id,selected_field_ids,answer};
  });
  const payload={expected_version:flow.version,answers};
  const response=flow.query_mode==='semantic'?await queryAction(flow.id,'clarify',payload):
   await queryAction(flow.id,'clarify-legacy',payload);
  await useFlow(response.flow,true);
 });
}
async function useFlow(next,replace=false){flow=next;delivery=null;const url=new URL(location.href);url.searchParams.set('flow',flow.id);history.replaceState(null,'',url);const response=await api('/api/portal/queries/'+flow.id);flow=response.flow;demand=response.demand;canDownload=response.can_download;canRecount=response.can_recount;renderFlow();if(flow.state==='query_completed')await readResult(routeGeneration);schedulePoll();}
async function advance(){let f=flow;
 if(f.state==='awaiting_chain_confirmation'){
  const ids=[...new Set((f.steps||[]).flatMap(s=>s.candidates||[]).map(x=>x.id).filter(Boolean))];
  f=await queryAction(f.id,'confirm-chain',{expected_version:f.version,confirmed:true,note:'用户在工作台核对筛选范围、输出字段和查询方案',reviewed_resource_ids:ids,accepted_resource_ids:ids});
 }
 if(f.state==='waiting_manual_spec_confirmation'&&allowed('demand.confirm')){
  if(['professional','semantic'].includes(f.query_mode))f=(await api('/api/portal/'+(f.query_mode==='semantic'?'queries':'professional')+'/'+f.id+'/confirm',{expected_version:f.version,confirmed:true})).flow;
  else{const current=await api('/api/portal/queries/'+f.id);const r=await api('/api/portal/query-demands/'+encodeURIComponent(f.demand_id)+'/confirm',{expected_version:current.demand.version,confirmations:{content:true,attachments:true,gaps:true,execution_constraints:true,quality_requirement_reviewed:Boolean($('#quality-review')?.checked)},note:'用户已核对工作台展示的筛选范围、字段、用途和查询方案',query_sql_confirmed:true,query_draft:f.query_validation.normalized_sql});f=r.intake_flow;}
 }
 if(f.state==='awaiting_plan_confirmation'&&allowed('plan.confirm'))f=(await queryAction(f.id,'confirm-plan',{expected_version:f.version,confirmed:true,note:'用户已确认本次查询范围和固定方案'})).flow;
 await useFlow(f);
}
async function readResult(gen){try{const r=await queryAction(flow.id,'result',{});if(gen!==routeGeneration)return;delivery=r;flow=r.flow;$('#flow-content').innerHTML='<p>查询已完成。以下为本次授权范围内的实际返回结果。</p>';renderResult();}catch(error){if(gen===routeGeneration){$('#results').innerHTML=errorBox(error)+'<button class="button" id="read-again">重新读取结果</button>';button('read-again',()=>readResult(gen));}}}
function renderResult(){const result=delivery.result||{},columns=result.columns||[],original=result.rows||[];let filtered=original.slice(),sortIndex=-1,ascending=true;
 const calculation=calculationLabels[result.calculation];
 const resultLabel=name=>name==='count'&&calculation?calculation:fieldLabel(name);
 const resultStatus=q=>result.suppressed?(calculation?'低于最小交付门槛':'结果受当前权限保护'):
  calculation?'汇总交付':q?`显示 ${filtered.length} / ${original.length} 条 · ${columns.length} 个字段`:`返回 ${original.length} 条 · ${columns.length} 个字段`;
 $('#results').innerHTML=`<section class="panel result-panel"><div class="result-head"><div><h2>查询结果 <span class="status ok">已完成</span></h2><small id="result-count">${resultStatus('')}</small></div><div class="result-tools"><input type="search" id="result-search" placeholder="筛选当前结果…" aria-label="筛选当前结果"><button id="copy-result" class="button small">${icon('copy')}复制</button>${canDownload&&allowed('delivery.download')?`<button id="export-result" class="button small">${icon('download')}导出</button>`:''}</div></div><div class="table-scroll"><table class="result-table"><thead><tr>${columns.map((c,i)=>`<th><button data-sort="${i}" title="按${esc(resultLabel(c))}排序">${esc(resultLabel(c))}<span>↕</span></button></th>`).join('')}</tr></thead><tbody id="result-rows"></tbody></table></div><div id="empty-result" class="empty" hidden></div><div class="result-foot"><span>${calculation?'本次仅交付汇总值':'仅展示本次已授权的返回记录'}</span><span>有效至 ${esc(date(delivery.expires_at))}</span></div></section>`;
 function paint(){const q=$('#result-search').value.toLocaleLowerCase();filtered=original.filter(row=>row.some(v=>String(v??'').toLocaleLowerCase().includes(q)));if(sortIndex>=0)filtered.sort((a,b)=>{const av=a[sortIndex],bv=b[sortIndex];const order=av==null?1:bv==null?-1:typeof av==='number'&&typeof bv==='number'?av-bv:String(av).localeCompare(String(bv),'zh-CN',{numeric:true});return ascending?order:-order;});$('#result-rows').innerHTML=filtered.map(row=>'<tr>'+row.map(v=>`<td>${v==null?'<span title="缺失值">—</span>':esc(v)}</td>`).join('')+'</tr>').join('');$('#result-count').textContent=resultStatus(q);const empty=$('#empty-result');empty.hidden=filtered.length>0;empty.textContent=result.message||(original.length?'当前筛选没有匹配记录，试试其他关键词。':'查询已执行，当前数据源没有满足本次筛选与关联条件的记录。');}
 $('#result-search').oninput=paint;document.querySelectorAll('[data-sort]').forEach(el=>el.onclick=()=>{const i=Number(el.dataset.sort);ascending=sortIndex===i?!ascending:true;sortIndex=i;document.querySelectorAll('[data-sort] span').forEach(s=>s.textContent='↕');el.querySelector('span').textContent=ascending?'↑':'↓';paint();});paint();
 if(result.suppressed&&canRecount){
  $('#empty-result').insertAdjacentHTML('afterend','<div class="actions"><button class="button small" id="recount">按最新规则重新统计</button></div>');
  button('recount',async()=>{
   const response=await queryAction(flow.id,'recount',{expected_version:flow.version});
   await useFlow(response.flow);
  });
 }
 button('copy-result',async()=>{await navigator.clipboard.writeText([columns.map(resultLabel),...filtered].map(row=>row.map(v=>String(v??'').replace(/[\t\r\n]/g,' ')).join('\t')).join('\n'));toast('已复制当前筛选后的结果');});
 button('export-result',async()=>{const r=await fetch('/api/portal/deliveries/'+encodeURIComponent(delivery.delivery_id)+'/download',{method:'GET',headers:headers()});if(!r.ok){let reason;try{reason=(await r.json()).error;}catch{}throw Error(reason||'当前结果不允许导出');}const url=URL.createObjectURL(await r.blob());const a=document.createElement('a');a.href=url;a.download='查询结果.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
}
const quote=value=>'"'+String(value).replaceAll('"','""')+'"';
function builderTables(){return [catalog.find(t=>t.id===builder.table),catalog.find(t=>t.id===builder.join)].filter(Boolean);}
function builderFields(){return builderTables().flatMap((t,i)=>t.fields.map(f=>({...f,table:t,alias:i?'j':'t'})));}
function buildSQL(){if(builder.sqlOverride!==null)return builder.sqlOverride;const tables=builderTables(),fields=builderFields(),ref=id=>{const f=fields.find(f=>f.id===id);return f?`${f.alias}.${quote(f.name)}`:null;};if(!tables.length||!builder.fields.length)return '';const outputs=builder.fields.map(ref).filter(Boolean);let sql='SELECT '+outputs.join(', ')+'\nFROM '+quote(tables[0].name)+' t';if(tables[1])sql+='\nJOIN '+quote(tables[1].name)+' j ON t."patient_id" = j."patient_id"';const filters=builder.filters.filter(f=>ref(f.field)&&String(f.value).trim());if(filters.length)sql+='\nWHERE '+filters.map(f=>ref(f.field)+" = '"+String(f.value).replaceAll("'","''")+"'").join('\n  AND ');if(builder.order&&ref(builder.order))sql+='\nORDER BY '+ref(builder.order)+' '+builder.direction.toUpperCase();return sql+'\nLIMIT '+builder.limit;}
async function professional(gen){await loadCatalog();if(gen!==routeGeneration)return;$('#main').innerHTML=heading('专业查询','按自己的方式探索数据。选择字段与精确筛选条件，SQL 可展开编辑。')+`<div class="builder"><aside class="data-picker"><h2>数据目录</h2><input type="search" id="table-search" placeholder="搜索数据表…" aria-label="搜索数据表"><div id="table-list"></div></aside><div class="builder-main" id="builder-main"></div></div>`;renderTableList();$('#table-search').oninput=renderTableList;renderBuilder();}
function renderTableList(){const q=($('#table-search')?.value||'').toLowerCase();const groups={};for(const t of catalog){if(!(t.name+' '+t.source+' '+t.fields.map(f=>f.label).join(' ')).toLowerCase().includes(q))continue;(groups[t.source]??=[]).push(t);}$('#table-list').innerHTML=Object.entries(groups).map(([source,tables])=>`<p class="source-name">${esc(source)}</p>${tables.map(t=>`<button class="table-option ${builder.table===t.id?'selected':''}" data-table="${esc(t.id)}">${icon('table')}<span>${esc(t.name)}</span><small>${t.fields.length}</small></button>`).join('')}`).join('')||'<div class="empty">没有匹配的数据表</div>';document.querySelectorAll('[data-table]').forEach(el=>el.onclick=()=>{if(builder.sqlOverride!==null&&!confirm('切换数据表将重新生成 SQL，继续吗？'))return;builder={table:el.dataset.table,join:'',fields:[],filters:[],order:'',direction:'desc',limit:100,sqlOverride:null};const t=builderTables()[0];const field=t.fields.find(f=>f.name==='name')||t.fields.find(f=>f.name==='patient_id')||t.fields[0];if(field)builder.filters=[{field:field.id,value:''}];renderTableList();renderBuilder();});}
function renderBuilder(){const host=$('#builder-main'),tables=builderTables(),fields=builderFields();if(!tables.length&&builder.sqlOverride===null){host.innerHTML='<section class="builder-card empty" style="padding:85px 25px">'+icon('database')+'<h2 style="margin-top:18px">从一张数据表开始</h2><p>在左侧选择数据表，再添加字段和筛选条件。</p></section>';return;}
 const options=(selected,empty=false)=>`${empty?'<option value="">不排序</option>':''}${fields.map(f=>`<option value="${esc(f.id)}" ${f.id===selected?'selected':''}>${esc(f.label)}${tables.length>1?' · '+esc(f.table.name):''}</option>`).join('')}`;
 host.innerHTML=`${builder.sqlOverride!==null?'<div class="notice">当前使用编辑后的 SQL。修改可视化选项将重新生成 SQL。</div>':''}<section class="builder-card"><h2><span class="step-number">1</span>选择输出字段</h2><p class="sub">${esc(tables.map(t=>t.name).join(' + ')||'自定义 SQL')} · 最多选择 10 个字段</p><div class="field-grid">${fields.map(f=>`<label class="field-check"><input type="checkbox" data-field="${esc(f.id)}" ${builder.fields.includes(f.id)?'checked':''}><span>${esc(f.label)}<small>${esc(f.name)}</small></span></label>`).join('')}</div>${tables[0]?.fields.some(f=>f.name==='patient_id')?`<label class="settings-row" style="margin-top:20px"><span class="sub" style="margin:0">关联表（可选）</span><select id="join-table" aria-label="关联表"><option value="">不关联</option>${catalog.filter(t=>t.id!==builder.table&&t.fields.some(f=>f.name==='patient_id')).map(t=>`<option value="${esc(t.id)}" ${builder.join===t.id?'selected':''}>${esc(t.name)} · ${esc(t.source)}</option>`).join('')}</select></label>`:''}</section><section class="builder-card"><h2><span class="step-number">2</span>设置精确筛选</h2><p class="sub">至少填写一个条件；多个条件同时成立。数字和文本均可输入。</p><div id="filters">${builder.filters.map((f,i)=>`<div class="filter-row"><select data-filter-field="${i}" aria-label="第${i+1}个筛选字段">${options(f.field)}</select><span class="equal">等于</span><input class="field-input" data-filter-value="${i}" aria-label="第${i+1}个筛选值" placeholder="输入筛选值" value="${esc(f.value)}"><button class="remove" data-remove="${i}" aria-label="删除第${i+1}个筛选条件">×</button></div>`).join('')}</div><button class="button small" id="add-filter">${icon('plus')}添加条件</button></section><section class="builder-card"><h2><span class="step-number">3</span>结果设置</h2><p class="sub">按需要排序并限制返回条数，不足条数时返回实际记录。</p><div class="settings-row"><label>排序字段<select id="sort-field">${options(builder.order,true)}</select></label><label>排序方向<select id="sort-direction"><option value="desc" ${builder.direction==='desc'?'selected':''}>从大到小 / 从新到旧</option><option value="asc" ${builder.direction==='asc'?'selected':''}>从小到大 / 从旧到新</option></select></label><label>最多返回<select id="row-limit">${[1,2,5,10,50,100,200].map(n=>`<option ${n===builder.limit?'selected':''}>${n}</option>`).join('')}</select></label></div><details class="details" id="sql-details" ${builder.sqlOverride!==null?'open':''}><summary>查看或编辑 SQL</summary><textarea id="sql-editor" class="sql-editor" spellcheck="false" aria-label="查询 SQL">${esc(buildSQL())}</textarea><div class="sql-note"><span>支持目录字段、精确条件、已确认患者关联、排序和 LIMIT。</span><button id="regenerate-sql" class="button small">按可视化重新生成</button></div></details></section><div id="pro-error" role="alert"></div><div class="builder-footer"><small>准备查询只校验方案，不读取数据。</small><button id="preview-query" class="button primary" ${allowed('demand.create')?'':'disabled'}>${icon('arrow')}准备查询</button></div><div id="pro-preview"></div>`;
 const changed=()=>{builder.sqlOverride=null;$('#sql-editor').value=buildSQL();$('#pro-preview').replaceChildren();};
 document.querySelectorAll('[data-field]').forEach(el=>el.onchange=()=>{if(el.checked&&builder.fields.length>=10){el.checked=false;toast('最多选择10个字段',true);return;}builder.fields=el.checked?[...builder.fields,el.dataset.field]:builder.fields.filter(x=>x!==el.dataset.field);changed();});
 if($('#join-table'))$('#join-table').onchange=()=>{builder.join=$('#join-table').value;const ids=new Set(builderFields().map(f=>f.id));builder.fields=builder.fields.filter(id=>ids.has(id));builder.filters=builder.filters.filter(f=>ids.has(f.field));builder.sqlOverride=null;renderBuilder();};
 document.querySelectorAll('[data-filter-field]').forEach(el=>el.onchange=()=>{builder.filters[Number(el.dataset.filterField)].field=el.value;changed();});document.querySelectorAll('[data-filter-value]').forEach(el=>el.oninput=()=>{builder.filters[Number(el.dataset.filterValue)].value=el.value;changed();});document.querySelectorAll('[data-remove]').forEach(el=>el.onclick=()=>{builder.filters.splice(Number(el.dataset.remove),1);builder.sqlOverride=null;renderBuilder();});$('#add-filter').onclick=()=>{if(builder.filters.length>=20||!fields.length)return;builder.filters.push({field:fields[0].id,value:''});builder.sqlOverride=null;renderBuilder();};$('#sort-field').onchange=()=>{builder.order=$('#sort-field').value;changed();};$('#sort-direction').onchange=()=>{builder.direction=$('#sort-direction').value;changed();};$('#row-limit').onchange=()=>{builder.limit=Number($('#row-limit').value);changed();};$('#sql-editor').oninput=()=>{builder.sqlOverride=$('#sql-editor').value;$('#pro-preview').replaceChildren();};$('#regenerate-sql').onclick=()=>{builder.sqlOverride=null;$('#sql-editor').value=buildSQL();$('#pro-preview').replaceChildren();};
 button('preview-query',async()=>{if(builder.sqlOverride===null&&builder.filters.some(f=>!String(f.value).trim()))throw Error('请填写筛选值，或删除空白条件');const sql=buildSQL();if(!sql.trim())throw Error('请选择输出字段和筛选条件');$('#pro-error').replaceChildren();try{const r=await api('/api/portal/professional/preview',{sql});$('#pro-preview').innerHTML=`<div class="answer-card"><h2>查询已准备好</h2><p>从 ${esc(r.tables.join(' + '))} 按 ${r.filters.length} 个条件${r.calculation?'计算汇总值':'筛选，最多返回 '+r.max_rows+' 条'}。</p><div class="chips">${r.outputs.map(f=>`<span class="chip">${esc(f.label)}</span>`).join('')}</div><details class="details"><summary>核对查询 SQL</summary><pre>${esc(r.sql)}</pre></details><div class="actions"><button id="save-pro" class="button primary">确认意图并执行</button></div></div>`;button('save-pro',async()=>{const saved=await api('/api/portal/professional',{sql:r.sql,confirmed:true});const executed=await api('/api/portal/queries/'+encodeURIComponent(saved.flow.id)+'/execute',{expected_version:saved.flow.version,confirmed:true});await useFlow(executed.flow);});$('#pro-preview').scrollIntoView({behavior:'smooth',block:'nearest'});}catch(error){$('#pro-error').innerHTML=errorBox(error);throw error;}});
}
async function renderRoute(){clearTimeout(poll);const gen=++routeGeneration;busy=false;flow=null;delivery=null;const p=Object.hasOwn(names,page())?page():'home';setText('#page-label',names[p]);document.title=names[p]+' · 数据编织平台';document.querySelectorAll('#nav a').forEach(a=>{const active=a.dataset.page===p;a.classList.toggle('active',active);if(active)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current');});$('#main').innerHTML=loading();const id=new URL(location.href).searchParams.get('flow');try{if(id){await loadFlow(id,gen);}else if(p==='home')await home(gen);else if(p==='ask')ask(new URLSearchParams(location.search).get('question')||'');else if(p==='query')await professional(gen);}catch(error){if(gen===routeGeneration)$('#main').innerHTML=heading('暂时无法加载','你的查询记录仍保留在平台中。')+errorBox(error)+'<button class="button" id="retry-page">重试</button>';button('retry-page',renderRoute);}}
bindRole();
try{await ready();const principal=currentAccess().principal||{},name=principal.display_name||principal.username||'当前账户';setText('#identity',name);setText('#avatar',name.slice(0,1));if($('#connection'))$('#connection').innerHTML='<span class="status-dot"></span>工作空间已连接';await renderRoute();}catch(error){setText('#connection','连接暂不可用');$('#main').innerHTML=errorBox(error);}

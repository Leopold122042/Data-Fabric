import {enterPage,currentAccess,readable,allowed} from '/static/shared/access.js';
import {$,api,element,link,button,table,status} from '/static/platform/common.js';
import {label,modeNames,attention,filterOrders,recordUrl,orderedTimeline,messagePayload} from './work-order-model.js';

const base='/api/management/work-orders';
let orders=[],selected=null,detail=null,activeTab='timeline',requestVersion=0,serviceFilter=new URLSearchParams(location.search).get('service')||'';
const drafts=new Map();
const provider=()=>currentAccess().principal?.role==='provider';
function time(value){if(!value)return '—';const date=new Date(value);return Number.isNaN(date.getTime())?'—':date.toLocaleString('zh-CN',{hour12:false});}
function badge(text,state){return element('span',text,'wo-badge '+(Object.hasOwn({success:1,delivered:1,done:1,failed:1,attention:1,blocked:1,running:1,current:1,confirmed:1},state)?state:''));}
function paragraph(text,cls='wo-subtle'){return element('p',text,cls);}
function destination(kind,id,text){return readable(kind)?link(text||id,recordUrl(kind,id)):element('span',text||id);}
function heading(host,text){host.append(element('h3',text));}
function empty(host,text){host.append(paragraph(text,'wo-empty'));}
function setSelectedUrl(id){const url=new URL(location.href);if(id)url.searchParams.set('selected',id);else url.searchParams.delete('selected');history.replaceState(null,'',url);}

function renderList(){
 const rows=filterOrders(orders,{query:$('#order-search').value,filter:$('#order-filter').value,service:serviceFilter});
 $('#order-count').textContent=rows.length+' 张';
 const host=$('#order-list');host.replaceChildren();
 for(const row of rows){
  const card=element('button','','wo-card');card.type='button';card.setAttribute('aria-pressed',String(row.id===selected));
  card.append(element('span',row.title,'wo-card-title'));
  const meta=element('span','','wo-card-meta');meta.append(element('span',row.requester_name),badge(row.status_label,row.status));card.append(meta);
  card.append(element('span',row.id+' · '+time(row.updated_at),'wo-card-id'));
  if(attention(row)&&row.status!=='attention')card.append(element('span','有待关注事项','wo-card-id'));
  card.onclick=()=>selectOrder(row.id).catch(error=>status(error.message,true));host.append(card);
 }
 if(!rows.length)empty(host,'没有符合当前筛选条件的工单。');
 // Keep a detail only while its card remains in the currently visible collection.
 if(selected&&!rows.some(row=>row.id===selected)){
  ++requestVersion;selected=null;detail=null;setSelectedUrl(null);$('#order-detail').replaceChildren(paragraph('选择工单查看办理进度。','wo-empty'));
 }
 return rows;
}
function renderStats(counts){const host=$('#work-order-stats');host.replaceChildren();for(const [key,title,note] of [['total','全部工单','当前身份可见'],['active','办理中','申请至执行进展'],['attention','需要关注','异常或待处理事项'],['delivered','已交付','已生成交付凭证']]){const card=element('div','','wo-stat '+(key==='attention'?'warn':''));card.append(element('span',title),element('b',String(counts[key]||0)),element('span',note));host.append(card);}}

async function refresh(){
 const button=$('#order-refresh');button.disabled=true;status('正在更新工单进度…');
 try{
  const data=await api(base);orders=data.items;renderStats(data.counts);
  if(serviceFilter){const filter=$('#service-filter');filter.hidden=false;filter.className='wo-filter-note';filter.replaceChildren(element('span','按服务筛选：'+serviceFilter+' '),link('查看全部工单','/static/management/work-orders.html'));}
  const previous=selected||new URLSearchParams(location.search).get('selected'),rows=renderList();
  const target=rows.find(x=>x.id===previous)||(!previous?rows[0]:null);
  if(target)await selectOrder(target.id,false);
  else if(previous){++requestVersion;selected=null;detail=null;$('#order-detail').replaceChildren(paragraph('未找到可查看的工单，或此工单不在当前筛选范围内。','wo-empty'));}
  status('已更新 '+orders.length+' 张工单 · '+time(new Date().toISOString()));
 }finally{button.disabled=false;}
}
async function selectOrder(id,resetTab=true){
 const version=++requestVersion;selected=id;setSelectedUrl(id);if(resetTab)activeTab='timeline';
 renderList();if(selected!==id)return;
 $('#order-detail').replaceChildren(paragraph('正在读取工单进度…','wo-empty'));
 try{const data=await api(base+'/'+encodeURIComponent(id));if(version!==requestVersion)return;detail=data;renderDetail();if(resetTab&&matchMedia('(max-width:950px)').matches)$('#order-detail').scrollIntoView({behavior:'smooth',block:'start'});}
 catch(error){if(version===requestVersion){detail=null;$('#order-detail').replaceChildren(paragraph(error.message,'wo-empty'));}throw error;}
}
function renderDetail(){
 if(!detail)return;const d=detail,host=$('#order-detail');host.replaceChildren();
 const top=element('div','','wo-topline');top.append(element('span',d.id),badge(d.status_label,d.status));host.append(top,element('h2',d.title));
 host.append(paragraph('申请部门：'+d.requester_name+' · 最近更新：'+time(d.updated_at)),paragraph('用途：'+d.purpose));
 if(d.historical_scope)host.append(paragraph('历史参与范围：仅展示本方服务参与时的需求版本及相关记录。后续无关修订不在本方查看范围内。'));
 const next=element('div','','wo-next');next.append(element('b','下一步'),element('span',provider()?'查看本方服务进展，及时补充使用条件与质量处理说明。':d.next_action));host.append(next);
 const stages=element('div','','wo-stage-grid');for(const stage of d.stages){const card=element('div','','wo-stage '+stage.state);card.append(element('h3',stage.label),badge(label(stage.state),stage.state),paragraph(stage.detail));stages.append(card);}host.append(stages);
 const links=element('div','','wo-links');
 if(readable('demand'))links.append(destination('demand',d.id,'打开需求单'));
 const plan=d.plans.filter(p=>p.current).at(-1);if(plan&&readable('plan'))links.append(destination('plan',plan.id,'查看当前方案'));
 if(d.deliveries.length&&readable('delivery'))links.append(destination('delivery',d.deliveries.at(-1).id,'查看交付凭证'));
 if(d.feedback.length&&readable('feedback'))links.append(link('查看评价复测','/static/management/feedback.html'));
 if(links.childElementCount)host.append(links);
 heading(host,provider()?'本方服务使用':'关联服务');
 if(d.services.length)host.append(table(['服务 / 供给方','使用进展','调用结果'],d.services.map(s=>{
  const name=element('div');name.append(destination('service',s.id,s.name),paragraph(s.provider_name));
  return [name,s.usage_state,s.job_count+' 次调用 · 成功 '+s.success_count+' / 未完成 '+s.failed_count];
 })));
 else host.append(paragraph('当前没有可展示的关联目录服务。具体执行方式与数据范围以已确认方案为准。'));
 const tabs=element('div','','wo-tabs');tabs.setAttribute('role','tablist');tabs.setAttribute('aria-label','工单记录');
 const definitions=[['timeline','办理时间线'],...(!provider()?[['execution','执行与交付']]:[]),['quality','异常与反馈'+(d.counts.exceptions?' · '+d.counts.exceptions:'')],['messages','协作记录'+(d.messages.length?' · '+d.messages.length:'')]];
 if(!definitions.some(([key])=>key===activeTab))activeTab='timeline';
 const content=element('div');content.id='wo-tab-content';content.setAttribute('role','tabpanel');
 for(const [key,title] of definitions){const tab=button(title,()=>{activeTab=key;renderDetail();$('#wo-tab-'+key)?.focus();});tab.id='wo-tab-'+key;tab.setAttribute('role','tab');tab.setAttribute('aria-controls',content.id);tab.setAttribute('aria-selected',String(activeTab===key));tabs.append(tab);}
 content.setAttribute('aria-labelledby','wo-tab-'+activeTab);host.append(tabs,content);
 if(activeTab==='timeline')renderTimeline(content,d);
 if(activeTab==='execution')renderExecution(content,d);
 if(activeTab==='quality')renderQuality(content,d);
 if(activeTab==='messages')renderMessages(content,d);
}
function renderTimeline(host,d){
 const list=element('ol','','wo-timeline');
 for(const row of orderedTimeline(d.timeline)){const item=element('li'),date=element('time',time(row.time));date.dateTime=row.time;item.append(date,element('strong',row.title),paragraph(row.detail));list.append(item);}
 if(list.childElementCount)host.append(list);else empty(host,'暂未产生办理记录。');
}
function renderExecution(host,d){
 heading(host,'方案与版本');host.append(paragraph('重新确认、改版和重试分别保留，历史交付不会被当作当前版本完成。'));
 host.append(table(['方案','版本依据','状态'],d.plans.map(p=>[p.current?destination('plan',p.id,p.name):p.name,'方案 v'+p.version+' / 规格 v'+p.spec_version,label(p.status)+(p.current?' · 当前版本':' · 历史版本')])));
 heading(host,'运行任务');host.append(table(['任务','执行状态','关联 / 时间'],d.jobs.map(j=>[destination('job',j.id,j.id),label(j.state),(j.retry_of?'重试自 '+j.retry_of+' · ':'')+time(j.time)])));
 heading(host,'交付凭证');host.append(paragraph('这里展示交付记录。打开结果时，系统仍会核验当前身份、授权期限和允许的交付方式。'));
 host.append(table(['凭证','交付方式','状态 / 时间'],d.deliveries.map(r=>[destination('delivery',r.id,r.id),modeNames[r.mode]||r.mode,label(r.state)+' · '+time(r.time)])));
}
function renderQuality(host,d){
 if(allowed('feedback.create')){
  const targets=d.feedback_targets||[];
  if(targets.length){heading(host,'评价已调用的服务');host.append(table(['服务','运行时间','操作'],targets.map(target=>[target.service_name,time(target.time),link('评价此服务','/static/management/feedback.html?run_id='+encodeURIComponent(target.run_id)+'&service_id='+encodeURIComponent(target.service_id))])));}
 }
 heading(host,'异常及处理结果');
 for(const row of d.exceptions){const box=element('div','','wo-entry'),head=element('div','','wo-entry-head');head.append(element('b',row.title),badge(row.resolved?'已处理':'需要关注',row.resolved?'success':'attention'));box.append(head,paragraph(row.detail),paragraph(time(row.time)+' · '+label(row.status)));host.append(box);}
 if(!d.exceptions.length)host.append(paragraph('当前可见范围内没有异常记录。'));
 heading(host,'使用与质量反馈');
 const ratingNames={usability:'可用性',explainability:'可解释性',timeliness:'及时性',satisfaction:'满意度'};
 for(const row of d.feedback){const box=element('div','','wo-entry');box.append(paragraph(time(row.time)),paragraph(row.comment||'已提交评分'),paragraph(Object.entries(row.ratings).map(([key,value])=>(ratingNames[key]||key)+' '+value+'/5').join(' · ')));host.append(box);}
 if(!d.feedback.length)host.append(paragraph('尚未收到使用反馈。'));
 if(readable('feedback'))host.append(link('前往评价复测','/static/management/feedback.html'));
}
function renderMessages(host,d){
 heading(host,'协作记录');host.append(paragraph('按服务选择沟通范围，保留需求补充、使用条件与处理说明。办理状态随业务记录更新。'));
 for(const row of d.messages){const box=element('div','','wo-entry'),head=element('div','','wo-entry-head');head.append(element('b',row.actor_name),element('span',time(row.time)));box.append(head,paragraph(row.service_name?'服务协作 · '+row.service_name:'需求方与平台内部说明','wo-scope'),paragraph(row.body));host.append(box);}
 if(!d.messages.length)host.append(paragraph('暂无协作记录。'));
 if(!d.can_message)return;
 const scopes=[...(!provider()?[{id:'',name:'需求方与平台内部说明'}]:[]),...d.message_scopes];if(!scopes.length)return;
 const draft=drafts.get(d.id)||{body:'',service_id:scopes[0].id},form=element('form','','wo-composer');
 const scopeLabel=element('label','沟通范围'),select=element('select');select.id='message-scope';scopeLabel.htmlFor=select.id;
 for(const scope of scopes){const option=element('option',scope.name);option.value=scope.id;select.append(option);}select.value=scopes.some(s=>s.id===draft.service_id)?draft.service_id:scopes[0].id;scopeLabel.append(select);
 const hint=paragraph('','wo-message-hint');function audience(){hint.textContent=select.value?'此服务的供给方、需求方、平台和审计方可查看。请仅填写与该服务有关的说明。':'仅需求方、平台和审计方可查看，供给方不可见。';}audience();
 const bodyLabel=element('label','补充说明'),input=element('textarea');input.id='message-body';bodyLabel.htmlFor=input.id;input.maxLength=2000;input.required=true;input.placeholder='例如：已确认研究用途与覆盖范围，请核对服务更新时间。';input.value=draft.body;bodyLabel.append(input);
 const count=paragraph(input.value.length+' / 2,000 字','wo-message-hint'),error=paragraph('','error');error.setAttribute('role','alert');
 const saveDraft=()=>{drafts.set(d.id,{body:input.value,service_id:select.value});count.textContent=input.value.length+' / 2,000 字';};input.oninput=saveDraft;select.onchange=()=>{audience();saveDraft();};
 const submit=element('button','提交协作说明','mf-button mf-button-primary');submit.type='submit';
 form.append(scopeLabel,hint,bodyLabel,count,error,submit);form.onsubmit=async event=>{event.preventDefault();submit.disabled=true;error.textContent='';try{await api(base+'/'+encodeURIComponent(d.id)+'/messages',messagePayload(input.value,select.value,scopes));drafts.delete(d.id);if(selected===d.id){activeTab='messages';await refresh();}status('协作说明已保存。');}catch(e){error.textContent=e.message;}finally{submit.disabled=false;}};host.append(form);
}

async function main(){
 if(!await enterPage('work_order'))return;
 if(provider())$('#work-order-help').textContent='查看本方服务被谁申请、用于何种需求，以及调用进展、质量反馈与协作说明。';
 else if(currentAccess().principal?.role==='requester')$('#work-order-help').textContent='从申请到交付，集中跟踪自己的需求、执行进度、异常处理和服务协作。';
 if(readable('demand'))$('#work-order-actions').append(link('进入需求受理','/static/management/intake.html'));
 $('#order-search').oninput=renderList;$('#order-filter').onchange=renderList;
 $('#order-refresh').onclick=()=>refresh().catch(e=>status(e.message,true));
 await refresh();
}
main().catch(e=>status(e.message,true));

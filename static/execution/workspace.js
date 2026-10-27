import {executionLabels as labels,executionActions as actionRules,visibleExecutionTabs} from '/static/shared/access-model.js';
import {enterPage,headers,allowed,readable,currentAccess,showAccess,level} from '/static/shared/access.js';
import {action,form,show as showJson,$,esc} from '/static/management/ui.js';
import {record as renderRecord} from '/static/platform/common.js';
async function main(){
if(!await enterPage())return;
let kind=new URLSearchParams(location.search).get('tab')||'plan',chosen=null,records=[];

const statusNames={draft:'待确认',confirmed:'已确认',paused:'已暂停',invalidated:'已失效',queued:'排队中',running:'执行中',success:'成功',failed:'失败',blocked:'已阻断',cancelled:'已取消',active:'有效',revoked:'已撤销',available:'结果就绪',destroyed:'平台副本已清除'};
function show(value,title='详情'){
 showJson(value,title);let preview=document.querySelector('#execution-preview');if(!preview){preview=document.createElement('section');preview.id='execution-preview';$('#detail').before(preview);}preview.replaceChildren();$('#detail').hidden=false;
 const text=(tag,content)=>{const el=document.createElement(tag);el.textContent=content;preview.append(el);return el;};
 const table=(columns,rows)=>{const t=document.createElement('table');t.className='mf-table';const head=document.createElement('thead'),tr=document.createElement('tr');for(const c of columns){const th=document.createElement('th');th.textContent=c;tr.append(th);}head.append(tr);t.append(head);const body=document.createElement('tbody');for(const row of rows){const tr=document.createElement('tr');for(const cell of row){const td=document.createElement('td');td.textContent=typeof cell==='object'?JSON.stringify(cell):String(cell??'');tr.append(td);}body.append(tr);}t.append(body);const wrap=document.createElement('div');wrap.className='mf-table-wrap';wrap.append(t);preview.append(wrap);};
 const result=value?.result||(value?.columns?value:null);
 if(result?.columns&&result?.rows){text('h3','允许交付的结果');const names={age_group:'年龄组',category:'诊断类别',count:result.columns.includes('age_group')?'人数':'数量',group:'分组'};table(result.columns.map(c=>names[c]||c),result.rows);if(result.notice)text('p',result.notice);}
 if(value?.receipt){text('h3','交付凭证');text('p','方案版本：'+value.receipt.plan_version+' · 到期时间：'+new Date(value.receipt.expires_at).toLocaleString());text('p','六项复核：'+Object.entries(value.receipt.checks).map(([k,v])=>({format:'格式',quality:'质量',authorization:'授权',sensitive_information:'敏感信息',granularity:'粒度',subsequent_purpose:'后续用途'}[k]||k)+' '+(v?'通过':'未通过')).join(' · '));text('p','本地完整性凭证，尚无第三方存证。');}
 if(value?.timeline){text('h3','执行时间线');table(['阶段','节点','时间'],value.timeline.map(t=>[statusNames[t.stage]||({submitted:'提交',node_started:'节点开始',delivered:'交付'}[t.stage])||t.stage,t.node||'—',new Date(t.time).toLocaleString()]));if(value.error)text('p',value.error);}
 if(Array.isArray(value?.nodes)){text('h3','方案节点');table(['节点','操作','前置依赖'],value.nodes.map(n=>[n.id,{medical:'医疗汇总',view:'逻辑查询',service:'服务调用'}[n.operation]||n.operation,n.depends_on.join('、')||'无']));}
 if(!preview.childElementCount)preview.append(renderRecord(value));
 if(preview.childElementCount){$('#detail').hidden=true;const toggle=text('button','查看完整记录');toggle.className='mf-button';toggle.onclick=()=>{$('#detail').hidden=!$('#detail').hidden;toggle.textContent=$('#detail').hidden?'查看完整记录':'收起完整记录';};}
}

$('#page-title').textContent='执行与受控交付';$('#page-help').textContent='从已确认规格生成方案，确认版本后申请本地授权。任务执行和交付均重新检查授权；运行证据进入偏差校准与评价复测。';
$('#identity').textContent='本机身份 · 本地权限已启用';
async function api(path,body,base='/api/execution'){const r=await fetch(base+path,{method:body===undefined?'GET':'POST',headers:headers({'Content-Type':'application/json'}),...(body===undefined?{}:{body:JSON.stringify(body)})});const result=await r.json();if(!r.ok)throw Error(result.error||result.detail||'请求失败');return result;}
function message(value){$('#status').textContent=value;}
function need(){if(!chosen)throw Error('请先选择一条记录');return chosen;}

let actionButtons=[];
function refreshButtons(){actionButtons=actionButtons.filter(x=>x.button.isConnected);for(const item of actionButtons){item.button.hidden=!allowed(item.key,item.selected?chosen:null);item.button.disabled=!!item.selected&&!chosen;}}
function button(name,fn){const rule=actionRules[name];if(!rule)return;const b=document.createElement('button');b.type='button';b.className='mf-button';b.textContent=name;b.hidden=true;const [key,selected]=rule;actionButtons.push({button:b,key,selected});b.onclick=async()=>{if(!allowed(key,selected?chosen:null)||selected&&!chosen)return;b.disabled=true;try{await fn();}catch(e){message(e.message);}finally{refreshButtons();}};$('#actions').append(b);refreshButtons();}

async function submit(path,title,fields,initial={}){const value=await form(title,fields,initial);if(!value)return;const result=await api(path,{...initial,...value});await load();show(result);message('已保存');return result;}
async function load(){
 if(!readable(kind))throw Error('当前身份不能查看此目录');showAccess(kind);
 const data=await api('/items/'+kind);records=data.items;chosen=null;render();refreshButtons();message(`已加载 ${records.length} 条${labels[kind]}`);
}
function render(){
 const q=$('#search').value.toLowerCase();const visible=records.filter(r=>JSON.stringify(r).toLowerCase().includes(q)).sort((a,b)=>b.updated_at.localeCompare(a.updated_at));
 $('#list').innerHTML='<thead><tr><th>名称 / 编号</th><th>状态</th><th>版本 / 时间</th></tr></thead><tbody>'+visible.map(r=>`<tr><td><button class="mf-button mf-link" data-id="${esc(r.id)}">${esc(r.name||r.id)}</button></td><td>${esc(statusNames[r.state?.status||r.state||r.status]||r.state?.status||r.state||r.status||'已登记')}</td><td>v${r.version}<br>${esc(new Date(r.updated_at).toLocaleString())}</td></tr>`).join('')+'</tbody>';
 $('#empty').hidden=visible.length>0;$('#empty').textContent='暂无记录。可从已确认的需求规格创建方案，完成授权后提交执行。';
 $('#list').querySelectorAll('button').forEach(b=>b.onclick=()=>{chosen=records.find(r=>r.id===b.dataset.id);show(chosen);refreshButtons();});
}

if(!labels[kind])kind='plan';
async function selectTab(key){if(!readable(key))return;kind=key;chosen=null;const u=new URL(location.href);u.searchParams.set('tab',kind);history.replaceState(null,'',u);$('#page-title').textContent=labels[kind];document.title=labels[kind]+' · MedFabric';$('#actions').replaceChildren();actions();document.querySelectorAll('#subtabs button').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.kind===kind)));window.dispatchEvent(new Event('platform-navigation'));await load();}
for(const [key,label] of visibleExecutionTabs(currentAccess())){const b=document.createElement('button');b.type='button';b.className='mf-button';b.textContent=label+(level(currentAccess(),key)==='read'?' · 只读':'');b.dataset.kind=key;b.onclick=()=>selectTab(key).catch(e=>message(e.message));$('#subtabs').append(b);}

function actions(){
 if(kind==='plan'){
  button('新建医疗汇总方案',async()=>{const p=await api('/plans',{scenario:'medical',name:'门诊年龄与诊断类别汇总'});await load();show(p);message('已生成草案；请确认后授权执行。');});
  button('从需求规格生成方案',async()=>{
   const specs=await api('/items/spec',undefined,'/api/management');if(!specs.items.length)throw Error('请先在 需求受理页人工确认规格');
   const choice=await form('选择已确认的任务规格',[['spec_id','任务规格','select',specs.items.map(s=>[s.id,s.id+' · v'+s.version])]],{spec_id:new URLSearchParams(location.search).get('spec')||specs.items[0].id});if(!choice)return;
   const selected=specs.items.find(s=>s.id===choice.spec_id),rules=selected?.specification?.execution_constraints;if(!rules)throw Error('该规格尚无执行条件，请回需求受理页补充并重新确认');
   const modeLabels={controlled_compute:'受控查看',api:'授权API引用',download:'固定结果下载'};
   await submit('/plans','从确认规格生成方案',[
    ['name','方案名称'],['spec_id','任务规格','select',[[selected.id,selected.id+' · v'+selected.version]]],
    ['output_mode','已确认的输出粒度','select',[[rules.output_mode,rules.output_mode==='aggregate'?'汇总':'对象级']]],['circulation_mode','允许的交付方式','select',rules.circulation_modes.map(m=>[m,modeLabels[m]])],
    ['nodes','节点定义（留空数组时从规格服务集合生成）','json'],['quality_min_complete','非空单元格比例下限','number',[],1]
   ],{nodes:[],spec_id:selected.id,spec_version:selected.version,output_mode:rules.output_mode,circulation_mode:rules.circulation_modes[0],quality_min_complete:rules.quality_min_complete??1});
  });
  button('确认当前版本',async()=>{const p=need();show(await api('/plans/'+p.id+'/confirm',{version:p.version}));await load();});
  button('修改方案生成新版本',async()=>{const p=need();const value=await form('修改后需要重新确认和授权',[['name','名称'],['nodes','节点与依赖','json'],['output_mode','输出粒度','select',['aggregate','object']],['circulation_mode','交付方式','select',['controlled_compute','api','download']]],p);if(value){show(await api('/plans',{...p,...value,expected_version:p.version}));await load();}});
  button('签发本地授权',async()=>{const p=need();await submit('/grants','签发本地方案授权',[
   ['subject','主体','select',[['demo-platform','平台方'],['demo-requester','需求方']]],['expires_at','到期时间（带时区）'],['mode','允许粒度','select',['aggregate','object']],['contract_id','合作约定ID（可空）']
  ],{plan_id:p.id,plan_version:p.version,purpose:p.purpose,subject:p.created_by,expires_at:new Date(Date.now()+3600000).toISOString(),mode:p.output_mode});});
  button('执行当前方案',async()=>{const p=need();const grants=(await api('/items/grant')).items.filter(g=>g.plan_id===p.id&&g.plan_version===p.version&&g.status==='active'&&g.subject===currentAccess().principal.id);if(!grants.length)throw Error('当前版本尚无有效授权');const data=await form('提交执行',[['grant_id','授权','select',grants.map(g=>[g.id,g.id+' · '+g.subject])],['priority','优先级0—9','number',[],0]]);if(data){const job=await api('/plans/'+p.id+'/run',{...data,version:p.version});await selectTab('job');show(job);message('任务已入队，刷新可查看节点与时间线。');}});
  button('测试医疗明细导出拒绝',async()=>{await api('/plans/'+need().id+'/detail-export',{});});
  button('测试过期授权拒绝',async()=>{const p=need();if(p.scenario!=='medical')throw Error('请选择医疗汇总方案');const g=await api('/grants',{subject:'demo-platform',purpose:p.purpose,plan_id:p.id,plan_version:p.version,expires_at:new Date(Date.now()-1000).toISOString(),mode:'aggregate'});await api('/plans/'+p.id+'/run',{version:p.version,grant_id:g.id});});
  button('暂停后续节点',async()=>{const p=need();show(await api('/control',{command_id:crypto.randomUUID(),action:'pause',object_id:p.id}));await load();});
 }
 if(kind==='view')button('从目录字段建立视图',async()=>{await submit('/views','只读逻辑视图（字段ID可从数据目录复制）',[
  ['name','视图名称'],['fields','字段ID，逗号分隔','list'],['concept_ids','已确认映射概念ID，可空','list'],['joins','等值连接 [[左字段ID,右字段ID]]','json',[],[]],['filters','过滤 [{field,op,value}]','json',[],[]],['group_by','分组字段ID，逗号分隔','list'],['output_mode','粒度','select',['aggregate','object']]
 ],{fields:(new URLSearchParams(location.search).get('fields')||'').split(',').filter(Boolean),name:new URLSearchParams(location.search).get('source_name')||''});});
 if(kind==='job'){
  button('查看最新节点和时间线',async()=>{const job=await api('/jobs/'+need().id);chosen=job;show(job);});
  button('取消任务',async()=>{show(await api('/jobs/'+need().id+'/cancel',{}));await load();});
  button('重试失败任务',async()=>{const j=need();show(await api('/plans/'+j.plan_id+'/run',{version:j.plan_version,grant_id:j.grant_id,retry_of:j.id}));await load();});
  button('查看受控结果',async()=>{const j=need();if(!j.delivery_id)throw Error('该任务没有交付物');show(await api('/deliveries/'+j.delivery_id));});
 }
 if(kind==='grant'&&allowed('grant.create')){const a=document.createElement('a');a.className='mf-button';a.href='/static/platform/connections.html#external-governance';a.textContent='外部数据检测与授权';$('#actions').append(a);}
 if(kind==='grant')button('撤销授权',async()=>{show(await api('/grants/'+need().id+'/revoke',{}));await load();});
 if(kind==='delivery'){
  button('读取结果（重新核验授权）',async()=>show(await api('/deliveries/'+need().id)));
  button('读取授权 API 引用',async()=>show(await api('/deliveries/'+need().id+'/data')));
  button('校验凭证',async()=>{const d=need();show(await api('/receipts/verify',{receipt:d.receipt,signature:d.signature}));});
  button('下载已授权结果',async()=>{const d=need();const r=await fetch('/api/execution/deliveries/'+d.id+'/download',{headers:headers()});if(!r.ok){const data=await r.json();throw Error(data.error||'不能下载');}const url=URL.createObjectURL(await r.blob());const a=document.createElement('a');a.href=url;a.download=d.id+'.json';a.click();URL.revokeObjectURL(url);});
  button('清除平台结果副本',async()=>{const d=need();if(!confirm('清除平台保存的结果副本，凭证保留；已下载副本无法收回。'))return;show(await api('/deliveries/'+d.id+'/destroy',{}));await load();});
 }
 if(kind==='trigger'){
  button('登记触发器',()=>submit('/triggers','只对已确认且已授权方案启用',[['plan_id','方案ID'],['plan_version','方案版本','number',[],1],['grant_id','授权ID'],['kind','触发方式','select',['interval','metadata_change','task_completed']],['interval','定时间隔秒（至少5）','number',[],60],['source_plan','任务完成触发：上游方案ID']]));
  button('停用触发器',async()=>{show(await api('/triggers/'+need().id+'/disable',{}));await load();});
 }
 if(kind==='contract'){
  button('登记合作约定',()=>submit('/contracts','约定随授权传入每个执行点',[['plan_id','方案ID'],['parties','参与者ID，逗号分隔','list'],['expires_at','到期时间'],['obligations','义务说明','textarea']],{parties:['demo-platform','demo-provider'],expires_at:new Date(Date.now()+3600000).toISOString()}));
  button('以当前身份确认',async()=>{show(await api('/contracts/'+need().id+'/accept',{}));await load();});
 }
 if(kind==='facility'){
  button('登记本地设施',()=>submit('/facilities','设施登记（尚未接入远程调用）',[['name','设施名称'],['capabilities','能力，逗号分隔','list']]));
  button('发送心跳',async()=>{show(await api('/facilities/'+need().id+'/heartbeat',{}));await load();});
 }
}
$('#refresh').onclick=()=>load().catch(e=>message(e.message));$('#search').oninput=render;await selectTab(kind);
}
main().catch(error=>{const host=document.querySelector('#status');if(host)host.textContent=error.message;});

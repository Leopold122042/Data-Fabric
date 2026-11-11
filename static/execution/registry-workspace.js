import {executionActions as actionRules} from '/static/shared/access-model.js?v=identity-20261111';
import {enterPage,headers,allowed} from '/static/shared/access.js?v=identity-20261111';
import {form,$,esc} from '/static/management/ui.js?v=identity-20261111';

const mode=location.pathname.endsWith('/contracts.html')?'contract':'facility';
const config={
 contract:{title:'合作约定',help:'集中查看并登记合作约定，核对参与方、约定义务、确认状态与有效期。',empty:'暂无合作约定。可登记新的合作约定，并逐方确认约定义务与有效期。',detail:'选择一条合作约定查看详情。'},
 facility:{title:'设施管理',help:'集中查看并登记本地设施，核对设施能力、心跳状态和登记信息。',empty:'暂无设施记录。可登记本地设施，并在登记后发送心跳更新状态。',detail:'选择一条设施记录查看详情。'}
}[mode];
const roleNames={'demo-platform':'平台方','demo-provider':'供给方','demo-requester':'需求方'};
const statusNames={draft:'待确认',confirmed:'已确认',active:'有效',revoked:'已撤销',available:'结果就绪'};
let records=[],selected=null,actionButtons=[];

function api(path,body){return fetch('/api/execution'+path,{method:body===undefined?'GET':'POST',headers:headers({'Content-Type':'application/json'}),cache:'no-store',...(body===undefined?{}:{body:JSON.stringify(body)})}).then(async response=>{const data=await response.json();if(!response.ok)throw Error(data.error||data.detail||'请求失败');return data;});}
function message(text){$('#status').textContent=text;}
function date(value){if(!value)return '—';const parsed=new Date(value);return Number.isNaN(parsed.getTime())?String(value):parsed.toLocaleString();}
function names(values){return Array.isArray(values)&&values.length?values.map(value=>roleNames[value]||value).join('、'):'—';}
function confirmed(record){return Array.isArray(record.parties)&&Array.isArray(record.accepted)&&record.parties.every(party=>record.accepted.includes(party));}
function status(record){if(mode==='contract')return confirmed(record)?'已确认':'待确认';return record.heartbeat_status==='online'?'心跳在线':'心跳待更新';}
function isOk(record){return mode==='contract'?confirmed(record):record.heartbeat_status==='online';}

function refreshActions(){
 actionButtons=actionButtons.filter(item=>item.button.isConnected);
 for(const item of actionButtons){
  item.button.hidden=!allowed(item.key,item.selects?selected:null);
  item.button.disabled=!!item.selects&&!selected;
 }
}
function addAction(name,handler,selects=false){
 const rule=actionRules[name];if(!rule)return;
 const button=document.createElement('button');button.type='button';button.className='mf-button';button.textContent=name;
 const [key]=rule;actionButtons.push({button,key,selects});
 button.onclick=async()=>{
  if(!allowed(key,selects?selected:null)||selects&&!selected)return;
  button.disabled=true;
  try{await handler();}catch(error){message(error.message);}finally{refreshActions();}
 };
 $('#actions').append(button);refreshActions();
}
function buildActions(){
 $('#actions').replaceChildren();actionButtons=[];
 if(mode==='contract'){
  addAction('登记合作约定',registerContract);
  addAction('以当前身份确认',async()=>{await api('/contracts/'+selected.id+'/accept',{});await load();},true);
 }else{
  addAction('登记本地设施',registerFacility);
  addAction('发送心跳',async()=>{await api('/facilities/'+selected.id+'/heartbeat',{});await load();},true);
 }
}
async function registerContract(){
 const value=await form('登记合作约定',[
  ['plan_id','关联业务记录编号'],
  ['parties','参与方，逗号分隔','list'],
  ['expires_at','到期时间'],
  ['obligations','约定义务','textarea']
 ],{parties:['demo-platform','demo-provider'],expires_at:new Date(Date.now()+3600000).toISOString()});
 if(!value)return;
 const result=await api('/contracts',value);await load();selected=result;showDetail(result);message('合作约定已登记');
}
async function registerFacility(){
 const value=await form('登记本地设施',[['name','设施名称'],['capabilities','设施能力，逗号分隔','list']]);
 if(!value)return;
 const result=await api('/facilities',value);await load();selected=result;showDetail(result);message('设施已登记');
}
function renderSummary(){
 const host=$('#registry-summary');if(!host)return;
 const values=mode==='contract'
  ?[`${records.length} 条合作约定`,`${records.filter(confirmed).length} 条已确认`,`${records.filter(record=>!confirmed(record)).length} 条待确认`]
  :[`${records.length} 个设施`,`${records.filter(record=>record.heartbeat_status==='online').length} 个心跳在线`,`${records.filter(record=>record.heartbeat_status!=='online').length} 个待更新`];
 host.replaceChildren(...values.map(value=>{const item=document.createElement('span');item.textContent=value;return item;}));host.hidden=false;
}
function renderList(){
 const query=$('#search').value.trim().toLowerCase();
 const visible=records.filter(record=>JSON.stringify(record).toLowerCase().includes(query)).sort((a,b)=>b.updated_at.localeCompare(a.updated_at));
 if(mode==='contract'){
  $('#list').innerHTML='<thead><tr><th>名称 / 编号</th><th>参与与确认</th><th>状态</th><th>版本 / 时间</th></tr></thead><tbody>'+visible.map(record=>{
   const ok=confirmed(record);
   return `<tr><td><button class="mf-button mf-link" data-id="${esc(record.id)}">${esc(record.name||record.id)}</button><div class="registry-record-id">${esc(record.id)}</div></td><td>${(record.parties||[]).length} 方参与<br><span class="registry-muted">${(record.accepted||[]).length} 方已确认</span></td><td><span class="registry-status ${ok?'is-ok':'is-pending'}">${ok?'已确认':'待确认'}</span></td><td>v${record.version}<br>${esc(date(record.updated_at))}</td></tr>`;
  }).join('')+'</tbody>';
 }else{
  $('#list').innerHTML='<thead><tr><th>名称 / 编号</th><th>设施能力</th><th>心跳状态</th><th>版本 / 时间</th></tr></thead><tbody>'+visible.map(record=>{
   const ok=record.heartbeat_status==='online';
   return `<tr><td><button class="mf-button mf-link" data-id="${esc(record.id)}">${esc(record.name||record.id)}</button><div class="registry-record-id">${esc(record.id)}</div></td><td>${esc((record.capabilities||[]).join('、')||'未登记能力')}</td><td><span class="registry-status ${ok?'is-ok':'is-pending'}">${ok?'心跳在线':'心跳待更新'}</span></td><td>v${record.version}<br>${esc(date(record.updated_at))}</td></tr>`;
  }).join('')+'</tbody>';
 }
 $('#empty').hidden=visible.length>0;$('#empty').textContent=config.empty;
 $('#list').querySelectorAll('button[data-id]').forEach(button=>{
  button.closest('tr').classList.toggle('is-selected',button.dataset.id===selected?.id);
  button.onclick=()=>{selected=records.find(record=>record.id===button.dataset.id)||null;const url=new URL(location.href);if(selected)url.searchParams.set('selected',selected.id);else url.searchParams.delete('selected');history.replaceState(null,'',url);renderList();showDetail(selected);refreshActions();};
 });
}
function showDetail(record){
 const host=$('#detail');const title=$('#detail-title');title.textContent='详情';
 if(!record){host.textContent=config.detail;return;}
 const fields=mode==='contract'
  ?[['名称',record.name||record.id],['编号',record.id],['用途',record.purpose||'—'],['参与方',names(record.parties)],['已确认方',names(record.accepted)],['约定义务',record.obligations||'—'],['到期时间',date(record.expires_at)],['最近更新',date(record.updated_at)],['版本',record.version],['变更说明',record.change_reason||'—']]
  :[['名称',record.name||record.id],['编号',record.id],['设施能力',names(record.capabilities)],['心跳状态',status(record)],['最近心跳',date(record.last_seen)],['登记方',roleNames[record.created_by]||record.created_by||'—'],['最近更新',date(record.updated_at)],['版本',record.version],['变更说明',record.change_reason||'—']];
 const list=document.createElement('dl');list.className='record-grid';
 for(const [label,value] of fields){const term=document.createElement('dt');term.textContent=label;const detail=document.createElement('dd');detail.textContent=value==null?'—':String(value);list.append(term,detail);}
 host.replaceChildren(list);
}
async function load(){
 const data=await api('/items/'+mode);records=data.items||[];
 const selectedId=new URLSearchParams(location.search).get('selected');
 selected=selectedId?records.find(record=>record.id===selectedId)||null:null;
 renderSummary();renderList();showDetail(selected);refreshActions();
 message(mode==='contract'?`已加载 ${records.length} 条合作约定`:`已加载 ${records.length} 个设施`);
}
async function main(){
 if(!await enterPage(mode))return;
 $('#page-title').textContent=config.title;$('#page-help').textContent=config.help;document.title=config.title+' · 数据编织平台';
 $('#search').oninput=renderList;$('#refresh').onclick=()=>load().catch(error=>message(error.message));
 buildActions();await load();
}
main().catch(error=>{const host=document.querySelector('#status');if(host)host.textContent=error.message;});

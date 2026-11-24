import {ready,currentAccess,role,bindRole} from '/static/shared/access.js?v=0.1.45';
import {icon} from '/static/shared/icons.js?v=0.1.28';
import {renderKnowledgeGraph} from '/static/report/topic1.js?v=0.1.53';

const host=document.querySelector('#knowledge-graph');
const labels={platform:'平台管理员',requester:'业务用户',provider:'数据供给方',auditor:'审计人员'};
const esc=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const roleSelect=document.querySelector('#role');
const identity=document.querySelector('#identity');
const avatar=document.querySelector('#avatar');
const connection=document.querySelector('#connection');
const refresh=document.querySelector('#refresh');

document.querySelectorAll('[data-icon]').forEach(element=>element.replaceChildren(icon(element.dataset.icon)));
bindRole();
refresh?.addEventListener('click',()=>renderKnowledgeGraph(host));

try{
 await ready();
 const current=labels[currentAccess().principal?.role||role()]||'当前用户';
 if(roleSelect)roleSelect.value=role();
 if(identity)identity.textContent=current;
 if(avatar)avatar.textContent=current.slice(0,1);
 if(connection)connection.innerHTML='<span class="status-dot"></span>工作空间已连接';
 renderKnowledgeGraph(host);
}catch(error){
 if(connection)connection.textContent='连接暂不可用';
 if(host)host.innerHTML=`<div class="notice error" role="alert">${esc(error.message||error)}</div>`;
}

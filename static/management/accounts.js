import {ready} from '/static/shared/access.js?v=0.1.45';
const $=selector=>document.querySelector(selector);
const esc=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const levelNames={1:'1级 · 查询使用者',2:'2级 · 范围分析者',3:'3级 · 数据管理者',4:'4级 · 平台管理员'};
const auditNames={'login.succeeded':'登录成功','login.failed':'登录失败','logout':'退出登录',
 'password.changed':'密码已修改','password.reset':'密码已重置','account.created':'账户已建立','account.updated':'账户权限已调整'};
const levelText={1:'限定5张表；可运行查询，只返回达到最小样本数的记录数。',
 2:'限定11张表；可查看授权范围内的脱敏明细。',
 3:'全部13张表；可查看脱敏明细和跨表关联。',
 4:'全部13张表及原始字段；可查看完整数据并管理账户。'};
const levelGuide=$('#level-guide');
levelGuide.innerHTML=Object.entries(levelNames).map(([level,name])=>`<article class="level-card"><strong>${esc(name)}<span>等级 ${level}</span></strong><p>${esc(levelText[level])}</p></article>`).join('');

async function api(path,options={}){
 const response=await fetch(path,{credentials:'same-origin',cache:'no-store',...options,
  headers:{...(options.body?{'Content-Type':'application/json'}:{}),...(options.headers||{})}});
 let data={};try{data=await response.json();}catch{}
 if(!response.ok)throw Error(data.error||'请求未完成');return data;
}
function setMessage(message,isError=false){const el=$('#create-message');el.textContent=message;el.classList.toggle('error',isError);}
function row(item){
 const status=item.enabled?'启用中':'已停用';
 return `<div class="account-row" data-account="${esc(item.username)}">
  <div class="account-name"><strong>${esc(item.display_name)}</strong><small>${esc(item.username)}${item.must_change_password?' · 首次登录需改密':''}</small></div>
  <div><select class="level-select" aria-label="${esc(item.username)}权限等级">${[1,2,3,4].map(n=>`<option value="${n}" ${item.level===n?'selected':''}>${esc(levelNames[n])}</option>`).join('')}</select></div>
  <div class="account-status ${item.enabled?'':'off'}">${status}</div>
  <div class="account-status">${item.last_login?esc(new Date(item.last_login).toLocaleString()):'尚未登录'}</div>
  <div class="account-actions"><button type="button" data-action="save">保存</button><button type="button" data-action="reset">重置密码</button><button type="button" data-action="toggle" class="${item.enabled?'danger':''}">${item.enabled?'停用':'启用'}</button></div>
 </div>`;
}
async function loadAudit(){
 const host=$('#account-audit');
 try{
  const data=await api('/api/accounts/audit?limit=200');
  host.innerHTML=data.events.length?'<div class="account-row audit-head"><span>时间</span><span>操作</span><span>操作者</span><span>目标</span><span>详情</span></div>'+data.events.map(event=>
   `<div class="account-row audit-row"><span>${esc(new Date(event.occurred_at).toLocaleString())}</span><span>${esc(auditNames[event.action]||event.action)}</span><span>${esc(event.actor||'—')}${event.actor_level?` · ${event.actor_level}级`:''}</span><span>${esc(event.target||'—')}</span><span>${esc(JSON.stringify(event.details||{}))}</span></div>`).join(''):'<p class="empty">暂无账户审计记录。</p>';
 }catch(error){host.innerHTML=`<p class="empty">${esc(error.message)}</p>`;}
}
async function load(){
 const host=$('#accounts-list');host.innerHTML='<p class="loading">正在读取账户…</p>';
 try{
  const data=await api('/api/accounts');
  host.innerHTML='<div class="account-row head"><span>账户</span><span>权限等级</span><span>状态</span><span>最近登录</span><span>操作</span></div>'+
   (data.accounts.length?data.accounts.map(row).join(''):'<p class="empty">还没有账户。</p>');
  host.querySelectorAll('[data-action="save"]').forEach(button=>button.onclick=async()=>{
   const item=button.closest('[data-account]'),username=item.dataset.account,level=Number(item.querySelector('.level-select').value);
   button.disabled=true;try{await api('/api/accounts/'+encodeURIComponent(username),{method:'PATCH',body:JSON.stringify({level})});$('#connection').textContent='权限已保存';await load();}
   catch(error){$('#connection').textContent=error.message;button.disabled=false;}
  });
  host.querySelectorAll('[data-action="toggle"]').forEach(button=>button.onclick=async()=>{
   const item=button.closest('[data-account]'),username=item.dataset.account,enabled=button.textContent==='启用';
   button.disabled=true;try{await api('/api/accounts/'+encodeURIComponent(username),{method:'PATCH',body:JSON.stringify({enabled})});$('#connection').textContent='账户状态已保存';await load();}
   catch(error){$('#connection').textContent=error.message;button.disabled=false;}
  });
  host.querySelectorAll('[data-action="reset"]').forEach(button=>button.onclick=async()=>{
   const item=button.closest('[data-account]'),username=item.dataset.account;
   const password=prompt(`为 ${username} 设置临时密码（10—128位）。用户下次登录时需要修改。`);
   if(password===null)return;
   button.disabled=true;try{await api('/api/accounts/'+encodeURIComponent(username)+'/reset-password',{method:'POST',body:JSON.stringify({password})});$('#connection').textContent='临时密码已重置';await load();}
   catch(error){$('#connection').textContent=error.message;button.disabled=false;}
  });
  $('#connection').textContent=`共 ${data.accounts.length} 个账户`;
  await loadAudit();
 }catch(error){host.innerHTML=`<p class="empty">${esc(error.message)}</p>`;$('#connection').textContent='账户读取失败';}
}
$('#create-form').addEventListener('submit',async event=>{
 event.preventDefault();const form=new FormData(event.currentTarget);const button=event.currentTarget.querySelector('button');button.disabled=true;setMessage('正在创建账户…');
 try{
  await api('/api/accounts',{method:'POST',body:JSON.stringify({username:form.get('username'),display_name:form.get('display_name'),password:form.get('password')})});
  event.currentTarget.reset();setMessage('账户已创建，初始等级为1。请将临时密码安全地交给该用户。');await load();
 }catch(error){setMessage(error.message,true);}finally{button.disabled=false;}
});
$('#refresh').addEventListener('click',load);
const access=await ready();
if(access.principal?.level!==4){$('#accounts-list').innerHTML='<p class="empty">当前账户不是4级管理员。</p>';throw Error('只有4级管理员可以管理账户。');}
load();

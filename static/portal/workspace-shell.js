import {loginURL} from '/static/shared/workspaces.js?v=0.1.46';
import {icon} from '/static/shared/icons.js?v=0.1.50';

const groups=[
 {label:'工作台',links:[
  ['home','总览','/user?page=home','grid'],
 ]},
 {label:'数据资源',links:[
  ['services','数据服务','/user/services','database'],
  ['graph','数据世界','/static/portal/knowledge-graph.html','network'],
 ]},
 {label:'查询方式',links:[
  ['ask','自然语言查询','/user?page=ask','spark'],
  ['query','专业查询','/user?page=query','sliders'],
 ]},
];
const path=location.pathname;
const page=new URLSearchParams(location.search).get('page')||'home';
const active=path==='/user/services'?'services':path.endsWith('/knowledge-graph.html')?'graph':page;
const aside=document.querySelector('.sidebar');
const sidebarPreference='medfabric.user.sidebar.collapsed';
if(aside){
 aside.setAttribute('aria-label','主导航');
 aside.innerHTML=`
  <div class="sidebar-heading">
   <a class="brand" data-full-navigation href="/user" aria-label="数据编织平台">
    <span class="brand-mark" aria-hidden="true"><i></i><i></i><i></i><i></i></span>
    <span class="sidebar-brand-name">数据编织平台<small>DATA WORKSPACE</small></span>
   </a>
   <button class="sidebar-toggle" type="button" aria-controls="nav"></button>
  </div>
  <nav id="nav" aria-label="工作空间导航">
   ${groups.map(group=>`
    <div class="nav-group" role="group" aria-label="${group.label}">
     <p class="nav-group-title">${group.label}</p>
     <div class="nav-group-links">
      ${group.links.map(([id,label,url,ico])=>`
       <a href="${url}" data-page="${id}" data-full-navigation aria-label="${label}" class="${active===id?'active':''}" ${active===id?'aria-current="page"':''}>
        <span data-icon="${ico}"></span><span class="sidebar-nav-label">${label}</span>${id==='ask'?'<span class="nav-tag">AI</span>':''}
       </a>
      `).join('')}
     </div>
    </div>
   `).join('')}
  </nav>
  <div class="sidebar-bottom">
   <div class="workspace-note"><span class="status-dot"></span>你的数据，清晰可用<p>从一个问题，到一个答案。</p></div>
   <button class="logout-button" id="logout-button" type="button" aria-label="退出登录"><span data-icon="logout"></span><span class="sidebar-logout-label">退出登录</span></button>
   <div class="profile"><span class="avatar" id="avatar">用</span><div><strong id="identity">账户</strong><small id="identity-scope">正在读取账户权限…</small></div></div>
  </div>`;
 aside.querySelectorAll('[data-icon]').forEach(el=>el.replaceChildren(icon(el.dataset.icon)));
 const toggle=aside.querySelector('.sidebar-toggle');
 const titledItems=aside.querySelectorAll('.brand, #nav a, .logout-button');
 function setSidebarCollapsed(collapsed){
  document.body.classList.toggle('sidebar-collapsed',collapsed);
  const label=collapsed?'展开导航栏':'收起导航栏';
  toggle.setAttribute('aria-expanded',String(!collapsed));
  toggle.setAttribute('aria-label',label);
  toggle.title=label;
  toggle.replaceChildren(icon(collapsed?'sidebarOpen':'sidebarClose'));
  titledItems.forEach(item=>{
   if(collapsed)item.title=item.getAttribute('aria-label');
   else item.removeAttribute('title');
  });
 }
 function restoreSidebar(){
  try{setSidebarCollapsed(localStorage.getItem(sidebarPreference)==='1');}
  catch{}
 }
 setSidebarCollapsed(false);
 restoreSidebar();
 toggle.addEventListener('click',()=>{
  const collapsed=!document.body.classList.contains('sidebar-collapsed');
  setSidebarCollapsed(collapsed);
  try{localStorage.setItem(sidebarPreference,collapsed?'1':'0');}
  catch{}
 });
 window.addEventListener('pageshow',event=>{if(event.persisted)restoreSidebar();});
 window.addEventListener('storage',event=>{
  if(event.storageArea===localStorage&&(event.key===sidebarPreference||event.key===null))restoreSidebar();
 });
}

document.querySelector('#role')?.closest('label')?.remove();
const topbar=document.querySelector('.topbar-right');
if(topbar){const badge=document.createElement('span');badge.className='permission-pill';badge.id='permission-pill';badge.textContent='核对账户权限…';topbar.prepend(badge);}

fetch('/api/access',{credentials:'same-origin',cache:'no-store'}).then(async response=>{
 if(response.status===401){location.replace(loginURL(location));return null;}
 let data={};try{data=await response.json();}catch{}
 if(!response.ok)throw Error(data.error||'无法读取账户权限');return data;
}).then(data=>{
 if(!data)return;
 const principal=data.principal||{},level=Number(principal.level||1),tables=principal.dataset_tables||[];
 const text=`${level}级 · ${tables[0]==='全部13张表'?'全部13张表':tables.length+'张表'} · ${principal.visibility||'按等级控制查看范围'}`;
 const name=principal.display_name||principal.username||'当前账户';
 const identity=document.querySelector('#identity'),avatar=document.querySelector('#avatar'),scope=document.querySelector('#identity-scope');
 if(identity)identity.textContent=name;if(avatar)avatar.textContent=name.slice(0,1);if(scope)scope.textContent=text;
 const badge=document.querySelector('#permission-pill');if(badge){badge.textContent=text;badge.title=`当前账户：${name}；数据范围：${tables.join('、')}`;}
}).catch(error=>{const badge=document.querySelector('#permission-pill');if(badge)badge.textContent=error.message;});

document.querySelector('#logout-button')?.addEventListener('click',async()=>{
 try{await fetch('/api/auth/logout',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'}});}finally{location.replace(loginURL(location,false));}
});

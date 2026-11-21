import {icon} from '/static/shared/icons.js?v=0.1.28';

const groups=[
 {label:'工作台',links:[
  ['home','总览','/static/portal/index.html?page=home','grid'],
  ['cockpit','驾驶舱总览','/static/portal/cockpit.html','chart'],
 ]},
 {label:'数据资源',links:[
  ['services','数据服务','/static/portal/services.html','database'],
  ['graph','知识图谱','/static/portal/knowledge-graph.html','network'],
 ]},
 {label:'查询方式',links:[
  ['ask','自然语言查询','/static/portal/index.html?page=ask','spark'],
  ['query','专业查询','/static/portal/index.html?page=query','sliders'],
 ]},
];
const path=location.pathname;
const page=new URLSearchParams(location.search).get('page')||'home';
const active=path.endsWith('/cockpit.html')?'cockpit':path.endsWith('/services.html')?'services':path.endsWith('/knowledge-graph.html')?'graph':page;
const aside=document.querySelector('.sidebar');
if(aside){
 aside.setAttribute('aria-label','主导航');
 aside.innerHTML=`
  <a class="brand" data-full-navigation href="/static/portal/index.html">
   <span class="brand-mark" aria-hidden="true"><i></i><i></i><i></i><i></i></span>
   <span>数据编织平台<small>DATA WORKSPACE</small></span>
  </a>
  <nav id="nav" aria-label="工作空间导航">
   ${groups.map(group=>`
    <div class="nav-group" role="group" aria-label="${group.label}">
     <p class="nav-group-title">${group.label}</p>
     <div class="nav-group-links">
      ${group.links.map(([id,label,url,ico])=>`
       <a href="${url}" data-page="${id}" data-full-navigation class="${active===id?'active':''}" ${active===id?'aria-current="page"':''}>
        <span data-icon="${ico}"></span>${label}${id==='ask'?'<span class="nav-tag">AI</span>':''}
       </a>
      `).join('')}
     </div>
    </div>
   `).join('')}
  </nav>
  <div class="sidebar-bottom">
   <div class="workspace-note"><span class="status-dot"></span>你的数据，清晰可用<p>从一个问题，到一个答案。</p></div>
   <a class="admin-link" id="admin-link" data-full-navigation href="/static/portal/index.html?page=admin" hidden><span data-icon="settings"></span>管理入口<span>↗</span></a>
   <a class="admin-link" id="accounts-link" data-full-navigation href="/static/portal/accounts.html" hidden><span data-icon="settings"></span>账户权限<span>↗</span></a>
   <button class="logout-button" id="logout-button" type="button">退出登录</button>
   <div class="profile"><span class="avatar" id="avatar">用</span><div><strong id="identity">账户</strong><small id="identity-scope">正在读取账户权限…</small></div></div>
  </div>`;
 aside.querySelectorAll('[data-icon]').forEach(el=>el.replaceChildren(icon(el.dataset.icon)));
}

document.querySelector('#role')?.closest('label')?.remove();
const topbar=document.querySelector('.topbar-right');
if(topbar){const badge=document.createElement('span');badge.className='permission-pill';badge.id='permission-pill';badge.textContent='核对账户权限…';topbar.prepend(badge);}

fetch('/api/access',{credentials:'same-origin',cache:'no-store'}).then(async response=>{
 if(response.status===401){location.replace('/static/portal/login.html');return null;}
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
 const admin=document.querySelector('#admin-link');if(admin)admin.hidden=level!==4;
 const accountLink=document.querySelector('#accounts-link');if(accountLink)accountLink.hidden=level!==4;
}).catch(error=>{const badge=document.querySelector('#permission-pill');if(badge)badge.textContent=error.message;});

document.querySelector('#logout-button')?.addEventListener('click',async()=>{
 try{await fetch('/api/auth/logout',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'}});}finally{location.replace('/static/portal/login.html');}
});

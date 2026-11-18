import {icon} from '/static/shared/icons.js?v=0.1.25';

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
const role=localStorage.getItem('mf-demo-role')||'platform';
const roleLabels={platform:'平台管理员',requester:'业务用户',provider:'数据供给方',auditor:'审计人员'};
const roleLabel=roleLabels[role]||'当前用户';

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
   <a class="admin-link" data-full-navigation href="/static/portal/index.html?page=admin"><span data-icon="settings"></span>管理入口<span>↗</span></a>
   <div class="profile"><span class="avatar" id="avatar">${roleLabel[0]}</span><div><strong id="identity">${roleLabel}</strong><small>当前工作空间</small></div></div>
  </div>`;
 aside.querySelectorAll('[data-icon]').forEach(el=>el.replaceChildren(icon(el.dataset.icon)));
}

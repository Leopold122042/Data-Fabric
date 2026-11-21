import {ready,currentAccess,bindRole} from '/static/shared/access.js?v=0.1.28';
const pageLabel=document.querySelector('#page-label');if(pageLabel)pageLabel.textContent='驾驶舱总览';
document.querySelector('a[href="/static/portal/cockpit.html"]').classList.add('active');
document.querySelector('a[href="/static/portal/cockpit.html"]').setAttribute('aria-current','page');
bindRole();
try {
 await ready();
 const principal=currentAccess().principal||{},name=principal.display_name||principal.username||'当前账户',level=Number(principal.level||1);
 const identity=document.querySelector('#identity');if(identity)identity.textContent=name;
 const avatar=document.querySelector('#avatar');if(avatar)avatar.textContent=name.slice(0,1);
 const adminLink=document.querySelector('#admin-link');if(adminLink)adminLink.hidden=level!==4;
 const connection=document.querySelector('#connection');if(connection)connection.textContent='工作空间已连接';
 await import('/static/dashboard.js?v=20261117-fix4');
 document.title='驾驶舱总览 · 数据编织平台';
 document.querySelector('#refresh').onclick=()=>document.querySelector('#dashboard-refresh').click();
} catch(error) {const status=document.querySelector('#dashboard-status');if(status)status.textContent=error.stack||error.message;}

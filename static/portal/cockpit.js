import {ready,currentAccess,role,bindRole} from '/static/shared/access.js?v=identity-20261111';
import {icon} from '/static/shared/icons.js';
document.querySelectorAll('[data-icon]').forEach(el=>el.replaceChildren(icon(({spark:'activity',sliders:'layers',settings:'shield'})[el.dataset.icon]||el.dataset.icon)));
const pageLabel=document.querySelector('#page-label');if(pageLabel)pageLabel.textContent='驾驶舱总览';
document.querySelector('a[href="/static/portal/cockpit.html"]').classList.add('active');
document.querySelector('a[href="/static/portal/cockpit.html"]').setAttribute('aria-current','page');
bindRole();
try {
 await ready();
 const labels={platform:'平台管理员',requester:'业务用户',provider:'数据供给方',auditor:'审计人员'};
 const identity=document.querySelector('#identity');if(identity)identity.textContent=labels[role()]||'当前用户';
 const avatar=document.querySelector('#avatar');if(avatar)avatar.textContent=(labels[role()]||'用户')[0];
 const adminLink=document.querySelector('#admin-link');if(adminLink)adminLink.hidden=false;
 const connection=document.querySelector('#connection');if(connection)connection.textContent='工作空间已连接';
 await import('/static/dashboard.js?v=20261117-fix4');
 document.title='驾驶舱总览 · 数据编织平台';
 document.querySelector('#refresh').onclick=()=>document.querySelector('#dashboard-refresh').click();
} catch(error) {const status=document.querySelector('#dashboard-status');if(status)status.textContent=error.stack||error.message;}

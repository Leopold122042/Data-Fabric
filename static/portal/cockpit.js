import {ready,currentAccess,role} from '/static/shared/access.js';
import {icon} from '/static/shared/icons.js';
document.querySelectorAll('[data-icon]').forEach(el=>el.replaceChildren(icon(({spark:'activity',sliders:'layers',settings:'shield'})[el.dataset.icon]||el.dataset.icon)));
document.querySelector('#page-label').textContent='驾驶舱总览';
document.querySelector('a[href="/static/portal/cockpit.html"]').classList.add('active');
document.querySelector('a[href="/static/portal/cockpit.html"]').setAttribute('aria-current','page');
try {
 await ready();
 const labels={platform:'平台管理员',requester:'业务用户',provider:'数据供给方',auditor:'审计人员'};
 document.querySelector('#identity').textContent=labels[role()]||'当前用户';
 document.querySelector('#avatar').textContent=(labels[role()]||'用户')[0];
 document.querySelector('#admin-link').hidden=!['platform','auditor'].includes(role());
 document.querySelector('#connection').textContent='工作空间已连接';
 await import('/static/dashboard.js');
 document.title='驾驶舱总览 · 数据编织平台';
 document.querySelector('#refresh').onclick=()=>document.querySelector('#dashboard-refresh').click();
} catch(error) {document.querySelector('#dashboard-status').textContent=error.message;}

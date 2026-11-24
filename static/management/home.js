import {currentAccess, enterPage, readable} from '/static/shared/access.js?v=0.1.45';

const tools = [
 ['驾驶舱总览','运行统计、状态与证据','/static/management/cockpit.html','overview'],
 ['数据源连接','管理源连接并更新目录','/static/platform/connections.html','connection'],
 ['资源目录','查看数据源及其逻辑表','/static/platform/resources.html','object'],
 ['字段库','维护字段定义及分类','/static/management/field-library.html','object'],
 ['需求受理','澄清规格、处理申请与审批','/static/management/intake.html','demand'],
 ['协作工单','跟踪需求和服务使用进度','/static/management/work-orders.html','work_order'],
 ['执行与交付','管理方案、任务与交付','/static/execution/index.html?tab=plan','plan'],
 ['运行证据','核查任务执行记录','/static/execution/index.html?tab=run','run'],
 ['风险监测','查看安全风险与授权核验','/static/platform/security.html','risk'],
 ['偏差校准','定位异常并跟踪校准结果','/static/management/reconciliation.html','case']
];

function entry(title, note, href) {
 const link=document.createElement('a');
 link.href=href;
 const name=document.createElement('strong');
 const description=document.createElement('span');
 name.textContent=title;
 description.textContent=note;
 link.append(name,description);
 return link;
}

if (await enterPage('overview')) {
 const host=document.querySelector('#admin-tools');
 for(const [title,note,href,resource] of tools) {
  if(readable(resource))host.append(entry(title,note,href));
 }
 if(currentAccess().principal?.can_manage_accounts)host.append(entry('账户权限','创建账户、分配等级及查看审计','/admin/accounts'));
 if(readable('demand')){
  const demands=document.querySelector('#admin-demands');
  document.querySelector('#admin-recent').hidden=false;
  demands.textContent='正在读取需求记录…';
  try{
   const response=await fetch('/api/management/items/demand',{credentials:'same-origin',cache:'no-store'});
   const data=await response.json();
   if(!response.ok)throw Error(data.error||'无法读取需求记录');
   const recent=[...data.items].sort((a,b)=>String(b.updated_at).localeCompare(String(a.updated_at))).slice(0,6);
   demands.replaceChildren(...recent.map(item=>entry(item.title||item.id,
    item.state==='confirmed'?'已确认规格':'查看需求进度','/static/management/intake.html?selected='+encodeURIComponent(item.id))));
   if(!recent.length)demands.textContent='暂无需求记录。';
  }catch(error){demands.textContent=error.message;}
 }
}

/* One platform navigation shared by resource, workflow and external pages. */
(() => {
 const path=location.pathname;
 if((path==='/'||path==='/static/index.html')&&!location.hash){location.replace('/static/workspace.html');return;}
 const groups=[
  ['平台',[['总览','/static/workspace.html']]],
  ['数据资源',[['外部数据接入','/static/platform/connections.html'],['资源目录','/static/platform/resources.html'],['概念映射','/static/management/semantic.html'],['语义图谱','/static/platform/graph.html'],['查询中心','/static/platform/query.html']]],
  ['需求与服务',[['需求受理','/static/management/intake.html'],['服务目录与发布','/static/management/services.html'],['元数据维护','/static/management/catalog.html']]],
  ['执行与交付',[['执行方案','plan'],['逻辑视图','view'],['执行流程','workflow'],['运行任务','job'],['触发调度','trigger'],['结果与凭证','delivery']]],
  ['安全与治理',[['访问授权','grant'],['授权核验','decision'],['运行证据','run'],['偏差校准','/static/management/reconciliation.html'],['评价复测','/static/management/feedback.html'],['合作约定','contract'],['设施管理','facility']]],
  ['运行观测',[['元数据事件','/static/platform/events.html'],['自动化规则','/static/platform/rules.html'],['风险监测','/static/platform/security.html'],['平台说明','/static/platform/about.html']]]
 ];
 let sidebar=document.querySelector('.mf-sidebar');
 if(!sidebar)return;
 sidebar.classList.add('platform-sidebar');sidebar.setAttribute('aria-label','平台导航');
 const brand=document.createElement('a');brand.className='platform-brand';brand.href='/static/workspace.html';brand.textContent='MedFabric';sidebar.append(brand);
 const caption=document.createElement('p');caption.className='platform-caption';caption.textContent='医疗数据平台';sidebar.append(caption);
 for(const [name,items] of groups){const group=document.createElement('details');group.open=true;const title=document.createElement('summary');title.textContent=name;group.append(title);const nav=document.createElement('nav');nav.className='platform-nav';for(const [label,target] of items){const a=document.createElement('a');a.textContent=label;a.href=target.startsWith('/')?target:'/static/execution/index.html?tab='+target;nav.append(a);}group.append(nav);sidebar.append(group);}
 function highlight(){const current=new URL(location.href);sidebar.querySelectorAll('nav a').forEach(a=>{const u=new URL(a.href);const same=(u.pathname===current.pathname||(current.pathname==='/'&&u.pathname==='/static/index.html'))&&u.hash.split('?')[0]===current.hash.split('?')[0]&&(u.searchParams.get('tab')||'plan')===(current.searchParams.get('tab')||'plan');a.classList.toggle('is-active',same);if(same)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current');});}
 highlight();window.addEventListener('platform-navigation',highlight);
 window.addEventListener('hashchange',highlight);
 const content=document.querySelector('.mf-content');
 const source=new URLSearchParams(location.search).get('source_name');if(content&&source){const context=document.createElement('p');context.className='management-status';context.textContent='当前资源：'+source+'。新建表单已带入来源信息，请补充业务定义。';content.prepend(context);}

 if(content&&path!=='/static/workspace.html'){
  const steps=document.createElement('nav');steps.className='platform-journey';steps.setAttribute('aria-label','业务流程');
  for(const [label,href] of [['接入与目录','/static/platform/resources.html'],['需求受理','/static/management/intake.html'],['方案与执行','/static/execution/index.html?tab=plan'],['结果交付','/static/execution/index.html?tab=delivery'],['治理反馈','/static/management/reconciliation.html']]){const a=document.createElement('a');a.href=href;a.textContent=label;steps.append(a);}content.prepend(steps);
 }
})();

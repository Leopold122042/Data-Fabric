/* One permission-aware navigation shared by platform pages. */
(async () => {
 const path=location.pathname;
 if((path==='/'||path==='/static/index.html')&&!location.hash){location.replace('/static/workspace.html');return;}
 const sidebar=document.querySelector('.mf-sidebar');if(!sidebar)return;
 const access=await import('/static/shared/access.js');
 const {visibleNavigation}=await import('/static/shared/access-model.js');
 access.bindRole();sidebar.replaceChildren();sidebar.classList.add('platform-sidebar');sidebar.setAttribute('aria-label','平台导航');
 const brand=document.createElement('a');brand.className='platform-brand';brand.href='/static/workspace.html';brand.textContent='MedFabric';sidebar.append(brand);
 const caption=document.createElement('p');caption.className='platform-caption';caption.textContent='医疗数据平台 · 正在加载权限';sidebar.append(caption);
 let permissions;try{permissions=await access.ready();}catch(error){caption.textContent='权限暂不可用';access.denyPage(error.message+'，数据与操作入口已隐藏。');return;}
 caption.textContent='医疗数据平台';
 const hrefFor=target=>target.startsWith('/')?target:'/static/execution/index.html?tab='+target;
 function navLink(label,target,resource){const a=document.createElement('a');a.href=hrefFor(target);a.dataset.resource=resource;a.textContent=label;return a;}
 for(const [name,items] of visibleNavigation(permissions)){const group=document.createElement('details');group.open=true;const title=document.createElement('summary');title.textContent=name;group.append(title);const nav=document.createElement('nav');nav.className='platform-nav';for(const [label,target,resource] of items)nav.append(navLink(label,target,resource));group.append(nav);sidebar.append(group);}
 function highlight(){const current=new URL(location.href);sidebar.querySelectorAll('nav a').forEach(a=>{const u=new URL(a.href);const same=(u.pathname===current.pathname||(current.pathname==='/'&&u.pathname==='/static/index.html'))&&u.hash.split('?')[0]===current.hash.split('?')[0]&&(u.searchParams.get('tab')||'plan')===(current.searchParams.get('tab')||'plan');a.classList.toggle('is-active',same);if(same)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current');});}
 highlight();window.addEventListener('platform-navigation',highlight);window.addEventListener('hashchange',highlight);
 const content=document.querySelector('.mf-content');const source=new URLSearchParams(location.search).get('source_name');
 if(content&&source){const context=document.createElement('p');context.className='management-status';context.textContent='当前资源：'+source+'。新建表单已带入来源信息，请补充业务定义。';content.prepend(context);}
 if(content&&path!=='/static/workspace.html'&&!document.querySelector('.access-denied')){
  const steps=document.createElement('nav');steps.className='platform-journey';steps.setAttribute('aria-label','业务流程');
  for(const [label,href,key] of [['接入与目录','/static/platform/resources.html','object'],['需求受理','/static/management/intake.html','demand'],['方案与执行','/static/execution/index.html?tab=plan','plan'],['结果交付','/static/execution/index.html?tab=delivery','delivery'],['治理反馈','/static/management/reconciliation.html','case']])if(access.readable(key))steps.append(navLink(label,href,key));
  content.prepend(steps);
 }
})();

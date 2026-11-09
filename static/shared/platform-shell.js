/* One permission-aware navigation shared by platform pages. */
(async () => {
 document.body.classList.add('ui-refresh-demo');
 const demoStyle=document.createElement('link');
 demoStyle.rel='stylesheet';
 demoStyle.href='/static/shared/demo-refresh.css?v=ui-demo-1';
 document.head.append(demoStyle);
 const motionStyle=document.createElement('link');
 motionStyle.rel='stylesheet';
 motionStyle.href='/static/shared/motion.css';
 document.head.append(motionStyle);
 import('/static/shared/motion.js').catch(()=>{});
 const path=location.pathname;
 if((path==='/'||path==='/static/index.html')&&!location.hash){location.replace('/static/workspace.html');return;}
 const sidebar=document.querySelector('.mf-sidebar');if(!sidebar)return;
 const intakeDock=document.querySelector('#intake-flow-dock');
 const access=await import('/static/shared/access.js');
 const {visibleNavigation}=await import('/static/shared/access-model.js?v=ui-demo-1');
 const {icon}=await import('/static/shared/icons.js');
 access.bindRole();
 sidebar.replaceChildren();sidebar.classList.add('platform-sidebar');sidebar.setAttribute('aria-label','平台导航');

 const brand=document.createElement('a');
 brand.className='platform-brand';brand.href='/static/workspace.html';brand.setAttribute('aria-label','返回 MedFabric 工作台');
 const brandMark=document.createElement('span');brandMark.className='platform-brand-mark';brandMark.setAttribute('aria-hidden','true');
 for(const height of [13,22,17]){const bar=document.createElement('i');bar.style.height=height+'px';brandMark.append(bar);}
 const brandCopy=document.createElement('span');brandCopy.className='platform-brand-copy';
 const brandName=document.createElement('strong');brandName.textContent='MedFabric';
 const brandSub=document.createElement('small');brandSub.textContent='医疗数据编织平台';
 brandCopy.append(brandName,brandSub);brand.append(brandMark,brandCopy);sidebar.append(brand);

 const caption=document.createElement('p');caption.className='platform-caption';caption.textContent='工作导航';sidebar.append(caption);
 let permissions;
 try{permissions=await access.ready();}catch(error){caption.textContent='权限暂不可用';access.denyPage(error.message+'，数据与操作入口已隐藏。');return;}

 const navHost=intakeDock?document.createElement('div'):sidebar;
 if(intakeDock){navHost.className='platform-nav-groups';sidebar.append(navHost);}
 const hrefFor=target=>target.startsWith('/')?target:'/static/execution/index.html?tab='+target;
 function navLink(label,target,resource){
  const a=document.createElement('a');a.href=hrefFor(target);a.dataset.resource=resource;a.textContent=label;return a;
 }
 const groupIcons={'工作台':'grid','数据资产':'database','需求与交付':'clipboard','安全与治理':'shield','运行与设置':'chart'};
 const groups=visibleNavigation(permissions);
 for(const [name,items] of groups){
  if(name==='工作台'&&items.length===1){
   const [label,target,resource]=items[0];
   const workbench=navLink('工作台',target,resource);
   workbench.className='platform-nav-root';
   workbench.prepend(icon(groupIcons[name]));
   navHost.append(workbench);
   continue;
  }
  const group=document.createElement('details');
  group.className='platform-nav-group';
  const title=document.createElement('summary');
  const label=document.createElement('span');label.className='platform-nav-label';label.textContent=name;
  const chevron=document.createElement('span');chevron.className='platform-nav-chevron';chevron.setAttribute('aria-hidden','true');
  title.append(icon(groupIcons[name]||'grid'),label,chevron);
  group.append(title);
  const nav=document.createElement('nav');nav.className='platform-nav';
  for(const [itemLabel,target,resource] of items)nav.append(navLink(itemLabel,target,resource));
  group.append(nav);navHost.append(group);
 }

 if(intakeDock)sidebar.append(intakeDock);
 const foot=document.createElement('div');foot.className='platform-sidebar-foot';
 const demoBadge=document.createElement('span');demoBadge.className='platform-demo-badge';demoBadge.textContent='UI 改版演示';
 const demoCopy=document.createElement('small');demoCopy.textContent='深色框架 · 主次操作 · 分组导航';
 foot.append(demoBadge,demoCopy);sidebar.append(foot);

 function highlight(){
  const current=new URL(location.href);
  if(current.pathname==='/static/platform/medical-overview.html')current.pathname='/static/platform/query.html';
  if(current.pathname==='/static/platform/patients.html')current.pathname='/static/platform/query.html';
  const demandTabs=new Set(['plan','workflow','view','job','trigger','delivery']);
  const currentExecutionTab=current.pathname==='/static/execution/index.html'?(current.searchParams.get('tab')||'plan'):null;
  sidebar.querySelectorAll('nav a').forEach(a=>{
   const u=new URL(a.href);
   const samePath=u.pathname===current.pathname||(current.pathname==='/'&&u.pathname==='/static/index.html');
   const uTab=u.searchParams.get('tab')||'plan';
   const currentTab=current.searchParams.get('tab')||'plan';
   let same=samePath&&uTab===currentTab;
   if(currentExecutionTab&&u.pathname==='/static/execution/index.html'){
    if(demandTabs.has(currentExecutionTab))same=uTab==='plan';
    else same=uTab===currentExecutionTab;
   }
   a.classList.toggle('is-active',same);
   if(same)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current');
  });
 }
 function setOpenGroup(activeGroup){
  sidebar.querySelectorAll('.platform-nav-group').forEach(group=>{group.open=group===activeGroup;});
 }
 highlight();
 const activeGroup=sidebar.querySelector('a.is-active')?.closest('details');
 setOpenGroup(activeGroup);
 sidebar.querySelectorAll('.platform-nav-group').forEach(group=>{
  group.addEventListener('toggle',()=>{
   if(!group.open)return;
   sidebar.querySelectorAll('.platform-nav-group').forEach(other=>{if(other!==group)other.open=false;});
  });
 });
 window.addEventListener('platform-navigation',highlight);
 window.addEventListener('hashchange',highlight);

 const content=document.querySelector('.mf-content');
 const source=new URLSearchParams(location.search).get('source_name');
 if(content&&source){
  const context=document.createElement('p');context.className='management-status';
  context.textContent='当前资源：'+source+'。新建表单已带入来源信息，请补充业务定义。';content.prepend(context);
 }
 if(content&&(path==='/static/management/intake.html'||path==='/static/execution/index.html')&&!document.querySelector('.access-denied')){
  const steps=document.createElement('nav');steps.className='platform-journey';steps.setAttribute('aria-label','业务流程');
  for(const [label,href,key] of [['接入与目录','/static/platform/resources.html','object'],['需求受理','/static/management/intake.html','demand'],['方案与执行','/static/execution/index.html?tab=plan','plan'],['结果交付','/static/execution/index.html?tab=delivery','delivery'],['治理反馈','/static/management/reconciliation.html','case']])if(access.readable(key))steps.append(navLink(label,href,key));
  content.prepend(steps);
 }

 const topbar=document.querySelector('.mf-topbar');
 if(topbar){
  topbar.classList.add('demo-topbar');
  const roleLabel=topbar.querySelector('label');
  if(roleLabel){
   const select=roleLabel.querySelector('select');
   roleLabel.classList.add('demo-role-pill');
   roleLabel.replaceChildren();
   const roleCaption=document.createElement('span');roleCaption.textContent='演示身份';
   roleLabel.append(roleCaption);
   if(select){select.setAttribute('aria-label','演示身份');roleLabel.append(select);}
  }
 }
 const dashboardRefresh=document.querySelector('#dashboard-refresh');
 if(dashboardRefresh)dashboardRefresh.classList.add('mf-button-secondary');
})();

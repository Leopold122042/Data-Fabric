import {enterPage,headers,currentAccess} from './shared/access.js';
import {dashboardRoles} from './shared/dashboard-guide.js';
import {icon} from './shared/icons.js';
import {format,statusName,eventName,distribution,orderURL,executionURL} from './dashboard-model.js';
import {node,link,iconBox,action,badge,panel,empty,row,metric,bars,stages,authorizationChart} from './dashboard-components.js?v=20261102';

const $=s=>document.querySelector(s),root=$('#dashboard-root');
const paths={resources:'/static/platform/resources.html',services:'/static/management/services.html',catalog:'/static/management/catalog.html',orders:'/static/management/work-orders.html',events:'/static/platform/events.html',risk:'/static/platform/security.html',medical:'/static/platform/medical-overview.html'};
function openDetail(title,body){$('#dashboard-dialog-title').textContent=title;$('#dashboard-dialog-body').replaceChildren(body);$('#dashboard-dialog').showModal();}
function summary(title,lines,url,label='前往完整记录'){
 const body=node('div');for(const text of lines)body.append(node('p',text));if(url)body.append(action(label,url));openDetail(title,body);
}
$('#dashboard-close').onclick=()=>$('#dashboard-dialog').close();
$('#dashboard-dialog').addEventListener('click',e=>{if(e.target!==e.currentTarget)return;const r=e.currentTarget.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)e.currentTarget.close();});
$('#dashboard-help').replaceChildren(icon('info'),node('span','指标口径'));
$('#dashboard-help').onclick=()=>summary('指标口径',[
 '业务数据为当前快照，各自按人、次、份和点统计。元数据事件为留存窗口，最多 800 条；核验与交付为已保存记录。',
 '首页仅显示当前身份可见的摘要。已有凭证不代表当前可读取结果，读取时重新检验授权。'
],'/static/platform/about.html#dashboard-design','完整口径与身份分区');
$('#dashboard-refresh').replaceChildren(icon('refresh'),node('span','刷新'));
const footerLink=$('.dashboard-footer>a');footerLink.replaceChildren(node('span','首页设计说明'),icon('arrow'));footerLink.classList.add('dashboard-footer-link');

function sourceBars(parent,d){
 return bars(parent,d.objects.by_source,d.source_names,{label:'目录对象按来源分布',tones:{his:'blue',lis:'teal',pacs:'violet',emr:'amber',iot:'cyan'},onDetail:(name,value,share)=>summary(name,[`关联目录对象 ${format(value)} 个，占当前分布 ${share}。`,d.role==='provider'?'仅统计本方服务明确引用的目录对象，不代表资源所有权。':'统计当前有效的表与字段目录对象，不代表业务数据行数。'],paths.catalog,'查看资源目录')});
}
function eventsPanel(d,auditor=false){
 const p=panel(auditor?'元数据变更与事件':'元数据事件',paths.events,'查看全部','activity');p.classList.add('dashboard-events');
 const overview=node('div','','dashboard-event-overview');
 if(d.events){for(const r of distribution(d.events.by_type,3))overview.append(badge(eventName(r.label)+' '+format(r.value),'blue','layers'));}
 overview.append(node('span',d.events?'当前留存窗口 · 最多 800 条':'事件暂不可用','dashboard-muted'));p.append(overview);
 const list=node('div','','dashboard-records dashboard-event-list');
 for(const ev of d.events?.items||[]){
  const r=row(list,ev.title||eventName(ev.etype),'',null,'','database'),body=r.querySelector('.dashboard-row-body');
  const meta=node('div','','dashboard-row-meta');meta.append(badge((d.source_names[ev.source_id]||ev.source_id||'平台'),'slate'),node('span',ev.ts||'','dashboard-muted'));body.lastChild.replaceWith(meta);
  const b=action('详情',null,'eye');b.onclick=()=>summary(ev.title||'事件详情',[ev.detail||'无补充说明',eventName(ev.etype)+' · '+(ev.ts||'')],paths.events,'前往元数据事件');r.append(b);
 }
 p.append(list);if(d.events&&!d.events.items.length)empty(list,'留存窗口内暂无事件','activity');return p;
}
function todoPanel(d){
 const p=panel('当前需关注的需求',paths.orders,'查看全部','clipboard'),list=node('div','','dashboard-records');
 for(const o of (d.attention||[]).slice(0,2)){
  const alert=o.counts.exceptions>0;
  row(list,o.title,alert?o.counts.exceptions+' 项未解决异常':d.role==='provider'?'通过协作记录补充本方服务说明':o.next_action,orderURL(o.id),'查看',alert?'alert':'clock',alert?'amber':'blue');
 }
 if(!list.children.length)empty(list,'暂无待关注事项','check');p.append(list);return p;
}
function renderPlatform(d,grid,metrics){
 metric(metrics,'接入来源',d.sources?.length,paths.resources,'本地来源','network');
 metric(metrics,'目录对象',d.objects.total,paths.catalog,'有效表与字段','layers','violet');
 metric(metrics,'患者',d.medical?.patients,paths.medical,'人 · 当前快照','users');
 metric(metrics,'就诊',d.medical?.visits,paths.medical,'次 · 当前快照','activity','teal');
 metric(metrics,'元数据事件',d.events?.total,paths.events,'留存窗口','pulse','violet');
 metric(metrics,'未解决异常',d.order_counts.exceptions,paths.orders,'工单关联异常','alert','amber');
 const data=panel('数据规模与分布',paths.resources,'资源目录','database'),split=node('div','','dashboard-split'),numbers=node('div','','dashboard-data-tiles'),dist=node('div','','dashboard-distribution');
 for(const [label,key,unit,symbol,tone] of [['患者','patients','人','users','blue'],['就诊','visits','次','activity','teal'],['检验','lab_tests','条','lab','cyan'],['影像','studies','项','image','violet'],['病历','emr_docs','份','file','blue'],['体征','vitals_points','点','pulse','amber']]){
  const tile=link('',paths.medical,'dashboard-data-tile'),body=node('div'),value=node('div','','dashboard-tile-value');value.append(node('strong',format(d.medical?.[key])),node('small',unit));body.append(node('span',label),value);tile.append(iconBox(symbol,tone),body);numbers.append(tile);
 }
 const plotHead=node('div','','dashboard-plot-heading');plotHead.append(node('h3','目录对象来源分布'),node('span','数量 / 占比','dashboard-muted'));dist.append(plotHead);sourceBars(dist,d);split.append(numbers,dist);data.append(split);
 const note=node('div','','dashboard-panel-foot');note.append(node('span','当前业务快照 · 各项按独立单位统计','dashboard-muted'),action('科室与诊断',paths.medical));data.append(note);
 const sources=panel('来源扫描状态',paths.resources,'来源明细','network'),sourceGrid=node('div','','dashboard-sources');
 for(const s of d.sources||[]){
  const success=s.status==='online',b=node('button','','dashboard-source');b.type='button';
  b.append(iconBox('database',success?'teal':'amber'),node('b',(s.id||'').toUpperCase()),badge(({online:'成功',error:'失败',pending:'待扫描'}[s.status]||'未知'),success?'teal':'amber',success?'check':'clock'));
  b.onclick=()=>summary('来源扫描摘要',[s.name||s.id,'最近扫描：'+(s.last_scan||'尚无记录'),'扫描成功只表示最近一次扫描结果，不代表持续在线。'],paths.resources,'前往资源目录');sourceGrid.append(b);
 }
 sources.append(sourceGrid,node('p','最近扫描结果；不代表持续连接健康。','dashboard-note'));if(!d.sources)empty(sources,'来源暂不可用');
 const business=panel('业务摘要',paths.orders,'协作工单','route'),cells=node('div','','dashboard-business');
 for(const [label,key,symbol,tone] of [['待确认','draft','clock','amber'],['待编制','confirmed','file','blue'],['执行中','running','activity','violet'],['已交付','delivered','check','teal']]){
  const c=link('',paths.orders);c.dataset.tone=tone;c.append(icon(symbol),node('span',label),node('strong',format(d.order_counts[key]||0)));cells.append(c);
 }
 business.append(cells);const bottom=node('div','','dashboard-bottom');bottom.append(sources,business,todoPanel(d));grid.append(data,eventsPanel(d),bottom);
}
function lifecycle(d){
 const container=node('div','','dashboard-lifecycle'),track=node('div','','dashboard-segments'),legend=node('div','','dashboard-chips');
 const colors={published:'teal',draft:'amber',review:'violet',trial:'blue',adjusting:'amber',retired:'slate'};
 for(const [state,count] of Object.entries(d.service_counts.by_state||{})){
  if(count<=0)continue;const segment=node('span');segment.dataset.tone=colors[state]||'blue';segment.style.flexGrow=count;track.append(segment);legend.append(badge(statusName(state)+' '+format(count),colors[state]||'blue'));
 }
 track.setAttribute('role','img');track.setAttribute('aria-label','服务状态：'+Object.entries(d.service_counts.by_state||{}).map(([s,c])=>statusName(s)+' '+format(c)).join('，'));
 container.append(node('h3','服务发布状态'),track,legend);return container;
}
function renderProvider(d,grid,metrics){
 metric(metrics,'关联目录对象',d.objects.total,paths.catalog,'本方服务明确引用','layers','violet');
 metric(metrics,'本方服务',d.service_counts.total,paths.services,'当前记录','database');
 metric(metrics,'已发布',d.service_counts.published,paths.services,'当前记录','check','teal');
 metric(metrics,'使用主体',d.consumer_count,paths.orders,'含申请使用','building');
 metric(metrics,'关联需求',d.order_counts.total,paths.orders,'当前记录','clipboard','violet');
 metric(metrics,'收到反馈',d.feedback_count,paths.orders,'工单定向评价','message','teal');
 const own=panel('本方资源与服务',paths.services,'服务目录','database');own.classList.add('dashboard-own-resources');
 const plotHead=node('div','','dashboard-plot-heading');plotHead.append(node('h3','关联目录来源'),node('span','数量 / 占比','dashboard-muted'));own.append(plotHead);sourceBars(own,d);own.append(lifecycle(d));
 const usage=panel('谁在使用本方服务',paths.orders,'使用工单','network');usage.classList.add('dashboard-usage');
 const captions=node('div','','dashboard-usage-head');for(const text of ['需求与使用主体','关联服务','使用状态'])captions.append(node('span',text));usage.append(captions);
 const list=node('div','','dashboard-records');
 for(const u of d.usages){
  const r=node('div','','dashboard-usage-row'),title=node('div','','dashboard-usage-demand'),heading=node('b',u.title);heading.title=u.title;
  const requester=node('span','','dashboard-muted');requester.append(icon('building'),node('span',u.requester));title.append(heading,requester);
  const service=node('div','','dashboard-usage-service');service.append(icon('database'),node('span',u.name));service.title=u.name;
  const end=node('div','','dashboard-usage-state');end.append(badge(u.usage_state,u.usage_state==='已调用'?'teal':u.usage_state==='申请使用'?'amber':'blue'),action('查看',orderURL(u.order_id)));
  r.append(title,service,end);list.append(r);
 }
 if(!d.usages.length)empty(list,'尚无关联需求','network');usage.append(list,node('p','申请、编入方案与实际调用分别记录。','dashboard-note'));
 const feedback=panel('本方服务质量反馈',paths.orders,'工单评价','message'),comments=node('div','','dashboard-records');
 for(const f of d.feedback)row(comments,f.title,f.comment||'已提交质量评价',orderURL(f.order_id),'查看','message','teal');if(!d.feedback.length)empty(comments,'尚未收到关联服务的使用评价','message');feedback.append(comments);grid.append(own,usage,feedback,todoPanel(d));
}
function renderRequester(d,grid,metrics){
 metric(metrics,'我的申请',d.order_counts.total,paths.orders,'当前记录','clipboard');
 metric(metrics,'待确认需求',d.order_counts.draft||0,paths.orders,'补全范围后由平台确认','clock','amber');
 metric(metrics,'执行中需求',d.order_counts.running||0,paths.orders,'含排队中的需求','activity','violet');
 metric(metrics,'交付凭证',d.delivery_count,executionURL('delivery'),'读取时重新检验授权','package','teal');
 metric(metrics,'未解决异常',d.order_counts.exceptions,paths.orders,'当前记录','alert','amber');
 metric(metrics,'可见服务',d.service_counts.total,paths.services,'当前记录','grid');
 const progress=panel('我的需求进展',paths.orders,'全部申请','route');progress.classList.add('dashboard-progress');const list=node('div','','dashboard-order-list');
 for(const o of d.orders){
  const card=node('article','','dashboard-order'),head=node('div','','dashboard-order-head'),title=node('b',o.title);title.title=o.title;
  head.append(title,badge(o.status_label,o.status==='delivered'?'teal':o.status==='draft'?'amber':'blue'),action('查看工单',orderURL(o.id)));
  const next=node('p',o.next_action,'dashboard-muted');next.title=o.next_action;card.append(head,next,stages(o.stages));list.append(card);
 }
 if(!d.orders.length)empty(list,'尚无申请，可从需求受理开始。','clipboard');progress.append(list);
 const results=panel('成果与授权',executionURL('delivery'),'查看全部','package'),receipts=node('div','','dashboard-records');
 for(const x of d.deliveries)row(receipts,x.name,statusName(x.state)+' · 读取时重新核验',executionURL('delivery',x.id),'查看凭证','file','teal');
 if(!d.deliveries.length)empty(receipts,'尚无交付凭证','package');
 for(const x of d.exceptions.slice(0,1))row(receipts,x.title,x.detail,orderURL(x.order_id),'查看异常','alert','amber').classList.add('dashboard-attention');results.append(receipts);
 const services=panel('可申请的数据服务',paths.services,'服务目录','grid'),cards=node('div','','dashboard-service-cards');
 for(const s of d.services.slice(0,2)){
  const card=node('div','','dashboard-service-card'),head=node('div'),title=node('b',s.name);title.title=s.name;head.append(iconBox('database'),title);
  const foot=node('div');foot.append(badge(statusName(s.lifecycle),'teal'),action('查看服务',paths.services+'?selected='+encodeURIComponent(s.id)));card.append(head,foot);cards.append(card);
 }
 if(!d.services.length)empty(cards,'暂无可见的已发布服务','database');services.append(cards);grid.append(progress,results,services,todoPanel(d));
}
function renderAuditor(d,grid,metrics){
 metric(metrics,'元数据事件',d.events?.total,paths.events,'留存窗口','activity');
 metric(metrics,'结构变更',d.events?d.events.by_type.schema_change||0:null,paths.events,'留存窗口','layers','violet');
 metric(metrics,'访问拒绝',d.authorization.rejected,executionURL('decision'),'已保存核验记录','alert','amber');
 metric(metrics,'登记授权',d.authorization.grants,executionURL('grant'),'包含过期及撤销记录','key');
 metric(metrics,'待核查风险',d.risks?.open,paths.risk,'风险留存窗口','shield','teal');
 metric(metrics,'交付凭证',d.delivery_count,executionURL('delivery'),'不含结果内容','package','violet');
 const auth=panel('访问授权与授权检验',executionURL('decision'),'查看全部','shield');auth.classList.add('dashboard-auth');
 const overview=node('div','','dashboard-auth-summary');overview.append(badge('累计 '+format(d.authorization.total)+' 次授权检验','blue','key'),node('span','已保存的授权检验记录','dashboard-muted'));auth.append(overview);
 const plot=node('div','','dashboard-auth-plot'),reasons=node('div','','dashboard-auth-reasons');reasons.append(node('h3','访问拒绝原因'));
 bars(reasons,d.authorization.reasons,{}, {tone:'amber',unit:'次',label:'访问拒绝原因分布',onDetail:(name,count,share)=>summary(name,[`拒绝 ${format(count)} 次，占全部拒绝记录 ${share}。`,'拒绝记录与待核查风险分别统计。'],executionURL('decision'),'查看授权检验')});
 plot.append(authorizationChart(d.authorization),reasons);auth.append(plot,node('p','通过率按已保存核验记录计算；拒绝记录不等同于待核查风险。','dashboard-note'));
 const evidence=panel('运行证据追溯',executionURL('run'),'查看全部','file'),runs=node('div','','dashboard-records');
 for(const r of d.evidence.slice(0,2))row(runs,r.name||r.plan_id||r.id,'方案版本 '+(r.plan_version||'—')+' · '+statusName(r.status),executionURL('run',r.id),'查看证据','file',r.status==='success'?'teal':'amber');if(!d.evidence.length)empty(runs,'尚无运行证据','file');evidence.append(runs);
 const risk=panel('待核查风险',paths.risk,'查看全部','shield'),risks=node('div','','dashboard-records');
 for(const r of d.risks?.items||[])row(risks,r.summary||r.target,r.level||'待核查',paths.risk,'查看','alert','amber');
 if(!d.risks?.items.length){const state=empty(risks,d.risks?'当前风险窗口内暂无待核查项':'风险信息暂不可用','shield');state.classList.add('dashboard-risk-clear');if(d.risks)state.append(node('small','风险与核验明细可进入安全与治理查看。'));}risk.append(risks);grid.append(eventsPanel(d,true),auth,evidence,risk);
}
let busy=false,lastData=null;
async function refresh(){
 if(busy)return;busy=true;$('#dashboard-refresh').disabled=true;root.setAttribute('aria-busy','true');
 try{
  const response=await fetch('/api/dashboard',{headers:headers(),cache:'no-store'}),d=await response.json();
  if(!response.ok)throw Error(d.error||'读取总览失败');if(d.role!==currentAccess().principal.role)throw Error('身份已变化，请刷新页面');
  const config=dashboardRoles.find(x=>x.role===d.role);$('#dashboard-title').textContent=config.title;document.title=config.title+' · MedFabric';$('#dashboard-scope').textContent=d.scope+' · '+config.focus;
  const metrics=node('section','','dashboard-metrics');metrics.setAttribute('aria-label','核心指标');const grid=node('div','','dashboard-grid '+d.role);
  ({platform:renderPlatform,provider:renderProvider,requester:renderRequester,auditor:renderAuditor}[d.role])(d,grid,metrics);
  root.replaceChildren(metrics,grid);lastData=d;$('#dashboard-status').textContent=d.errors.join('；');$('#dashboard-status').hidden=!d.errors.length;$('#dashboard-time').textContent='更新于 '+new Date(d.updated_at).toLocaleTimeString('zh-CN',{hour12:false});
 }catch(e){$('#dashboard-status').hidden=false;$('#dashboard-status').textContent=e.message+(lastData?'；保留上次快照。':'；请点击刷新重试。');}
 finally{busy=false;$('#dashboard-refresh').disabled=false;root.setAttribute('aria-busy','false');}
}
$('#dashboard-refresh').onclick=refresh;
if(await enterPage('overview'))await refresh();

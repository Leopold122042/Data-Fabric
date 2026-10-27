/* Pure presentation rules. The server remains authoritative for every request. */
export const roleNames={platform:'平台方 / 数据服务中心',provider:'供给方 / 医院数据中心',requester:'需求方 / 临床研究中心',auditor:'审计方 / 数据合规岗'};
export const navigationGroups=[
 ['平台',[['总览','/static/workspace.html','overview']]],
 ['数据资源',[['外部数据接入','/static/platform/connections.html','connection'],['资源目录','/static/platform/resources.html','object'],['概念映射','/static/management/semantic.html','concept'],['语义图谱','/static/platform/graph.html','concept'],['查询中心','/static/platform/query.html','view']]],
 ['需求与服务',[['需求受理','/static/management/intake.html','demand'],['服务目录与发布','/static/management/services.html','service'],['元数据维护','/static/management/catalog.html','object']]],
 ['执行与交付',[['执行方案','plan','plan'],['逻辑视图','view','view'],['执行流程','workflow','workflow'],['运行任务','job','job'],['触发调度','trigger','trigger'],['结果与凭证','delivery','delivery']]],
 ['安全与治理',[['访问授权','grant','grant'],['授权核验','decision','decision'],['运行证据','run','run'],['偏差校准','/static/management/reconciliation.html','case'],['评价复测','/static/management/feedback.html','feedback'],['合作约定','contract','contract'],['设施管理','facility','facility']]],
 ['运行观测',[['元数据事件','/static/platform/events.html','event'],['自动化规则','/static/platform/rules.html','rule'],['风险监测','/static/platform/security.html','risk'],['平台说明','/static/platform/about.html','about']]]
];
const pageResources={'intake':'demand','services':'service','catalog':'object','semantic':'concept','reconciliation':'case','feedback':'feedback','connections':'connection','resources':'object','graph':'concept','query':'view','patients':'patient','events':'event','rules':'rule','security':'risk','about':'about','workspace':'overview'};
export function level(snapshot,resource){const value=snapshot?.resources?.[resource];return ['read','write'].includes(value)?value:'none';}
export function canRead(snapshot,resource){return level(snapshot,resource)!=='none';}
export function pageResource(url){const u=new URL(url,'http://local');if(u.pathname==='/static/execution/index.html')return u.searchParams.get('tab')||'plan';const name=u.pathname.split('/').pop().replace(/\.html$/,'');return pageResources[name]||null;}
export function visibleNavigation(snapshot){return navigationGroups.map(([title,items])=>[title,items.filter(([, , resource])=>canRead(snapshot,resource))]).filter(([,items])=>items.length);}
export function owns(snapshot,record){return !!record&&[record.actor,record.created_by,record.subject,record.provider].includes(snapshot?.principal?.id);}
export function actionAllowed(snapshot,key,record=null){
 if(!key||snapshot?.actions?.[key==='job.result'?'delivery.read':key]!==true)return false;
 const id=snapshot.principal?.id,role=snapshot.principal?.role;
 if(record){
  if(['delivery.read','delivery.reference','delivery.download','job.result'].includes(key)){
   if(record.actor!==id)return false;
   if(key==='job.result')return !!record.delivery_id;
   if(record.state!=='available')return false;
   if(key==='delivery.reference'&&record.circulation_mode!=='api')return false;
   if(key==='delivery.download'&&record.circulation_mode!=='download')return false;
   if(key==='delivery.read'&&record.circulation_mode==='api')return false;
  }
  if(key==='contract.accept')return (record.parties||[]).includes(id)&&!(record.accepted||[]).includes(id);
  if(key==='job.cancel'&&!['queued','running'].includes(record.state))return false;
  if(key==='job.retry'&&!['failed','interrupted','cancelled'].includes(record.state))return false;
  if(key==='grant.revoke'&&record.status!=='active')return false;
  if(key==='trigger.disable'&&!record.enabled)return false;
  if(key==='delivery.destroy'&&record.state!=='available')return false;
  if(key==='plan.detail_export'&&record.scenario!=='medical')return false;
  if(key==='plan.expiry_test'&&record.scenario!=='medical')return false;
  if(role!=='platform'&&['service.update','service.validate','service.regularize'].includes(key))return record.provider===id;
  if(role!=='platform'&&['demand.update','demand.constraints','plan.update','plan.confirm','plan.run','job.cancel','job.retry','trigger.disable','facility.heartbeat','delivery.destroy'].includes(key)&&!owns(snapshot,record))return false;
 }
 return true;
}
const rule=(key,tabs=null,selected=false)=>({key,tabs,selected});
export const managementActions={
 '新建需求':rule('demand.create',['demand']), '编辑需求':rule('demand.update',['demand'],true), '设置执行条件':rule('demand.constraints',['demand'],true), '查看缺口':rule('demand.gaps',['demand'],true), '人工确认规格':rule('demand.confirm',['demand'],true), '生成执行方案':rule('spec.plan',['spec'],true),
 '新建服务':rule('service.create',['service']), '编辑服务':rule('service.update',['service'],true), '执行发布验证':rule('service.validate',['service'],true), '变更状态':rule('service.transition',['service'],true), '定制转常态':rule('service.regularize',['service'],true), '登记稳定组合':rule('service.compose',['service']),
 '扫描目录变更':rule('object.scan'), '编辑说明':rule('object.annotate',null,true), '影响范围':rule('object.impact',null,true), '确认源字段改名':rule('object.rename',null,true), '登记全局标识':rule('identifier.create'), '导出目录':rule('object.export'), '接入本地文件':rule('source.create'), '字段统计剖析':rule('object.profile',null,true), '登记加工血缘':rule('relation.create'),
 '新建概念':rule('concept.create',['concept']), '修改概念':rule('concept.update',['concept'],true), '候选字段':rule('concept.candidates',['concept'],true), '新增映射':rule('mapping.create',['mapping','concept']), '复核映射':rule('mapping.update',['mapping'],true), '检查可执行映射':rule('concept.resolve',['concept'],true), '导入既有语义映射':rule('concept.import'), '四类映射说明':rule('mapping.read'), '建立客户、机构、指标概念':rule('concept.business'),
 '执行对账':rule('case.reconcile'), '健康指标':rule('case.metrics'), '处置偏差':rule('case.action',['case'],true), '隔离扰动验证':rule('case.experiment'),
 '查看运行证据':rule('run.read'), '提交评价':rule('feedback.create',['feedback']), '新增复测对比':rule('improvement.create',['improvement']), '新增指标口径':rule('metric_definition.create',['metric_definition']), '修订指标口径':rule('metric_definition.update',['metric_definition'],true)
};
export function managementRule(label,kind){if(['历史版本','查看历史版本','处置历史'].includes(label))return rule(kind+'.history',null,true);return managementActions[label]||null;}
export function presentation(snapshot,descriptor,kind,record){
 if(!descriptor||descriptor.tabs&&!descriptor.tabs.includes(kind))return {visible:false,disabled:true};
 const visible=actionAllowed(snapshot,descriptor.key,descriptor.selected?record:null);
 return {visible,disabled:descriptor.selected&&!record};
}
export function buttonState(permission,{businessDisabled=false,busy=false}={}){
 return {hidden:!permission.visible,disabled:!permission.visible||!!permission.disabled||businessDisabled||busy};
}
export function paginationState(page,total,size){
 if(!Number.isInteger(page)||page<0||!Number.isInteger(total)||total<0||!Number.isInteger(size)||size<1)throw RangeError('分页参数无效');
 return {previousDisabled:page===0,nextDisabled:(page+1)*size>=total};
}

export const executionLabels={plan:'方案',workflow:'执行流程',view:'逻辑视图',job:'任务',grant:'授权',decision:'授权核验',run:'运行证据',delivery:'交付凭证',trigger:'触发器',contract:'合作约定',facility:'设施'};
export const executionActions={
 '新建医疗汇总方案':['plan.create'], '从需求规格生成方案':['plan.create'], '确认当前版本':['plan.confirm',true], '修改方案生成新版本':['plan.update',true], '签发本地授权':['grant.create',true], '执行当前方案':['plan.run',true], '测试医疗明细导出拒绝':['plan.detail_export',true], '测试过期授权拒绝':['plan.expiry_test',true], '暂停后续节点':['plan.pause',true],
 '从目录字段建立视图':['view.create'], '查看最新节点和时间线':['job.read',true], '取消任务':['job.cancel',true], '重试失败任务':['job.retry',true], '查看受控结果':['job.result',true], '撤销授权':['grant.revoke',true],
 '读取结果（重新核验授权）':['delivery.read',true], '读取授权 API 引用':['delivery.reference',true], '校验凭证':['delivery.verify',true], '下载已授权结果':['delivery.download',true], '清除平台结果副本':['delivery.destroy',true],
 '登记触发器':['trigger.create'], '停用触发器':['trigger.disable',true], '登记合作约定':['contract.create'], '以当前身份确认':['contract.accept',true], '登记本地设施':['facility.create'], '发送心跳':['facility.heartbeat',true]
};
export function visibleExecutionTabs(snapshot){return Object.entries(executionLabels).filter(([key])=>canRead(snapshot,key));}

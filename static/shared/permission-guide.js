import {navigationGroups,roleNames} from './access-model.js';

export const permissionRoles=['platform','provider','requester','auditor'];
const accessLabels={write:'可查看、可操作',read:'可查看',none:'不可查看'};
const childResources={
 demand:[['任务规格','spec'],['服务变更通知','notice']],
 service:[['发布验证','validation']],
 object:[['数据源','source'],['全局标识','identifier'],['关系与血缘','relation'],['扫描记录','scan']],
 concept:[['字段映射关系','mapping'],['映射规则','mapping_rule']],
 feedback:[['复测对比','improvement'],['指标口径','metric_definition']],
 case:[['设计运行对照','pair'],['登记设计','external_design']]
};

export const roleGuidance=[
 ['platform','统筹需求、服务、执行和治理，查看平台范围的业务记录。','受理与确认需求，管理服务发布、授权和治理处置；交付内容仍按交付主体与有效授权读取。'],
 ['provider','维护本方服务，查看本方服务的使用需求、使用方、进度与质量反馈。','维护服务、申请发布验证、确认本方参与的合作约定；工单只展示与本方服务有关的摘要。'],
 ['requester','查看本人申请的需求及确认、执行、交付、异常处理全过程。','提交与修改本人的需求，管理自己的执行任务，按授权获取结果并提交评价。'],
 ['auditor','查看业务过程、授权与治理证据。','查看记录、历史和交付凭证，可核验凭证；不修改业务记录，不读取交付结果内容。']
].map(([role,scope,operations])=>({role,name:roleNames[role],scope,operations}));

// Derive every cell from the same policy that controls navigation and API access.
export function permissionSections(policy){
 const seen=new Set();
 const aliases=new Map();for(const [,items] of navigationGroups)for(const [label,,key] of items){const names=aliases.get(key)||[];if(!names.includes(label))names.push(label);aliases.set(key,names);}
 const row=(label,key)=>{
  if(seen.has(key)||!policy?.resources?.[key])return null;
  seen.add(key);
  return {key,label:aliases.has(key)?aliases.get(key).join(' / '):label,levels:permissionRoles.map(role=>accessLabels[policy.resources[key][role]]||accessLabels.none)};
 };
 const sections=navigationGroups.map(([name,items])=>({name,rows:items.flatMap(([label,,key])=>{
  const rows=[row(label,key),...(childResources[key]||[]).map(([child,resource])=>row(child,resource))];
  return rows.filter(Boolean);
 })})).filter(section=>section.rows.length);
 const remaining=Object.keys(policy?.resources||{}).filter(key=>!seen.has(key)).map(key=>row(key==='patient'?'患者全景':key,key));
 if(remaining.length)sections.push({name:'其他功能',rows:remaining});
 return sections;
}

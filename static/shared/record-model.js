/* Presentation only: the original record remains available for traceability. */
const hiddenFields=new Set(['demo','demo_seed','seed_id','seed_version','legacy_seed']);
const identityNames={
 'demo-platform':'数据服务中心',
 'demo-provider':'医院数据中心',
 'demo-requester':'临床研究中心',
 'demo-auditor':'数据合规岗'
};

export function recordPreview(value,key=''){
 if(value===null||typeof value!=='object'){
  if(key==='environment'&&value==='demo')return '本机环境';
  return typeof value==='string'&&Object.hasOwn(identityNames,value)?identityNames[value]:value;
 }
 if(Array.isArray(value))return value.map(item=>recordPreview(item,key));
 return Object.fromEntries(Object.entries(value).filter(([name])=>!hiddenFields.has(name)).map(([name,item])=>[name,recordPreview(item,name)]));
}

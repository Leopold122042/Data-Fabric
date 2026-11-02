export const format = value => value == null || !Number.isFinite(Number(value)) ? '—' : Number(value).toLocaleString('zh-CN');
export const statusName = value => ({draft:'待确认',published:'已发布',review:'审核中',trial:'试用',adjusting:'调整中',retired:'已退出',available:'已有凭证',destroyed:'副本已清除',success:'成功',failed:'失败',blocked:'已阻断',running:'执行中',queued:'排队中'}[value] || value || '未记录');
export const eventName = value => ({data_update:'数据增量',schema_change:'结构变更',catalog_built:'目录纳管',semantic_link_discovered:'语义关联',quality_anomaly:'体征业务提示',security_risk:'访问安全事件',orchestrated_action:'任务反馈'}[value] || '其他事件');
export function distribution(values={}, limit=6){
 const rows=Object.entries(values).filter(([,v])=>Number.isFinite(v)&&v>=0).sort((a,b)=>b[1]-a[1]);
 if(rows.length>limit){const rest=rows.splice(limit-1);rows.push(['其他',rest.reduce((n,[,v])=>n+v,0)]);}
 const max=Math.max(1,...rows.map(([,v])=>v));
 return rows.map(([label,value])=>({label,value,width:value/max*100}));
}
export const orderURL = id => '/static/management/work-orders.html?selected='+encodeURIComponent(id);
export const executionURL = (kind,id) => '/static/execution/index.html?tab='+encodeURIComponent(kind)+(id?'&selected='+encodeURIComponent(id):'');

export function stagePresentation(state){
 return ({done:{state:'done',label:'已完成',icon:'check'},current:{state:'current',label:'当前阶段',icon:null},attention:{state:'attention',label:'需关注',icon:'alert'},pending:{state:'pending',label:'待处理',icon:null}})[state]||{state:'pending',label:'待处理',icon:null};
}
export function authorizationSummary(auth){
 const total=Math.max(0,Number(auth.total)||0),rejected=Math.min(total,Math.max(0,Number(auth.rejected)||0)),passed=total-rejected;
 return {total,rejected,passed,rate:total?passed/total:null};
}

export const stateNames={draft:'待补充',confirmed:'已确认',planning:'待确认方案',awaiting_authorization:'待授权',authorized:'待执行',superseded:'已有后续版本',recovered:'已恢复',ready:'待执行',pending:'待办理',active:'办理中',running:'执行中',queued:'排队中',delivered:'已交付',attention:'需要关注',failed:'失败',blocked:'已阻断',cancelled:'已取消',interrupted:'已中断',success:'成功',available:'结果就绪',destroyed:'平台副本已清除',paused:'已暂停',invalidated:'已失效',closed:'已关闭',observing:'观察中',open:'待处理',resolved:'已处理',requested:'已申请',planned:'已编排',used:'已调用',not_started:'尚未开始',completed:'已完成',done:'已完成',current:'进行中',waiting:'待办理',skipped:'未涉及'};
export const modeNames={controlled_compute:'受控查看',download:'授权下载',api:'API 引用'};
export function label(value){return stateNames[value]||value||'待办理';}
export function attention(item){return item.status==='attention'||Number(item.counts?.exceptions)>0;}
export function filterOrders(items,{query='',filter='all',service=''}={}){
 const q=query.trim().toLocaleLowerCase();
 return items.filter(item=>(!q||[item.title,item.id,item.purpose,item.requester_name].some(v=>String(v||'').toLocaleLowerCase().includes(q)))&&
  (filter==='all'||filter==='attention'&&attention(item)||filter==='delivered'&&item.status==='delivered'||filter==='active'&&!['delivered','attention'].includes(item.status))&&
  (!service||(item.service_ids||[]).includes(service)||(item.services||[]).some(s=>s.id===service)));
}
export function recordUrl(kind,id){
 if(!id)return null;
 if(kind==='demand')return '/static/management/intake.html?selected='+encodeURIComponent(id);
 if(kind==='service')return '/static/management/services.html?selected='+encodeURIComponent(id);
 if(['plan','job','delivery'].includes(kind))return '/static/execution/index.html?tab='+kind+'&selected='+encodeURIComponent(id);
 return null;
}
export function orderedTimeline(rows){return [...rows].sort((a,b)=>(a.time||'').localeCompare(b.time||'')||String(a.id||'').localeCompare(String(b.id||'')));}
export function messagePayload(body,serviceId,scopes){
 const value=body.trim();if(!value)throw Error('请填写协作说明。');if(value.length>2000)throw Error('协作说明最多 2,000 字。');
 if(!scopes.some(s=>s.id===serviceId))throw Error('请选择可用的沟通范围。');
 return {body:value,service_id:serviceId};
}

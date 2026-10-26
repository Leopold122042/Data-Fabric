import {api,action,list,need,show,form,edit,init,$,tabs} from './ui.js';
await init('需求受理','填写需求后设置执行条件，并核对范围说明与实际筛选一致。数据查询按绑定字段限制范围和时间；元数据快照的时间窗为执行有效期。日期按UTC日界，结束日期包含当天。当前交付时限同时是执行和结果读取截止。规格确认不等于执行授权。');let kind='demand';const load=()=>list(kind);$('#refresh').onclick=load;tabs([['demand','需求单'],['spec','任务规格']],async v=>{kind=v;await load();});
const fields=[['title','需求标题'],['entry','入口','select',[['goal','按目标提需求'],['service','直接选择服务']]],['description','原始描述','textarea'],['service_ids','服务ID（逗号分隔）','list'],['object_scope','对象范围'],['time_start','时间窗起点','date'],['time_end','时间窗终点','date'],['purpose','用途'],['quality','质量要求'],['deadline','交付时限','date'],['delivery','交付形态'],['cost','成本约束（可选）'],['attachments','附件名称或引用（逗号分隔）','list']];
action('新建需求',async()=>{const q=new URLSearchParams(location.search);const b=await form('需求草稿',fields,{object_scope:q.get('source_name')||'',description:q.get('source_object')?'所需数据资源：'+q.get('source_object'):''});if(b){show(await api('/demands',b));await load();}});action('编辑需求',()=>{if(kind!=='demand')throw Error('请选择需求单');return edit('demand','/demands','修改后需重新确认',fields,need());});
action('查看缺口',async()=>{if(kind!=='demand')throw Error('请选择需求单');show(await api('/demands/'+need().id+'/gaps'),'字段、服务、授权与质量缺口');});
action('设置执行条件',async()=>{
 if(kind!=='demand')throw Error('请选择需求单');const r=need();
 const objects=(await api('/items/object')).items.filter(o=>o.kind==='field'&&o.status==='active');
 const options=[['','请选择'],...objects.map(o=>[o.id,`${o.source_id} / ${o.table} / ${o.name}`])];
 const old=r.execution_constraints||{},scope=old.scope||{},first=(scope.filters||[])[0]||{};
 if((scope.filters||[]).length>1)throw Error('此需求包含多项筛选条件，请通过完整执行条件接口编辑，避免覆盖已有条件');
 const b=await form('明确执行范围与交付条件',[
  ['scope_kind','查询对象','select',[['rows','数据行（按范围与数据时间筛选）'],['metadata','当前元数据快照（时间窗限制执行时间）']]],
  ['scope_field','范围字段','select',options],['scope_op','比较方式','select',['=','!=','>','>=','<','<=']],
  ['scope_value','范围取值'],['scope_value_type','取值类型','select',[['text','文本'],['number','数值']]],
  ['all_rows','已明确允许全部对象（仍限制时间窗）','checkbox'],['time_field','数据时间字段（ISO日期或带时区时间）','select',options],
  ['output_mode','输出粒度','select',[['aggregate','汇总统计'],['object','对象级（受字段分类检查）']]],
  ['allow_controlled','允许受控查看','checkbox'],['allow_api','允许API引用','checkbox'],['allow_download','允许下载','checkbox'],
  ['quality_min_complete','完整率下限（0至1，1表示无缺失）','number',[],1],['max_rows','最大交付行数（1至200）','number',[],200],['min_group_size','最小分组记录数（至少5，医疗样例为人数）','number',[],5]
 ],{...old,scope_kind:scope.kind||'rows',scope_field:first.field||'',scope_op:first.op||'=',scope_value:first.value??'',scope_value_type:typeof first.value==='number'?'number':'text',all_rows:scope.all_rows||false,allow_controlled:old.circulation_modes?old.circulation_modes.includes('controlled_compute'):true,allow_api:old.circulation_modes?.includes('api')||false,allow_download:old.circulation_modes?.includes('download')||false});
 if(!b)return;
 const filters=b.scope_field?[{field:b.scope_field,op:b.scope_op,value:b.scope_value_type==='number'?Number(b.scope_value):b.scope_value}]:[];
 if(b.scope_value_type==='number'&&b.scope_field&&(b.scope_value.trim()===''||!Number.isFinite(Number(b.scope_value))))throw Error('请填写有效数值');
 const execution_constraints={scope:b.scope_kind==='metadata'?{kind:'metadata'}:{kind:'rows',filters,all_rows:b.all_rows},time_field:b.scope_kind==='rows'?b.time_field:null,output_mode:b.output_mode,circulation_modes:[...(b.allow_controlled?['controlled_compute']:[]),...(b.allow_api?['api']:[]),...(b.allow_download?['download']:[])],quality_min_complete:b.quality_min_complete,max_rows:b.max_rows,min_group_size:b.min_group_size};
 const saved=await api('/demands',{...r,expected_version:r.version,execution_constraints,quality:b.quality_min_complete===1?'完整':'完整率',delivery:b.output_mode==='aggregate'?'汇总':'对象级'});await load();show(saved,'执行条件已保存，需重新确认规格');
});
action('人工确认规格',async()=>{if(kind!=='demand')throw Error('请选择需求单');const r=need();if(!r.execution_constraints)throw Error('请先设置执行条件');show({object_scope:r.object_scope,time_start:r.time_start,time_end:r.time_end,execution_constraints:r.execution_constraints},'待核对的实际执行条件');const b=await form('受理岗逐项确认',[['content','已核对目标、范围及用途','checkbox'],['attachments','已核对附件及适用性','checkbox'],['gaps','已查看四类缺口并确认后续处理','checkbox'],['execution_constraints','已核对范围说明与实际筛选、时间字段和交付条件一致','checkbox'],['note','修改与确认说明','textarea']]);if(b){show(await api('/demands/'+r.id+'/confirm',{expected_version:r.version,confirmations:{content:b.content,attachments:b.attachments,gaps:b.gaps,execution_constraints:b.execution_constraints},note:b.note}),'任务规格');await load();}});
action('生成执行方案',()=>{if(kind!=='spec')throw Error('请先切换任务规格并选择已确认规格');location.href='/static/execution/index.html?tab=plan&spec='+encodeURIComponent(need().id);});
action('历史版本',async()=>show(await api('/items/'+kind+'/'+need().id+'/history')));await load();

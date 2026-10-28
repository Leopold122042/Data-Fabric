import {api,action,list,need,show,form,edit,init,$,tabs} from './ui.js';
import {currentAccess} from '/static/shared/access.js';
async function main(){
if(!await init('评价与改进复测','评价关联运行证据。选择指定服务时，请仅填写该服务的质量情况；跨供给方任务的整体评价不会展开给各服务供给方。同范围复测保留改善和未改善结果。'))return;let kind='feedback';const load=()=>list(kind);$('#refresh').onclick=load;tabs([['feedback','用户评价'],['improvement','改进复测'],['metric_definition','指标口径']],async v=>{kind=v;await load();});
action('查看运行证据',async()=>show(await api('/runs'),'可引用的运行记录'));
action('提交评价',async()=>{
 const principal=currentAccess().principal,query=new URLSearchParams(location.search);
 const runs=(await api('/runs')).items.filter(run=>['success','failed','blocked'].includes(run.status)&&(principal.role==='platform'||run.actor===principal.id));
 if(!runs.length)throw Error('暂无已结束且可评价的运行记录。');
 const preferred=runs.find(run=>run.id===query.get('run_id'))||runs[0];
 const runChoices=runs.map(run=>[run.id,(run.name||run.id)+' · '+({success:'成功',failed:'失败',blocked:'已阻断'}[run.status])+' · '+new Date(run.updated_at).toLocaleString()]);
 const choice=await form('选择评价记录',[['run_id','已结束的运行','select',runChoices]],{run_id:preferred.id});
 if(!choice)return;
 const run=runs.find(item=>item.id===choice.run_id),serviceIds=new Set();
 for(const node of Object.values(run.nodes||{}))if(node.status==='success'&&node.trace?.service_id)serviceIds.add(node.trace.service_id);
 // Work-order links also identify actually invoked services whose calls failed.
 // The server verifies their pinned plan version and actual invocation evidence.
 const scoped=query.get('run_id')===run.id?query.get('service_id'):null;if(scoped)serviceIds.add(scoped);
 const services=(await api('/items/service')).items,names=new Map(services.map(item=>[item.id,item.name]));
 const scopes=[['','整个任务'],...[...serviceIds].map(id=>[id,'指定服务 · '+(names.get(id)||id)])];
 const b=await form('运行质量评价',[['service_id','评价范围','select',scopes],...['usability','explainability','timeliness','satisfaction'].map((x,i)=>[x,['可用性','解释性','及时性','满意度'][i]+'（1—5）','number',[],3]),['comment','说明（请针对所选任务或服务填写）','textarea']],{service_id:scoped||''});
 if(b){const result=await api('/feedback',{run_id:run.id,...(b.service_id?{service_id:b.service_id}:{}),comment:b.comment,ratings:{usability:b.usability,explainability:b.explainability,timeliness:b.timeliness,satisfaction:b.satisfaction}});await load();show(result,'评价已保存');}
});
action('新增复测对比',()=>edit(kind,'/improvements','相同任务范围复测',[['before','基线运行ID（逗号分隔）','list'],['after','复测运行ID（逗号分隔）','list'],['change_note','修订内容','textarea']]));
const defs=[['name','指标名称'],['definition','计算定义','textarea'],['source','数据来源'],['denominator','分母'],['window','观察窗口']];action('新增指标口径',()=>edit(kind,'/metric-definitions','指标口径',defs));action('修订指标口径',()=>{if(kind!=='metric_definition')throw Error('请选择指标口径');return edit(kind,'/metric-definitions','修订口径',defs,need());});await load();

}
main().catch(error=>{const host=document.querySelector('#status');if(host)host.textContent=error.message;});

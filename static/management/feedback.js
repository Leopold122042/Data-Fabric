import {api,action,list,need,show,form,edit,init,$,tabs} from './ui.js';
async function main(){
if(!await init('评价与改进复测','评价关联真实运行证据；同范围复测保留改善和未改善结果。暂无证据时不生成评分。'))return;let kind='feedback';const load=()=>list(kind);$('#refresh').onclick=load;tabs([['feedback','用户评价'],['improvement','改进复测'],['metric_definition','指标口径']],async v=>{kind=v;await load();});
action('查看运行证据',async()=>show(await api('/runs'),'可引用的运行记录'));
action('提交评价',async()=>{const b=await form('任务评价',[['run_id','运行ID'],...['usability','explainability','timeliness','satisfaction'].map((x,i)=>[x,['可用性','解释性','及时性','满意度'][i]+'（1—5）','number',[],3]),['comment','说明','textarea']]);if(b){show(await api('/feedback',{run_id:b.run_id,comment:b.comment,ratings:{usability:b.usability,explainability:b.explainability,timeliness:b.timeliness,satisfaction:b.satisfaction}}));await load();}});
action('新增复测对比',()=>edit(kind,'/improvements','相同任务范围复测',[['before','基线运行ID（逗号分隔）','list'],['after','复测运行ID（逗号分隔）','list'],['change_note','修订内容','textarea']]));
const defs=[['name','指标名称'],['definition','计算定义','textarea'],['source','数据来源'],['denominator','分母'],['window','观察窗口']];action('新增指标口径',()=>edit(kind,'/metric-definitions','指标口径',defs));action('修订指标口径',()=>{if(kind!=='metric_definition')throw Error('请选择指标口径');return edit(kind,'/metric-definitions','修订口径',defs,need());});await load();

}
main().catch(error=>{const host=document.querySelector('#status');if(host)host.textContent=error.message;});

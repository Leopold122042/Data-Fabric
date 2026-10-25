import {api,action,list,need,show,form,init,$,tabs} from './ui.js';
await init('设计运行校准','按对象、实际加载版本和环境配对。缺失运行证据显示未配对；关闭偏差必须提供后续复测证据。');let kind='case';const load=()=>list(kind);$('#refresh').onclick=load;tabs([['case','偏差工单'],['pair','配对结果']],async v=>{kind=v;await load();});
action('执行对账',async()=>{show(await api('/reconciliation/run',{}));await load();});action('健康指标',async()=>show(await api('/reconciliation/metrics'),'最近30天指标与口径'));
action('处置偏差',async()=>{if(kind!=='case')throw Error('请选择偏差工单');const r=need(),b=await form('分级处置',[['action','动作','select',[['observe','观察'],['local_fix','局部修正并重试'],['pause_review','暂停执行并复核'],['design_change','转设计变更'],['close','凭复测证据关闭']]],['note','处置说明','textarea'],['evidence_id','关闭证据运行ID']]);if(b){show(await api('/cases/'+r.id+'/action',{...b,expected_version:r.version}));await load();}});action('处置历史',async()=>show(await api('/items/'+kind+'/'+need().id+'/history')));await load();

action('隔离扰动验证',async()=>show(await api('/reconciliation/experiment',{}),'本地扰动与版本对照结果'));

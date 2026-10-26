import {api,action,list,need,show,form,edit,history,init,$} from './ui.js';
await init('数据目录与血缘','扫描本地源及已纳入的外部表结构，登记结构、业务说明、责任人和限制；改名需人工确认，保留对象编号。');
const load=()=>list('object');$('#refresh').onclick=()=>load().catch(e=>show(e.message));
action('扫描目录变更',async()=>{show(await api('/catalog/scan',{}),'本次变化');await load();});
action('编辑说明',()=>edit('object','/catalog/'+need().id+'/annotate','元数据说明',[
 ['meaning','业务含义','textarea'],['domain','业务域'],['owner','责任人'],['update_period','更新周期'],['classification','分类','select',['待确认','健康信息','个人标识','敏感','公开','内部']],['constraints','使用限制','json']],need()));
action('影响范围',async()=>show(await api('/catalog/'+need().id+'/impact'),'上下游影响'));
action('确认源字段改名',async()=>{const r=need(),b=await form('确认改名（先确认源端变更，再执行扫描）',[['name','新的字段名']],r);if(b){show(await api('/catalog/'+r.id+'/rename',{...b,expected_version:r.version}));await load();}});
action('登记全局标识',()=>edit('object','/identifiers','标识映射（本地演示结构）',[['local_id','本地对象ID'],['global_id','全局标识']],null));
action('导出目录',async()=>{const d=await api('/catalog/export');const url=URL.createObjectURL(new Blob([JSON.stringify(d,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='catalog-demo.json';a.click();URL.revokeObjectURL(url);});history('object');await load();
action('接入本地文件',()=>edit('object','/sources','源文件应位于MEDFABRIC_DATA_DIR内',[['source_id','新数据源ID'],['name','名称'],['connector','连接器','select',['sqlite','csv','json']],['path','相对数据目录的文件路径']]));
action('字段统计剖析',async()=>{show(await api('/catalog/'+need().id+'/profile',{}));await load();});
action('登记加工血缘',()=>edit('object','/relations','上下游关系',[['source','上游对象ID'],['target','下游对象ID'],['type','关系类型','select',['transforms','uses','depends_on']]]));

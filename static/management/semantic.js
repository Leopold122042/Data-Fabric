import {allowed} from '/static/shared/access.js';
import {element,table,button} from '/static/platform/common.js';
import {api,action,list,need,show,form,edit,history,init,$,tabs} from './ui.js';
async function main(){
if(!await init('概念与映射','候选匹配仅提供建议，类型冲突会直接否决。复杂映射保存结构化定义，执行时使用已确认的映射版本。'))return;let kind='concept';
const load=()=>list(kind);$('#refresh').onclick=()=>load();tabs([['concept','业务概念'],['mapping','映射记录'],['mapping_rule','复用规则']],async v=>{kind=v;await load();});
const fields=[['name','概念名称'],['meaning','业务含义','textarea'],['domain','业务域'],['aliases','同义名称（逗号分隔）','list'],['context_keywords','上下文关键词（表名、相邻字段等，逗号分隔）','list'],['data_type','数据类型','select',['text','integer','real','date','boolean']],['aggregate_only','仅用于归集','checkbox'],['parent_id','父概念ID（可空）'],['range','取值范围（可选min/max）','json']];
action('新建概念',()=>edit(kind,'/concepts','新建概念',fields));action('修改概念',()=>{if(kind!=='concept')throw Error('请切换到业务概念');return edit(kind,'/concepts','编辑概念',fields,need());});
action('候选字段',async()=>{
 if(kind!=='concept')throw Error('请选择具体属性概念');const concept=need(),rows=await api('/concepts/'+concept.id+'/candidates');show('','候选排序与排除依据');const host=$('#record-preview');host.replaceChildren(element('p','基础分由字段名35%、结构20%、取值25%、上下文20%组成。有效的已确认同名同类型映射可加0.1分，总分不超过1；经验不补足缺失证据，也不覆盖类型或范围冲突。匹配分不是统计概率，高分建议仍需人工确认。'));
 const label={suggested:'建议采用，待确认',needs_confirmation:'待人工判断',rejected:'已排除'};
 host.append(table(['字段 / 来源','匹配分','证据分项','处理建议','依据','操作'],rows.map(r=>[
 r.name+' · '+r.source+' / '+r.table,r.score,
 Object.entries(r.components).map(([k,v])=>({name:'名称',structure:'结构',values:'取值',context:'上下文'}[k])+': '+(v.available?v.score:'缺证据')).join('；'),
 label[r.status],r.veto.concat(r.uncertainty,(r.experience?.rules||[]).map(rule=>'复用已确认映射 '+rule.mapping_id+' v'+rule.mapping_version+'（'+rule.confirmed_by+'）')).join('；')||'证据完整',r.veto.length?'不能建立':!allowed('mapping.create')?'—':button('建立映射草稿',async()=>{const result=await api('/mappings',{concept_id:concept.id,fields:[r.field_id],mapping_type:'direct',status:'draft'});show(result,'映射草稿已建立，复核后再确认');})
 ])));
});
const maps=[['concept_id','概念ID'],['fields','字段ID（逗号分隔）','list'],['mapping_type','映射类型','select',[['direct','直接'],['conditional','条件'],['derived','派生'],['join','跨表']]],['expression','结构化表达式（复杂映射填写）','json'],['status','状态','select',[['draft','草稿'],['confirmed','人工确认'],['disabled','停用']]],['confirmation_note','确认依据','textarea']];
action('新增映射',async()=>{const concepts=(await api('/items/concept')).items.filter(c=>!c.aggregate_only);const options=maps.map(f=>f[0]==='concept_id'?['concept_id','统一概念','select',concepts.map(c=>[c.id,c.name+' · '+c.domain+' · '+c.id])]:f);const b=await form('建立映射',options,{fields:(new URLSearchParams(location.search).get('fields')||'').split(',').filter(Boolean)});if(b){show(await api('/mappings',b));kind='mapping';await load();}});action('复核映射',()=>{if(kind!=='mapping')throw Error('请切换到映射记录');return edit(kind,'/mappings','复核映射',maps,need());});
action('检查可执行映射',async()=>{if(kind!=='concept')throw Error('请选择概念');show(await api('/concepts/'+need().id+'/resolve'));});action('历史版本',async()=>show(await api('/items/'+kind+'/'+need().id+'/history')));await load();
action('导入既有语义映射',async()=>{show(await api('/semantic/import-legacy',{}));await load();});

action('四类映射说明',()=>show({直接对应:{说明:'一个源字段对应一个概念',expression:{}},有条件对应:{说明:'条件字段可与输出字段类型不同',expression:{predicate:{operator:'eq',args:[{field:'类别字段ID'},{literal:'机构'}]},then:{field:'名称字段ID'},else:{literal:null}}},多个字段计算:{说明:'例如数量×单价；字段必须为数值类型',expression:{operator:'multiply',args:[{field:'数量字段ID'},{field:'单价字段ID'}]}},跨表关联:{说明:'声明两张表的关联字段与最终输出字段',expression:{on:[['客户表机构编号字段ID','机构表编号字段ID']],field:'机构名称字段ID'}}},'映射定义与示例'));
action('建立客户、机构、指标概念',async()=>{show(await api('/semantic/business-concepts',{}),'业务对象概念已建立；请补充本机构定义与属性');kind='concept';await load();});

}
main().catch(error=>{const host=document.querySelector('#status');if(host)host.textContent=error.message;});

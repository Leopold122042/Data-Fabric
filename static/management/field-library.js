import {api,init,$,esc,form,message,show} from './ui.js';
import {allowed} from '/static/shared/access.js';
const labels={draft:'待确认',confirmed:'已确认',review_required:'待复核',retired:'已停用'};
let rows=[],selected=null;
async function main(){
 if(!await init('字段定义库','按来源表维护字段的中文名称、定义和别名。逐条审核后确认，已确认定义用于自然语言需求匹配；结构变化需要重新复核。'))return;
 const toolbar=$('#subtabs');
 toolbar.innerHTML='<label>来源表 <select id="table-filter"><option value="">全部表</option></select></label><label>审核状态 <select id="state-filter"><option value="">全部状态</option>'+Object.entries(labels).map(([v,t])=>`<option value="${v}">${t}</option>`).join('')+'</select></label>';
 const actions=$('#actions');
 function button(text,fn,write=false){if(write&&!allowed('object.annotate'))return;const b=document.createElement('button');b.className='mf-button';b.textContent=text;b.onclick=async()=>{b.disabled=true;try{await fn();}catch(e){message(e.message,true);}finally{b.disabled=false;}};actions.append(b);}
 function need(){if(!selected)throw Error('请先选择一个字段');return selected;}
 button('同步新增字段',async()=>{await api('/field-library/sync',{});await load();},true);
 button('编辑定义',async()=>{const r=need();const body=await form('编辑字段定义（保存后需重新确认）',[
 ['label','中文名称'],['definition','业务定义','textarea'],['aliases','别名与常用说法（逗号分隔）','list'],['unit','单位与口径'],['notes','取值规则、限制及歧义','textarea'],['basis','定义依据','textarea']],r);
 if(body){await api(`/field-library/${r.id}/save`,{...body,expected_version:r.version});await load();}},true);
 button('确认当前字段',async()=>{const r=need();if(r.status==='retired')throw Error('停用字段不能确认');show(r,'请核对当前字段定义');const body=await form(`确认 ${r.source_id} / ${r.table} / ${r.name}`,[['definition_review','待确认定义','textarea'],['confirmation_note','确认依据（请核对定义后填写）']],{definition_review:r.definition});if(body){if(body.definition_review!==r.definition)throw Error('定义有改动，请先通过“编辑定义”保存后再确认');await api(`/field-library/${r.id}/confirm`,{confirmation_note:body.confirmation_note,expected_version:r.version});await load();}},true);
 button('历史版本',async()=>show(await api(`/field-library/${need().id}/history`),'字段定义历史'));
 button('试匹配需求',async()=>{const b=await form('用已确认字段试匹配',[['text','自然语言需求','textarea']]);if(b){const result=await api('/field-library/match',b);show(result,'已确认字段匹配候选');message(`匹配到 ${result.items.length} 个候选；同名字段需结合来源表确认`);}});
 button('导出字段库',async()=>{const url=URL.createObjectURL(new Blob([JSON.stringify({schema_version:1,items:rows},null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='field-library.json';a.click();URL.revokeObjectURL(url);});
 async function load(){const id=selected?.id;rows=(await api('/field-library')).items;selected=rows.find(x=>x.id===id)||null;const old=$('#table-filter').value;$('#table-filter').innerHTML='<option value="">全部表</option>'+[...new Set(rows.filter(x=>x.status!=='retired').map(r=>r.source_id+' / '+r.table))].sort().map(t=>`<option>${esc(t)}</option>`).join('');$('#table-filter').value=old;render();if(selected)show(selected,selected.label);}
 function render(){const q=$('#search').value.trim().toLowerCase(),table=$('#table-filter').value,state=$('#state-filter').value;const filtered=rows.filter(r=>(!table||r.source_id+' / '+r.table===table)&&(!state||r.status===state)&&(!q||JSON.stringify(r).toLowerCase().includes(q))).sort((a,b)=>`${a.source_id}/${a.table}/${a.name}`.localeCompare(`${b.source_id}/${b.table}/${b.name}`));
 $('#list').innerHTML='<thead><tr><th>来源表 / 字段</th><th>中文名称与定义</th><th>审核</th></tr></thead><tbody>'+filtered.map(r=>`<tr><td><button class="mf-button mf-link" data-id="${esc(r.id)}">${esc(r.name)}</button><div>${esc(r.source_id)} / ${esc(r.table)}</div><small>${esc(r.data_type)}</small></td><td>${esc(r.label)}<br><small>${esc(r.definition||'待补充定义')}</small></td><td>${labels[r.status]} · v${r.version}</td></tr>`).join('')+'</tbody>';$('#empty').hidden=filtered.length>0;
 $('#list').querySelectorAll('[data-id]').forEach(b=>b.onclick=()=>{selected=rows.find(r=>r.id===b.dataset.id);show(selected,selected.label);});
 const active=rows.filter(r=>r.status!=='retired');message(`${new Set(active.map(r=>r.source_id+'/'+r.table)).size} 张表 · ${active.length} 个字段 · 已确认 ${active.filter(r=>r.status==='confirmed').length} · 待处理 ${active.filter(r=>r.status!=='confirmed').length} · 当前显示 ${filtered.length}`);}
 $('#refresh').onclick=()=>load().catch(e=>message(e.message,true));$('#search').oninput=render;$('#table-filter').onchange=render;$('#state-filter').onchange=render;await load();
}
main().catch(e=>message(e.message,true));

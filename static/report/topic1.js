const esc=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const sourceNames={his:'医院 HIS',lis:'检验 LIS',pacs:'影像 PACS',iot:'监护 IoT',emr:'电子病历 EMR','src-clinical-operations':'临床运营主题库'};
const sourceOrder=['his','lis','pacs','iot','emr'];
const tableOrder={his:['patient','visit','orders'],lis:['test_order','result'],iot:['vitals','devices']};
const relationSpecs=[
 {key:'patient_id',field:'patient_id',label:'患者编号',title:'患者编号关联候选',color:'#2985a1',rootSource:'his',rootTable:'patient'},
 {key:'visit_id',field:'visit_id',label:'就诊编号',title:'就诊编号关联候选',color:'#b9823b',rootSource:'his',rootTable:'visit'},
 {key:'test_no',field:'test_no',label:'检验申请单号',title:'检验申请单关联候选',color:'#4b9679',rootSource:'lis',rootTable:'test_order'}
];

let activeObserver=null;

export function renderKnowledgeGraph(host){
 if(!host)return;
 activeObserver?.disconnect();
 host.innerHTML='<div class="kg-loading" role="status">正在读取统一目录和字段库元数据…</div>';
 Promise.all([
  fetch('/api/portal/catalog',{credentials:'same-origin',headers:{Accept:'application/json'}}),
  fetch('/api/catalog',{credentials:'same-origin',headers:{Accept:'application/json'}})
 ]).then(async([catalogResponse,viewsResponse])=>{
  if(!catalogResponse.ok)throw new Error(`目录接口返回 ${catalogResponse.status}`);
  if(!viewsResponse.ok)throw new Error(`逻辑视图接口返回 ${viewsResponse.status}`);
  const [catalog,viewCatalog]=await Promise.all([catalogResponse.json(),viewsResponse.json()]);
  const viewByPhysicalTable=new Map();
  for(const view of viewCatalog.logical_views||[]){
   for(const physical of view.physical_tables||[])viewByPhysicalTable.set(String(physical).toLowerCase(),view.label||view.entity);
  }
  const tables=(catalog.tables||[]).map(table=>{
   const simpleName=String(table.name||'').split('(')[0].trim();
   const physicalKey=`${table.source}.${simpleName}`.toLowerCase();
   return {...table,simpleName,logicalView:viewByPhysicalTable.get(physicalKey)||'独立逻辑表'};
  }).sort(compareTables).map((table,index)=>({...table,index}));
  const relations=makeRelations(tables);
  mountGraph(host,tables,relations);
 }).catch(error=>{
  host.innerHTML=`<section class="kg-panel"><header class="kg-heading"><div><p class="kg-eyebrow">字段关系图谱</p><h2>目录暂不可用</h2></div></header><div class="kg-error" role="alert"><p>读取统一目录失败，无法生成当前字段图谱。</p><small>${esc(error.message)}</small><button type="button" class="kg-retry">重试</button></div></section>`;
  host.querySelector('.kg-retry')?.addEventListener('click',()=>renderKnowledgeGraph(host));
 });
}

function compareTables(a,b){
 const sourceA=sourceOrder.indexOf(String(a.source).toLowerCase()),sourceB=sourceOrder.indexOf(String(b.source).toLowerCase());
 const orderA=sourceA<0?sourceOrder.length:sourceA,orderB=sourceB<0?sourceOrder.length:sourceB;
 if(orderA!==orderB)return orderA-orderB;
 const nameA=a.simpleName.toLowerCase(),nameB=b.simpleName.toLowerCase();
 const names=tableOrder[String(a.source).toLowerCase()]||[];
 const indexA=names.indexOf(nameA),indexB=names.indexOf(nameB);
 return (indexA<0?99:indexA)-(indexB<0?99:indexB)||nameA.localeCompare(nameB,'zh-CN');
}

function makeRelations(tables){
 return relationSpecs.map(spec=>{
  const members=[];
  tables.forEach(table=>{
   for(const field of table.fields||[]){
    if(String(field.name).toLowerCase()===spec.field&&field.label===spec.label&&field.status==='confirmed')members.push({table,field});
   }
  });
  return {...spec,members};
 }).filter(relation=>relation.members.length>1);
}

function mountGraph(host,tables,relations){
 if(!tables.length){host.innerHTML='<section class="kg-panel"><header class="kg-heading"><div><p class="kg-eyebrow">字段关系图谱</p><h2>暂无已登记逻辑表</h2></div></header><p class="kg-empty">请先扫描并登记数据源结构，字段库会同步生成图谱节点。</p></section>';return;}
 const state={tables,relations,selectedTable:chooseInitialTable(tables,relations),selectedRelation:null,search:'',mode:'overview'};
 host.innerHTML=`<section class="kg-panel" aria-labelledby="kg-title">
  <header class="kg-heading"><div><p class="kg-eyebrow">目录元数据 · 字段语义 · 跨表关系候选</p><h2 id="kg-title">字段与逻辑表知识图谱</h2><p>全部表关系展示完整网络；选中表关系聚焦当前表。点击表查看字段，点击字段突出显示对应连线。</p></div><div class="kg-counts"><strong>${tables.length}</strong><span>张表</span><i></i><strong>${tables.reduce((sum,table)=>sum+(table.fields||[]).length,0)}</strong><span>个字段</span><i></i><strong>${relations.length}</strong><span>类候选关联</span></div></header>
  <div class="kg-toolbar"><div class="kg-modes" role="group" aria-label="图谱视图"><button type="button" data-graph-mode="overview">全部表关系</button><button type="button" data-graph-mode="focus">选中表关系</button></div><span>图谱只读取表结构和字段定义，不读取业务数据行。</span><label><span>筛选</span><input class="kg-search" type="search" placeholder="搜索表名、字段或来源" aria-label="搜索逻辑表和字段"></label></div>
  <div class="kg-layout"><div class="kg-graph-scroll" aria-label="逻辑表关系图，可横向滚动"><div class="kg-canvas"><svg class="kg-edges" aria-hidden="true"></svg><div class="kg-source-grid"></div></div></div><aside class="kg-inspector" aria-live="polite"></aside></div>
  <footer class="kg-legend"><span><i class="kg-legend-node"></i>逻辑表</span><span><i class="kg-legend-field"></i>字段归属</span><span><i class="kg-legend-line"></i>字段关联候选</span><small>连线依据已确认的字段定义生成关联候选；跨系统身份关系仍需业务核对，且连线不表示数据库物理外键。没有明确标识字段的表保持独立。</small></footer>
 </section>`;

 const render=()=>renderGraph(host,state);
 host.querySelector('.kg-graph-scroll').addEventListener('click',event=>{
  const tableButton=event.target.closest('[data-table-index]');
  if(tableButton){state.selectedTable=Number(tableButton.dataset.tableIndex);state.selectedRelation=null;state.search='';host.querySelector('.kg-search').value='';render();return;}
 });
 host.querySelector('.kg-search').addEventListener('input',event=>{state.search=event.target.value.trim().toLowerCase();render();});
 host.addEventListener('click',event=>{
  const modeButton=event.target.closest('[data-graph-mode]');
  if(modeButton){state.mode=modeButton.dataset.graphMode;render();return;}
  const fieldButton=event.target.closest('[data-field-index]');
  if(fieldButton){
   const table=state.tables.find(item=>item.index===state.selectedTable),field=table?.fields?.[Number(fieldButton.dataset.fieldIndex)];
   const relation=state.relations.find(item=>item.members.some(member=>member.field.id===field?.id));
   state.selectedRelation=relation?.key||null;render();return;
  }
  const related=event.target.closest('[data-related-table]');
  if(!related)return;
  state.selectedTable=Number(related.dataset.relatedTable);state.search='';state.selectedRelation=related.dataset.relationKey||state.selectedRelation;
  const search=host.querySelector('.kg-search');if(search)search.value='';render();
 });
 render();
}

function chooseInitialTable(tables,relations){
 const patient=relations.find(relation=>relation.key==='patient_id')?.members.find(member=>member.table.source==='his'&&member.table.simpleName==='patient');
 return patient?.table.index??tables[0].index;
}

function renderGraph(host,state){
 const {tables,relations,selectedTable,selectedRelation,search}=state;
 const selected=tables.find(table=>table.index===selectedTable)||tables[0];
 const visible=tables.filter(table=>{
  if(!search)return true;
  const haystack=[table.source,table.name,table.logicalView,...(table.fields||[]).flatMap(field=>[field.name,field.label,field.definition])].join(' ').toLowerCase();
  return haystack.includes(search);
 });
 const visibleSet=new Set(visible.map(table=>table.index));
 const groups=new Map();
 for(const table of visible){const key=String(table.source||'other');if(!groups.has(key))groups.set(key,[]);groups.get(key).push(table);}
 const orderedGroups=[...groups].sort((a,b)=>sourceSort(a[0])-sourceSort(b[0]));
 host._selectedTable=selectedTable;host._graphMode=state.mode;
 host.classList.toggle('kg-overview',state.mode==='overview');
 host.querySelectorAll('[data-graph-mode]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.graphMode===state.mode)));
 const grid=host.querySelector('.kg-source-grid');
 grid.innerHTML=orderedGroups.map(([source,items])=>`<section class="kg-source-column"><header><small>${esc(sourceNames[source.toLowerCase()]||source)}</small><b>${items.length} 张表</b></header>${items.map(table=>tableNode(table,relations,selectedTable)).join('')}</section>`).join('');
 host.querySelector('.kg-inspector').innerHTML=inspector(selected,relations,selectedRelation);
 layoutGraph(host,state,visibleSet);
 drawEdges(host,relations,visibleSet,selectedRelation);
 if(activeObserver)activeObserver.disconnect();
 const canvas=host.querySelector('.kg-canvas');
 if(globalThis.ResizeObserver){activeObserver=new ResizeObserver(()=>{layoutGraph(host,state,visibleSet);drawEdges(host,relations,visibleSet,state.selectedRelation);});activeObserver.observe(host.querySelector('.kg-graph-scroll'));}
 requestAnimationFrame(()=>requestAnimationFrame(()=>drawEdges(host,relations,visibleSet,state.selectedRelation)));
}

function sourceSort(source){const index=sourceOrder.indexOf(String(source).toLowerCase());return index<0?sourceOrder.length:index;}

function tableNode(table,relations,selectedTable){
 const rels=relations.filter(relation=>relation.members.some(member=>member.table.index===table.index));
 const tags=rels.map(relation=>`<span style="--relation-color:${relation.color}">${esc(relation.label)}</span>`).join('');
 return `<button type="button" class="kg-table-node ${selectedTable===table.index?'is-selected':''}" data-table-index="${table.index}" aria-pressed="${selectedTable===table.index}" title="${esc(table.source+'.'+table.name)}"><span class="kg-table-source">${esc(table.logicalView)}</span><strong>${esc(table.simpleName)}</strong><small>${esc(table.source)} · ${(table.fields||[]).length} 个字段</small><span class="kg-node-keys">${tags||'<i>未发现共享标识字段</i>'}</span></button>`;
}

function inspector(table,relations,selectedRelation){
 const fields=table.fields||[];
 const relationById=new Map();
 for(const relation of relations)for(const member of relation.members)relationById.set(member.field.id,relation);
 const chosen=relations.find(relation=>relation.key===selectedRelation&&relation.members.some(member=>member.table.index===table.index));
 const list=fields.map((field,index)=>{
  const relation=relationById.get(field.id),active=relation?.key===selectedRelation;
  return `<button type="button" class="kg-field-row ${relation?'has-relation':''} ${active?'is-active':''}" data-field-index="${index}" aria-pressed="${active}" title="点击查看字段关联" style="${relation?`--relation-color:${relation.color}`:''}"><span class="kg-field-heading"><b>${esc(field.label||field.name)}</b><code>${esc(field.name)}</code><em>${esc(field.type||'未知类型')}</em></span><small>${esc(field.definition||'字段含义待补充')}</small>${relation?`<span class="kg-field-relation" style="--relation-color:${relation.color}">关联键 · ${esc(relation.label)}</span>`:''}<span class="kg-field-status ${field.status==='confirmed'?'confirmed':''}">${field.status==='confirmed'?'已确认定义':'定义待确认'}</span></button>`;
 }).join('');
 const relationPanel=chosen?`<section class="kg-related-panel"><h4><i style="--relation-color:${chosen.color}"></i>${esc(chosen.title)}</h4><p>具有相同字段名和已确认定义的表构成以下关联候选；跨系统标识口径请继续核对：</p><ul>${chosen.members.map(member=>`<li><button type="button" data-related-table="${member.table.index}" data-relation-key="${chosen.key}" aria-current="${member.table.index===table.index?'true':'false'}"><span>${esc(sourceNames[String(member.table.source).toLowerCase()]||member.table.source)} · ${esc(member.table.simpleName)}</span><b>${esc(member.field.label)} <code>${esc(member.field.name)}</code></b></button></li>`).join('')}</ul></section>`:selectedRelation?`<section class="kg-related-panel kg-related-empty"><p>当前表没有此关联字段。选择列表中的关联表继续查看。</p></section>`:`<section class="kg-related-panel kg-related-empty"><p>点击一个字段，查看它是否连接到其他逻辑表。</p></section>`;
 return `<div class="kg-inspector-heading"><p>已选逻辑表</p><h3>${esc(table.logicalView)} · ${esc(table.simpleName)}</h3><span>${esc(sourceNames[String(table.source).toLowerCase()]||table.source)} / ${esc(table.name)} / ${fields.length} 个字段</span></div><div class="kg-field-list" aria-label="表字段列表">${list}</div>${relationPanel}`;
}

function layoutGraph(host,state,visibleSet){
 const grid=host.querySelector('.kg-source-grid');
 const nodes=[...grid.querySelectorAll('.kg-table-node')];
 nodes.forEach(node=>grid.append(node));
 grid.querySelectorAll('.kg-source-column').forEach(column=>column.remove());
 if(state.mode==='overview'){
  const canvas=host.querySelector('.kg-canvas');
  const width=Math.max(640,host.querySelector('.kg-graph-scroll').clientWidth-48);
  const related=nodes.filter(node=>state.relations.some(r=>r.members.some(m=>m.table.index===Number(node.dataset.tableIndex))));
  const isolated=nodes.filter(node=>!related.includes(node));
  const nodeWidth=Math.min(148,width/5.2),cx=width/2,cy=245,rx=width/2-nodeWidth/2-18,ry=185;
  related.forEach((node,index)=>{
   const angle=-Math.PI/2+index*2*Math.PI/Math.max(related.length,1);
   Object.assign(node.style,{position:'absolute',width:nodeWidth+'px',left:(cx+rx*Math.cos(angle)-nodeWidth/2)+'px',top:(cy+ry*Math.sin(angle)-38)+'px'});
  });
  const columns=Math.max(1,Math.floor(width/(nodeWidth+14)));
  isolated.forEach((node,index)=>Object.assign(node.style,{position:'absolute',width:nodeWidth+'px',left:(18+(index%columns)*(width-36)/columns)+'px',top:(510+Math.floor(index/columns)*90)+'px'}));
  grid.style.minWidth='0';grid.style.width=width+'px';canvas.style.width=width+'px';
  grid.style.height=(isolated.length?510+Math.ceil(isolated.length/columns)*90:490)+'px';
  return;
 }
 grid.style.width='';grid.style.minWidth='';host.querySelector('.kg-canvas').style.width='';
 const focus=state.selectedTable;
 const connected=new Set();
 state.relations.forEach(relation=>{if(relation.members.some(m=>m.table.index===focus))relation.members.forEach(m=>{if(m.table.index!==focus)connected.add(m.table.index);});});
 const neighbours=nodes.filter(n=>connected.has(Number(n.dataset.tableIndex)));
 const others=nodes.filter(n=>Number(n.dataset.tableIndex)!==focus&&!connected.has(Number(n.dataset.tableIndex)));
 const height=Math.max(500,neighbours.length*155+90);
 grid.style.height=(height+Math.ceil(others.length/4)*130+90)+'px';
 nodes.forEach(node=>{node.style.position='absolute';node.style.width='210px';});
 const center=nodes.find(n=>Number(n.dataset.tableIndex)===focus);
 if(center){center.style.left='60px';center.style.top=(height/2-55)+'px';}
 neighbours.forEach((node,index)=>{node.style.left='790px';node.style.top=(55+index*155)+'px';});
 others.forEach((node,index)=>{node.style.left=(35+(index%4)*250)+'px';node.style.top=(height+60+Math.floor(index/4)*130)+'px';});
}

function drawEdges(host,relations,visibleSet,selectedRelation){
 const canvas=host.querySelector('.kg-canvas'),svg=host.querySelector('.kg-edges');
 if(!canvas||!svg)return;
 const width=Math.max(canvas.scrollWidth,canvas.clientWidth),height=Math.max(canvas.scrollHeight,canvas.clientHeight);
 svg.setAttribute('width',String(width));svg.setAttribute('height',String(height));svg.setAttribute('viewBox',`0 0 ${width} ${height}`);
 const canvasRect=canvas.getBoundingClientRect(),pieces=[];
 for(const relation of relations){
  const members=relation.members.filter(member=>visibleSet.has(member.table.index));
  if(members.length<2)continue;
  const root=(host._graphMode==='focus'&&members.find(member=>member.table.index===host._selectedTable))||members.find(member=>member.table.source===relation.rootSource&&member.table.simpleName===relation.rootTable)||members[0];
  for(const target of members){
   if(target===root)continue;
   if(host._graphMode==='focus'&&root.table.index!==host._selectedTable&&target.table.index!==host._selectedTable)continue;
   const a=canvas.querySelector(`[data-table-index="${root.table.index}"]`),b=canvas.querySelector(`[data-table-index="${target.table.index}"]`);
   if(!a||!b)continue;
   const ar=a.getBoundingClientRect(),br=b.getBoundingClientRect();
   let x1,y1,x2,y2,d;
   const ax=ar.left-canvasRect.left,ay=ar.top-canvasRect.top,bx=br.left-canvasRect.left,by=br.top-canvasRect.top;
   if(Math.abs(ax-bx)<6){
    const upper=ay<=by?{rect:ar,base:ay}:{rect:br,base:by},lower=ay<=by?{rect:br,base:by}:{rect:ar,base:ay};
    x1=upper.rect.left-canvasRect.left+upper.rect.width/2;y1=upper.base+upper.rect.height;
    x2=lower.rect.left-canvasRect.left+lower.rect.width/2;y2=lower.base;
    d=`M ${x1} ${y1} C ${x1} ${y1+15}, ${x2} ${y2-15}, ${x2} ${y2}`;
   }else{
    const forward=ax<bx;
    x1=forward?ax+ar.width:ax;x2=forward?bx:bx+br.width;
    const offset=(relations.indexOf(relation)-1)*25;
    y1=ay+ar.height/2+offset;y2=by+br.height/2+offset;
    const mid=(x1+x2)/2;d=`M ${x1} ${y1} C ${mid} ${y1}, ${mid} ${y2}, ${x2} ${y2}`;
   }
   const active=selectedRelation===relation.key;
   const mx=(x1+x2)/2,my=(y1+y2)/2-4,label=esc(relation.field);
   pieces.push(`<g class="kg-edge-group ${active?'is-active':''}" style="--relation-color:${relation.color}"><path d="${d}"/><circle cx="${x1}" cy="${y1}" r="3.5"/><circle cx="${x2}" cy="${y2}" r="3.5"/><text x="${mx}" y="${my}" text-anchor="middle">${label}</text></g>`);
  }
 }
 svg.innerHTML=pieces.join('');
}

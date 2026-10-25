/* Shared inventory view; all data comes from the existing session-scoped external API. */
(() => {
  const hosts=[...document.querySelectorAll('[data-external-inventory]')];
  if(!hosts.length)return;
  const link=(label,url)=>{const a=document.createElement('a');a.textContent=label;a.href=url;return a;};
  const text=(parent,tag,value)=>{const e=document.createElement(tag);e.textContent=value;parent.append(e);return e;};
  const url=id=>'/static/platform/connections.html'+(id?'?external_table='+encodeURIComponent(id):'');
  async function api(path,body){const response=await fetch('/api/external/'+path,{method:body?'POST':'GET',headers:{'X-Medfabric-External':'1','Content-Type':'application/json','X-Demo-Role':document.querySelector('#role')?.value||'platform'},...(body?{body:JSON.stringify(body)}:{})});const value=await response.json();if(!response.ok)throw Error(value.detail||value.error||'外部目录暂不可用');return value;}
  for(const host of hosts){
    host.classList.add('external-inventory');
    const head=text(host,'div','');head.className='external-inventory-head';text(head,'h2','外部数据资源');
    const nav=text(head,'div','');nav.append(link('连接云盘 / 更新目录',url()),link('平台目录','/static/management/catalog.html'),link('概念映射','/static/management/semantic.html'));
    const status=text(host,'p','正在读取外部目录…');status.setAttribute('role','status');
    const context=host.dataset.externalInventory;
    text(host,'p',context==='services'?'选中表结构纳入目录后，可用对象 ID 登记服务来源。查询适配与发布验证通过前，不会自动生成服务。':context==='graph'?'统一虚拟表由来源表组成；字段同名不代表业务含义相同。纳入目录后，可进一步建立概念映射与加工血缘。':'目录读取自已保存的外部来源；连接状态与医疗五源指标分别展示。');
    const body=text(host,'details','');body.open=['overview','catalog','discovery'].includes(context);text(body,'summary','查看来源表与字段 / 纳入平台目录');
    const controls=text(body,'div','');controls.className='external-inventory-controls';const search=document.createElement('input');search.placeholder='搜索外部表名或字段';search.setAttribute('aria-label','搜索外部表');controls.append(search);
    const refresh=text(controls,'button','刷新外部目录');refresh.type='button';
    const publish=text(controls,'button','将所选表结构纳入平台目录');publish.type='button';
    const notice=text(body,'p','纳入操作会向平台用户共享所选表名和字段结构；不复制源数据、云盘路径或令牌。');notice.className='external-inventory-note';
    const list=text(body,'div','');const selected=new Set();let data={tables:[],collections:[]},page=0;const size=8;
    function render(){
      list.replaceChildren();const query=search.value.toLowerCase();const rows=data.tables.filter(t=>(t.name+' '+(t.columns||[]).join(' ')).toLowerCase().includes(query));
      if(!rows.length)text(list,'p',data.tables.length?'没有匹配的表。':'暂无已保存来源表，请连接云盘并扫描目录。');
      const group=text(list,'div','');group.className='external-inventory-groups';for(const c of data.collections)group.append(link(c.name+'（'+(c.sources?.length??c.source_ids?.length??0)+' 个来源）',url(c.id)));
      for(const t of rows.slice(page*size,(page+1)*size)){
        const row=text(list,'div','');row.className='external-inventory-row';const label=text(row,'label','');const cb=document.createElement('input');cb.type='checkbox';cb.checked=selected.has(t.id);cb.setAttribute('aria-label','纳入 '+t.name);cb.onchange=()=>cb.checked?selected.add(t.id):selected.delete(t.id);label.append(cb,document.createTextNode(t.name));
        const meta=text(row,'span',(t.columns?.length||0)+' 个字段 · '+(t.status||'已保存结构'));meta.className='external-inventory-note';row.append(link('查看 / 查询',url(t.id)));
        const detail=text(row,'details','');text(detail,'summary','字段结构');text(detail,'p',(t.columns||[]).map((c,i)=>c+' ('+(t.types?.[i]||'未知')+')').join('；'));
      }
      if(rows.length>size){const pager=text(list,'div','');const prev=text(pager,'button','上一页');prev.disabled=page===0;prev.onclick=()=>{page--;render();};text(pager,'span',` ${page+1}/${Math.ceil(rows.length/size)} `);const next=text(pager,'button','下一页');next.disabled=(page+1)*size>=rows.length;next.onclick=()=>{page++;render();};}
    }
    async function load(){refresh.disabled=true;try{data=await api('discovery/catalog');const connection=await api('platform/status');status.textContent=`${connection.connected?'云盘已连接':'云盘未连接 · 查询时需授权'} · ${data.tables.length} 张来源表 · ${data.collections.length} 张统一虚拟表`;page=0;render();}catch(e){status.textContent='读取失败：'+e.message;}finally{refresh.disabled=false;}}
    search.oninput=()=>{page=0;render();};refresh.onclick=load;
    publish.onclick=async()=>{if(!selected.size){status.textContent='请先勾选需要纳入的来源表。';return;}publish.disabled=true;try{const r=await api('platform/publish',{table_ids:[...selected],share_metadata:true});status.textContent=r.message;document.querySelector('#refresh')?.click();}catch(e){status.textContent=e.message;}finally{publish.disabled=false;}};
    load();
    // Returning from cloud login/scan refreshes the shared inventory without downloading source files.
    window.addEventListener('focus',load);
    window.addEventListener('external-catalog-updated',load);
  }
})();

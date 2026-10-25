/* Credentials are sent only at connect; server retains them in session memory until disconnect/expiry. */
(() => {
  const el = id => document.getElementById(id);
  let externalTarget=new URLSearchParams(location.search).get('external_table');
  let ready = false, busy = false, cloudConnected = false, pendingCloudAction=null;
  const status = message => { el('extStatus').textContent = message; };
  function fieldBrowser(host, record, run){
    host.replaceChildren();
    text(host,'h4','字段结构与条件查询');
    text(host,'p','勾选返回字段；可添加多个条件（同时满足）。不添加条件则浏览前 200 行。');
    const grid=text(host,'div','');grid.className='virtual-grid';grid.style.maxHeight='260px';grid.style.overflow='auto';
    table(grid,['返回','字段名','类型'],record.columns.map((c,i)=>['',c,record.types?.[i]||'按映射定义']));
    const picks=[];
    grid.querySelectorAll('tbody tr').forEach((row,i)=>{
      const cb=document.createElement('input');cb.type='checkbox';cb.checked=true;cb.setAttribute('aria-label','返回字段 '+record.columns[i]);row.firstElementChild.append(cb);picks.push(cb);
    });
    const toggle=text(host,'button','全选 / 取消全选');toggle.className='ghost';toggle.onclick=()=>{const next=!picks.every(p=>p.checked);picks.forEach(p=>p.checked=next);};
    const rules=text(host,'div','');const entries=[];
    const add=text(host,'button','添加筛选条件');add.className='ghost';add.onclick=()=>{
      const row=text(rules,'div','');row.className='virtual-toolbar';
      const field=text(row,'select','');field.setAttribute('aria-label','筛选字段');record.columns.forEach(c=>{const o=text(field,'option',c);o.value=c;});
      const op=text(row,'select','');op.setAttribute('aria-label','比较方式');
      [['eq','等于'],['contains','包含文字'],['>','大于（数值）'],['>=','大于等于（数值）'],['<','小于（数值）'],['<=','小于等于（数值）'],['null','为空值'],['notnull','不为空值']].forEach(([v,label])=>{text(op,'option',label).value=v;});
      const value=text(row,'input','');value.placeholder='条件值，例如：北京市';value.setAttribute('aria-label','条件值');op.onchange=()=>{value.disabled=['null','notnull'].includes(op.value);};
      const entry={row,field,op,value};entries.push(entry);
      text(row,'button','移除').onclick=()=>{entries.splice(entries.indexOf(entry),1);row.remove();};
    };
    const query=text(host,'button','按字段查询');query.className='act';query.onclick=()=>action(async()=>{
      const sql=VirtualFieldQuery.build(record.id,record.columns,record.columns.filter((c,i)=>picks[i].checked),entries.map(e=>({column:e.field.value,op:e.op.value,value:e.value.value})));
      await run(sql);
    });
  }
  async function api(path, body) {
    const response = await fetch('/api/external/' + path, {
      method: body === undefined ? 'GET' : 'POST', credentials:'same-origin',
      headers:{'Content-Type':'application/json','X-Medfabric-External':'1','X-Demo-Role':localStorage.getItem('mf-demo-role')||'platform','X-External-Grant':el('externalGrant')?.value||'','X-External-Purpose':encodeURIComponent(el('externalGrant')?.selectedOptions[0]?.dataset.purpose||'')},
      body: body === undefined ? undefined : JSON.stringify(body)
    });
    const data = await response.json();
    if (!response.ok) {const error=new Error(typeof data.detail === 'string' ? data.detail : '请求参数无效，请检查输入。');error.status=response.status;throw error;}
    return data;
  }
  async function loadGrants(){
    const select=el('externalGrant');if(!select)return;const previous=select.value;
    try{const data=await api('governance/status');select.replaceChildren();text(select,'option','请选择读取授权').value='';const subject='demo-'+(localStorage.getItem('mf-demo-role')||'platform');for(const g of data.grants.filter(g=>g.subject===subject&&g.status==='active'&&Date.parse(g.expires_at)>Date.now())){const option=text(select,'option',g.purpose+' · '+g.table_ids.length+' 张表 · '+g.id);option.value=g.id;option.dataset.purpose=g.purpose;}select.value=previous;el('externalGrantStatus').textContent='读取前和返回结果前均检查授权；新增或变化的来源需重新授权。';}catch(e){el('externalGrantStatus').textContent=e.message;}
  }
  window.addEventListener('external-grants-updated',loadGrants);
  async function action(fn) {
    if (busy) return;
    busy = true;
    el('tab-external').querySelectorAll('button').forEach(b => b.disabled = true);
    status('正在处理，请稍候…');
    try { await fn(); if(el('extStatus').textContent==='正在处理，请稍候…')status('操作完成。'); } catch (error) { status(error.message); }
    finally { busy = false; el('tab-external').querySelectorAll('button').forEach(b => b.disabled = false); }
  }
  function text(parent, tag, value) {
    const n = document.createElement(tag); n.textContent = String(value ?? ''); parent.appendChild(n); return n;
  }
  function table(parent, columns, rows) {
    const t = document.createElement('table'); t.className = 'tbl';
    const head = t.createTHead().insertRow(); columns.forEach(c => text(head,'th',c));
    const body = t.createTBody(); rows.forEach(row => { const tr = body.insertRow(); row.forEach(v => text(tr,'td',v === null ? '' : v)); });
    parent.appendChild(t);
  }
  function groupedResults(parent,data){
    const groups=VirtualFieldQuery.groupResults(data.columns,data.rows);
    groups.forEach(group=>{
      const section=text(parent,'section','');section.className='virtual-result-group';
      section.style.margin='16px 0';section.style.padding='12px';section.style.border='1px solid var(--border, #d6dce5)';section.style.borderRadius='8px';
      const file=(group.member||group.path).split('/').pop();
      text(section,'h4',file+' / '+group.sheet+' · '+group.rows.length+' 条结果');
      const source=text(section,'details','');text(source,'summary','来源位置');
      const location=text(source,'p',group.path+(group.member?' → '+group.member:''));location.style.overflowWrap='anywhere';
      const grid=text(section,'div','');grid.style.overflowX='auto';grid.style.maxWidth='100%';
      if(group.columns.length)table(grid,group.columns,group.rows);
      else text(grid,'p','这些记录没有可展示的非空业务字段。');
    });
    if(!groups.length)text(parent,'p','没有包含该关键词的数据。');
  }
  function render(data) {
    el('extTables').replaceChildren();
    if (!data.tables.length) text(el('extTables'),'p','尚未载入外部文件。');
    data.tables.forEach(t => {
      const block = text(el('extTables'),'div',''); block.style.marginBottom = '14px';
      text(block,'b',`${t.name} ← ${t.label} · ${t.rows} 行 · ${t.source}`);
      text(block,'p',`${t.location} · 刷新时间 ${new Date(t.refreshed_at).toLocaleString()}`);
      text(block,'p',t.columns.map(c => `${c.name} (${c.type})`).join(' · '));
      const b = text(block,'button','预览前 20 行'); b.className='ghost';
      b.onclick=()=>{ el('extSql').value=`SELECT * FROM "${t.name.replaceAll('"','""')}" LIMIT 20;`; runQuery(); };
    });
    el('extEvents').replaceChildren();
    data.events.forEach(e => text(el('extEvents'),'p',`${new Date(e.time).toLocaleString()} · ${e.source} · ${e.message}`));
  }
  async function initialize() {
    try{await loadVirtualCatalog();}catch(error){
      el('extVirtualDefinitions').textContent='旧 CESAI 目录不可用：'+error.message;
      const connection=await api('platform/status');cloudConnected=Boolean(connection.connected);
    }
    await loadDiscovered();
    const data = await api('status'); render(data);
    if (!el('extGithub').value) el('extGithub').value=data.defaults.github_url;
    if (!el('extExcel').value) el('extExcel').value=data.defaults.box_path;
    ready=true; status(cloudConnected?'已连接云盘，可以直接查询。':'已显示保存的虚拟表目录。查询数据前请连接云盘。');
  }
  const box = path => cloudConnected ? {path} : ({path,username:el('extUser').value.trim(),token:el('extToken').value});
  async function browse() {
    if(!cloudConnected){openCloudLogin(browse);return;}
    const data=await api('box/list',box(el('extDir').value)); el('extFiles').replaceChildren();
    if(!data.items.length) text(el('extFiles'),'p','此目录无可见条目。');
    data.items.forEach(item=>{
      const b=text(el('extFiles'),'button',(item.directory?'目录：':'文件：')+item.name);
      b.className='ghost';b.style.display='block';b.style.margin='5px 0';
      b.onclick=()=>action(async()=>{if(item.directory){el('extDir').value=item.path;await browse();}else{el('extExcel').value=item.path;status('已选择文件，点击读取或刷新文件。');}});
    }); status(`目录已读取：${data.items.length} 项（最多 ${data.limit} 项）。`);
  }
  let scanDirectory=null;
  function resetScanDirectory(){
    scanDirectory=null;el('extScanDirectoryPanel').hidden=true;
    ['extScanBreadcrumb','extScanDirectories','extScanDirStatus'].forEach(id=>el(id).replaceChildren());
    el('extScanDirChoose').hidden=true;
  }
  async function browseScanDirectory(path=''){
    if(!cloudConnected){openCloudLogin(()=>browseScanDirectory(path));return;}
    el('extScanDirectoryPanel').hidden=false;el('extScanDirChoose').hidden=true;
    el('extScanDirectories').replaceChildren();el('extScanBreadcrumb').replaceChildren();
    el('extScanDirStatus').textContent='正在读取目录列表…';
    try{
      const data=await api('box/list',{path});
      scanDirectory=data.path||'';
      const crumbs=el('extScanBreadcrumb');
      const root=text(crumbs,'button','云盘');root.className='ghost';root.onclick=()=>action(()=>browseScanDirectory(''));
      let prefix='';
      scanDirectory.split('/').filter(Boolean).forEach(part=>{
        prefix+=(prefix?'/':'')+part;const target=prefix;
        text(crumbs,'span','/');const b=text(crumbs,'button',part);b.className='ghost';b.onclick=()=>action(()=>browseScanDirectory(target));
      });
      const directories=data.items.filter(i=>i.directory).sort((a,b)=>a.name.localeCompare(b.name,'zh-CN'));
      const files=data.items.length-directories.length;
      el('extScanDirStatus').textContent=`当前：${scanDirectory||'云盘根目录'} · ${directories.length} 个子目录、${files} 个文件${data.items.length>=data.limit?'（列表可能被截断）':''}`;
      const host=el('extScanDirectories');
      if(!directories.length)text(host,'p','当前目录没有子文件夹。仍可选择当前目录扫描其中的文件。');
      directories.forEach(item=>{
        const row=text(host,'div','');row.className='virtual-toolbar';
        const enter=text(row,'button','📁 '+item.name);enter.className='ghost';enter.onclick=()=>action(()=>browseScanDirectory(item.path));
        const choose=text(row,'button','选为扫描目录');choose.className='ghost';choose.setAttribute('aria-label','选择扫描目录 '+item.name);
        choose.onclick=()=>chooseScanDirectory(item.path);
      });
      el('extScanDirChoose').hidden=false;
      status('目录已加载，选择文件夹后再点击“扫描并建立虚拟表”。');
    }catch(error){
      el('extScanDirStatus').textContent='读取目录失败：'+error.message;
      if(error.status===401){cloudConnected=false;openCloudLogin(()=>browseScanDirectory(path));return;}
      throw error;
    }
  }
  function chooseScanDirectory(path){
    el('extScanRoot').value=path;
    status('已选择扫描目录：'+(path||'云盘根目录')+'。点击“扫描并建立虚拟表”开始。');
    el('extScanRoot').focus();
  }
  el('extScanBrowse').onclick=()=>action(()=>browseScanDirectory(''));
  el('extScanDirRoot').onclick=()=>action(()=>browseScanDirectory(''));
  el('extScanDirUp').onclick=()=>action(()=>browseScanDirectory((scanDirectory||'').split('/').filter(Boolean).slice(0,-1).join('/')));
  el('extScanDirReload').onclick=()=>action(()=>browseScanDirectory(scanDirectory||''));
  el('extScanDirChoose').onclick=()=>{if(scanDirectory!==null)chooseScanDirectory(scanDirectory);};
  function runQuery() { return action(async()=>{
    const data=await api('query',{sql:el('extSql').value}); el('extResult').replaceChildren();
    table(el('extResult'),data.columns,data.rows);
    status(`查询完成：展示 ${data.rows.length} 行，${data.elapsed_ms} ms${data.truncated?'；结果已截断到 200 行':''}。`);
  }); }
  async function loadFile(source, body) {
    if(source==='box'&&!cloudConnected){const next={...body};delete next.username;delete next.token;openCloudLogin(()=>loadFile(source,next));return;}
    const data = await api(source+'/load',body);
    if (data.archive) {
      const a=data.archive;
      el('extArchivePanel').hidden=false;
      el('extArchiveInfo').textContent=`${a.location} · ${a.items.length} 项 · 解压总大小 ${(a.total_size/1024/1024).toFixed(2)} MB`;
      el('extArchiveFiles').replaceChildren();
      a.items.forEach(item=>{
        const row=text(el('extArchiveFiles'),'div',`${item.name} · ${item.size} 字节 `);
        if(item.supported){
          const b=text(row,'button','读取为逻辑表');b.className='ghost';
          // Credentials are read afresh, never captured in the archive listing.
          const location=a.location, model=body.model;
          b.onclick=()=>action(()=>loadFile(source,{
            ...(source==='github'?{url:location,model}:box(location)),
            archive_member:item.name,archive_digest:a.digest
          }));
        }else{text(row,'span',item.directory?'目录':'仅列出（格式不支持或文件过大）');}
      });
      status('压缩包文件列表已读取。请选择内部文件。');
    }else{
      render(data);el('extResult').replaceChildren();
      if(!body.archive_member)el('extArchivePanel').hidden=true;
      status('文件已读取为逻辑表，请点击下方预览。');
    }
  }
  el('extGithubLoad').onclick=()=>action(()=>loadFile('github',{url:el('extGithub').value,model:el('extModel').value}));
  el('extBoxLoad').onclick=()=>action(()=>loadFile('box',box(el('extExcel').value)));
  let virtualId=null;
  async function loadVirtualCatalog(){
    const data=await api('virtual/catalog');
    cloudConnected=Boolean(data.connected);
    el('extCloudState').textContent=cloudConnected?'数据源已连接 · 当前会话可直接查询':'数据源未连接 · 目录已保存，查询时需连接';
    el('extCloudDisconnect').hidden=!cloudConnected;
    el('extCloudConnect').textContent=cloudConnected?'更换云盘连接':'连接云盘';
    el('extCloudConnect').hidden=false;
    virtualId=data.views[0]?.id || null;
    [['extVirtualMetric','指标','全部指标'],['extVirtualScope','口径','全部口径']].forEach(([id,key,label])=>{
      const select=el(id),previous=select.value;select.replaceChildren();const all=text(select,'option',label);all.value='';
      const values=new Set(data.sources.flatMap(src=>Object.values(src.sheet_capabilities||{}).flatMap(c=>c[key]||[])));
      [...values].sort().forEach(value=>{const option=text(select,'option',value);option.value=value;});
      if(values.has(previous))select.value=previous;
    });
    const host=el('extVirtualDefinitions');host.replaceChildren();
    const catalogGrid=text(host,'div','');catalogGrid.className='virtual-grid';
    table(catalogGrid,['虚拟表名称','查询表名','源文件','工作表','建档记录数','存储方式'],data.views.map(v=>[
      v.name,v.id,v.sources.length,data.sources.filter(src=>v.sources.includes(src.id)).reduce((n,src)=>n+src.sheets.length,0),
      v.observed_rows,'保存映射 · 查询时读取'
    ]));
    if(data.views[0]&&!el('extVirtualFields').hasChildNodes())fieldBrowser(el('extVirtualFields'),data.views[0],async sql=>{el('extVirtualSql').value=sql;await queryVirtual();});
    text(host,'h4','字段结构');
    const chips=text(host,'div','');chips.className='virtual-chips';
    (data.views[0]?.columns || []).forEach(c=>{const chip=text(chips,'span',c);chip.className='virtual-chip';});
    const sources=text(host,'details','');sources.className='virtual-details';sources.open=true;
    text(sources,'summary','来源映射 · 每行对应一个实际工作表');
    const sourceGrid=text(sources,'div','');sourceGrid.className='virtual-grid';
    table(sourceGrid,['来源编号','来源文件','压缩包内部文件','实际工作表'],data.sources.flatMap(src=>src.sheets.map(sheet=>[
      src.id,src.path,src.member || '—',sheet
    ])));
    const relations=text(host,'details','');relations.className='virtual-details';
    text(relations,'summary','关系规则与口径说明');
    const relationGrid=text(relations,'div','');relationGrid.className='virtual-grid';
    table(relationGrid,['关联字段','规则','确认状态'],data.relations.map(r=>[r.keys.join(' + '),r.rule,r.status]));
    data.notes.forEach(n=>text(relations,'p',n));
    status(cloudConnected?'目录已加载，云盘已连接，可以查询。':'目录已加载，无需登录；查询数据前请连接云盘。');
  }
  el('extVirtualCatalog').onclick=()=>action(loadVirtualCatalog);
  function openCloudLogin(continuation=null){
    pendingCloudAction=continuation;
    status('请在弹窗中连接云盘；取消后仍可查看目录。');
    el('extLoginError').textContent='';
    el('extLoginSubmit').textContent=continuation?'连接并继续':'连接云盘';
    if(!el('extLoginDialog').open)el('extLoginDialog').showModal();
    el('extUser').focus();
  }
  el('extCloudConnect').onclick=()=>openCloudLogin();
  el('extLoginCancel').onclick=()=>el('extLoginDialog').close();
  el('extLoginDialog').addEventListener('cancel',event=>{if(busy)event.preventDefault();});
  el('extLoginDialog').addEventListener('close',()=>{pendingCloudAction=null;el('extToken').value='';if(!cloudConnected)status('已取消连接，目录和查询条件已保留。');});
  el('extLoginForm').onsubmit=event=>{
    event.preventDefault();
    action(async()=>{
      try {
        el('extLoginError').textContent='正在验证连接…';
        await api('box/connect',{username:el('extUser').value.trim(),token:el('extToken').value});
        resetScanDirectory();
        el('extToken').value='';
        await loadVirtualCatalog();
        await loadDiscovered();
        const continuation=pendingCloudAction;
        pendingCloudAction=null;
        el('extLoginDialog').close();
        status('云盘已连接。');
        if(continuation)await continuation();
      } catch(error){
        if(el('extLoginDialog').open){
          el('extLoginError').textContent=error.message;
          cloudConnected=false;
        }
        throw error;
      }
    });
  };
  el('extCloudDisconnect').onclick=()=>action(async()=>{
    scanning=false;resetScanDirectory();el('extCollectionEditor').replaceChildren();el('extCollectionEditor').hidden=true;el('extAutoResult').replaceChildren();el('extAutoEditor').hidden=true;autoTable=null;render(await api('box/disconnect',{}));el('extToken').value='';
    ['extVirtualResult','extVirtualSources','extResult','extFiles','extArchiveFiles'].forEach(id=>el(id).replaceChildren());
    el('extArchivePanel').hidden=true;
    el('extVirtualStatus').textContent='已断开连接并释放云盘缓存，虚拟表定义仍然保留。';
    await loadVirtualCatalog();
  });
  function virtualBrowse(entity) {
    if(!virtualId){status('请先刷新虚拟表目录。');return;}
    const name='"'+virtualId.replaceAll('"','""')+'"';
    const literal="'"+entity.replaceAll("'","''")+"'";
    const columns=['资料集','口径','对象名称','指标','排名','数值'];
    const where=entity?' WHERE '+columns.map(c=>`contains(lower(CAST("${c}" AS VARCHAR)), lower(${literal}))`).join(' OR '):'';
    el('extVirtualSql').value=`SELECT * FROM ${name}${where} LIMIT 201;`;
    el('extVirtualQuery').click();
  }
  el('extVirtualSearch').onclick=()=>{
    const value=el('extVirtualEntity').value.trim();
    virtualBrowse(value);
  };
  el('extVirtualPreview').onclick=()=>virtualBrowse('');
  el('extVirtualEntity').addEventListener('keydown',e=>{if(e.key==='Enter'&&!busy){e.preventDefault();el('extVirtualSearch').click();}});
  el('extVirtualExample').onclick=()=>{el('extVirtualSql').value='SELECT * FROM cesai_indicator_index WHERE "对象名称" = \'北京市\' ORDER BY "口径", "指标" LIMIT 100;';};
  async function queryVirtual(request){
    request=request || {view_id:virtualId,sql:el('extVirtualSql').value,force_refresh:el('extVirtualForce').checked};
    if(!virtualId)throw new Error('请先刷新虚拟表目录。');
    if(!cloudConnected){openCloudLogin(()=>queryVirtual(request));return;}
    el('extVirtualStatus').textContent='正在选择来源并检查文件缓存，请稍候…';
    el('extVirtualResult').replaceChildren();el('extVirtualSources').replaceChildren();
    try {
      const data=await api('virtual/query',request);
      el('extVirtualForce').checked=false;
      table(el('extVirtualResult'),data.columns,data.rows);
      if(!data.rows.length)text(el('extVirtualResult'),'p','没有包含该关键词的记录，可以清空关键词后浏览数据。');
      el('extVirtualStatus').textContent=`查询范围：原 CESAI 指标表（15 个工作表）。本次选择 ${data.plan.selected_sources}/${data.plan.total_sources} 个来源，映射 ${data.loaded_rows} 条；结果展示 ${data.rows.length} 行${data.truncated?'（超过 200 行已截断）':''}。读取时间 ${new Date(data.fetched_at).toLocaleString()}。${data.cache}`;
      data.sources.forEach(src=>{
        const row=text(el('extVirtualSources'),'p',`${src.path}${src.member?' :: '+src.member:''} · ${src.sheets.join('、')} · ${src.cache_status || ''} · 文件下载时间 ${src.downloaded_at?new Date(src.downloaded_at).toLocaleString():'—'} `);
        const b=text(row,'button','打开来源文件');b.className='ghost';
        b.onclick=()=>action(()=>loadFile('box',box(src.path)));
      });
      status('虚拟表查询完成，结果含来源定位。');
    } catch(error){el('extVirtualStatus').textContent='本次查询失败，未展示旧结果。';if(error.status===401){cloudConnected=false;await loadVirtualCatalog();openCloudLogin(()=>queryVirtual(request));return;}throw error;}
  }
  el('extVirtualQuery').onclick=()=>action(()=>queryVirtual());
  el('extList').onclick=()=>action(browse);
  el('extUp').onclick=()=>action(async()=>{el('extDir').value=el('extDir').value.replace(/\/+$/,'').split('/').slice(0,-1).join('/');await browse();});
  el('extQuery').onclick=runQuery;
  el('extExample').onclick=()=>{el('extSql').value='SELECT passenger_class, COUNT(*) AS total_passengers,\nROUND(AVG(CAST(survived AS INTEGER))*100, 2) AS survival_rate\nFROM passengers GROUP BY passenger_class ORDER BY passenger_class;';};
  el('extClear').onclick=()=>action(async()=>{scanning=false;resetScanDirectory();el('extCollectionEditor').replaceChildren();el('extCollectionEditor').hidden=true;el('extAutoResult').replaceChildren();el('extAutoEditor').hidden=true;autoTable=null;await api('clear',{});el('extToken').value='';cloudConnected=false;virtualId=null;['extVirtualDefinitions','extVirtualResult','extVirtualSources','extVirtualStatus'].forEach(id=>el(id).replaceChildren());el('extArchivePanel').hidden=true;el('extArchiveFiles').replaceChildren();el('extFiles').replaceChildren();el('extResult').replaceChildren();await initialize();status('已释放逻辑表并清除页面中的令牌。');});
  let scanning=false,scanInFlight=false,autoTable=null,previewHeader=null;
  let queryGroups=[];
  function selectQueryScope(){
    const id=el('extQueryScope').value;
    el('extLegacyQuery').hidden=id!=='legacy:cesai';
    el('extCollectionEditor').hidden=true;
    el('extQueryScopeHint').textContent=id==='legacy:cesai'?'当前范围：CESAI 指标数据集，使用预配置文件与工作表；其来源配置独立于扫描目录。':'';
    if(id==='legacy:cesai')return;
    const group=queryGroups.find(g=>g.id===id);
    if(group)openCollection(group,null,false);
    else el('extQueryScopeHint').textContent='暂无扫描生成的统一虚拟表，请先扫描建立，或主动选择CESAI 指标表。';
  }
  function updateQueryScopes(groups){
    queryGroups=groups;
    const select=el('extQueryScope'),previous=select.value;
    select.replaceChildren();
    if(!groups.length)text(select,'option','暂无新虚拟表').value='';
    groups.forEach(g=>{text(select,'option',g.name+'（'+g.sources.length+' 张来源表）').value=g.id;});
    text(select,'option','旧 CESAI 指标表（3 个文件 / 15 个工作表）').value='legacy:cesai';
    select.value=previous==='legacy:cesai'||groups.some(g=>g.id===previous)?previous:(groups[0]?.id||'');
    if(select.value!==previous||el('extCollectionEditor').hidden)selectQueryScope();
  }
  el('extQueryScope').onchange=selectQueryScope;
  async function changeMembership(group,source,restore=false){
    if(!cloudConnected){openCloudLogin(()=>changeMembership(group,source,restore));return;}
    try{
      await api('discovery/collection-membership',{view_id:group.id,source_id:source.id,restore});
      el('extCollectionEditor').replaceChildren();el('extCollectionEditor').hidden=true;
      await loadDiscovered();status(restore?'已恢复到统一虚拟表。':'已从统一虚拟表移除，重新扫描也不会自动加入。可在“已移除的表”中恢复。');
    }catch(error){if(error.status===401){cloudConnected=false;openCloudLogin(()=>changeMembership(group,source,restore));return;}throw error;}
  }
  function renderCollections(groups){
    const host=el('extCollections');host.replaceChildren();
    if(!groups.length){text(host,'p','完成一次扫描后，这里会生成统一虚拟表。之前的单表目录仍保留。');return;}
    groups.forEach(g=>{
      const section=text(host,'section','');
      text(section,'h4',g.name+' · '+g.sources.length+' 张表'+(g.incomplete?' · 部分项目未扫描成功':''));
      const open=text(section,'button','查询此统一表');open.className='ghost';
      open.onclick=()=>openCollection(g);
      if(!g.sources.length)text(section,'p','当前统一表没有来源，可从下方恢复已移除的表。');
      const excluded=text(section,'details','');text(excluded,'summary','已移除的表（'+(g.excluded_sources||[]).length+'）');
      (g.excluded_sources||[]).forEach(source=>{const row=text(excluded,'p',source.name+' ');text(row,'button','恢复').onclick=()=>action(()=>changeMembership(g,source,true));});
      const tree=text(section,'div','');tree.className='virtual-source-tree';
      const branches=new Map();
      function branch(parts){
        let parent=tree,key='';
        parts.forEach(part=>{
          key+='/'+part;
          if(!branches.has(key)){
            const node=text(parent,'details','');node.open=true;node.style.marginLeft='16px';
            text(node,'summary',part);branches.set(key,node);
          }
          parent=branches.get(key);
        });
        return parent;
      }
      g.sources.forEach(source=>{
        const parts=[...source.path.split('/').filter(Boolean),...source.member.split('/').filter(Boolean)];
        const parent=branch(parts);
        const leaf=text(parent,'div','');leaf.style.margin='6px 0 6px 22px';
        text(leaf,'span',source.sheet+' ');
        const select=text(leaf,'button','到查询区');select.className='ghost';
        select.onclick=()=>openCollection(g,source.id);
        const remove=text(leaf,'button','移除此表');remove.className='ghost';
        remove.onclick=()=>action(()=>changeMembership(g,source));
      });
    });
  }
  function openCollection(g,selectedSource=null,scroll=true){
    el('extQueryScope').value=g.id;el('extLegacyQuery').hidden=true;
    el('extQueryScopeHint').textContent='当前范围：'+g.name+'。仅查询该虚拟表当前保留的来源。';
    const host=el('extCollectionEditor');host.hidden=false;host.replaceChildren();
    host.className='external-query-layout';
    const controls=text(host,'div','');controls.className='external-query-controls';
    text(controls,'span','QUERY').className='external-eyebrow';
    text(controls,'h4','搜索数据');text(controls,'p',g.name);
    const output=text(host,'section','');output.className='external-query-output';output.setAttribute('aria-label','查询结果');
    text(output,'span','RESULTS').className='external-eyebrow';text(output,'h4','查询结果');
    const state=text(output,'p','选择左侧范围并搜索，结果会显示在这里。');state.setAttribute('role','status');state.setAttribute('aria-live','polite');state.className='external-result-status';
    const result=text(output,'div','');result.className='virtual-result-groups';
    text(result,'div','尚未查询 · 输入关键词，发现来源数据').className='external-result-empty';
    const sourcePanel=text(controls,'details','');text(sourcePanel,'summary',selectedSource?'查询范围：已选择一张来源表':'查询范围：全部来源（可调整）');
    const search=text(sourcePanel,'input','');search.placeholder='按目录、文件名或工作表筛选来源';search.setAttribute('aria-label','筛选统一表来源');search.style.width='min(100%,500px)';
    const grid=text(sourcePanel,'div','');grid.className='virtual-grid';grid.style.maxHeight='300px';grid.style.overflow='auto';
    table(grid,['查询','目录','来源文件','包内文件','工作表','状态'],g.sources.map(r=>['',r.directory,r.path,r.member,r.sheet,r.status]));
    const choices=[];
    grid.querySelectorAll('tbody tr').forEach((row,i)=>{const cb=document.createElement('input');cb.type='checkbox';cb.checked=!selectedSource||g.sources[i].id===selectedSource;cb.setAttribute('aria-label','查询来源 '+g.sources[i].sheet);row.firstElementChild.append(cb);choices.push({row,cb,source:g.sources[i]});});
    search.oninput=()=>choices.forEach(e=>{e.row.hidden=![e.source.directory,e.source.path,e.source.member,e.source.sheet].join(' ').toLowerCase().includes(search.value.toLowerCase());});
    text(sourcePanel,'button','选择当前可见来源').onclick=()=>choices.forEach(e=>{e.cb.checked=!e.row.hidden;});
    text(sourcePanel,'p','可调整本次查询的来源范围；选择表名只设置查询范围，不读取内容。');
    const toolbar=text(controls,'div','');toolbar.className='virtual-toolbar external-search-form';
    controls.insertBefore(toolbar,sourcePanel);
    text(toolbar,'label','搜索内容 ');
    const keyword=text(toolbar,'input','');keyword.placeholder='例如：北京（匹配任意业务字段中包含的文字）';keyword.setAttribute('aria-label','搜索数据内容');keyword.style.minWidth='0';
    const searchButton=text(toolbar,'button','搜索');searchButton.className='act';
    text(controls,'p','包含匹配所有业务字段。留空浏览前 200 行；不同来源分组展示。').className='external-help';
    async function run(request){
      if(!cloudConnected){openCloudLogin(()=>run(request));return;}
      result.replaceChildren();result.hidden=false;
      state.textContent=`正在读取 ${request.source_ids.length} 张来源表的文件、解压并查询；选择较多来源时需要更长时间。`;status(state.textContent);result.replaceChildren();text(result,'div','正在读取和整理数据…').className='external-result-empty is-loading';output.setAttribute('aria-busy','true');
      try{const data=await api('discovery/collection-query',request);result.replaceChildren();groupedResults(result,data);result.hidden=false;state.textContent=`查询范围：${g.name}。读取 ${data.source_count} 张来源表、${data.loaded_rows} 行，命中 ${data.matched_source_count ?? data.source_count} 张表，返回 ${data.rows.length} 行${data.truncated?'（还有更多匹配，当前仅显示前 200 行）':''}。`;status('查询完成。'+state.textContent);}
      catch(e){result.replaceChildren();text(result,'div','查询未完成，请根据上方提示重试。').className='external-result-empty';state.textContent=e.message;if(e.status===401){cloudConnected=false;openCloudLogin(()=>run(request));return;}throw e;}finally{output.setAttribute('aria-busy','false');}
    }
    searchButton.onclick=()=>action(async()=>{
      const source_ids=choices.filter(e=>e.cb.checked).map(e=>e.source.id);
      if(!source_ids.length)throw new Error('请先选择来源表。');
      await run({view_id:g.id,source_ids,keyword:keyword.value});
    });
    keyword.onkeydown=e=>{if(e.key==='Enter'&&!busy){e.preventDefault();searchButton.click();}};
    if(scroll)host.scrollIntoView({behavior:'smooth',block:'start'});
  }
  async function loadDiscovered(){
    const data=await api('discovery/catalog');
    renderCollections(data.collections||[]);
    updateQueryScopes(data.collections||[]);
    if(autoTable){const updated=data.tables.find(t=>t.id===autoTable.id);if(updated){const changed=JSON.stringify(updated.columns)!==JSON.stringify(autoTable.columns);autoTable=updated;if(changed)fieldBrowser(el('extAutoFields'),updated,async sql=>{el('extAutoSql').value=sql;await autoQuery('query');});}}
    const host=el('extScanTables');host.replaceChildren();
    table(host,['虚拟表','状态','字段数','扫描行数','说明','操作'],data.tables.map(t=>[t.name,t.status,t.columns.length,t.rows,t.reason,'']));
    const rows=host.querySelectorAll('tbody tr');
    data.tables.forEach((item,i)=>{
      const b=text(rows[i].lastElementChild,'button','查看 / 查询');b.className='ghost';
      b.onclick=()=>{
        autoTable=item;previewHeader=null;el('extAutoEditor').hidden=false;
        el('extAutoName').textContent=item.name+' · '+item.id;
        fieldBrowser(el('extAutoFields'),item,async sql=>{el('extAutoSql').value=sql;await autoQuery('query');});
        el('extAutoHeader').value=item.header_row||1;
        el('extAutoSql').value=`SELECT * FROM "${item.id}" LIMIT 20;`;
        el('extAutoStatus').textContent=item.status+'：'+item.reason;
        el('extAutoResult').replaceChildren();
        el('extAutoEditor').scrollIntoView({behavior:'smooth',block:'nearest'});
      };
    });
    if(externalTarget){
      const target=externalTarget;externalTarget=null;
      const index=data.tables.findIndex(t=>t.id===target);
      if(index>=0)rows[index].querySelector('button').click();
      else if((data.collections||[]).some(c=>c.id===target)){el('extQueryScope').value=target;selectQueryScope();}
      else el('extAutoStatus').textContent='目标表未找到，请刷新目录或连接所属云盘账户。';
    }
    if(!scanning)el('extScanStatus').textContent=`已保存 ${data.tables.length} 张来源表，自动生成 ${(data.collections||[]).length} 张统一虚拟表。`;
    window.dispatchEvent(new Event('external-catalog-updated'));
    return data;
  }
  async function scanLoop(){
    if(!scanning||scanInFlight)return;
    scanInFlight=true;
    try{
      const result=await api('discovery/step',{});
      el('extScanStatus').textContent=`${result.done?'扫描完成':scanning?'扫描中':'已暂停'} · 已处理 ${result.processed} 项，登记 ${result.registered} 张表，失败 ${result.errors.length} 项。`;
      const host=el('extScanErrors');host.replaceChildren();
      if(result.errors.length||result.skipped.length){
        const details=text(host,'details','');text(details,'summary','失败与未扫描项目');
        result.errors.forEach(e=>text(details,'p',`${e.item} ${e.member||''}：${e.error}`));
        result.skipped.forEach(e=>text(details,'p',e));
      }
      await loadDiscovered();
      if(result.done){scanning=false;status('扫描完成，目录已保存。选择表名后可到查询区查询。');}
    }catch(error){scanning=false;if(error.status===401)cloudConnected=false;el('extScanStatus').textContent=error.message+'；会话有效时可继续，过期后需重新开始。';}
    finally{scanInFlight=false;if(scanning)setTimeout(scanLoop,300);}
  }
  async function startScan(){
    if(scanning||scanInFlight){status('扫描正在进行，请等待或暂停。');return;}
    if(!cloudConnected){openCloudLogin(startScan);return;}
    await api('discovery/start',{root:el('extScanRoot').value.trim(),recursive:el('extScanRecursive').checked,archives:el('extScanArchives').checked});
    el('extCollectionEditor').replaceChildren();el('extCollectionEditor').hidden=true;
    el('extAutoResult').replaceChildren();el('extAutoEditor').hidden=true;
    el('extScanStatus').textContent='扫描已启动，正在读取目录…';
    scanning=true;setTimeout(scanLoop,0);
  }
  el('extScanShortcut').onclick=()=>{
    el('extDiscoveryPanel').open=true;
    el('extDiscoveryPanel').scrollIntoView({behavior:'smooth',block:'start'});
    el('extScanRoot').focus({preventScroll:true});
  };
  el('extScanStart').onclick=()=>action(async()=>{
    el('extDiscoveryPanel').open=true;
    el('extScanStatus').textContent=cloudConnected?'正在创建扫描任务…':'请在弹窗中连接云盘，连接成功后自动开始扫描。';
    try{await startScan();}
    catch(error){el('extScanStatus').textContent='无法开始扫描：'+error.message;throw error;}
  });
  el('extScanPause').onclick=()=>{scanning=false;el('extScanStatus').textContent='已请求暂停，当前文件处理完后停止；可继续扫描。';};
  el('extScanResume').onclick=()=>{if(!cloudConnected){openCloudLogin(()=>{scanning=true;scanLoop();});return;}scanning=true;scanLoop();};
  el('extScanRefresh').onclick=()=>action(loadDiscovered);
  async function autoQuery(mode){
    if(!autoTable)throw new Error('请先选择虚拟表。');
    if(!cloudConnected){openCloudLogin(()=>autoQuery(mode));return;}
    const header=Number(el('extAutoHeader').value);
    if(mode==='preview')previewHeader=null;
    el('extAutoResult').replaceChildren();
    const data=await api('discovery/query',{view_id:autoTable.id,sql:el('extAutoSql').value,preview:mode==='preview',confirm:mode==='confirm',header_row:mode==='query'?null:header});
    if(mode==='confirm'){autoTable.status='可查询';previewHeader=null;await loadDiscovered();el('extAutoStatus').textContent='映射已永久保存，可按字段查询。';return;}
    previewHeader=mode==='preview'?header:null;
    el('extAutoResult').replaceChildren();table(el('extAutoResult'),data.columns,data.rows);
    el('extAutoStatus').textContent=`${data.mode} · 显示 ${data.rows.length} 行 · ${data.source} / ${data.sheet}`;
  }
  el('extAutoPreview').onclick=()=>action(()=>autoQuery('preview'));
  el('extAutoConfirm').onclick=()=>action(()=>autoQuery('confirm'));
  el('extAutoQuery').onclick=()=>action(()=>autoQuery('query'));
  document.querySelector('[data-tab="external"]')?.addEventListener('click',()=>{if(!ready)action(initialize);});
  if(document.getElementById('tab-external')){action(initialize);loadGrants();}
})();

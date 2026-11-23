const main=document.querySelector('#main');
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function dorisStatus(sources, configError) {
  if (!sources?.length && !configError) return '';
  return `<section class="openalex-panel"><h2>Doris 数据源</h2>
    ${configError ? `<p class="openalex-error">${esc(configError)}</p>` : ''}
    ${(sources || []).map(source => `<p><strong>${esc(source.name)}</strong> ·
      ${source.status === 'connected' ? '最近扫描成功' : esc(source.last_error || '等待连接及首次扫描')}
    </p>`).join('')}</section>`;
}

async function runLive(button) {
  const output = button.closest('article').querySelector('[data-live-result]');
  button.disabled = true;
  output.textContent = '正在向源库查询…';
  try {
    const response = await fetch('/api/portal/services/' + encodeURIComponent(button.dataset.liveService) + '/run-live', {
      method: 'POST', credentials: 'same-origin'
    });
    const data = await response.json();
    if (!response.ok) throw Error(data.error || '源库调用失败');
    output.textContent = data.suppressed ? '记录数少于 5 条，结果已隐藏。' :
      '当前记录数 ' + Number(data.count).toLocaleString('zh-CN');
  } catch (error) {
    output.textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

main.addEventListener('click', event => {
  const button = event.target.closest('[data-live-service]');
  if (button) runLive(button);
});

async function load(){const r=await fetch('/api/portal/services',{credentials:'same-origin'});const d=await r.json();if(!r.ok)throw Error(d.error||'无法读取服务目录');const groups={};for(const x of d.services)(groups[x.category]??=[]).push(x);const o=d.openalex_sync;const state=o?.state==='ready'?'已连接并完成同步':o?.state==='syncing'?'正在同步':o?.state==='failed'?'同步异常':'等待首次同步';const last=o?.last_success_at?new Date(o.last_success_at).toLocaleString('zh-CN'):'尚无成功记录';const openalex=o?`<section class="openalex-panel"><div class="openalex-heading"><div><p class="eyebrow">PUBLIC SCHOLARLY DATA</p><h2>OpenAlex · 医学与健康科学</h2></div><span class="status ${o.state==='ready'?'ok':o.state==='failed'?'error':''}">${esc(state)}</span></div><p>${esc(o.scope||'医学与健康科学公开学术元数据')}。API key ${o.key_configured?'已保存在服务端配置中':'未配置'}；平台每 ${Math.round((o.sync_interval_seconds||86400)/3600)} 小时检查一次，逻辑表最多同步 ${esc(o.max_records||1000)} 条近期记录。</p><div class="openalex-meta"><span>最近成功：${esc(last)}</span><span>记录数：${esc(o.record_count??'—')}</span><span>逻辑表：${esc((o.logical_tables||[]).length)}</span></div>${o.last_error?`<p class="openalex-error">${esc(o.last_error)}</p>`:''}<details><summary>查看逻辑表字段入口</summary><p>${esc((o.logical_tables||[]).join('、'))}</p><p>已纳入字段库与目录，可在自然语言查询、专业查询和已发布服务中使用。</p></details></section>`:'';main.innerHTML=`<section class="service-intro"><p class="eyebrow">DATA FABRIC SERVICES</p><h1>数据服务</h1><p>当前账户等级：${esc(d.permission?.label||'按账户权限提供服务')}。服务会使用你有权限的数据范围。</p></section>${openalex}${dorisStatus(d.doris_sources,d.doris_config_error)}${Object.entries(groups).map(([g,items])=>`<section><h2>${esc(g)}</h2><div class="service-grid">${items.map(x=>`<article class="service-card"><span class="status">${esc(x.status)}</span><h2>${esc(x.name)}</h2><p>${esc(x.description)}</p><div class="service-meta"><span>输入：${esc(x.input)}</span><span>${esc(x.id)}</span></div>${x.live_call ? `<p><button type="button" data-live-service="${esc(x.id)}">实时查询</button></p><p data-live-result aria-live="polite"></p>` : ''}</article>`).join('')}</div></section>`).join('')}<section class="compose"><h2>服务组合</h2><p>自然语言查询在你确认意图后，会按当前账户权限自动执行并展示对应等级的结果。</p></section>`}load().catch(e=>main.innerHTML=`<div class="notice error">${esc(e.message)}</div>`);

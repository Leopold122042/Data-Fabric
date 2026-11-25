const main=document.querySelector('#main');
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function runLive(button) {
  const output = button.closest('article').querySelector('[data-live-result]');
  button.disabled = true;
  output.textContent = '正在查询…';
  try {
    const response = await fetch('/api/portal/services/' + encodeURIComponent(button.dataset.liveService) + '/run-live', {
      method: 'POST', credentials: 'same-origin'
    });
    if (!response.ok) {
      const messages = {
        401: '登录已失效，请重新登录。',
        403: '当前账户权限无法调用此服务。',
        404: '这项服务已下架或暂不可用。',
        409: '服务已更新，请刷新页面后重试。'
      };
      output.textContent = messages[response.status] || '实时服务暂不可用，请稍后重试。';
      return;
    }
    const data = await response.json();
    output.textContent = '当前记录数 ' + Number(data.count).toLocaleString('zh-CN');
  } catch {
    output.textContent = '实时服务暂不可用，请稍后重试。';
  } finally {
    button.disabled = false;
  }
}

main.addEventListener('click', event => {
  const button = event.target.closest('[data-live-service]');
  if (button) runLive(button);
});

async function load() {
  const response = await fetch('/api/portal/services', {credentials: 'same-origin'});
  if (!response.ok) throw Error('无法读取服务目录，请刷新后重试。');
  const data = await response.json();
  const groups = {};
  for (const service of data.services) (groups[service.category] ??= []).push(service);
  main.innerHTML = `
    <section class="service-intro">
      <p class="eyebrow">DATA FABRIC SERVICES</p>
      <h1>数据服务</h1>
      <p>当前账户等级：${esc(data.permission?.label || '按账户权限提供服务')}。服务会使用你有权限的数据范围。</p>
    </section>
    ${Object.entries(groups).map(([category, items]) => `
      <section>
        <h2>${esc(category)}</h2>
        <div class="service-grid">${items.map(service => `
          <article class="service-card">
            <span class="status">${esc(service.status)}</span>
            <h2>${esc(service.name)}</h2>
            <p>${esc(service.description)}</p>
            <div class="service-meta">
              <span>输入：${esc(service.input)}</span>
              ${service.live_call || service.query_text ? '' : `<span>${esc(service.id)}</span>`}
            </div>
            ${service.query_text ? `<p><a class="button small" href="/user?page=ask&question=${encodeURIComponent(service.query_text)}">打开统计</a></p>` : ''}
            ${service.live_call ? `
              <p><button type="button" data-live-service="${esc(service.id)}">实时查询</button></p>
              <p data-live-result aria-live="polite"></p>` : ''}
          </article>`).join('')}
        </div>
      </section>`).join('')}
    <section class="compose">
      <h2>服务组合</h2>
      <p>自然语言查询在你确认意图后，会按当前账户权限自动执行并展示对应等级的结果。</p>
    </section>`;
}

load().catch(error => {
  main.innerHTML = `<div class="notice error" role="alert">${esc(error.message)}</div>`;
});

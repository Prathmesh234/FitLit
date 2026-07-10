/* Prioritized cross-domain operating signals for the overview tab. */
'use strict';
FitComp.register('cmp-insight-feed', '/api/comp/insight_feed', function (mount, d) {
  if (!d || !d.items || !d.items.length) {
    mount.innerHTML = '<p class="cmp-empty">no insights available</p>';
    return;
  }
  const rows = d.items.map((item) => `
    <div class="signal ${item.level || 'info'}">
      <i class="signal-mark"></i>
      <div class="signal-metric">${item.metric || '—'}</div>
      <div class="signal-copy"><b>${item.title}</b><span>${item.detail || ''}</span></div>
    </div>`).join('');
  mount.innerHTML = `
    <div class="cmp-head"><h3>Decision feed</h3><span class="cmp-tag">ranked signals · ${d.generated_at}</span></div>
    <div class="signal-list">${rows}</div>`;
}, 30000);

/* Collection operations — scheduler, freshness, quota, and service trace. */
'use strict';
FitComp.register('cmp-ops-trace', '/api/comp/operations_trace', function (mount, d) {
  if (!d || !d.fetchers) {
    mount.innerHTML = '<p class="cmp-empty">operations data unavailable</p>';
    return;
  }
  const rate = d.rate_limit || {};
  const live = d.fetchers.filter((f) => f.state === 'live').length;
  const storage = d.fetchers.reduce((sum, f) => sum + (f.storage_mb || 0), 0);
  const tone = d.overall === 'healthy' ? 'ok' : 'warn';
  const table = d.fetchers.map((f) => `
    <tr>
      <td>${f.name.replaceAll('_', ' ')}</td>
      <td><span class="state ${f.state}">${f.state}</span></td>
      <td class="num">${f.data_types}</td>
      <td class="num">${f.cadence_seconds}s</td>
      <td class="num">${f.last_dispatch || '—'}</td>
      <td class="num">${f.due_in_seconds}s</td>
      <td class="num">${f.fetch_age}</td>
      <td class="num">${f.storage_mb.toFixed(1)} MB</td>
    </tr>`).join('');
  const trace = (d.trace || []).map((row) => `
    <div class="trace-row ${row.level}">
      <span class="trace-time">${row.time}</span>
      <span class="trace-source">${row.source}</span>
      <span class="trace-message">${row.message}</span>
    </div>`).join('');

  mount.innerHTML = `
    <div class="cmp-head"><h3>Collection operations</h3><span class="cmp-tag">${d.generated_at} PT</span></div>
    <div class="ops-strip">
      <div class="ops-stat"><span>system</span><b class="${tone}">${d.overall}</b></div>
      <div class="ops-stat"><span>live fetchers</span><b>${live}/${d.fetchers.length}</b></div>
      <div class="ops-stat"><span>API budget</span><b class="${rate.remaining < 20 ? 'warn' : 'ok'}">${rate.remaining ?? '—'}</b></div>
      <div class="ops-stat"><span>hot storage</span><b>${storage.toFixed(0)} MB</b></div>
    </div>
    <div class="ops-table-wrap">
      <table class="ops-table">
        <thead><tr><th>Fetcher</th><th>State</th><th>Types</th><th>Cadence</th><th>Dispatch</th><th>Due</th><th>Fetch age</th><th>Storage</th></tr></thead>
        <tbody>${table}</tbody>
      </table>
    </div>
    <div class="cmp-head" style="margin-top:16px"><h3>Live trace</h3><span class="cmp-tag">latest events</span></div>
    <div class="trace-list">${trace}</div>`;
}, 30000);

/* Sleep diagnostics — debt, consistency, stage quality, and nightly trace. */
'use strict';
FitComp.register('cmp-sleep-insights', '/api/comp/sleep_insights', function (mount, d) {
  if (!d || !d.nights) {
    mount.innerHTML = '<p class="cmp-empty">not enough sleep history</p>';
    return;
  }
  const delta = d.duration_delta_hours == null ? '—' :
    `${d.duration_delta_hours >= 0 ? '+' : ''}${d.duration_delta_hours.toFixed(2)}h`;
  const stages = Object.entries(d.latest_stages || {}).map(([name, value]) => `
    <div class="stage-diag ${name}">
      <b>${value.minutes}m</b><span>${name} · ${value.pct}%</span>
    </div>`).join('');
  const trace = (d.trace || []).map((row) => `
    <div class="trace-row ${row.level}">
      <span class="trace-time">${row.time.slice(5)}</span>
      <span class="trace-source">sleep</span>
      <span class="trace-message"><b>${row.title}</b><br>${row.detail}</span>
    </div>`).join('');

  mount.innerHTML = `
    <div class="cmp-head"><h3>Sleep diagnostics</h3><span class="cmp-tag">${d.nights}-night operating window</span></div>
    <div class="diagnostic-kpis">
      <div class="diagnostic-kpi"><span>7-night average</span><b>${d.avg_hours?.toFixed(2) ?? '—'}h</b><small>${d.avg_efficiency ?? '—'}% efficiency</small></div>
      <div class="diagnostic-kpi"><span>sleep debt</span><b>${d.sleep_debt_hours}h</b><small>against 7.5h/night</small></div>
      <div class="diagnostic-kpi"><span>bedtime variation</span><b>${d.bedtime_consistency_min ?? '—'}m</b><small>standard deviation</small></div>
      <div class="diagnostic-kpi"><span>3-night trend</span><b>${delta}</b><small>vs prior three nights</small></div>
    </div>
    <div class="insight-note">${d.recommendation}</div>
    <div class="trace-layout">
      <div>
        <div class="cmp-head"><h3>Latest stage mix</h3><span class="cmp-tag">minutes + share</span></div>
        <div class="stage-diagnostics">${stages}</div>
      </div>
      <div>
        <div class="cmp-head"><h3>Night trace</h3><span class="cmp-tag">newest first</span></div>
        <div class="trace-list">${trace}</div>
      </div>
    </div>`;
}, 60000);

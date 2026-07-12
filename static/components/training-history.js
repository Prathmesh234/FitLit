/* Training history — 30-day load chart and recent workout ledger. */
'use strict';
FitComp.register('cmp-training-history', '/api/comp/training_history', function (mount, d, api) {
  if (!d || !d.series || !d.series.length) {
    mount.innerHTML = '<p class="cmp-empty">training history unavailable</p>';
    return;
  }
  const rows = d.series, W = 700, H = 205, l = 32, r = 10, t = 10, b = 23;
  const iw = W-l-r, ih = H-t-b;
  const x = (i) => l + (rows.length === 1 ? iw/2 : i*iw/(rows.length-1));
  const maxDuration = Math.max.apply(null, rows.map((row) => row.duration_min).concat([60]));
  const y = (v) => t + ih - v/maxDuration*ih;
  const barW = Math.max(3, iw/rows.length*.58);
  const bars = rows.map((row, i) => row.duration_min <= 0 ? '' :
    `<rect x="${(x(i)-barW/2).toFixed(1)}" y="${y(row.duration_min).toFixed(1)}"
      width="${barW.toFixed(1)}" height="${(t+ih-y(row.duration_min)).toFixed(1)}" rx="2"
      fill="${api.palette.clay}" opacity=".62"><title>${row.day}: ${row.duration_min} min · ${row.active_zone_minutes} zone min</title></rect>`).join('');
  const zonePoints = rows.filter((row) => row.active_zone_minutes > 0)
    .map((row) => [x(rows.indexOf(row)), y(Math.min(row.active_zone_minutes, maxDuration))]);
  const zonePath = zonePoints.length > 1 ? api.linePath(zonePoints) : '';
  const labels = rows.map((row, i) => i % 6 ? '' :
    `<text x="${x(i).toFixed(1)}" y="${H-6}" text-anchor="middle" class="history-axis">${row.day.slice(5)}</text>`).join('');
  const pace = (seconds) => {
    if (!seconds) return '—';
    const minutes = Math.floor(seconds/60);
    return `${minutes}:${String(seconds%60).padStart(2, '0')}/km`;
  };
  const sessions = (d.sessions || []).slice(0, 8).map((row) => `
    <tr>
      <td>${row.day.slice(5)} ${row.start}</td><td>${row.name || row.type}</td>
      <td class="num">${row.duration_min}m</td><td class="num">${row.distance_km || '—'} km</td>
      <td class="num">${pace(row.pace_seconds_km)}</td><td class="num">${row.avg_hr || '—'} bpm</td>
      <td class="num">${row.active_zone_minutes}m</td>
    </tr>`).join('');

  mount.innerHTML = `
    <div class="cmp-head"><h3>30-day training dashboard</h3><span class="cmp-tag">load + session ledger</span></div>
    <div class="history-kpis">
      <div class="history-kpi"><span>sessions</span><b>${d.summary.sessions}</b><small>${(d.summary.sessions/d.days*7).toFixed(1)} per week</small></div>
      <div class="history-kpi"><span>training time</span><b>${d.summary.duration_min} min</b><small>${Math.round(d.summary.duration_min/Math.max(d.summary.sessions,1))} min / session</small></div>
      <div class="history-kpi"><span>active zone load</span><b>${d.summary.active_zone_minutes} min</b><small>Fitbit active-zone minutes</small></div>
      <div class="history-kpi"><span>distance</span><b>${d.summary.distance_km} km</b><small>${d.summary.calories.toLocaleString()} exercise kcal</small></div>
    </div>
    <div class="history-panel">
      <div class="history-panel-head"><b>Daily training load</b><span>minutes by day</span></div>
      <svg viewBox="0 0 ${W} ${H}" class="history-svg">
        ${bars}
        ${zonePath ? `<path d="${zonePath}" fill="none" stroke="${api.palette.honey}" stroke-width="2.2" stroke-linejoin="round"/>` : ''}
        ${labels}
      </svg>
      <div class="chart-legend"><span><i style="background:${api.palette.clay};opacity:.65"></i>workout duration</span><span><i style="background:${api.palette.honey}"></i>active-zone minutes</span></div>
    </div>
    <div class="history-table-wrap">
      <table class="history-table"><thead><tr><th>start PT</th><th>session</th><th>duration</th><th>distance</th><th>pace</th><th>avg HR</th><th>zone load</th></tr></thead>
      <tbody>${sessions || '<tr><td colspan="7">No recent sessions</td></tr>'}</tbody></table>
    </div>`;
}, 60000);

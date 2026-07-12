/* Overview history — 14-day activity, sleep, and recovery trajectory. */
'use strict';
FitComp.register('cmp-overview-history', '/api/comp/overview_history', function (mount, d, api) {
  if (!d || !d.series || !d.series.length) {
    mount.innerHTML = '<p class="cmp-empty">progress history unavailable</p>';
    return;
  }
  const rows = d.series;
  const W = 700, H = 205, l = 34, r = 12, t = 10, b = 24;
  const iw = W - l - r, ih = H - t - b;
  const x = (i) => l + (rows.length === 1 ? iw / 2 : i * iw / (rows.length - 1));
  const stepMax = Math.max(d.targets.steps, ...rows.map((row) => row.steps || 0), 1);
  const stepY = (value) => t + ih - value / stepMax * ih;
  const sleepY = (value) => t + ih - Math.max(0, Math.min(1, (value - 4) / 6)) * ih;
  const barW = Math.max(5, iw / rows.length * .55);
  const bars = rows.map((row, i) => row.steps == null ? '' :
    `<rect x="${(x(i) - barW / 2).toFixed(1)}" y="${stepY(row.steps).toFixed(1)}"
      width="${barW.toFixed(1)}" height="${(t + ih - stepY(row.steps)).toFixed(1)}" rx="3"
      fill="${api.palette.sage}" opacity=".52"><title>${row.day}: ${row.steps.toLocaleString()} steps</title></rect>`).join('');
  const sleepPoints = rows.map((row, i) => row.sleep_hours == null ? null : [x(i), sleepY(row.sleep_hours), row])
    .filter((point) => point);
  const sleepPath = sleepPoints.length > 1 ? api.linePath(sleepPoints) : '';
  const sleepDots = sleepPoints.map((point) =>
    `<circle cx="${point[0].toFixed(1)}" cy="${point[1].toFixed(1)}" r="3" fill="${api.palette.rose}">
      <title>${point[2].day}: ${point[2].sleep_hours}h sleep</title></circle>`).join('');
  const labels = rows.map((row, i) => i % 3 ? '' :
    `<text x="${x(i).toFixed(1)}" y="${H - 7}" text-anchor="middle" class="history-axis">${row.day.slice(5)}</text>`).join('');
  const targetY = stepY(d.targets.steps);
  const summary = d.summary || {};

  mount.innerHTML = `
    <div class="cmp-head"><h3>14-day progress dashboard</h3><span class="cmp-tag">activity + recovery</span></div>
    <div class="history-kpis">
      <div class="history-kpi"><span>average steps</span><b>${(summary.avg_steps || 0).toLocaleString()}</b><small>${summary.days_with_activity || 0}/${d.days} days captured</small></div>
      <div class="history-kpi"><span>average sleep</span><b>${summary.avg_sleep_hours || '—'}h</b><small>${summary.nights_with_sleep || 0} nights captured</small></div>
      <div class="history-kpi"><span>average HRV</span><b>${summary.avg_hrv_ms || '—'} ms</b><small>higher supports recovery</small></div>
      <div class="history-kpi"><span>resting heart rate</span><b>${summary.avg_resting_hr || '—'} bpm</b><small>14-day baseline</small></div>
    </div>
    <div class="history-panel">
      <div class="history-panel-head"><b>Daily movement and sleep</b><span>goal ${d.targets.steps.toLocaleString()} steps · ${d.targets.sleep_hours}h sleep</span></div>
      <svg viewBox="0 0 ${W} ${H}" class="history-svg">
        <line x1="${l}" y1="${targetY.toFixed(1)}" x2="${W-r}" y2="${targetY.toFixed(1)}" stroke="${api.palette.sage}" stroke-dasharray="4 4" opacity=".35"/>
        ${bars}
        ${sleepPath ? `<path d="${sleepPath}" fill="none" stroke="${api.palette.rose}" stroke-width="2.5" stroke-linejoin="round"/>` : ''}
        ${sleepDots}${labels}
      </svg>
      <div class="chart-legend">
        <span><i style="background:${api.palette.sage};opacity:.55"></i>steps</span>
        <span><i style="background:${api.palette.rose}"></i>sleep duration</span>
        <span><i style="background:transparent;border-top:1px dashed ${api.palette.sage}"></i>step goal</span>
      </div>
    </div>`;
}, 60000);

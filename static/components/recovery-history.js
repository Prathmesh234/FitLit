/* Recovery history — 30-day sleep quality, HRV, and resting-HR dashboard. */
'use strict';
FitComp.register('cmp-recovery-history', '/api/comp/recovery_history', function (mount, d, api) {
  if (!d || !d.series || !d.series.length) {
    mount.innerHTML = '<p class="cmp-empty">recovery history unavailable</p>';
    return;
  }
  const rows = d.series;
  const W = 540, H = 205, l = 30, r = 10, t = 10, b = 22;
  const iw = W - l - r, ih = H - t - b;
  const x = (i) => l + (rows.length === 1 ? iw / 2 : i * iw / (rows.length - 1));
  const labels = rows.map((row, i) => i % 6 ? '' :
    `<text x="${x(i).toFixed(1)}" y="${H - 6}" text-anchor="middle" class="history-axis">${row.day.slice(5)}</text>`).join('');
  const sleepY = (v) => t + ih - Math.max(0, Math.min(1, v / 10)) * ih;
  const barW = Math.max(3, iw / rows.length * .62);
  const sleepBars = rows.map((row, i) => row.sleep_hours == null ? '' :
    `<rect x="${(x(i)-barW/2).toFixed(1)}" y="${sleepY(row.sleep_hours).toFixed(1)}"
      width="${barW.toFixed(1)}" height="${(t+ih-sleepY(row.sleep_hours)).toFixed(1)}" rx="2"
      fill="${api.palette.teal}" opacity=".55"><title>${row.day}: ${row.sleep_hours}h · ${row.efficiency_pct}% efficient</title></rect>`).join('');
  const targetY = sleepY(d.targets.sleep_hours);
  const hitWidth = rows.length > 1 ? iw / (rows.length - 1) : iw;
  const hits = rows.map((row, i) =>
    `<g class="history-hit" data-index="${i}">
      <line x1="${x(i).toFixed(1)}" y1="${t}" x2="${x(i).toFixed(1)}" y2="${t+ih}" class="history-guide"/>
      <rect x="${(x(i)-hitWidth/2).toFixed(1)}" y="${t}" width="${hitWidth.toFixed(1)}" height="${ih}" fill="transparent"/>
    </g>`).join('');
  const sleepGrid = [0, 5, 10].map((value) => {
    const gy = sleepY(value);
    return `<line x1="${l}" y1="${gy.toFixed(1)}" x2="${W-r}" y2="${gy.toFixed(1)}" class="history-gridline"/>
      <text x="${l-5}" y="${(gy+3).toFixed(1)}" text-anchor="end" class="history-axis">${value}h</text>`;
  }).join('');

  function normalizedPath(key, invert) {
    const values = rows.map((row) => row[key]).filter((value) => value != null);
    if (values.length < 2) return '';
    const lo = Math.min.apply(null, values), hi = Math.max.apply(null, values);
    const span = Math.max(hi - lo, 1);
    const points = rows.map((row, i) => {
      if (row[key] == null) return null;
      let ratio = (row[key] - lo) / span;
      if (invert) ratio = 1 - ratio;
      return [x(i), t + ih - ratio * ih];
    }).filter((point) => point);
    return api.linePath(points);
  }
  const hrvPath = normalizedPath('hrv_ms', false);
  const rhrPath = normalizedPath('resting_hr', true);
  const trend = d.summary.sleep_trend_hours;
  const trendText = trend == null ? 'not enough paired nights' :
    `${trend > 0 ? '+' : ''}${trend}h recent 3-night shift`;

  mount.innerHTML = `
    <div class="cmp-head"><h3>30-day recovery dashboard</h3><span class="cmp-tag">${d.summary.sleep_coverage}/${d.days} nights synced</span></div>
    <div class="history-kpis">
      <div class="history-kpi"><span>average sleep</span><b>${d.summary.avg_sleep_hours || '—'}h</b><small>target ${d.targets.sleep_hours}h</small></div>
      <div class="history-kpi"><span>sleep direction</span><b>${trend == null ? '—' : (trend > 0 ? '↑' : trend < 0 ? '↓' : '→')}</b><small>${trendText}</small></div>
      <div class="history-kpi"><span>average HRV</span><b>${d.summary.avg_hrv_ms || '—'} ms</b><small>autonomic recovery</small></div>
      <div class="history-kpi"><span>average resting HR</span><b>${d.summary.avg_resting_hr || '—'} bpm</b><small>lower is generally favorable</small></div>
    </div>
    <div class="history-grid">
      <div class="history-panel history-sleep-panel">
        <div class="history-panel-head"><b>Sleep volume</b><span>hover or focus a night</span></div>
        <svg viewBox="0 0 ${W} ${H}" class="history-svg">
          ${sleepGrid}
          <line x1="${l}" y1="${targetY.toFixed(1)}" x2="${W-r}" y2="${targetY.toFixed(1)}" stroke="${api.palette.honey}" stroke-dasharray="4 4" opacity=".65"/>
          ${sleepBars}${labels}${hits}
        </svg>
        <div class="chart-legend"><span><i style="background:${api.palette.teal};opacity:.6"></i>sleep hours</span><span><i style="background:transparent;border-top:1px dashed ${api.palette.honey}"></i>7.5h target</span></div>
      </div>
      <div class="history-panel history-signal-panel">
        <div class="history-panel-head"><b>Recovery signals</b><span>relative direction · hover for values</span></div>
        <svg viewBox="0 0 ${W} ${H}" class="history-svg">
          <line x1="${l}" y1="${t+ih/2}" x2="${W-r}" y2="${t+ih/2}" class="history-gridline"/>
          ${hrvPath ? `<path d="${hrvPath}" fill="none" stroke="${api.palette.teal}" stroke-width="2.4" stroke-linejoin="round"/>` : ''}
          ${rhrPath ? `<path d="${rhrPath}" fill="none" stroke="${api.palette.rust}" stroke-width="2.4" stroke-linejoin="round"/>` : ''}
          ${labels}${hits}
        </svg>
        <div class="chart-legend"><span><i style="background:${api.palette.teal}"></i>HRV higher</span><span><i style="background:${api.palette.rust}"></i>resting HR lower</span></div>
      </div>
    </div>`;
  function recoveryTip(target) {
    const row = rows[Number(target.dataset.index)];
    return {
      title: api.formatDay(row.day),
      rows: [
        { label: 'Sleep', value: row.sleep_hours == null ? 'No data' : row.sleep_hours + ' h', color: api.palette.teal },
        { label: 'Efficiency', value: row.efficiency_pct == null ? 'No data' : row.efficiency_pct + '%' },
        { label: 'Awake', value: row.awake_min == null ? 'No data' : row.awake_min + ' min' },
        { label: 'HRV', value: row.hrv_ms == null ? 'No data' : row.hrv_ms + ' ms', color: api.palette.teal },
        { label: 'Resting HR', value: row.resting_hr == null ? 'No data' : row.resting_hr + ' bpm', color: api.palette.rust },
      ],
    };
  }
  api.bindTooltip(mount.querySelector('.history-sleep-panel'), '.history-hit', recoveryTip);
  api.bindTooltip(mount.querySelector('.history-signal-panel'), '.history-hit', recoveryTip);
}, 60000);

/* Body history — 90-day weight trajectory and energy/protein coverage. */
'use strict';
FitComp.register('cmp-body-history', '/api/comp/body_history', function (mount, d, api) {
  if (!d || !d.weights) {
    mount.innerHTML = '<p class="cmp-empty">body history unavailable</p>';
    return;
  }
  const panelWidth = window.innerWidth > 560 ? (mount.clientWidth - 110) / 2 : mount.clientWidth - 70;
  const W = Math.max(180, Math.round(panelWidth));
  const H = window.innerWidth <= 560 ? 180 : 205;
  const l = 38, r = 12, t = 12, b = 24;
  const iw = W-l-r, ih = H-t-b;
  function lineChart(rows, keys, colors, target) {
    if (!rows.length) return '<div class="history-empty">no measurements in this window</div>';
    const values = [];
    rows.forEach((row) => keys.forEach((key) => { if (row[key] != null) values.push(row[key]); }));
    if (target != null) values.push(target);
    const lo = Math.min.apply(null, values)-1, hi = Math.max.apply(null, values)+1;
    const span = Math.max(hi-lo, 1);
    const x = (i) => l + (rows.length === 1 ? iw/2 : i*iw/(rows.length-1));
    const y = (v) => t + ih - (v-lo)/span*ih;
    const paths = keys.map((key, index) => {
      const points = rows.map((row, i) => row[key] == null ? null : [x(i), y(row[key])]).filter((point) => point);
      if (points.length > 1) return `<path d="${api.linePath(points)}" fill="none" stroke="${colors[index]}" stroke-width="${index ? 2.6 : 1.2}" opacity="${index ? 1 : .35}" stroke-linejoin="round"/>`;
      return points.length ? `<circle cx="${points[0][0]}" cy="${points[0][1]}" r="3" fill="${colors[index]}"/>` : '';
    }).join('');
    const labels = rows.map((row, i) => i % Math.max(1, Math.floor(rows.length/5)) ? '' :
      `<text x="${x(i).toFixed(1)}" y="${H-6}" text-anchor="middle" class="history-axis">${row.day.slice(5)}</text>`).join('');
    const targetLine = target == null ? '' :
      `<line x1="${l}" y1="${y(target).toFixed(1)}" x2="${W-r}" y2="${y(target).toFixed(1)}" stroke="${api.palette.clay}" stroke-dasharray="4 4" opacity=".65"/>`;
    const hitWidth = rows.length > 1 ? iw / (rows.length - 1) : iw;
    const hits = rows.map((row, i) =>
      `<g class="history-hit" data-index="${i}">
        <line x1="${x(i).toFixed(1)}" y1="${t}" x2="${x(i).toFixed(1)}" y2="${t+ih}" class="history-guide"/>
        <rect x="${(x(i)-hitWidth/2).toFixed(1)}" y="${t}" width="${hitWidth.toFixed(1)}" height="${ih}" fill="transparent"/>
      </g>`).join('');
    const grid = [lo, (lo+hi)/2, hi].map((value) => {
      const gy = y(value);
      return `<line x1="${l}" y1="${gy.toFixed(1)}" x2="${W-r}" y2="${gy.toFixed(1)}" class="history-gridline"/>
        <text x="${l-5}" y="${(gy+3).toFixed(1)}" text-anchor="end" class="history-axis">${value.toFixed(0)}</text>`;
    }).join('');
    return `<svg viewBox="0 0 ${W} ${H}" class="history-svg">${grid}${targetLine}${paths}${labels}${hits}</svg>`;
  }
  const weights = d.weights;
  const fuel = d.fuel || [];
  const fuelRows = fuel.filter((row) => row.calories_in != null || row.calories_out != null);
  let fuelChart = '<div class="history-empty">log meals to unlock energy balance trends</div>';
  if (fuelRows.length) {
    const max = Math.max.apply(null, fuelRows.reduce((all, row) =>
      all.concat([row.calories_in || 0, row.calories_out || 0]), [1]));
    const x = (i) => l + (fuelRows.length === 1 ? iw/2 : i*iw/(fuelRows.length-1));
    const y = (v) => t + ih - v/max*ih;
    const inPoints = fuelRows.filter((row) => row.calories_in != null).map((row) => [x(fuelRows.indexOf(row)), y(row.calories_in)]);
    const outPoints = fuelRows.filter((row) => row.calories_out != null).map((row) => [x(fuelRows.indexOf(row)), y(row.calories_out)]);
    const labels = fuelRows.map((row, i) => i % Math.max(1, Math.floor(fuelRows.length/5)) ? '' :
      `<text x="${x(i).toFixed(1)}" y="${H-6}" text-anchor="middle" class="history-axis">${row.day.slice(5)}</text>`).join('');
    const hitWidth = fuelRows.length > 1 ? iw / (fuelRows.length - 1) : iw;
    const hits = fuelRows.map((row, i) =>
      `<g class="history-hit" data-index="${i}">
        <line x1="${x(i).toFixed(1)}" y1="${t}" x2="${x(i).toFixed(1)}" y2="${t+ih}" class="history-guide"/>
        <rect x="${(x(i)-hitWidth/2).toFixed(1)}" y="${t}" width="${hitWidth.toFixed(1)}" height="${ih}" fill="transparent"/>
      </g>`).join('');
    const grid = [0, Math.round(max/2), max].map((value) => {
      const gy = y(value);
      return `<line x1="${l}" y1="${gy.toFixed(1)}" x2="${W-r}" y2="${gy.toFixed(1)}" class="history-gridline"/>
        <text x="${l-5}" y="${(gy+3).toFixed(1)}" text-anchor="end" class="history-axis">${Math.round(value/100)/10}k</text>`;
    }).join('');
    fuelChart = `<svg viewBox="0 0 ${W} ${H}" class="history-svg">
      ${grid}
      ${inPoints.length > 1 ? `<path d="${api.linePath(inPoints)}" fill="none" stroke="${api.palette.clay}" stroke-width="2.3"/>` : ''}
      ${outPoints.length > 1 ? `<path d="${api.linePath(outPoints)}" fill="none" stroke="${api.palette.sage}" stroke-width="2.3"/>` : ''}
      ${inPoints.length === 1 ? `<circle cx="${inPoints[0][0]}" cy="${inPoints[0][1]}" r="3" fill="${api.palette.clay}"/>` : ''}
      ${outPoints.length === 1 ? `<circle cx="${outPoints[0][0]}" cy="${outPoints[0][1]}" r="3" fill="${api.palette.sage}"/>` : ''}
      ${labels}${hits}</svg>`;
  }
  const change = d.summary.weight_change_lb;
  const changeLabel = change == null ? '—' : `${change > 0 ? '+' : ''}${change}`;
  const changeUnit = change == null ? '' : '<em>lb</em>';

  mount.innerHTML = `
    <div class="cmp-head"><h3>90-day body and fuel dashboard</h3><span class="cmp-tag">trend-weighted progress</span></div>
    <div class="history-kpis">
      <div class="history-kpi"><span>Latest 7-day average</span><b>${d.summary.latest_avg7_lb || '—'} <em>lb</em></b><small>${weights.length} weigh-ins plotted</small></div>
      <div class="history-kpi"><span>Observed change</span><b>${changeLabel} ${changeUnit}</b><small>First to latest rolling average</small></div>
      <div class="history-kpi"><span>Target weight</span><b>${d.summary.target_lb || '—'} <em>lb</em></b><small>Sub-15% model target</small></div>
      <div class="history-kpi"><span>Nutrition coverage</span><b>${d.summary.days_with_calories}/${d.days} <em>days</em></b><small>Protein target ${d.summary.protein_target_g || '—'} g</small></div>
    </div>
    <div class="history-grid">
      <div class="history-panel history-weight-panel">
        <div class="history-panel-head"><b>Weight trajectory</b><span>hover or focus a weigh-in</span></div>
        ${lineChart(weights, ['weight_lb','avg7_lb'], [api.palette.t30, api.palette.teal], d.summary.target_lb)}
        <div class="chart-legend"><span><i style="background:${api.palette.t30}"></i>scale weight</span><span><i style="background:${api.palette.teal}"></i>7-reading average</span><span><i style="background:transparent;border-top:1px dashed ${api.palette.clay}"></i>target</span></div>
      </div>
      <div class="history-panel history-fuel-panel">
        <div class="history-panel-head"><b>Energy trajectory</b><span>hover or focus a logged day</span></div>
        ${fuelChart}
        <div class="chart-legend"><span><i style="background:${api.palette.clay}"></i>calories in</span><span><i style="background:${api.palette.sage}"></i>calories out</span></div>
      </div>
    </div>`;
  api.bindTooltip(mount.querySelector('.history-weight-panel'), '.history-hit', function (target) {
    const row = weights[Number(target.dataset.index)];
    return {
      title: api.formatDay(row.day),
      rows: [
        { label: 'Scale weight', value: row.weight_lb + ' lb' },
        { label: '7-reading average', value: row.avg7_lb + ' lb', color: api.palette.teal },
        { label: 'Conditions', value: row.fasted ? 'Fasted' : 'Not marked fasted' },
        { label: 'Target gap', value: d.summary.target_lb == null ? 'No target' : (row.avg7_lb-d.summary.target_lb).toFixed(1) + ' lb' },
      ],
    };
  });
  api.bindTooltip(mount.querySelector('.history-fuel-panel'), '.history-hit', function (target) {
    const row = fuelRows[Number(target.dataset.index)];
    return {
      title: api.formatDay(row.day),
      rows: [
        { label: 'Calories in', value: row.calories_in == null ? 'Not logged' : row.calories_in.toLocaleString() + ' kcal', color: api.palette.clay },
        { label: 'Calories out', value: row.calories_out == null ? 'No data' : row.calories_out.toLocaleString() + ' kcal', color: api.palette.sage },
        { label: 'Energy balance', value: row.balance_kcal == null ? 'Unavailable' : (row.balance_kcal > 0 ? '+' : '') + row.balance_kcal.toLocaleString() + ' kcal' },
        { label: 'Protein', value: row.protein_g == null ? 'Not logged' : row.protein_g + ' g' },
      ],
    };
  });
}, 60000);

/* Sleep history — per-night stacked stage bars (deep / light / rem / awake). */
'use strict';
FitComp.register('cmp-sleep-history', '/api/comp/sleep_history', function (mount, d, api) {
  if (!d || !d.nights || !d.nights.length) { mount.innerHTML = '<p class="cmp-empty">no sleep history</p>'; return; }
  const W = 640, H = 190, padL = 30, padR = 8, padT = 10, padB = 26;
  const iw = W - padL - padR, ih = H - padT - padB;
  const order = ['deep', 'light', 'rem', 'awake'];        // stack bottom→top
  const totals = d.nights.map((n) => n.deep + n.light + n.rem + n.awake);
  const mx = Math.max(...totals, 1);
  const bw = iw / d.nights.length;
  const y = (m) => (m / mx) * ih;

  let bars = '';
  d.nights.forEach((n, i) => {
    let yTop = padT + ih;
    order.forEach((k) => {
      const h = y(n[k] || 0);
      yTop -= h;
      if (h > 0) bars += `<rect x="${(padL + i * bw + 2).toFixed(1)}" y="${yTop.toFixed(1)}"
        width="${(bw - 4).toFixed(1)}" height="${h.toFixed(1)}" fill="${api.stageColors[k]}">
        <title>${n.night} · ${k} ${n[k]}m</title></rect>`;
    });
  });
  // x labels (every other)
  let xt = '';
  d.nights.forEach((n, i) => { if (i % 2 === 0) xt += `<text x="${(padL + i * bw + bw / 2).toFixed(1)}" y="${H - 12}" font-size="8.5" fill="${api.palette.t30}" text-anchor="middle" font-family="'SF Mono',monospace">${n.night.slice(5)}</text>`; });
  // hour gridlines
  let gl = '';
  for (let hrs = 2; hrs <= mx / 60; hrs += 2) gl += `<line x1="${padL}" y1="${(padT + ih - y(hrs * 60)).toFixed(1)}" x2="${W - padR}" y2="${(padT + ih - y(hrs * 60)).toFixed(1)}" stroke="${api.palette.t08}"/><text x="4" y="${(padT + ih - y(hrs * 60) + 3).toFixed(1)}" font-size="9" fill="${api.palette.t30}" font-family="'SF Mono',monospace">${hrs}h</text>`;

  const legend = [['deep', 'Deep'], ['light', 'Light'], ['rem', 'REM'], ['awake', 'Awake']]
    .map(([k, l]) => `<span><i style="background:${api.stageColors[k]}"></i>${l}</span>`).join('');

  mount.innerHTML = `
    <div class="cmp-head"><h3>Sleep history</h3><span class="cmp-tag">${d.nights.length} nights</span></div>
    <svg viewBox="0 0 ${W} ${H}" class="cmp-svg" style="width:100%;height:${H}px">${gl}${bars}${xt}</svg>
    <div class="legend-inline">${legend}</div>`;
}, 60000);

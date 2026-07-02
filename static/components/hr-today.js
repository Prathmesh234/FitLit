/* Intraday heart rate — today's bpm curve over colored zone bands. */
'use strict';
FitComp.register('cmp-hr-today', '/api/comp/heart_today', function (mount, d, api) {
  if (!d || !d.points || d.points.length < 2) { mount.innerHTML = '<p class="cmp-empty">no heart data today</p>'; return; }
  const W = 640, H = 190, padL = 34, padR = 8, padT = 10, padB = 22;
  const iw = W - padL - padR, ih = H - padT - padB;
  const bpms = d.points.map((p) => p.bpm);
  const lo = Math.min(50, Math.min(...bpms) - 5);
  const hi = Math.max((d.max_hr || 190), Math.max(...bpms) + 6);
  const y = (b) => padT + ih - ((b - lo) / (hi - lo)) * ih;
  const x = (i) => padL + (i / (d.points.length - 1)) * iw;

  // zone bands
  const bands = (d.zones || []).map((z) => {
    const yTop = y(Math.min(z.high, hi)), yBot = y(Math.max(z.low, lo));
    return `<rect x="${padL}" y="${yTop.toFixed(1)}" width="${iw}" height="${Math.max(0, yBot - yTop).toFixed(1)}"
      fill="${api.zoneColors[z.zone] || 'transparent'}" opacity="0.12"/>`;
  }).join('');

  const pts = d.points.map((p, i) => [x(i), y(p.bpm)]);
  const line = api.linePath(pts);
  const area = line + ` L${pts.at(-1)[0].toFixed(1)} ${padT + ih} L${pts[0][0].toFixed(1)} ${padT + ih} Z`;
  const peakI = bpms.indexOf(Math.max(...bpms));

  // y ticks
  let yt = '';
  [lo, Math.round((lo + hi) / 2), Math.round(hi)].forEach((v) =>
    yt += `<text x="4" y="${(y(v) + 3).toFixed(1)}" font-size="9" fill="${api.palette.t30}" font-family="'SF Mono',monospace">${Math.round(v)}</text>`);
  // hourly x ticks (points are HH:MM strings)
  let xt = '';
  d.points.forEach((p, i) => { if (p.t.endsWith(':00') && p.t.slice(0, 2) % 3 === 0) xt += `<text x="${x(i).toFixed(1)}" y="${H - 6}" font-size="9" fill="${api.palette.t30}" text-anchor="middle" font-family="'SF Mono',monospace">${p.t}</text>`; });

  mount.innerHTML = `
    <div class="cmp-head"><h3>Heart rate · today</h3><span class="cmp-tag">peak ${Math.max(...bpms)} · rest ${d.resting_hr ?? '—'}</span></div>
    <svg viewBox="0 0 ${W} ${H}" class="cmp-svg" style="width:100%;height:${H}px">
      ${bands}${yt}${xt}
      <path d="${area}" fill="${api.palette.rust}" opacity="0.08"/>
      <path d="${line}" fill="none" stroke="${api.palette.rust}" stroke-width="1.8" stroke-linejoin="round"/>
      <circle cx="${x(peakI).toFixed(1)}" cy="${y(bpms[peakI]).toFixed(1)}" r="3.5" fill="${api.palette.rust}"/>
      <circle cx="${x(pts.length - 1).toFixed(1)}" cy="${y(bpms.at(-1)).toFixed(1)}" r="3" fill="${api.palette.ink}"/>
    </svg>`;
}, 15000);

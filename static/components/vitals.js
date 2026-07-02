/* Vitals grid — SpO2, respiratory rate, HRV, resting HR (value + mini trend). */
'use strict';
FitComp.register('cmp-vitals', '/api/comp/vitals', function (mount, d, api) {
  if (!d) { mount.innerHTML = '<p class="cmp-empty">no vitals</p>'; return; }
  const items = [
    ['SpO₂', d.spo2, api.palette.teal],
    ['Respiratory', d.respiratory, api.palette.sage],
    ['HRV', d.hrv, api.palette.clay],
    ['Resting HR', d.resting_hr, api.palette.rose],
    ['VO₂ max', d.vo2max, api.palette.honey],
  ];
  function mini(series, color) {
    if (!series || series.length < 2) return '';
    const w = 100, h = 30, pad = 3;
    const mn = Math.min(...series), mx = Math.max(...series), rng = (mx - mn) || 1;
    const dx = (w - pad * 2) / (series.length - 1);
    const pts = series.map((v, i) => [pad + i * dx, h - pad - ((v - mn) / rng) * (h - pad * 2)]);
    return `<svg viewBox="0 0 ${w} ${h}" class="vt-spark"><path d="${api.linePath(pts)}"
      fill="none" stroke="${color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
      <circle cx="${pts.at(-1)[0].toFixed(1)}" cy="${pts.at(-1)[1].toFixed(1)}" r="2.6" fill="${color}"/></svg>`;
  }
  const tiles = items.map(([label, v, color]) => {
    const val = v && v.latest != null ? v.latest : '—';
    return `<div class="vt">
      <div class="vt-l">${label}</div>
      <div class="vt-v" style="color:${val === '—' ? api.palette.t30 : api.palette.ink}">${val}<span class="vt-u">${v && v.latest != null ? (v.unit || '') : ''}</span></div>
      ${mini(v && v.series, color)}
    </div>`;
  }).join('');

  mount.innerHTML = `<div class="cmp-head"><h3>Vitals</h3><span class="cmp-tag">daily</span></div>
    <div class="vitals-grid">${tiles}</div>`;
}, 60000);

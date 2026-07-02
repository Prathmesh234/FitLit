/* Zone distribution — time in each HR zone today, as a donut + legend. */
'use strict';
FitComp.register('cmp-zone-donut', '/api/comp/zones_today', function (mount, d, api) {
  if (!d || !d.zones || !d.total_min) { mount.innerHTML = '<p class="cmp-empty">no zone data today</p>'; return; }
  const cx = 70, cy = 70, r = 54, sw = 20, C = 2 * Math.PI * r;
  const total = d.total_min || 1;

  function arc(fromPct, lenPct, color) {
    const len = (lenPct) * C;
    const off = -fromPct * C;
    return `<circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="${color}" stroke-width="${sw}"
      stroke-dasharray="${len.toFixed(2)} ${(C - len).toFixed(2)}" stroke-dashoffset="${off.toFixed(2)}"
      transform="rotate(-90 ${cx} ${cy})" style="transition:stroke-dasharray .6s ease"/>`;
  }
  let acc = 0, segs = '';
  d.zones.forEach((z) => {
    const frac = z.minutes / total;
    if (frac > 0) segs += arc(acc, frac, api.zoneColors[z.zone] || api.palette.t30);
    acc += frac;
  });

  const legend = d.zones.map((z) =>
    `<div class="zl"><i style="background:${api.zoneColors[z.zone]}"></i>
      <span class="zl-n">${z.zone}</span><span class="zl-r">${z.range}</span>
      <b>${z.minutes}m</b><span class="zl-p">${z.pct}%</span></div>`).join('');

  mount.innerHTML = `
    <div class="cmp-head"><h3>Zones · today</h3><span class="cmp-tag">${d.total_min} min tracked</span></div>
    <div class="donut-wrap">
      <svg viewBox="0 0 140 140" class="donut">
        <circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="${api.palette.t08}" stroke-width="${sw}"/>
        ${segs}
        <text x="${cx}" y="${cy - 2}" text-anchor="middle" font-family="'Instrument Serif',serif" font-style="italic" font-size="30" fill="${api.palette.ink}">${d.total_min}</text>
        <text x="${cx}" y="${cy + 16}" text-anchor="middle" font-size="10" fill="${api.palette.t30}" letter-spacing="1.5" font-family="'Inter Tight',sans-serif">MINUTES</text>
      </svg>
      <div class="donut-legend">${legend}</div>
    </div>`;
}, 20000);

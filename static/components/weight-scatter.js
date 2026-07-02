/* Weight scatter — weigh-ins (fasted filled / other hollow), 7-day avg, target. */
'use strict';
FitComp.register('cmp-weight-scatter', '/api/comp/weight_scatter', function (mount, d, api) {
  if (!d || !d.points || !d.points.length) { mount.innerHTML = '<p class="cmp-empty">no weigh-ins logged</p>'; return; }
  const W = 640, H = 200, padL = 38, padR = 10, padT = 12, padB = 22;
  const iw = W - padL - padR, ih = H - padT - padB;
  const allV = d.points.map((p) => p.lb).concat(d.avg7.map((a) => a.lb));
  if (d.target) allV.push(d.target);
  const lo = Math.min(...allV) - 1.5, hi = Math.max(...allV) + 1.5;
  const y = (v) => padT + ih - ((v - lo) / (hi - lo)) * ih;
  const n = d.points.length;
  const x = (i) => padL + (n === 1 ? iw / 2 : (i / (n - 1)) * iw);

  // target line
  let target = '';
  if (d.target) {
    target = `<line x1="${padL}" y1="${y(d.target).toFixed(1)}" x2="${W - padR}" y2="${y(d.target).toFixed(1)}"
      stroke="${api.palette.clay}" stroke-width="1.4" stroke-dasharray="5 4"/>
      <text x="${W - padR}" y="${(y(d.target) - 5).toFixed(1)}" text-anchor="end" font-size="10" fill="${api.palette.clay}" font-family="'SF Mono',monospace">target ${d.target}</text>`;
  }
  // avg7 line
  const avgPts = d.avg7.map((a, i) => [x(i), y(a.lb)]);
  const avgLine = avgPts.length > 1 ? `<path d="${api.linePath(avgPts)}" fill="none" stroke="${api.palette.sage}" stroke-width="2.2" stroke-linejoin="round"/>` : '';
  // scatter dots
  const dots = d.points.map((p, i) => p.fasted
    ? `<circle cx="${x(i).toFixed(1)}" cy="${y(p.lb).toFixed(1)}" r="4" fill="${api.palette.ink}"><title>${p.date} ${p.lb} (fasted)</title></circle>`
    : `<circle cx="${x(i).toFixed(1)}" cy="${y(p.lb).toFixed(1)}" r="4" fill="none" stroke="${api.palette.t30}" stroke-width="1.6"><title>${p.date} ${p.lb}</title></circle>`
  ).join('');
  // y ticks
  let yt = '';
  [lo, (lo + hi) / 2, hi].forEach((v) => yt += `<text x="4" y="${(y(v) + 3).toFixed(1)}" font-size="9" fill="${api.palette.t30}" font-family="'SF Mono',monospace">${v.toFixed(0)}</text>`);

  mount.innerHTML = `
    <div class="cmp-head"><h3>Weight trend</h3><span class="cmp-tag">● fasted · ○ other</span></div>
    <svg viewBox="0 0 ${W} ${H}" class="cmp-svg" style="width:100%;height:${H}px">
      ${yt}${target}${avgLine}${dots}
    </svg>`;
}, 30000);

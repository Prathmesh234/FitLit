/* Hourly steps — today's step count by hour of day. */
'use strict';
FitComp.register('cmp-hourly-steps', '/api/comp/hourly_steps', function (mount, d, api) {
  if (!d || !d.hours) { mount.innerHTML = '<p class="cmp-empty">no step data</p>'; return; }
  const W = 640, H = 150, padL = 8, padR = 8, padT = 10, padB = 20;
  const iw = W - padL - padR, ih = H - padT - padB;
  const vals = d.hours.map((h) => h.steps);
  const mx = Math.max(...vals, 1);
  const bw = iw / 24;
  const nowHr = new Date().getHours();

  const bars = d.hours.map((h, i) => {
    const bh = (h.steps / mx) * ih;
    const cur = h.hour === nowHr;
    return `<rect x="${(padL + i * bw + 1).toFixed(1)}" y="${(padT + ih - bh).toFixed(1)}"
      width="${(bw - 2).toFixed(1)}" height="${bh.toFixed(1)}" rx="2"
      fill="${cur ? api.palette.clay : api.palette.honey}" opacity="${h.steps ? (cur ? 1 : 0.75) : 0.15}">
      <title>${String(h.hour).padStart(2, '0')}:00 · ${h.steps} steps</title></rect>`;
  }).join('');

  let xt = '';
  [0, 6, 12, 18, 23].forEach((hh) =>
    xt += `<text x="${(padL + hh * bw + bw / 2).toFixed(1)}" y="${H - 6}" font-size="9" fill="${api.palette.t30}" text-anchor="middle" font-family="'SF Mono',monospace">${String(hh).padStart(2, '0')}</text>`);

  mount.innerHTML = `
    <div class="cmp-head"><h3>Steps by hour</h3><span class="cmp-tag">${(d.total || 0).toLocaleString()} today</span></div>
    <svg viewBox="0 0 ${W} ${H}" class="cmp-svg" style="width:100%;height:${H}px">${bars}${xt}</svg>`;
}, 20000);

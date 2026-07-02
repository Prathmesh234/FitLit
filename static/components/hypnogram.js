/* Hypnogram — last night's sleep stages as a stepped timeline. */
'use strict';
FitComp.register('cmp-hypnogram', '/api/comp/hypnogram', function (mount, d, api) {
  if (!d || !d.stages || !d.stages.length) { mount.innerHTML = '<p class="cmp-empty">no sleep data</p>'; return; }
  const W = 640, H = 150, padL = 46, padR = 8, padT = 8, padB = 20;
  const iw = W - padL - padR, ih = H - padT - padB;
  const lanes = ['awake', 'rem', 'light', 'deep'];      // top → bottom
  const laneY = (s) => padT + (lanes.indexOf(s) + 0.5) * (ih / lanes.length);
  const laneH = (ih / lanes.length) * 0.7;
  const x = (m) => padL + (m / d.total_min) * iw;

  // stepped connecting line through segment centers
  let steps = [];
  d.stages.forEach((s) => {
    const y = laneY(s.type);
    steps.push([x(s.offset_min), y], [x(s.offset_min + s.dur_min), y]);
  });

  const blocks = d.stages.map((s) => {
    const c = api.stageColors[s.type] || api.palette.t30;
    return `<rect x="${x(s.offset_min).toFixed(1)}" y="${(laneY(s.type) - laneH / 2).toFixed(1)}"
      width="${Math.max(1.2, (x(s.offset_min + s.dur_min) - x(s.offset_min))).toFixed(1)}" height="${laneH.toFixed(1)}"
      rx="2.5" fill="${c}"><title>${s.type} ${Math.round(s.dur_min)}m</title></rect>`;
  }).join('');

  const laneLabels = lanes.map((l) =>
    `<text x="6" y="${(laneY(l) + 3).toFixed(1)}" font-size="10" fill="${api.palette.t30}" font-family="'Inter Tight',sans-serif">${l}</text>`).join('');

  // hour ticks
  let ticks = '';
  for (let m = 0; m <= d.total_min; m += 60) {
    ticks += `<line x1="${x(m).toFixed(1)}" y1="${padT}" x2="${x(m).toFixed(1)}" y2="${H - padB}" stroke="${api.palette.t08}"/>`;
  }

  mount.innerHTML = `
    <div class="cmp-head"><h3>Sleep architecture</h3><span class="cmp-tag">${d.bedtime} → ${d.wake} · ${d.efficiency}%</span></div>
    <svg viewBox="0 0 ${W} ${H}" class="cmp-svg" style="width:100%;height:${H}px">
      ${ticks}${laneLabels}
      <path d="${api.linePath(steps)}" fill="none" stroke="${api.palette.t14}" stroke-width="1.5"/>
      ${blocks}
    </svg>`;
}, 60000);

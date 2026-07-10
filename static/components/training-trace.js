/* Training trace — detected session overlaid with heart rate and movement. */
'use strict';
FitComp.register('cmp-training-trace', '/api/comp/training_trace', function (mount, d, api) {
  if (!d || !d.points || !d.points.length) {
    mount.innerHTML = '<p class="cmp-empty">no training trace today</p>';
    return;
  }
  const session = d.session;
  const W = 700, H = 220, padL = 38, padR = 12, padT = 12, padB = 24;
  const iw = W - padL - padR, ih = H - padT - padB;
  const minute = (t) => Number(t.slice(0, 2)) * 60 + Number(t.slice(3, 5));
  const minT = minute(d.points[0].t), maxT = Math.max(minT + 5, minute(d.points.at(-1).t));
  const x = (t) => padL + ((minute(t) - minT) / (maxT - minT)) * iw;
  const bpms = d.points.map((p) => p.bpm).filter((v) => v != null);
  const hrLo = Math.min(55, ...bpms) - 5;
  const hrHi = Math.max(180, ...bpms) + 5;
  const y = (bpm) => padT + ih - ((bpm - hrLo) / (hrHi - hrLo)) * ih;
  const maxSteps = Math.max(1, ...d.points.map((p) => p.steps || 0));
  const barW = Math.max(2, iw / d.points.length - 1);

  const bars = d.points.map((p) => {
    const height = ((p.steps || 0) / maxSteps) * ih * .42;
    return `<rect x="${(x(p.t) - barW / 2).toFixed(1)}" y="${(padT + ih - height).toFixed(1)}"
      width="${barW.toFixed(1)}" height="${height.toFixed(1)}" rx="2" fill="${api.palette.honey}" opacity=".45">
      <title>${p.t} · ${p.steps} steps</title></rect>`;
  }).join('');
  const hrPoints = d.points.filter((p) => p.bpm != null).map((p) => [x(p.t), y(p.bpm)]);
  const line = hrPoints.length > 1 ? api.linePath(hrPoints) : '';
  const zoneLines = (d.zones || []).map((z) =>
    `<line x1="${padL}" y1="${y(z.low).toFixed(1)}" x2="${W - padR}" y2="${y(z.low).toFixed(1)}"
      stroke="${api.zoneColors[z.zone]}" stroke-width="1" opacity=".18"/>`).join('');
  let sessionShade = '';
  if (session) {
    const x1 = x(session.start), x2 = x(session.end);
    sessionShade = `<rect x="${x1.toFixed(1)}" y="${padT}" width="${Math.max(4, x2 - x1).toFixed(1)}"
      height="${ih}" fill="${api.palette.clay}" opacity=".07" rx="6"/>
      <text x="${(x1 + 5).toFixed(1)}" y="${padT + 12}" font-size="9" fill="${api.palette.clay}" font-family="'SF Mono',monospace">DETECTED SESSION</text>`;
  }
  let ticks = '';
  for (let m = Math.ceil(minT / 120) * 120; m <= maxT; m += 120) {
    const label = `${String(Math.floor(m / 60)).padStart(2, '0')}:00`;
    ticks += `<text x="${x(label).toFixed(1)}" y="${H - 6}" text-anchor="middle" font-size="9" fill="${api.palette.t30}" font-family="'SF Mono',monospace">${label}</text>`;
  }
  const trace = (d.trace || []).map((row) => `
    <div class="trace-row ${row.level}">
      <span class="trace-time">${row.time}</span>
      <span class="trace-source">training</span>
      <span class="trace-message"><b>${row.title}</b><br>${row.detail}</span>
    </div>`).join('');
  const zoneText = session ? Object.entries(session.zone_minutes || {})
    .filter(([, value]) => value > 0)
    .map(([name, value]) => `${name.replaceAll('_', ' ')} ${value}m`).join(' · ') : '';

  mount.innerHTML = `
    <div class="cmp-head"><h3>Training trace</h3><span class="cmp-tag">data through ${d.data_as_of} PT</span></div>
    ${session ? `<div class="diagnostic-kpis">
      <div class="diagnostic-kpi"><span>detected block</span><b>${session.start}–${session.end}</b><small>${session.duration_min} minutes</small></div>
      <div class="diagnostic-kpi"><span>heart load</span><b>${session.avg_bpm}/${session.max_bpm}</b><small>average / peak bpm</small></div>
      <div class="diagnostic-kpi"><span>movement</span><b>${session.steps.toLocaleString()}</b><small>${session.distance_km} km</small></div>
      <div class="diagnostic-kpi"><span>recovery drop</span><b>${session.recovery_drop_bpm ?? '—'} bpm</b><small>next 15 minutes</small></div>
    </div>` : '<div class="insight-note">No sustained training block detected yet.</div>'}
    <svg viewBox="0 0 ${W} ${H}" class="training-svg">
      ${zoneLines}${sessionShade}${bars}${ticks}
      ${line ? `<path d="${line}" fill="none" stroke="${api.palette.rust}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>` : ''}
    </svg>
    <div class="chart-legend">
      <span><i style="background:${api.palette.rust}"></i>heart rate</span>
      <span><i style="background:${api.palette.honey};opacity:.55"></i>steps / 5 min</span>
      ${session ? `<span>${session.intensity} intensity · ${zoneText}</span>` : ''}
    </div>
    ${trace ? `<div class="cmp-head" style="margin-top:16px"><h3>Session events</h3><span class="cmp-tag">derived</span></div><div class="trace-list">${trace}</div>` : ''}`;
}, 20000);

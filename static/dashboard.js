/* FitLit live dashboard — fetch the snapshot, render metric components.
   Each render*() function owns one card and can be reused/embedded standalone. */
'use strict';

const REFRESH_MS = 5000;
const STEP_GOAL = 10000;
const $ = (id) => document.getElementById(id);

/* ---------- tiny SVG helpers ---------- */
function sparkline(el, values, color, fill) {
  if (!values || values.length < 2) { el.innerHTML = ''; return; }
  const w = 300, h = el.viewBox.baseVal.height || 60, pad = 4;
  const min = Math.min(...values), max = Math.max(...values);
  const rng = (max - min) || 1;
  const step = (w - pad * 2) / (values.length - 1);
  const pts = values.map((v, i) => [pad + i * step, h - pad - ((v - min) / rng) * (h - pad * 2)]);
  const d = pts.map((p, i) => (i ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join(' ');
  const area = d + ` L${pts[pts.length-1][0].toFixed(1)} ${h} L${pts[0][0].toFixed(1)} ${h} Z`;
  el.innerHTML = `
    ${fill ? `<path d="${area}" fill="${color}" opacity="0.12"/>` : ''}
    <path d="${d}" fill="none" stroke="${color}" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>
    <circle cx="${pts[pts.length-1][0].toFixed(1)}" cy="${pts[pts.length-1][1].toFixed(1)}" r="3.5" fill="${color}"/>`;
}

function bars(el, values, color) {
  if (!values || !values.length) { el.innerHTML = ''; return; }
  const w = 300, h = el.viewBox.baseVal.height || 70, pad = 2;
  const max = Math.max(...values, 1);
  const bw = (w - pad * 2) / values.length;
  el.innerHTML = values.map((v, i) => {
    const bh = (v / max) * (h - 6);
    return `<rect x="${(pad + i * bw + 1).toFixed(1)}" y="${(h - bh).toFixed(1)}" width="${(bw - 2).toFixed(1)}" height="${bh.toFixed(1)}" rx="2" fill="${color}" opacity="${i === values.length - 1 ? 1 : 0.45}"/>`;
  }).join('');
}

function gaugeRing(el, pct, color) {
  const cx = 100, cy = 100, r = 80, c = 2 * Math.PI * r;
  const off = c * (1 - pct / 100);
  el.innerHTML = `
    <circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="#1a2230" stroke-width="16"/>
    <circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="${color}" stroke-width="16"
      stroke-linecap="round" stroke-dasharray="${c}" stroke-dashoffset="${off}"
      transform="rotate(-90 ${cx} ${cy})" style="transition:stroke-dashoffset .8s ease, stroke .4s"/>
    <text x="${cx}" y="${cy - 4}" text-anchor="middle" fill="#e8eef6" font-size="46" font-weight="800" font-family="JetBrains Mono">${pct == null ? '—' : Math.round(pct)}</text>
    <text x="${cx}" y="${cy + 24}" text-anchor="middle" fill="#6f7d92" font-size="13" letter-spacing="2">READY</text>`;
}

function progressRing(el, pct, color) {
  const cx = 60, cy = 60, r = 50, c = 2 * Math.PI * r;
  const off = c * (1 - Math.min(pct, 100) / 100);
  el.innerHTML = `
    <circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="#1a2230" stroke-width="12"/>
    <circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="${color}" stroke-width="12"
      stroke-linecap="round" stroke-dasharray="${c}" stroke-dashoffset="${off}"
      style="transition:stroke-dashoffset .8s ease"/>`;
}

const bandColor = (b) => ({ green: '#00e6a8', amber: '#ffb020', red: '#ff4d6d' }[b] || '#36a3ff');
const fmt = (n) => (n == null ? '—' : Number(n).toLocaleString());

/* ---------- component renderers ---------- */
function renderReadiness(d) {
  if (!d || d.error || d.readiness == null) { $('readiness-band').textContent = 'no data'; return; }
  const col = bandColor(d.band);
  gaugeRing($('readiness-gauge'), d.readiness, col);
  const tag = $('readiness-band'); tag.textContent = d.band; tag.className = 'tag ' + d.band;
  const c = d.components || {};
  const parts = [['Sleep', c.sleep], ['HRV', c.hrv], ['Rest HR', c.resting_hr]];
  $('readiness-components').innerHTML = parts.map(([lbl, o]) => {
    const s = o && o.score != null ? o.score : null;
    const col = s == null ? '#4a566b' : s >= 75 ? '#00e6a8' : s >= 55 ? '#ffb020' : '#ff4d6d';
    return `<div class="rc"><span class="rc-lbl">${lbl}</span>
      <div class="rc-bar"><div class="rc-fill" style="width:${s || 0}%;background:${col}"></div></div>
      <span class="rc-val">${s == null ? '—' : Math.round(s)}</span></div>`;
  }).join('');
}

function renderHeart(d) {
  if (!d || d.error) { $('hr-bpm').textContent = '—'; return; }
  $('hr-bpm').textContent = d.live_bpm ?? '—';
  $('heart-asof').textContent = d.as_of ? 'as of ' + d.as_of : '';
  $('hr-range').textContent = (d.min != null) ? `30-min range ${d.min}–${d.max} bpm` : '';
  sparkline($('hr-spark'), d.series, '#ff4d6d', true);
}

function renderSteps(a) {
  if (!a || a.error) return;
  const today = a.steps_today;
  $('steps-num').textContent = fmt(today);
  $('steps-avg').textContent = a.avg_steps ? `avg ${fmt(a.avg_steps)}` : '';
  if (today != null) {
    const pct = Math.round((today / STEP_GOAL) * 100);
    $('steps-goal').textContent = `${pct}% of ${fmt(STEP_GOAL)} goal`;
  } else $('steps-goal').textContent = 'awaiting sync';
  bars($('steps-bars'), (a.series || []).map((s) => s.steps || 0), '#36a3ff');
}

function renderRecomp(d, prog) {
  if (!d || d.error || !d.current) return;
  const now = d.current.weight_lb, target = d.target.weight_lb;
  const start = prog && prog.latest_avg7_lb ? Math.max(now, prog.latest_avg7_lb) : now;
  $('recomp-now').textContent = now;
  $('recomp-target').textContent = target;
  $('recomp-startlbl').textContent = `${start} lb`;
  $('recomp-targetlbl').textContent = `target ${target} lb`;
  $('recomp-asof').textContent = d.as_of ? 'as of ' + d.as_of : '';
  // progress: how far from start toward target (lower = better)
  const span = (start - target) || 1;
  const done = Math.max(0, Math.min(1, (start - now) / span));
  $('recomp-fill').style.width = (done * 100).toFixed(0) + '%';
  $('recomp-marker').style.left = (done * 100).toFixed(0) + '%';
  $('recomp-kpis').innerHTML = `
    <div class="kpi"><b>${d.fat_to_lose_lb ?? '—'}</b><span>lb to lose</span></div>
    <div class="kpi"><b>${d.eta_weeks ?? '—'}</b><span>weeks ETA</span></div>
    <div class="kpi"><b>${d.daily_deficit_kcal ?? '—'}</b><span>kcal deficit/d</span></div>
    <div class="kpi"><b>${d.current.lean_mass_kg ?? '—'}</b><span>lean kg</span></div>`;
}

function renderSleep(s) {
  if (!s) return;
  const n = s.last_night;
  if (n) {
    $('sleep-hrs').textContent = n.hours_asleep != null ? n.hours_asleep.toFixed(1) : '—';
    $('sleep-eff').textContent = n.efficiency_pct != null ? n.efficiency_pct + '%' : '';
  }
  $('sleep-avg').textContent = s.avg_hours ? `avg ${s.avg_hours}h` : '';
  // stage bars need stage minutes — derive from nights if present (fallback hidden)
  const stages = n && n.stages;
  if (stages) {
    const tot = Object.values(stages).reduce((a, b) => a + b, 0) || 1;
    $('sleep-stages').innerHTML = ['deep', 'rem', 'light', 'awake'].map((k) =>
      `<div class="stage ${k}" style="width:${((stages[k] || 0) / tot) * 100}%"></div>`).join('');
  }
  sparkline($('sleep-spark'), (s.nights || []).map((x) => x.hours_asleep).filter((v) => v != null), '#9d7bff', true);
}

function renderProtein(p) {
  if (!p || p.error) return;
  const target = p.target_g, logged = p.logged_g;
  $('protein-target').textContent = target ?? '—';
  $('protein-logged').textContent = logged != null ? logged : '0';
  const pct = (target && logged) ? (logged / target) * 100 : 0;
  progressRing($('protein-ring'), pct, pct >= 90 ? '#00e6a8' : '#36a3ff');
  if (p.gap_g != null) $('protein-gap').textContent = p.gap_g > 0 ? `${p.gap_g}g to go` : 'target met ✓';
  else $('protein-gap').textContent = 'log meals to track';
}

function renderRHR(r) {
  if (!r || r.error) return;
  $('rhr-num').textContent = r.latest ?? '—';
  const s = r.series || [];
  if (s.length >= 2) {
    const delta = s[s.length - 1] - s[0];
    const t = $('rhr-trend');
    t.textContent = (delta <= 0 ? '▼ ' : '▲ ') + Math.abs(delta).toFixed(0);
    t.className = 'trend ' + (delta <= 0 ? 'down' : 'up');
  }
  sparkline($('rhr-spark'), s, '#00e6a8', true);
}

function renderZones(z) {
  if (!z || z.error || !z.zones) return;
  $('zones-max').textContent = `max ${z.max_hr} · rest ${z.resting_hr ?? '—'}`;
  const colors = { 'Zone 1': '#4a566b', 'Zone 2': '#00e6a8', 'Zone 3': '#36a3ff', 'Zone 4': '#ffb020', 'Zone 5': '#ff4d6d' };
  const maxHi = Math.max(...z.zones.map((x) => x.bpm_high));
  $('zones-list').innerHTML = z.zones.map((x) => {
    const isT = x.zone === 'Zone 2';
    const wpct = (x.bpm_high / maxHi) * 100;
    return `<div class="zrow ${isT ? 'target' : ''}">
      <span class="zname">${x.zone}</span>
      <div class="zbar" style="width:${wpct}%;background:${colors[x.zone]}">${isT ? 'TARGET' : ''}</div>
      <span class="zrange">${x.bpm_low}–${x.bpm_high}</span></div>`;
  }).join('');
}

/* ---------- poll loop ---------- */
async function tick() {
  try {
    const r = await fetch('/dashboard/data', { cache: 'no-store' });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const d = await r.json();
    $('live-dot').className = 'dot live';
    $('live-label').textContent = 'live';
    $('generated').textContent = d.generated_at + ' PT';
    $('err').textContent = '';
    renderReadiness(d.readiness);
    renderHeart(d.heart);
    renderSteps(d.activity);
    renderRecomp(d.recomp, d.recomp_progress);
    renderSleep(d.sleep);
    renderProtein(d.protein);
    renderRHR(d.resting_hr);
    renderZones(d.zones);
  } catch (e) {
    $('live-dot').className = 'dot';
    $('live-label').textContent = 'offline';
    $('err').textContent = String(e.message || e);
  }
}

$('refresh-rate').textContent = (REFRESH_MS / 1000) + 's';
tick();
setInterval(tick, REFRESH_MS);

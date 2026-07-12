/* FitLit dashboard — tabbed, warm visuals. Polls /dashboard/data and renders. */
'use strict';

const REFRESH_MS = 5000;
const STEP_GOAL = 10000;
const $ = (id) => document.getElementById(id);

/* warm palette */
const C = { clay: '#bd6a4a', sage: '#7c8154', honey: '#c8973f', rust: '#a94e33', teal: '#5f8579', rose: '#b07766', ink: '#221e16', faint: 'rgba(34,30,22,.10)' };
const bandCol = (b) => ({ green: C.sage, amber: C.honey, red: C.rust }[b] || C.clay);
const fmt = (n) => (n == null ? '—' : Number(n).toLocaleString());
const last = (items) => items[items.length - 1];

/* ---------- tabs ---------- */
$('tabs').addEventListener('click', (e) => {
  const t = e.target.closest('.tab'); if (!t) return;
  document.querySelectorAll('.tab').forEach((x) => x.classList.toggle('is-active', x === t));
  const name = t.dataset.tab;
  document.querySelectorAll('.panel').forEach((p) => p.classList.toggle('is-active', p.dataset.panel === name));
});

/* ---------- svg helpers ---------- */
function spark(el, vals, color, fill = true) {
  if (!el || !vals || vals.length < 2) { if (el) el.innerHTML = ''; return; }
  const w = 320, h = el.viewBox.baseVal.height || 56, pad = 5;
  const mn = Math.min(...vals), mx = Math.max(...vals), rng = (mx - mn) || 1;
  const dx = (w - pad * 2) / (vals.length - 1);
  const pts = vals.map((v, i) => [pad + i * dx, h - pad - ((v - mn) / rng) * (h - pad * 2)]);
  const line = pts.map((p, i) => (i ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join(' ');
  const area = line + ` L${last(pts)[0].toFixed(1)} ${h} L${pts[0][0].toFixed(1)} ${h} Z`;
  el.innerHTML =
    (fill ? `<path d="${area}" fill="${color}" opacity="0.12"/>` : '') +
    `<path d="${line}" fill="none" stroke="${color}" stroke-width="2.4" stroke-linejoin="round" stroke-linecap="round"/>` +
    `<circle cx="${last(pts)[0].toFixed(1)}" cy="${last(pts)[1].toFixed(1)}" r="3.4" fill="${color}"/>`;
}

function bars(el, vals, color) {
  if (!el || !vals || !vals.length) { if (el) el.innerHTML = ''; return; }
  const w = 320, h = el.viewBox.baseVal.height || 64, pad = 2, mx = Math.max(...vals, 1);
  const bw = (w - pad * 2) / vals.length;
  el.innerHTML = vals.map((v, i) => {
    const bh = (v / mx) * (h - 6);
    return `<rect x="${(pad + i * bw + 1).toFixed(1)}" y="${(h - bh).toFixed(1)}" width="${(bw - 2).toFixed(1)}" height="${bh.toFixed(1)}" rx="3" fill="${color}" opacity="${i === vals.length - 1 ? 1 : 0.4}"/>`;
  }).join('');
}

function gauge(el, pct, color) {
  const cx = 110, cy = 110, r = 88, c = 2 * Math.PI * r;
  const off = pct == null ? c : c * (1 - pct / 100);
  el.innerHTML = `
    <circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="${C.faint}" stroke-width="14"/>
    <circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="${color}" stroke-width="14"
      stroke-linecap="round" stroke-dasharray="${c}" stroke-dashoffset="${off}"
      transform="rotate(-90 ${cx} ${cy})" style="transition:stroke-dashoffset .9s ease, stroke .4s"/>
    <text x="${cx}" y="${cy - 2}" text-anchor="middle" fill="${C.ink}" font-family="'Instrument Serif',serif" font-style="italic" font-size="60">${pct == null ? '—' : Math.round(pct)}</text>
    <text x="${cx}" y="${cy + 26}" text-anchor="middle" fill="rgba(34,30,22,.4)" font-size="12" letter-spacing="3" font-family="'Inter Tight',sans-serif">READY</text>`;
}

function ring(el, pct, color) {
  const cx = 65, cy = 65, r = 54, c = 2 * Math.PI * r;
  const off = c * (1 - Math.min(pct || 0, 100) / 100);
  el.innerHTML = `
    <circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="${C.faint}" stroke-width="11"/>
    <circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="${color}" stroke-width="11"
      stroke-linecap="round" stroke-dasharray="${c}" stroke-dashoffset="${off}" style="transition:stroke-dashoffset .9s ease"/>`;
}

function stages(el, st) {
  if (!el) return;
  if (!st) { el.innerHTML = ''; return; }
  const tot = Object.values(st).reduce((a, b) => a + b, 0) || 1;
  el.innerHTML = ['deep', 'rem', 'light', 'awake'].map((k) =>
    `<div class="seg ${k}" style="width:${((st[k] || 0) / tot) * 100}%"></div>`).join('');
}

/* ---------- renderers ---------- */
function renderReadiness(d) {
  if (!d || d.error || d.readiness == null) return;
  const col = bandCol(d.band);
  gauge($('ov-gauge'), d.readiness, col);
  const p = $('ov-band'); p.textContent = d.band; p.className = 'pill ' + d.band;
  $('ov-reco').textContent = d.recommendation || '';
  $('ribbon-readiness').textContent = `readiness ${Math.round(d.readiness)} · ${d.band}`;
  const c = d.components || {};
  $('ov-components').innerHTML = [['Sleep', c.sleep], ['HRV', c.hrv], ['Rest HR', c.resting_hr]].map(([l, o]) => {
    const s = o && o.score != null ? o.score : null;
    const col = s == null ? C.faint : s >= 75 ? C.sage : s >= 55 ? C.honey : C.rust;
    return `<div class="hb"><span class="hb-l">${l}</span><div class="hb-track"><div class="hb-fill" style="width:${s || 0}%;background:${col}"></div></div><span class="hb-v">${s == null ? '—' : Math.round(s)}</span></div>`;
  }).join('');
}

function renderHeart(h) {
  if (!h || h.error) return;
  const asof = h.as_of ? 'as of ' + h.as_of : '';
  const rng = h.min != null ? `30-min range ${h.min}–${h.max} bpm` : '';
  ['ov-hr', 'ca-hr'].forEach((id) => $(id) && ($(id).textContent = h.live_bpm ?? '—'));
  $('ov-hr-asof').textContent = asof; $('ca-asof').textContent = asof;
  $('ca-range').textContent = rng;
  spark($('ov-hr-spark'), h.series, C.rust);
  spark($('ca-spark'), h.series, C.rust);
}

function renderSteps(a) {
  if (!a || a.error) return;
  const today = a.steps_today, avg = a.avg_steps;
  $('ov-steps').textContent = fmt(today);
  $('ov-steps-avg').textContent = avg ? `avg ${fmt(avg)}` : '';
  $('ca-steps-avg').textContent = fmt(avg);
  if (today != null) {
    const pct = Math.min(100, Math.round((today / STEP_GOAL) * 100));
    $('ov-steps-meter').style.width = pct + '%';
    $('ov-steps-goal').textContent = `${pct}% of ${fmt(STEP_GOAL)} goal`;
  } else $('ov-steps-goal').textContent = 'awaiting sync';
  bars($('ca-steps-bars'), (a.series || []).map((s) => s.steps || 0), C.honey);
}

function renderRecomp(d, prog) {
  if (!d || d.error || !d.current) return;
  const now = d.current.weight_lb, target = d.target.weight_lb;
  const start = prog && prog.latest_avg7_lb ? Math.max(now, prog.latest_avg7_lb) : now;
  const span = (start - target) || 1, done = Math.max(0, Math.min(1, (start - now) / span));
  const kpis = `
    <div class="kpi"><b>${d.fat_to_lose_lb ?? '—'}</b><span>lb to lose</span></div>
    <div class="kpi"><b>${d.eta_weeks ?? '—'}</b><span>weeks eta</span></div>
    <div class="kpi"><b>${d.daily_deficit_kcal ?? '—'}</b><span>kcal/day</span></div>`;
  [['ov-now', 'ov-target', 'ov-track', 'ov-dot', 'ov-kpis', 'ov-recomp-asof'],
   ['bo-now', 'bo-target', 'bo-track', 'bo-dot', 'bo-kpis', 'bo-asof']].forEach(([n, t, tr, dot, k, as]) => {
    if (!$(n)) return;
    $(n).textContent = now; $(t).textContent = target;
    $(tr).style.width = (done * 100).toFixed(0) + '%';
    $(dot).style.left = (done * 100).toFixed(0) + '%';
    $(k).innerHTML = kpis;
    $(as).textContent = d.as_of ? 'as of ' + d.as_of : '';
  });
  const cur = d.current;
  if ($('bo-comp')) $('bo-comp').innerHTML = `
    <div class="kpi"><b>${cur.lean_mass_kg ?? '—'}</b><span>lean kg</span></div>
    <div class="kpi"><b>${cur.fat_mass_kg ?? '—'}</b><span>fat kg</span></div>
    <div class="kpi"><b>${cur.bmi ?? '—'}</b><span>bmi</span></div>
    <div class="kpi"><b>${(d.assumptions && d.assumptions.bodyfat_pct) ?? '—'}%</b><span>body fat</span></div>`;
}

function renderSleep(s) {
  if (!s) return;
  const n = s.last_night;
  if (n) {
    ['ov-sleep-h', 'sl-hours'].forEach((id) => $(id) && ($(id).textContent = n.hours_asleep != null ? n.hours_asleep.toFixed(1) : '—'));
    const eff = n.efficiency_pct != null ? n.efficiency_pct + '% efficiency' : '';
    $('ov-sleep-eff').textContent = eff; $('sl-eff').textContent = n.efficiency_pct != null ? n.efficiency_pct + '%' : '';
    $('sl-date').textContent = n.night || '';
    stages($('ov-stages'), n.stages); stages($('sl-stages'), n.stages);
    if (n.stages) {
      $('sl-deep').textContent = n.stages.deep ?? '—';
      $('sl-rem').textContent = n.stages.rem ?? '—';
      $('sl-kpis').innerHTML = `
        <div class="kpi"><b>${n.bedtime || '—'}</b><span>bedtime</span></div>
        <div class="kpi"><b>${n.wake || '—'}</b><span>wake</span></div>
        <div class="kpi"><b>${n.awake_min ?? '—'}</b><span>min awake</span></div>`;
      $('sl-legend').innerHTML = [['deep', C.teal, 'Deep'], ['rem', C.rose, 'REM'], ['light', C.honey, 'Light'], ['awake', 'rgba(34,30,22,.22)', 'Awake']]
        .map(([k, c, l]) => `<span><i style="background:${c}"></i>${l} ${n.stages[k] ?? 0}m</span>`).join('');
    }
  }
  const avg = s.avg_hours ? `avg ${s.avg_hours}h` : '';
  $('sl-avg').textContent = avg;
  const nights = s.nights || [];
  spark($('sl-spark'), nights.map((x) => x.hours_asleep).filter((v) => v != null), C.teal);
  spark($('sl-eff-spark'), nights.map((x) => x.efficiency_pct).filter((v) => v != null), C.sage);
  if (nights.length) $('sl-axis').innerHTML = `<span>${nights[0].night}</span><span>${last(nights).night}</span>`;
}

function renderProtein(p) {
  if (!p || p.error) return;
  $('bo-prot-t').textContent = p.target_g ?? '—';
  $('bo-prot').textContent = p.logged_g != null ? p.logged_g : '0';
  const pct = (p.target_g && p.logged_g) ? (p.logged_g / p.target_g) * 100 : 0;
  ring($('bo-ring'), pct, pct >= 90 ? C.sage : C.clay);
  $('bo-prot-gap').textContent = p.gap_g != null ? (p.gap_g > 0 ? `${p.gap_g}g to go` : 'target met') : 'log meals to track';
}

function renderRHR(r) {
  if (!r || r.error) return;
  $('ca-rhr').textContent = r.latest ?? '—';
  const s = r.series || [];
  if (s.length >= 2) {
    const delta = last(s) - s[0], t = $('ca-rhr-trend');
    t.textContent = (delta <= 0 ? '▼ ' : '▲ ') + Math.abs(delta).toFixed(0);
    t.className = 'aside trend ' + (delta <= 0 ? 'down' : 'up');
  }
  spark($('ca-rhr-spark'), s, C.sage);
}

function renderWeight(w) {
  if (!w || w.error) return;
  $('bo-w').textContent = w.avg7_lb ?? '—';
  $('bo-w-n').textContent = w.n_readings ? `${w.n_readings} readings` : '';
  spark($('bo-w-spark'), (w.series || []).map((x) => x.avg7_lb).filter((v) => v != null), C.clay);
}

function renderZones(z) {
  if (!z || z.error || !z.zones) return;
  $('ca-zmax').textContent = `max ${z.max_hr} · rest ${z.resting_hr ?? '—'}`;
  const cols = { 'Zone 1': 'rgba(34,30,22,.30)', 'Zone 2': C.sage, 'Zone 3': C.teal, 'Zone 4': C.honey, 'Zone 5': C.rust };
  const mx = Math.max(...z.zones.map((x) => x.bpm_high));
  $('ca-zones').innerHTML = z.zones.map((x) => {
    const t = x.zone === 'Zone 2';
    return `<div class="zrow ${t ? 'target' : ''}"><span class="zname">${x.zone}</span>
      <div class="zbar" style="width:${(x.bpm_high / mx) * 100}%;background:${cols[x.zone]}">${t ? 'TARGET' : ''}</div>
      <span class="zrange">${x.bpm_low}–${x.bpm_high}</span></div>`;
  }).join('');
}

/* ---------- poll ---------- */
async function tick() {
  try {
    const r = await fetch('/dashboard/data', { cache: 'no-store' });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const d = await r.json();
    $('live-dot').className = 'dot on'; $('live-label').textContent = 'live';
    $('generated').textContent = d.generated_at + ' PT';

    const errors = [];
    [
      ['readiness', () => renderReadiness(d.readiness)],
      ['heart', () => renderHeart(d.heart)],
      ['steps', () => renderSteps(d.activity)],
      ['recomp', () => renderRecomp(d.recomp, d.recomp_progress)],
      ['sleep', () => renderSleep(d.sleep)],
      ['protein', () => renderProtein(d.protein)],
      ['resting HR', () => renderRHR(d.resting_hr)],
      ['weight', () => renderWeight(d.weight)],
      ['zones', () => renderZones(d.zones)],
    ].forEach(([name, render]) => {
      try {
        render();
      } catch (error) {
        errors.push(`${name}: ${error.message || error}`);
      }
    });
    $('err').textContent = errors.join(' · ');
  } catch (e) {
    $('live-dot').className = 'dot'; $('live-label').textContent = 'offline';
    $('err').textContent = String(e.message || e);
  }
}

$('rate').textContent = (REFRESH_MS / 1000) + 's';
tick();
setInterval(tick, REFRESH_MS);

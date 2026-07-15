/* Body and fuel diagnostics — recomposition trajectory and data confidence. */
'use strict';
FitComp.register('cmp-body-insights', '/api/comp/body_insights', function (mount, d) {
  if (!d || !d.plan) {
    mount.innerHTML = '<p class="cmp-empty">body diagnostics unavailable</p>';
    return;
  }
  const p = d.plan, fuel = d.fuel || {}, coverage = d.coverage || {};
  const pace = p.actual_weekly_change_lb == null ? '—' :
    `${p.actual_weekly_change_lb > 0 ? '+' : ''}${p.actual_weekly_change_lb} lb`;
  const balance = fuel.balance_kcal == null ? 'unlogged' :
    `${fuel.balance_kcal > 0 ? '+' : ''}${fuel.balance_kcal} kcal`;
  const priorities = (d.priorities || []).map((x) => `<li>${x}</li>`).join('');
  const trace = (d.trace || []).map((row) => `
    <div class="trace-row ${row.level}">
      <span class="trace-time">${row.time || '—'}</span>
      <span class="trace-source">body</span>
      <span class="trace-message"><b>${row.title}</b><br>${row.detail}</span>
    </div>`).join('');

  mount.innerHTML = `
    <div class="cmp-head"><h3>Body-plan diagnostics</h3><span class="cmp-tag">configured estimate + logged trend</span></div>
    <div class="diagnostic-kpis">
      <div class="diagnostic-kpi"><span>body-fat model</span><b>${p.bodyfat_pct ?? '—'}% → ${p.target_bodyfat_pct ?? '—'}%</b><small>configured estimate</small></div>
      <div class="diagnostic-kpi"><span>remaining</span><b>${p.to_go_lb ?? '—'} lb</b><small>${p.eta_weeks ?? '—'} projected weeks</small></div>
      <div class="diagnostic-kpi"><span>target pace</span><b>-${p.target_weekly_loss_lb ?? '—'} lb</b><small>per week</small></div>
      <div class="diagnostic-kpi"><span>observed pace</span><b>${pace}</b><small>requires 5+ fasted readings</small></div>
    </div>
    <div class="body-grid">
      <div>
        <div class="cmp-head"><h3>Fuel controls</h3><span class="cmp-tag">today</span></div>
        <div class="ops-strip">
          <div class="ops-stat"><span>protein</span><b>${fuel.protein_logged_g ?? '—'} / ${fuel.protein_target_g ?? '—'}g</b></div>
          <div class="ops-stat"><span>energy balance</span><b>${balance}</b></div>
          <div class="ops-stat"><span>calories out</span><b>${fuel.calories_out ?? '—'}</b></div>
          <div class="ops-stat"><span>14d avg out</span><b>${fuel.avg_calories_out ?? '—'}</b></div>
        </div>
        <div class="cmp-head"><h3>Data coverage</h3><span class="cmp-tag">confidence inputs</span></div>
        <div class="coverage-grid">
          <div class="coverage"><b>${coverage.fasted_weights ?? 0}</b><span>fasted weights</span></div>
          <div class="coverage"><b>${coverage.meal_days ?? 0}</b><span>meal days</span></div>
          <div class="coverage"><b>${coverage.waist_readings ?? 0}</b><span>waist readings</span></div>
          <div class="coverage"><b>${coverage.workouts_logged ?? 0}</b><span>workout logs</span></div>
        </div>
      </div>
      <div>
        <div class="cmp-head"><h3>Training priorities</h3><span class="cmp-tag">configured profile</span></div>
        <ol class="priority-list">${priorities || '<li>No private priorities configured</li>'}</ol>
        <div class="cmp-head" style="margin-top:16px"><h3>Plan trace</h3><span class="cmp-tag">latest state</span></div>
        <div class="trace-list">${trace}</div>
      </div>
    </div>`;
}, 30000);

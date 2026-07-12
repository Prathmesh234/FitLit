/* FitComp — tiny framework for self-fetching dashboard components.
   Each component registers a mount id + endpoint + render fn and refreshes on
   its own cadence, independent of the rest of the page. */
'use strict';

window.FitComp = (function () {
  const palette = {
    clay: '#bd6a4a', sage: '#7c8154', honey: '#c8973f', rust: '#a94e33',
    teal: '#5f8579', rose: '#b07766', ink: '#221e16',
    t30: 'rgba(34,30,22,.30)', t14: 'rgba(34,30,22,.14)', t08: 'rgba(34,30,22,.08)',
  };
  const zoneColors = {
    'Zone 1': 'rgba(34,30,22,.28)', 'Zone 2': '#7c8154', 'Zone 3': '#5f8579',
    'Zone 4': '#c8973f', 'Zone 5': '#a94e33',
  };
  const stageColors = { deep: '#5f8579', rem: '#b07766', light: 'rgba(200,151,63,.7)', awake: 'rgba(34,30,22,.22)' };

  const SVGNS = 'http://www.w3.org/2000/svg';
  function svg(tag, attrs) {
    const e = document.createElementNS(SVGNS, tag);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    return e;
  }
  function el(w, h) {
    const s = svg('svg', { viewBox: `0 0 ${w} ${h}`, class: 'cmp-svg', preserveAspectRatio: 'none' });
    s.style.width = '100%'; s.style.height = h + 'px'; s.style.display = 'block';
    return s;
  }
  // smooth polyline path from [[x,y]...]
  function linePath(pts) {
    return pts.map((p, i) => (i ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join(' ');
  }
  function formatDay(day) {
    return new Date(day + 'T12:00:00').toLocaleDateString('en-US', {
      timeZone: 'America/Los_Angeles', weekday: 'short', month: 'short', day: 'numeric',
    });
  }
  function bindTooltip(root, selector, describe) {
    if (!root) return;
    const tip = document.createElement('div');
    tip.className = 'chart-tooltip';
    tip.setAttribute('role', 'tooltip');
    root.appendChild(tip);

    function fill(data) {
      tip.textContent = '';
      const title = document.createElement('b');
      title.textContent = data.title;
      tip.appendChild(title);
      (data.rows || []).forEach((row) => {
        const line = document.createElement('span');
        const label = document.createElement('i');
        const value = document.createElement('strong');
        label.textContent = row.label;
        value.textContent = row.value;
        if (row.color) label.style.setProperty('--tip-color', row.color);
        line.appendChild(label);
        line.appendChild(value);
        tip.appendChild(line);
      });
    }
    function position(target, event) {
      const rootRect = root.getBoundingClientRect();
      const targetRect = target.getBoundingClientRect();
      const pointerX = event && event.clientX ? event.clientX : targetRect.left + targetRect.width / 2;
      const pointerY = event && event.clientY ? event.clientY : targetRect.top + targetRect.height / 2;
      let left = pointerX - rootRect.left + 12;
      let top = pointerY - rootRect.top + 12;
      if (left + tip.offsetWidth > rootRect.width - 8) left -= tip.offsetWidth + 24;
      if (top + tip.offsetHeight > rootRect.height - 8) top -= tip.offsetHeight + 24;
      tip.style.left = Math.max(8, left) + 'px';
      tip.style.top = Math.max(8, top) + 'px';
    }
    function show(target, event) {
      const data = describe(target);
      if (!data) return;
      fill(data);
      tip.classList.add('is-visible');
      position(target, event);
    }
    root.querySelectorAll(selector).forEach((target) => {
      const data = describe(target);
      if (!data) return;
      target.setAttribute('tabindex', '0');
      target.setAttribute('role', 'img');
      target.setAttribute('aria-label', [data.title].concat(
        (data.rows || []).map((row) => row.label + ' ' + row.value)
      ).join(', '));
      target.addEventListener('pointerenter', (event) => show(target, event));
      target.addEventListener('pointermove', (event) => position(target, event));
      target.addEventListener('pointerleave', () => tip.classList.remove('is-visible'));
      target.addEventListener('focus', () => show(target));
      target.addEventListener('blur', () => tip.classList.remove('is-visible'));
    });
  }

  const registry = [];
  function visible(c) {
    if (document.hidden) return false;
    const mount = document.getElementById(c.mount);
    const panel = mount && mount.closest('.panel');
    return Boolean(mount && (!panel || panel.classList.contains('is-active')));
  }
  async function run(c) {
    if (!visible(c)) return;
    try {
      const r = await fetch(c.endpoint, { cache: 'no-store' });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const data = await r.json();
      const mount = document.getElementById(c.mount);
      if (mount) { c.render(mount, data, api); mount.classList.remove('cmp-err'); }
    } catch (e) {
      const mount = document.getElementById(c.mount);
      if (mount) mount.classList.add('cmp-err');
    }
  }

  function register(mount, endpoint, render, intervalMs) {
    const c = { mount, endpoint, render, intervalMs: intervalMs || 8000 };
    registry.push(c);
    // stagger initial fetches slightly so they don't all fire at once
    setTimeout(() => { run(c); c.timer = setInterval(() => run(c), c.intervalMs); },
      120 * registry.length);
  }

  const tabs = document.getElementById('tabs');
  if (tabs) {
    tabs.addEventListener('click', () => {
      setTimeout(() => registry.filter(visible).forEach(run), 0);
    });
  }

  const api = { palette, zoneColors, stageColors, svg, el, linePath, formatDay, bindTooltip };
  return { register, palette, zoneColors, stageColors, svg, el, linePath, formatDay, bindTooltip, _api: api };
})();

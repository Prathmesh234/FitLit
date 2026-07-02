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

  const registry = [];
  async function run(c) {
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

  const api = { palette, zoneColors, stageColors, svg, el, linePath };
  return { register, palette, zoneColors, stageColors, svg, el, linePath, _api: api };
})();

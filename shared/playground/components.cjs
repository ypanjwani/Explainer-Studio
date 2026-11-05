// Copied from the ai-playground experiment (Bloging-substack/ECommerce/ai-playground), adapted for Explainer Studio's Playground mode.
// Pre-built components. Layout, styling and events are fixed here; behaviour
// comes only from compiled slot functions passed in via `slots`.
// ctx = { store, apply(patch, where), run(slotFn, where, ...args), runWith(slotFn, where, state, ...args),
//         registerPlayer(p), reset() }
(function (root) {
  const SVG_NS = 'http://www.w3.org/2000/svg';
  const PALETTE = ['blue', 'orange', 'green', 'red', 'purple', 'teal', 'gray', 'pink'];

  const el = (tag, cls, txt) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (txt !== undefined) e.textContent = txt;
    return e;
  };
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  // Big numbers in full with thousands separators (262,144, not 2.62e+5: parameter counts and memory sizes are common
  // in AI topics); only very large or very small ones in e-notation.
  const group = (v) => (v < 0 ? '-' : '') + String(Math.round(Math.abs(v))).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  const fmt = (v) => {
    if (typeof v !== 'number') return String(v);
    if (v === 0) return '0';
    const a = Math.abs(v);
    if (a >= 1e4 && a < 1e15) return group(v);
    if (a >= 1e4 || a < 1e-3) return v.toExponential(2);
    return String(Number(v.toPrecision(a >= 100 ? 4 : 3)));
  };
  // Axis ticks: short forms (200k, 1.5M), since a y tick label has about 40 px left of the axis.
  const fmtTick = (v) => {
    const a = Math.abs(v);
    for (const [u, s] of [[1e12, 'T'], [1e9, 'B'], [1e6, 'M'], [1e3, 'k']]) if (a >= Math.max(u, 1e4)) return String(Number((v / u).toPrecision(3))) + s;
    return fmt(v);
  };

  // ---------------------------------------------------------------- controls
  function slider(box, spec, slots, ctx) {
    const wrap = el('label', 'pg-ctl pg-slider');
    const head = el('div', 'pg-ctl-head');
    const name = el('span', 'pg-ctl-label', spec.label || spec.key);
    const out = el('span', 'pg-ctl-value');
    head.append(name, out);
    const input = el('input');
    Object.assign(input, { type: 'range', min: spec.min, max: spec.max, step: spec.step || (spec.max - spec.min) / 100 });
    const range = el('div', 'pg-range');
    range.append(el('span', '', fmt(spec.min)), el('span', '', fmt(spec.max)));
    wrap.append(head, input, range);
    box.append(wrap);
    input.addEventListener('input', () => {
      const v = Number(input.value);
      const patch = slots.onChange ? ctx.run(slots.onChange, `${spec.id}.onChange`, v) : { [spec.key]: v };
      ctx.apply(patch, `${spec.id}.onChange`);
    });
    ctx.store.watch((st) => {
      const v = st[spec.key];
      if (typeof v === 'number') { input.value = v; out.textContent = fmt(v); }
    });
  }

  function select(box, spec, slots, ctx) {
    const wrap = el('label', 'pg-ctl pg-select');
    wrap.append(el('span', 'pg-ctl-label', spec.label || spec.key));
    const sel = el('select');
    const opts = spec.options.map((o) => (o !== null && typeof o === 'object' ? o : { value: o, label: String(o) }));
    opts.forEach((o, i) => { const op = el('option', '', o.label ?? String(o.value)); op.value = i; sel.append(op); });
    wrap.append(sel);
    box.append(wrap);
    sel.addEventListener('change', () => {
      const v = opts[Number(sel.value)].value;
      const patch = slots.onChange ? ctx.run(slots.onChange, `${spec.id}.onChange`, v) : { [spec.key]: v };
      ctx.apply(patch, `${spec.id}.onChange`);
    });
    ctx.store.watch((st) => {
      const cur = JSON.stringify(st[spec.key]); // option values may be arrays/objects
      const i = opts.findIndex((o) => JSON.stringify(o.value) === cur);
      if (i >= 0) sel.value = i;
    });
  }

  function toggle(box, spec, slots, ctx) {
    const wrap = el('label', 'pg-ctl pg-toggle');
    const input = el('input');
    input.type = 'checkbox';
    wrap.append(input, el('span', 'pg-switch'), el('span', 'pg-ctl-label', spec.label || spec.key));
    box.append(wrap);
    input.addEventListener('change', () => {
      const v = input.checked;
      const patch = slots.onChange ? ctx.run(slots.onChange, `${spec.id}.onChange`, v) : { [spec.key]: v };
      ctx.apply(patch, `${spec.id}.onChange`);
    });
    ctx.store.watch((st) => { input.checked = !!st[spec.key]; });
  }

  function button(box, spec, slots, ctx) {
    const b = el('button', 'pg-btn' + (spec.action === 'reset' ? ' pg-btn-ghost' : ''), spec.label || (spec.action === 'reset' ? 'Reset' : 'Go'));
    b.type = 'button';
    box.append(b);
    b.addEventListener('click', () => {
      if (spec.action === 'reset') return ctx.reset();
      ctx.apply(ctx.run(slots.onClick, `${spec.id}.onClick`), `${spec.id}.onClick`);
    });
    return b;
  }

  function play(box, spec, slots, ctx) {
    const b = el('button', 'pg-btn pg-play');
    b.type = 'button';
    box.append(b);
    const label = spec.label || 'Play';
    let timer = null, step = 0;
    const show = () => { b.textContent = timer ? 'Pause' : label; b.classList.toggle('is-running', !!timer); };
    const stop = () => { clearInterval(timer); timer = null; show(); };
    const tick = () => {
      const patch = ctx.run(slots.onTick, `${spec.id}.onTick`, step);
      step++;
      if (patch === null || patch === undefined || step >= (spec.maxSteps || 500)) stop();
      if (patch) ctx.apply(patch, `${spec.id}.onTick`);
    };
    b.addEventListener('click', () => {
      if (timer) return stop();
      if (step >= (spec.maxSteps || 500)) step = 0;
      timer = setInterval(() => { try { tick(); } catch (e) { stop(); } }, spec.interval || 100);
      show();
    });
    ctx.registerPlayer({ stop, reset() { stop(); step = 0; } });
    show();
  }

  // ------------------------------------------------------------------ views
  // Added Nov 2 (rubric eval row 30): a readout named in bytes ("Cache (KB)") showed 20,480 KB. From 10,000 of a unit
  // up, the value moves up units (1024 each) while it is 1024 or more, and the label's unit follows: "20 MB", "Cache (MB)".
  const BYTE_UNITS = ['B', 'KB', 'MB', 'GB', 'TB'];
  const BYTE_LABEL = /\((B|bytes|KB|kB|MB|GB|TB)\)\s*$/;
  function byteUnits(label, r) {
    const m = String(label || '').match(BYTE_LABEL);
    if (!m || typeof r !== 'number' || !Number.isFinite(r) || Math.abs(r) < 10000) return null;
    let k = BYTE_UNITS.indexOf(m[1] === 'bytes' ? 'B' : m[1].toUpperCase()), val = r;
    while (Math.abs(val) >= 1024 && k < BYTE_UNITS.length - 1) { val /= 1024; k++; }
    return { value: Math.round(val * 10) / 10, label: String(label).replace(BYTE_LABEL, `(${BYTE_UNITS[k]})`) };
  }
  function readout(box, spec, slots, ctx) {
    const tile = el('div', 'pg-readout');
    const v = el('div', 'pg-readout-value');
    const lab = el('div', 'pg-readout-label', spec.label || '');
    tile.append(v, lab);
    box.append(tile);
    ctx.store.watch((st) => {
      const r = ctx.runWith(slots.value, `${spec.id}.value`, st);
      if (r === undefined) return;
      const u = byteUnits(spec.label, r);
      v.textContent = fmt(u ? u.value : r);
      lab.textContent = u ? u.label : spec.label || '';
    });
  }

  // Chart size, margins, axis rules and auto ranges are shared with the checker (structures.cjs chartFrame).
  const { W, H, STRUCT, AXISLESS, heatExtent, autoDomain, chartFrame } = root.structures;

  function niceTicks(lo, hi, n = 5) {
    const span = hi - lo;
    if (!(span > 0)) return [lo];
    const raw = span / n, mag = 10 ** Math.floor(Math.log10(raw));
    const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => span / s <= n) || 10 * mag;
    const out = [];
    for (let t = Math.ceil(lo / step) * step; t <= hi + step * 1e-9; t += step) out.push(Math.abs(t) < step * 1e-9 ? 0 : t);
    return out;
  }

  // Free-form diagram parts (shapes joined by links) are drawn in their own unclipped layer.
  const isDiagram = (m) => m.type === 'shape' || m.type === 'link';

  function colorAttr(c, prop) {
    if (typeof c === 'number') c = PALETTE[Math.abs(Math.round(c)) % PALETTE.length];
    if (typeof c === 'string' && PALETTE.includes(c)) return { cls: `c-${c}`, style: '' };
    if (typeof c === 'string' && /^#[0-9a-f]{3,8}$/i.test(c)) return { cls: '', style: `${prop}:${c};` };
    return { cls: 'c-blue', style: '' };
  }

  function chart(box, spec, slots, ctx) {
    const card = el('div', 'pg-chart');
    if (spec.title) card.append(el('div', 'pg-chart-title', spec.title));
    const svg = document.createElementNS(SVG_NS, 'svg');
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    svg.setAttribute('class', 'pg-svg');
    card.append(svg);
    box.append(card);
    const clipId = 'clip-' + spec.id.replace(/[^\w-]/g, '');
    const M = { ...root.structures.MARGIN }; // left margin widens for category labels
    let dom = { x: spec.x || [0, 1], y: spec.y || [0, 1] };
    let lastMarks = [];
    let textAt = new Map();  // text mark -> place (structures.placeLabels), set in render()

    const sx = (v) => M.l + (v - dom.x[0]) / (dom.x[1] - dom.x[0]) * (W - M.l - M.r);
    const sy = (v) => H - M.b - (v - dom.y[0]) / (dom.y[1] - dom.y[0]) * (H - M.t - M.b);
    const n = (v) => Math.round(v * 10) / 10;
    const tip = (s) => ` data-tip="${esc(s)}"`;
    const xy = (x, y) => `${spec.xLabel || 'x'} ${fmt(x)} · ${spec.yLabel || 'y'} ${fmt(y)}`;
    // Clickable elements of structural marks: onClick(state, pos, id, index, part)
    const hit = (m, index, part) => ` data-hit="${esc(`${m.id || ''}|${index}|${part}`)}"`;
    const sctx = {
      n, esc, fmt, colorAttr, tip, hit, sx, sy,
      arrowId: `ah-${clipId}`,
      // plot area in px, or m.box = [x0, y0, x1, y1] as fractions of it (from top-left)
      box(m) {
        const L = M.l, T = M.t, Rr = W - M.r, B = H - M.b;
        const f = Array.isArray(m && m.box) && m.box.length === 4 ? m.box : [0, 0, 1, 1];
        return { x0: L + f[0] * (Rr - L), y0: T + f[1] * (B - T), x1: L + f[2] * (Rr - L), y1: T + f[3] * (B - T) };
      },
    };

    // Added Nov 2 (rubric eval rows 1, 4): a line or points past the y range were cut at the edge with no sign, so a
    // diverging gradient-descent path or an overfit curve looked as if it just stopped. Like bars past the range: an
    // arrow in the line's colour where it leaves (one per stretch outside, at least 14 px apart, at most 8 per edge)
    // and, once per edge, the furthest value it reaches.
    // One series' bar values written alike (one number of decimals for all, a leading 0 dropped only if needed), each
    // at most `chars` long; null when they can't all fit (then none are shown). A non-zero never shows as 0.
    function barValues(vals, chars) {
      const ok = (ss) => ss.every((t, i) => t.length <= chars && !(/^-?0?\.?0*$/.test(t) && vals[i] !== 0));
      const raw = vals.map((v) => fmt(v));
      if (ok(raw)) return raw;
      const big = vals.some((v) => Math.abs(v) >= 1e4);
      for (const bare of [false, true]) {
        for (const d of [3, 2, 1, 0]) {
          const ss = vals.map((v) => {
            if (big) return fmtTick(v);
            const t = String(+Number(v).toFixed(d));
            return bare ? t.replace(/^(-?)0\./, '$1.') : t;
          });
          if (ok(ss)) return ss;
        }
      }
      return null;
    }

    function offEdge(m, col) {
      const yLo = Math.min(...dom.y), yHi = Math.max(...dom.y), tol = (yHi - yLo) * 0.005;
      const side = (v) => (v > yHi + tol ? 1 : v < yLo - tol ? -1 : 0);
      const pts = (m.points || []).filter((p) => Array.isArray(p) && Number.isFinite(p[0]) && Number.isFinite(p[1]));
      if (m.type === 'line' && pts.length <= 2) return;  // a straight guide (tangent, fitted line) may run past the range
      const runs = [];
      let cur = null;
      pts.forEach((p, i) => {
        const s = side(p[1]);
        // dots aren't joined: each one outside is its own stretch
        if (cur && s === cur.s && m.type === 'line') { if (s * p[1] > s * cur.peak) cur.peak = p[1]; return; }
        cur = null;
        if (!s) return;
        let x = sx(p[0]);
        const q = pts[i - 1];  // a line leaves between its last point inside and this one
        if (m.type === 'line' && q && !side(q[1]) && p[1] !== q[1]) x = sx(q[0] + (p[0] - q[0]) * ((s > 0 ? yHi : yLo) - q[1]) / (p[1] - q[1]));
        cur = { s, x, peak: p[1] };
        runs.push(cur);
      });
      if (!runs.length) return;
      const pc = col.cls ? `var(--${col.cls})` : (col.style.match(/#[0-9a-f]{3,8}/i) || ['var(--text)'])[0];
      for (const s of [1, -1]) {
        const shown = [];
        const near = (r, px, label) => edgeSeen.some((o) => o.s === s && !!o.label === label && Math.abs(o.x - r.x) < px);
        for (const r of runs) {
          if (r.s !== s || r.x < M.l - 1 || r.x > W - M.r + 1 || shown.length >= 8 || shown.some((o) => Math.abs(o.x - r.x) < 14)) continue;
          shown.push(r);
        }
        if (!shown.length) continue;
        const ey = s > 0 ? M.t : H - M.b, d = s > 0 ? 1 : -1;
        for (const r of shown) {
          if (near(r, 24, false)) continue;  // a line and its own dots leave at (about) the same spot
          edgeSeen.push({ s, x: r.x });
          overlay.push(`<path class="mk-clip" style="stroke:${pc}" d="M${n(r.x - 5)},${n(ey + 7 * d)} L${n(r.x)},${n(ey + d)} L${n(r.x + 5)},${n(ey + 7 * d)}"/>`);
        }
        const far = shown.reduce((a, b) => (s * b.peak > s * a.peak ? b : a));
        const tw = String(fmt(far.peak)).length * 7;
        const left = M.l + 24;
        const lx = Math.min(W - M.r - tw / 2 - 2, Math.max(left, far.x));
        if (near({ x: lx }, 50, true)) continue;
        edgeSeen.push({ s, x: lx, label: true });
        overlay.push(`<text class="mk-cliplabel" x="${n(lx)}" y="${n(s > 0 ? ey + 19 : ey - 11)}" text-anchor="middle">${esc(fmt(far.peak))}</text>`);
      }
    }

    function renderMark(m) {
      // `colors: 'blue'` (a single string) is treated as `color: 'blue'`
      const col = colorAttr(m.color !== undefined ? m.color : (typeof m.colors === 'string' ? m.colors : undefined),m.type === 'text' ? 'fill' : (m.type === 'bars' || m.type === 'rect' || m.type === 'area' || m.type === 'points' || m.type === 'point') ? 'fill' : 'stroke');
      const op = m.opacity !== undefined ? ` opacity="${Number(m.opacity)}"` : '';
      const dash = m.dashed ? ' stroke-dasharray="5 4"' : '';
      const sw = m.width !== undefined && m.type !== 'bars' ? ` stroke-width="${Number(m.width)}"` : '';
      const st = col.style ? ` style="${col.style}"` : '';
      switch (m.type) {
        case 'graph': return root.structures.graph(m, sctx);
        case 'tree': return root.structures.tree(m, sctx);
        case 'grid': return root.structures.grid(m, sctx);
        case 'shape': return root.structures.shape(m, sctx);
        case 'link': return root.structures.link(m, diagramGeoms, sctx);
        case 'line': {
          offEdge(m, col);
          const under = underLines.has(m) ? ' stroke-width="7" stroke-opacity="0.4"' : sw;
          // a line drawn to the edge of the chart's range (an axis or guide line) reaches the edge of a range widened
          // for equal scales (structures.equalAspect), instead of stopping short
          const toEdge = (v, axis) => {
            const r = spec[axis], d = dom[axis];
            if (!equalScales || !Array.isArray(r)) return v;
            return v === r[0] ? d[0] : v === r[1] ? d[1] : v;
          };
          return `<polyline class="mk-line ${col.cls}"${st}${op}${dash}${under} points="${m.points.map((p) => `${n(sx(toEdge(p[0], 'x')))},${n(sy(toEdge(p[1], 'y')))}`).join(' ')}"/>`;
        }
        case 'area': {
          const y0 = sy(Math.min(Math.max(0, dom.y[0]), dom.y[1]));
          const pts = m.points.map((p) => `${n(sx(p[0]))},${n(sy(p[1]))}`);
          if (!pts.length) return '';
          return `<polygon class="mk-area ${col.cls}"${st}${op} points="${n(sx(m.points[0][0]))},${n(y0)} ${pts.join(' ')} ${n(sx(m.points[m.points.length - 1][0]))},${n(y0)}"/>`;
        }
        case 'points':
          offEdge(m, col);
          return m.points.map((p, i) => {
            const c = Array.isArray(m.colors) ? colorAttr(m.colors[i], 'fill') : col;
            return `<circle class="mk-pt ${c.cls}${m.draggable ? ' is-drag' : ''}"${c.style ? ` style="${c.style}"` : ''}${op} cx="${n(sx(p[0]))}" cy="${n(sy(p[1]))}" r="${m.r || 4.5}"${m.draggable ? ` data-drag="${esc(m.id || '')}" data-index="${i}"` : ''}${tip(xy(p[0], p[1]))}/>`;
          }).join('');
        case 'point':
          return `<circle class="mk-pt mk-big ${col.cls}${m.draggable ? ' is-drag' : ''}"${st}${op} cx="${n(sx(m.x))}" cy="${n(sy(m.y))}" r="${m.r || 7}"${m.draggable ? ` data-drag="${esc(m.id || '')}" data-index="0"` : ''}${tip(xy(m.x, m.y))}/>`;
        case 'bars': {
          const lay = barLayout.get(m) || { w: 0.8, dx: 0 };
          const w = m.width || lay.w;
          const base = Math.min(Math.max(0, dom.y[0]), dom.y[1]);
          const [yLo, yHi] = [Math.min(...dom.y), Math.max(...dom.y)];
          // Added Nov 2 (rubric eval rows 10, 29): few bars and no text marks of the chart's own -> each bar shows its
          // value (showValues: false turns it off, true turns it on regardless). Characters that fit the bar's slot:
          // a lone series may use the gap between bars too.
          const slotPx = Math.abs(sx(w) - sx(0)) / (barLayout.size > 1 || w !== lay.w ? 1 : 0.8);
          const valueChars = (m.showValues === true || (m.showValues !== false && autoBarValues)) ? Math.floor((slotPx - 4) / 6.6) : 0;
          const valueText = valueChars >= 3 ? barValues(m.items.map((p) => p[1]), valueChars) : null;
          // Many bars: label every k-th one so labels never run together (30 step labels read "0123456789101112…").
          let labelStep = 1;
          if (m.labels && m.items.length > 1) {
            const xs = m.items.map((p) => p[0]).sort((a, b) => a - b);
            let gap = Infinity;
            for (let i = 1; i < xs.length; i++) if (xs[i] > xs[i - 1]) gap = Math.min(gap, xs[i] - xs[i - 1]);
            const px = Number.isFinite(gap) ? Math.abs(sx(gap) - sx(0)) : (W - M.l - M.r) / m.items.length;
            const widest = Math.max(1, ...m.labels.slice(0, m.items.length).map((l) => String(l ?? '').length));
            labelStep = Math.max(1, Math.ceil((widest * 6.5 + 6) / Math.max(px, 1)));
          }
          return m.items.map((p, i) => {
            const cx = p[0] + lay.dx;
            const x1 = sx(cx - w / 2), x2 = sx(cx + w / 2), ya = sy(base), yb = sy(p[1]);
            const c = Array.isArray(m.colors) ? colorAttr(m.colors[i], 'fill') : col;
            const name = m.labels && m.labels[i] !== undefined ? m.labels[i] : fmt(p[0]);
            // labels sit under the group centre (unshifted), outside the clip
            if (m.labels && m.labels[i] !== undefined && i % labelStep === 0) overlay.push(`<text class="mk-barlabel" x="${n(sx(p[0]))}" y="${H - M.b + 18}" text-anchor="middle">${esc(m.labels[i])}</text>`);
            // its value just past the bar's end (inside it when there is no room), shortened to the bar's width
            if (valueText && p[1] !== 0 && p[1] <= yHi && p[1] >= yLo) {
              const t = valueText[i];
              const out = p[1] >= base ? yb - 16 < M.t : yb + 16 > H - M.b;
              const ty = (p[1] >= base) !== out ? yb - 4 : yb + 13;
              overlay.push(`<text class="mk-barval${out ? ' is-in' : ''}" x="${n((x1 + x2) / 2)}" y="${n(ty)}" text-anchor="middle">${esc(t)}</text>`);
            }
            // bar past the fixed range: arrow + true value at the edge
            if (p[1] > yHi || p[1] < yLo) {
              const up = p[1] > yHi, ey = up ? M.t : H - M.b, mx = (x1 + x2) / 2;
              overlay.push(`<path class="mk-clip" d="M${n(mx - 5)},${n(ey + (up ? 7 : -7))} L${n(mx)},${n(ey + (up ? 1 : -1))} L${n(mx + 5)},${n(ey + (up ? 7 : -7))}"/>` +
                `<text class="mk-cliplabel" x="${n(mx)}" y="${n(ey + (up ? 19 : -11))}" text-anchor="middle">${esc(fmt(p[1]))}</text>`);
            }
            // Added Nov 2 (rubric eval row 28): a bar of 0 (or under 2 px) drew nothing, so a series still at 0 ("Q"
            // before any pull) looked missing. It is drawn at least 2 px tall, on its side of the baseline.
            let ry = Math.min(ya, yb), rh = Math.abs(yb - ya);
            if (rh < 2 && p[1] <= yHi && p[1] >= yLo) { rh = 2; ry = p[1] > base ? ya - 2 : p[1] < base ? ya : ya - 1; }
            return `<rect class="mk-bar ${c.cls}"${c.style ? ` style="${c.style}"` : ''}${op} x="${n(Math.min(x1, x2))}" y="${n(ry)}" width="${n(Math.abs(x2 - x1))}" height="${n(rh)}"${tip(`${m.label ? m.label + ' · ' : ''}${name}: ${fmt(p[1])}`)}/>`;
          }).join('');
        }
        case 'hline':
          return `<line class="mk-rule ${col.cls}"${st}${op}${dash}${sw} x1="${M.l}" x2="${W - M.r}" y1="${n(sy(m.y))}" y2="${n(sy(m.y))}"/>`;
        case 'vline':
          return `<line class="mk-rule ${col.cls}"${st}${op}${dash}${sw} y1="${M.t}" y2="${H - M.b}" x1="${n(sx(m.x))}" x2="${n(sx(m.x))}"/>`;
        case 'rect':
          return `<rect class="mk-rect ${col.cls}"${st}${m.opacity === undefined ? ' opacity="0.18"' : op} x="${n(Math.min(sx(m.x1), sx(m.x2)))}" y="${n(Math.min(sy(m.y1), sy(m.y2)))}" width="${n(Math.abs(sx(m.x2) - sx(m.x1)))}" height="${n(Math.abs(sy(m.y2) - sy(m.y1)))}"/>`;
        case 'arrow': {
          // Added Nov 2 (rubric eval row 7): an arrow ending on a dot (a draggable handle at its tip) had its head hidden
          // under the dot; it now stops at the dot's edge.
          const x1 = sx(m.x1), y1 = sy(m.y1);
          let x2 = sx(m.x2), y2 = sy(m.y2);
          const len = Math.hypot(x2 - x1, y2 - y1), d = dotsAt.find((o) => Math.hypot(o.x - x2, o.y - y2) < o.r && len > o.r + 8);
          if (d) { const k = (len - d.r - 2) / len; x2 = x1 + (x2 - x1) * k; y2 = y1 + (y2 - y1) * k; }
          return `<line class="mk-arrow ${col.cls}"${st}${op}${sw} marker-end="url(#ah-${clipId})" x1="${n(x1)}" y1="${n(y1)}" x2="${n(x2)}" y2="${n(y2)}"/>`;
        }
        case 'text': {
          // kept inside the plot and off lines (structures.placeLabels, also what the checker measures)
          const t = textAt.get(m) || root.structures.placeText(m, { sx, sy, plot: sctx.box(null) });
          return `<text class="mk-text ${m.color === undefined ? '' : col.cls}"${st}${op} x="${n(t.x)}" y="${n(t.y)}" text-anchor="${t.anchor}" dy="-0.4em">${esc(m.text)}</text>`;
        }
        case 'heatmap': {
          // Color intensity = opacity of one palette color (works in both themes).
          // diverging: negatives use the second color, centred on 0.
          const v = m.values, rows = v.length, cols = v[0].length;
          const [x0, x1] = heatExtent(m, 'x'), [y0, y1] = heatExtent(m, 'y');
          const cw = (x1 - x0) / cols, ch = (y1 - y0) / rows;
          const dense = Math.min(Math.abs(sx(x0 + cw) - sx(x0)), Math.abs(sy(y0 + ch) - sy(y0))) < 16;
          const flat = v.flat();
          const lo = m.min !== undefined ? m.min : Math.min(...flat), hi = m.max !== undefined ? m.max : Math.max(...flat);
          const absMax = Math.max(Math.abs(lo), Math.abs(hi)) || 1;
          const pos = colorAttr(m.color === undefined ? 'blue' : m.color, 'fill');
          const neg = colorAttr(m.negColor === undefined ? 'red' : m.negColor, 'fill');
          // Added Nov 4: cell values by default (Haiku rarely set showValues, so cells were colour only). Unset ->
          // written when the grid is small (<= 100 cells), the cells are big enough, the chart has no text marks of
          // its own (they would double up) and every value fits its cell in one style (barValues). true forces, false hides.
          const cellW = Math.abs(sx(x0 + cw) - sx(x0)), cellH = Math.abs(sy(y0 + ch) - sy(y0));
          let shown = null;  // per-cell value text, in the auto case
          if (m.showValues === undefined && !dense && rows * cols <= 100 && !chartHasText && cellH >= 14) {
            const chars = Math.floor((cellW - 4) / 6.6);
            shown = chars >= 1 ? barValues(flat, chars) : null;
          }
          const show = m.showValues === true || !!shown;
          const valText = (r, c) => (shown ? shown[r * cols + c] : fmt(v[r][c]));
          let out = '';
          for (let r = 0; r < rows; r++) {
            for (let c = 0; c < cols; c++) {
              const val = v[r][c];
              const t = m.diverging ? Math.abs(val) / absMax : (hi > lo ? (val - lo) / (hi - lo) : 1);
              const cc = m.diverging && val < 0 ? neg : pos;
              const ax = sx(x0 + c * cw), bx = sx(x0 + (c + 1) * cw) + (dense ? 0.6 : 0), ay = sy(y1 - r * ch), by = sy(y1 - (r + 1) * ch) + (dense ? 0.6 : 0);
              // small cells: no borders (they would hide the colors); slight overlap avoids seams
              out += `<rect class="mk-cell${dense ? ' is-dense' : ''} ${cc.cls}"${cc.style ? ` style="${cc.style}"` : ''} fill-opacity="${n(0.06 + 0.94 * Math.min(1, Math.max(0, t)))}" x="${n(Math.min(ax, bx))}" y="${n(Math.min(ay, by))}" width="${n(Math.abs(bx - ax))}" height="${n(Math.abs(by - ay))}"${tip(`${m.yLabels ? m.yLabels[r] : `row ${r + 1}`} · ${m.xLabels ? m.xLabels[c] : `col ${c + 1}`}: ${fmt(val)}`)}/>`;
              // Added Nov 2 (rubric eval row 3): cellLabels names each cell (TP / FN / FP / TN), drawn above its value
              const nm = Array.isArray(m.cellLabels) && Array.isArray(m.cellLabels[r]) ? m.cellLabels[r][c] : null;
              const hasName = nm !== null && nm !== undefined && String(nm) !== '';
              const cx = (ax + bx) / 2, cy = (ay + by) / 2, two = hasName && show && Math.abs(by - ay) >= 34;
              const strong = t > 0.55 ? ' is-strong' : '';
              if (hasName) {
                const room = Math.max(2, Math.floor((Math.abs(bx - ax) - 6) / 7));
                const label = two || !show ? String(nm) : `${nm} ${valText(r, c)}`;
                out += `<text class="mk-cellval mk-cellname${strong}" x="${n(cx)}" y="${n(two ? cy - 9 : cy)}" text-anchor="middle" dy="0.35em">${esc(root.structures.cut(label, room))}</text>`;
              }
              if (show && (!hasName || two)) {
                out += `<text class="mk-cellval${strong}" x="${n(cx)}" y="${n(two ? cy + 9 : cy)}" text-anchor="middle" dy="0.35em">${esc(valText(r, c))}</text>`;
              }
            }
          }
          return out;
        }
        case 'pie': {
          const total = m.values.reduce((a, b) => a + b, 0);
          if (!(total > 0)) return '';
          const axesOff = !hasAxes;
          const cx = axesOff ? W / 2 : (M.l + W - M.r) / 2, cy = axesOff ? H / 2 : (M.t + H - M.b) / 2;
          const R = (axesOff ? H : Math.min(W - M.l - M.r, H - M.t - M.b)) / 2 - 8;
          const r0 = R * (m.donut || 0);
          const at = (a, rad) => [cx + rad * Math.cos(a), cy + rad * Math.sin(a)];
          let a = -Math.PI / 2, out = '';
          m.values.forEach((val, i) => {
            if (!(val > 0)) return;
            const frac = val / total, b = a + frac * 2 * Math.PI;
            const c = colorAttr(Array.isArray(m.colors) ? m.colors[i] : (m.color !== undefined && m.values.length === 1 ? m.color : i), 'fill');
            const cs = (c.style ? ` style="${c.style}"` : '') +
              tip(`${m.labels && m.labels[i] !== undefined ? m.labels[i] : `slice ${i + 1}`}: ${fmt(val)} (${Math.round(frac * 100)}%)`);
            if (frac > 0.9999) {
              out += `<circle class="mk-slice ${c.cls}"${cs}${op} cx="${n(cx)}" cy="${n(cy)}" r="${n(R)}"/>`;
              if (r0) out += `<circle class="mk-hole" cx="${n(cx)}" cy="${n(cy)}" r="${n(r0)}"/>`;
            } else {
              const big = frac > 0.5 ? 1 : 0;
              const [p1x, p1y] = at(a, R), [p2x, p2y] = at(b, R), [q1x, q1y] = at(b, r0), [q2x, q2y] = at(a, r0);
              const d = r0
                ? `M${n(p1x)},${n(p1y)} A${n(R)},${n(R)} 0 ${big} 1 ${n(p2x)},${n(p2y)} L${n(q1x)},${n(q1y)} A${n(r0)},${n(r0)} 0 ${big} 0 ${n(q2x)},${n(q2y)} Z`
                : `M${n(cx)},${n(cy)} L${n(p1x)},${n(p1y)} A${n(R)},${n(R)} 0 ${big} 1 ${n(p2x)},${n(p2y)} Z`;
              out += `<path class="mk-slice ${c.cls}"${cs}${op} d="${d}"/>`;
            }
            if (frac >= 0.04) {
              const [lx, ly] = at((a + b) / 2, r0 ? (R + r0) / 2 : R * 0.62);
              const lab = m.labels && m.labels[i] !== undefined ? `${esc(m.labels[i])} ` : '';
              out += `<text class="mk-slicelabel" x="${n(lx)}" y="${n(ly)}" text-anchor="middle" dy="0.35em">${lab}${Math.round(frac * 100)}%</text>`;
            }
            a = b;
          });
          return out;
        }
        default:
          return '';
      }
    }

    let hasAxes = true;
    let diagramGeoms = new Map();  // shape id -> where it is on screen, for links
    let overlay = [];
    let edgeSeen = [];  // off-edge arrows and labels drawn this render (offEdge), shared by all marks
    let barLayout = new Map();
    let autoBarValues = false;  // set per render: few bars, no text marks
    let chartHasText = false;   // set per render: the chart draws text marks of its own
    let dotsAt = [];            // set per render: every dot's centre and radius in px (an arrow ending on one stops at its edge)
    let equalScales = false;    // set per render: the range was widened for equal x and y scales
    let underLines = new Set(); // set per render: lines another line lies exactly on top of (drawn wide and faint beneath it)

    // All bars marks in a chart are laid out together: width comes from the
    // closest x spacing across every series, and series sharing x positions
    // are placed side by side (grouped) instead of on top of each other.
    function layoutBars(marks) {
      barLayout = new Map();
      const bms = marks.filter((m) => m.type === 'bars');
      if (!bms.length) return;
      const xs = [...new Set(bms.flatMap((m) => m.items.map((p) => p[0])))].sort((a, b) => a - b);
      let gap = Infinity;
      for (let i = 1; i < xs.length; i++) gap = Math.min(gap, xs[i] - xs[i - 1]);
      if (!Number.isFinite(gap) || gap <= 0) gap = 1;
      const shared = bms.length > 1 && bms.some((m, j) => bms.some((o, k) => k !== j && m.items.some((p) => o.items.some((q) => q[0] === p[0]))));
      if (shared) {
        const w = gap * 0.8 / bms.length;
        bms.forEach((m, j) => barLayout.set(m, { w, dx: (j - (bms.length - 1) / 2) * w }));
      } else {
        bms.forEach((m) => barLayout.set(m, { w: gap * 0.8, dx: 0 }));
      }
    }
    // the series legend (structures.seriesLegend): a swatch drawn like the series, then its name
    function legendSvg(L) {
      let out = `<g class="mk-legendbox"><rect class="mk-legend" x="${n(L.x)}" y="${n(L.y)}" width="${n(L.w)}" height="${n(L.h)}" rx="6"/>`;
      L.items.forEach((it, k) => {
        const cy = L.y + 4 + (k + 0.5) * L.rowH, x = L.x + 7;
        const isLine = it.m.type === 'line';
        const c = colorAttr(it.color, isLine ? 'stroke' : 'fill'), st = c.style ? ` style="${c.style}"` : '';
        out += isLine
          ? `<line class="mk-line ${c.cls}"${st}${it.m.dashed ? ' stroke-dasharray="4 3"' : ''} x1="${n(x)}" x2="${n(x + 14)}" y1="${n(cy)}" y2="${n(cy)}"/>`
          : `<rect class="${c.cls}"${st} x="${n(x + 2)}" y="${n(cy - 5)}" width="10" height="10" rx="${it.m.type === 'points' ? 5 : 2}"/>`;
        out += `<text class="mk-legendtext" x="${n(x + 20)}" y="${n(cy)}" dy="0.35em">${esc(it.label)}</text>`;
      });
      return out + '</g>';
    }

    // Added Nov 2 (rubric eval row 26): a line drawn exactly on top of another ("your line" after "Set to best fit")
    // hid it. The one underneath is drawn wide and faint, so both colours show. Lines of 2-300 points and 20+ px, every
    // vertex of each within 2 px of the other.
    function coveredLines(marks) {
      const out = new Set();
      const px = (m) => m.points.map((p) => [sx(p[0]), sy(p[1])]);
      const long = (p) => p.reduce((t, a, i) => t + (i ? Math.hypot(a[0] - p[i - 1][0], a[1] - p[i - 1][1]) : 0), 0) >= 20;  // not a residual stub
      const lines = marks.filter((m) => m.type === 'line' && Array.isArray(m.points) && m.points.length >= 2 && m.points.length <= 300)
        .map((m) => ({ m, p: px(m) })).filter((l) => long(l.p));
      const near = (q, poly) => poly.some((a, i) => i > 0 && segDist(q, poly[i - 1], a) < 2);
      for (let i = 0; i < lines.length; i++) {
        for (let j = i + 1; j < lines.length; j++) {
          const A = lines[i], B = lines[j];
          if (B.p.every((q) => near(q, A.p)) && A.p.every((q) => near(q, B.p))) out.add(A.m);
        }
      }
      return out;
    }
    function segDist([x, y], [x1, y1], [x2, y2]) {
      const dx = x2 - x1, dy = y2 - y1, L = dx * dx + dy * dy;
      const t = L ? Math.max(0, Math.min(1, ((x - x1) * dx + (y - y1) * dy) / L)) : 0;
      return Math.hypot(x - x1 - t * dx, y - y1 - t * dy);
    }
    // Added Nov 2 (rubric eval rows 15, 23): dots set out in rows at 1-3 fixed heights (a 1-D strip, rows named by text)
    // drew y numbers that mean nothing. With no y-axis name, no line/area/bars/rect/heatmap, 4+ dots and every height
    // used by 2+ dots, the y ticks and grid are left out.
    function isStrip(marks) {
      if (spec.yLabel || marks.some((m) => ['line', 'area', 'bars', 'rect', 'heatmap', 'arrow'].includes(m.type))) return false;
      const ys = [];
      for (const m of marks) {
        if (m.type === 'point') ys.push(m.y);
        if (m.type === 'points' && Array.isArray(m.points)) for (const p of m.points) ys.push(p[1]);
      }
      const count = new Map();
      for (const y of ys) count.set(y, (count.get(y) || 0) + 1);
      return ys.length >= 4 && count.size <= 3 && [...count.values()].every((c) => c >= 2);
    }

    function render(marks) {
      overlay = [];
      edgeSeen = [];
      layoutBars(marks);
      chartHasText = marks.some((m) => m.type === 'text' && String(m.text == null ? '' : m.text).trim());
      autoBarValues = marks.filter((m) => m.type === 'bars').reduce((t, m) => t + (m.items || []).length, 0) <= 12 && !chartHasText;
      const frame = chartFrame(spec, marks);  // the same frame the checker measures layout with
      dom = frame.dom;
      hasAxes = frame.hasAxes;
      Object.assign(M, frame.M);
      textAt = root.structures.placeLabels(marks, { sx, sy, plot: sctx.box(null) });
      dotsAt = [];
      for (const m of marks) {
        if (m.type === 'point') dotsAt.push({ x: sx(m.x), y: sy(m.y), r: Number(m.r) || 7 });
        if (m.type === 'points' && Array.isArray(m.points)) for (const p of m.points) dotsAt.push({ x: sx(p[0]), y: sy(p[1]), r: Number(m.r) || 4.5 });
      }
      underLines = coveredLines(marks);
      equalScales = root.structures.equalAspect(spec, marks);
      const stripY = isStrip(marks);
      const hasBarLabels = marks.some((m) => m.type === 'bars' && m.labels);
      const hm = marks.find((m) => m.type === 'heatmap' && (m.xLabels || m.yLabels));
      const xt = niceTicks(dom.x[0], dom.x[1]), yt = niceTicks(dom.y[0], dom.y[1]);
      // Dots get their own clip, grown by their radius: a dot centred on the plot's edge (the first step at x = 0, a
      // probability of 1) is drawn whole instead of cut in half (Nov 1: 24 of 206 playgrounds). Dots further out stay hidden.
      const isDot = (m) => m.type === 'point' || m.type === 'points';
      const dotPad = Math.min(14, Math.max(0, ...marks.filter(isDot).map((m) => Number(m.r) || (m.type === 'point' ? 7 : 4.5))) + 1);
      let g = `<defs><clipPath id="${clipId}"><rect x="${M.l}" y="${M.t}" width="${W - M.l - M.r}" height="${H - M.t - M.b}"/></clipPath>` +
        `<clipPath id="${clipId}-dot"><rect x="${M.l - dotPad}" y="${M.t - dotPad}" width="${W - M.l - M.r + 2 * dotPad}" height="${H - M.t - M.b + 2 * dotPad}"/></clipPath>` +
        `<marker id="ah-${clipId}" viewBox="0 0 10 10" refX="9" refY="5" markerUnits="userSpaceOnUse" markerWidth="11" markerHeight="11" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="mk-head"/></marker></defs>`;
      if (hasAxes) {
        // Heatmap category labels replace numeric ticks on that axis.
        const cat = (axis) => {
          if (!hm || !hm[axis === 'x' ? 'xLabels' : 'yLabels']) return null;
          const labs = hm[axis === 'x' ? 'xLabels' : 'yLabels'], [a, b] = heatExtent(hm, axis), k = labs.length, step = (b - a) / k;
          // y labels follow row order: row 0 at the top
          return labs.map((l, i) => [axis === 'x' ? a + (i + 0.5) * step : b - (i + 0.5) * step, l]);
        };
        const yc = cat('y'), xc = cat('x');
        if (stripY) { /* rows of dots at a few fixed heights: the y numbers mean nothing, the rows are named by text */ }
        else g += yc
          ? yc.map(([v, l]) => `<text class="ax-tick" x="${M.l - 6}" y="${n(sy(v))}" text-anchor="end" dy="0.32em">${esc(l)}</text>`).join('')
          : yt.map((t) => `<line class="ax-grid" x1="${M.l}" x2="${W - M.r}" y1="${n(sy(t))}" y2="${n(sy(t))}"/><text class="ax-tick" x="${M.l - 6}" y="${n(sy(t))}" text-anchor="end" dy="0.32em">${fmtTick(t)}</text>`).join('');
        if (xc) g += xc.map(([v, l]) => `<text class="ax-tick" x="${n(sx(v))}" y="${H - M.b + 18}" text-anchor="middle">${esc(l)}</text>`).join('');
        else if (!hasBarLabels) g += xt.map((t) => `<text class="ax-tick" x="${n(sx(t))}" y="${H - M.b + 18}" text-anchor="middle">${fmtTick(t)}</text>`).join('');
        g += `<line class="ax-line" x1="${M.l}" x2="${W - M.r}" y1="${H - M.b}" y2="${H - M.b}"/>`;
        const names = root.structures.axisNames(spec, marks);
        if (names.x) g += `<text class="ax-label" x="${W - M.r}" y="${H - 2}" text-anchor="end">${esc(names.x)}</text>`;
        // above the plot (chartFrame left room for it), starting at the y axis: nothing plotted can sit on it
        if (names.y) g += `<text class="ax-label" x="${M.l}" y="${M.t - 8}">${esc(names.y)}</text>`;
      }
      const isStruct = (m) => STRUCT.has(m.type) && m.type !== 'grid';
      diagramGeoms = new Map(marks.filter((m) => m.type === 'shape' && m.id !== undefined).map((m) => [m.id, root.structures.shapeGeom(m, sctx)]));
      const inBody = (m) => m.type !== 'text' && m.type !== 'pie' && !m.draggable && !isStruct(m) && !isDiagram(m);
      const body = marks.filter((m) => inBody(m) && !isDot(m)).map(renderMark).join('');
      const dots = marks.filter((m) => inBody(m) && isDot(m)).map(renderMark).join('');
      // links first, then the shapes on top of their ends
      const diagram = marks.filter((m) => m.type === 'link').map(renderMark).join('') +
        marks.filter((m) => m.type === 'shape' && !m.draggable).map(renderMark).join('');
      // structural marks: unclipped (labels may extend); matrices and sequences lay out as groups
      let struct = marks.filter((m) => isStruct(m) && !m.draggable && (m.type === 'graph' || m.type === 'tree')).map(renderMark).join('');
      const mats = marks.filter((m) => m.type === 'matrix'), seqs = marks.filter((m) => m.type === 'sequence');
      if (mats.length) struct += root.structures.matrices(mats, sctx, sctx.box(mats[0]));
      if (seqs.length) struct += root.structures.sequences(seqs, sctx, sctx.box(seqs[0]));
      const top = marks.filter((m) => m.type === 'pie' || m.type === 'text' || m.draggable).map(renderMark).join('');
      const legend = root.structures.seriesLegend(marks, { sx, sy, dom, plot: sctx.box(null) });
      g += `<g clip-path="url(#${clipId})">${body}</g><g clip-path="url(#${clipId}-dot)">${dots}</g>${struct}${diagram}${overlay.join('')}${legend ? legendSvg(legend) : ''}${top}`;
      svg.innerHTML = g;
    }

    ctx.store.watch((st) => {
      const marks = ctx.runWith(slots.draw, `${spec.id}.draw`, st);
      if (marks) { lastMarks = marks; render(marks); }
    });

    // ---- pointer: drag draggable marks, or click empty area
    const interactive = slots.onDrag || slots.onClick;
    if (interactive) svg.classList.add('is-interactive');
    let drag = null;
    const toData = (ev) => {
      const pt = svg.createSVGPoint();
      pt.x = ev.clientX; pt.y = ev.clientY;
      const p = pt.matrixTransform(svg.getScreenCTM().inverse());
      const x = dom.x[0] + (p.x - M.l) / (W - M.l - M.r) * (dom.x[1] - dom.x[0]);
      const y = dom.y[0] + (H - M.b - p.y) / (H - M.t - M.b) * (dom.y[1] - dom.y[0]);
      const c = (v, d) => Math.min(Math.max(v, Math.min(d[0], d[1])), Math.max(d[0], d[1]));
      const pos = { x: c(x, dom.x), y: c(y, dom.y) };
      // On a grid chart, also report which cell the pointer is over.
      const gm = lastMarks.find((m) => m.type === 'grid');
      return gm ? Object.assign(pos, root.structures.cellAt(gm, pos.x, pos.y)) : pos;
    };
    svg.addEventListener('pointerdown', (ev) => {
      const t = ev.target.closest && ev.target.closest('[data-drag]');
      if (t && slots.onDrag) {
        drag = { id: t.getAttribute('data-drag'), index: Number(t.getAttribute('data-index')) };
        svg.setPointerCapture(ev.pointerId);
        svg.classList.add('is-dragging');
        ev.preventDefault();
      } else if (slots.onClick) {
        // element of a structural mark -> (id, index, part); empty area -> pos only
        const h = ev.target.closest && ev.target.closest('[data-hit]');
        const args = [toData(ev)];
        if (h) { const [id, index, part] = h.getAttribute('data-hit').split('|'); args.push(id, Number(index), part); }
        ctx.apply(ctx.run(slots.onClick, `${spec.id}.onClick`, ...args), `${spec.id}.onClick`);
      }
    });
    svg.addEventListener('pointermove', (ev) => {
      if (!drag) return hover(ev);
      const pos = toData(ev);
      ctx.apply(ctx.run(slots.onDrag, `${spec.id}.onDrag`, pos, drag.id, drag.index), `${spec.id}.onDrag`);
      showTip(ev, xy(pos.x, pos.y));
    });
    const end = () => { drag = null; svg.classList.remove('is-dragging'); };
    svg.addEventListener('pointerup', end);
    svg.addEventListener('pointercancel', end);

    // ---- hover values: marks carry data-tip; lines snap to their nearest point
    const tipEl = el('div', 'pg-tip');
    tipEl.hidden = true;
    card.append(tipEl);
    function showTip(ev, text) {
      tipEl.textContent = text;
      tipEl.hidden = false;
      const r = card.getBoundingClientRect();
      const x = Math.min(ev.clientX - r.left + 12, r.width - tipEl.offsetWidth - 4);
      tipEl.style.left = `${Math.max(4, x)}px`;
      tipEl.style.top = `${Math.max(4, ev.clientY - r.top - 30)}px`;
    }
    function hideTip() {
      tipEl.hidden = true;
      const d = svg.querySelector('.hover-dot');
      if (d) d.remove();
    }
    function hover(ev) {
      const t = ev.target.closest && ev.target.closest('[data-tip]');
      const old = svg.querySelector('.hover-dot');
      if (old) old.remove();
      if (t) return showTip(ev, t.getAttribute('data-tip'));
      // nearest point on a line/area within 16 px (in SVG units)
      const pt = svg.createSVGPoint();
      pt.x = ev.clientX; pt.y = ev.clientY;
      const p = pt.matrixTransform(svg.getScreenCTM().inverse());
      let best = null, bd = 16 * 16;
      for (const m of lastMarks) {
        if (m.type !== 'line' && m.type !== 'area') continue;
        for (const q of m.points) {
          const dx = sx(q[0]) - p.x, dy = sy(q[1]) - p.y, d = dx * dx + dy * dy;
          if (d < bd) { bd = d; best = q; }
        }
      }
      if (!best) return hideTip();
      const dot = document.createElementNS(SVG_NS, 'circle');
      dot.setAttribute('class', 'hover-dot');
      dot.setAttribute('cx', n(sx(best[0])));
      dot.setAttribute('cy', n(sy(best[1])));
      dot.setAttribute('r', 4);
      svg.append(dot);
      showTip(ev, xy(best[0], best[1]));
    }
    svg.addEventListener('pointerleave', () => { if (!drag) hideTip(); });
  }

  const components = { slider, select, toggle, button, play, readout, chart };
  root.components = components;
  root.componentUtils = { niceTicks, autoDomain, fmt };
})(window);

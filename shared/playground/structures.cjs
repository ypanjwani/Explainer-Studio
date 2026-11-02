// Copied from the ai-playground experiment (Bloging-substack/ECommerce/ai-playground), adapted for Explainer Studio's Playground mode.
// Layout + SVG for structural marks: graph, tree, matrix, sequence, grid.
// Snippets pass structure only; every position is computed here.
// ctx = { n, esc, fmt, colorAttr, tip(text), hit(mark, index, part), sx, sy, box(mark), arrowId }
(function (root) {
  const PALETTE = ['blue', 'orange', 'green', 'red', 'purple', 'teal', 'gray', 'pink'];
  // grid categories: 0 empty, 1 wall, 2 goal, 3 hazard, then the palette
  const GRID_COLORS = [null, 'gray', 'green', 'red', 'blue', 'orange', 'purple', 'teal', 'pink'];
  const clamp = (x, lo, hi) => Math.min(hi, Math.max(lo, x));
  const cut = (s, max) => { s = String(s); return s.length > max ? s.slice(0, Math.max(1, max - 1)) + '…' : s; };
  // A label in room for maxChars characters (charW px each): a slightly long one is squeezed up to 25% narrower (SVG
  // textLength) before it is cut with …, so 'newest low' fits where 'newest …' was drawn. Returns [text, extra attrs].
  const SQUEEZE = 0.75;
  function fitText(s, maxChars, charW) {
    s = String(s);
    if (s.length <= maxChars) return [s, ''];
    const squeeze = ` textLength="${Math.round(maxChars * charW)}" lengthAdjust="spacingAndGlyphs"`;
    return [s.length * SQUEEZE <= maxChars ? s : cut(s, Math.floor(maxChars / SQUEEZE)), squeeze];
  }
  // A number in maxChars characters: fewer decimals and no leading zero (-0.53 -> -.53 -> -.5), then 12k / 3.4M, and
  // only then cut. A non-zero value never shows as 0, and one below 1 is never rounded to a whole number (-0.64 is not
  // -1); with no room left it is cut (the tooltip keeps the exact value).
  function fitNum(v, maxChars, fmt) {
    const s = fmt(v);
    if (s.length <= maxChars || typeof v !== 'number' || !Number.isFinite(v)) return cut(s, maxChars);
    for (const d of Math.abs(v) < 1 ? [3, 2, 1] : [3, 2, 1, 0]) {
      const t = String(Number(v.toFixed(d))).replace(/^(-?)0\./, '$1.');
      if (t.length <= maxChars && (Number(t) !== 0 || v === 0)) return t;
    }
    const a = Math.abs(v);
    for (const [u, sfx] of [[1e12, 'T'], [1e9, 'B'], [1e6, 'M'], [1e3, 'k']]) {
      if (a < u) continue;
      for (const d of [1, 0]) { const t = String(Number((v / u).toFixed(d))) + sfx; if (t.length <= maxChars) return t; }
    }
    return cut(s, maxChars);
  }
  const attrs = (c) => `${c.cls}"${c.style ? ` style="${c.style}"` : ''}`;
  const maxAbs = (vals) => vals.reduce((m, v) => (Number.isFinite(v) ? Math.max(m, Math.abs(v)) : m), 0) || 1;

  // ------------------------------------------------------------ chart frame
  // A chart's size and plot area in px (its 440x290 viewBox). components.cjs draws with chartFrame and validator.cjs
  // checks layout with it, so a layout problem the checker reports is one the reader would see.
  const W = 440, H = 290, MARGIN = { l: 46, r: 14, t: 16, b: 32 };
  // Structural marks are laid out here; charts made only of these (plus pie/text) have no axes.
  const STRUCT = new Set(['graph', 'tree', 'matrix', 'sequence', 'grid']);
  const AXISLESS = new Set(['graph', 'tree', 'matrix', 'sequence', 'grid', 'pie', 'text', 'shape', 'link']);
  // Data-space extent of a heatmap: explicit x/y, else one unit per cell.
  const heatExtent = (m, axis) => axis === 'x' ? (m.x || [0, m.values[0].length]) : (m.y || [0, m.values.length]);

  function autoDomain(marks, axis) {
    let lo = Infinity, hi = -Infinity;
    const see = (v) => { if (Number.isFinite(v)) { lo = Math.min(lo, v); hi = Math.max(hi, v); } };
    for (const m of marks) {
      const i = axis === 'x' ? 0 : 1;
      if (m.type === 'heatmap') { heatExtent(m, axis).forEach(see); continue; }
      if (m.type === 'grid') { gridExtent(m)[axis].forEach(see); continue; }
      if (m.type === 'graph' && m.layout === 'manual') { m.nodes.forEach((nd) => see(nd[axis])); continue; }
      if (STRUCT.has(m.type) || m.type === 'pie') continue;
      (m.points || []).forEach((p) => see(p[i]));
      if (m.type === 'bars') {
        if (axis === 'y') { see(0); m.items.forEach((p) => see(p[1])); } else m.items.forEach((p) => { see(p[0] - 0.5); see(p[0] + 0.5); });
      }
      if (axis === 'x') [m.x, m.x1, m.x2].forEach(see); else [m.y, m.y1, m.y2].forEach(see);
    }
    if (lo === Infinity) return [0, 1];
    if (lo === hi) { lo -= 1; hi += 1; }
    const tight = marks.length && marks.every((m) => m.type === 'heatmap' || m.type === 'grid' || m.type === 'text' || m.type === 'bars');
    const pad = tight ? 0 : (hi - lo) * 0.08;
    return [lo - pad, hi + pad];
  }

  // Added Nov 2 (rubric eval rows 13, 14, 17, 25): a fixed range sized for a slider's extreme left the data in a sliver
  // (gradient bars, 8-bit errors) or a heatmap covering a third of x. The range now follows the data in view:
  // - a chart with a heatmap: each fixed axis shrinks to what is drawn (the heatmap's extent and the data on it);
  // - otherwise the y range zooms in by halves (1/4 .. 1/64, on a grid of eighth steps, so it moves rarely and in
  //   whole steps) when the data fits in a quarter of it (under ~22% with padding; less squashed data reads fine).
  // Never: spec.zoom === false, draggable marks or onDrag (the axis would move under the pointer), diagrams, a y range
  // starting at 0 (sizes: memory, cost, counts, probabilities growing with a slider are the lesson; 15 of the 45 charts
  // zoomed in a first replay were these), data with no height (all zero / flat). What counts: lines of 3+ points (a 2-point
  // line is a guide drawn across the chart), areas and bars from 0, dots, rules, rects, arrows, heatmaps; not text
  // (placeText keeps it inside). Returns {x?, y?} ranges to draw with.
  const ZOOMABLE = new Set(['line', 'area', 'bars', 'points', 'point', 'hline', 'vline', 'rect', 'arrow', 'text', 'heatmap']);
  const ZOOM_MIN = 2, ZOOM_MAX = 6;  // zoom to 1/4 .. 1/64 of the range
  function dataExtent(marks, axis) {
    let lo = Infinity, hi = -Infinity;
    const see = (v) => { if (Number.isFinite(v)) { lo = Math.min(lo, v); hi = Math.max(hi, v); } };
    const i = axis === 'x' ? 0 : 1;
    for (const m of marks) {
      if (m.type === 'heatmap') heatExtent(m, axis).forEach(see);
      else if (m.type === 'line') { if ((m.points || []).length > 2) m.points.forEach((p) => see(p[i])); }
      else if (m.type === 'area') { (m.points || []).forEach((p) => see(p[i])); if (axis === 'y') see(0); }
      else if (m.type === 'points') (m.points || []).forEach((p) => see(p[i]));
      else if (m.type === 'bars') (m.items || []).forEach((p) => { if (axis === 'y') { see(0); see(p[1]); } else { see(p[0] - 0.5); see(p[0] + 0.5); } });
      else if (m.type === 'point') see(m[axis]);
      else if (m.type === 'hline') { if (axis === 'y') see(m.y); }
      else if (m.type === 'vline') { if (axis === 'x') see(m.x); }
      else if (m.type === 'rect' || m.type === 'arrow') [m[axis + '1'], m[axis + '2']].forEach(see);
    }
    return lo <= hi ? [lo, hi] : null;
  }
  function zoomRange(spec, marks) {
    const out = {};
    if (spec.zoom === false || spec.onDrag || !marks.length || marks.some((m) => !m || !ZOOMABLE.has(m.type) || m.draggable)) return out;
    const fixed = (r) => Array.isArray(r) && r.length === 2 && Number.isFinite(r[0]) && Number.isFinite(r[1]) && r[1] > r[0];
    const inside = (e, r) => (e ? [Math.max(e[0], r[0]), Math.min(e[1], r[1])] : null);
    if (marks.some((m) => m.type === 'heatmap')) {
      for (const axis of ['x', 'y']) {
        const r = spec[axis], e = fixed(r) && inside(dataExtent(marks, axis), r);
        if (e && e[1] > e[0] && e[1] - e[0] < 0.9 * (r[1] - r[0])) out[axis] = e;
      }
      return out;
    }
    const r = spec.y;
    if (!fixed(r) || r[0] === 0) return out;
    const e = inside(dataExtent(marks, 'y'), r), F = r[1] - r[0];
    if (!e || e[1] - e[0] < F / 2 ** ZOOM_MAX) return out;
    for (let k = ZOOM_MAX; k >= ZOOM_MIN; k--) {
      const span = F / 2 ** k, step = span / 8, pad = 0.06 * span;
      if (e[1] - e[0] + 2 * pad > span) continue;
      const base = r[0] + Math.floor((e[0] - pad - r[0]) / step) * step, eps = 1e-9 * F;
      for (let j = 0; j < 8 && !out.y; j++) {
        let lo = Math.max(r[0], base - j * step);
        if (lo + span > r[1]) lo = r[1] - span;
        // padding at an end is needed only where the range could go further (not at the fixed range's own end)
        const okLo = lo <= e[0] - pad + eps || lo <= r[0] + eps, okHi = lo + span >= e[1] + pad - eps || lo + span >= r[1] - eps;
        if (okLo && okHi) out.y = [lo, lo + span];
      }
      if (out.y) break;
    }
    return out;
  }

  // Added Nov 2 (rubric eval row 15): a plane drawn with the same range on both axes (vectors, PCA directions, query and
  // key arrows) was stretched 1.6x sideways, so perpendicular lines didn't look perpendicular. Equal scales when
  // aspect: 'equal', or by default when x and y are the same fixed range around 0 (lo < 0 < hi) and every mark is a
  // plotted one (no bars, heatmap, grid, diagram). aspect: 'free' turns it off. Such charts don't zoom.
  const PLANE = new Set(['line', 'area', 'points', 'point', 'hline', 'vline', 'rect', 'arrow', 'text']);
  function equalAspect(spec, marks) {
    if (spec.aspect === 'equal') return true;
    if (spec.aspect !== undefined) return false;
    const x = spec.x, y = spec.y;
    return Array.isArray(x) && Array.isArray(y) && x.length === 2 && y.length === 2 && x[0] === y[0] && x[1] === y[1] &&
      x[0] < 0 && x[1] > 0 && marks.length > 0 && marks.every((m) => m && PLANE.has(m.type));
  }

  // { dom, M, hasAxes, sx, sy, plot } for a chart spec drawing these marks (the rules components.cjs renders with).
  // The chart's axis names, minus one a text mark already writes word for word (Nov 2, rubric eval row 1: 'L(w)' was
  // both the y-axis name and the curve's label). The text mark stays: it is placed by the section, often on its curve.
  const Y_NAME_ROOM = 10;
  function axisNames(spec, marks) {
    const said = new Set(marks.filter((m) => m && m.type === 'text').map((m) => String(m.text == null ? '' : m.text).trim().toLowerCase()));
    const keep = (v) => (v !== undefined && v !== null && String(v).trim() && !said.has(String(v).trim().toLowerCase()) ? String(v) : '');
    return { x: keep(spec.xLabel), y: keep(spec.yLabel) };
  }

  function chartFrame(spec, marks) {
    const dom = { x: spec.x || autoDomain(marks, 'x'), y: spec.y || autoDomain(marks, 'y') };
    // Bars on a fixed y range get 4% room above it, so a bar as tall as the range (a probability of 1 in [0, 1]) doesn't
    // touch the frame and look cut off (Nov 1: 24 of 206 playgrounds). Ticks and the checker's range rules still use spec.y.
    const equal = equalAspect(spec, marks);
    const zoom = equal ? {} : zoomRange(spec, marks);
    if (zoom.x) dom.x = zoom.x;
    if (zoom.y) dom.y = zoom.y;
    else if (spec.y && spec.y[1] > 0 && marks.some((m) => m.type === 'bars')) dom.y = [spec.y[0], spec.y[1] + 0.04 * (spec.y[1] - spec.y[0])];
    // No axes for charts made only of pie/structural/text marks, or with a grid; `axes: true/false` overrides.
    const auto = !(marks.some((m) => m.type !== 'text') &&
      (marks.every((m) => AXISLESS.has(m.type)) || marks.some((m) => m.type === 'grid')));
    const hasAxes = spec.axes === undefined ? auto : !!spec.axes;
    const hm = marks.find((m) => m.type === 'heatmap' && (m.xLabels || m.yLabels));
    const M = hasAxes ? { ...MARGIN } : { l: 12, r: 12, t: 12, b: 12 };
    if (hasAxes && hm && hm.yLabels) M.l = Math.min(140, Math.max(MARGIN.l, 14 + 7.5 * Math.max(...hm.yLabels.map((l) => String(l).length))));
    // The y-axis name is drawn above the plot's top-left corner (Nov 2, rubric eval rows 17, 21, 25): inside the plot it sat
    // on curves, text marks and heatmap cells.
    if (hasAxes && axisNames(spec, marks).y) M.t = MARGIN.t + Y_NAME_ROOM;
    if (equal) {
      // one x unit as long as one y unit: the axis with spare room gets more range, centred (the plot keeps its size)
      const pw = W - M.l - M.r, ph = H - M.t - M.b, xs = dom.x[1] - dom.x[0], ys = dom.y[1] - dom.y[0];
      const grow = (r, span) => { const c = (r[0] + r[1]) / 2; return [c - span / 2, c + span / 2]; };
      if (pw / xs > ph / ys) dom.x = grow(dom.x, pw * ys / ph); else dom.y = grow(dom.y, ph * xs / pw);
    }
    const sx = (v) => M.l + (v - dom.x[0]) / (dom.x[1] - dom.x[0]) * (W - M.l - M.r);
    const sy = (v) => H - M.b - (v - dom.y[0]) / (dom.y[1] - dom.y[0]) * (H - M.t - M.b);
    return { dom, M, hasAxes, sx, sy, plot: { x0: M.l, y0: M.t, x1: W - M.r, y1: H - M.b } };
  }

  // A mark's drawing box in px: the plot, or m.box = [x0, y0, x1, y1] as fractions of it (from top-left).
  function markBox(m, plot) {
    const f = Array.isArray(m && m.box) && m.box.length === 4 ? m.box : [0, 0, 1, 1];
    return { x0: plot.x0 + f[0] * (plot.x1 - plot.x0), y0: plot.y0 + f[1] * (plot.y1 - plot.y0),
      x1: plot.x0 + f[2] * (plot.x1 - plot.x0), y1: plot.y0 + f[3] * (plot.y1 - plot.y0) };
  }

  // Added in Explainer Studio (Nov 2, rubric eval row 18): hand-placed graph nodes (layout 'manual') shrink until no two
  // circles touch and no name drawn under a node lands on the node (or its value) below it. Before, the radius came
  // from the node count only, so a beam-search tree with rows 36 px apart drew 36 px circles on top of each other.
  // Returns {pos, r, clash}: clash lists what still collides at GRAPH_MIN_R (the checker reports it) as
  // {i, j, kind: 'nodes' | 'name' | 'names', need} with need = the px distance (dy, or dx for 'names') that would fit.
  const GRAPH_MIN_R = 6;
  const graphNameBelow = (nd) => nd.label !== undefined && String(nd.label).length > 3;
  const graphNameHalf = (nd) => Math.min(12, String(nd.label).length) * 6.5 / 2;  // fitText(label, 12, 6.5)
  function manualGraph(m, sx, sy, b) {
    const N = m.nodes.length;
    const pos = m.nodes.map((nd) => [sx(nd.x), sy(nd.y)]);
    const r0 = m.r || clamp(Math.min(b.x1 - b.x0, b.y1 - b.y0) / (Math.ceil(Math.sqrt(N)) * 2.8), 7, 18);
    const top = m.showValues ? 14 : 0;  // a value drawn above a node takes ~14 px
    const limits = [];                  // {i, j, kind, max: the largest r that fits, need}
    const nameRows = [];
    for (let i = 0; i < N; i++) {
      for (let j = i + 1; j < N; j++) {
        const dx = Math.abs(pos[j][0] - pos[i][0]), dy = pos[j][1] - pos[i][1], d = Math.hypot(dx, dy);
        limits.push({ i, j, kind: 'nodes', max: (d - 3) / 2, need: 2 * GRAPH_MIN_R + 3 });
        // the name under the upper node: from y + r + 3 to y + r + 16 (baseline at y + r + 13)
        const [up, lo, gap] = dy >= 0 ? [i, j, dy] : [j, i, -dy];
        if (gap > 0 && graphNameBelow(m.nodes[up]) && dx < graphNameHalf(m.nodes[up]) + r0) {
          limits.push({ i: up, j: lo, kind: 'name', max: (gap - 17 - top) / 2, need: 17 + top + 2 * GRAPH_MIN_R });
        }
        // two names side by side: moving with their nodes, no radius separates them
        if (graphNameBelow(m.nodes[i]) && graphNameBelow(m.nodes[j]) && Math.abs(dy) < 14) {
          const room = graphNameHalf(m.nodes[i]) + graphNameHalf(m.nodes[j]) + 4;
          if (dx < room) nameRows.push({ i, j, kind: 'names', need: room });
        }
      }
    }
    const r = Math.max(GRAPH_MIN_R, Math.min(r0, ...limits.map((l) => l.max)));
    const clash = [...limits.filter((l) => l.max < GRAPH_MIN_R), ...nameRows];
    return { pos, r, clash };
  }

  // ------------------------------------------------------------------ graph
  function graph(m, ctx) {
    const { n, esc, fmt, colorAttr, tip, hit } = ctx;
    const b = ctx.box(m), N = m.nodes.length;
    const layered = m.layout === 'layers' || (!m.layout && m.nodes.some((nd) => nd.layer !== undefined));
    let pos = new Array(N);
    let span = 1, manualR = 0;
    if (m.layout === 'manual') {
      ({ pos, r: manualR } = manualGraph(m, ctx.sx, ctx.sy, b));
    } else if (layered) {
      const layers = [...new Set(m.nodes.map((nd) => nd.layer || 0))].sort((a, c) => a - c);
      const cols = layers.map((l) => m.nodes.map((nd, i) => ((nd.layer || 0) === l ? i : -1)).filter((i) => i >= 0));
      cols.forEach((ids, li) => ids.forEach((i, k) => {
        pos[i] = [b.x0 + (li + 0.5) * (b.x1 - b.x0) / cols.length, b.y0 + (k + 0.5) * (b.y1 - b.y0) / ids.length];
      }));
      span = Math.max(cols.length, ...cols.map((c) => c.length));
    } else {
      // leave room outside the ring for self-loops
      const loops = m.edges.some((e) => e[0] === e[1]);
      const r0 = m.r || clamp(Math.min(b.x1 - b.x0, b.y1 - b.y0) / (Math.max(3, N / 2.2) * 2.8), 7, 18);
      const cx = (b.x0 + b.x1) / 2, cy = (b.y0 + b.y1) / 2, R = Math.min(b.x1 - b.x0, b.y1 - b.y0) / 2 - 22 - (loops ? r0 * 2.6 : 0);
      m.nodes.forEach((nd, i) => { const a = -Math.PI / 2 + 2 * Math.PI * i / N; pos[i] = [cx + R * Math.cos(a), cy + R * Math.sin(a)]; });
      span = Math.max(3, N / 2.2);
    }
    const r = manualR || m.r || clamp(Math.min(b.x1 - b.x0, b.y1 - b.y0) / (span * 2.8), 7, 18);

    const wMax = maxAbs(m.edges.map((e) => e[2]));
    const cx0 = pos.reduce((t, p) => t + p[0], 0) / N, cy0 = pos.reduce((t, p) => t + p[1], 0) / N;
    const pairs = new Set(m.edges.map((e) => `${e[0]}>${e[1]}`));
    const unit = (x, y) => { const L = Math.hypot(x, y) || 1; return [x / L, y / L]; };
    let out = '';
    m.edges.forEach((e, j) => {
      const [a, c, w] = e;
      const [x1, y1] = pos[a], [x2, y2] = pos[c];
      const has = Number.isFinite(w), t = has ? Math.abs(w) / wMax : 0.5;
      const col = colorAttr(has ? (w >= 0 ? (m.posColor || 'blue') : (m.negColor || 'red')) : 'gray', 'stroke');
      const name = `${m.nodes[a].label || `#${a}`} → ${m.nodes[c].label || `#${c}`}`;
      let d, lx, ly;
      if (a === c) {
        // self-loop, bulging away from the graph's centre
        const [ux, uy] = N > 1 && Math.hypot(x1 - cx0, y1 - cy0) > 1 ? unit(x1 - cx0, y1 - cy0) : [0, -1];
        const [px, py] = [-uy, ux];
        const sx0 = x1 + (ux * 0.7 + px * 0.7) * r, sy0 = y1 + (uy * 0.7 + py * 0.7) * r;
        const ex = x1 + (ux * 0.7 - px * 0.7) * r, ey = y1 + (uy * 0.7 - py * 0.7) * r;
        const k = r * 2.6;
        d = `M${n(sx0)},${n(sy0)} C${n(sx0 + ux * k + px * r)},${n(sy0 + uy * k + py * r)} ${n(ex + ux * k - px * r)},${n(ey + uy * k - py * r)} ${n(ex)},${n(ey)}`;
        lx = x1 + ux * (r + k * 0.8); ly = y1 + uy * (r + k * 0.8);
      } else {
        // edges in both directions curve apart instead of overlapping
        const bend = m.directed && pairs.has(`${c}>${a}`) ? 0.2 : 0;
        const [ux, uy] = unit(x2 - x1, y2 - y1), L = Math.hypot(x2 - x1, y2 - y1);
        const qx = (x1 + x2) / 2 - uy * L * bend, qy = (y1 + y2) / 2 + ux * L * bend;
        const [sx1, sy1] = unit(qx - x1, qy - y1), [sx2, sy2] = unit(x2 - qx, y2 - qy);
        const end = r + (m.directed ? 3 : 0);
        d = `M${n(x1 + sx1 * r)},${n(y1 + sy1 * r)} Q${n(qx)},${n(qy)} ${n(x2 - sx2 * end)},${n(y2 - sy2 * end)}`;
        lx = 0.25 * x1 + 0.5 * qx + 0.25 * x2; ly = 0.25 * y1 + 0.5 * qy + 0.25 * y2;
      }
      out += `<path class="st-edge ${attrs(col)} d="${d}" stroke-width="${n(has ? 0.8 + 4 * t : 1.5)}" stroke-opacity="${n(has ? 0.22 + 0.78 * t : 0.6)}"${m.directed ? ` marker-end="url(#${ctx.arrowId})"` : ''}/>`;
      out += `<path class="st-hit" d="${d}"${tip(has ? `${name}: ${fmt(w)}` : name)}${hit(m, j, 'edge')}/>`;
      if (m.edgeLabels && has) out += `<text class="st-small" x="${n(lx)}" y="${n(ly)}" text-anchor="middle" dy="0.35em">${esc(fmt(w))}</text>`;
    });

    const vMax = maxAbs(m.nodes.map((nd) => nd.value));
    m.nodes.forEach((nd, i) => {
      const [x, y] = pos[i];
      let c, fop = 1, plain = false;
      if (nd.color !== undefined) { c = colorAttr(nd.color, 'fill'); fop = 0.85; }
      else if (Number.isFinite(nd.value)) { c = colorAttr(nd.value >= 0 ? (m.posColor || 'blue') : (m.negColor || 'red'), 'fill'); fop = 0.12 + 0.88 * Math.abs(nd.value) / vMax; }
      else { c = { cls: '', style: '' }; plain = true; }
      const label = nd.label === undefined ? '' : String(nd.label);
      const drag = m.layout === 'manual' && m.draggable ? ` data-drag="${esc(m.id || '')}" data-index="${i}"` : '';
      out += `<circle class="st-node${plain ? ' is-plain' : ''} ${attrs(c)} cx="${n(x)}" cy="${n(y)}" r="${n(r)}" fill-opacity="${n(fop)}"${drag}` +
        `${tip(`${label || `node ${i}`}${Number.isFinite(nd.value) ? `: ${fmt(nd.value)}` : ''}`)}${hit(m, i, 'node')}/>`;
      if (label) {
        out += label.length <= 3
          ? `<text class="st-label" x="${n(x)}" y="${n(y)}" text-anchor="middle" dy="0.35em">${esc(label)}</text>`
          : ((t) => `<text class="st-label" x="${n(x)}" y="${n(y + r + 13)}" text-anchor="middle"${t[1]}>${esc(t[0])}</text>`)(fitText(label, 12, 6.5));
      }
      if (m.showValues && Number.isFinite(nd.value)) {
        out += `<text class="st-small" x="${n(x)}" y="${n(y - r - 5)}" text-anchor="middle">${esc(fmt(nd.value))}</text>`;
      }
    });
    return out;
  }

  // ------------------------------------------------------------------- tree
  function tree(m, ctx) {
    const { n, esc, fmt, colorAttr, tip, hit } = ctx;
    const b = ctx.box(m);
    const nodes = [];
    (function walk(nd, d, p) {
      const i = nodes.length;
      nodes.push({ nd, d, p, kids: [] });
      if (p >= 0) nodes[p].kids.push(i);
      (nd.children || []).forEach((ch) => walk(ch, d + 1, i));
    })(m.root, 0, -1);
    const depth = Math.max(...nodes.map((x) => x.d));
    let leaf = 0;
    const xs = new Array(nodes.length);
    (function place(i) {
      const k = nodes[i].kids;
      if (!k.length) { xs[i] = leaf++; return; }
      k.forEach(place);
      xs[i] = (xs[k[0]] + xs[k[k.length - 1]]) / 2;
    })(0);
    const slot = (b.x1 - b.x0) / leaf, row = (b.y1 - b.y0) / (depth + 1);
    const X = (i) => b.x0 + (xs[i] + 0.5) * slot, Y = (i) => b.y0 + (nodes[i].d + 0.5) * row;
    const bh = clamp(row * 0.5, 16, 26), maxW = clamp(slot - 6, 24, 120);
    const hl = new Set(m.highlight || []);
    let out = '';
    nodes.forEach((x, i) => {
      if (x.p < 0) return;
      const x1 = X(x.p), y1 = Y(x.p) + bh / 2, x2 = X(i), y2 = Y(i) - bh / 2;
      const on = hl.has(i) || x.nd.active;
      out += `<line class="st-tedge${on ? ' is-on' : ''}" x1="${n(x1)}" y1="${n(y1)}" x2="${n(x2)}" y2="${n(y2)}"/>`;
      if (x.nd.edge !== undefined) out += `<text class="st-small" x="${n((x1 + x2) / 2)}" y="${n((y1 + y2) / 2)}" text-anchor="middle" dy="0.35em">${esc(cut(x.nd.edge, 8))}</text>`;
    });
    nodes.forEach((x, i) => {
      const label = x.nd.label === undefined ? '' : String(x.nd.label);
      const w = Math.min(maxW, Math.max(24, label.length * 6.6 + 14));
      const c = x.nd.color !== undefined ? colorAttr(x.nd.color, 'fill') : { cls: '', style: '' };
      const on = hl.has(i) || x.nd.active;
      out += `<rect class="st-box${x.nd.color !== undefined ? ' is-colored' : ''}${on ? ' is-on' : ''} ${attrs(c)} x="${n(X(i) - w / 2)}" y="${n(Y(i) - bh / 2)}" width="${n(w)}" height="${n(bh)}" rx="6"` +
        `${tip(`${label || `node ${i}`}${x.nd.value !== undefined ? ` (${fmt(x.nd.value)})` : ''}`)}${hit(m, i, 'node')}/>`;
      if (label) { const [t, sq] = fitText(label, Math.floor((w - 8) / 6.6), 6.6); out += `<text class="st-label" x="${n(X(i))}" y="${n(Y(i))}" text-anchor="middle" dy="0.35em"${sq}>${esc(t)}</text>`; }
    });
    return out;
  }

  // ------------------------------------------------- matrices (left to right)
  function matrices(ms, ctx, b) {
    const { n, esc, fmt, colorAttr, tip, hit } = ctx;
    const OP = 26, PAD = 10, ROWLAB = 44;
    const cols = ms.reduce((t, m) => t + m.values[0].length, 0);
    const rows = Math.max(...ms.map((m) => m.values.length));
    const colLab = ms.some((m) => m.colLabels);
    const overhead = ms.reduce((t, m) => t + 2 * PAD + (m.op ? OP : 0) + (m.rowLabels ? ROWLAB : 0), 0) + (ms.length - 1) * 6;
    const cs = clamp(Math.min((b.x1 - b.x0 - overhead) / cols, (b.y1 - b.y0 - 26 - (colLab ? 16 : 0)) / rows), 10, 44);
    let x = b.x0 + (b.x1 - b.x0 - (cols * cs + overhead)) / 2;
    const cy = (b.y0 + b.y1) / 2 + 8 + (colLab ? 8 : 0);
    const fs = clamp(cs * 0.36, 9, 14);
    let out = '';
    ms.forEach((m) => {
      const R = m.values.length, C = m.values[0].length, h = R * cs;
      if (m.op) { out += `<text class="st-op" x="${n(x + OP / 2)}" y="${n(cy)}" text-anchor="middle" dy="0.35em">${esc(cut(m.op, 2))}</text>`; x += OP; }
      if (m.rowLabels) x += ROWLAB;
      const left = x + PAD, top = cy - h / 2;
      const name = m.label === undefined ? 'M' : String(m.label);
      // the matrix name may use the matrix's whole width (with its brackets), not a fixed 10 characters
      if (m.label !== undefined) { const [t, sq] = fitText(m.label, Math.max(10, Math.floor((C * cs + 2 * PAD - 4) / 7)), 7); out += `<text class="st-mlabel" x="${n(left + C * cs / 2)}" y="${n(top - 8 - (m.colLabels ? 15 : 0))}" text-anchor="middle"${sq}>${esc(t)}</text>`; }
      (m.colLabels || []).slice(0, C).forEach((l, c) => { const [t, sq] = fitText(l, Math.max(2, Math.floor(cs / 6.5)), 6.5); out += `<text class="st-small" x="${n(left + (c + 0.5) * cs)}" y="${n(top - 5)}" text-anchor="middle"${sq}>${esc(t)}</text>`; });
      (m.rowLabels || []).slice(0, R).forEach((l, r) => { const [t, sq] = fitText(l, 6, 6.5); out += `<text class="st-small" x="${n(left - 7)}" y="${n(top + (r + 0.5) * cs)}" text-anchor="end" dy="0.35em"${sq}>${esc(t)}</text>`; });
      const vMax = maxAbs(m.values.flat());
      const hr = new Set(m.highlightRows || []), hc = new Set(m.highlightCols || []);
      const hcell = new Set((m.highlightCells || []).map((p) => `${p[0]},${p[1]}`));
      m.values.forEach((rowVals, r) => rowVals.forEach((v, c) => {
        const on = hr.has(r) || hc.has(c) || hcell.has(`${r},${c}`);
        const heat = m.heat ? colorAttr(v >= 0 ? (m.posColor || 'blue') : (m.negColor || 'red'), 'fill') : null;
        out += `<rect class="st-cell${on ? ' is-on' : ''}${heat ? ` ${heat.cls}` : ''}"${heat && heat.style ? ` style="${heat.style}"` : ''}${heat ? ` fill-opacity="${n(0.1 + 0.75 * Math.abs(v) / vMax)}"` : ''} x="${n(left + c * cs)}" y="${n(top + r * cs)}" width="${n(cs)}" height="${n(cs)}"` +
          `${tip(`${name}[${r + 1},${c + 1}] = ${fmt(v)}`)}${hit(m, r * C + c, 'cell')}/>`;
        out += `<text class="st-num${on ? ' is-on' : ''}" x="${n(left + (c + 0.5) * cs)}" y="${n(top + (r + 0.5) * cs)}" text-anchor="middle" dy="0.35em" font-size="${n(fs)}">${esc(fitNum(v, Math.max(3, Math.floor(cs / (fs * 0.55))), fmt))}</text>`;
      }));
      const right = left + C * cs;
      out += `<path class="st-bracket" d="M${n(left - 2)},${n(top - 3)} h-5 v${n(h + 6)} h5 M${n(right + 2)},${n(top - 3)} h5 v${n(h + 6)} h-5"/>`;
      x = right + PAD + 6;
    });
    return out;
  }

  // ------------------------------------------------ sequences (stacked rows)
  function sequences(ms, ctx, b) {
    const { n, esc, fmt, colorAttr, tip, hit } = ctx;
    const rows = ms.length, band = (b.y1 - b.y0) / rows;
    const labW = ms.some((m) => m.label !== undefined) ? 58 : 0;
    const GAP = 6;
    const geo = ms.map((m, i) => {
      const N = m.tokens.length;
      const tw = Math.min(84, (b.x1 - b.x0 - labW - GAP * (N - 1)) / N);
      const th = clamp(band * 0.4, 18, 34);
      const x0 = b.x0 + labW + (b.x1 - b.x0 - labW - (N * tw + (N - 1) * GAP)) / 2;
      const y = b.y0 + band * (i + 0.5) - th / 2 - (m.values ? 7 : 0);
      return { tw, th, y, cx: m.tokens.map((_, k) => x0 + k * (tw + GAP) + tw / 2) };
    });
    let out = '';
    // links first so boxes sit on top
    ms.forEach((m, i) => {
      if (!m.links || !ms[i + 1]) return;
      const g = geo[i], h = geo[i + 1], next = ms[i + 1].tokens;
      const wMax = maxAbs(m.links.map((l) => l[2]));
      const col = colorAttr(m.linkColor || 'purple', 'stroke');
      m.links.forEach(([a, c, w], j) => {
        if (c < 0 || c >= next.length) return;
        const t = Number.isFinite(w) ? Math.abs(w) / wMax : 0.5;
        const y1 = g.y + g.th + (m.values ? 14 : 0), y2 = h.y;
        const xy = `x1="${n(g.cx[a])}" y1="${n(y1)}" x2="${n(h.cx[c])}" y2="${n(y2)}"`;
        out += `<line class="st-link ${attrs(col)} ${xy} stroke-width="${n(0.6 + 5 * t)}" stroke-opacity="${n(0.12 + 0.88 * t)}"/>`;
        out += `<line class="st-hit" ${xy}${tip(`${m.tokens[a]} → ${next[c]}${Number.isFinite(w) ? `: ${fmt(w)}` : ''}`)}${hit(m, j, 'link')}/>`;
      });
    });
    ms.forEach((m, i) => {
      const g = geo[i];
      const hl = new Set(m.highlight || []);
      const win = Array.isArray(m.window) ? m.window : null;
      const maxChars = Math.max(2, Math.floor((g.tw - 6) / 7));
      if (m.label !== undefined) { const [t, sq] = fitText(m.label, 8, 6.5); out += `<text class="st-mlabel" x="${n(b.x0 + labW - 8)}" y="${n(g.y + g.th / 2)}" text-anchor="end" dy="0.35em"${sq}>${esc(t)}</text>`; }
      m.tokens.forEach((tok, k) => {
        const on = hl.has(k) || (win && k >= win[0] && k <= win[1]);
        const c = Array.isArray(m.colors) && m.colors[k] !== undefined && m.colors[k] !== null ? colorAttr(m.colors[k], 'fill') : null;
        const x = g.cx[k] - g.tw / 2;
        if (m.arrows && k < m.tokens.length - 1) {
          out += `<line class="st-tedge" x1="${n(x + g.tw)}" y1="${n(g.y + g.th / 2)}" x2="${n(x + g.tw + GAP - 1)}" y2="${n(g.y + g.th / 2)}" marker-end="url(#${ctx.arrowId})"/>`;
        }
        const val = m.values && m.values[k] !== undefined ? m.values[k] : undefined;
        out += `<rect class="st-tok${on ? ' is-on' : ''}${c ? ` is-colored ${c.cls}` : ''}"${c && c.style ? ` style="${c.style}"` : ''} x="${n(x)}" y="${n(g.y)}" width="${n(g.tw)}" height="${n(g.th)}" rx="6"` +
          `${tip(`${tok}${val !== undefined ? `: ${fmt(val)}` : ''}`)}${hit(m, k, 'item')}/>`;
        const [tt, tsq] = fitText(tok, maxChars, 7);
        out += `<text class="st-label" x="${n(g.cx[k])}" y="${n(g.y + g.th / 2)}" text-anchor="middle" dy="0.35em"${tsq}>${esc(tt)}</text>`;
        if (val !== undefined) out += `<text class="st-small" x="${n(g.cx[k])}" y="${n(g.y + g.th + 12)}" text-anchor="middle">${esc(fitNum(val, maxChars + 1, fmt))}</text>`;
      });
    });
    return out;
  }

  // ------------------------------------------------- grid (in data units)
  const gridExtent = (m) => ({ x: m.x || [0, m.cells[0].length], y: m.y || [0, m.cells.length] });
  const ARROW = { up: [0, -1], down: [0, 1], left: [-1, 0], right: [1, 0] };

  function grid(m, ctx) {
    const { n, esc, colorAttr, tip, hit } = ctx;
    const R = m.cells.length, C = m.cells[0].length, ext = gridExtent(m);
    const cw = (ext.x[1] - ext.x[0]) / C, ch = (ext.y[1] - ext.y[0]) / R;
    const px = (c) => ctx.sx(ext.x[0] + c * cw), py = (r) => ctx.sy(ext.y[1] - r * ch);
    const cellPx = Math.min(Math.abs(px(1) - px(0)), Math.abs(py(1) - py(0)));
    const fs = clamp(cellPx * 0.28, 8, 13);
    let out = '';
    m.cells.forEach((row, r) => row.forEach((v, c) => {
      const x = Math.min(px(c), px(c + 1)), y = Math.min(py(r), py(r + 1)), w = Math.abs(px(c + 1) - px(c)), h = Math.abs(py(r + 1) - py(r));
      const name = v === null || v === 0 ? null : (Array.isArray(m.colors) && m.colors[v] !== undefined ? m.colors[v] : (GRID_COLORS[v] || PALETTE[v % PALETTE.length]));
      const col = name ? colorAttr(name, 'fill') : null;
      const label = m.labels ? m.labels[r][c] : null;
      out += `<rect class="st-gcell${col ? ` is-colored ${col.cls}` : ''}"${col && col.style ? ` style="${col.style}"` : ''}${col ? ` fill-opacity="${m.opacity !== undefined ? Number(m.opacity) : v === 1 ? 0.8 : 0.5}"` : ''} x="${n(x)}" y="${n(y)}" width="${n(w)}" height="${n(h)}"` +
        `${tip(`(${r}, ${c})${label !== null && label !== undefined && label !== '' ? `: ${label}` : ''}`)}${hit(m, r * C + c, 'cell')}/>`;
      const a = m.arrows ? m.arrows[r][c] : null;
      const hasLabel = label !== null && label !== undefined && label !== '';
      const cx = x + w / 2, cy = y + h / 2;
      if (a) {
        const [dx, dy] = ARROW[a], len = cellPx * 0.26, ay = hasLabel ? y + h * 0.32 : cy;
        out += `<line class="st-garrow" x1="${n(cx - dx * len)}" y1="${n(ay - dy * len)}" x2="${n(cx + dx * len)}" y2="${n(ay + dy * len)}" marker-end="url(#${ctx.arrowId})"/>`;
      }
      if (hasLabel) out += `<text class="st-label" x="${n(cx)}" y="${n(a ? y + h * 0.72 : cy)}" text-anchor="middle" dy="0.35em" font-size="${n(fs)}">${esc(cut(label, Math.max(2, Math.floor(w / (fs * 0.6)))))}</text>`;
    }));
    (m.highlight || []).forEach((p) => {
      if (!Array.isArray(p) || p[0] < 0 || p[0] >= R || p[1] < 0 || p[1] >= C) return;
      out += `<rect class="st-ghl" x="${n(Math.min(px(p[1]), px(p[1] + 1)))}" y="${n(Math.min(py(p[0]), py(p[0] + 1)))}" width="${n(Math.abs(px(1) - px(0)))}" height="${n(Math.abs(py(1) - py(0)))}"/>`;
    });
    (m.agents || []).forEach((ag, i) => {
      const col = colorAttr(ag.color !== undefined ? ag.color : 'purple', 'fill');
      const drag = m.draggable ? ` data-drag="${esc(m.id || '')}" data-index="${i}"` : '';
      // with a label or an arrow in the cell, the agent sits in the top-left corner so they stay readable (an arrow
      // under the agent: rubric eval row 8, Nov 2)
      const lab = (m.labels && m.labels[ag.r] && m.labels[ag.r][ag.c] !== null && m.labels[ag.r][ag.c] !== undefined && m.labels[ag.r][ag.c] !== '') ||
        !!(m.arrows && m.arrows[ag.r] && m.arrows[ag.r][ag.c]);
      const x0 = Math.min(px(ag.c), px(ag.c + 1)), y0 = Math.min(py(ag.r), py(ag.r + 1));
      const [ax, ay, ar] = lab ? [x0 + cellPx * 0.22, y0 + cellPx * 0.22, cellPx * 0.16] : [x0 + cellPx / 2, y0 + cellPx / 2, cellPx * 0.3];
      out += `<circle class="st-agent ${attrs(col)} cx="${n(ax)}" cy="${n(ay)}" r="${n(ar)}"${drag}${tip(`agent (${ag.r}, ${ag.c})`)}/>`;
    });
    return out;
  }

  // Row/column of a data-space position on a grid mark (for pos.row / pos.col).
  function cellAt(m, x, y) {
    const R = m.cells.length, C = m.cells[0].length, ext = gridExtent(m);
    const col = Math.floor((x - ext.x[0]) / ((ext.x[1] - ext.x[0]) / C));
    const row = Math.floor((ext.y[1] - y) / ((ext.y[1] - ext.y[0]) / R));
    return { row: clamp(row, 0, R - 1), col: clamp(col, 0, C - 1) };
  }

  // ------------------------------------------------------------ shapes and links
  // Free-form diagram parts: a shape (box / circle / diamond, or a node placed by a point) and a link that joins two
  // shapes by id. Shapes sit at data-unit positions; a node's size is in pixels so a label always fits.
  const SHAPE_SIZE = { circle: [16, 16], box: [32, 16], diamond: [32, 22] };  // default half-width, half-height (px)

  // Where a shape is on screen: centre and half-sizes in px.
  function shapeGeom(m, ctx) {
    if (m.x1 !== undefined) {
      const a = ctx.sx(m.x1), b = ctx.sx(m.x2), c = ctx.sy(m.y1), d = ctx.sy(m.y2);
      return { shape: m.shape, cx: (a + b) / 2, cy: (c + d) / 2, hw: Math.max(3, Math.abs(b - a) / 2), hh: Math.max(3, Math.abs(d - c) / 2) };
    }
    const [dw, dh] = SHAPE_SIZE[m.shape];
    const hw = m.r !== undefined ? m.r : m.w !== undefined ? m.w / 2 : dw;
    const hh = m.r !== undefined ? m.r : m.h !== undefined ? m.h / 2 : dh;
    return { shape: m.shape, cx: ctx.sx(m.x), cy: ctx.sy(m.y), hw, hh };
  }

  // The point where the line from a shape's centre toward (tx, ty) leaves the shape.
  function edgePoint(g, tx, ty) {
    const dx = tx - g.cx, dy = ty - g.cy;
    if (!dx && !dy) return [g.cx, g.cy];
    const t = g.shape === 'box' ? Math.min(dx ? g.hw / Math.abs(dx) : Infinity, dy ? g.hh / Math.abs(dy) : Infinity)
      : g.shape === 'diamond' ? 1 / (Math.abs(dx) / g.hw + Math.abs(dy) / g.hh)
      : 1 / Math.hypot(dx / g.hw, dy / g.hh);
    return [g.cx + dx * t, g.cy + dy * t];
  }

  function shape(m, ctx) {
    const { n, esc, colorAttr, tip, hit } = ctx;
    const g = shapeGeom(m, ctx);
    const plain = m.color === undefined;
    const c = plain ? { cls: '', style: '' } : colorAttr(m.color, 'fill');
    const fop = m.opacity !== undefined ? Number(m.opacity) : plain ? 1 : 0.28;
    const drag = m.draggable ? ` data-drag="${esc(m.id || '')}" data-index="0"` : '';
    const label = m.label === undefined ? '' : String(m.label);
    const extra = `${m.dashed ? ' stroke-dasharray="5 4"' : ''}${m.width !== undefined ? ` stroke-width="${Number(m.width)}"` : ''}`;
    const common = `class="st-node${plain ? ' is-plain' : ''} ${attrs(c)} fill-opacity="${n(fop)}"${extra}${drag}${tip(label || m.id || m.shape)}${hit(m, 0, 'node')}`;
    let out;
    if (g.shape === 'box') out = `<rect ${common} x="${n(g.cx - g.hw)}" y="${n(g.cy - g.hh)}" width="${n(g.hw * 2)}" height="${n(g.hh * 2)}" rx="${n(Math.min(6, g.hh))}"/>`;
    else if (g.shape === 'diamond') out = `<polygon ${common} points="${n(g.cx)},${n(g.cy - g.hh)} ${n(g.cx + g.hw)},${n(g.cy)} ${n(g.cx)},${n(g.cy + g.hh)} ${n(g.cx - g.hw)},${n(g.cy)}"/>`;
    else out = `<ellipse ${common} cx="${n(g.cx)}" cy="${n(g.cy)}" rx="${n(g.hw)}" ry="${n(g.hh)}"/>`;
    if (label) out += `<text class="st-label" x="${n(g.cx)}" y="${n(g.cy)}" text-anchor="middle" dy="0.35em">${esc(cut(label, labelRoom(g)))}</text>`;
    return out;
  }

  // How many label characters fit inside a shape (longer labels are cut with …); a diamond has less room.
  function labelRoom(g) {
    return Math.max(3, Math.floor(g.hw * (g.shape === 'diamond' ? 1.2 : 1.8) / 6.5));
  }

  // A line (arrow by default) from one shape to another, starting and ending on their outlines. curve bends it
  // (-1..1); a link from a shape to itself is a loop above the shape.
  function link(m, geoms, ctx) {
    const lg = linkGeom(m, geoms, ctx.n);
    if (!lg) return '';
    const { n, esc, colorAttr } = ctx;
    const col = colorAttr(m.color === undefined ? 'gray' : m.color, 'stroke');
    const directed = m.directed !== false;
    const { d, lx, ly } = lg;
    const extra = `${m.dashed ? ' stroke-dasharray="5 4"' : ''}${m.opacity !== undefined ? ` stroke-opacity="${Number(m.opacity)}"` : ''}`;
    let out = `<path class="st-link ${attrs(col)} d="${d}" stroke-width="${m.width !== undefined ? Number(m.width) : 1.8}"${extra}${directed ? ` marker-end="url(#${ctx.arrowId})"` : ''}/>`;
    if (m.label !== undefined && m.label !== '') out += `<text class="st-small" x="${n(lx)}" y="${n(ly)}" text-anchor="middle" dy="-0.4em">${esc(String(m.label))}</text>`;
    return out;
  }

  // A link's path and label point (px), or null when an end isn't a known shape. Shared by link() and the checker.
  function linkGeom(m, geoms, n = (v) => v) {
    const a = geoms.get(m.from), b = geoms.get(m.to);
    if (!a || !b) return null;
    let d, lx, ly, path = null;
    if (a === b) {
      const top = a.cy - a.hh * 3;
      const [x1, y1] = edgePoint(a, a.cx - a.hw * 0.7, top), [x2, y2] = edgePoint(a, a.cx + a.hw * 0.7, top);
      const k = a.hh * 2.4;
      d = `M${n(x1)},${n(y1)} C${n(x1 - a.hw * 0.3)},${n(y1 - k)} ${n(x2 + a.hw * 0.3)},${n(y2 - k)} ${n(x2)},${n(y2)}`;
      lx = a.cx; ly = a.cy - a.hh - k * 0.85;
    } else {
      const L = Math.hypot(b.cx - a.cx, b.cy - a.cy) || 1;
      const bend = Number.isFinite(m.curve) ? m.curve : 0;
      const qx = (a.cx + b.cx) / 2 - (b.cy - a.cy) / L * L * bend * 0.5, qy = (a.cy + b.cy) / 2 + (b.cx - a.cx) / L * L * bend * 0.5;
      const [x1, y1] = edgePoint(a, qx, qy), [x2, y2] = edgePoint(b, qx, qy);
      d = `M${n(x1)},${n(y1)} Q${n(qx)},${n(qy)} ${n(x2)},${n(y2)}`;
      lx = 0.25 * x1 + 0.5 * qx + 0.25 * x2; ly = 0.25 * y1 + 0.5 * qy + 0.25 * y2;
      path = { x1, y1, qx, qy, x2, y2 };  // the quadratic curve, so the checker can trace where the link runs
      // Added Nov 2 (rubric eval row 14): a label on a short link between close shapes sat on them ('(ŷ−y)²' squeezed
      // into a 20 px gap). It moves above the two shapes (or below them) when that spot is free of every shape.
      const text = m.label === undefined || m.label === null ? '' : String(m.label);
      if (text) {
        const w = text.length * 6.5, box = (y) => ({ x0: lx - w / 2, x1: lx + w / 2, y0: y - 14, y1: y - 2 });
        const hits = (bx) => [...geoms.values()].some((g) => Math.min(bx.x1, g.cx + g.hw) - Math.max(bx.x0, g.cx - g.hw) > 2 &&
          Math.min(bx.y1, g.cy + g.hh) - Math.max(bx.y0, g.cy - g.hh) > 2);
        if (hits(box(ly))) {
          const free = [Math.min(a.cy - a.hh, b.cy - b.hh), Math.max(a.cy + a.hh, b.cy + b.hh) + 15].find((y) => !hits(box(y)));
          if (free !== undefined) ly = free;
        }
      }
    }
    return { d, lx, ly, self: a === b, path };
  }

  // Where a text mark is drawn: { x, y, anchor } in px (y before the 0.4em lift). A label that would cross the plot's
  // edge is moved the least it takes to sit inside (seen Oct 31: curve names centred near the left or top edge, and
  // labels following a moving point). Width is estimated at ~8 px per character (14px/600); the checker uses the same.
  // Added in Explainer Studio (not in the ai-playground experiment).
  const TEXT_CHAR_W = 8;
  function placeText(m, f) {
    const s = String(m.text == null ? '' : m.text), w = s.length * TEXT_CHAR_W, { plot } = f;
    let x = f.sx(m.x), y = f.sy(m.y);
    let anchor = m.align === 'left' ? 'start' : m.align === 'right' ? 'end' : 'middle';
    if (!Number.isFinite(x) || !Number.isFinite(y)) return { x, y, anchor, w };
    const x0 = anchor === 'start' ? x : anchor === 'end' ? x - w : x - w / 2;
    if (w <= plot.x1 - plot.x0 - 4) {
      if (x0 < plot.x0) { anchor = 'start'; x = plot.x0 + 2; }
      else if (x0 + w > plot.x1) { anchor = 'end'; x = plot.x1 - 2; }
    }
    // drawn raised 0.4em (5.6 px): the glyphs run from about y - 16.6 to y - 1.6
    if (y - 16.6 < plot.y0) y = plot.y0 + 17.6;
    else if (y - 1.6 > plot.y1) y = plot.y1 + 0.6;
    return { x, y, anchor, w };
  }

  // The glyph box of a placed text mark (placeText's x/y/anchor/w), in px.
  function textBox(t) {
    const x0 = t.anchor === 'start' ? t.x : t.anchor === 'end' ? t.x - t.w : t.x - t.w / 2;
    return { x0, x1: x0 + t.w, y0: t.y - 16.6, y1: t.y - 1.6 };
  }

  // Does segment (ax, ay)-(bx, by) cross box b (shrunk by 1 px)? Liang-Barsky clipping.
  function segHitsBox(ax, ay, bx, by, b) {
    const x0 = b.x0 + 1, x1 = b.x1 - 1, y0 = b.y0 + 1, y1 = b.y1 - 1, dx = bx - ax, dy = by - ay;
    let t0 = 0, t1 = 1;
    for (const [p, q] of [[-dx, ax - x0], [dx, x1 - ax], [-dy, ay - y0], [dy, y1 - ay]]) {
      if (p === 0) { if (q < 0) return false; continue; }
      const r = q / p;
      if (p < 0) { if (r > t1) return false; if (r > t0) t0 = r; } else { if (r < t0) return false; if (r < t1) t1 = r; }
    }
    return true;
  }

  // Lines, horizontal rules and arrows a label must not sit on, as px segments.
  function lineSegments(marks, f) {
    const segs = [], { plot } = f;
    for (const m of marks) {
      if ((m.type === 'line' || m.type === 'area') && Array.isArray(m.points)) {
        let prev = null;
        for (const p of m.points) {
          const q = Array.isArray(p) ? [f.sx(p[0]), f.sy(p[1])] : null;
          if (q && Number.isFinite(q[0]) && Number.isFinite(q[1])) { if (prev) segs.push([prev[0], prev[1], q[0], q[1]]); prev = q; } else prev = null;
        }
      } else if (m.type === 'hline' && Number.isFinite(f.sy(m.y))) segs.push([plot.x0, f.sy(m.y), plot.x1, f.sy(m.y)]);
      else if (m.type === 'arrow') {
        const s = [f.sx(m.x1), f.sy(m.y1), f.sx(m.x2), f.sy(m.y2)];
        if (s.every(Number.isFinite)) segs.push(s);
      }
      // vline is left out: it spans the whole height, so no up/down nudge clears it (and it is usually a moving marker)
    }
    return segs;
  }

  const LABEL_NUDGE = [-6, 6, -12, 12, -18, 18, -24, 24, -30, 30, -36, 36, -42, 42, -48, 48];  // px; up first
  const MAX_NUDGED_LABELS = 12;  // more text marks are data labels, not curve names: left where they are
  // Every text mark's place: placeText, then (seen Oct 31: curve names like 'unscaled' or 'mode -2' drawn across a
  // line) nudged up or down the least it takes to clear every line, rule and earlier label, staying inside the plot.
  // Map mark -> placement; `onLine` is set when no nudge worked. Added in Explainer Studio, like placeText.
  function placeLabels(marks, f) {
    const out = new Map(), texts = marks.filter((m) => m.type === 'text' && String(m.text == null ? '' : m.text).trim());
    const segs = texts.length <= MAX_NUDGED_LABELS ? lineSegments(marks, f) : [];
    const taken = [], { plot } = f;
    const overlaps = (a, b) => Math.min(a.x1, b.x1) - Math.max(a.x0, b.x0) > 1 && Math.min(a.y1, b.y1) - Math.max(a.y0, b.y0) > 1;
    const clear = (b) => !segs.some((s) => segHitsBox(s[0], s[1], s[2], s[3], b)) && !taken.some((o) => overlaps(o, b));
    for (const m of texts) {
      let t = placeText(m, f);
      if (segs.length && Number.isFinite(t.x) && Number.isFinite(t.y) && !clear(textBox(t))) {
        const free = LABEL_NUDGE.map((dy) => ({ ...t, y: t.y + dy }))
          .find((c) => c.y - 16.6 >= plot.y0 && c.y - 1.6 <= plot.y1 && clear(textBox(c)));
        t = free || { ...t, onLine: segs.some((s) => segHitsBox(s[0], s[1], s[2], s[3], textBox(t))) };
      }
      taken.push(textBox(t));
      out.set(m, t);
    }
    return out;
  }

  // Added Nov 2 (rubric eval rows 12, 28): a series' name (opts.label) was shown only on hover, so two bar series in
  // grey and orange had nothing saying which was which. When 2+ named series (line/area/bars/points, one colour each)
  // differ in colour, a small legend is drawn in the plot corner with the fewest marks under it. A name already
  // written as a text mark is left out. Returns {items: [{m, label, color}], x, y, w, h, rowH} in px, or null.
  // components.cjs draws it; the validator's legend check counts a chart with one as named.
  const LEGEND_TYPES = new Set(['line', 'area', 'bars', 'points']);
  function seriesLegend(marks, f) {
    const one = (m) => (m.color !== undefined ? m.color : typeof m.colors === 'string' ? m.colors : Array.isArray(m.colors) ? null : 'blue');
    const all = [], keys = new Set();
    for (const m of marks) {
      if (!LEGEND_TYPES.has(m.type) || typeof m.label !== 'string' || !m.label.trim() || one(m) === null) continue;
      const key = JSON.stringify(one(m));
      if (keys.has(key)) continue;
      keys.add(key);
      all.push({ m, label: cut(m.label.trim(), 18), color: one(m) });
    }
    if (all.length < 2) return null;
    const written = new Set(marks.filter((m) => m.type === 'text').map((m) => String(m.text == null ? '' : m.text).trim().toLowerCase()));
    const items = all.filter((it) => !written.has(it.m.label.trim().toLowerCase())).slice(0, 6);
    if (!items.length) return null;
    const rowH = 16, w = 30 + Math.max(...items.map((it) => it.label.length)) * 6.6, h = items.length * rowH + 8;
    const { plot } = f;
    if (w > (plot.x1 - plot.x0) * 0.6 || h > (plot.y1 - plot.y0) * 0.6) return null;
    // what lies under a box: line pieces, dots, bars (with ~16 px above them for their values)
    const segs = lineSegments(marks, f);
    const dots = [], rects = [];
    for (const m of marks) {
      if (m.type === 'points' && Array.isArray(m.points)) for (const p of m.points) if (Array.isArray(p)) dots.push([f.sx(p[0]), f.sy(p[1])]);
      if (m.type === 'point') dots.push([f.sx(m.x), f.sy(m.y)]);
      if (m.type === 'bars' && Array.isArray(m.items)) {
        for (const p of m.items) {
          if (!Array.isArray(p)) continue;
          const x = f.sx(p[0]), y0 = f.sy(Math.max(f.dom.y[0], Math.min(0, f.dom.y[1]))), y1 = f.sy(p[1]);
          rects.push({ x0: x - 10, x1: x + 10, y0: Math.min(y0, y1) - 16, y1: Math.max(y0, y1) });
        }
      }
    }
    const cost = (b) => segs.filter((s) => segHitsBox(s[0], s[1], s[2], s[3], b)).length * 3 +
      dots.filter(([x, y]) => x > b.x0 - 4 && x < b.x1 + 4 && y > b.y0 - 4 && y < b.y1 + 4).length +
      rects.filter((r) => Math.min(r.x1, b.x1) > Math.max(r.x0, b.x0) && Math.min(r.y1, b.y1) > Math.max(r.y0, b.y0)).length * 5;
    const top = plot.y0 + 6;
    const corners = [[plot.x1 - w - 6, top], [plot.x0 + 6, top], [plot.x1 - w - 6, plot.y1 - h - 6], [plot.x0 + 6, plot.y1 - h - 6]];
    let best = null;
    for (const [x, y] of corners) {
      const c = cost({ x0: x, x1: x + w, y0: y, y1: y + h });
      if (!best || c < best.c) best = { x, y, c };
    }
    return { items, x: best.x, y: best.y, w, h, rowH };
  }

  const structures = {
    graph, tree, matrices, sequences, grid, gridExtent, cellAt, shape, shapeGeom, edgePoint, labelRoom, cut, fitText, fitNum, link, linkGeom,
    W, H, MARGIN, STRUCT, AXISLESS, heatExtent, autoDomain, chartFrame, zoomRange, dataExtent, equalAspect, placeText, placeLabels, textBox, TEXT_CHAR_W,
    markBox, axisNames, manualGraph, graphNameBelow, graphNameHalf, GRAPH_MIN_R, seriesLegend,
  };
  if (typeof module !== 'undefined' && module.exports) module.exports = structures;
  else root.structures = structures;
})(typeof window !== 'undefined' ? window : globalThis);

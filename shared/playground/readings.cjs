// Explainer Studio (Nov 13 2026), ours: not from the ai-playground experiment. Keep it when re-copying shared/playground/.
// Readings: what a playground really shows. Runs a spec (like the validator's dry run) at chosen settings and returns
// each readout's value and a short summary of each chart's marks, so an AI (the follow-up talk step and editor, Jev's
// text-vs-playground check) can compare what the text claims with the numbers the reader would see.
//
// read(spec, runs?) -> { runs: [{ label, readouts: {name: value}, charts: {name: [summary]} } | { label, error }] }
//   or { error } when the spec can't be compiled.
// A run is { set?: {stateKey: value}, play?: ticks, press?: {buttonId: times} }, applied from the start state like a
// reader would: set through the control's onChange, then buttons pressed, then Play ticked. Every run starts from the
// same seeded start state, so the numbers are the same each time.
// Without runs: the start, each slider at its min and max, each select option, each toggle flipped, Play after 10 and
// 60 ticks, each button pressed once and 3 times (at most MAX_RUNS).
// format(readings, maxChars) -> compact text lines for a prompt.
(function () {
  const sandbox = require('./sandbox.cjs');
  const helpers = require('./helpers.cjs');

  const INIT_SEED = 42;  // = validator.cjs / renderer.cjs
  const MAX_RUNS = 12;
  const MAX_TICKS = 60;
  const MAX_PRESSES = 5;
  const MAX_SELECT = 4;
  const SLOTS = ['onChange', 'onClick', 'onTick', 'onDrag', 'draw', 'value'];

  const isNum = (v) => typeof v === 'number' && Number.isFinite(v);
  const clone = (v) => JSON.parse(JSON.stringify(v));
  const clip = (t, n) => { const x = String(t == null ? '' : t).replace(/\s+/g, ' ').trim(); return x.length > n ? x.slice(0, n - 1) + '…' : x; };
  const num = (v) => (isNum(v) ? +v.toPrecision(4) : v);
  const nameOf = (c) => clip(c.label || c.title || c.id, 60);
  const optValue = (o) => (o !== null && typeof o === 'object' && 'value' in o ? o.value : o);
  const optLabel = (o) => (o !== null && typeof o === 'object' ? (o.label ?? o.value) : o);

  // ---------------------------------------------------------------- mark summaries

  function shapeOf(pts) {
    const ys = pts.map((p) => p[1]);
    const lo = Math.min(...ys), hi = Math.max(...ys), span = hi - lo;
    if (span <= 1e-9 * Math.max(1, Math.abs(hi))) return 'flat';
    const tol = span * 0.02;
    let up = true, down = true;
    for (let i = 1; i < ys.length; i++) {
      if (ys[i] < ys[i - 1] - tol) up = false;
      if (ys[i] > ys[i - 1] + tol) down = false;
    }
    if (up) return 'rising';
    if (down) return 'falling';
    const iMax = ys.indexOf(hi), iMin = ys.indexOf(lo), last = ys.length - 1;
    if (iMax > 0 && iMax < last && ys[0] < hi - tol && ys[last] < hi - tol) return `peaks at x=${num(pts[iMax][0])}`;
    if (iMin > 0 && iMin < last && ys[0] > lo + tol && ys[last] > lo + tol) return `dips at x=${num(pts[iMin][0])}`;
    return 'up and down';
  }

  function lineSummary(m) {
    const pts = (m.points || []).filter((p) => Array.isArray(p) && isNum(p[0]) && isNum(p[1]));
    const name = m.label ? `${m.type} "${clip(m.label, 40)}"` : m.type;
    if (!pts.length) return `${name}: empty`;
    if (pts.length === 1) return `${name}: one point (${num(pts[0][0])}, ${num(pts[0][1])})`;
    let iMin = 0, iMax = 0;
    pts.forEach((p, i) => { if (p[1] < pts[iMin][1]) iMin = i; if (p[1] > pts[iMax][1]) iMax = i; });
    const first = pts[0], last = pts[pts.length - 1];
    return `${name}: ${pts.length} points, x ${num(first[0])}..${num(last[0])}, y ${num(first[1])} → ${num(last[1])}, `
      + `min ${num(pts[iMin][1])} at x=${num(pts[iMin][0])}, max ${num(pts[iMax][1])} at x=${num(pts[iMax][0])}, ${shapeOf(pts)}`;
  }

  function range(vals) {
    const v = vals.filter(isNum);
    return v.length ? `${num(Math.min(...v))}..${num(Math.max(...v))}` : '?';
  }

  function summarize(m) {
    if (!m || typeof m !== 'object') return null;
    switch (m.type) {
      case 'line': case 'area': return lineSummary(m);
      case 'points': {
        const pts = (m.points || []).filter((p) => Array.isArray(p));
        return `${m.label ? `dots "${clip(m.label, 40)}"` : 'dots'}: ${pts.length}, x ${range(pts.map((p) => p[0]))}, y ${range(pts.map((p) => p[1]))}`;
      }
      case 'point': return `point${m.label ? ` "${clip(m.label, 40)}"` : ''} at (${num(m.x)}, ${num(m.y)})`;
      case 'bars': {
        const items = (m.items || []).slice(0, 12);
        const labels = Array.isArray(m.labels) ? m.labels : [];
        const parts = items.map((p, i) => `${clip(labels[i] ?? p[0], 24)}: ${num(p[1])}`);
        const more = (m.items || []).length > 12 ? ` (+${m.items.length - 12} more)` : '';
        return `bars${m.label ? ` "${clip(m.label, 40)}"` : ''}: ${parts.join(', ')}${more}`;
      }
      case 'hline': return `horizontal line${m.label ? ` "${clip(m.label, 40)}"` : ''} at y=${num(m.y)}`;
      case 'vline': return `vertical line${m.label ? ` "${clip(m.label, 40)}"` : ''} at x=${num(m.x)}`;
      case 'text': return `text "${clip(m.text, 80)}"`;
      case 'heatmap': case 'matrix': {
        const vals = Array.isArray(m.values) ? m.values : [];
        let lo = null, hi = null;
        vals.forEach((row, r) => (Array.isArray(row) ? row : []).forEach((v, c) => {
          if (!isNum(v)) return;
          if (!lo || v < lo[0]) lo = [v, r, c];
          if (!hi || v > hi[0]) hi = [v, r, c];
        }));
        const cols = vals[0] && Array.isArray(vals[0]) ? vals[0].length : 0;
        const where = (x) => {
          const rl = Array.isArray(m.rowLabels) ? m.rowLabels[x[1]] : null, cl = Array.isArray(m.colLabels) ? m.colLabels[x[2]] : null;
          return rl != null || cl != null ? `${clip(rl ?? x[1], 16)}/${clip(cl ?? x[2], 16)}` : `row ${x[1]}, col ${x[2]}`;
        };
        const stat = lo ? `, min ${num(lo[0])} at ${where(lo)}, max ${num(hi[0])} at ${where(hi)}` : '';
        return `${m.type} ${vals.length}x${cols}${stat}`;
      }
      case 'pie': {
        const vals = Array.isArray(m.values) ? m.values.slice(0, 8) : [];
        const labels = Array.isArray(m.labels) ? m.labels : [];
        return `pie: ${vals.map((v, i) => `${clip(labels[i] ?? i, 20)}: ${num(v)}`).join(', ')}`;
      }
      case 'table': {
        const cols = (m.columns || []).map((c) => clip(c, 20));
        const rows = (m.rows || []).slice(0, 6).map((r) => (Array.isArray(r) ? r.map((v) => clip(num(v), 20)).join(' | ') : clip(r, 60)));
        const more = (m.rows || []).length > 6 ? ` (+${m.rows.length - 6} rows)` : '';
        return `table [${cols.join(' | ')}]: ${rows.join('; ')}${more}`;
      }
      case 'drawing': {
        // Its own picture (follow-ups v4, phase 3): what it is made of, its words, and the numbers of its first shapes,
        // so a change with the controls shows up in the readings.
        const items = Array.isArray(m.items) ? m.items.filter((it) => it && typeof it === 'object') : [];
        const kinds = {};
        items.forEach((it) => { kinds[it.tag] = (kinds[it.tag] || 0) + 1; });
        const words = items.filter((it) => it.tag === 'text').slice(0, 6).map((it) => `"${clip(it.text, 30)}"`);
        const GEOM = ['x', 'y', 'width', 'height', 'cx', 'cy', 'r', 'rx', 'ry', 'x1', 'y1', 'x2', 'y2'];
        const shapes = items.filter((it) => it.tag !== 'text').slice(0, 4).map((it) => {
          const a = it.attrs || {};
          const nums = GEOM.filter((k) => typeof a[k] === 'number').map((k) => `${k}=${num(a[k])}`);
          if (Array.isArray(a.points)) nums.push(`${a.points.length} points`);
          if (typeof a.opacity === 'number') nums.push(`opacity=${num(a.opacity)}`);
          if (typeof a['stroke-width'] === 'number') nums.push(`stroke-width=${num(a['stroke-width'])}`);
          return `${it.tag}(${nums.join(' ')})`;
        });
        return `drawing${m.label ? ` "${clip(m.label, 40)}"` : ''}: ${items.length} shapes (${Object.entries(kinds).map(([k, v]) => `${v} ${k}`).join(', ')})` +
          `${words.length ? `, texts ${words.join(' ')}` : ''}${shapes.length ? `; ${shapes.join(' ')}` : ''}`;
      }
      case 'graph': return `graph: ${(m.nodes || []).length} nodes${labelsOf((m.nodes || []).map((n) => n && n.label))}`;
      case 'sequence': return `sequence: ${(m.tokens || []).slice(0, 12).map((t) => clip(t, 16)).join(' ')}`;
      case 'tree': {
        const out = [];
        const walk = (nd, d) => { if (!nd || d > 2 || out.length >= 10) return; if (nd.label != null) out.push(clip(nd.label, 24)); (nd.children || []).forEach((ch) => walk(ch, d + 1)); };
        walk(m.root, 0);
        return `tree: ${out.join(', ')}`;
      }
      case 'grid': return `grid ${(m.cells || []).length}x${(m.cells && m.cells[0] || []).length}`;
      default: return null;  // rect, arrow, shape, link: drawing, not numbers
    }
  }

  function labelsOf(list) {
    const ls = list.filter((x) => x != null && String(x).trim()).slice(0, 10).map((x) => clip(x, 16));
    return ls.length ? ` (${ls.join(', ')})` : '';
  }

  function summarizeMarks(marks) {
    const out = [];
    for (const m of Array.isArray(marks) ? marks : []) {
      if (out.length >= 10) { out.push('…'); break; }
      const s = summarize(m);
      if (s && !(m.type === 'text' && out.filter((x) => x.startsWith('text ')).length >= 6)) out.push(s);
    }
    return out;
  }

  // ---------------------------------------------------------------- running

  function compileSpec(spec) {
    if (!spec || typeof spec !== 'object' || !Array.isArray(spec.components)) throw new Error('not a playground spec');
    const fns = {};
    for (const c of spec.components) {
      if (!c || typeof c !== 'object' || typeof c.id !== 'string') continue;
      fns[c.id] = {};
      for (const s of SLOTS) if (typeof c[s] === 'string') fns[c.id][s] = sandbox.compile(c[s], s, c.id);
    }
    const init = typeof spec.init === 'string' ? sandbox.compile(spec.init, 'init', 'init') : null;
    return { fns, init };
  }

  function startState(spec, init) {
    let S = clone(spec.state || {});
    helpers.seed(INIT_SEED);
    if (init) {
      const p = init(sandbox.view(sandbox.deepFreeze(clone(S))));
      if (p && typeof p === 'object' && !Array.isArray(p)) S = Object.assign(S, clone(p));
    }
    return S;
  }

  const patched = (S, p) => (p && typeof p === 'object' && !Array.isArray(p) ? Object.assign(clone(S), clone(p)) : S);
  const frozen = (S) => sandbox.view(sandbox.deepFreeze(clone(S)));

  function runOne(spec, compiled, run) {
    const { fns, init } = compiled;
    const comps = spec.components.filter((c) => c && fns[c.id]);
    let S = startState(spec, init);
    for (const [k, v] of Object.entries(run.set || {})) {
      if (!(k in S)) throw new Error(`"${k}" is not a state key (keys: ${Object.keys(S).slice(0, 20).join(', ')})`);
      const ctl = comps.find((c) => c.key === k && fns[c.id].onChange);
      S = patched(S, ctl ? fns[ctl.id].onChange(frozen(S), v) : { [k]: v });
    }
    for (const [id, times] of Object.entries(run.press || {})) {
      const b = comps.find((c) => c.id === id && c.type === 'button');
      if (!b) throw new Error(`"${id}" is not a button`);
      if (b.action === 'reset') { S = startState(spec, init); continue; }
      if (!fns[id].onClick) continue;
      for (let i = 0; i < Math.min(MAX_PRESSES, Math.max(1, Math.floor(times) || 1)); i++) S = patched(S, fns[id].onClick(frozen(S), undefined));
    }
    const ticks = Math.min(MAX_TICKS, Math.max(0, Math.floor(run.play) || 0));
    if (ticks) {
      const p = comps.find((c) => c.type === 'play' && fns[c.id].onTick);
      if (!p) throw new Error('this playground has no Play button');
      for (let step = 0; step < ticks; step++) {
        const patch = fns[p.id].onTick(frozen(S), step);
        if (patch === null || patch === undefined) break;
        S = patched(S, patch);
      }
    }
    const readouts = {}, charts = {};
    for (const c of comps) {
      if (c.type === 'readout' && fns[c.id].value) {
        const v = fns[c.id].value(frozen(S));
        readouts[nameOf(c)] = isNum(v) ? num(v) : clip(v, 80);
      } else if (c.type === 'chart' && fns[c.id].draw) {
        charts[nameOf(c)] = summarizeMarks(fns[c.id].draw(frozen(S)));
      }
    }
    return { readouts, charts };
  }

  function defaultRuns(spec) {
    const runs = [{ label: 'at the start' }];
    for (const c of spec.components || []) {
      if (!c || typeof c !== 'object') continue;
      if (c.type === 'slider' && isNum(c.min) && isNum(c.max) && c.key) {
        runs.push({ label: `${nameOf(c)} = ${num(c.min)} (min)`, set: { [c.key]: c.min } });
        runs.push({ label: `${nameOf(c)} = ${num(c.max)} (max)`, set: { [c.key]: c.max } });
      } else if (c.type === 'select' && Array.isArray(c.options) && c.key) {
        c.options.slice(0, MAX_SELECT).forEach((o) => runs.push({ label: `${nameOf(c)} = ${clip(optLabel(o), 30)}`, set: { [c.key]: optValue(o) } }));
      } else if (c.type === 'toggle' && c.key && typeof (spec.state || {})[c.key] === 'boolean') {
        runs.push({ label: `${nameOf(c)} ${spec.state[c.key] ? 'off' : 'on'}`, set: { [c.key]: !spec.state[c.key] } });
      } else if (c.type === 'play') {
        runs.push({ label: 'after 10 Play steps', play: 10 }, { label: 'after 60 Play steps', play: 60 });
      } else if (c.type === 'button' && c.action !== 'reset') {
        runs.push({ label: `${nameOf(c)} pressed once`, press: { [c.id]: 1 } }, { label: `${nameOf(c)} pressed 3 times`, press: { [c.id]: 3 } });
      }
    }
    return runs.slice(0, MAX_RUNS);
  }

  function runLabel(run) {
    if (run.label) return clip(run.label, 80);
    const parts = Object.entries(run.set || {}).map(([k, v]) => `${k} = ${clip(JSON.stringify(v), 30)}`);
    Object.entries(run.press || {}).forEach(([id, n]) => parts.push(`${id} pressed ${n}x`));
    if (run.play) parts.push(`after ${run.play} Play steps`);
    return parts.join(', ') || 'at the start';
  }

  function read(spec, runs) {
    let compiled;
    try { compiled = compileSpec(spec); } catch (e) { return { error: e.message }; }
    const list = Array.isArray(runs) && runs.length ? runs.slice(0, MAX_RUNS) : defaultRuns(spec);
    const out = [];
    for (const r of list) {
      const run = r && typeof r === 'object' ? r : {};
      const label = runLabel(run);
      try { out.push({ label, ...runOne(spec, compiled, run) }); } catch (e) { out.push({ label, error: clip(e && e.message ? e.message : e, 200) }); }
    }
    return { runs: out };
  }

  function format(readings, maxChars = 3000) {
    if (!readings || readings.error) return `Couldn't run it: ${readings ? readings.error : 'no readings'}`;
    const lines = [];
    for (const r of readings.runs || []) {
      if (r.error) { lines.push(`- ${r.label}: error: ${r.error}`); continue; }
      lines.push(`- ${r.label}:`);
      const ro = Object.entries(r.readouts || {});
      if (ro.length) lines.push(`  readouts: ${ro.map(([k, v]) => `${k} = ${v}`).join('; ')}`);
      for (const [name, sums] of Object.entries(r.charts || {})) lines.push(`  chart "${name}": ${sums.length ? sums.join('; ') : 'nothing drawn'}`);
    }
    let text = lines.join('\n');
    if (text.length > maxChars) text = text.slice(0, maxChars - 1) + '…';
    return text;
  }

  module.exports = { read, format, defaultRuns, summarize, MAX_RUNS };
})();

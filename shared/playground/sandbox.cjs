// Copied from the ai-playground experiment (Bloging-substack/ECommerce/ai-playground), adapted for Explainer Studio's Playground mode.
// Turns slot snippet strings into functions that can only see `state`, their
// own arguments and the helper library. Real isolation comes from running the
// playground in a sandboxed iframe; this layer gives clear early errors.
(function (root) {
  const helpers = typeof module !== 'undefined' && module.exports ? require('./helpers.cjs') : root.helpers;

  // Arguments each slot receives (after `state`).
  const SLOT_ARGS = {
    init: ['state'],
    onChange: ['state', 'value'],
    onClick: ['state', 'pos', 'id', 'index', 'part'],
    onTick: ['state', 'step'],
    onDrag: ['state', 'pos', 'id', 'index'],
    draw: ['state'],
    value: ['state'],
  };

  const BANNED = ['window', 'document', 'globalThis', 'self', 'top', 'parent', 'frames', 'opener',
    'fetch', 'XMLHttpRequest', 'WebSocket', 'EventSource', 'eval', 'Function', 'import', 'require',
    'process', 'module', 'exports', 'localStorage', 'sessionStorage', 'indexedDB', 'navigator',
    'location', 'postMessage', 'setTimeout', 'setInterval', 'constructor', '__proto__', 'prototype',
    'Worker', 'importScripts', 'alert', 'confirm', 'prompt', 'open', 'Reflect', 'Proxy', 'async', 'await'];
  // Names shadowed as undefined parameters (reserved words can't be parameters).
  const SHADOW = BANNED.filter((n) => !['eval', 'import', 'async', 'await', 'constructor', '__proto__', 'prototype'].includes(n));
  const BANNED_RE = new RegExp('\\b(' + BANNED.join('|') + ')\\b');
  const HELPER_NAMES = Object.keys(helpers);
  const HELPER_VALUES = HELPER_NAMES.map((k) => helpers[k]);

  // Compile a snippet body for a slot. Throws an Error with a readable message.
  function compile(code, slot, where) {
    if (typeof code !== 'string' || !code.trim()) throw new Error(`${where}: ${slot} must be a non-empty string`);
    const bad = code.replace(/(["'`])(?:\\.|(?!\1).)*\1/g, '""').match(BANNED_RE);
    if (bad) throw new Error(`${where}: ${slot} uses forbidden name "${bad[1]}"`);
    if (!/\breturn\b/.test(code)) throw new Error(`${where}: ${slot} must use "return"`);
    let fn;
    try {
      // The block lets a snippet declare `const dist = ...` etc. without
      // clashing with the helper of the same name (helpers are parameters).
      fn = new Function(...HELPER_NAMES, ...SHADOW, ...SLOT_ARGS[slot], '"use strict";\n{\n' + code + '\n}');
    } catch (e) {
      throw new Error(`${where}: ${slot} has a syntax error: ${e.message}`);
    }
    const pad = SHADOW.map(() => undefined);
    return (...args) => fn(...HELPER_VALUES, ...pad, ...args);
  }

  // Read-only view of state that records which keys were read.
  function view(data, seen) {
    return new Proxy(data, {
      get(t, k) { if (typeof k === 'string') seen && seen.add(k); return t[k]; },
      set(t, k) { throw new Error(`state is read-only (tried to set "${String(k)}"); return a patch instead`); },
      deleteProperty(t, k) { throw new Error(`state is read-only (tried to delete "${String(k)}")`); },
    });
  }

  // Freeze values entering state so in-place edits (arr.push) fail loudly.
  function deepFreeze(v) {
    if (v && typeof v === 'object' && !Object.isFrozen(v)) {
      Object.freeze(v);
      for (const k of Object.keys(v)) deepFreeze(v[k]);
    }
    return v;
  }

  // Returns a path to the first non-finite number / unsupported value, or null.
  function findBadValue(v, path = '') {
    if (typeof v === 'number') return Number.isFinite(v) ? null : `${path || 'value'} is ${v}`;
    if (v === null || typeof v === 'string' || typeof v === 'boolean') return null;
    if (v === undefined) return `${path || 'value'} is undefined`;
    if (Array.isArray(v)) {
      for (let i = 0; i < v.length; i++) { const r = findBadValue(v[i], `${path}[${i}]`); if (r) return r; }
      return null;
    }
    if (typeof v === 'object') {
      for (const k of Object.keys(v)) { const r = findBadValue(v[k], path ? `${path}.${k}` : k); if (r) return r; }
      return null;
    }
    return `${path || 'value'} has unsupported type ${typeof v}`;
  }

  // Validate the array returned by a chart's draw(). Returns error string or null.
  const MARK_FIELDS = {
    line: ['points'], area: ['points'], points: ['points'], point: ['x', 'y'], bars: ['items'],
    hline: ['y'], vline: ['x'], rect: ['x1', 'y1', 'x2', 'y2'], arrow: ['x1', 'y1', 'x2', 'y2'], text: ['x', 'y', 'text'],
    heatmap: ['values'], pie: ['values'],
    graph: ['nodes', 'edges'], tree: ['root'], matrix: ['values'], table: ['columns', 'rows'], drawing: ['items'], sequence: ['tokens'], grid: ['cells'],
    shape: [], link: ['from', 'to'],
  };
  const isInt = (v, lo, hi) => Number.isInteger(v) && v >= lo && v < hi;
  const short = (v, max) => (typeof v === 'string' || typeof v === 'number') && String(v).length <= max;
  const rect2d = (v) => Array.isArray(v) && v.length > 0 && v.every((r) => Array.isArray(r) && r.length === v[0].length && r.length > 0);

  // The drawing mark (follow-ups v4, phase 3): only these tags and attributes, each value checked, so the runtime can
  // build the markup itself (escaped) and the model never writes markup: no scripts, links, styles, images or url().
  const DRAW_GEOM = ['x', 'y', 'width', 'height', 'cx', 'cy', 'r', 'rx', 'ry', 'x1', 'y1', 'x2', 'y2'];
  const DRAW_STYLE = ['fill', 'stroke', 'stroke-width', 'opacity', 'fill-opacity', 'stroke-opacity', 'stroke-dasharray', 'transform'];
  const DRAW_TAGS = {
    rect: ['x', 'y', 'width', 'height', 'rx', 'ry'], circle: ['cx', 'cy', 'r'], ellipse: ['cx', 'cy', 'rx', 'ry'],
    line: ['x1', 'y1', 'x2', 'y2'], polyline: ['points'], polygon: ['points'], path: ['d'],
    text: ['x', 'y', 'font-size', 'text-anchor', 'font-weight'],
  };
  const DRAW_PALETTE = ['blue', 'orange', 'green', 'red', 'purple', 'teal', 'gray', 'pink'];
  const PATH_RE = /^[MmLlHhVvCcSsQqTtAaZz0-9eE.,\s+-]*$/;
  const TRANSFORM_RE = /^(\s*(rotate|translate|scale)\(\s*-?[\d.eE+-]+(\s*[,\s]\s*-?[\d.eE+-]+){0,2}\s*\)\s*)+$/;
  function checkDrawAttr(tag, k, v, where) {
    if (DRAW_GEOM.includes(k)) return Number.isFinite(v) ? null : `${where}: ${k} must be a finite number`;
    switch (k) {
      case 'points':
        if (!Array.isArray(v) || v.length < 2 || v.length > 500 || !v.every((p) => Array.isArray(p) && p.length === 2 && p.every(Number.isFinite))) {
          return `${where}: points must be 2-500 [x, y] number pairs`;
        }
        return null;
      case 'd': return typeof v === 'string' && v.length <= 2000 && PATH_RE.test(v) && /^\s*[Mm]/.test(v) ? null
        : `${where}: d must be a path of commands (M, L, C, Q, A, Z ...) and numbers only, starting with M, at most 2000 characters`;
      case 'fill': case 'stroke':
        return Number.isInteger(v) || v === 'none' || DRAW_PALETTE.includes(v) || (typeof v === 'string' && /^#[0-9a-f]{3,8}$/i.test(v)) ? null
          : `${where}: ${k} must be a palette colour (${DRAW_PALETTE.join(', ')}), a palette index, "none" or #hex`;
      case 'stroke-width': return Number.isFinite(v) && v >= 0 && v <= 40 ? null : `${where}: stroke-width must be a number 0-40`;
      case 'opacity': case 'fill-opacity': case 'stroke-opacity':
        return Number.isFinite(v) && v >= 0 && v <= 1 ? null : `${where}: ${k} must be a number 0-1`;
      case 'stroke-dasharray': return Array.isArray(v) && v.length >= 1 && v.length <= 6 && v.every((x) => Number.isFinite(x) && x >= 0) ? null
        : `${where}: stroke-dasharray must be a list of 1-6 numbers`;
      case 'font-size': return Number.isFinite(v) && v >= 8 && v <= 40 ? null : `${where}: font-size must be a number 8-40`;
      case 'text-anchor': return ['start', 'middle', 'end'].includes(v) ? null : `${where}: text-anchor must be start, middle or end`;
      case 'font-weight': return ['normal', 'bold'].includes(v) ? null : `${where}: font-weight must be normal or bold`;
      case 'transform': return typeof v === 'string' && v.length <= 120 && TRANSFORM_RE.test(v) ? null
        : `${where}: transform may only use rotate(...), translate(...) and scale(...) with numbers`;
    }
    return `${where}: "${k}" is not allowed on ${tag} (allowed: ${[...DRAW_TAGS[tag], ...DRAW_STYLE].join(', ')})`;
  }
  function checkDrawing(m, at) {
    if (!Array.isArray(m.items) || !m.items.length || m.items.length > 300) return `${at} (drawing) needs 1-300 items made with svgEl(tag, attrs, text)`;
    for (const k of ['w', 'h']) {
      if (m[k] !== undefined && !(Number.isFinite(m[k]) && m[k] >= 10 && m[k] <= 2000)) return `${at} (drawing) ${k} is its width/height in its own units, 10-2000`;
    }
    if (m.label !== undefined && !short(m.label, 40)) return `${at} (drawing) label must be at most 40 characters`;
    for (let i = 0; i < m.items.length; i++) {
      const it = m.items[i], where = `${at} (drawing) item ${i}`;
      if (!it || typeof it !== 'object' || Array.isArray(it)) return `${where} must be svgEl(tag, attrs, text)`;
      if (!Object.prototype.hasOwnProperty.call(DRAW_TAGS, it.tag)) return `${where}: tag "${it.tag}" is not allowed (use ${Object.keys(DRAW_TAGS).join(', ')})`;
      for (const k of Object.keys(it)) if (!['tag', 'attrs', 'text'].includes(k)) return `${where}: unknown field "${k}" (use svgEl(tag, attrs, text))`;
      const attrs = it.attrs === undefined ? {} : it.attrs;
      if (!attrs || typeof attrs !== 'object' || Array.isArray(attrs)) return `${where}: attrs must be an object`;
      for (const k of Object.keys(attrs)) {
        const e = checkDrawAttr(it.tag, k, attrs[k], where);
        if (e) return e;
        if (!DRAW_TAGS[it.tag].includes(k) && !DRAW_STYLE.includes(k)) return `${where}: "${k}" is not allowed on ${it.tag} (allowed: ${[...DRAW_TAGS[it.tag], ...DRAW_STYLE].join(', ')})`;
      }
      if (it.tag === 'text') {
        if (!short(it.text, 60) || !String(it.text).trim()) return `${where}: a text item needs its words (at most 60 characters) as the third argument`;
      } else if (it.text !== undefined) return `${where}: only a text item has text`;
      if (it.tag === 'path' && attrs.d === undefined) return `${where}: a path needs d`;
      if ((it.tag === 'polyline' || it.tag === 'polygon') && attrs.points === undefined) return `${where}: a ${it.tag} needs points`;
    }
    return null;
  }

  // Structure checks for graph / tree / matrix / sequence / grid. Returns error or null.
  function checkStructure(m, at) {
    switch (m.type) {
      case 'graph': {
        if (!Array.isArray(m.nodes) || !m.nodes.length || m.nodes.length > 60) return `${at} (graph) needs 1-60 nodes`;
        if (!Array.isArray(m.edges) || m.edges.length > 400) return `${at} (graph) edges must be an array of at most 400 [from, to, weight?]`;
        const N = m.nodes.length;
        for (const nd of m.nodes) {
          if (!nd || typeof nd !== 'object') return `${at} (graph) every node must be an object like {label, layer}`;
          if (nd.label !== undefined && !short(nd.label, 12)) return `${at} (graph) node labels must be at most 12 characters`;
          if (m.layout === 'manual' && !(Number.isFinite(nd.x) && Number.isFinite(nd.y))) return `${at} (graph) layout 'manual' needs x and y on every node`;
        }
        for (const e of m.edges) {
          if (!Array.isArray(e) || !isInt(e[0], 0, N) || !isInt(e[1], 0, N)) return `${at} (graph) edge ${JSON.stringify(e)} must be [fromIndex, toIndex, weight?] with indices 0-${N - 1}`;
        }
        if (m.layout !== undefined && !['layers', 'circle', 'manual'].includes(m.layout)) return `${at} (graph) layout must be 'layers', 'circle' or 'manual'`;
        if (m.draggable && m.layout !== 'manual') return `${at} (graph) only layout 'manual' graphs can be draggable`;
        return null;
      }
      case 'tree': {
        let count = 0, depth = 0;
        const walk = (nd, d) => {
          if (!nd || typeof nd !== 'object') return 'every tree node must be an object like {label, children}';
          if (++count > 127) return 'has too many nodes (max 127)';
          depth = Math.max(depth, d);
          if (d > 8) return 'is too deep (max depth 8)';
          if (nd.label !== undefined && !short(nd.label, 16)) return 'node labels must be at most 16 characters';
          if (nd.children !== undefined && !Array.isArray(nd.children)) return 'children must be an array';
          for (const ch of nd.children || []) { const e = walk(ch, d + 1); if (e) return e; }
          return null;
        };
        const e = walk(m.root, 0);
        return e ? `${at} (tree) ${e}` : null;
      }
      case 'matrix':
        if (!rect2d(m.values)) return `${at} (matrix) values must be a non-empty 2D array with equal-length rows`;
        if (m.values.length > 12 || m.values[0].length > 12) return `${at} (matrix) is too big (max 12x12; use heatmap for larger)`;
        if (!m.values.every((r) => r.every((v) => typeof v === 'number'))) return `${at} (matrix) values must be numbers`;
        return null;
      case 'table': {
        if (!Array.isArray(m.columns) || !m.columns.length || m.columns.length > 8) return `${at} (table) needs 1-8 columns`;
        if (!m.columns.every((h) => short(h, 24))) return `${at} (table) column names must be words of at most 24 characters`;
        if (!Array.isArray(m.rows) || !m.rows.length || m.rows.length > 12) return `${at} (table) needs 1-12 rows`;
        const C = m.columns.length;
        for (const r of m.rows) {
          if (!Array.isArray(r) || r.length !== C) return `${at} (table) every row must have exactly ${C} cells, one per column`;
          for (const v of r) {
            if (typeof v === 'number' ? !Number.isFinite(v) : !short(v, 40)) return `${at} (table) cells must be finite numbers or text of at most 40 characters`;
          }
        }
        if (m.highlight !== undefined && !(m.highlight && typeof m.highlight === 'object' && (m.highlight.row === undefined || Number.isInteger(m.highlight.row)) && (m.highlight.col === undefined || Number.isInteger(m.highlight.col)))) {
          return `${at} (table) highlight must be {row, col} with whole numbers (-1 = a whole row or column)`;
        }
        return null;
      }
      case 'drawing': return checkDrawing(m, at);
      case 'sequence':
        if (!Array.isArray(m.tokens) || !m.tokens.length || m.tokens.length > 40) return `${at} (sequence) needs 1-40 tokens`;
        if (!m.tokens.every((t) => short(t, 12))) return `${at} (sequence) tokens must be strings/numbers of at most 12 characters`;
        if (m.links !== undefined && !(Array.isArray(m.links) && m.links.every((l) => Array.isArray(l) && isInt(l[0], 0, m.tokens.length) && Number.isInteger(l[1])))) {
          return `${at} (sequence) links must be [[i, j, weight], ...] from token i to token j of the next sequence`;
        }
        return null;
      case 'grid': {
        if (!rect2d(m.cells)) return `${at} (grid) cells must be a non-empty 2D array with equal-length rows`;
        const R = m.cells.length, C = m.cells[0].length;
        if (R > 30 || C > 30) return `${at} (grid) is too big (max 30x30)`;
        if (!m.cells.every((r) => r.every((v) => v === null || Number.isInteger(v)))) return `${at} (grid) cells must be integer categories (0 empty, 1 wall, 2 goal, 3 hazard, ...) or null`;
        for (const k of ['labels', 'arrows']) {
          if (m[k] !== undefined && !(rect2d(m[k]) && m[k].length === R && m[k][0].length === C)) return `${at} (grid) ${k} must be a 2D array the same shape as cells`;
        }
        if (m.arrows && !m.arrows.every((r) => r.every((a) => a === null || ['up', 'down', 'left', 'right'].includes(a)))) return `${at} (grid) arrows must be 'up', 'down', 'left', 'right' or null`;
        if (m.agents !== undefined && !(Array.isArray(m.agents) && m.agents.every((a) => a && isInt(a.r, 0, R) && isInt(a.c, 0, C)))) return `${at} (grid) agents must be [{r, c}] inside the grid`;
        return null;
      }
      case 'shape': {
        const kind = m.shape;
        if (!['circle', 'box', 'diamond'].includes(kind)) return `${at} (shape) must be a circle, box or diamond`;
        const fin = (k) => Number.isFinite(m[k]);
        if (!(fin('x') && fin('y')) && !(fin('x1') && fin('y1') && fin('x2') && fin('y2'))) {
          return `${at} (${kind}) needs a position: node(id, x, y) or ${kind}(x1, y1, x2, y2) with numbers`;
        }
        if (m.id !== undefined && !(typeof m.id === 'string' && /^[\w-]{1,32}$/.test(m.id))) return `${at} (${kind}) id must be a short word like "h1"`;
        if (m.label !== undefined && !short(m.label, 24)) return `${at} (${kind}) label must be at most 24 characters`;
        for (const k of ['r', 'w', 'h']) {
          if (m[k] !== undefined && !(Number.isFinite(m[k]) && m[k] >= 4 && m[k] <= 200)) return `${at} (${kind}) ${k} is a size in pixels between 4 and 200`;
        }
        return null;
      }
      case 'link':
        if (typeof m.from !== 'string' || typeof m.to !== 'string') return `${at} (link) from and to must be the ids of shapes in the same draw`;
        if (m.label !== undefined && !short(m.label, 16)) return `${at} (link) label must be at most 16 characters`;
        if (m.curve !== undefined && !(Number.isFinite(m.curve) && Math.abs(m.curve) <= 1)) return `${at} (link) curve must be a number between -1 and 1`;
        return null;
    }
    return null;
  }
  const isRange = (r) => Array.isArray(r) && r.length === 2 && r.every(Number.isFinite) && r[0] < r[1];
  function checkMarks(marks) {
    if (!Array.isArray(marks)) return 'draw must return an array of marks';
    if (marks.length > 400) return 'draw returned too many marks (max 400)';
    for (let i = 0; i < marks.length; i++) {
      const m = marks[i];
      const at = `mark ${i}`;
      if (!m || !MARK_FIELDS[m.type]) {
        // name the likely fix for common slips (a bare root / node list / grid)
        const hint = !m || typeof m !== 'object' ? ''
          : m.children !== undefined || m.label !== undefined ? '; it looks like a tree node: return tree(root) or treeNodes(fittedTree)'
          : m.nodes !== undefined ? '; it looks like graph data: return graph({ nodes, edges })'
          : Array.isArray(m) ? '; draw must return an array of marks, not nested arrays' : '';
        return `${at} has unknown type "${m && m.type}" (use the mark helpers${hint})`;
      }
      for (const f of MARK_FIELDS[m.type]) if (m[f] === undefined) return `${at} (${m.type}) is missing ${f}`;
      const list = m.type === 'drawing' ? null : m.points || m.items;
      if (list) {
        if (!Array.isArray(list)) return `${at} (${m.type}) points must be an array of [x, y]`;
        if (list.length > 5000) return `${at} has too many points`;
        for (let j = 0; j < list.length; j++) {
          const p = list[j];
          if (!Array.isArray(p) || p.length < 2) return `${at} (${m.type}) item ${j} must be [x, y]`;
        }
      }
      if (m.type === 'heatmap') {
        const v = m.values;
        if (!Array.isArray(v) || !v.length || !v.every((r) => Array.isArray(r) && r.length === v[0].length && r.length > 0)) {
          return `${at} (heatmap) values must be a non-empty 2D array with equal-length rows`;
        }
        if (v.length * v[0].length > 2500) return `${at} (heatmap) has too many cells (max 2500)`;
        if (!v.every((r) => r.every((x) => typeof x === 'number'))) return `${at} (heatmap) values must be numbers`;
        if ((m.x !== undefined && !isRange(m.x)) || (m.y !== undefined && !isRange(m.y))) return `${at} (heatmap) x/y must be [min, max]`;
        if (m.cellLabels !== undefined && !(Array.isArray(m.cellLabels) && m.cellLabels.every((r) => Array.isArray(r)))) return `${at} (heatmap) cellLabels must be a 2D array of names, one per cell`;
      }
      if (m.type === 'pie') {
        if (!Array.isArray(m.values) || !m.values.length || m.values.length > 24) return `${at} (pie) values must be an array of 1-24 numbers`;
        if (!m.values.every((x) => typeof x === 'number' && x >= 0)) return `${at} (pie) values must be numbers >= 0`;
        if (m.donut !== undefined && !(m.donut >= 0 && m.donut < 0.95)) return `${at} (pie) donut must be between 0 and 0.9`;
      }
      const se = checkStructure(m, at);
      if (se) return se;
      if (m.draggable && !['point', 'points', 'graph', 'grid', 'shape'].includes(m.type)) return `${at}: only point, points, shapes (node, box, circle, diamond), graph (manual layout) and grid agents can be draggable`;
      const { text, color, label, labels, xLabels, yLabels, cellLabels, id, from, to, ...rest } = m;
      const nums = m.type === 'drawing' ? {} : rest;  // a drawing's items, w and h: checkDrawing
      const bad = findBadValue(nums, m.type);
      if (bad) return `${at}: ${bad}`;
    }
    // links join shapes by id: every id must exist in this draw, once
    const ids = new Set();
    for (let i = 0; i < marks.length; i++) {
      const m = marks[i];
      if (m.type !== 'shape' || m.id === undefined) continue;
      if (ids.has(m.id)) return `mark ${i} (${m.shape}) repeats the id "${m.id}"; shape ids must be unique`;
      ids.add(m.id);
    }
    for (let i = 0; i < marks.length; i++) {
      const m = marks[i];
      if (m.type !== 'link') continue;
      for (const end of [m.from, m.to]) {
        if (!ids.has(end)) return `mark ${i} (link) joins "${end}" but no shape in this draw has that id; give the node/box/circle/diamond that id`;
      }
    }
    return null;
  }

  const sandbox = { DRAW_TAGS, DRAW_STYLE, SLOT_ARGS, HELPER_NAMES, compile, view, deepFreeze, findBadValue, checkMarks };
  if (typeof module !== 'undefined' && module.exports) module.exports = sandbox;
  else root.sandbox = sandbox;
})(typeof window !== 'undefined' ? window : globalThis);

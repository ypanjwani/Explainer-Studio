// Code Lab 7.0's look check (server/explainer/codelab.py, look_check). Reads one widget's HTML fragment on stdin, opens it
// in headless Chromium (Playwright) the way the page shows it (the same frame wrapper, helper kit and security policy, in
// the default light theme) and takes a short strip of frames, like a GIF a reviewer can read:
//   start, then each slider moved to its min and to its max (skipping the one it starts at; then back), each menu at its last option, and ~1 s after a
//   play / step / run button.
// Prints on stdout: {"frames": [{"label", "png"}], "errors": [...]} (png is base64), or {"error": "..."} when the
// browser can't run. At most MAX_FRAMES frames.
// stdin is the widget's HTML, or (follow-ups v5's look tool, Nov 18 2026) JSON {html, mode?: "light"|"dark", palette?,
// maxFrames?}: the page's own colours (read from app/src/styles/tokens.css and themes.css, as the page sets them) and
// fewer frames.
import { readFileSync } from "node:fs";

const WIDTH = 640; // the page's section column (.x-phase max-width)

const raw = await new Promise((resolve) => {
  let s = "";
  process.stdin.setEncoding("utf8");
  process.stdin.on("data", (c) => (s += c));
  process.stdin.on("end", () => resolve(s));
});
const done = (o) => { console.log(JSON.stringify(o)); process.exit(0); };
let input = { html: raw };
if (raw.trimStart().startsWith("{")) {
  try {
    const o = JSON.parse(raw);
    if (o && typeof o.html === "string") input = o;
  } catch { /* plain html that starts with "{" */ }
}
const html = input.html;
const DARK = input.mode === "dark";
const MAX_FRAMES = Math.max(1, Math.min(6, Number(input.maxFrames) || 6));

let chromium;
try {
  ({ chromium } = await import("playwright"));
} catch (e) {
  done({ error: `playwright could not be loaded: ${String(e?.message || e).slice(0, 200)}` });
}

const read = (f) => readFileSync(new URL(f, import.meta.url), "utf8");
const inline = (js) => `<script>${js.replace(/<\/script/gi, "<\\/script")}</script>`;
const KIT = read("../shared/widget-kit.js");
const VEGA = /\bXW\.vega\s*\(/.test(html)
  ? ["../node_modules/vega/build/vega.min.js", "../node_modules/vega-lite/build/vega-lite.min.js", "../shared/vega-interpreter.js"].map((f) => inline(read(f))).join("")
  : "";
// Playground mode: the playground runtime goes in after the kit, as widget.ts does (same files and order as
// app/src/render/components/playground-src.ts), and that frame's policy allows eval (the runtime compiles snippets).
const PLAYGROUND = /\bXW\.playground\s*\(/.test(html);
const PG_FILES = ["state.cjs", "helpers.cjs", "sandbox.cjs", "structures.cjs", "components.cjs", "renderer.cjs", "entry.js"];
const PG = PLAYGROUND
  ? `<style>${read("../shared/playground/components.css")}</style>` + PG_FILES.map((f) => inline(read(`../shared/playground/${f}`))).join("")
  : "";

// The page's default light theme (app/src/styles/tokens.css and the default .explainer theme in themes.css).
const THEME = {
  "--bg": "#ffffff", "--surface": "#ffffff", "--surface-2": "#f4f4f4", "--text": "#0d0d0d", "--text-2": "#424242",
  "--muted": "#6b6b6b", "--border": "#e5e5e5", "--accent": "#2563eb", "--accent-2": "#4f46e5", "--accent-soft": "#eff4ff",
  "--accent-text": "#1d4ed8", "--on-accent": "#ffffff", "--cat-0": "#2563eb", "--cat-1": "#ea580c", "--cat-2": "#0d9488",
  "--cat-3": "#c026d3", "--cat-4": "#ca8a04", "--cat-5": "#64748b", "--neg": "#2563eb", "--pos": "#ea580c",
  "--font-body": "Inter, ui-sans-serif, system-ui, sans-serif", "--font-mono": "ui-monospace, Consolas, monospace", "--radius-sm": "10px",
};
// The variables the page hands its widget frames (app/src/render/components/widget.ts THEME_VARS).
const FRAME_VARS = new Set(Object.keys(THEME));

// The page's colours for a mode and palette, layered as the page does: surface tokens (light, then dark), then the
// explainer's palette (default, then the named one), then its dark variant. A "custom" palette is set by the app from
// the reader's accent, so it falls back to the default palette. On any problem: THEME (light).
function pageTheme(dark, palette) {
  const block = (css, selector) => {
    const esc = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const m = new RegExp(`(?:^|\\n)${esc}\\s*\\{([^}]*)\\}`).exec(css);
    const out = {};
    for (const d of (m ? m[1] : "").matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)) out[d[1]] = d[2].trim();
    return out;
  };
  const tokens = read("../app/src/styles/tokens.css");
  const themes = read("../app/src/styles/themes.css");
  const named = palette && /^[\w-]+$/.test(palette) && palette !== "custom" ? `[data-theme="${palette}"]` : "";
  const layers = [
    block(tokens, ":root"),
    dark ? block(tokens, ':root[data-mode="dark"]') : {},
    block(themes, ".explainer"),
    named ? block(themes, `.explainer${named}`) : {},
    dark ? block(themes, ':root[data-mode="dark"] .explainer') : {},
    dark && named ? block(themes, `:root[data-mode="dark"] .explainer${named}`) : {},
  ];
  const out = { ...THEME };
  for (const layer of layers) for (const [k, v] of Object.entries(layer)) if (FRAME_VARS.has(k)) out[k] = v;
  if (dark && out["--bg"] === THEME["--bg"]) throw new Error("no dark colours found");
  return out;
}

const setupErrors = [];
let colours = THEME;
if (DARK || input.palette) {
  try {
    colours = pageTheme(DARK, String(input.palette || ""));
  } catch (e) {
    setupErrors.push(`page colours not read (light used): ${String(e?.message || e).slice(0, 150)}`);
  }
}
const SCHEME = DARK && colours !== THEME ? "dark" : "light";
const vars = Object.entries(colours).map(([k, v]) => `${k}:${v};`).join("");

// The same document app/src/render/components/widget.ts builds for the sandboxed frame (minus the height messages).
const doc = `<!doctype html><html><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'${PLAYGROUND ? " 'unsafe-eval'" : ""}; style-src 'unsafe-inline'; img-src data: blob:">
<style>
:root{${vars}color-scheme:${SCHEME}}
html,body{margin:0;padding:0;background:var(--bg);color:var(--text);font-family:var(--font-body),system-ui,sans-serif;font-size:15px;line-height:1.5}
body{padding:2px}
*,*::before,*::after{box-sizing:border-box}
input,button,select{font:inherit;color:inherit;accent-color:var(--accent)}
svg,canvas{max-width:100%}
</style>
${VEGA}
${inline(KIT)}
<script>(function(){var s=document.createElement("style");s.textContent=window.XW_CSS||"";document.head.appendChild(s)})();</script>
${PG}
</head><body>
${html}
</body></html>`;

let browser;
try {
  browser = await chromium.launch();
} catch (e) {
  done({ error: `the browser could not start: ${String(e?.message || e).slice(0, 200)}` });
}
const errors = [...setupErrors];
const frames = [];
try {
  const page = await browser.newPage({ viewport: { width: WIDTH, height: 700 } });
  page.on("pageerror", (e) => errors.push(String(e.message || e).slice(0, 200)));
  await page.setContent(doc, { waitUntil: "load" });
  await page.waitForTimeout(600);

  const snap = async (label) => {
    if (frames.length >= MAX_FRAMES) return;
    // The widget's own height, as the page's frame would size it (capped like the frame, 1200px).
    const h = await page.evaluate(() => Math.min(1200, Math.ceil(document.body.getBoundingClientRect().bottom) + 4));
    const png = await page.screenshot({ clip: { x: 0, y: 0, width: WIDTH, height: Math.max(80, h) }, fullPage: true });
    frames.push({ label, png: png.toString("base64") });
  };
  const describe = (el) => el.evaluate((e) => {
    const id = e.id ? `#${e.id}` : "";
    const lab = (e.labels && e.labels[0] && e.labels[0].textContent) || e.getAttribute("aria-label") || e.textContent || "";
    return `${e.tagName.toLowerCase()}${id} "${lab.replace(/\s+/g, " ").trim().slice(0, 40)}"`;
  });
  const setValue = (el, v) => el.evaluate((e, v) => {
    e.value = String(v);
    e.dispatchEvent(new Event("input", { bubbles: true }));
    e.dispatchEvent(new Event("change", { bubbles: true }));
  }, v);

  await snap("start (the reader's first view)");
  for (const el of (await page.$$('input[type="range"]')).slice(0, 2)) {
    const name = await describe(el);
    const { min, max, value } = await el.evaluate((e) => ({ min: e.min || "0", max: e.max || "100", value: e.value }));
    // A slider that starts at its min (or max) would give a frame identical to the start, which reads as "nothing changed".
    if (Number(min) !== Number(value)) { await setValue(el, min); await page.waitForTimeout(350); await snap(`slider ${name} moved from ${value} to its minimum (${min})`); }
    if (Number(max) !== Number(value)) { await setValue(el, max); await page.waitForTimeout(350); await snap(`slider ${name} moved from ${value} to its maximum (${max})`); }
    await setValue(el, value); await page.waitForTimeout(150);
  }
  for (const el of (await page.$$("select")).slice(0, 1)) {
    const name = await describe(el);
    const last = await el.evaluate((e) => (e.options.length > 1 ? e.options[e.options.length - 1].value : null));
    if (last == null) continue;
    await setValue(el, last); await page.waitForTimeout(350); await snap(`menu ${name} at its last option`);
  }
  const buttons = await page.$$("button");
  for (const b of buttons) {
    const text = (await b.evaluate((e) => e.textContent || e.getAttribute("aria-label") || "")).trim();
    if (!/play|start|run|step|next|animate|go\b|sample|resample/i.test(text)) continue;
    const name = await describe(b);
    await b.click({ timeout: 2000 }).catch(() => {});
    await page.waitForTimeout(1200);
    await snap(`about 1 s after clicking ${name}`);
    break;
  }
} catch (e) {
  errors.push(`frame capture stopped: ${String(e?.message || e).slice(0, 200)}`);
} finally {
  await browser.close().catch(() => {});
}
done({ frames, errors });

# Playground mode: code fixes from the rubric eval

Written Nov 2, 2026. This report covers every deterministic (code-only) fix built from the Playground rubric eval
(`playground-rubric-eval.md`, `playground-rubric-eval-results.md`, workbook
`C:\Users\dell\OneDrive\Desktop\Bloging-substack\playground_rubric_eval.xlsx`).

## Status at a glance

- **Built:** 20 fixes, F1 to F20. F1-F6 date from Nov 1 and F7-F20 from Nov 2.
- **Not committed.** Everything is uncommitted in the `explainer-studio` repo (`main`).
- **Not run live.** No real Playground run has used these fixes yet. The scores below are the AI judge's estimates from the mistake analysis, not new scores.
- **Tests pass:** `npm test` gives 222 passed, and the server's `pytest` gives 525 passed (`server/.venv/Scripts/python.exe -m pytest -q`).
- **All fixes are generic.** None is tied to a topic. Each was replayed on 182 playgrounds (152 saved in `server/instance/dev.db` + the 30 eval specs) before it was kept.
- **Next step:** commit, re-run the 30 eval rows (test side), and re-score them.

### Points lost to code-fixable gaps (category 1 in the eval: 44 gaps, 30.5 of the 143 points lost)

| Status | Points | Meaning |
|---|---|---|
| Fixed by code | **15.0** | the renderer or checker now draws it right by itself |
| Partly fixed | 5.0 | part of the gap is fixed |
| Depends on Haiku | 2.5 | code sends Haiku a message or offers an option; the result depends on Haiku using it |
| Still open | 3.5 | not fixed (mostly wording or titles, or couldn't reproduce) |
| Rejected on purpose | 4.5 | looked at and left out, with the reason below |

The other 112.5 lost points are not code problems. They are planning, logic and missing parts in what Haiku builds. Planning alone is 48% of all lost points and is still the biggest lever.

---

## Row by row (all 30 eval rows)

The status format is `[status fix -points] gap (note)`. The same text is in workbook sheet `Rubric eval`, columns V (`code_fix`) and W (`code_fix_points`).

| Row | Topic | Code-fixable gaps and status |
|---|---|---|
| 1 | Gradient descent and the learning rate | **fixed F8** path flies off the chart at lr>1 (-1) · **fixed F13** 'L(w)' label drawn twice (-0.5) |
| 2 | Self-attention | no code-fixable gaps |
| 3 | Precision, recall and the threshold | **depends on Haiku F18** confusion matrix has no TP/FP/FN/TN (-1.5): rows and columns were named, but the cells weren't; Haiku must use `cellLabels` · **open** data re-randomises on each load (-0.5): not reproduced, since init is seeded (`renderer.cjs`), so it may be a judging mistake · **rejected** emails chart: dots coloured per class, no legend (-1): the rule flagged 4 of 14 hand-written examples (k-means) |
| 4 | Overfitting and regularization | **fixed F8** curve shoots off the chart at degree 9+ (-1) · **depends on Haiku F2** no legend (-0.5) |
| 5 | K-means clustering | **partly F1** inertia chart stopped at iteration 4 (-1): it now keeps growing, but the missing start point isn't fixed |
| 6 | Decision tree / Gini | **depends on Haiku F11** no legend (-0.5) |
| 7 | Word embeddings / cosine | **fixed F15** arrow tip hidden under the drag handle (-0.5) (also F19: equal scales) |
| 8 | Q-learning | **fixed F1** return chart froze at 4 episodes (-1) · **fixed F15** agent dot covers the policy arrow (-0.5) |
| 9 | Diffusion forward process | **fixed F10** linear/cosine curves unlabelled (-1): Haiku had named them; a legend now shows the names |
| 10 | Softmax temperature | **fixed F9** no bar values (-0.5) · **open** logit x axis is 0-7, not words (-0.5): Haiku's choice |
| 11 | CNN filters | no code-fixable gaps |
| 12 | Dropout | **fixed F10** no legend (-0.5) · **open** big empty space (-0.5) |
| 13 | Logistic regression | **fixed F14** shading doesn't fill the axis (-0.5): y now fits the heatmap, [-3, 3] · **partly F11** odd title, no legend (-0.5): the legend is asked of Haiku, and the title isn't code-fixable |
| 14 | Backpropagation | **fixed F16** '(ŷ−y)²' label squeezed (-0.5): it was a link label between close boxes and is now drawn above them · **fixed F14** gradient bars vanish on fixed axis (-0.5): y [-8, 8] now zooms to [-2.5, 1.5] · **open** L box rounds to 0 (-0.5): Haiku's own `round(L, 2)` |
| 15 | PCA | **fixed F19** not equal-aspect, so the PCs didn't look perpendicular (-0.5) (also F15: no y numbers on the projection strip) |
| 16 | BPE tokenization | **fixed F6** word counts cut off (-0.5) |
| 17 | Positional encoding | **fixed F14** heatmap axis fixed at 48, empty at d=16 (-1): x now fits [0, d] · **fixed F12** labels overlap (-0.5): 'PE value' sat on 'dim 0' |
| 18 | Greedy vs beam search | **partly F7** tree leaves overlap (-2): nodes shrink, but Haiku's own score texts on the nodes are left to its layout repair |
| 19 | k-nearest neighbours | **open** odd title (-0.5) · **rejected** region colours don't match point colours (-1.5): too rare, 1 of 206 playgrounds |
| 20 | LoRA | **fixed F6** matrix cells cut off (-1) |
| 21 | SVM margin | **fixed F12** x2 label overlaps the line (-0.5) (also F19: equal scales) |
| 22 | Naive Bayes | **open** toggle drawn out of sync (-0.5): the toggle code (`components.cjs` toggle) looks right; needs a reproduction |
| 23 | Batch normalization | **fixed F15** meaningless y axes (-0.5) · **rejected** input axis 0-10, points cramped (-1): the dots are draggable, and a range that moves while dragging would fight the pointer |
| 24 | Mixture of experts | **fixed F3** bars hit the chart top (-0.5) |
| 25 | Weight quantization | **partly F12** labels overlap / legend misplaced (-0.5): 'w' no longer covers 'original', but 'quantized' still sits far from its dots · **open** 'Memory vs 32-bit 8' wording (-0.5) · **rejected** y range squashes ordinary weights (-0.5): the outlier uses 31% of y, and that's the lesson · **rejected** error chart's fixed axis hides 8-bit (-0.5): the range starts at 0, so 8-bit vs 2-bit size is the lesson |
| 26 | Linear regression | **fixed F15** my line and the best line overlap (-0.5): the one underneath is drawn wide and faint |
| 27 | Gradient boosting | no code-fixable gaps |
| 28 | Multi-armed bandits | **fixed F10 + F17** no legend, missing Q bars (-0.5): a p/Q legend is drawn, and Q bars at 0 are drawn as stubs |
| 29 | Cross-entropy loss | **fixed F9** no bar values (-0.5) |
| 30 | KV cache | **fixed F20** '2.05e+4 KB' not MB (-0.5): now shows "20" with the label "Cache (MB)" · **partly F6** token row unreadable at 40 tokens (-1): labels are squeezed up to 25%, but 40 is still tight |

---

## The fixes, with files and functions

All paths are relative to `explainer-studio/`. The runtime is in `shared/playground/`, loaded in this order: `state.cjs`, `helpers.cjs`, `sandbox.cjs`, `structures.cjs`, `components.cjs`, `renderer.cjs`, `entry.js`. `components.cjs` draws the page. The checker (`validator.cjs`) measures with the same `structures.cjs` geometry, so a problem it reports is one the reader would see.

### Nov 1

| Fix | What it does | Eval rows | Where |
|---|---|---|---|
| F1 | Charts whose data runs further along x with each Step/Play (a "growing" chart) are no longer narrowed by our own layout autofix. The dry run presses twice, so the autofix used to cut a loss-per-step chart to its first 3-4 points. | 5, 8 | `shared/playground/validator.cjs`, `noteGrowth()` (~line 201) and the `grows` flag in `layoutProblems` |
| F2 | 2-6 lines/areas in 2+ colours with no text mark → a soft layout message asking Haiku for a text label per line | 4 | `validator.cjs`, legend check (~line 643) |
| F3 | Bars on a fixed y range get 4% headroom above `y[1]` | 24 | `structures.cjs`, `chartFrame()` |
| F4 | Numbers 1e4-1e15 written in full with commas (262,144), axis ticks short (200k, 1.5M), no e+4 | 30 (partly) | `components.cjs`, `fmt` / `fmtTick` (top of file) |
| F5 | Dots on the plot edge drawn whole (their own clip, grown by their radius) | — | `components.cjs`, the `-dot` clipPath in `render()` |
| F6 | Tree/graph/matrix/sequence labels squeezed up to 25% (SVG `textLength`) before being cut with …; matrix/sequence numbers never shown as 0 | 16, 20, 30 | `structures.cjs`, `fitText()` / `fitNum()` |

### Nov 2

| Fix | What it does | Eval rows | Where | How often (182 playgrounds) |
|---|---|---|---|---|
| F7 | Hand-placed graph nodes (`layout: 'manual'`) shrink (down to `GRAPH_MIN_R` = 6) until circles and the names under them don't collide. What still collides, and a text mark on a node, becomes a layout message for Haiku. | 18 | `structures.cjs`, `manualGraph()`, `graphNameBelow`, `graphNameHalf`; `graph()` uses it; `validator.cjs` manual-graph check | message on row 18 only |
| F8 | A line or dots past the y range get an arrow in their colour where they leave, plus the furthest value once per edge. Straight 2-point lines (tangents) are skipped. | 1, 4 | `components.cjs`, `offEdge()` (~line 233), called in the `line` and `points` cases | first view of 5 of 152 |
| F9 | With 12 or fewer bars and no text marks, each bar shows its value. Values in a series share one style, zero bars get none, and the value goes inside the bar when there's no room. `showValues: false` hides them, `true` forces them. | 10, 29 | `components.cjs`, `barValues()` (~line 215), `bars` case, `autoBarValues` in `render()`; CSS `.mk-barval` | 79 of 103 with bars |
| F10 | 2+ named series (`label`) in different colours get a legend in the emptiest plot corner. Names already written as text marks are left out. | 9, 12, 28 | `structures.cjs`, `seriesLegend()`; `components.cjs`, `legendSvg()`; CSS `.mk-legend`, `.mk-legendtext` | 21 |
| F11 | 2+ unnamed bar series or dot sets in different colours → message asking Haiku for `label` (drawn as a legend) | 6, 13 | `validator.cjs`, legend check (~line 659) | 10 |
| F12 | The y-axis name is drawn **above** the plot (the top margin grows 10 px, `Y_NAME_ROOM`), not inside its top-left corner, where it sat on curves and text. This removed the heatmap "y-axis name on cells" message and its server autofix. | 17, 21, 25 | `structures.cjs`, `chartFrame()`; `components.cjs`, axis drawing in `render()`; `server/explainer/codelab.py` (`_PG_HEAT_YLABEL` branch removed) | every chart with a yLabel |
| F13 | An axis name that a text mark already writes word for word isn't drawn | 1 | `structures.cjs`, `axisNames()` | 7 charts |
| F14 | **Ranges follow the data in view.** A chart with a heatmap shrinks each fixed axis to what's drawn. Otherwise a y range **not starting at 0** zooms in by halves (1/4 to 1/64, on an eighth-step grid) when the data fits a quarter of it. It never zooms with `zoom: false`, draggable marks/onDrag, diagrams, flat data, or ranges from 0 (memory, cost, counts or probabilities growing with a slider are the lesson there). | 13, 14, 17 | `structures.cjs`, `zoomRange()` + `dataExtent()`, used by `chartFrame()` | 24 of 393 charts |
| F15 | Small drawing fixes: an arrow ending on a dot stops at the dot's edge (row 7). The grid agent moves to the corner of a cell with a policy arrow (row 8). A line lying exactly on another (20+ px) is drawn wide and faint underneath (row 26). Rows of dots at 1-3 heights, with no yLabel, get no y numbers or grid (rows 15, 23). | 7, 8, 15, 23, 26 | `components.cjs`: `arrow` case, `coveredLines()`, `isStrip()`; `structures.cjs`: `grid()` agents | strips 3, covered lines 5 |
| F16 | A link label that would sit on a shape moves above the two shapes (or below them) when that spot is free. The checker's message fires only when neither spot is free. | 14 | `structures.cjs`, `linkGeom()` | — |
| F17 | A bar of 0 (or under 2 px) is drawn 2 px tall, so a series still at 0 shows | 28 | `components.cjs`, `bars` case | — |
| F18 | New heatmap option `cellLabels` (2D names), drawn bold above each cell's value; checked by the sandbox; the prompt mentions it | 3 | `components.cjs`, `heatmap` case; `sandbox.cjs` heatmap check; CSS `.mk-cellname`; `prompts/prompts.ts` | depends on Haiku |
| F19 | Equal x/y scales for a plane: the same fixed range on both axes around 0 (e.g. -4..4), with plotted marks only. The axis with spare room gets more range, centred. `aspect: 'equal'` forces it and `'free'` turns it off. Lines drawn to the old range edge are stretched to the new one. These charts don't zoom. | 15 (and 7, 21) | `structures.cjs`, `equalAspect()`, used by `chartFrame()`; `components.cjs`, `toEdge` in the `line` case | 36 of 393 charts |
| F20 | A readout whose label ends in a byte unit (B/bytes/KB/MB/GB/TB) moves up units from 10,000 (1024 each, while it is 1024 or more), and the label's unit follows | 30 | `components.cjs`, `byteUnits()` + `readout()` | 4 readouts |

### Prompt changes (`prompts/prompts.ts`, exported to `server/explainer/generated/prompts.json` by `npm run export:server`)

- Mark opts: `label` says that 2+ named series get a legend, and `showValues` is described for bars.
- Chart keys: added `"zoom"?` and `"aspect"?`, with one sentence each on what happens by default.
- Heatmap opts: added `cellLabels`, "give it when the cells have names, like TP / FN / FP / TN".

---

## Rejected or not built, and why

| Idea | Why not |
|---|---|
| Message for dots coloured per class (row 3) | It flagged 4 of 14 hand-written fixtures, including k-means, whose clusters have no names. Too noisy. |
| Zoom on ranges starting at 0 | In the first replay, 15 of the 45 zoomed charts were "watch it grow" charts (memory, cost, counts). Zooming makes every state look the same height. |
| Zoom on charts with dragging (row 23) | The axis would move under the pointer. |
| Zoom when the data uses under half the range | It changed 88 of 393 charts. Data filling 30-50% of a chart reads fine. |
| A general "same text twice" rule | The 5 repeats found were real (equal scores in row 18, the word "the" twice in a token row). Only "text equals the axis name" was fixed (F13). |
| Region colours vs point colours (row 19) | Too rare: 1 of 206 playgrounds. |
| "Matrix has no row/column names" message (row 3) | Row 3's matrix had them. The cell names were missing, so F18 adds `cellLabels` instead. |

## Still remaining

**Code can't fix these (wording, titles, Haiku's choices):** row 10 (numbers instead of words on the x axis), row 13 and row 19 (odd titles), row 25 ('Memory vs 32-bit 8'), row 14 (L shown as 0, from Haiku's own `round`), row 25 ('quantized' label far from its dots), row 12 (big empty space), row 5 (no start point).

**Need a reproduction first:** row 3 (data re-randomises, though init looks seeded) and row 22 (toggle out of sync).

**Depend on Haiku's one layout repair:** rows 4, 6 and 13 (legend messages, F2/F11), row 18 (score texts on nodes, F7), row 3 (whether Haiku uses `cellLabels`, F18).

**Known side issue:** the Nov 1 legend message (F2) fires on row 21's SVM chart (two grey dashed margin lines plus the purple line count as "3 lines, nothing says which is which"). That costs a layout repair call the chart arguably doesn't need, so the rule could skip dashed guide lines that share one colour.

**Trade-offs to watch in the re-run:**
- On zoomed charts (F14), the axis numbers change as sliders move.
- Attention heatmaps always fill their chart now, so an n×n matrix shows growth through smaller cells rather than by taking up more of the chart.
- Equal-scale planes (F19) show wider x ranges than the spec asked for.

---

## Every file changed (uncommitted)

| File | Changes |
|---|---|
| `shared/playground/structures.cjs` | F3, F6, F7, F10, F12, F13, F14, F15 (grid agent), F16, F19; exports `markBox, axisNames, manualGraph, graphNameBelow, graphNameHalf, GRAPH_MIN_R, seriesLegend, zoomRange, dataExtent, equalAspect` |
| `shared/playground/components.cjs` | F4, F5, F8, F9, F10 (drawing), F12 (drawing), F15, F17, F18, F19 (`toEdge`), F20 |
| `shared/playground/components.css` | `.mk-barval`, `.mk-legend`, `.mk-legendtext`, `.mk-cellname`; pointer-events list |
| `shared/playground/validator.cjs` | F1, F2, F7 messages, F11; heatmap y-axis-name check removed (F12); `seriesLegend` call without yLabel |
| `shared/playground/sandbox.cjs` | `cellLabels` check, and `cellLabels` excluded from the numeric-value check (F18) |
| `prompts/prompts.ts` | mark opts, chart keys (`zoom`, `aspect`), heatmap `cellLabels` |
| `server/explainer/generated/prompts.json` (+ other generated files, line endings only) | regenerated by `npm run export:server`; never edit by hand |
| `server/explainer/codelab.py` | `_PG_HEAT_YLABEL` autofix + `PG_TITLE_MAX` removed (F12); `PG_LOOK_CHECKED` wording |
| `server/tests/test_playground.py` | heatmap y-label autofix test removed; layout-autofix tests now use an empty-x-range chart (`WIDE`) |
| `tests/playground.test.ts` | new describe blocks: "crowded manual graphs and lines past the y range", "bar values and series legends", "axis names", "ranges that follow the data, and small drawing fixes", "zero bars, cell names, equal scales, byte units"; updated heatmap and link-label tests |
| `tests/fixtures/playground/overfitting.json`, `confusion-matrix.json` | text names added to their lines so they pass the Nov 1 legend message (F2) |
| `.claude/rules/playground.md` | "Nov 1 fixes" and "Nov 2 fixes" parts 1-5 describe each fix for future sessions; **keep these when re-copying `shared/playground/` from the ai-playground experiment** |

## Tools and data for the next session

- **Eval workbook:** `C:\Users\dell\OneDrive\Desktop\Bloging-substack\playground_rubric_eval.xlsx`
  - Sheet `Rubric eval` has columns V-W per row.
  - Sheet `Code fixes` has one row per fix with totals.
  - Close Excel before writing. Backups are saved as `C:\Users\dell\playground_eval\backup_*.xlsx`.
- **Rewrite the workbook columns:** `python -X utf8 -I C:\Users\dell\playground_eval\tools\write_code_fixes.py`. Its `FIXES` dict lists the fixes and its `MAP` dict maps (row, gap text) to (fix, status, note). Gaps come from `write_mistakes.py` (the `G` dict).
- **Build eval pages and screenshots:** `node C:\Users\dell\playground_eval\tools\build.mjs --side test --rows 1-30`. It writes `<side>/rowNN.html`, `_before.png` and `_after.png`, plus `build_log.json` with validator layout messages.
- **Replay scripts** (Node, using the repo runtime via jsdom), in `C:\Users\dell\playground_eval\tools\replay\`. Run with `node <script> C:\Users\dell\playground_eval\replay_specs`:
  - `replay2.cjs`: legends, bar values, new legend messages
  - `replay3.cjs`: repeated text and axis names
  - `replay4.cjs`: charts that zoom in the first view
  - `replay5.cjs`: strip plots and covered lines
  - `replay6.cjs`: zoom plus equal-scale charts
- **Replay specs:** `C:\Users\dell\playground_eval\replay_specs\` holds 182 specs: `s001`-`s152` are taken from `server/instance/dev.db`, and `eval_row01`-`eval_row30` are the eval's test specs.

## Suggested next steps

1. Commit these changes in `explainer-studio` (no Claude co-author line).
2. Re-run the 30 eval rows for the test side (Playground mode, Medium, Thinking On), build screenshots with `build.mjs`, and re-score them in the workbook.
3. Compare per row against this report. In particular, check that zoom (F14) and equal scales (F19) don't hurt any row.
4. Move on to the planning gaps (48% of lost points). That's where the big gains are.

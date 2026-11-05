# Playground rubric eval: results, issues and next steps

Nov 1, 2026. This is the results write-up for the eval planned in `playground-rubric-eval.md`. That file has the original plan; this one records what was actually done, the scores, what they mean, the mistake analysis and what to do next.

## 1. TL;DR

- 30 AI topics. For each one, a **reference** playground (built by Claude) and a **test** playground (built by Playground mode: Medium effort, Thinking On, Haiku) were made from the same section summary.
- **Playground mode scores about 5-6 out of 10**: average human score 5.93, average AI score 5.23. The pages almost always work and the maths is usually right, but they're less complete than the references.
- **The AI judge agrees with the human fairly well**: MAE 1.10, within 1 point on 20 of 30 rows, correlation 0.64. It's about 0.7 points harsher and never gives 9 or 10.
- **Nearly half of the lost points (48%) come from "LLM planning"**: the builder leaves out things the summary clearly asks for. About a fifth (21%) can be fixed by code checks. Only 17% need expensive fixes. Nothing was unfixable or blocked by the spec format.
- **Next step**: a "summary coverage" check in Playground mode (list every idea in the summary, check each one is in the plan), plus cheap code checks for axes, labels and legends. Re-run the same 30 rows to measure the change.

## 2. Where everything is

| What | Where |
|---|---|
| Workbook (live) | `C:\Users\dell\OneDrive\Desktop\Bloging-substack\playground_rubric_eval.xlsx`, sheets `Rubric eval` and `Mistake summary` |
| Frozen copy of the workbook before the eval | `C:\Users\dell\playground_eval\playground_rubric_eval_FROZEN_2026-11-01.xlsx` |
| Reference pages + screenshots | `C:\Users\dell\playground_eval\ref\rowNN.html`, `rowNN_before.png`, `rowNN_after.png` |
| Test pages + specs + screenshots | `C:\Users\dell\playground_eval\test\rowNN.html`, `rowNN.json`, `_before/_after.png`, `run_log.json` (cost and steps) |
| Workbook backups (one per write) | `C:\Users\dell\playground_eval\backup_*.xlsx` |
| Scripts | `C:\Users\dell\playground_eval\tools\` (see section 8) |

The eval files live outside OneDrive because OneDrive storage is full, so the workbook links use absolute `file:///` paths.

## 3. How it was done

1. **Rubrics first.** Each row has a section summary (about 900-1,200 characters) and a 5-item rubric written from it before anything was built.
2. **Reference playgrounds.** Claude wrote one spec per row in the same JSON spec format Playground mode uses, checked with the repo's validator, and rendered to standalone HTML (`tools/build.mjs`). Specs are in `tools/ref_specs*.mjs`. Rows 17, 18, 20 and 29 were flagged by the user as doubtful and fixed.
3. **Test playgrounds.** Built by Playground mode itself (`codelab._design_playground`: Haiku write → checks → layout fix → Jev review) through `tools/run_test.py`. **The builder only saw the title and summary, never the rubric or the reference.** All 30 built and valid. Total cost **$0.103** (about $0.003 each). Repairs were needed on rows 16-19 (1 each) and 23 (2).
4. **Screenshots** for both sides follow one fixed rule: move the first slider to 75%, then press Play (else Step), wait 1.5 s.
5. **Rubric check (col N, `test_ai_check`).** Claude marked the 5 rubric items as ✓/✗ for each test page, after using every control (sliders at both ends, Step/Play, toggles, drags), not just looking at screenshots. A number the rubric asks for that the page doesn't show = ✗.
6. **Holistic scores.** The user gave a 1-10 **Human Score** + **Reason** (cols O-P). Separately and blind (without reading cols O-P), Claude gave a 1-10 **AI_score** + **AI_reason** (cols Q-R). Again every page was played with a Playwright probe script (set sliders, click buttons, drag marks, screenshot, read the readouts) and compared with the reference. Done in batches of 5.
7. **Mistake analysis (cols S-U + `Mistake summary` sheet).** Every gap from the AI reasons was given a category and an estimate of the points it cost (see section 5).

## 4. Human vs AI judge

Per-row scores, reasons and rubric checks are in the workbook (cols N-R). Rubric check average: 2.9/5. Item 5 (usually the "extra" feature such as a kernel, test mode or comparison) passed on only 7 of 30 rows.

| Measure | Value |
|---|---|
| Average human score | **5.93** |
| Average AI score | **5.23** |
| MAE | **1.10** |
| AI minus human, on average | -0.70 (AI lower on 17 rows, higher on 4, same on 9) |
| Within 1 point / within 2 points | 20 of 30 / 28 of 30 |
| Off by 3+ | row 11 (H 10, AI 7), row 12 (H 7, AI 4) |
| Pearson / Spearman correlation | 0.64 / 0.55 |
| Range used | human 3-10, AI 3-8 |

What it means:

- **The AI judge is usable.** About 1 point of disagreement on a 1-10 overall score is close to what two people would give.
- **The AI is stricter because of how it judged.** It compared against a full reference and docked points for every missing rubric item, readout or visual glitch. The human rewarded what works and how it feels to use. The gap shows mostly on the human's best pages (rows 11, 12, 4, 8, 13, 19).
- **The two agree on weak pages.** When the human gave 5 or less, the AI was usually equal or within 1.
- **The AI never gives top marks**, because every test page was missing something the reference had. That narrow range is the main reason the correlation isn't higher.
- **The AI was more generous on rows 25, 26 and 10**, where it credited correct maths that the human may have found plain.
- If the AI judge is used alone later: add about +0.7 to match the human scale, or tell it to judge as a learner would rather than against the reference.
- Caveat: the rubric check (col N) was also done by Claude, so it lines up more with the AI score (0.72) than the human score (0.54). It isn't independent evidence.

## 5. Mistake analysis: what to fix

Categories (agreed in this chat; the user's original 4, plus 2a/2b, 5 and 6 suggested by Claude):

- **1 Deterministic**: code could catch or fix it.
- **2a LLM planning**: the builder left out something the summary asks for.
- **2b LLM logic**: the builder built it wrong.
- **3 High cost**: fixable, but needs a bigger model, more thinking or more widgets.
- **5 Missing component**: the spec format can't express it.
- **6 Not in brief**: the rubric asks for something the summary doesn't mention.
- **4 Not fixable**: a true limit.

Each row's gaps were tagged with a category and the points they cost, adding up to 10 minus the AI score. The per-row gaps are in cols S-U. The counts, shares, fixes and projected averages are in the `Mistake summary` sheet. Projections there are upper bounds, since no fix catches 100%; the order of priority is the reliable part.

Findings:

1. **Planning is the biggest problem.** The builder reads the summary and builds only part of it. Examples: no test-mode switch in Dropout (12) or Batch norm (23), no sampling in Softmax (10), no words at all in Word embeddings (7), no feature scaling in k-NN (19), no new-word box in BPE (16), no running totals in KV cache (30), and the summary's worked example not used in Gradient boosting (27).
2. **A fifth is code-fixable** (see the list in section 6).
3. **There are no missing components and nothing unfixable.** The references were built in the same spec format, so everything they show is possible today. Category 5 was suggested in this chat but turned out empty.
4. **Expensive fixes are a small share.** The elbow chart (5), analogy chart (7), tree growing (6), RBF kernel (21), load balancing (24), LoRA training (20) and epsilon comparison (28) need more widgets and code. They can wait.
5. **The brief was almost always enough.** Only one gap (a forward/backward step-through in backprop, row 14) went beyond the summary.

## 6. Issues found (useful as test cases)

**Deterministic (category 1): could be caught by code**
- Axis range much wider than the data: positional-encoding heatmap fixed at 48 dims while d=16 (17); batch-norm input axis 0-10 for points in -1..3 (23); quantization y axis ±5 squashes the weights (25); logistic shading covers only part of the axis (13).
- Lines or curves leaving the chart: gradient-descent path at lr>1 (1); overfitting curve at degree 9+ (4).
- Charts that stop updating after 4 points: Q-learning return chart (8); K-means inertia chart (5).
- Labels overlapping or cut off: beam-search tree leaves pile up (18); LoRA matrix cells `-1...` (20); BPE word names `newest ...` (16); KV token boxes `to...` (30); backprop `(ŷ−y)²` squeezed (14); axis labels on top of lines or legends (17, 21, 25).
- Colours: k-NN region colours don't match point colours (19).
- Missing legends or axis labels: confusion matrix without TP/FP/FN/TN (3), emails chart (3), diffusion curves (9), dropout bars (12), bandit bars (28), overfitting (4).
- Formatting: `2.05e+4 KB` instead of MB (30), `Memory vs 32-bit 8` instead of "8x smaller" (25), L box rounds to 0 while the readout says 0.0033 (14).
- Unseeded randomness: precision/recall data changes on every load (3).
- A toggle drawn out of sync with its state (22).

**Logic (category 2b): built wrong**
- Cross-entropy: one slider sets p for whichever class is true, so switching the label can't show 0.36 vs 1.61 (29).
- LoRA toy chart shows LoRA with more parameters than full fine-tuning (20).
- BPE pair bars labelled with the joined string ("low") instead of the pair (16).
- CNN "custom" filter is just the blur filter (11).
- Bandit regret defined as expected regret, not best × pulls − reward (28).
- Overfitting test MSE silently capped at 1 (4).
- SVM support vectors and margin violators drawn the same (21).

**Planning (category 2a): left out**
- Number readouts the rubric needs: per-word attention scores (2), eigenvalues (15), |a| and |b| (7), parameter counts (20, 24), stump split values (27), KV totals (30).
- Controls the summary describes: test mode (12, 23), sampling (10), word / review picker (2, 22), new word (16), feature scaling (19), new batch / batch size (23), step-by-step buttons (5, 8, 14).

## 7. Next steps (what Claude proposes)

In priority order, each measured by re-running the same 30 rows with `run_test.py` → `build.mjs` and re-scoring:

1. **Summary coverage check (targets 2a, 48% of lost points).** Before writing the spec, have the planner list every idea, control and number in the summary (e.g. "test mode", "sampling", "worked example 10/12/30/32") and check that each maps to a widget or readout in the plan; re-plan if any are missing. It's cheap, since it's one extra short Haiku step or a checklist in the plan prompt. Measure: rubric pass rate and AI score before vs after, plus cost per page.
2. **Deterministic checks (targets category 1, 21%).** Add to the existing layout checks / `_pg_layout_autofix`: axis domain vs data extent, marks leaving the plot area, label overlap and truncation, a legend required when 2+ series share a chart, consistent colour per class across charts, charts whose data stops growing while a counter keeps rising, seeded randomness, number formatting (units, no `e+4`).
3. **Logic review (targets 2b, 12%).** Give the Jev review a short list of the logic traps above (label switch must change the loss, counts must be the right way round, named options must differ).
4. **High-cost items (17%)**: only if needed after 1-3, e.g. allow one more widget for an "extra" feature at High effort.
5. **Judge calibration**: when the human finishes any re-scoring, re-check MAE. Consider asking the AI judge to score "as a learner" to close the -0.7 gap.

Nothing in steps 1-5 has been built yet.

## 8. Scripts

All in `C:\Users\dell\playground_eval\tools\`. Workbook writers back up the workbook first and fail with `PermissionError` if Excel has it open (close Excel and re-run). Use `python -X utf8 -I` for the ✓ symbols.

| Script | What it does |
|---|---|
| `ref_specs.mjs` (+ `ref_specs_06_10.mjs` … `_26_30.mjs`) | Reference specs as JS |
| `build.mjs --side ref\|test --rows 1-30` | Validate specs, write standalone `rowNN.html`, take before/after screenshots |
| `run_test.py --rows 1-30` (repo venv python) | Build test specs with Playground mode from title + summary only |
| `write_test_cols.py` | Fill cols J-M (test spec/link/screenshots) |
| `write_ai_check.py` | Fill col N (5-item ✓/✗ marks, reasons as cell notes) |
| `write_ai_score.py` | Fill cols Q-R (AI_score, AI_reason) from the `SCORES` dict |
| `write_mistakes.py` | Fill cols S-U and the `Mistake summary` sheet from the `G` dict (gaps with category + points) |

The probe used to play pages (not in tools, kept in the session scratchpad): `node probe.mjs <html> <outPrefix> <actions>` with actions `s<i>=<v>` (slider), `sel<i>=<text>` (select), `b=<text>` (button), `t<i>` (toggle), `d=<dragId>:<index>:<dx>:<dy>` (drag a mark), `w=<ms>`, `shot=<name>` (screenshot + print readouts).

## 9. About this chat (Nov 1, 2026)

- Started after the user's human scores for rows 1-5 were in. The AI scores for rows 1-5 (8, 4, 6, 6, 5) were done in an earlier session; this session scored rows 6-30 in batches of 5.
- Rules set by the user: don't read the human score or reason; judge from the summary, rubric, reference and test page only; play every control and look at how it looks and behaves.
- For each batch: listed the controls on each test page, played them all with the probe (sliders at extremes, buttons, Play, drags, dropdowns), viewed the screenshots next to the reference's, then wrote a score and reasons. Excel being open blocked two writes; both were redone after the user closed it.
- Then computed MAE, averages and agreement (section 4). Human scores are stored as text ("9 out of 10") and were parsed for this.
- The user proposed a `mistake` column with 4 categories. Claude suggested labelling each gap (not just each row), splitting "LLM issue" into planning vs logic, and adding "missing component" and "not in brief"; the user agreed. The analysis (section 5) found that planning is the biggest lever and that the spec format isn't the bottleneck.
- Nothing in the Playground mode code was changed in this session. The labels and point splits in cols S-U are Claude's estimates and are open for the user to review.

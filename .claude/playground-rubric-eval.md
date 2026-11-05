# Playground rubric evaluation

> Results, scores, mistake analysis and next steps: see `playground-rubric-eval-results.md`.

## 1. Short description

A checklist-based way to test Playground mode. Each AI section gets a short rubric of 5 yes/no items that say what a good playground for that section must have. Two playgrounds are built for every section (a reference and a test), then a human and an AI judge both mark the rubric. The scores show (a) how good the playground model is, and (b) whether the AI judge can be trusted to replace the human.

Data file: `C:\Users\dell\OneDrive\Desktop\Bloging-substack\playground_rubric_eval.xlsx` (sheet `Rubric eval`).

## 2. What it is for and what it does

The previous evaluation (`playground_test_results_medium.xlsx`) gave each playground two 1-10 scores. Scores like that are hard to keep consistent. A per-section checklist is easier to mark the same way every time, and it points straight at what is missing (e.g. "no w1 slider", "no Train/Play button").

The method answers two separate questions. Keep them apart:

| Question | How it is answered |
|---|---|
| **How good is my playground model?** | Compare rubric scores: test playground vs reference playground |
| **Can the AI judge be trusted?** | Compare human vs AI scores on the same playground (MAE + item agreement) |

A low MAE means the AI judge agrees with the human. It does **not** mean the playgrounds are good.

Flow for each row:

1. Section summary (the input text)
2. Rubric written from the summary, **before** any playground is built
3. Reference playground built (by Claude)
4. Test playground built (by my Playground mode)
5. Human marks the 5 items for both playgrounds
6. AI judge marks the 5 items for both playgrounds
7. Scores and gaps calculated

## 3. Columns

### Done (in the workbook now)

| Column | What it holds |
|---|---|
| `#` | Row number, 1-30. One row = one section = one playground topic (built twice: reference and test) |
| `Section title` | Short topic name, e.g. "K-means clustering" |
| `section_summary` | The section text the playground must teach (about 900-1,200 characters). This is the input given to both builders |
| `rubric` | 5 numbered yes/no items, each starting with a short label, e.g. `3. Step / Play: ...` |

### To add (for the pipeline)

| Column | What it holds |
|---|---|
| `ref_spec` | Reference playground spec (JSON text). If longer than about 32,000 characters (Excel's cell limit), save it as a file and put the path here |
| `ref_link` | Link/path to the rendered reference HTML, so the human can open and click it |
| `ref_screenshot` | Optional thumbnail (or path), ideally a "before" and an "after moving a control" pair |
| `test_spec` / `test_link` / `test_screenshot` | Same three things for the test playground |
| `ref_human_items` / `ref_ai_items` | The 5 marks as a string, e.g. `11011` (1 = yes, 0 = no). Needed for item agreement |
| `test_human_items` / `test_ai_items` | Same for the test playground |
| `ref_human` / `ref_ai` / `test_human` / `test_ai` | Score 0-5 = count of 1s in the matching items string |
| `ref_abs_error` / `test_abs_error` | \|human - AI\| for that playground |
| `ref_agreement` / `test_agreement` | Share of the 5 items where human and AI gave the same mark |
| `ai_reasons` | The AI judge's one-line reason per item (useful when checking disagreements) |
| `notes` | Human notes: bugs seen, why an item failed |

## 4. What's done so far (Nov 1, 2026)

All 30 rows have a `section_summary` and a `rubric`.

| Rows | section_summary | rubric |
|---|---|---|
| 1-20 | Reused word for word from the previous test (`playground_test_results_medium.xlsx`), so new results can be compared with the old 1-10 scores | Written by Claude, reviewed by me (rows 1-2 checked first, then the rest written in the same style) |
| 21-25 | Written by Claude on new topics (SVM, Naive Bayes, batch norm, mixture of experts, weight quantization) | Written by Claude, reviewed by me |
| 26-30 | Written by me (linear regression, gradient boosting, multi-armed bandits, cross-entropy, KV cache) | Written by me; worked numbers checked and correct |

All 30 topics are different from each other (no repeated titles or summaries).

Not done yet: no playgrounds have been built and nothing has been scored.

### How the rubrics were written (keep this style for new rows)

- **5 items per row**, each a yes/no question.
- **Describe what the learner can do, not exact widgets.** Use "slider or equivalent", "(or drag)", so a different but valid design still passes.
- **At least one correctness item per row**, not only "is it there" items: numbers must match the formula, the chart must not be cut off, etc. Last round's most common bug was chart ranges cut too short (rows 1, 5, 8, 9, 12, 14), probably caused by the layout auto-fix rather than Haiku. That's why many rubrics say "stays visible on the chart, not cut off".
- **Worked numbers make correctness items sharper.** Rows 26-30 include concrete examples (e.g. points (1,2), (2,3), (3,5) give y = 1.5x + 1/3, SSE about 0.17). Rows 1-25 mostly check against the formula instead.
- Rows 1-20 deliberately target last round's gaps (e.g. backpropagation: Train/Play, w1 slider, gradients on the diagram; LoRA: actual training so the rank lesson shows).

## 5. Score calculation

**Per playground:**

```
items  = 5 marks, each 0 or 1        e.g. 11011
score  = sum of items (0-5)          e.g. 4
```

**Question 1: model quality (test vs reference)**

```
gap per row       = ref_score - test_score        (human scores)
average test      = mean of test_human over 30 rows
average reference = mean of ref_human over 30 rows
```

Also report the per-item pass rate (e.g. "correctness items pass 40% of the time") to find which kind of item the model fails most.

**Question 2: judge reliability (human vs AI)**

There are 60 judged playgrounds (30 rows x 2).

```
abs_error = |human_score - ai_score|            per playground
MAE       = mean of abs_error over all 60        (0 = perfect, scale 0-5)
agreement = matching item marks / total marks    (out of 60 x 5 = 300)
bias      = mean of (ai_score - human_score)     (+ means the AI is too lenient)
```

**Why item agreement as well as MAE:** MAE on totals can hide disagreements that cancel out. Say the human marks `10000` and the AI marks `01000`. Both scores are 1, so the error is 0, but they disagreed on 2 of 5 items (agreement 60%).

Worked example (one row):

| | Human items | AI items | Human | AI | abs_error | agreement |
|---|---|---|---|---|---|---|
| Reference | 11111 | 11111 | 5 | 5 | 0 | 5/5 |
| Test | 11010 | 11011 | 3 | 4 | 1 | 4/5 |

## 6. Other things to keep in mind

- **Same evidence for both judges.** The human tests the live HTML. If the AI only reads the spec, part of the MAE just comes from that difference. Give the AI the spec plus screenshots before and after moving a control. A spec alone misses display bugs (like cut-off charts, which happen after the spec); a screenshot alone can't show whether Play or the sliders work.
- **Hide which playground is which.** Don't tell either judge which is reference and which is test (use random labels A/B). Otherwise the judge may favour one.
- **Self-preference.** Claude builds the reference. If a Claude model is also the AI judge, it may lean toward the reference. Consider a different model as judge (e.g. Jev), or at least note it.
- **Items that check two things** (e.g. "correct math *and* not cut off"): mark yes only if **both** parts are met. Write this rule into the judge prompt.
- **Record the settings** for each test run: model, effort level (Low/Medium), cost per build, date. Last round showed Low vs Medium changes scores a lot.
- **AI judge stability:** run the AI judge 2-3 times on a few rows to check it gives the same marks. If not, its MAE is partly noise.
- **Human workload:** 30 rows x 2 playgrounds x 5 items = 300 yes/no marks. Plan for it or score a subset first (e.g. 10 rows).
- **The reference is optional for question 2.** It's useful as a ceiling and as proof that every rubric item can actually be met. If the only goal is judge reliability, skipping it halves the work.
- **Rubric is frozen before building.** Don't edit a row's rubric after seeing its playgrounds, or the reference/test will be graded against a target that moved to fit them.
- **Excel lock:** a script can't write to the workbook while it's open in Excel. Close it before running the pipeline.
- **Comparing with last round:** rows 1-20 have old scores in `playground_test_results_medium.xlsx` (Score1 = test vs reference, Score2 = test vs summary, 1-10). Useful to check whether the rubric score and the old score roughly agree.

# Follow-up review: Hypothesis Testing explainer (Nov 10 2026)

What was seen when comparing every version of one Explorer (`playground`) page made with follow-ups v3 (`FOLLOWUP_PLAN=1`).
Observations only.

- **Thread:** `7kopz39vmwaqjfoj`, "Hypothesis Testing: A Friendly Guide to Deciding If Something Is Real", in the dev
  database (`server/instance/dev.db`).
- **First prompt:** "Explain the concept of Hypothesis testing and its types in detail and keep the tone simple and easy
  to understand".
- **The page:** 6 sections:
  1. `big-idea`
  2. `two-claims`
  3. `p-value`
  4. `two-ways-wrong`
  5. `types-of-tests`
  6. `mistakes-cheatsheet`

  It has 3 playgrounds: Coin Flip Surprise (section 1), Sweep the p-value (section 3) and the Error trade-off lab
  (section 4).
- **Follow-ups:** 7 in total. They made 5 new versions (V2 to V6), and 2 ended as "checked, nothing needed changing"
  replies.

## Version by version

| Version | Follow-up | What changed | Not asked for |
|---|---|---|---|
| V2 | "check every interactive labs… if anything wrong then fix" | All 3 playgrounds edited (details below); one paragraph added to section 1 | Page design lost text size, density and emphasis |
| reply | "again check the same thing" | Nothing; shown as a reply | None |
| V3 | "check for each sections explanation itself" | One sentence in section 3 (the 100-flip coin example got p-values) | None |
| V4 | "check the whole explanation… beginner friendly" | Small wording edits in all 6 sections | None. Edits were small; numbers, quiz answers and playgrounds kept. |
| V5 | "in many places there are $..$ so correct that" | Section 2 card titles; section 3 cosmetic math edits | Section 3 edits weren't needed. The report doesn't match the change. |
| reply | "check the interactive labs are correct with the explanation" | Nothing; shown as a reply | None (but see "checks missed edge cases") |
| V6 | Long 5-part review (sweep p-value, error lab, coin lab, statistical wording, consistency) | Sections 1, 3, 4, 5 and 6 changed; section 2 kept | 2 clarity callouts added; raw `$` reintroduced |

### Details per version

**V2 (labs check)**
- **Coin Flip Surprise:**
  - The highlighted tail and both readouts changed from one-sided (`j >= heads`) to two-sided
    (`|j − 5| >= |heads − 5|`).
  - The simulated chart's fixed y range `[0, 0.342]` was removed, so it auto-scales.
  - A text paragraph was added before the widget: "Surprise can point either way… looks at both ends".
- **Sweep the p-value:** the "z obs" marker and label now sit on the side where z falls; before, they were always on the
  right. Text kept.
- **Error trade-off lab:** the x-axis label changed from "sample mean (in SD units)" to "sample mean (effect units)".
- **Top level:** the `design` lost `textSize: large`, `density: comfortable` and `emphasis: bold`. Theme, fonts and
  heading style were kept. Earlier `warnings` were cleared.

**Reply after V2:** the reply checked each lab again (22/1024 ≈ 0.0215, z = 2 and p ≈ 0.046 at the defaults, β and power
formulas) and found nothing to change.

**V3 (explanations check)**
- Sections 1, 2, 4, 5 and 6 were kept, each with a specific reason.
- In section 3, "Getting 60 heads is a bit unusual" became "… its two-sided p-value is about 0.05, right at the usual
  cut-off". The exact two-sided binomial value is ≈ 0.0569 (above 0.05), and the normal approximation is ≈ 0.0455.
- The section 5 report noted that the Mann-Whitney row is a simplification, but kept it ("accurate enough").

**V4 (beginner friendly)** changed all 6 sections, with small edits:
- **Section 1:** said what the boring explanation is for the coin, explained where "5 heads" comes from, reworded a
  flipcard front, and replaced the word "chart".
- **Section 2:** the one-sided example changed from "a drug can only help" to a fertilizer example; "signal" became
  "evidence".
- **Section 3:** defined "two-sided" and "at least this surprising", and reworded the misreading card. Its report says
  "60 heads gives… p of about 0.046" but left the text's "about 0.05, right at the cut-off".
- **Section 4:** reworded the first sentence, and the quiz explanation now covers each distractor.
- **Section 5:** glosses for spread, ranks, non-parametric and α, plus why averages look bell-shaped.
- **Section 6:** defined H₀, H₁, α, test statistic and effect size, and reworded "adjust for multiple tests".

**V5 (`$..$` fix)**
- **Section 2:** the card titles `Null hypothesis ($H_0$)` and `Alternative hypothesis ($H_1$ or $H_a$)` became H₀, H₁
  and Hₐ. This was right: card titles render as plain text (`tools.ts` `cards`, `h4.x-card-title`), so the `$` showed raw.
- **Section 3 changed with no visible effect:**
  - `$\mu_0$` became `$\mu_{0}$`;
  - "often 0.05" became "often `$0.05$`" in the callout;
  - `0.05` and `0.01` became `$0.05$` and `$0.01$` in the quiz.
- **The section 3 report is wrong twice:**
  - It says the quiz's `$\alpha$` was replaced with α, but the quiz still has `$\alpha$`.
  - It says quiz text is plain text, but the quiz question renders through `mdInline`, so its math rendered all along.
- **Sections 4, 5 and 6:** kept, correctly; their math is in fields that render it.
- **Result:** V5 is the only version with no raw `$` in plain-text fields.

**Reply after V5:** checked the 3 labs against the text and found nothing to change. It looked at the default slider
values only.

**V6 (5-part review)**
- **Section 1:** a worked-example paragraph was added before the widget (10 + 1 + 10 + 1 over 1024 = 22/1024 ≈ 0.0215).
  The playground was unchanged. The request said to verify, "do not unnecessarily change"; the editor's reason was that
  the text never stated the example.
- **Section 3:**
  - Text:
    - the z-pattern text now talks about the absolute gap |x̄ − μ₀| and √n;
    - the coin example now gives exact 0.0569 versus approximate 0.0455;
    - the p-value callout adds "fail to reject… does not show the null is true".
  - Playground:
    - a new |z| readout;
    - the p readout uses `toFixed(4)`, or e-notation below 0.0001;
    - the curve is drawn on [−L, L] with L = min(12, max(4, |z| + 0.5));
    - the chart's x range changed from [−4, 4] to [−12, 12].
  - Two callouts were added by the clarity pass ("Before you continue: standard error" and "… binomial and normal
    approximation"). They weren't requested.
- **Section 4:**
  - Text:
    - the table header and the β formula now say "fail to reject";
    - new text says the lab is a one-sided upper-tail test;
    - two new formulas: α as the right tail under H₀, and β and power under H₁;
    - a new "Reading the numbers" paragraph claims β and power show to four decimals.
  - Playground: the β and power readouts dropped `round(…, 3)` and now return the raw value.
- **Section 5:** the Mann-Whitney row now says the test compares ranks, equals a median comparison only when the shapes
  are similar, and still assumes independent groups.
- **Section 6:** "keep the null" became "fail to reject". A large p-value is now described as not showing no effect.
  Step 4 now defines the p-value. The p-value is now said to measure evidence, not effect size.
- **No "keep the null" is left** anywhere (V1 to V5 each had one "keep the null" and one "keeps the null").
- **Chatbot:** its context was rebuilt every version; in V6 it mentions 0.0569 and "fail to reject". The system prompt
  was unchanged in every version.
- **Summary:** the request asked for a summary of what changed, what was kept and what couldn't be verified. The
  revision steps give one reason per section, but nothing lists what couldn't be verified.

## Good things seen

- **Sections that need nothing are left alone, with a reason.** V3 changed 1 sentence across 6 sections, and V5 kept 3
  sections, each with a specific reason.
- **Reasons are concrete and mostly correct.** For example: "the tail counts only the high end, so 2 heads would look
  unsurprising".
- **Math added in fixes is correct.** This covers the two-sided coin probability, 0.0569 versus 0.0455, the α, β and
  power formulas for the one-sided test (SE = 1/√n, which matches the lab's σ = 1), and "two-sided = 2 × 11/1024".
- **"Check again" with nothing to fix becomes a reply, not a version.** This happened 2 out of 2 times.
- **Structure was kept throughout.** All 6 sections, all 3 playgrounds and every quiz answer survived, and the title was
  unchanged.
- **The playground edits were targeted:** only the parts named changed, and the rest of each spec was identical.

## Problems seen

1. **Design settings were lost on the first follow-up and stayed lost.**
   - V1's design had `textSize: large`, `density: comfortable` and `emphasis: bold`. V2 to V6 have none of them, so the
     page renders with default size and spacing after any follow-up.
   - The app sends only `theme`, `fontPairing`, `headingStyle` (and stage icons) with a follow-up (`designOf` in
     `shared/followup.ts`), and the follow-up pipeline uses that design.
   - `deepen._carry` carries these three fields over for Review Rounds; the follow-up path doesn't. This isn't specific
     to v3.
2. **V6 reintroduced raw `$..$` in plain-text fields.** Six spots in section 4, all `formula` captions or term meanings,
   which render as plain text (`text.ts` `formula`: `figcaption` and `dd` get the raw string):
   - "Under the null, `$\alpha$` is the right-tail probability beyond the cut-off `$c$`."
   - "the standard normal value with only `$\alpha$` of its probability above it"
   - "the right-tail probability under `$H_0$`… `$\bar{X}$` lands beyond `$c$`…"
   - "Beta and power are computed under the alternative distribution, centred at `$d$`."
   - Two more term meanings, each with `$H_1$` and `$\bar{X}$`.

   The reader had fixed exactly this kind of problem one version earlier.
3. **The error lab's "four decimal places" was not done, but the text says it was.**
   - V6 removed `round(…, 3)` from the β and power readouts.
   - The playground's readout display (`components.cjs` `fmt`) shows 3 significant digits, and uses e-notation only
     below 0.001. So power of 0.99996 still shows as "1", while a tiny β now shows in e-notation.
   - The new paragraph says "The lab shows β and power to four decimal places. A displayed 0.0000 means…", which
     disagrees with what the lab shows. The after-edit check passed it.
4. **The sweep p-value chart is less clear after the clipping fix.**
   - The x range is now always [−12, 12]. At the default z = 2, the curve is drawn on [−4, 4], about a third of the axis
     width.
   - The request said "preserve the central normal curve's visual clarity". The playground's own plan notes "The axis
     stays at ±12 because chart ranges are fixed".
5. **Lab checks looked only at default slider values.**
   - Both "nothing needed changing" replies and the V2 check missed what the V6 review named: z clipped at ±4 when the
     sliders push |z| past 4, and β or power shown as 0 or 1 when the true value is small but nonzero.
   - The reasons quote only default-state numbers ("at the default settings… z = 2 and p ≈ 0.046").
6. **A follow-up added an imprecise number, and later checks didn't catch it.**
   - V3 wrote "about 0.05, right at the usual cut-off" for 60 heads in 100 flips.
   - V4's report and the reply after V5 quoted p ≈ 0.046 (normal approximation) and left the text as it was.
   - The exact value (0.0569, above the cut-off) came in only through the reader's V6 review.
7. **Edits that weren't asked for, all small:**
   - **V5:** cosmetic math rewrites in section 3, plus a report that doesn't match the change and gets wrong how quiz
     text renders.
   - **V6:** two "Before you continue" callouts added by the clarity pass, and a worked-example paragraph added in
     section 1 when the request was to verify and not change the coin lab unnecessarily.
8. **The editor doesn't know which fields render math.** V5's report said quiz text is plain (it isn't); V6 put math in
   formula captions and term meanings (which are plain). Fields that render `$..$`:
   - text `md`, callout `md`, card `md`, table cells, quiz question and explanation, flipcard back, `breaksIf`.

   Fields that show it raw:
   - card titles, callout titles, flipcard fronts, formula captions, formula term meanings.

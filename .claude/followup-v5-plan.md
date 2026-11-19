# Follow-ups v5: version store + main file (plan, Nov 15 2026; revised Nov 16 2026)
## Goal

On every follow-up, Haiku always gets the **latest prompt, recent messages, the latest version's main file and the version
log**. It opens anything else only when needed: **any version's main file, any section or playground in any version, a
version's full changes, playground readings**. Code loads and fetches the content, Haiku decides, code and Jev check,
and each change is saved as a **new version**.

Three rules:
- **Code fetches content and Haiku only decides.** Haiku never reconstructs an old version from the log; it names a
  version and a section id, and code fetches the exact content.
- **Every change is a new version.** Editing "v4" never rewrites v4 or anything after it; the result is the next version
  number, with `parent` = the version it was made from.
- **The server owns version numbers.** The number the user sees is the number the server assigned when it saved the
  version; the app never counts versions itself.

Why v4 can't do this: the server gets only the latest page (`req["page"]`), the editor's tools all work on one `Page`
made from it, and nothing links conversation turns to version numbers or old content. Version numbers are made up by
the browser today (`app.ts`: `version = (base.version ?? 1) + 1` when a follow-up is created), so two follow-ups from
the same base both become "v4", and the server has no record of either.

**Phase 1 as built (Nov 16 2026), where it differs from the text below:**
- **Numbers are per page line, not per conversation.** A line is a page and its revisions; a new explainer or a
  new-topic page in the same conversation starts one (v1), as the app always showed. The key is the line's first record
  id (`line`). So `threads` below is `page_lines` (`user_id`, `line`, `thread`, `next_version`), and `page_versions` is
  keyed by `(user_id, line, version)`. "Unique per conversation" for section ids means unique per line.
- **The store is base code:** `server/explainer/versions.py` (next to `history.py`, which calls it), not
  `modes/followup/versions.py`. It's in `ALLOWED` in `test_modes_boundary.py`, so any follow-up version can use it, and
  deleting a follow-up version never touches it.
- **`Generation.meta`** (one JSON column) holds `{line, base, kind, number?, thread, request}` from the start and
  `version` once saved, instead of separate columns.
- **Revert endpoint:** `POST /api/explainers/<record_id>/revert {baseId, to?}`.
- **Backfill** runs when a base (or a replaced record) has no version row, from any follow-up version, not only v5.

**Phase 2 as built (Nov 16 2026):** `server/explainer/modes/followup/main_file.py`, table `section_notes` (migration 0011),
prompt `MAIN_FILE_SYSTEM`, format `mainFileNotes`. Differences from the text below:
- A section's readings (at the start only) are kept in its section note, not in a separate spec-hash cache.
- The main file is saved as JSON (`format`, `version`, header fields, `sections` with handle, id, code part, readings,
  introduces / uses / claims, and `text`, what Haiku reads), gzip-compressed in `page_versions.main_file`; its size
  counts toward the version's `size`.
- It's saved only once every section has its AI part; until then it's returned with `complete: false` and built again
  next time (only the missing sections reach the AI).
- `handle_of(main, ref)` resolves a handle or id against one version's main file.

**Phase 3 as built (Nov 16 2026, behind `FOLLOWUP_V5=1`; `FOLLOWUP_V5_FULLPAGE` on by default):** code in
`modes/followup/v5/` (talk, editor, tools, page, reply, review, base). Differences from the text below:
- **Copied, not shared:** v4's editor, tools, page and reply were copied into `v5/` and changed there, so deleting v4
  never touches v5 (`test_v5_runs_with_v3_and_v4_deleted`). `entry.py` and `run.py` now import v4 lazily too.
- **The talk step decides with a tool:** read tools, then a `decide` tool (format `followupV5Talk`, forced after 4 rounds;
  `base` / `to` 0 = not set). Prompt `FOLLOWUP_V5_TALK_SYSTEM`.
- **The base page is loaded on the server:** `v5/base.py` `follow_of` ports `shared/followup.ts` `buildFollowUp`, so
  `run.py` edits the version the server holds (any version, with its own look, playgrounds and worked steps); it falls
  back to the app's copy when the store can't give it.
- **Restore tools:** `restore_section` / `restore_playground` copy content from another version by code.
- **Review:** `v5/review.py`, a `report` tool (format `followupV5Review`, prompt `FOLLOWUP_V5_REVIEW_SYSTEM`). A
  problem whose section the fix round didn't change counts as not fixed, and its cause is put back (a removed section
  comes back where it was). Suggestions are added to the reply by code and kept in `spec.revision.suggestions` and the
  version's log.
- **"Made from version N":** `history.version_meta` takes `plan.v5.base` as the parent; the `done` event and the
  record carry `madeFrom`.
- **The app** sends `baseId` to `/api/followup/plan` and passes a revert's `to` to the revert endpoint.
- **Not yet:** the billed v4-vs-v5 comparison (`followup-live.mjs` now keeps `plan.v4` / `plan.v5`) and switching the
  default.

**Phase 4 as built (Nov 16 2026): one agent.** The talk step, its brief and the separate editor were replaced by one
Haiku agent (`v5/agent.py`), because the chain made follow-ups feel restricted: one outcome per message, a brief the
editor had to follow "exactly", an editor that never saw the conversation, and checks that put changes back instead of
telling the editor. Details in `.claude/rules/followup.md`. In short:
- `/api/followup/plan` is code only (undo, or a plan carrying the conversation); everything else runs in `/api/generate`.
- The agent sees the conversation, answers and changes in one turn, asks only before writing (later questions go in its
  reply), goes back to any version with `start_from`, and writes its own reply.
- Budget by Effort (rounds and $); effort is the reader's Effort from the first call (Nov 19 2026; was: starts Low, steps up once at the first write); thinking per page, fixed.
- The checks (Jev, readings, the review) come back as feedback at the first `finish`; a final guard puts back only what
  is still broken. Past the day's explainers it runs answer-only.
- Replaced: `decide` / `followupV5Talk`, `FOLLOWUP_V5_TALK_SYSTEM`, `FOLLOWUP_V5_EDITOR_SYSTEM` / `_LITE_SYSTEM`,
  `editor.edit_loop` / `run`, `review.review_and_fix`. §4 and §5 below describe the phase 3 design.
- **Not yet:** a billed live comparison with v3/v4 (`followup-live.mjs`).

---

## 1. Data model (new tables, Alembic migration)

**`page_versions`**: one row per version in a conversation.

| Column | Meaning |
|---|---|
| `user_id`, `thread`, `version` | Key (FK to users, cascade delete) |
| `record_id` | The explainer record this version belongs to |
| `parent` | The version it was made from (versions form a **tree**: v7 can come from v3); empty for v1 |
| `kind` | `new` (v1), `change` (a follow-up's edit) or `revert` (restore of version N, no AI) |
| `payload` | gzip-compressed JSON of the **whole page** (lossless: unpacking gives back the exact page); empty once deleted |
| `main_file` | gzip-compressed main file of this version (§3), with its format version; empty until first needed |
| `section_ids` | Every section id on this page (kept after a delete, so ids stay reserved) |
| `log` | Short entry: request, reply, changed sections with what changed, the review's suggestions |
| `deleted` | The version's record was deleted (tombstone, see below) |
| `created_at` | |

**`threads`**: one row per conversation: `user_id`, `thread` (key, FK to users, cascade delete) and `next_version`. The
next version's number comes from this counter, never from `max(version) + 1`, so a number is never reused, even after
the latest version is deleted.

**Thread of a first explainer:** a new explainer has no `thread` field in the app (only follow-ups set it, to the root's
id), so the server uses the record's own id as the thread for v1.

**Every version is a full copy**, v1 included, stored gzip-compressed (a ~63 KB page packs to roughly 10–15 KB). Versions
don't depend on each other: opening one is a read and an unpack, and a damaged row affects only that version.

**Deleted versions are tombstones:** deleting a record clears its version's `payload` and `main_file` but keeps the row
(`version`, `parent`, `log`, `section_ids`, `deleted = true`). So the version log still reads "v5: deleted", a later
version's `parent` still points somewhere, numbers aren't reused, and the ids it used stay reserved. Rows are removed
only with the account.

**What changed in a version** is worked out by code when needed, by comparing the version with its parent: sections
added, replaced or removed (by **section id**, full content), the new section order, and any changed top-level page
field (compared generically, not from a fixed list).

**`section_notes`**: each section's own description, saved once per unique content per user, so an unchanged section is
never described twice.

| Column | Meaning |
|---|---|
| `user_id`, `hash` | Key (FK to users, cascade delete, so account delete removes notes about the user's content); `hash` = sha256 of the section's content plus the format version |
| `note` | The section's own part of the main file: code part plus AI meaning (see §3) |
| `created_at` | For pruning |

**Section ids** (`st["id"]`) are what link a section across versions: positions shift when sections move and labels
change when they're renamed, so neither can be used. Haiku chooses by meaning and copies the id from the main file.

**Section ids are unique across the whole conversation, not just one page.** Versions form a tree, so a new section in
one branch must never reuse an id that meant something else in another (v4 adds `attention`, v5 removes it, v7 made
from v3 adds a different "Attention"). The used ids are the union of `section_ids` over the thread's rows, tombstones
included (no separate table). Rules:
- A kept section keeps its id in every version, so the same section matches across versions.
- A new section gets an id not used in the thread (label-based, with a suffix when taken: `attention-2`).
- A section copied back from another version ("bring back v3's chart", a restored section) keeps its original id,
  since it's the same section; if that id is already on the working page for a different section, it gets a new one.

---

## 2. Version store (code only)

New shared module, `modes/followup/versions.py`:
- `save_version(thread, record_id, parent, kind, page, log)` takes the next number from `threads.next_version`, compresses
  the whole page and writes the row, all in one transaction under the user row lock (the same lock as
  `start_generation`). Before writing, it unpacks the payload and checks it equals the page (canonical JSON: sorted
  keys). Returns the number.
- `load(thread, version)` reads the row and unpacks the page; a deleted version says so instead of returning a page.
- `used_ids(thread)` returns every section id the conversation has used (union of `section_ids`).
- `version_log(thread)` returns one line per version (number, parent, kind, request summary, which sections changed and
  how, "deleted" for tombstones).
- `change_of(thread, version)` compares the version with its parent and returns what changed, before and after content
  included; "parent deleted" when the parent is a tombstone.

**What makes a version:** only a result with a new page. An answer, a question (ask, or the v4 editor's `Asked`), a
`cannot`, a v3 `Checked` reply and an error never make a version and never use a number.

**When versions are saved:**
- **A generation that finishes with a page:** `start_generation` stores `thread`, `base` (the version it was made from;
  empty for a new explainer) and `kind` on the `Generation` row, so the version can be saved when the `done` event
  arrives, even if the browser hasn't saved the record row yet. A new explainer is v1; a follow-up is the next number
  with `parent` = `base`. It's saved from `generations.spec` before that copy is cleared (§7).
- **Revert / undo:** built in the browser today with no generation, so the server never sees it. It becomes a server
  call, `POST /api/versions/revert {thread, to}`: code copies version N as a new version (`kind: revert`, no AI, not
  counted) and returns the page and number for the record.
- **The number is returned to the app** (in the `done` event and the revert answer) and stored on the record; the app
  shows that number and no longer computes `version` itself.

**Main file:** built when first needed, not in the background: when a follow-up arrives for a version without one, or
when an old version is first opened with `get_overview`. It's saved in the row then. This puts its cost on the
follow-up that needed it (so it counts toward the user's daily cap), needs no background worker or "being built" lock,
and spends nothing on conversations that never get another follow-up. `main_file_of(thread, version)` returns it.

**Existing conversations:** backfill lazily on the first v5 follow-up in a thread, from the records' saved pages and
their app version numbers in order, registering every section id they contain and setting `threads.next_version` past
the highest one. The main file is built only when needed, as above.

**Edge cases:**
- **Two follow-ups at once in one thread** (two tabs): each gets its own number when it's saved; if both used the same
  base, they're two branches with the same parent.
- **Retry and ✏️ Edit replace a record:** the new record replaces that version's row (page, `record_id`, `section_ids`;
  main file cleared to be built again), not a new number. The old record is kept while the retry runs, so the row is
  replaced only when the retry is done; a failed retry leaves it as it was.
- **Deleting a record or account:** a record's version becomes a tombstone (§1); an account delete removes all rows.

**Invariant test:** for every saved version, `load(v)` must equal the page that was saved.

---

## 3. Main file (one per version, built when first needed, then saved)

**What it is:** a short description of one version of the page: each section's description, its playground's
readings, its key facts, its quizzes and other parts. It isn't the whole page; it's enough for Haiku to know **what is
where** and pick what to open.

**One per version:** a version's content never changes, so its main file, once saved, describes exactly that version.
The row stores the main file's format version; a saved main file in an older format is rebuilt (cheaply, through the
notes cache) the next time it's needed. Its meaning doesn't change, only its format.

**How it's used (talk step and editor):**
1. Haiku reads the main file and decides what it needs, e.g. "the `attention` section's playground".
2. It fetches the real content from the database with a read tool (`get_playground("attention")`). The main file is for
   **finding**; the real content is for **changing** and for **answering about details**. Haiku never changes a section,
   or answers about specific content (numbers, table rows, quiz answers, what a playground shows), from the main file
   alone; it opens the real content first.
3. It makes the change; code checks it and saves a **new version** (§5).
4. The new version gets its own main file (when first needed) and a new line in the version log.
5. The after-edit review (§5) reads the **new** main file with the version's changes: Haiku's one review confirms the
   request was done, finds and fixes what the edit broke, and suggests what else could be improved.

For an older version, the same steps use that version's main file (`get_overview(version)`).

New shared module, `modes/followup/main_file.py`. **Header:** page title, subtitle, level, mode and version number.
Then for each section, in order:
- **Code part (always exact):** handle and id together (`S3 · attention`), label, blurb, the list of blocks (one line
  each; a quiz shows its question), and the playground summary: controls with ranges and defaults, charts, readouts, and
  short readings at default settings.
- **AI part (meaning):** what the section introduces, uses and claims (key facts and numbers). This is today's page map
  AI part (`page_index.py`), moved to **per section**. It describes the section on its own (no neighbours in the call),
  so it stays right wherever the section is moved and can be cached by the section's content alone.

**Handles vs ids:** handles (S1, S2 …) are positions, so they differ between versions. Every read tool takes a section
**id**; a handle is accepted only as a shortcut and is resolved by code against the main file of the `version` passed
(the latest when none is), never across versions.

**No section dependency graph (for now):** the main file has no links between sections. Haiku reasons about which
sections depend on each other from the main file (what each section introduces, uses and claims) and the version's
changes, and finds passing mentions with `search_page` (§4). If the phase 3 comparison shows Haiku missing broken
sections, links can be added later behind a new main file format version.

**Building it cheaply:** a section's code part and AI part depend only on its own content, so they're cached in
`section_notes` by user and content hash. Building a main file sends only new or changed sections to one Haiku Low call;
everything else comes from the cache. Readings are cached the same way, keyed by the playground spec's hash, so no
playground is run twice.

This replaces `page_index.py`'s whole-page cache for v5; v3 and v4 keep theirs.

---

## 4. Talk step (`v5/talk.py`, `/api/followup/plan`)

**Request:** the app sends `thread` and the base version; the server loads the page from the version store instead of
trusting a page sent from the browser.

**Always sent:** the latest prompt, the conversation (`history_text` as today), the latest main file and the version log.

**Page text, behind a switch (`FOLLOWUP_V5_FULLPAGE`, decided by the comparison in phase 3):**
- **On:** the latest page's full text (`cards.page_text`, as v4) is sent too when it fits `PAGE_CHARS`; the main file is
  used for older versions and for pages that don't fit. Fewer read rounds for detail questions.
- **Off:** only the main file; details always come through the read tools.

**Open before answering:** a question about specific content (a number, a table row, a quiz answer, what a playground
shows, whether something is right) is answered only from the real content: the full page text when it was sent, or
after opening that section or playground with a read tool. Never from the main file alone. A general question the main
file fully covers ("what does S2 cover?") can be answered from it. The comparison measures how often answers need a read
round and the extra delay.

**Read tools** (short loop, at most 4 rounds, all read-only; `section` is an id, §3):
- `get_overview(version)`: that version's main file
- `get_section(section, version?)`
- `get_playground(section, version?)`
- `try_playground(section, version?, runs?)`
- `get_change(version)`
- `search_page(query, version?)`: plain text search (code, no AI) over that version's sections; returns which sections
  mention it, with a short snippet each. Used to find passing mentions the main file doesn't show ("as we saw in
  section 3", a removed section's label, a number copied from another section).
- `search_source(query)`

A deleted version answers "version N was deleted" to every tool.

**Outcomes:** answer, ask (2–4 options), cannot (+ alternative), new page, change, and **revert to any version**:
- `{kind: "revert", to: N}`: code restores version N as a new version (§2, no AI, not counted).
- `{kind: "change", brief, base: N, refs?: [versions]}`: N defaults to the latest version; `refs` names older versions
  the editor should read from.
- **Old versions, two cases:**
  - "Go back to v3 and change X" / "start from v3 as it was": `base: 3`. The later changes aren't in the result.
  - "Keep everything since v3 but fix S3" / "bring back v3's chart": `base: latest`, `refs: [3]`. The editor reads v3
    with the read tools and edits the latest page, so the later changes stay without any merging.
- If it's unclear which one the user means, Haiku **asks**: "Start from v3 as it was, or keep the changes made since?"

---

## 5. Editor (`v5/editor.py`, `/api/generate`)

- **The working page is version `base`**, loaded by code, not always the latest.
- **Read tools** are the same as the talk step's, all taking an optional `version`, so the editor can copy from any
  version ("bring back v3's chart"). The brief's `refs` tell it which older versions to look at.
- **No merging:** the editor always edits one working page. Keeping later changes means `base: latest` with an older
  version read as a reference (§4), never replaying later versions on top of an old one.
- **Write tools:** today's v4 set unchanged (edit text, blocks, sections, playgrounds). They work only on the working page.
  New section ids come from `used_ids(thread)` (§1), not just the working page and its parent as in v4's `Page.new_id`.
- **Checks:** the same as v4. Jev checks each changed section, and the text is checked against what the playground
  actually shows; a section that fails is put back.
- **After-edit review (one pass, after the checks above; replaces a separate knock-on check):**
  1. **Skip:** no review call only when the change is just the page title or the page's look (code knows from
     `change_of`). Every other change is reviewed, including any removed, renamed or moved section.
  2. **Review (one Haiku call):** it gets the request, the new version's main file and what changed (`change_of`, with
     the old content of changed and removed sections). From these it works out which unchanged sections may now be
     wrong, finds passing mentions with `search_page` (e.g. a removed section's label or key terms), opens what it
     needs with the read tools, and looks at the whole page against the request:
  - **Now wrong because of this edit** (the text names a slider the playground doesn't have, a number that no longer
    matches, a section still pointing at a removed one, or part of the request not done): each problem names the broken
    section **and the changed section that caused it** (`cause`, an id from `change_of`; empty for "part of the request
    not done"). The editor gets **one** fix round for these, and Jev checks each fix with the checks above. A fix that
    fails is put back; if a broken section can't be fixed, code puts back its `cause` section (restored from `base`). A
    broken page is never left with only a suggestion.
  - **Could be better** (e.g. the text was made beginner friendly but the playground still uses advanced terms): **not
    changed**. It becomes a suggestion in the reply ("The S2 playground still uses advanced terms. Want me to make it
    beginner friendly too?"), so the reader decides. There's no fixed number of suggestions; Haiku gives as many as it
    finds worth mentioning.
  - Only one review per follow-up; fixes from it aren't reviewed again by Haiku (Jev's checks still apply).
  - Suggestions are saved in the version's log as well as the reply, so a later "yes, do that" refers to them clearly.
- **Budget:** the read rounds, the review call and the fix round count toward the follow-up's cost; `max_usd`
  (`FOLLOWUP_MAX_USD`) and the talk step's cost estimate include them. Over budget, the review still runs but the fix
  round is skipped and the `cause` sections are put back instead.
- **Saving:** the result is a new version whose `parent` is `base`, with a log entry built from `page.log` plus the
  review's suggestions.

---

## 6. App changes (`app/src/shell/app.ts`, `shared/`)

- **Version numbers come from the server** (phase 1): the app stops computing `version = base.version + 1`; it shows the
  number in the `done` event or the revert answer. Before a follow-up finishes, it shows "Revising version N" (the base),
  not the number the result will get.
- **Undo / revert** calls `POST /api/versions/revert` instead of copying the page in the browser.
- Send `thread` and the base version in follow-up requests (phase 3); the server loads pages itself.
- Under a version made from an older one, show "Made from version 3".
- Optional later: a version list on the page that opens any version through the same `load`.

---

## 7. Storage

Today (dev DB, Nov 15 2026): a record averages ~63 KB (max ~132 KB); every version is a full copy. Limits in
`routes/history.py`: 3 MB per record, 200 MB and 2,000 records per user.

**Every page is already stored twice today:**
- `generation_event` (`history.py`) writes the finished page into `generations.spec`, uncompressed.
- `settle` / `_apply_generation` (and `save()` when a tab saves while the result is waiting) then copies it into the
  record (`explainers.data.spec`).

After that, `generations.spec` is never read again: `_apply_generation` runs only while the record is `generating`, and a
new run on the record deletes the row first (`start_generation`). The copy stays until the record or account is
deleted, and it doesn't count toward the 200 MB cap. Without a fix, v5 would make three copies per version (record ~63
KB + `generations.spec` ~63 KB + version row ~15 KB ≈ 140 KB).

- **Clear `generations.spec` once the record holds the page:**
  - **Where:** in all three places that store the finished page into the record (`status: done`), in the same
    transaction, set `gen.spec = None`: `generation_event`, `settle`, and `save()` (`history.py`, the
    "finished but not yet applied" branch). `status`, `usage` and `error` stay; they're small.
  - **Order:** the version row is saved from `generations.spec` before it's cleared.
  - **Not yet:** if the record row doesn't exist yet (the browser never saved it), `generations.spec` is the only copy.
    It's cleared later, when `settle` or `save` applies it to the record.
  - **`_apply_generation`, in the same commit:** today `done` with an empty spec matches no branch, so the record would
    stay `generating` for good. Add the branch: `done` with an empty spec means "already applied, keep the record's
    page" and sets `status: done`, never a failure.
  - **Existing rows:** a one-off cleanup clears `generations.spec` wherever the record is already `done` with a spec.
  - **Result:** one big copy per page instead of two (~63 KB saved per finished page), with no change for share links.
    With v5: record ~63 KB + version row ~15 KB ≈ 78 KB per version.
- **Version rows:** a full page per version, gzip-compressed: roughly 10–15 KB for an average page instead of ~63 KB,
  plus its compressed main file (a few KB). Tombstones are a few hundred bytes.
- **Records, later and optional:** a follow-up record could keep only `thread + version`, with the page loaded from its
  version row when opened (cached in the browser). Clearing `generations.spec` already gives most of the savings, and
  this touches the app's loading, export and share links (`/s/<id>` must keep working). Do it only if storage numbers
  call for it, and only after the invariant has held in production for a while.
- **If storage becomes the bottleneck later:** versions can switch to storing only changes from their parent, behind
  the same `save_version` / `load` / `change_of` functions, so nothing else changes.
- **Section notes:** only a cache for building main files (saved main files don't need them), so notes older than a set
  age can be pruned freely.
- **Per-user limits:** count version rows toward the existing 200 MB cap.
- Not planned unless usage demands it: thinning old versions to log-only (they could no longer be opened).

---

## 8. Code layout and rules

- Put the new code in `modes/followup/v5/` behind `FOLLOWUP_V5=1`. Only `entry.py` and `run.py` choose the version, as
  with v3 and v4.
- `versions.py` and `main_file.py` go next to the other shared follow-up files, so v4 or later versions can use them.
  The version store and server-owned numbers (phase 1) apply to every follow-up version, not only v5.
- Update `test_modes_boundary.py`: v5 never imports v3 or v4.
- New prompts go in `prompts/` → `npm run export:server` (never edit `generated/` by hand).
- Topic-neutral prompts, with examples from several AI areas.

---

## 9. Build order: three phases

### Phase 1: storage and version store (code only, no AI, no flag) — built Nov 16 2026

Ships on its own; the only user-visible change is that version numbers now come from the server.

- This doc; rules entry in `.claude/rules/followup.md`.
- **Clear `generations.spec`** in `generation_event`, `settle` and `save()`; the `_apply_generation` "done with empty
  spec" branch in the same commit; the one-off cleanup (§7).
- **Tables:** `page_versions` (with tombstones and `section_ids`) and `threads` (number counter), one Alembic migration.
- **`thread`, `base`, `kind` on the `Generation` row** at `start_generation`; the version saved on `done` before the
  spec is cleared; nothing saved for answers, questions, `cannot`, `Checked` or `Asked`.
- **Server-owned numbers:** returned in the `done` event; the app shows them and stops counting.
- **Revert endpoint** (`POST /api/versions/revert`); the app's Undo uses it.
- **Retry / Edit** replacing a version's row on success; record delete making a tombstone.
- **Code-only reading:** `load`, `used_ids`, `version_log`, `change_of`, `search_page`.
- **Lazy backfill** of existing threads.
- **Version rows counted** toward the 200 MB cap.
- Tests: §10 "Load", "`generations.spec` cleared", "Version numbers", "Changes", "Section ids", "`search_page`".

### Phase 2: main file (no flag needed, nothing reads it yet except tests) — built Nov 16 2026

- **Code part:** header, per-section lines with `handle · id`, blocks, playground summary and readings (readings cached
  by spec hash).
- **AI part:** per-section notes (context-free, no neighbours), one Haiku Low call for new or changed sections only.
- **`section_notes`** with `user_id`, content hash and format version; pruning by age.
- **Lazy build:** `main_file_of` builds when first needed and saves it in the row; an older format is rebuilt.
- Tests: §10 "Main file".

### Phase 3: v5 follow-ups (behind `FOLLOWUP_V5=1`, then the default) — built Nov 16 2026 behind the flag; the billed comparison and the switch are still to do

- **Talk step:** `thread` + base sent by the app, server-loaded page, read tools by id and version, the
  `FOLLOWUP_V5_FULLPAGE` switch, revert to any version, the "as it was or keep the changes since?" question.
- **Editor:** working page = `base`, `refs`, version-aware reads, new ids from `used_ids`, new version with `parent`,
  "Made from version N" in the app.
- **After-edit review:** one call with `cause` per problem, one fix round, put-back rules, suggestions in the reply and
  the log, budget update.
- **Compare and switch:** v4 vs v5 (both settings of the full-page switch) with `followup-live.mjs` and the plan eval:
  quality, cost, delay, read rounds, missed broken sections; then make v5 the default.
- Tests: §10 "Talk", "Editor", "After-edit review", "Finding broken sections", "Open before answering", "Boundary",
  "Live".

Later, not a phase: measure storage and slim records to `thread + version` only if the numbers call for it (§7).

---

## 10. Tests

- **Load:** every version equals what was saved (canonical JSON), including branches, tombstones and replaced records.
- **`generations.spec` cleared:** it's empty once the record is `done` with the page, in all three places
  (`generation_event`, `settle`, `save()`); it stays while the record row doesn't exist yet and is cleared when the
  record gets it; a reload while generating, a stale tab's save and a retry still work; `done` with an empty spec keeps
  the record's page and status `done`; the version row is saved before the clear; the one-off cleanup clears only rows
  whose record is already `done` with a spec.
- **Version numbers:** assigned by the server when saved; two follow-ups from the same base get different numbers and
  the same parent; a deleted latest version's number is never reused; answers, questions, `cannot`, `Checked` and
  `Asked` use no number; a revert is a new number with `kind: revert`; Retry keeps its number; backfill continues after
  the app's old numbers.
- **Changes:** `change_of` reports add, remove, move, rename and page-field changes exactly, and "parent deleted" for a
  tombstone parent.
- **Section ids:** a section added in one branch never reuses an id from another (v4 adds `attention`, v5 removes it,
  v7 from v3 adds "Attention" → `attention-2`); kept and copied-back sections keep their ids; ids stay reserved after a
  delete (tombstone); backfill registers old ids.
- **Main file:** stable for the same content; only new or changed sections cause an AI call; notes are per user and
  removed with the account; a main file is built on first need and saved; an older format is rebuilt; a handle resolves
  against the version passed, never another.
- **`search_page`:** finds every section mentioning a term (labels, "section 3", numbers) in the version asked for,
  and nothing from other versions.
- **Talk:** fake-model tests for each outcome, including `revert to N`, `change with base N`, `base: latest` with
  `refs: [3]`, and the "as it was or keep the changes since?" question; a deleted version is reported, not opened.
- **Editor:** reading an old version, editing from base 3 making a new version with parent 3, and `base: latest`
  copying a section or playground from v3 while every later change stays.
- **After-edit review:** runs once; skipped only for a title-only or look-only change; a "now wrong" problem is fixed
  or its `cause` section put back; a "could be better" one is only suggested and nothing outside the request changes;
  suggestions land in the reply and the log; a later "yes, do that" uses them; over budget, the fix round is skipped
  and causes are put back.
- **Finding broken sections without a graph (live, phase 3):** removing a section that others refer to, renaming one
  referred to by its label, and moving one referred to as "the previous section": the review must find and fix (or
  put back) each broken section. Misses here are the signal to add links back (§3).
- **Open before answering:** fake-model test that a question about a table row or a playground number calls a read tool
  before answering (with the full-page switch off), or answers from the page text (on).
- **Boundary test:** v5 runs with v3 and v4 unimportable.
- **Live, billed:** a scripted run of 6+ versions, then "change v3's playground", "go back to v2" and "keep everything
  since v3 but fix S3".

---

## 11. Risks and how they're covered

| Risk | Covered by |
|---|---|
| A loaded version doesn't match the saved page | Checked when saving; invariant test; records keep their full page; a damaged row affects only that one version |
| The user's "v3" and the server's v3 are different pages | The server assigns every number and the app only shows it (phase 1) |
| A version number is reused after a delete | `threads.next_version` counter; tombstones keep the row |
| The same page stored three times (record, `generations.spec`, version row), one copy uncounted | `generations.spec` cleared once the record holds the page (phase 1); version rows count toward the cap |
| Clearing `generations.spec` loses the only copy, or leaves a record stuck `generating` | Cleared only in the same transaction that stores the page in the record; kept while the record row doesn't exist; the empty-spec branch ships in the same commit |
| A revert or undo isn't in the version store | Reverts go through the server endpoint, not the browser |
| Two different sections in different branches share an id | Ids are unique per conversation (`section_ids`, tombstones included) |
| A handle points at a different section in another version | Tools take ids; handles resolve only against the version passed |
| Haiku picks the wrong version | The log shows request text per version; it asks when unclear; nothing is overwritten |
| Stale or wrong AI description | Notes are keyed by content hash and format version, written without neighbours; Haiku always opens the real section before changing it or answering about its details |
| Notes about a user's content outlive the account | `section_notes` keyed by user, cascade delete |
| AI spend outside the daily cap | Main files are built inside the follow-up that needs them, never in the background |
| The review changes things nobody asked for | One pass only; it fixes only what this edit broke, everything else is a suggestion |
| A section broken by the edit is missed (no dependency graph) | The review reads the main file and the changes (old content included) and uses `search_page` for passing mentions; it runs on every change except title or look; phase 3 tests removal, rename and move; links can be added back if it misses |
| A broken section can't be fixed and code doesn't know what to undo | Each problem names its `cause`; code puts that section back |
| More round trips and cost in the talk step | At most 4 read rounds, effort Low; the full-page switch; budget includes reads, review and fixes; measured against v4 in phase 3 |
| Later changes lost when working from an old version | "Keep the changes since" edits the latest page with the old version as a reference; Haiku asks when it's unclear |

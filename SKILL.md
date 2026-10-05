---
name: schedule-update-narrative
description: Draft the monthly schedule update narrative from two Primavera P6 XER exports (last update vs this update) - project finish movement, progress in the period, longest path and float-critical changes, delays and gains, milestones, logic/duration/constraint changes, retrospective actual-date edits, and a look-ahead. Every sentence cites computed facts and is validated against them; causes of delay are never invented (left for the planner). Writes narrative.md, facts.json, claims.json and an evidence workbook. Use when a planner/scheduler needs to write a schedule update narrative, monthly progress narrative, schedule variance report, or compare two P6 schedule updates.
---

# Schedule Update Narrative (P6 update -> written narrative)

Each monthly schedule update usually needs a written narrative (e.g. VA 01 32 16.15, NPS, DOT specs):
what progressed, what moved, the critical path, milestones, changes and the look-ahead. Planners write
it by hand by comparing two schedule versions. This skill computes the comparison and drafts the text.

## Rule
Code computes facts. Every sentence with a number, date, Activity ID or direction is a fixed template
filled from the facts it cites - you cannot type those values yourself. You may only:
- choose, order or drop claims;
- add one of the fixed neutral phrases (`"template": "phrase"`: intro, evidence, planner_items);
- add planner input (`"planner": {"text","by","date"}`) - shown attributed; the only place a cause may appear.
Never write a reason for a delay yourself - ask the planner.

## Run
```
py scripts/doctor.py previous.xer current.xer --out outputs/<job>   # ALWAYS first: explains the files, prints the command
py scripts/narrative.py compare previous.xer current.xer --out outputs/<job>
    [--project-prev ID --project-curr ID]   # when an export holds linked projects
    [--map renames.csv]                     # columns old,new; must be one-to-one or the run stops; row X,X = same activity
py scripts/narrative.py validate outputs/<job> --write   # after editing <job>-claims.json
```
Outputs: `<job>-narrative.md`, `<job>-facts.json`, `<job>-claims.json`, `<job>-evidence.xlsx`.

## How to use this as Claude
0. Run `doctor` and tell the user its verdict in plain words.
   - FIX FIRST: stop. Explain what is wrong with the files (swapped, different projects, duplicate IDs, no data date)
     and how to fix it in P6. Do not run compare.
   - Renumbered activities: show the user `<job>-renames-suggested.csv` (old, new, name). They delete wrong rows;
     then pass it as `--map`. Never pass a rename map the user hasn't confirmed.
   - Use the exact command doctor prints (it picks `--project-prev/--project-curr` for files with linked projects).
1. Run `compare`. Read the summary line and the narrative back to the user: finish movement first,
   then longest-path delays, milestones, schedule changes, warnings.
2. Ask the planner for reasons, recovery actions, decisions needed and contractual completion status.
   Put each into the claim as text plus `"planner_input": "<who, date>"`.
3. Put the planner's answers into `<job>-claims.json` (a `planner` field on the delay claim, or a
   `"template": "planner"` claim), then run `validate --write`. Never edit the .md directly - validate
   fails if the .md differs from claims.json. Only hand over the narrative when validation is 100% PASS.

## When a new schedule breaks something (self-repair)
New companies' files will show new edge cases. You may fix the tool yourself, but only this way.

**Triggers:** a crash/traceback; compare STOPPED when doctor said READY; validation FAIL on untouched output;
a number that disagrees with the XER or with what the user sees in P6; a sentence that is wrong or makes no sense;
a section that is empty when it shouldn't be, or too long to read.

**Steps:**
1. Tell the user in one line what went wrong. Do not hand over that narrative.
2. Find the cause in the raw XER: the smallest set of rows that shows it. Check the actual field values.
3. Decide which kind it is:
   - **The file is wrong** (invalid dates, missing fields, bad export): the fix is a clear warning (doctor and/or
     a `warning` fact). Never "correct" the user's data or hide it.
   - **The tool is wrong**: fix the code.
3b. **Before changing any code**, snapshot the real-schedule corpus (real XER pairs kept OUTSIDE this repo, by
   default `../p6-test/corpus/corpus.csv`; add the user's two files as a new row, with their OK):
   `py scripts/corpus_check.py <corpus.csv> --save <scratch>/before.json`
   No corpus yet (a new user)? Create `corpus.csv` OUTSIDE this repo with columns name,previous,current, add every
   pair of the user's XERs you can find (with their OK) - at least the pair that broke - and tell the user the
   side-effect check is only as wide as the schedules in it. Never put client XERs inside the repo.
4. Write a failing test FIRST in `tests/regression.py`, using the `xer()`/`T()` helpers to build a small invented
   fixture that reproduces the pattern. Never copy the user's project names, Activity IDs or data into tests.
5. Make the smallest fix in `scripts/narrative.py` or `scripts/doctor.py`.
6. Run `py tests/regression.py`. Everything must pass, including the P6 Schedule Comparison checks.
   Never delete, skip or loosen an existing test to get there. If an existing test blocks the fix, stop and ask the user.
7. Re-run doctor + compare on the user's files. Validation must be 100% PASS.
7b. Run `py scripts/corpus_check.py <corpus.csv> --diff <scratch>/before.json`. Every number that changed must be
   one your fix was MEANT to change. For each other changed pair, open the facts and look: a side effect on a
   project you weren't fixing is a new bug - go back to step 4 with a test for it. Show the user the diff summary.
8. Add a row to `CASES.md` (date, symptom, cause, fix, test name).
9. Tell the user what changed and why. Ask before committing or pushing. If they don't own this repo, offer to
   draft a GitHub issue for the author with the symptom, the invented test and the fix - never with their data.

**Never, even to make a run pass:** edit facts.json/claims.json/.md by hand; let claims carry free text or
typed numbers; relax the validator; invent a fact; guess a cause of delay.

## Definitions (stated in every fact)
- Reporting window: (previous data date, current data date].
- Longest path = TASK.driving_path_flag; float-critical = total float <= 0 h (unfinished). Reported separately.
- Dates: completed -> actual; otherwise P6's remaining early dates (restart_date/reend_date) - the dates P6 shows.
  Movements are calendar days between dates. Durations: raw hours + days on the activity's own calendar.
- Remaining-duration changes are "estimate changes" only - not productivity.
- Start milestones compare start dates; finish milestones compare finish dates. Always shown.

## Limits
Explains WHAT changed, not WHY. Not a forensic delay analysis. No working-day arithmetic between dates
(v1). Needs stable Activity IDs or a rename map. Calendar and schedule-setting changes not yet reported.

## Tests
`py tests/regression.py` - 90 checks: a real P6 25 update pair (Oracle sample EC00501 progressed one
month with known changes), fixtures (mixed calendars, completed-late, renames, missing fields, external
links, retrospective actuals, branched longest path, duplicate IDs, ambiguous rename maps, reused Activity IDs,
same ID holding a differently named activity), and validator traps (swapped IDs,
reversed dates, wrong direction, quantity words, hand-edited .md, edited headings, stale facts, unsupported status sentences, unattributed causes),
real-data cases (backdated progress, actuals after the data date, capped long lists) and doctor checks
(swapped files, unrelated projects, renumbered activities, linked projects). Also verified on 3 real contractor
update pairs (residential, admin building, oil & gas) with an independent re-count. Case history: `CASES.md`.

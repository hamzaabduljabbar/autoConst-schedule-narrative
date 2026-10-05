# Schedule Update Narrative: design (rev 3)

Rev 3 (after Codex code review #1, 7 HIGH findings, all reproduced as tests then fixed):
- Claims no longer hold free text with values. They name a fixed template + cited facts, and the text is rendered.
- `free` text must contain no digits, IDs, dates, quantity/direction/cause words; planner input is attributed (by, date).
- validate re-renders and compares the delivered .md (`--write` rebuilds it from claims.json).
- The longest-path sequence is directed: chain vs network (sources/sinks).
- Blank/duplicate Activity IDs raise IdentityError (the run stops).
- Longest-path/float availability is checked per export.
- completed_in_period needs a transition.

Rev 3.1 (Codex code review #2, 3 HIGH, fixed): free text is replaced by fixed neutral phrases; section ids map to fixed
headings, and the title/source come from facts; rename maps must be one-to-one with a single match table (a renamed-away
code never matches itself); claims.json carries a sha256 of facts.json (stale runs stop).

(rev 2 header follows)

Rev 2 changes (Codex findings 1-7, all accepted after checking against the real files):
- Every claim links to the facts behind it. The validator checks claims, not just loose numbers.
- Longest path and float-critical are reported separately. In the real test data they differ: 23 activities flagged longest path vs 15 with TF ≤ 0.
- Raw hours are kept; each conversion basis is stated. Real data: EC1370 is 33.6 h on an 8 h/day calendar.
- Slips are split into separate fact types. No "impact" claim is made without a fact behind it.
- Date semantics, identity and reporting window are defined. Look-ahead includes ongoing work.
- Planner inputs (reasons, recovery, decisions) are kept separate and attributed.
- Scope is split into v1 and later; fixtures are added.

## Problem
On most contracts, every monthly schedule update must come with a written **narrative**. Planners write it by hand by comparing two schedule versions. This tool drafts it from the two XERs.

## Inputs / outputs
**In:**
- `previous.xer` and `current.xer`;
- optional `--project` (Activity ID prefix / project short name when the export holds several projects);
- optional `--map renames.csv` (old Activity ID → new);
- optional `planner-inputs.yaml` (reasons, recovery actions, decisions needed, contractual completion status, each with author and date);
- optional `--template` (named spec section list).

**Out:**
1. `facts.json`: the only source of truth (schema below).
2. `narrative.md` / `.docx`: every sentence carries the `fact_id`s it rests on (hidden in the docx, visible in the md).
3. `evidence.xlsx`: generated from the same fact references, one row per claim → its facts → activity IDs and raw values.
4. `validation.txt`: PASS/FAIL per claim.

## Principle
- **Code computes facts. The LLM only phrases claims that cite fact_ids.**
- **The validator** checks every claim's activity, metric, value, unit, compared dates and direction against the cited facts.
- **Reasons, impact and recovery** come only from planner inputs, shown as attributed. Without an input, the narrative inserts `[REASON: planner to confirm]`.

## Facts schema (v1)
```json
{
  "fact_id": "F-FIN-EC1390-001",
  "type": "forecast_finish_movement",
  "activity": "EC1390", "activity_name": "Erect Structural Frame",
  "metric": "early_end_date",
  "previous": "2011-08-01 09:36", "current": "2011-08-24 17:00",
  "delta": {"calendar_days": 23, "basis": "calendar days between dates"},
  "direction": "later",
  "source": {"prev_field": "TASK.early_end_date", "curr_field": "TASK.early_end_date"},
  "flags": []
}
```

**Fact types (v1):**
| type | rule |
|---|---|
| `data_dates` | PROJECT.last_recalc_date prev/curr. Reporting window = (prev DD, curr DD] |
| `project_finish_movement` | PROJECT.scd_end_date prev → curr |
| `completed_in_period` | act_end_date in window |
| `started_in_period` | act_start_date in window |
| `completed_late` | completed in window AND act_end_date > previous forecast finish (prev early_end_date) |
| `planned_not_started` | prev early_start_date ≤ curr DD, still TK_NotStart in curr |
| `forecast_finish_movement` | unfinished in curr: early_end_date prev → curr (all movements kept; display filter default ≥5 d) |
| `forecast_start_movement` | not started in curr: early_start_date prev → curr |
| `original_duration_change` | target_drtn_hr_cnt delta (raw hours + days on the activity's calendar day_hr_cnt) |
| `remaining_estimate_change` | remain_drtn_hr_cnt delta. **Labelled "estimate change"; no productivity interpretation** |
| `milestone_movement` | TT_Mile → start date; TT_FinMile → finish date. **Always shown** regardless of the threshold |
| `longest_path_membership` | TASK.driving_path_flag='Y' prev vs curr. Entered / left; "left because completed" kept separate from "left while unfinished" |
| `float_critical_membership` | total_float_hr_cnt ≤ threshold (default 0 h; stated) prev vs curr, same split |
| `longest_path_sequence` | only if relationships connect the members (walk TASKPRED among LP activities); otherwise none, and no sequence claim |
| `near_critical` | 0 < TF ≤ N working days (N × activity calendar hours) |
| `activity_added` / `activity_deleted` | task_code set difference after the rename map |
| `logic_change` | TASKPRED diff on (pred code, succ code, pred_type, lag_hr_cnt) |
| `constraint_change` | cstr_type/cstr_date and cstr_type2/cstr_date2 |
| `actual_date_correction` | act_start/act_end that existed in prev and changed in curr (retrospective edit) |
| `lookahead` | curr: starts, finishes and ongoing work in (curr DD, curr DD + N days], critical first |
| `warning` | unresolved external relationships, missing fields, duplicate/blank IDs, multi-project ambiguity |

**Date rules:**
- Completed activities use act_*.
- In-progress activities use act_start + early_end (P6 remaining early end).
- Not-started activities use early_*.
- Dates are compared in calendar days. **Working days between dates are NOT computed in v1** (that would need workweeks, shifts and exceptions); durations are given in raw hours and in days on the activity's own calendar (day_hr_cnt).

**Identity:**
- task_code within the selected project (proj_id from the PROJECT row matching `--project`, or the only project with TASK rows; linked-project stub rows are ignored and their relationships are flagged as external).
- Renamed codes are handled via `--map`. Without it, a rename shows as a delete plus an add, with a warning.

## v1 vs later
- **v1 (build now):** everything in the table above + validator + evidence + md/docx narrative + planner-inputs file.
- **Later:** calendar definition changes, schedule-setting changes (retained logic / progress override), baseline comparison, working-day date math, owner-specific templates beyond the default.

**Default template:** sections mapped to VA 01 32 16.15 (named revision), as a starting point:
1. Summary
2. Progress
3. Longest path & critical
4. Delays (facts) + reasons (planner)
5. Milestones & contract completion (planner)
6. Schedule changes (logic/durations/constraints/added/deleted/actual corrections)
7. Look-ahead
8. Recovery actions & decisions needed (planner)

## Tests
**Real pair: EC00501-U1 → U2** (Oracle sample, progressed in P6 25 by Hamza). Answer key:
1. EC1390 original duration 42 → 52 d (336 → 416 h); finish 1 Aug → 24 Aug 2011.
2. EC1370 remaining estimate → 64 h, now TK_Active; finish 2 Jun → 10 Jun.
3. Logic: removed EC1430 → EC1470 FS 0; EC1470 start 25 Oct → 1 Jun.
4. Project finish 2013-01-11 → 2013-01-31.

The exports include stub rows of linked projects EC00515/EC00530, which test project selection.

**Fixtures (small, hand-built XERs):** mixed calendars, renamed activity (with/without map), completed-late, parallel longest paths, missing fields, external relationships, a retrospective actual-date change.

**Validator tests:**
- wrong activity/value pairing using numbers that exist in facts (e.g. "EC1370 slipped 23 days") → FAIL;
- an unsupported impact claim with no fact_id → FAIL.

Claim Digger is used only as a cross-check of raw differences.

## Honest limits
- It explains WHAT changed. WHY, the impact interpretation and recovery come from the planner.
- It isn't a forensic delay analysis.
- No working-day math between dates in v1.
- It needs stable Activity IDs (or a rename map).

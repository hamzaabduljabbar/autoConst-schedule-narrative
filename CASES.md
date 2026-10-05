# Edge-case log

Every real-world problem found, how it was fixed, and the test that keeps it fixed.
New rows are added by the self-repair steps in SKILL.md.

| Date | Symptom | Cause | Fix | Test |
|---|---|---|---|---|
| 2026-10-04 | EC1390 slip 23 d vs P6 22 d | used early_end_date; P6 shows reend_date | use restart_date/reend_date for unfinished work | finish dates match P6's own Schedule Comparison |
| 2026-10-05 | 22 completions missing from Progress (real residential pair) | actual finish on the previous data date, outside the window | `backdated_actual` warning | backdated completion -> not in period, warned |
| 2026-10-05 | finish dated after the data date not flagged | no check | `actual_after_data_date` warning, worst warnings listed first | actual finish after data date -> warned |
| 2026-10-05 | 1,187 bullets in Schedule changes (real oil & gas pair) | every change listed | cap 15 per list + cited count sentence; full list in evidence | long list capped |
| 2026-10-05 | look-ahead cut at 25 of 200 with no note, not longest-path first | hard slice, date sort | longest path first, capped with count | (look-ahead in long-list cap) |
| 2026-10-05 | "remaining 8 -> 0 days" for every completed activity | completion zeroes remaining | kept as fact (P6 lists it), hidden from narrative | P6 remaining durations check |
| 2026-10-05 | 160 deleted + 120 added, 62 actually renumbered (real oil & gas pair) | IDs changed between updates | doctor suggests same-name pairs for the planner to confirm (`--map`) | doctor: renumbered activity suggested |
| 2026-10-05 | "original duration changed from 2 to 5 days" on an activity nobody touched (real oil & gas pair) | IDs re-sequenced between updates: same Activity ID now holds a different activity (different name; old name moved to another ID), compared row-to-row by ID | `activity_name_change` fact; ID kept with different words and not confirmed by `--map` -> its compared facts flagged `identity_unconfirmed`, kept in evidence, left out of narrative and counts, warned; map row X,X confirms; doctor warns + suggests re-sequenced pairs. Names only re-punctuated / words added or dropped count as the same activity | reused ID: ... (4 checks), map row A100,A100 confirms ..., doctor: same ID with a different name ..., same ID, name only re-punctuated ..., one-letter abbreviation change ..., doctor: different projects (FIX FIRST) -> no same-ID name-change warning ..., doctor: re-punctuated/shortened names not warned ... |
| 2026-10-05 | doctor said "same project" for unrelated projects (two different contractor projects, 100% ID match) | many companies keep P6 default IDs (A1000...) | doctor also requires activity NAMES to match (real pairs 64-100%, unrelated 0-6%); template twins with no shared project-name word -> confirm | doctor: same default IDs but different names -> FIX FIRST; same template -> confirm |

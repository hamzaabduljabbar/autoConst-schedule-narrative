# Schedule Update Narrative

**Two P6 schedule updates in -> the monthly schedule narrative drafted, every sentence traced to the schedule.**

Most contracts require a written narrative with every schedule update: what progressed, what moved,
the critical path, milestones, changes, and what's next. Planners write it by hand, comparing last
month's schedule with this month's. This tool does the comparison and drafts the narrative - and it
never invents a reason for a delay. Causes stay with the planner.

## Real test
Oracle's P6 sample project EC00501, progressed one month in Primavera P6 Professional 25 with three
known changes (structural frame +10 days, backfill remaining estimate raised, one relationship removed):

```
Project finish 11 Jan 2013 -> 31 Jan 2013 (+20 calendar days)       found
EC1390 Erect Structural Frame 42 -> 52 days, finish 1 Aug -> 24 Aug  found
EC1430 -> EC1470 finish-to-start removed (EC1470 now 147 days earlier) found
Two actual start dates silently rewritten by P6 Update Progress      flagged
53 narrative claims, 53/53 validated against the computed facts
```
**Cross-checked against P6's own Schedule Comparison report:** finish dates for all 46 changed activities,
remaining and original durations, actual-date corrections and the project finish all match. (That check
caught one real bug: P6's Finish column uses *remaining* early dates - fixed.)

**Real contractor schedules:** also run on 3 real update pairs from contractors (residential, 923 activities;
admin building, 69; oil & gas, ~850, two P6 versions). An independent re-count from the raw files matched the tool
on 33/33 numbers. `doctor` and the report were then run on every same-project candidate among 73 real contractor
XERs (153 pairs): unrelated projects blocked, 0 crashes, 0 validation failures. Real data surfaced 11 edge cases
(backdated progress, actuals after the data date, renumbered and REUSED Activity IDs, default-ID look-alike projects,
very long lists...), all fixed and kept as tests - see [CASES.md](CASES.md).

## How it stays honest
- **No typed numbers.** Every sentence with a date, number, Activity ID or direction is a fixed template
  filled straight from the schedule facts. The AI can choose and order sentences, not write values.
- **Validated end to end.** Swapped IDs, reversed dates, a "later" sentence on an earlier fact, "two weeks",
  or a hand-edited narrative file all FAIL validation.
- **No invented causes.** Reasons appear only as attributed planner input. Delays get
  `[REASON: planner to confirm]`.
- **No free status claims.** Headings are fixed; extra wording is limited to neutral phrases. "The project is
  complete" can only appear as attributed planner input.
- **Refuses bad data.** Duplicate/blank Activity IDs, ambiguous rename maps, or claims made from a different
  facts file stop the run instead of producing a narrative.
- **Longest path and float-critical reported separately** (they differ: 22 vs 14 activities in the test).
- **Evidence workbook:** each sentence -> its facts -> activity IDs and raw values.

## Use
### Step 1 - Install
**Windows:** `winget install --id Python.Python.3.12 -e` then `py -m pip install openpyxl`
**Mac:** `brew install python@3.12` then `python3 -m pip install openpyxl` (use `python3` wherever this page says `py`)

### Step 2 - Clone with Claude Code
> *"Clone https://github.com/hamzaabduljabbar/autoConst-schedule-narrative and install it as a skill."*

### Step 3 - Export both updates from P6
Open the project > **File > Export > Primavera PM (XER) > Project** > tick one project > save.
Do it for last month's update and this month's (or keep the XERs you already submitted).

### Step 4 - Ask
> *"Write the schedule update narrative from C:\jobs\may.xer and C:\jobs\june.xer"*

Claude runs `doctor` first, tells you what it found, then drafts the narrative and asks you for the reasons,
recovery actions and decisions it must not invent. Or run it yourself:
```bash
py scripts/doctor.py last.xer this.xer --out outputs/job
py scripts/narrative.py compare last.xer this.xer --out outputs/job
```
`doctor` checks the files first and says, in plain words, what it found: files swapped, not the same project,
several projects inside one export, activities that look renumbered (it writes a suggested rename list for you
to confirm), impossible actual dates. Verdict: READY / READY WITH WARNINGS / FIX FIRST, plus the exact command.

**New edge case?** When a new schedule breaks something, Claude follows a fixed self-repair procedure in
SKILL.md: reproduce it as a small invented test, fix, re-run every test (including the P6 cross-check), log it
in CASES.md, then re-run every real schedule pair we have (`scripts/corpus_check.py`) and explain every number
that moved, so a fix for one project can't quietly change another. It never edits the outputs or loosens a test to
make a run pass.

## Limits
It explains WHAT changed, not WHY. It is not a forensic delay analysis. Movements are in calendar days
(no working-day math between dates yet). Needs stable Activity IDs (or a rename map). Calendar and
schedule-setting changes are not reported yet.

## Tests
`py tests/regression.py` (90 checks). The real P6 pair is not bundled (Oracle sample data); fixtures are.

Built by [Hamza Jabbar](https://hamzajabbar.online) - AutoConst. License: AutoConst Source Available.

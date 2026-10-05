"""Regression checks. Real pair (EC00501 progressed in P6 25) + small fixtures + validator traps.

  py tests/regression.py

Real data lives outside the repo (../p6-test/narrative/). Missing data = those checks SKIP, never pass.
"""
import csv, json, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
import narrative as N  # noqa: E402

REAL = ROOT.parent / "p6-test" / "narrative"
passed = failed = skipped = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS {name}")
    else:
        failed += 1
        print(f"  FAIL {name} {detail}")


def facts_for(prev, curr, renames=None, pp=None, pc=None):
    warn = []
    a, b = N.Schedule(prev, pp, warn), N.Schedule(curr, pc, warn)
    F = N.compute_facts(a, b, N.match_table(a, b, list((renames or {}).items())))
    for w in warn:
        F.append({"fact_id": f"F-WARNING-{len(F):03d}", **w})
    return F


def find(F, ftype, act=None, **kw):
    return [f for f in F if f["type"] == ftype and (act is None or f.get("activity") == act)
            and all(f.get(k) == v for k, v in kw.items())]

# ------------------------------------------------------------------ real pair
print("Real pair: EC00501-U1 -> EC00501-U2")
u1, u2 = REAL / "EC00501-U1.xer", REAL / "EC00501-U2.xer"
if not (u1.exists() and u2.exists()):
    skipped += 1
    print("  SKIP real data not found")
else:
    F = facts_for(u1, u2)
    from p6_finish_truth import P6_FINISH
    mine = {f["activity"]: (f["previous"], f["current"]) for f in find(F, "forecast_finish_movement")}
    miss = {k: (v, mine.get(k)) for k, v in P6_FINISH.items() if mine.get(k) != v}
    check(f"finish dates match P6's own Schedule Comparison report ({len(P6_FINISH)} activities)", not miss, miss)
    from p6_finish_truth import P6_REMAINING_H, P6_ORIGINAL_H, P6_ACTUAL_START
    unf = {k for k in P6_FINISH if N.Schedule(u2).tasks[k]["status_code"] != "TK_Complete"}
    check("no finish movement reported that P6 doesn't report (44 = 44)", set(mine) == unf, set(mine) ^ unf)
    rnd = lambda ftype: {f["activity"]: (round(f["previous"]), round(f["current"])) for f in find(F, ftype)}
    check("remaining durations match P6 report", rnd("remaining_estimate_change") == P6_REMAINING_H)
    check("original durations match P6 report", rnd("original_duration_change") == P6_ORIGINAL_H)
    check("actual start corrections match P6 report",
          {f["activity"]: (f["previous"], f["current"]) for f in find(F, "actual_date_correction")} == P6_ACTUAL_START)
    pf = find(F, "project_finish_movement")
    check("project finish 2013-01-11 -> 2013-01-31 (+20)", pf and pf[0]["previous"] == "2013-01-11"
          and pf[0]["current"] == "2013-01-31" and pf[0]["delta"]["calendar_days"] == 20, pf)
    od = find(F, "original_duration_change", "EC1390")
    check("EC1390 original duration 336 -> 416 h (42 -> 52 d)", od and od[0]["previous"] == 336 and od[0]["current"] == 416
          and od[0]["previous_days"] == 42 and od[0]["current_days"] == 52, od)
    fm = find(F, "forecast_finish_movement", "EC1390")
    check("EC1390 finish 2011-08-02 -> 2011-08-24 (P6 Finish column)", fm and fm[0]["previous"] == "2011-08-02" and fm[0]["current"] == "2011-08-24", fm)
    re_ = find(F, "remaining_estimate_change", "EC1370")
    check("EC1370 remaining estimate change reported, labelled estimate", re_ and re_[0]["current"] == 64
          and "estimate" in re_[0]["note"], re_)
    check("EC1370 started in period (2011-05-27)", find(F, "started_in_period", "EC1370", current="2011-05-27"))
    lg = find(F, "logic_change", "EC1470", change="removed", predecessor="EC1430", relationship="PR_FS")
    check("logic: EC1430 -> EC1470 FS removed", bool(lg), lg)
    check("no phantom added links", not find(F, "logic_change", change="added"))
    check("EC1470 is a gain, not a delay", find(F, "forecast_finish_movement", "EC1470", direction="earlier"))
    check("actual-date corrections flagged (Update Progress rewrote EC1350 start)",
          find(F, "actual_date_correction", "EC1350", previous="2011-04-15", current="2011-04-13"))
    check("linked-project rows warned, not mixed in", find(F, "warning") and len(N.Schedule(u1).tasks) == 71)
    lp = find(F, "longest_path_membership")
    tf = find(F, "float_critical_membership")
    check("longest path and float-critical reported separately (22 vs 14)",
          lp and tf and lp[0]["current_count"] == 22 and tf[0]["current_count"] == 14)
    ids = [f["fact_id"] for f in F]
    check("fact_ids unique", len(ids) == len(set(ids)))
    C = N.render_all(N.draft_claims(F), F)
    R = N.validate(C, F)
    check("drafted narrative validates 100%", all(r["status"] == "PASS" for r in R),
          [r for r in R if r["status"] == "FAIL"][:2])

    # validator traps (Codex code review #1, findings 1-2)
    f1390 = find(F, "forecast_finish_movement", "EC1390")[0]
    f1470 = find(F, "forecast_finish_movement", "EC1470")[0]
    good = {"claim_id": "T0", "section": "delays", "template": "finish_later", "fact_ids": [f1390["fact_id"]]}
    good["text"] = N.render(good, {f["fact_id"]: f for f in F})
    swapped = dict(good, claim_id="T1", text=good["text"].replace("EC1390", "EC1370"))
    reversed_ = dict(good, claim_id="T2", text=good["text"].replace("24 Aug 2011", "XX").replace("01 Aug 2011", "24 Aug 2011")
                     .replace("XX", "01 Aug 2011"))
    wrong_dir = {"claim_id": "T3", "section": "delays", "template": "finish_later", "fact_ids": [f1470["fact_id"]]}
    wrong_type = {"claim_id": "T4", "section": "delays", "template": "milestone", "fact_ids": [f1390["fact_id"]]}
    nofact = {"claim_id": "T5", "section": "delays", "template": "finish_later", "fact_ids": []}
    frees = {f"F{i}": {"claim_id": f"F{i}", "section": "summary", "template": "free", "free_text": txt, "fact_ids": []}
             for i, txt in enumerate(["a999 moved twenty days.", "The frame slipped two weeks.", "The delay was caused by rain.",
                                      "The contractor has met the contractual completion date.",   # Codex review #2, #1
                                      "The project is complete.", "All milestones have been achieved."])}
    frees["F9"] = {"claim_id": "F9", "section": "summary", "template": "phrase", "phrase": "project_complete"}
    frees["H1"] = {"claim_id": "H1", "section": "The project is complete and all milestones are achieved",   # Codex #2, #2
                   "template": "gap", "label": "REVIEW", "fact_ids": []}
    clean = {"claim_id": "OK1", "section": "summary", "template": "phrase", "phrase": "evidence"}
    planner_ok = dict(good, claim_id="OK2", planner={"text": "steel delivery was late due to a supplier issue",
                                                       "by": "J. Planner", "date": "2011-06-03"})
    planner_bad = dict(good, claim_id="T6", planner={"text": "rain"})
    traps = [good, swapped, reversed_, wrong_dir, wrong_type, nofact, clean, planner_ok, planner_bad] + list(frees.values())
    for c in traps:
        if c["claim_id"] in ("OK2",):
            c.pop("text", None)
    res = {r["claim_id"]: r for r in N.validate(traps, F)}
    check("correct template claim PASSES", res["T0"]["status"] == "PASS", res["T0"])
    for k, why in (("T1", "activity ID swapped in the text"), ("T2", "previous/current dates reversed in the text"),
                   ("T3", "'later' template on a fact that moved earlier"), ("T4", "template used with the wrong fact type"),
                   ("T5", "template without its fact"), ("T6", "planner input without author/date")):
        check(f"validator FAILS {k}: {why}", res[k]["status"] == "FAIL", res[k])
    for k, c in frees.items():
        check(f"rejected: {c.get('free_text') or c.get('phrase') or 'heading: ' + c['section']}", res[k]["status"] == "FAIL", res[k])
    check("approved neutral phrase PASSES", res["OK1"]["status"] == "PASS", res["OK1"])
    check("attributed planner reason (may state a cause) PASSES", res["OK2"]["status"] == "PASS"
          and "Planner input (J. Planner, 2011-06-03)" in res["OK2"]["text"], res["OK2"])

# ------------------------------------------------------------------ fixtures
print("Fixtures")
TASK_F = ["task_id", "proj_id", "clndr_id", "task_code", "task_name", "task_type", "status_code", "target_drtn_hr_cnt",
          "remain_drtn_hr_cnt", "total_float_hr_cnt", "act_start_date", "act_end_date", "early_start_date", "early_end_date",
          "driving_path_flag", "cstr_type", "cstr_date", "cstr_type2", "cstr_date2"]


def xer(path, dd, finish, tasks, links=(), cals=(("1", "8"), ("2", "10")), projects=(("1", "P1"),)):
    L = ["ERMHDR\t25.0", "%T\tPROJECT", "%F\tproj_id\tproj_short_name\tlast_recalc_date\tscd_end_date"]
    for pid, name in projects:
        L.append(f"%R\t{pid}\t{name}\t{dd}\t{finish}")
    L += ["%T\tCALENDAR", "%F\tclndr_id\tclndr_name\tday_hr_cnt"] + [f"%R\t{c}\tcal{c}\t{h}" for c, h in cals]
    L += ["%T\tTASK", "%F\t" + "\t".join(TASK_F)]
    for t in tasks:
        L.append("%R\t" + "\t".join(str(t.get(k, "")) for k in TASK_F))
    L += ["%T\tTASKPRED", "%F\ttask_pred_id\ttask_id\tpred_task_id\tproj_id\tpred_type\tlag_hr_cnt"]
    for i, (s, p, typ, lag) in enumerate(links):
        L.append(f"%R\t{i + 1}\t{s}\t{p}\t1\t{typ}\t{lag}")
    L.append("%E")
    Path(path).write_text("\n".join(L) + "\n", encoding="cp1252")


def T(i, code, **kw):
    base = {"task_id": i, "proj_id": "1", "clndr_id": "1", "task_code": code, "task_name": f"Task {code}",
            "task_type": "TT_Task", "status_code": "TK_NotStart", "target_drtn_hr_cnt": 80, "remain_drtn_hr_cnt": 80,
            "total_float_hr_cnt": 40, "driving_path_flag": "N"}
    base.update(kw)
    return base


with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    # completed late + mixed calendars + retrospective actual + milestone + constraint + external link
    prev = [T(1, "A100", status_code="TK_Active", act_start_date="2025-01-06 08:00", early_end_date="2025-01-20 17:00",
              remain_drtn_hr_cnt=40),
            T(2, "A200", clndr_id="2", target_drtn_hr_cnt=100, remain_drtn_hr_cnt=100, early_start_date="2025-02-03 08:00",
              early_end_date="2025-02-14 17:00", driving_path_flag="Y", total_float_hr_cnt=0),
            T(3, "M900", task_type="TT_FinMile", target_drtn_hr_cnt=0, remain_drtn_hr_cnt=0,
              early_end_date="2025-03-01 17:00", driving_path_flag="Y", total_float_hr_cnt=0),
            T(4, "OLD1", early_start_date="2025-01-10 08:00", early_end_date="2025-01-15 17:00"),
            T(5, "X1", proj_id="2")]
    curr = [T(1, "A100", status_code="TK_Complete", act_start_date="2025-01-08 08:00", act_end_date="2025-01-28 17:00",
              remain_drtn_hr_cnt=0),
            T(2, "A200", clndr_id="2", target_drtn_hr_cnt=150, remain_drtn_hr_cnt=150, early_start_date="2025-02-10 08:00",
              early_end_date="2025-02-28 17:00", driving_path_flag="Y", total_float_hr_cnt=0,
              cstr_type="CS_MSO", cstr_date="2025-02-10 08:00"),
            T(3, "M900", task_type="TT_FinMile", target_drtn_hr_cnt=0, remain_drtn_hr_cnt=0,
              early_end_date="2025-03-14 17:00", driving_path_flag="Y", total_float_hr_cnt=0),
            T(4, "NEW1", early_start_date="2025-01-10 08:00", early_end_date="2025-01-15 17:00"),
            T(5, "X1", proj_id="2")]
    xer(tmp / "p.xer", "2025-01-01 08:00", "2025-03-01 17:00", prev, links=[(2, 1, "PR_FS", 0), (3, 2, "PR_FS", 0)],
        projects=(("1", "P1"), ("2", "OTHER")))
    xer(tmp / "c.xer", "2025-02-01 08:00", "2025-03-14 17:00", curr, links=[(2, 1, "PR_FS", 0), (3, 2, "PR_FS", 0), (1, 5, "PR_FS", 0)],
        projects=(("1", "P1"), ("2", "OTHER")))
    F = facts_for(tmp / "p.xer", tmp / "c.xer", pp="P1", pc="P1")
    cl = find(F, "completed_late", "A100")
    check("completed-late: A100 finished 8 days after its previous forecast", cl and cl[0]["delta"]["calendar_days"] == 8, cl)
    check("retrospective actual start change flagged", find(F, "actual_date_correction", "A100"))
    od = find(F, "original_duration_change", "A200")
    check("mixed calendars: A200 on 10 h/day -> 10 -> 15 days", od and od[0]["previous_days"] == 10 and od[0]["current_days"] == 15
          and "10 h per day" in od[0]["delta"]["basis"], od)
    ms = find(F, "milestone_movement", "M900")
    check("finish milestone compared on finish (+13 d)", ms and ms[0]["metric"] == "finish" and ms[0]["delta"]["calendar_days"] == 13, ms)
    check("constraint added is reported", find(F, "constraint_change", "A200"))
    check("rename without map = delete + add", find(F, "activity_deleted", "OLD1") and find(F, "activity_added", "NEW1"))
    check("external relationship warned", any(f.get("code") == "external_relationship" for f in F))
    check("--project selects P1 (other project's task ignored)", not find(F, "activity_added", "X1"))
    F2 = facts_for(tmp / "p.xer", tmp / "c.xer", renames={"OLD1": "NEW1"}, pp="P1", pc="P1")
    check("rename with map = no delete/add", not find(F2, "activity_deleted") and not find(F2, "activity_added"))
    # Codex review #2, #3: previous A100; current has A100 AND C300; map A100 -> C300
    r1 = [T(1, "A100", early_end_date="2025-02-07 17:00")]
    r2 = [T(1, "A100", early_end_date="2025-02-14 17:00"), T(2, "C300", early_end_date="2025-02-21 17:00")]
    xer(tmp / "r1.xer", "2025-01-01 08:00", "2025-03-01 17:00", r1)
    xer(tmp / "r2.xer", "2025-02-01 08:00", "2025-03-01 17:00", r2)
    Fr = facts_for(tmp / "r1.xer", tmp / "r2.xer", renames={"A100": "C300"})
    mv = find(Fr, "forecast_finish_movement")
    check("rename A100->C300: previous A100 matched once (to C300); current A100 is added",
          [f["activity"] for f in mv] == ["C300"] and find(Fr, "activity_added", "A100"), [f["activity"] for f in mv])
    sa, sb = N.Schedule(tmp / "r1.xer"), N.Schedule(tmp / "r2.xer")
    for pairs, why in (([("A100", "C300"), ("A100", "A100x")], "one old -> two new"),
                       ([("A100", "C300"), ("B999", "C300")], "two old -> one new"),
                       ([("Z1", "C300")], "old ID not in previous export"),
                       ([("A100", "Z9")], "new ID not in current export")):
        try:
            N.match_table(sa, sb, pairs)
            check(f"rename map rejected: {why}", False, "accepted")
        except N.RenameError:
            check(f"rename map rejected: {why}", True)
    seq = find(F, "longest_path_sequence")
    check("longest-path sequence only from linked members", seq and seq[0]["activities"] == ["A200", "M900"], seq)

    # missing fields: no driving_path_flag column, no scd_end_date
    TASK_F_SAVE = TASK_F[:]
    TASK_F.remove("driving_path_flag")
    xer(tmp / "p2.xer", "2025-01-01 08:00", "", prev[:3])
    xer(tmp / "c2.xer", "2025-02-01 08:00", "", curr[:3])
    TASK_F[:] = TASK_F_SAVE
    F3 = facts_for(tmp / "p2.xer", tmp / "c2.xer")
    check("missing longest-path flag -> warning, no LP claims", any(f.get("code") == "no_driving_path_flag" for f in F3)
          and not find(F3, "longest_path_membership"))
    check("missing project finish -> no finish claim", not find(F3, "project_finish_movement"))
    R3 = N.validate(N.render_all(N.draft_claims(F3), F3), F3)
    check("fixture narrative validates", all(r["status"] == "PASS" for r in R3), [r for r in R3 if r["status"] == "FAIL"][:2])

    # Codex code review #1, findings 4-7
    # 4: branched longest path is a network, not a chain
    br = [T(1, "A100", driving_path_flag="Y", early_start_date="2025-02-03 08:00", early_end_date="2025-02-07 17:00"),
          T(2, "B200", driving_path_flag="Y", early_start_date="2025-02-10 08:00", early_end_date="2025-02-14 17:00"),
          T(3, "C300", driving_path_flag="Y", early_start_date="2025-02-17 08:00", early_end_date="2025-02-21 17:00")]
    xer(tmp / "b1.xer", "2025-01-01 08:00", "2025-03-01 17:00", br, links=[(2, 1, "PR_FS", 0), (3, 1, "PR_FS", 0)])
    xer(tmp / "b2.xer", "2025-02-01 08:00", "2025-03-01 17:00", br, links=[(2, 1, "PR_FS", 0), (3, 1, "PR_FS", 0)])
    Fb = facts_for(tmp / "b1.xer", tmp / "b2.xer")
    sq = find(Fb, "longest_path_sequence")
    Cb = N.render_all(N.draft_claims(Fb), Fb)
    check("branched longest path = network (A100 -> B200, A100 -> C300), not a chain",
          sq and not sq[0]["is_chain"] and sq[0]["sources"] == ["A100"] and sorted(sq[0]["sinks"]) == ["B200", "C300"]
          and any(c["template"] == "lp_network" for c in Cb) and not any(c["template"] == "lp_chain" for c in Cb), sq)

    # 5: duplicate / blank Activity IDs stop the run
    xer(tmp / "dup.xer", "2025-01-01 08:00", "2025-03-01 17:00", [T(1, "A100"), T(2, "A100")])
    xer(tmp / "blank.xer", "2025-01-01 08:00", "2025-03-01 17:00", [T(1, "A100"), T(2, "")])
    for name in ("dup", "blank"):
        try:
            N.Schedule(tmp / f"{name}.xer")
            check(f"{name} Activity ID stops the run", False, "no error")
        except N.IdentityError:
            check(f"{name} Activity ID stops the run", True)
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "narrative.py"), "compare", str(tmp / "dup.xer"),
                        str(tmp / "b2.xer"), "--out", str(tmp / "dupjob")], capture_output=True, text=True)
    check("CLI refuses duplicate IDs (no narrative written)", r.returncode != 0 and not Path(f"{tmp}/dupjob-narrative.md").exists(),
          r.stdout[-200:])

    # 6: previous export without driving_path_flag -> no entered/left, no fake previous count
    TASK_F.remove("driving_path_flag")
    xer(tmp / "nolp.xer", "2025-01-01 08:00", "2025-03-01 17:00", br, links=[(2, 1, "PR_FS", 0)])
    TASK_F[:] = TASK_F_SAVE
    F6 = facts_for(tmp / "nolp.xer", tmp / "b2.xer")
    m6 = find(F6, "longest_path_membership")
    check("missing previous LP data: current count only, no entered/left",
          m6 and m6[0]["previous_count"] is None and m6[0]["current_count"] == 3 and not find(F6, "longest_path_entered"), m6)
    C6 = N.render_all(N.draft_claims(F6), F6)
    check("missing previous LP data: 'no data to compare' sentence", any(c["template"] == "membership_now" for c in C6))

    # 7: already complete in both exports -> not 'completed in period'; flagged
    done = [T(1, "A100", status_code="TK_Complete", act_start_date="2025-01-06 08:00", act_end_date="2025-01-20 17:00",
              remain_drtn_hr_cnt=0)]
    xer(tmp / "d1.xer", "2025-01-01 08:00", "2025-03-01 17:00", done)
    xer(tmp / "d2.xer", "2025-02-01 08:00", "2025-03-01 17:00", done)
    F7 = facts_for(tmp / "d1.xer", tmp / "d2.xer")
    check("no completion transition -> not reported as progress, warned inconsistent",
          not find(F7, "completed_in_period") and any(f.get("code") == "inconsistent_source" for f in F7))

    # real data (residential 2010): completion backdated to the previous data date, and actuals after the data date
    q1 = [T(1, "A100"), T(2, "B200")]
    q2 = [T(1, "A100", status_code="TK_Complete", act_start_date="2025-01-01 08:00", act_end_date="2025-01-01 08:00",
            remain_drtn_hr_cnt=0),
          T(2, "B200", status_code="TK_Complete", act_start_date="2025-01-20 08:00", act_end_date="2025-02-09 17:00",
            remain_drtn_hr_cnt=0)]
    xer(tmp / "q1.xer", "2025-01-01 08:00", "2025-03-01 17:00", q1)
    xer(tmp / "q2.xer", "2025-02-01 08:00", "2025-03-01 17:00", q2)
    Fq = facts_for(tmp / "q1.xer", tmp / "q2.xer")
    wq = [f for f in Fq if f["type"] == "warning"]
    check("backdated completion -> not in period, warned (not silently dropped)",
          not find(Fq, "completed_in_period", "A100") and any(f["code"] == "backdated_actual" and f["activity"] == "A100" for f in wq), wq)
    check("actual finish after data date -> warned, not counted as progress",
          not find(Fq, "completed_in_period", "B200")
          and any(f["code"] == "actual_after_data_date" and f["activity"] == "B200" for f in wq), wq)

    # real data (oil & gas 2012): 719 relationship changes -> narrative lists LIST_CAP + a cited count, all facts kept
    xer(tmp / "k1.xer", "2025-01-01 08:00", "2025-03-01 17:00", [T(1, "A100")])
    xer(tmp / "k2.xer", "2025-02-01 08:00", "2025-03-01 17:00", [T(1, "A100")] + [T(i, f"N{i}") for i in range(2, 22)])
    Fk = facts_for(tmp / "k1.xer", tmp / "k2.xer")
    Ck = N.render_all(N.draft_claims(Fk), Fk)
    ls = [c for c in Ck if c["template"] == "list_summary"]
    check("long list capped: 20 added -> 15 listed + count sentence, 20 facts kept, validates",
          len(find(Fk, "activity_added")) == 20 and sum(c["template"] == "added" for c in Ck) == N.LIST_CAP
          and ls and ls[0]["text"].startswith("20 activities added")
          and all(r["status"] == "PASS" for r in N.validate(Ck, Fk)), [c["text"] for c in ls])

    # doctor: explains the files before compare (swapped, unrelated, renumbered, linked projects, invalid actuals)
    import doctor as DR
    v, Fd, cmd = DR.diagnose(tmp / "q2.xer", tmp / "q1.xer")
    check("doctor: swapped files -> FIX FIRST, no command", v == "FIX FIRST" and cmd is None
          and any("swapped" in t for _, t in Fd), Fd)
    xer(tmp / "u1.xer", "2025-01-01 08:00", "2025-03-01 17:00", [T(1, "ZZ1"), T(2, "ZZ2")])
    v, Fd, cmd = DR.diagnose(tmp / "u1.xer", tmp / "q1.xer")
    check("doctor: unrelated projects -> FIX FIRST", v == "FIX FIRST" and any("0%" in t for _, t in Fd), Fd)
    rn1 = [T(1, "A100", task_name="Pour slab"), T(2, "A200", task_name="Erect frame"), T(3, "A300", task_name="Roof"),
           T(4, "TMP1", task_name="Install lifts")]
    rn2 = [T(1, "A100", task_name="Pour slab"), T(2, "A200", task_name="Erect frame"), T(3, "A300", task_name="Roof"),
           T(4, "L-100", task_name="install  LIFTS"), T(5, "N1", task_name="Brand new")]
    xer(tmp / "rn1.xer", "2025-01-01 08:00", "2025-03-01 17:00", rn1, projects=(("1", "P1"), ("2", "STUB")))
    rn1_stub = [T(9, "S1", proj_id="2")]
    xer(tmp / "rn1.xer", "2025-01-01 08:00", "2025-03-01 17:00", rn1 + rn1_stub, projects=(("1", "P1"), ("2", "STUB")))
    xer(tmp / "rn2.xer", "2025-02-01 08:00", "2025-03-01 17:00", rn2)
    csvp = tmp / "rs.csv"
    v, Fd, cmd = DR.diagnose(tmp / "rn1.xer", tmp / "rn2.xer", csvp)
    rows = list(csv.reader(open(csvp, encoding="utf-8")))[1:]
    check("doctor: renumbered activity suggested by name (TMP1 -> L-100), new one not paired",
          [r[:2] for r in rows] == [["TMP1", "L-100"]] and "--map" in cmd, rows)
    check("doctor: linked project stub -> picks P1 and puts --project-prev in the command",
          '--project-prev "P1"' in cmd and v == "READY WITH WARNINGS", cmd)
    # real data (zip of 73 contractor XERs): unrelated projects share P6 default IDs (A1000, A1010...) 100%
    dflt1 = [T(i, f"A{1000 + 10 * i}", task_name=f"Fence panel {i}") for i in range(1, 9)]
    dflt2 = [T(i, f"A{1000 + 10 * i}", task_name=f"Excavate zone {i}") for i in range(1, 9)]
    xer(tmp / "df1.xer", "2025-01-01 08:00", "2025-03-01 17:00", dflt1)
    xer(tmp / "df2.xer", "2025-02-01 08:00", "2025-03-01 17:00", dflt2)
    v, Fd, cmd = DR.diagnose(tmp / "df1.xer", tmp / "df2.xer")
    check("doctor: same default IDs but different names -> FIX FIRST (different projects)",
          v == "FIX FIRST" and cmd is None and any("names" in t for lvl, t in Fd if lvl == "FIX"), Fd)
    half = dflt1[:4] + [T(i, f"A{1000 + 10 * i}", task_name=f"Excavate zone {i}") for i in range(5, 9)]
    xer(tmp / "df3.xer", "2025-02-01 08:00", "2025-03-01 17:00", half)
    v, Fd, cmd = DR.diagnose(tmp / "df1.xer", tmp / "df3.xer")
    check("doctor: half the names match -> warning to confirm, not a silent OK",
          v == "READY WITH WARNINGS" and any("confirm" in t for lvl, t in Fd if lvl == "WARN"), Fd)
    # real data: two factories from one company template - same activity names, different project names
    xer(tmp / "tp1.xer", "2025-01-01 08:00", "2025-03-01 17:00", dflt1, projects=(("1", "JUICE PLANT"),))
    xer(tmp / "tp2.xer", "2025-02-01 08:00", "2025-03-01 17:00", dflt1, projects=(("1", "EL WAREHOUSE"),))
    v, Fd, cmd = DR.diagnose(tmp / "tp1.xer", tmp / "tp2.xer")
    check("doctor: same template, project names share no word -> warning to confirm",
          v == "READY WITH WARNINGS" and any("project names" in t for lvl, t in Fd if lvl == "WARN"), Fd)
    xer(tmp / "tp3.xer", "2025-02-01 08:00", "2025-03-01 17:00", dflt1, projects=(("1", "JUICE PLANT update 2"),))
    v, Fd, cmd = DR.diagnose(tmp / "tp1.xer", tmp / "tp3.xer")
    check("doctor: renamed update of the same project (shared word) -> no project-name warning",
          not any("project names" in t for _, t in Fd), Fd)
    v, Fd, cmd = DR.diagnose(tmp / "q1.xer", tmp / "q2.xer")
    check("doctor: actual after data date and backdated progress reported",
          any("after the data date" in t for _, t in Fd) and any("on or before the previous data date" in t for _, t in Fd), Fd)

    # real data (oil & gas 2012): IDs re-sequenced between updates - an Activity ID kept but now holding a DIFFERENT
    # activity (different name). Comparing the two rows reported "original duration changed" that nobody made.
    ru1 = [T(1, "A100", task_name="Approve submittal", target_drtn_hr_cnt=16, remain_drtn_hr_cnt=16),
           T(2, "B200", task_name="Order pumps", target_drtn_hr_cnt=40, remain_drtn_hr_cnt=40),
           T(3, "C300", task_name="Install pumps", target_drtn_hr_cnt=80, remain_drtn_hr_cnt=80)]
    ru2 = [T(1, "A100", task_name="Order pumps", target_drtn_hr_cnt=40, remain_drtn_hr_cnt=40),
           T(2, "A090", task_name="Approve submittal", target_drtn_hr_cnt=16, remain_drtn_hr_cnt=16),
           T(3, "C300", task_name="Install pumps", target_drtn_hr_cnt=120, remain_drtn_hr_cnt=120)]
    xer(tmp / "ru1.xer", "2025-01-01 08:00", "2025-03-01 17:00", ru1)
    xer(tmp / "ru2.xer", "2025-02-01 08:00", "2025-03-01 17:00", ru2)
    Fu = facts_for(tmp / "ru1.xer", tmp / "ru2.xer")
    Cu = N.render_all(N.draft_claims(Fu), Fu)
    idx_u = {f["fact_id"]: f for f in Fu}
    narrated = lambda C, ftype: [idx_u[i]["activity"] for c in C for i in c["fact_ids"]
                                 if idx_u.get(i, {}).get("type") == ftype]
    nc = find(Fu, "activity_name_change", "A100")
    check("reused ID: same Activity ID with a different name -> name change fact (unconfirmed) + warning",
          nc and nc[0]["previous"] == "Approve submittal" and nc[0]["current"] == "Order pumps" and not nc[0]["confirmed"]
          and any(f.get("code") == "activity_id_name_changed" for f in Fu), nc)
    check("reused ID: its duration 'change' is kept as a flagged fact but NOT narrated as a schedule change",
          find(Fu, "original_duration_change", "A100") and find(Fu, "original_duration_change", "A100")[0].get("identity_unconfirmed")
          and "A100" not in narrated(Cu, "original_duration_change") and "A100" not in narrated(Cu, "remaining_estimate_change"),
          narrated(Cu, "original_duration_change"))
    check("reused ID: unchanged-name activity (C300) still narrated; name change narrated; validates 100%",
          narrated(Cu, "original_duration_change") == ["C300"] and "A100" in narrated(Cu, "activity_name_change")
          and all(r["status"] == "PASS" for r in N.validate(Cu, Fu)), [c["text"] for c in Cu if c["section"] == "changes"])
    Fu2 = facts_for(tmp / "ru1.xer", tmp / "ru2.xer", renames={"A100": "A100"})
    nc2 = find(Fu2, "activity_name_change", "A100")
    check("map row A100,A100 confirms same activity -> compared and narrated normally",
          nc2 and nc2[0]["confirmed"] and not find(Fu2, "original_duration_change", "A100")[0].get("identity_unconfirmed"), nc2)
    mp = tmp / "same.csv"
    mp.write_text("old,new\nA100,A100\n", encoding="utf-8")
    check("rename map keeps an X,X row (it confirms identity)", N.load_renames(mp) == [("A100", "A100")], N.load_renames(mp))
    Fu3 = facts_for(tmp / "ru1.xer", tmp / "ru2.xer", renames={"B200": "A100", "A100": "A090"})
    check("re-sequenced IDs mapped (B200->A100, A100->A090) -> no duration change, no name change",
          not find(Fu3, "original_duration_change", "A100") and not find(Fu3, "original_duration_change", "A090")
          and not find(Fu3, "activity_name_change"), [f["type"] for f in Fu3 if f.get("activity") in ("A100", "A090")])
    csvu = tmp / "ru.csv"
    v, Fd, cmd = DR.diagnose(tmp / "ru1.xer", tmp / "ru2.xer", csvu)
    rows = sorted(r[:2] for r in csv.reader(open(csvu, encoding="utf-8"))) if csvu.exists() else []
    check("doctor: same ID with a different name -> warned; re-sequenced pairs suggested (A100->A090, B200->A100)",
          any("changed name" in t for lvl, t in Fd if lvl == "WARN") and [["A100", "A090"], ["B200", "A100"]] == rows[:-1]
          and rows[-1][0] == "old", (Fd, rows))

    # corpus side effect of the fix above (real residential pair): names only re-punctuated or shortened/extended on the
    # same ID are the same activity - their changes must still be narrated. A one-letter abbreviation change is not.
    cs1 = [T(1, "C300", task_name="Steel erection (columns & rafters)", target_drtn_hr_cnt=80, remain_drtn_hr_cnt=80),
           T(2, "D400", task_name="Slab pouring  part2", target_drtn_hr_cnt=40, remain_drtn_hr_cnt=40),
           T(3, "E500", task_name="Additional bulk MRP", target_drtn_hr_cnt=40, remain_drtn_hr_cnt=40)]
    cs2 = [T(1, "C300", task_name="Steel erection(columns&rafters)", target_drtn_hr_cnt=120, remain_drtn_hr_cnt=120),
           T(2, "D400", task_name="Slab Pouring", target_drtn_hr_cnt=64, remain_drtn_hr_cnt=64),
           T(3, "E500", task_name="Additional bulk MRQ", target_drtn_hr_cnt=120, remain_drtn_hr_cnt=120)]
    same5 = [T(i, f"S{i}", task_name=f"Unchanged step {i}") for i in range(4, 9)]
    xer(tmp / "cs1.xer", "2025-01-01 08:00", "2025-03-01 17:00", cs1 + same5)
    xer(tmp / "cs2.xer", "2025-02-01 08:00", "2025-03-01 17:00", cs2 + same5)
    Fc = facts_for(tmp / "cs1.xer", tmp / "cs2.xer")
    Cc = N.render_all(N.draft_claims(Fc), Fc)
    idx_c = {f["fact_id"]: f for f in Fc}
    od_narr = [idx_c[i]["activity"] for c in Cc for i in c["fact_ids"] if idx_c[i]["type"] == "original_duration_change"]
    check("same ID, name only re-punctuated or shortened -> same activity, changes still narrated (C300, D400)",
          od_narr == ["C300", "D400"] and all(f["confirmed"] for f in find(Fc, "activity_name_change") if f["activity"] != "E500")
          and all(r["status"] == "PASS" for r in N.validate(Cc, Fc)), od_narr)
    check("same ID, one-letter abbreviation change (MRP -> MRQ) -> not assumed the same activity (withheld)",
          find(Fc, "original_duration_change", "E500")[0].get("identity_unconfirmed") and "E500" not in od_narr)
    v, Fd, cmd = DR.diagnose(tmp / "df1.xer", tmp / "df2.xer")
    check("doctor: different projects (FIX FIRST) -> no same-ID name-change warning or rename advice on top",
          v == "FIX FIRST" and not any("changed name" in t for _, t in Fd), Fd)
    v, Fd, cmd = DR.diagnose(tmp / "cs1.xer", tmp / "cs2.xer")
    check("doctor: re-punctuated/shortened names not warned; abbreviation change warned (1 activity)",
          any(t.startswith("1 activities kept their Activity ID") for _, t in Fd), Fd)

    # real data (admin building): every floor's "Pour Part2" renamed to "Pour" (a name repeated on other floors)
    # is the same activity on each floor - its duration change is still narrated, not held back
    fl1, fl2 = [], []
    for k in range(4):
        fl1 += [T(10 * k + 1, f"F{k}1", task_name="Pour"), T(10 * k + 2, f"F{k}2", task_name="Pour Part2")]
        fl2 += [T(10 * k + 1, f"F{k}1", task_name="Pour"),
                T(10 * k + 2, f"F{k}2", task_name="Pour", target_drtn_hr_cnt=96, remain_drtn_hr_cnt=96)]
    xer(tmp / "fl1.xer", "2025-01-01 08:00", "2025-03-01 17:00", fl1)
    xer(tmp / "fl2.xer", "2025-02-01 08:00", "2025-03-01 17:00", fl2)
    Ffl = facts_for(tmp / "fl1.xer", tmp / "fl2.xer")
    odf = find(Ffl, "original_duration_change", "F02")
    Cfl = N.render_all(N.draft_claims(Ffl), Ffl)
    check("repeated per-floor name renamed: same activity, duration change narrated (F02 80 -> 96 h)",
          odf and not odf[0].get("identity_unconfirmed") and not find(Ffl, "activity_deleted")
          and any(c["template"] == "od_change" and odf[0]["fact_id"] in c["fact_ids"] for c in Cfl), odf)

    # CLI end to end: compare, then edits to the .md and to claims.json are caught
    out = tmp / "job"
    NS = [sys.executable, str(ROOT / "scripts" / "narrative.py")]
    r = subprocess.run(NS + ["compare", str(tmp / "p.xer"), str(tmp / "c.xer"), "--out", str(out),
                             "--project-prev", "P1", "--project-curr", "P1"], capture_output=True, text=True)
    check("CLI compare runs", r.returncode == 0 and Path(f"{out}-narrative.md").exists(), (r.stdout + r.stderr)[-300:])
    r = subprocess.run(NS + ["validate", str(out)], capture_output=True, text=True)
    check("CLI validate passes untouched output", r.returncode == 0, r.stdout[-300:])
    md = Path(f"{out}-narrative.md")
    original = md.read_text(encoding="utf-8")
    md.write_text(original.replace("01 Feb 2025", "08 Feb 2025", 1), encoding="utf-8")
    r = subprocess.run(NS + ["validate", str(out)], capture_output=True, text=True)
    check("CLI validate FAILS when the .md is edited by hand (Codex #3)", r.returncode == 1 and "NARRATIVE" in r.stdout,
          r.stdout[-300:])
    doc = json.loads(Path(f"{out}-claims.json").read_text(encoding="utf-8"))
    doc["claims"][0]["text"] = doc["claims"][0]["text"].replace("2025", "2024")
    Path(f"{out}-claims.json").write_text(json.dumps(doc), encoding="utf-8")
    r = subprocess.run(NS + ["validate", str(out)], capture_output=True, text=True)
    check("CLI validate FAILS when claim text is edited by hand", r.returncode == 1, r.stdout[-300:])
    fj = Path(f"{out}-facts.json")
    ft = fj.read_text(encoding="utf-8")
    fj.write_text(ft.replace("2025-03-14", "2025-03-21", 1), encoding="utf-8")
    r = subprocess.run(NS + ["validate", str(out)], capture_output=True, text=True)
    check("validate STOPS when facts.json changed after claims were made (stale run)", r.returncode != 0 and "STOPPED" in
          (r.stdout + r.stderr), (r.stdout + r.stderr)[-200:])
    fj.write_text(ft, encoding="utf-8")
    doc["claims"][0].pop("text")
    doc["claims"].append({"claim_id": "P1", "section": "recovery", "template": "planner",
                          "planner": {"text": "add a second steel crew", "by": "PM", "date": "2025-02-02"}})
    Path(f"{out}-claims.json").write_text(json.dumps(doc), encoding="utf-8")
    r = subprocess.run(NS + ["validate", str(out), "--write"], capture_output=True, text=True)
    check("validate --write rebuilds .md from edited claims and PASSES", r.returncode == 0
          and "Planner input (PM, 2025-02-02)" in md.read_text(encoding="utf-8"), r.stdout[-300:])

print(f"\n{passed} passed, {failed} failed, {skipped} skipped")
sys.exit(1 if failed else 0)

"""Doctor - check two P6 XER exports BEFORE writing the narrative, and say in plain words what to do.

  py scripts/doctor.py previous.xer current.xer [--out outputs/job]

It changes nothing. It explains the files and suggests the compare command:
- which project inside each file to use (exports often carry linked projects);
- whether the two files are really the same project, in the right order;
- blank / duplicate Activity IDs (compare would stop);
- activities that look renumbered (deleted + added, or an ID re-used, with the same name) -> renames-suggested.csv
  for the planner to confirm; IDs kept with a different name are warned;
- missing fields (longest path flag, remaining dates, float);
- invalid or backdated actual dates.
Verdict: READY, READY WITH WARNINGS, or FIX FIRST.
"""
import argparse, csv, re, sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from narrative import read_xer, dt, d, Schedule, IdentityError, same_activity_name  # noqa: E402

MIN_OVERLAP = 0.5          # share of current Activity IDs found in the previous file
# Share of matched IDs whose activity names also match. Real update pairs in 73 contractor XERs: 0.64-1.00.
# Unrelated projects sharing P6's default IDs (A1000, A1010...): 0.00-0.06. In between = same company template.
NAMES_SAME, NAMES_DIFFERENT = 0.8, 0.3


def norm(name):
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def projects(path):
    """Every project in the file that has activities: proj_id -> info."""
    t, fields = read_xer(path)
    hdr = Path(path).read_bytes()[:200].decode("latin-1").splitlines()[0].split("\t")
    version = hdr[1] if len(hdr) > 1 and hdr[0].startswith("ERMHDR") else "?"
    codes, names = defaultdict(set), defaultdict(dict)
    for r in t["TASK"]:
        c = (r.get("task_code") or "").strip()
        codes[r["proj_id"]].add(c)
        names[r["proj_id"]][c] = norm(r.get("task_name"))
    out = {}
    for p in t["PROJECT"]:
        if codes.get(p["proj_id"]):
            out[p["proj_id"]] = {"name": p["proj_short_name"], "data_date": dt(p.get("last_recalc_date")),
                                 "codes": codes[p["proj_id"]] - {""}, "names": names[p["proj_id"]]}
    return version, out, fields.get("TASK", [])


def diagnose(prev_path, curr_path, rename_csv=None):
    """Returns (verdict, findings, command). findings: list of (level, text); level FIX / WARN / OK / INFO."""
    F = []
    pv, P, pfields = projects(prev_path)
    cv, C, cfields = projects(curr_path)
    F.append(("INFO", f"Previous file: P6 export version {pv}, {len(P)} project(s) with activities."))
    F.append(("INFO", f"Current file: P6 export version {cv}, {len(C)} project(s) with activities."))
    if not P or not C:
        F.append(("FIX", "One of the files has no activities. Re-export the project from P6 (File > Export > XER)."))
        return "FIX FIRST", F, None

    # pick the pair of projects that share the most Activity IDs WITH the same names (IDs alone are not enough:
    # many companies keep P6's default numbering, so unrelated projects share A1000, A1010...)
    def score(a, b):
        common = P[a]["codes"] & C[b]["codes"]
        overlap = len(common) / max(len(C[b]["codes"]), 1)
        same = sum(P[a]["names"][c] == C[b]["names"][c] for c in common) / len(common) if common else 0.0
        return overlap * same, overlap, same
    _, overlap, same, pa, cb = max((*score(a, b), a, b) for a in P for b in C)
    pname, cname = P[pa]["name"], C[cb]["name"]
    for label, projs, pick in (("previous", P, pa), ("current", C, cb)):
        if len(projs) > 1:
            others = ", ".join(f"{v['name']} ({len(v['codes'])} activities)" for k, v in projs.items() if k != pick)
            F.append(("WARN", f"The {label} file holds {len(projs)} projects. Using {projs[pick]['name']} "
                              f"({len(projs[pick]['codes'])} activities) because it matches the other file best. "
                              f"Also inside: {others}."))
    if overlap < MIN_OVERLAP:
        F.append(("FIX", f"Only {overlap:.0%} of the current Activity IDs exist in the previous file. These may not be "
                         f"two updates of the same project, or most activities were renumbered. Check the files."))
    elif same < NAMES_DIFFERENT:
        F.append(("FIX", f"The Activity IDs match ({overlap:.0%}) but only {same:.0%} of their names do. These look like "
                         f"different projects that both use P6's default numbering. Check you exported the right projects."))
    elif same < NAMES_SAME:
        F.append(("WARN", f"{overlap:.0%} of the Activity IDs match, but only {same:.0%} of those have the same name. "
                          f"Either a heavy re-plan, or a different project built from the same template. Please confirm "
                          f"'{pname}' and '{cname}' are the same project."))
    else:
        F.append(("OK", f"Same project ('{pname}' -> '{cname}'): {overlap:.0%} of the current Activity IDs are in the "
                        f"previous file and {same:.0%} of them have the same name."))

    words = lambda n: {w for w in re.split(r"[^0-9a-z]+", n.lower()) if w}
    if overlap >= MIN_OVERLAP and same >= NAMES_DIFFERENT and not (words(pname) & words(cname)):
        F.append(("WARN", f"The project names '{pname}' and '{cname}' share no word. A company template reused on "
                          f"another project looks exactly like an update. Please confirm they are the same project."))

    pdd, cdd = P[pa]["data_date"], C[cb]["data_date"]
    if not pdd or not cdd:
        F.append(("FIX", "A data date is missing. Schedule the project in P6 (F9) and export again."))
    elif cdd < pdd:
        F.append(("FIX", f"The 'previous' file has the later data date ({d(pdd)} vs {d(cdd)}). The files look swapped."))
    elif cdd == pdd:
        F.append(("FIX", f"Both files have the same data date ({d(cdd)}). Was the update scheduled (F9) before export?"))
    else:
        F.append(("OK", f"Data dates in order: {d(pdd)} -> {d(cdd)} ({(cdd - pdd).days} days)."))

    # identity
    sched = {}
    for label, path, name in (("previous", prev_path, pname), ("current", curr_path, cname)):
        try:
            sched[label] = Schedule(path, name, [])
        except IdentityError as e:
            F.append(("FIX", f"{label.capitalize()} file: {str(e).split(' - ', 1)[-1]}. Give every activity a unique ID in P6."))
    if len(sched) < 2:
        return "FIX FIRST", F, None
    prev, curr = sched["previous"], sched["current"]
    if all(r.get("status_code") == "TK_NotStart" for r in prev.tasks.values()):
        F.append(("INFO", "The previous file has no progress (it looks like a baseline). Fine for a first update."))

    # fields the narrative needs
    for fld, why in (("driving_path_flag", "longest-path changes"), ("total_float_hr_cnt", "float-critical changes"),
                     ("reend_date", "P6's displayed finish dates for unfinished work (early dates used instead)")):
        for label, flds in (("previous", pfields), ("current", cfields)):
            if fld not in flds:
                F.append(("WARN", f"The {label} file has no {fld}: {why} will be limited."))

    # renumbered activities. An ID kept with a different name may have been re-used for another activity
    # (IDs re-sequenced - real oil & gas pair 2012), so it is a candidate on both sides too.
    gone = [c for c in prev.tasks if c not in curr.tasks]
    new = [c for c in curr.tasks if c not in prev.tasks]
    # (not when the files already look like different projects - that FIX finding comes first)
    renamed = [] if overlap < MIN_OVERLAP or same < NAMES_DIFFERENT else sorted(
        c for c in curr.tasks if c in prev.tasks
        and not same_activity_name(prev.tasks[c].get("task_name"), curr.tasks[c].get("task_name")))
    if renamed:
        F.append(("WARN", f"{len(renamed)} activities kept their Activity ID but changed name (e.g. {renamed[0]}: "
                          f"'{prev.tasks[renamed[0]].get('task_name', '')}' -> '{curr.tasks[renamed[0]].get('task_name', '')}'). "
                          f"The ID may now hold a different activity, so the narrative leaves their compared changes out. "
                          f"Same activity renamed: add a row ID,ID to the rename map. Re-numbered: map old ID to new ID."))
    suggestions = []
    if (gone or renamed) and (new or renamed):
        g_by, n_by = defaultdict(list), defaultdict(list)
        for c in gone + renamed:
            g_by[norm(prev.tasks[c].get("task_name"))].append(c)
        for c in new + renamed:
            n_by[norm(curr.tasks[c].get("task_name"))].append(c)
        for name, olds in g_by.items():
            if name and len(olds) == 1 and len(n_by.get(name, [])) == 1:
                suggestions.append((olds[0], n_by[name][0], prev.tasks[olds[0]].get("task_name", "")))
    if suggestions:
        F.append(("WARN", f"{len(gone)} activities deleted and {len(new)} added"
                          f"{f' ({len(renamed)} IDs changed name)' if renamed else ''}; "
                          f"{len(suggestions)} pairs have the same name "
                          f"and may have been renumbered. Check renames-suggested.csv, delete wrong rows, then use --map."))
        if rename_csv:
            with open(rename_csv, "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(["old", "new", "name (for checking; ignored by --map)"])
                w.writerows(sorted(suggestions))
    elif gone or new:
        F.append(("INFO", f"{len(gone)} activities deleted and {len(new)} added (no same-name pairs, so no renames suggested)."))

    # actual dates
    after, back = [], []
    for c, r in curr.tasks.items():
        for fld in ("act_start_date", "act_end_date"):
            v = dt(r.get(fld))
            if v and cdd and v > cdd:
                after.append(c)
        if c in prev.tasks and pdd:
            p = prev.tasks[c]
            ae, ast = dt(r.get("act_end_date")), dt(r.get("act_start_date"))
            if (r.get("status_code") == "TK_Complete" and p.get("status_code") != "TK_Complete" and ae and ae <= pdd) or \
               (ast and ast <= pdd and not dt(p.get("act_start_date"))):
                back.append(c)
    if after:
        ids = sorted(set(after))
        F.append(("WARN", f"{len(ids)} activities have actual dates after the data date (impossible): {', '.join(ids[:8])}"
                          f"{' ...' if len(ids) > 8 else ''}. Fix in P6 if you can; the narrative will flag them."))
    if back:
        ids = sorted(set(back))
        F.append(("WARN", f"{len(ids)} activities were progressed this period but dated on or before the previous data date: "
                          f"{', '.join(ids[:8])}{' ...' if len(ids) > 8 else ''}. They won't count as progress this period."))

    cmd = f'py scripts/narrative.py compare "{prev_path}" "{curr_path}" --out outputs/<job>'
    if len(P) > 1:
        cmd += f' --project-prev "{pname}"'
    if len(C) > 1:
        cmd += f' --project-curr "{cname}"'
    if suggestions:
        cmd += " --map renames.csv   (after checking renames-suggested.csv)"
    levels = {lvl for lvl, _ in F}
    verdict = "FIX FIRST" if "FIX" in levels else "READY WITH WARNINGS" if "WARN" in levels else "READY"
    return verdict, F, (None if verdict == "FIX FIRST" else cmd)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("previous"); ap.add_argument("current")
    ap.add_argument("--out", help="folder/prefix for renames-suggested.csv (default: next to the current file)")
    a = ap.parse_args()
    rename_csv = f"{a.out}-renames-suggested.csv" if a.out else str(Path(a.current).with_name("renames-suggested.csv"))
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    verdict, F, cmd = diagnose(a.previous, a.current, rename_csv)
    for lvl, text in F:
        print(f"[{lvl:4}] {text}")
    print(f"\nVERDICT: {verdict}")
    if cmd:
        print(f"Run:  {cmd}")
    sys.exit(1 if verdict == "FIX FIRST" else 0)


if __name__ == "__main__":
    main()

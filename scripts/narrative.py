"""Schedule Update Narrative - compare two P6 XER exports, compute cited facts, draft and validate a narrative.

  py scripts/narrative.py compare previous.xer current.xer --out outputs/job [--project ID] [--map renames.csv]
  py scripts/narrative.py validate outputs/job [--write]

Code computes facts (facts.json). The narrative is a list of claims (claims.json). A claim never holds its own
numbers: it names a fixed template and the facts that fill it, so no value can land on the wrong activity or
date role. Other wording: fixed neutral phrases ("phrase") or attributed planner input ("planner").
validate re-renders every claim and checks the delivered .md matches; Blank/duplicate Activity IDs stop the run.
"""
import argparse, csv, hashlib, json, re, sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

# ------------------------------------------------------------------ XER reading
def read_xer(path):
    raw = Path(path).read_bytes()
    for enc in ("utf-8", "cp1252", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    tables, fields, cur = defaultdict(list), {}, None
    for line in text.splitlines():
        if line.startswith("%T\t"):
            cur = line.split("\t")[1]
        elif line.startswith("%F\t"):
            fields[cur] = line.split("\t")[1:]
        elif line.startswith("%R\t") and cur:
            tables[cur].append(dict(zip(fields[cur], line.split("\t")[1:])))
    return tables, fields


def dt(s):
    s = (s or "").strip()
    if not s:
        return None
    for f in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, f)
        except ValueError:
            pass
    return None


def num(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


class IdentityError(Exception):
    """Blank or duplicate Activity IDs - comparing would silently drop rows, so we refuse."""


class Schedule:
    """One project out of an XER (linked-project stub rows are kept aside, not mixed in)."""

    def __init__(self, path, project=None, warn=None):
        self.path = str(path)
        self.t, self.fields = read_xer(path)
        warn = warn if warn is not None else []
        counts = defaultdict(int)
        for r in self.t["TASK"]:
            counts[r["proj_id"]] += 1
        projs = {p["proj_id"]: p for p in self.t["PROJECT"]}
        if project:
            hits = [p for p in projs.values() if p["proj_short_name"].lower() == project.lower()]
            if not hits:
                sys.exit(f"{path}: project '{project}' not found. Projects: {[p['proj_short_name'] for p in projs.values()]}")
            pid = hits[0]["proj_id"]
        else:
            pid = max(counts, key=counts.get)
            others = [projs[k]["proj_short_name"] for k in counts if k != pid and k in projs]
            if others:
                warn.append({"type": "warning", "code": "linked_projects",
                             "file": self.path, "detail": f"also contains rows of {others}; used {projs[pid]['proj_short_name']} (most activities). Pass --project to choose."})
        self.proj = projs[pid]
        self.pid = pid
        self.name = self.proj["proj_short_name"]
        self.data_date = dt(self.proj.get("last_recalc_date"))
        self.finish = dt(self.proj.get("scd_end_date"))
        self.cal_hours = {c["clndr_id"]: num(c.get("day_hr_cnt")) or 8.0 for c in self.t["CALENDAR"]}
        self.tasks, self.by_id, errors = {}, {}, []
        for r in self.t["TASK"]:
            self.by_id[r["task_id"]] = r
            if r["proj_id"] != pid:
                continue
            code = (r.get("task_code") or "").strip()
            if not code:
                errors.append(f"blank Activity ID (task_id {r.get('task_id')}, '{r.get('task_name')}')")
                continue
            if code in self.tasks:
                errors.append(f"duplicate Activity ID {code}")
            self.tasks[code] = r
        if errors:
            raise IdentityError(f"{path}: cannot compare - " + "; ".join(errors[:10]) +
                                (f" (+{len(errors) - 10} more)" if len(errors) > 10 else ""))
        self.links, self.external = set(), []
        for r in self.t["TASKPRED"]:
            p, s = self.by_id.get(r["pred_task_id"]), self.by_id.get(r["task_id"])
            if not p or not s:
                continue
            inside_p, inside_s = p["proj_id"] == pid, s["proj_id"] == pid
            if inside_p and inside_s:
                self.links.add((p["task_code"], s["task_code"], r.get("pred_type", ""), num(r.get("lag_hr_cnt")) or 0.0))
            elif inside_p or inside_s:
                self.external.append((p["task_code"], s["task_code"], r.get("pred_type", "")))

    def day_hr(self, code):
        return self.cal_hours.get(self.tasks[code].get("clndr_id"), 8.0)

    # status-dependent dates (rev 2 date rules)
    # Unfinished work uses P6's REMAINING early dates (restart_date / reend_date) - the dates P6 shows in its
    # Start/Finish columns. Plain early dates can differ (e.g. EC1390 early_end 01-Aug vs reend 02-Aug in the
    # real test pair); checked against P6's own Schedule Comparison report.
    def start(self, code):
        r = self.tasks[code]
        return dt(r.get("act_start_date")) or dt(r.get("restart_date")) or dt(r.get("early_start_date"))

    def finish_of(self, code):
        r = self.tasks[code]
        if r.get("status_code") == "TK_Complete":
            return dt(r.get("act_end_date")) or dt(r.get("reend_date")) or dt(r.get("early_end_date"))
        return dt(r.get("reend_date")) or dt(r.get("early_end_date"))


def d(x):
    return x.strftime("%Y-%m-%d") if x else None


def cal_days(a, b):
    """Calendar days from a to b, by date (time of day ignored)."""
    if not a or not b:
        return None
    return (b.date() - a.date()).days

# ------------------------------------------------------------------ facts
class RenameError(Exception):
    """Rename map is not a clean one-to-one correspondence."""


class Match(dict):
    """Current Activity ID -> previous Activity ID. .confirmed = current IDs the planner's map vouches for
    (a renamed pair, or a row X,X = 'same activity')."""
    confirmed = frozenset()


def norm_name(name):
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def same_activity_name(a, b):
    """True when two names are the same wording: equal ignoring case/spacing/punctuation ('beams & columns' =
    'beams&columns'), or one name's words all appear in the other ('Slab pouring part2' -> 'Slab pouring').
    Deliberately no fuzzy ratio: 'bulk MRP' vs 'bulk MRQ' are different activities one letter apart."""
    ta, tb = re.findall(r"[a-z0-9]+", (a or "").lower()), re.findall(r"[a-z0-9]+", (b or "").lower())
    if "".join(ta) == "".join(tb):
        return True
    sa, sb = set(ta), set(tb)
    return bool(sa and sb and (sa <= sb or sb <= sa))


def match_table(prev, curr, pairs):
    """Current Activity ID -> previous Activity ID, one-to-one. pairs = [(old, new), ...] from the rename map.
    A renamed-away old code never also matches itself; ambiguous maps are rejected (Codex review #2, #3)."""
    olds, news, errs = [o for o, _ in pairs], [n for _, n in pairs], []
    for label, codes in (("old", olds), ("new", news)):
        dup = sorted({c for c in codes if codes.count(c) > 1})
        if dup:
            errs.append(f"{label} ID used more than once: {dup}")
    errs += [f"old ID {o} is not in the previous export" for o in olds if o not in prev.tasks]
    errs += [f"new ID {n} is not in the current export" for n in news if n not in curr.tasks]
    if errs:
        raise RenameError("rename map rejected - " + "; ".join(errs))
    to_old = {n: o for o, n in pairs}
    m = Match()
    m.confirmed = frozenset(news)
    for c in curr.tasks:
        if c in to_old:
            m[c] = to_old[c]
        elif c in prev.tasks and c not in olds:
            m[c] = c
    used = [v for v in m.values()]
    assert len(used) == len(set(used)), "internal: previous activity matched twice"
    return m


LIST_CAP = 15
LIST_LABELS = {
    "original_duration_change": "original duration changes", "remaining_estimate_change": "remaining estimate changes on unfinished activities",
    "logic_change": "relationship changes", "constraint_change": "constraint changes",
    "activity_added": "activities added", "activity_deleted": "activities deleted",
    "actual_date_correction": "actual date corrections", "warning": "data warnings",
    "lookahead": "activities in the look-ahead window (longest path first)",
    "activity_name_change": "Activity IDs whose activity name changed",
}
# Facts that compare the previous row of an Activity ID with its current row. If the ID now holds an activity with a
# different name (IDs re-sequenced between updates - real oil & gas pair 2012), these compare two different activities.
COMPARED = {"completed_late", "planned_not_started", "actual_date_correction", "forecast_finish_movement",
            "forecast_start_movement", "original_duration_change", "remaining_estimate_change", "milestone_movement",
            "constraint_change", "logic_change", "longest_path_entered", "longest_path_left",
            "float_critical_entered", "float_critical_left"}


def compute_facts(prev, curr, match, lp_threshold_h=0.0, near_days=10, look_days=30):
    """match: current Activity ID -> previous Activity ID (from match_table)."""
    F, n = [], defaultdict(int)
    pcode = lambda c: match.get(c, c)
    inv = {p: c for c, p in match.items()}                      # previous -> current
    GONE = "∅"                                             # marks a previous code with no current match
    to_cur = lambda c: inv.get(c, GONE + c)

    def add(ftype, activity=None, **kw):
        key = f"{ftype}-{activity or 'PRJ'}"
        n[key] += 1
        fid = f"F-{ftype.upper().replace('_', '-')}-{activity or 'PRJ'}-{n[key]:02d}"
        rec = {"fact_id": fid, "type": ftype}
        if activity:
            rec["activity"] = activity
            src = curr.tasks.get(activity) or prev.tasks.get(activity)
            rec["activity_name"] = (src or {}).get("task_name", "")
        rec.update(kw)
        F.append(rec)
        return rec

    pdd, cdd = prev.data_date, curr.data_date
    add("data_dates", previous=d(pdd), current=d(cdd), delta={"calendar_days": cal_days(pdd, cdd)},
        previous_project=prev.name, current_project=curr.name,
        previous_file=Path(prev.path).name, current_file=Path(curr.path).name,
        window=f"({d(pdd)}, {d(cdd)}]")
    if prev.finish and curr.finish:
        delta = cal_days(prev.finish, curr.finish)
        add("project_finish_movement", metric="PROJECT.scd_end_date", previous=d(prev.finish), current=d(curr.finish),
            delta={"calendar_days": delta, "basis": "calendar days between dates"},
            direction="later" if delta > 0 else "earlier" if delta < 0 else "unchanged")

    common = [c for c in curr.tasks if c in match]
    in_window = lambda x: x is not None and pdd is not None and cdd is not None and pdd < x <= cdd

    name_changes = {}
    for c in sorted(common):
        p, q, pc = prev.tasks[pcode(c)], curr.tasks[c], pcode(c)
        if norm_name(p.get("task_name")) != norm_name(q.get("task_name")):
            by_map = c in getattr(match, "confirmed", ())
            similar = same_activity_name(p.get("task_name"), q.get("task_name"))
            name_changes[c] = add("activity_name_change", c, metric="TASK.task_name", previous=p.get("task_name", ""),
                                  current=q.get("task_name", ""), previous_activity=pc, confirmed=by_map or similar,
                                  basis="rename map" if by_map else "same words" if similar else "different words",
                                  withheld=0)
        ps, qs = p.get("status_code"), q.get("status_code")
        ttype = q.get("task_type", "")
        hr = curr.day_hr(c)
        a_end, a_start = dt(q.get("act_end_date")), dt(q.get("act_start_date"))
        if qs == "TK_Complete" and ps == "TK_Complete" and in_window(dt(p.get("act_end_date"))):
            add("warning", code="inconsistent_source", activity=c,
                detail=f"{c} was already complete in the previous export with an actual finish after its data date")
        if qs == "TK_Complete" and ps != "TK_Complete" and in_window(a_end):
            add("completed_in_period", c, metric="TASK.act_end_date", current=d(a_end))
            prev_fc = prev.finish_of(pc)
            if ps != "TK_Complete" and prev_fc and a_end.date() > prev_fc.date():
                add("completed_late", c, metric="TASK.act_end_date vs previous remaining early finish",
                    previous=d(prev_fc), current=d(a_end),
                    delta={"calendar_days": cal_days(prev_fc, a_end), "basis": "calendar days between dates"}, direction="later")
        if qs == "TK_Complete" and ps != "TK_Complete" and a_end and pdd and a_end <= pdd:
            add("warning", code="backdated_actual", activity=c,
                detail=f"{c} was completed this period with an actual finish of {d(a_end)}, on or before the previous data date; not counted as completed in period")
        if a_start and in_window(a_start) and not dt(p.get("act_start_date")):
            add("started_in_period", c, metric="TASK.act_start_date", current=d(a_start))
        if a_start and pdd and a_start <= pdd and not dt(p.get("act_start_date")):
            add("warning", code="backdated_actual", activity=c,
                detail=f"{c} was started this period with an actual start of {d(a_start)}, on or before the previous data date; not counted as started in period")
        for fld in ("act_start_date", "act_end_date"):
            av = dt(q.get(fld))
            if av and cdd and av > cdd:
                add("warning", code="actual_after_data_date", activity=c,
                    detail=f"{c} has {fld} {d(av)}, after the current data date; invalid actual, check the update")
        pes = prev.start(pc)
        if ps == "TK_NotStart" and qs == "TK_NotStart" and pes and cdd and pes <= cdd:
            add("planned_not_started", c, metric="previous remaining early start", previous=d(pes),
                note="was forecast to start by the current data date; still not started")
        for fld in ("act_start_date", "act_end_date"):
            pv, cv = dt(p.get(fld)), dt(q.get(fld))
            if pv and cv and pv != cv:
                add("actual_date_correction", c, metric=f"TASK.{fld}", previous=d(pv), current=d(cv),
                    delta={"calendar_days": cal_days(pv, cv)}, flags=["retrospective edit of an actual date"])
        if qs != "TK_Complete":
            pf, qf = prev.finish_of(pc), curr.finish_of(c)
            dd = cal_days(pf, qf)
            if dd:
                add("forecast_finish_movement", c, metric="finish (actual if complete, else remaining early finish)", previous=d(pf), current=d(qf),
                    delta={"calendar_days": dd, "basis": "calendar days between dates"},
                    direction="later" if dd > 0 else "earlier", longest_path=q.get("driving_path_flag") == "Y")
        if qs == "TK_NotStart":
            ds = cal_days(prev.start(pc), curr.start(c))
            if ds:
                add("forecast_start_movement", c, metric="remaining early start (restart_date)", previous=d(prev.start(pc)),
                    current=d(curr.start(c)), delta={"calendar_days": ds, "basis": "calendar days between dates"},
                    direction="later" if ds > 0 else "earlier")
        po, qo = num(p.get("target_drtn_hr_cnt")), num(q.get("target_drtn_hr_cnt"))
        if po is not None and qo is not None and abs(qo - po) > 0.01:
            add("original_duration_change", c, metric="TASK.target_drtn_hr_cnt", previous=round(po, 2), current=round(qo, 2),
                delta={"hours": round(qo - po, 2), "days": round((qo - po) / hr, 2),
                       "basis": f"hours / {hr:g} h per day (activity calendar)"},
                previous_days=round(po / hr, 2), current_days=round(qo / hr, 2),
                direction="longer" if qo > po else "shorter")
        pr, qr = num(p.get("remain_drtn_hr_cnt")), num(q.get("remain_drtn_hr_cnt"))
        if pr is not None and qr is not None and abs(qr - pr) > 0.01:
            add("remaining_estimate_change", c, metric="TASK.remain_drtn_hr_cnt", previous=round(pr, 2), current=round(qr, 2),
                on_completion=q.get("status_code") == "TK_Complete",   # kept as a fact (P6 lists it), not narrated
                delta={"hours": round(qr - pr, 2), "days": round((qr - pr) / hr, 2),
                       "basis": f"hours / {hr:g} h per day (activity calendar)"},
                previous_days=round(pr / hr, 2), current_days=round(qr / hr, 2),
                direction="higher" if qr > pr else "lower",
                note="estimate change only - not interpreted as productivity without planner input")
        if ttype in ("TT_Mile", "TT_FinMile"):
            fn = curr.start if ttype == "TT_Mile" else curr.finish_of
            pfn = prev.start if ttype == "TT_Mile" else prev.finish_of
            pv, cv = pfn(pc), fn(c)
            dd = cal_days(pv, cv)
            if dd:
                add("milestone_movement", c, metric="start" if ttype == "TT_Mile" else "finish",
                    previous=d(pv), current=d(cv), delta={"calendar_days": dd, "basis": "calendar days between dates"},
                    direction="later" if dd > 0 else "earlier")
        for k in ("", "2"):
            pt, ct = p.get(f"cstr_type{k}", ""), q.get(f"cstr_type{k}", "")
            pdv, cdv = p.get(f"cstr_date{k}", ""), q.get(f"cstr_date{k}", "")
            if (pt, pdv) != (ct, cdv):
                add("constraint_change", c, metric=f"TASK.cstr_type{k}/cstr_date{k}",
                    previous=f"{pt} {pdv[:10]}".strip(), current=f"{ct} {cdv[:10]}".strip())

    # added / deleted
    for c in sorted(set(curr.tasks) - set(common)):
        add("activity_added", c, current=d(curr.start(c)))
    prev_used = set(match.values())
    for c in sorted(set(prev.tasks) - prev_used):
        add("activity_deleted", c)
        F[-1]["activity_name"] = prev.tasks[c].get("task_name", "")

    # logic
    pl = {(to_cur(a), to_cur(b), t, round(l, 2)) for a, b, t, l in prev.links}
    cl = {(a, b, t, round(l, 2)) for a, b, t, l in curr.links}
    for a, b, t, l in sorted(pl - cl):
        add("logic_change", b, change="removed", predecessor=a, successor=b, relationship=t, lag_hours=l)
    for a, b, t, l in sorted(cl - pl):
        add("logic_change", b, change="added", predecessor=a, successor=b, relationship=t, lag_hours=l)

    # longest path / float-critical (kept separate - Codex #2)
    def members(s, rule):
        out = set()
        for c, r in s.tasks.items():
            if rule == "lp" and r.get("driving_path_flag") == "Y":
                out.add(c)
            if rule == "tf":
                tf = num(r.get("total_float_hr_cnt"))
                if tf is not None and tf <= lp_threshold_h and r.get("status_code") != "TK_Complete":
                    out.add(c)
        return out

    has = lambda s, fld: fld in s.fields.get("TASK", [])
    has_lp = has(curr, "driving_path_flag")
    for rule, ftype, fld in (("lp", "longest_path_membership", "driving_path_flag"),
                             ("tf", "float_critical_membership", "total_float_hr_cnt")):
        if not has(curr, fld):
            add("warning", code=f"no_{fld}", detail=f"current XER has no {fld}; {ftype.split('_m')[0]} not reported")
            continue
        cm = members(curr, rule)
        basis = "TASK.driving_path_flag = Y" if rule == "lp" else f"total float <= {lp_threshold_h:g} h, unfinished"
        if not has(prev, fld):
            add(ftype, metric=basis, current_count=len(cm), current=sorted(cm), previous_count=None,
                flags=["previous export has no " + fld + " - no comparison"])
            add("warning", code=f"no_previous_{fld}", detail=f"previous XER has no {fld}; entered/left not reported")
            continue
        pm = members(prev, rule)
        pm_cur = {to_cur(c) for c in pm}                     # previous members, in current IDs
        add(ftype, metric=basis, previous_count=len(pm), current_count=len(cm), current=sorted(cm))
        for c in sorted(cm - pm_cur):
            add(ftype.replace("membership", "entered"), c, metric=basis)
        for c in sorted(pm_cur - cm):
            if c.startswith(GONE):
                add(ftype.replace("membership", "left"), c[len(GONE):], metric=basis, reason_code="deleted")
                continue
            done = curr.tasks[c].get("status_code") == "TK_Complete"
            add(ftype.replace("membership", "left"), c, metric=basis,
                reason_code="completed" if done else "still_unfinished")

    # longest-path sequence only where relationships connect the members
    if has_lp:
        lp = members(curr, "lp")
        adj, succ, pred = defaultdict(set), defaultdict(set), defaultdict(set)
        for a, b, t, l in curr.links:
            if a in lp and b in lp:
                adj[a].add(b); adj[b].add(a); succ[a].add(b); pred[b].add(a)
        seen = set()
        for c in sorted(lp, key=lambda x: curr.start(x) or datetime.max):
            if c in seen or not adj[c]:
                continue
            comp, stack = [], [c]
            while stack:
                x = stack.pop()
                if x in seen:
                    continue
                seen.add(x); comp.append(x); stack.extend(adj[x] - seen)
            comp.sort(key=lambda x: (curr.start(x) or datetime.max, x))
            sources = [x for x in comp if not pred[x]]
            sinks = [x for x in comp if not succ[x]]
            chain = all(len(succ[x]) <= 1 and len(pred[x]) <= 1 for x in comp) and len(sources) == 1 and len(sinks) == 1
            add("longest_path_sequence", metric="directed TASKPRED links among longest-path activities",
                activities=comp, count=len(comp), sources=sources, sinks=sinks, is_chain=chain,
                first=sources[0] if chain else None, last=sinks[0] if chain else None)

    # near-critical
    for c, r in curr.tasks.items():
        tf = num(r.get("total_float_hr_cnt"))
        if tf is None or r.get("status_code") == "TK_Complete":
            continue
        days = tf / curr.day_hr(c)
        if lp_threshold_h < tf and days <= near_days:
            add("near_critical", c, metric="TASK.total_float_hr_cnt", current=round(tf, 2),
                delta={"days": round(days, 2), "basis": f"hours / {curr.day_hr(c):g} h per day"})

    # look-ahead: starts, finishes, ongoing
    if cdd:
        end = cdd + timedelta(days=look_days)
        lp_first = lambda x: (curr.tasks[x].get("driving_path_flag") != "Y", curr.start(x) or datetime.max, x)
        for c in sorted(curr.tasks, key=lp_first):
            r = curr.tasks[c]
            if r.get("status_code") == "TK_Complete":
                continue
            s, f = curr.start(c), curr.finish_of(c)
            kinds = []
            if r.get("status_code") == "TK_Active":
                kinds.append("is ongoing")
            if r.get("status_code") == "TK_NotStart" and s and cdd < s <= end:
                kinds.append("starts")
            if f and cdd < f <= end:
                kinds.append("finishes")
            if kinds:
                add("lookahead", c, kinds=kinds, start=d(s), finish=d(f),
                    longest_path=r.get("driving_path_flag") == "Y", window=f"({d(cdd)}, {d(end)}]")

    # same Activity ID, different name, not confirmed by the map: the compared facts stay (flagged, in the evidence)
    # but are not narrated or counted - they may compare two different activities.
    unconfirmed = {c for c, f in name_changes.items() if not f["confirmed"]}
    for f in F:
        if f["type"] in COMPARED:
            hit = {x for x in (f.get("activity"), f.get("predecessor")) if x in unconfirmed}
            if hit:
                f["identity_unconfirmed"] = True
                for x in hit:
                    name_changes[x]["withheld"] += 1
    if unconfirmed:
        ex = name_changes[sorted(unconfirmed)[0]]
        add("warning", code="activity_id_name_changed",
            detail=f"{len(unconfirmed)} activities kept their Activity ID but have a different name than in the previous "
                   f"update (e.g. {ex['activity']}: '{ex['previous']}' -> '{ex['current']}'). The ID may now hold a different "
                   f"activity, so {sum(name_changes[c]['withheld'] for c in unconfirmed)} changes compared on these IDs are "
                   f"flagged in the evidence and left out of the narrative. Planner to confirm: same activity renamed -> "
                   f"map row ID,ID; IDs re-sequenced -> map old ID to new ID (--map).")

    # display summaries (counts are facts too, so a summary sentence can cite them)
    mv = [f for f in F if f["type"] == "forecast_finish_movement" and not f.get("identity_unconfirmed")]
    for direction in ("later", "earlier"):
        grp = [f for f in mv if f["direction"] == direction]
        if grp:
            add("movement_summary", metric=f"unfinished activities forecast to finish {direction}",
                direction=direction, count=len(grp), on_longest_path=sum(1 for f in grp if f.get("longest_path")),
                max_days=max(abs(f["delta"]["calendar_days"]) for f in grp))

    for a, b, t in curr.external:
        add("warning", code="external_relationship", detail=f"{a} -> {b} ({t}) links outside the project; not analysed")

    # long lists are capped in the narrative; the count is a fact so the summary sentence can cite it
    for ftype, label in LIST_LABELS.items():
        cnt = sum(1 for f in F if f["type"] == ftype and not f.get("on_completion") and not f.get("identity_unconfirmed"))
        if cnt > LIST_CAP:
            add("list_summary", list_type=ftype, items=label, count=cnt, shown=LIST_CAP)
    return F

# ------------------------------------------------------------------ claims: fixed templates filled from facts
# A claim never carries its own numbers. It names a template and the facts that fill it, so a value can't
# be attached to the wrong activity or date role. Other wording: fixed neutral phrases ("phrase") or attributed
# planner input ("planner"). Headings are fixed section ids. (Codex code reviews #1 and #2.)

def fmt_date(s):
    return datetime.strptime(s, "%Y-%m-%d").strftime("%d %b %Y") if s else "?"


def _g(f, path):
    """Field lookup with a few derived names."""
    if path == "days":
        return abs(f["delta"]["calendar_days"])
    if path == "basis":
        return f["delta"]["basis"]
    if path == "rel":
        return f["relationship"].replace("PR_", "")
    if path == "label":
        return "longest path" if f["type"].startswith("longest") else "float-critical set"
    cur = f
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            raise KeyError(path)
        cur = cur[part]
    return cur


FORMATS = {
    "date": fmt_date,
    "list": lambda v: ", ".join(v),
    "and": lambda v: " and ".join(v),
    "none": lambda v: v or "none",
    "lp_note": lambda v: " It is on the longest path." if v else "",
    "lp_dash": lambda v: " - on the longest path" if v else "",
    "noun": lambda v: "unfinished activity is" if v == 1 else "unfinished activities are",
}

REASON = " [REASON: planner to confirm]"
#  id: (fact types per position, condition on the facts, format string)
TEMPLATES = {
    "data_dates": (("data_dates",), None, "This update moves the data date from {0.previous:date} to {0.current:date}."),
    "finish_moved": (("project_finish_movement",), lambda f: f[0]["direction"] in ("later", "earlier"),
                     "The forecast project finish moved {0.direction} by {0.days} calendar days, "
                     "from {0.previous:date} to {0.current:date}." + REASON),
    "finish_unchanged": (("project_finish_movement",), lambda f: f[0]["direction"] == "unchanged",
                         "The forecast project finish is unchanged at {0.current:date}."),
    "completed": (("completed_in_period",), None, "{0.activity} {0.activity_name} was completed on {0.current:date}."),
    "started": (("started_in_period",), None, "{0.activity} {0.activity_name} started on {0.current:date}."),
    "completed_late": (("completed_late",), None, "{0.activity} {0.activity_name} finished on {0.current:date}, "
                       "{0.days} calendar days after its previous forecast of {0.previous:date}."),
    "not_started": (("planned_not_started",), None, "{0.activity} {0.activity_name} was forecast to start by "
                    "{0.previous:date} and has not started." + REASON),
    "membership": (("longest_path_membership|float_critical_membership",), lambda f: f[0].get("previous_count") is not None,
                   "The {0.label} ({0.metric}) has {0.current_count} activities, compared with {0.previous_count} "
                   "in the previous update."),
    "membership_now": (("longest_path_membership|float_critical_membership",), lambda f: f[0].get("previous_count") is None,
                       "The {0.label} ({0.metric}) has {0.current_count} activities. The previous export has no data "
                       "to compare with."),
    "lp_chain": (("longest_path_sequence",), lambda f: f[0]["is_chain"],
                 "A longest-path chain of {0.count} activities runs from {0.first} to {0.last}."),
    "lp_network": (("longest_path_sequence",), lambda f: not f[0]["is_chain"],
                   "The longest path forms a connected network of {0.count} activities, starting at {0.sources:list} "
                   "and ending at {0.sinks:list}."),
    "lp_entered": (("longest_path_entered",), None, "{0.activity} {0.activity_name} joined the longest path."),
    "lp_left_completed": (("longest_path_left",), lambda f: f[0]["reason_code"] == "completed",
                          "{0.activity} {0.activity_name} left the longest path (completed)."),
    "lp_left_unfinished": (("longest_path_left",), lambda f: f[0]["reason_code"] == "still_unfinished",
                           "{0.activity} {0.activity_name} left the longest path while still unfinished."),
    "lp_left_deleted": (("longest_path_left",), lambda f: f[0]["reason_code"] == "deleted",
                        "{0.activity} {0.activity_name} left the longest path (deleted)."),
    "list_summary": (("list_summary",), None,
                     "{0.count} {0.items} in this update; the first {0.shown} are listed below and all are in the evidence workbook."),
    "movement_summary": (("movement_summary",), None,
                         "{0.count} {0.count:noun} forecast to finish {0.direction} than in the previous update "
                         "({0.on_longest_path} on the longest path; largest movement {0.max_days} calendar days). "
                         "The full list is in the evidence workbook."),
    "finish_later": (("forecast_finish_movement",), lambda f: f[0]["direction"] == "later",
                     "{0.activity} {0.activity_name} is now forecast to finish on {0.current:date}, {0.days} calendar days "
                     "later than {0.previous:date}.{0.longest_path:lp_note}" + REASON),
    "finish_earlier": (("forecast_finish_movement",), lambda f: f[0]["direction"] == "earlier",
                       "{0.activity} {0.activity_name} is now forecast to finish on {0.current:date}, {0.days} calendar "
                       "days earlier than {0.previous:date}.{0.longest_path:lp_note}"),
    "milestone": (("milestone_movement",), None, "Milestone {0.activity} {0.activity_name} ({0.metric}) moved "
                  "{0.direction} by {0.days} calendar days, from {0.previous:date} to {0.current:date}."),
    "od_change": (("original_duration_change",), None, "{0.activity} {0.activity_name} original duration changed from "
                  "{0.previous_days} to {0.current_days} days ({0.basis})."),
    "rem_change": (("remaining_estimate_change",), None, "{0.activity} {0.activity_name} remaining duration estimate "
                   "changed from {0.previous_days} to {0.current_days} days."),
    "logic": (("logic_change",), None, "Relationship {0.predecessor} to {0.successor} ({0.rel}, lag {0.lag_hours} h) "
              "was {0.change}."),
    "constraint": (("constraint_change",), None, "{0.activity} {0.activity_name} constraint changed from "
                   "'{0.previous:none}' to '{0.current:none}'."),
    "added": (("activity_added",), None, "Activity {0.activity} {0.activity_name} was added."),
    "renamed": (("activity_name_change",), lambda f: f[0]["confirmed"],
                "{0.activity} was renamed from '{0.previous}' to '{0.current}'."),
    "same_id_new_name": (("activity_name_change",), lambda f: not f[0]["confirmed"] and f[0]["withheld"] > 0,
                         "{0.activity} keeps its Activity ID but its name changed from '{0.previous}' to '{0.current}'. "
                         "It may be a different activity, so changes compared on this ID ({0.withheld}) are left out "
                         "of this narrative until the planner confirms; they are in the evidence workbook."),
    "same_id_new_name_only": (("activity_name_change",), lambda f: not f[0]["confirmed"] and f[0]["withheld"] == 0,
                              "{0.activity} keeps its Activity ID but its name changed from '{0.previous}' to "
                              "'{0.current}'. No other change is compared on this ID; planner to confirm it is the same activity."),
    "deleted": (("activity_deleted",), None, "Activity {0.activity} {0.activity_name} was deleted."),
    "actual_corrected": (("actual_date_correction",), None, "{0.activity} {0.activity_name} {0.metric} was corrected "
                         "from {0.previous:date} to {0.current:date}."),
    "lookahead": (("lookahead",), None, "{0.activity} {0.activity_name} {0.kinds:and} (start {0.start:date}, finish "
                  "{0.finish:date}){0.longest_path:lp_dash}."),
    "warning": (("warning",), None, "Data warning: {0.detail}"),
}
PH = re.compile(r"\{(\d+)\.([a-z_.]+)(?::([a-z_]+))?\}")


def _val(v):
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


def render(claim, idx):
    """Text of a claim, built only from its template and cited facts. Raises ValueError if it can't be."""
    t = claim.get("template")
    if t == "gap":
        label = claim.get("label", "")
        if not re.fullmatch(r"[A-Z][A-Z ,/&]{2,60}", label):
            raise ValueError("gap label must be capitals only")
        return f"[{label}: planner to provide]"
    if t == "phrase":
        key = claim.get("phrase")
        if key not in PHRASES:
            raise ValueError(f"unknown phrase '{key}' (allowed: {sorted(PHRASES)})")
        return PHRASES[key]
    if t == "planner":
        p = claim.get("planner") or {}
        if not (p.get("text") and p.get("by") and p.get("date")):
            raise ValueError("planner input needs text, by and date")
        return f"Planner input ({p['by']}, {p['date']}): {p['text']}"
    if t not in TEMPLATES:
        raise ValueError(f"unknown template '{t}'")
    types, cond, fmt = TEMPLATES[t]
    ids = claim.get("fact_ids", [])
    if len(ids) != len(types):
        raise ValueError(f"template '{t}' needs {len(types)} fact(s), got {len(ids)}")
    facts = []
    for fid, allowed in zip(ids, types):
        f = idx.get(fid)
        if f is None:
            raise ValueError(f"unknown fact_id {fid}")
        if f["type"] not in allowed.split("|"):
            raise ValueError(f"template '{t}' can't use a {f['type']} fact ({fid})")
        facts.append(f)
    if cond and not cond(facts):
        raise ValueError(f"template '{t}' doesn't fit fact {ids} (e.g. wrong direction)")

    def sub(m):
        i, path, form = int(m.group(1)), m.group(2), m.group(3)
        try:
            v = _g(facts[i], path)
        except KeyError:
            raise ValueError(f"fact {ids[i]} has no field '{path}'")
        return FORMATS[form](v) if form else _val(v)

    text = PH.sub(sub, fmt)
    p = claim.get("planner")
    if p:                                   # planner reason replaces the gap, attributed
        if not (p.get("text") and p.get("by") and p.get("date")):
            raise ValueError("planner input needs text, by and date")
        text = text.replace(REASON, f" Planner input ({p['by']}, {p['date']}): {p['text']}")
    return text


# Neutral sentences that state nothing about the schedule. Anything about status, completion or causes must be a
# fact template or attributed planner input - a word blocklist can't be complete (Codex review #2, #1).
PHRASES = {
    "intro": "This narrative accompanies the schedule update and was drafted from the two schedule exports named above.",
    "evidence": "Each statement is traced to the schedule data in the evidence workbook.",
    "planner_items": "Items in brackets are for the planner to complete before submission.",
}

# Fixed headings. A claim names a section id; the heading text can't be edited (Codex review #2, #2).
SECTIONS = {
    "summary": "1 Summary", "progress": "2 Progress", "critical": "3 Critical path", "delays": "4 Delays",
    "gains": "4b Forecast gains", "milestones": "5 Milestones", "changes": "6 Schedule changes",
    "lookahead": "7 Look-ahead", "recovery": "8 Recovery and decisions", "warnings": "9 Data warnings",
}


def draft_claims(F, show_min_days=5, top_n=10):
    by = defaultdict(list)
    for f in F:
        if not f.get("identity_unconfirmed"):         # may compare two different activities - evidence only
            by[f["type"]].append(f)
    C = []

    def claim(section, template, facts=(), **kw):
        C.append({"claim_id": f"C{len(C) + 1:03d}", "section": section, "template": template,
                  "fact_ids": [f["fact_id"] for f in facts], **kw})

    def pick(section, facts, *templates):
        for f in facts:
            for t in templates:
                types, cond, _ = TEMPLATES[t]
                if f["type"] in types[0].split("|") and (cond is None or cond([f])):
                    claim(section, t, [f])
                    break

    claim("summary", "phrase", phrase="intro")
    pick("summary", by["data_dates"], "data_dates")
    pick("summary", by["project_finish_movement"], "finish_moved", "finish_unchanged")
    pick("progress", by["completed_in_period"], "completed")
    pick("progress", by["started_in_period"], "started")
    pick("progress", by["completed_late"], "completed_late")
    pick("progress", by["planned_not_started"], "not_started")
    pick("critical", by["longest_path_membership"] + by["float_critical_membership"], "membership", "membership_now")
    pick("critical", [f for f in by["longest_path_sequence"] if f["count"] > 1], "lp_chain", "lp_network")
    pick("critical", by["longest_path_entered"], "lp_entered")
    pick("critical", by["longest_path_left"], "lp_left_completed", "lp_left_unfinished", "lp_left_deleted")
    for direction, sec in (("later", "delays"), ("earlier", "gains")):
        pick(sec, [f for f in by["movement_summary"] if f["direction"] == direction], "movement_summary")
        grp = [f for f in by["forecast_finish_movement"] if f["direction"] == direction
               and abs(f["delta"]["calendar_days"]) >= show_min_days]
        grp.sort(key=lambda x: (not x.get("longest_path"), -abs(x["delta"]["calendar_days"]), x["activity"]))
        pick(sec, grp[:top_n], "finish_later", "finish_earlier")
    pick("milestones", by["milestone_movement"], "milestone")
    claim("milestones", "gap", label="CONTRACT COMPLETION STATUS")
    summ = {f["list_type"]: f for f in by["list_summary"]}
    by["remaining_estimate_change"] = [f for f in by["remaining_estimate_change"] if not f.get("on_completion")]
    severity = {"actual_after_data_date": 0, "activity_id_name_changed": 1, "inconsistent_source": 2, "backdated_actual": 3}
    by["warning"].sort(key=lambda f: severity.get(f.get("code"), 4))       # worst first, before the cap

    def capped(section, ftype, *templates):
        if ftype in summ:
            claim(section, "list_summary", [summ[ftype]])
        pick(section, by[ftype][:LIST_CAP], *templates)

    capped("changes", "activity_name_change", "same_id_new_name", "same_id_new_name_only", "renamed")
    capped("changes", "original_duration_change", "od_change")
    capped("changes", "remaining_estimate_change", "rem_change")
    capped("changes", "logic_change", "logic")
    capped("changes", "constraint_change", "constraint")
    capped("changes", "activity_added", "added")
    capped("changes", "activity_deleted", "deleted")
    capped("changes", "actual_date_correction", "actual_corrected")
    capped("lookahead", "lookahead", "lookahead")
    claim("recovery", "gap", label="RECOVERY ACTIONS AND DECISIONS NEEDED")
    capped("warnings", "warning", "warning")
    claim("warnings", "phrase", phrase="evidence")
    return C


def validate(claims, facts):
    """Each claim must render from its template and facts, and its stored text must equal that rendering."""
    idx = {f["fact_id"]: f for f in facts}
    results, seen = [], set()
    for c in claims:
        errs = []
        if c.get("claim_id") in seen:
            errs.append("duplicate claim_id")
        seen.add(c.get("claim_id"))
        if c.get("section") not in SECTIONS:
            errs.append(f"section '{c.get('section')}' is not one of {sorted(SECTIONS)}")
        try:
            text = render(c, idx)
            if "text" in c and c["text"] != text:
                errs.append("stored text differs from what its template and facts produce (edited by hand?)")
        except ValueError as e:
            text, errs = c.get("text", ""), errs + [str(e)]
        results.append({"claim_id": c.get("claim_id"), "status": "FAIL" if errs else "PASS", "errors": errs, "text": text})
    return results


def render_all(claims, facts):
    idx = {f["fact_id"]: f for f in facts}
    for c in claims:
        try:
            c["text"] = render(c, idx)
        except ValueError as e:
            c["text"] = f"<<INVALID: {e}>>"
    return claims

# ------------------------------------------------------------------ outputs
def md_text(claims, facts):
    """Title and source come from the facts, headings from SECTIONS - nothing editable in claims.json."""
    dd = next(f for f in facts if f["type"] == "data_dates")
    lines = [f"# Schedule Update Narrative - {dd['current_project']}",
             f"_Draft generated from {dd['previous_file']} -> {dd['current_file']}._", ""]
    sec = None
    for c in claims:
        if c["section"] != sec:
            sec = c["section"]
            lines += ["", f"## {SECTIONS.get(sec, '?')}", ""]
        lines.append(f"- {c['text']}")
    return "\n".join(lines) + "\n"


def write_evidence(claims, facts, results, path):
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill
    except ImportError:
        return False
    idx = {f["fact_id"]: f for f in facts}
    res = {r["claim_id"]: r for r in results}
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Claims"
    ws.append(["Claim", "Section", "Validation", "Text", "Template", "Fact IDs", "Activities", "Previous", "Current",
               "Delta", "Basis", "Errors"])
    for c in claims:
        fs = [idx[x] for x in c.get("fact_ids", []) if x in idx]
        r = res.get(c["claim_id"], {})
        ws.append([c["claim_id"], c["section"], r.get("status"), c.get("text"), c.get("template"), ", ".join(c.get("fact_ids", [])),
                   ", ".join(f.get("activity", "") for f in fs if f.get("activity")),
                   ", ".join(str(f.get("previous", "")) for f in fs), ", ".join(str(f.get("current", "")) for f in fs),
                   ", ".join(json.dumps(f.get("delta", "")) for f in fs if f.get("delta")),
                   ", ".join(f.get("delta", {}).get("basis", "") for f in fs if isinstance(f.get("delta"), dict)),
                   "; ".join(r.get("errors", []))])
        if r.get("status") == "FAIL":
            ws.cell(ws.max_row, 3).fill = PatternFill("solid", fgColor="F4C7C3")
    ws2 = wb.create_sheet("Facts")
    keys = ["fact_id", "type", "activity", "activity_name", "metric", "previous", "current", "delta", "direction", "flags", "note",
            "detail", "identity_unconfirmed"]
    ws2.append(keys)
    for f in facts:
        ws2.append([json.dumps(f.get(k)) if isinstance(f.get(k), (dict, list)) else f.get(k) for k in keys])
    for w in (ws, ws2):
        for cell in w[1]:
            cell.font = Font(bold=True)
        w.freeze_panes = "A2"
    wb.save(path)
    return True


def load_renames(path):
    """[(old, new), ...] - kept as a list so duplicates can be detected. A row X,X confirms X is the same activity."""
    if not path:
        return []
    with open(path, encoding="utf-8-sig") as f:
        return [(r["old"].strip(), r["new"].strip()) for r in csv.DictReader(f) if r["old"].strip() and r["new"].strip()]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("compare")
    c.add_argument("previous"); c.add_argument("current")
    c.add_argument("--out", required=True)
    c.add_argument("--project-prev"); c.add_argument("--project-curr")
    c.add_argument("--map", help="CSV with columns old,new for renamed Activity IDs")
    c.add_argument("--show-min-days", type=int, default=5)
    c.add_argument("--near-days", type=int, default=10)
    c.add_argument("--lookahead-days", type=int, default=30)
    v = sub.add_parser("validate", help="re-check claims.json and the narrative .md after edits")
    v.add_argument("out")
    v.add_argument("--write", action="store_true", help="re-render the claim texts and the .md from claims.json first")
    a = ap.parse_args()
    out = Path(a.out)

    if a.cmd == "compare":
        warn = []
        try:
            prev = Schedule(a.previous, a.project_prev, warn)
            curr = Schedule(a.current, a.project_curr, warn)
        except IdentityError as e:
            sys.exit(f"STOPPED: {e}")
        try:
            match = match_table(prev, curr, load_renames(a.map))
        except RenameError as e:
            sys.exit(f"STOPPED: {e}")
        facts = compute_facts(prev, curr, match, near_days=a.near_days, look_days=a.lookahead_days)
        for w in warn:
            facts.append({"fact_id": f"F-WARNING-{len(facts):03d}", **w})
        claims = render_all(draft_claims(facts, a.show_min_days), facts)
        results = validate(claims, facts)
        facts_text = json.dumps(facts, indent=1)
        doc = {"facts_sha256": hashlib.sha256(facts_text.encode("utf-8")).hexdigest(), "claims": claims}
        out.parent.mkdir(parents=True, exist_ok=True)
        Path(f"{out}-facts.json").write_text(facts_text, encoding="utf-8")
        Path(f"{out}-claims.json").write_text(json.dumps(doc, indent=1), encoding="utf-8")
        Path(f"{out}-narrative.md").write_text(md_text(claims, facts), encoding="utf-8")
        write_evidence(claims, facts, results, f"{out}-evidence.xlsx")
        counts = defaultdict(int)
        for f in facts:
            counts[f["type"]] += 1
        fails = [r for r in results if r["status"] == "FAIL"]
        print(f"{prev.name} ({d(prev.data_date)}) -> {curr.name} ({d(curr.data_date)}): {len(facts)} facts, {len(claims)} claims")
        print("  " + ", ".join(f"{k} {v2}" for k, v2 in sorted(counts.items())))
        print(f"Validation: {len(results) - len(fails)} PASS, {len(fails)} FAIL")
        for r in fails:
            print(f"  FAIL {r['claim_id']}: {r['errors']}")
        print(f"Wrote {out}-narrative.md, -facts.json, -claims.json, -evidence.xlsx")
        sys.exit(1 if fails else 0)

    facts_text = Path(f"{out}-facts.json").read_text(encoding="utf-8")
    facts = json.loads(facts_text)
    doc = json.loads(Path(f"{out}-claims.json").read_text(encoding="utf-8"))
    if doc.get("facts_sha256") != hashlib.sha256(facts_text.encode("utf-8")).hexdigest():
        sys.exit("STOPPED: claims.json was made from a different facts.json (stale run or edited facts) - run compare again")
    md_path = Path(f"{out}-narrative.md")
    if a.write:                             # rebuild text + .md + evidence from the (edited) claims
        render_all(doc["claims"], facts)
        Path(f"{out}-claims.json").write_text(json.dumps(doc, indent=1), encoding="utf-8")
        md_path.write_text(md_text(doc["claims"], facts), encoding="utf-8")
        write_evidence(doc["claims"], facts, validate(doc["claims"], facts), f"{out}-evidence.xlsx")
    results = validate(doc["claims"], facts)
    expected = md_text(render_all([dict(c) for c in doc["claims"]], facts), facts)
    if not md_path.exists() or md_path.read_text(encoding="utf-8") != expected:
        results.append({"claim_id": "NARRATIVE", "status": "FAIL", "text": str(md_path),
                        "errors": ["narrative .md differs from claims.json - edit claims.json, then run validate --write"]})
    fails = [r for r in results if r["status"] == "FAIL"]
    lines = []
    for r in results:
        lines.append(f"{r['status']} {r['claim_id']}: {r['text']}")
        if r["errors"]:
            lines.append(f"    {r['errors']}")
    Path(f"{out}-validation.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Validation: {len(results) - len(fails)} PASS, {len(fails)} FAIL -> {out}-validation.txt")
    for r in fails:
        print(f"  FAIL {r['claim_id']}: {r['errors']}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()

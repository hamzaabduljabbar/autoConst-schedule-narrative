"""Corpus check - run doctor + compare on every real schedule pair we have, and show what a code change moved.

  py scripts/corpus_check.py corpus.csv --save before.json      # BEFORE changing code
  py scripts/corpus_check.py corpus.csv --diff before.json      # AFTER: every number that changed, per pair

corpus.csv columns: name,previous,current  (paths to real XERs; kept OUTSIDE the repo - it is client data).
Used by the self-repair steps in SKILL.md: a fix must change only what it was meant to change. Any other
difference is a side effect to explain or fix. Also reports crashes and validation failures.
"""
import argparse, csv, json, re, subprocess, sys, tempfile
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from doctor import diagnose  # noqa: E402


def snapshot(corpus):
    snap, tmp = {}, Path(tempfile.mkdtemp())
    for row in csv.DictReader(open(corpus, encoding="utf-8-sig")):
        name, prev, curr = row["name"], row["previous"], row["current"]
        s = {}
        try:
            verdict, F, cmd = diagnose(prev, curr)
            s["doctor"] = verdict
            s["doctor_findings"] = sorted(t.split(":")[0][:60] for lvl, t in F if lvl in ("WARN", "FIX"))
        except Exception as e:                                  # noqa: BLE001 - a crash is a finding
            s["doctor"] = f"CRASH {e!r}"[:200]
            snap[name] = s
            continue
        if verdict != "FIX FIRST":
            args = [sys.executable, str(HERE / "narrative.py"), "compare", prev, curr, "--out", str(tmp / name)]
            for flag in ("--project-prev", "--project-curr"):
                m = re.search(flag + r' "([^"]+)"', cmd or "")
                if m:
                    args += [flag, m.group(1)]
            r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace")
            v = re.search(r"Validation: (\d+) PASS, (\d+) FAIL", r.stdout)
            s["compare"] = "ok" if r.returncode == 0 and v and v.group(2) == "0" else (r.stdout + r.stderr)[-300:]
            fp = Path(f"{tmp / name}-facts.json")
            if fp.exists():
                facts = json.loads(fp.read_text(encoding="utf-8"))
                c = Counter(f["type"] if f["type"] != "warning" else "warning:" + f.get("code", "") for f in facts)
                s["facts"] = dict(sorted(c.items()))
        snap[name] = s
        print(f"  {name:30} doctor={s['doctor']:20} compare={s.get('compare', '-') if len(str(s.get('compare', '-'))) < 10 else 'PROBLEM'}")
    return snap


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("corpus")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--save"); g.add_argument("--diff")
    a = ap.parse_args()
    snap = snapshot(a.corpus)
    bad = [n for n, s in snap.items() if "CRASH" in s["doctor"] or s.get("compare", "ok") != "ok"]
    if a.save:
        Path(a.save).write_text(json.dumps(snap, indent=1), encoding="utf-8")
        print(f"Saved {len(snap)} pairs to {a.save}. Crashes/failures: {bad or 'none'}")
        sys.exit(1 if bad else 0)
    before = json.loads(Path(a.diff).read_text(encoding="utf-8"))
    changed = 0
    for name in sorted(set(before) | set(snap)):
        b, n = before.get(name, {}), snap.get(name, {})
        lines = []
        for key in ("doctor", "compare"):
            if b.get(key) != n.get(key):
                lines.append(f"{key}: {b.get(key)} -> {n.get(key)}")
        if b.get("doctor_findings") != n.get("doctor_findings"):
            lines.append(f"doctor findings: {b.get('doctor_findings')} -> {n.get('doctor_findings')}")
        bf, nf = b.get("facts", {}), n.get("facts", {})
        for t in sorted(set(bf) | set(nf)):
            if bf.get(t, 0) != nf.get(t, 0):
                lines.append(f"{t}: {bf.get(t, 0)} -> {nf.get(t, 0)}")
        if lines:
            changed += 1
            print(f"\n{name}")
            for ln in lines:
                print(f"  {ln}")
    print(f"\n{changed} of {len(snap)} pairs changed. Crashes/failures: {bad or 'none'}")
    print("Every change above must be one the fix was MEANT to make. Explain or fix anything else.")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()

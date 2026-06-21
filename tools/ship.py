#!/usr/bin/env python3
"""ship.py -- build a tracked submission zip from solution/dev/ + log it.

  python3 tools/ship.py --tag entryaware --date 2026-06-21

Writes submissions/<date>_<tag>.zip (production modules + placer.c), prints the SUBMISSIONS.md row to
append, and reminds you to freeze a results snapshot. One artifact per ship, dated + tagged — no loose zips.

Date must be passed explicitly (the runtime has no wall clock); use today's date.
"""
import os, sys, zipfile, argparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEV = os.path.join(ROOT, "solution", "dev")
SUB = os.path.join(ROOT, "submissions")

# production set (myalgorithm transitive) + the C placer + utils (competition-provided, bundled for safety)
FILES = ["myalgorithm.py", "anytime.py", "asls.py", "assign_lns.py", "beam_polish.py", "brkga2.py",
         "cpsat_packer.py", "cpsat_polish.py", "cpsat_z3.py", "improve.py", "master.py", "packer.py",
         "polypack.py", "solve.py", "placer.c"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--date", required=True, help="YYYY-MM-DD (pass today's date)")
    a = ap.parse_args()
    os.makedirs(SUB, exist_ok=True)
    # bundle utils from baseline so the zip is self-contained even if the grader doesn't inject it
    utils_src = os.path.join(ROOT, "solution", "baseline", "baseline", "utils.py")
    missing = [f for f in FILES if not os.path.exists(os.path.join(DEV, f))]
    if missing:
        sys.exit(f"ERROR missing production files in dev/: {missing}")
    name = f"{a.date}_{a.tag}.zip"
    path = os.path.join(SUB, name)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in FILES:
            z.write(os.path.join(DEV, f), f)
        if os.path.exists(utils_src):
            z.write(utils_src, "utils.py")
    print(f"built {path} ({len(FILES)+1} files, {os.path.getsize(path)} bytes)")
    print("\nAppend to SUBMISSIONS.md:")
    print(f"| {a.date} | {a.tag} | `submissions/{name}` | `results/{a.date}_{a.tag}.json` | <fill> |")
    print(f"\nThen freeze results: python3 tools/compare.py --tag {a.date}_{a.tag} --inst all --tl <sec>")


if __name__ == "__main__":
    main()

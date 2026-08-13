#!/usr/bin/env python3
"""Promote rsv4 into solver/ as v6.4. Run ONLY after every gate has passed.

The candidate lives in builds/rsv4 behind two env knobs. Promotion makes it the
default: the reservation uses the residency-aware profile and the achievable
density as its threshold, and it stands down above peak load 1.60. Nothing is
left behind a switch, because a defect repair has no reason to be optional.

What changes, in one sentence each:
  knobs.hpp   rsvMaxLoad() becomes a constant 160 instead of an env read, and
              rsv() disappears -- the repair is unconditional.
  core.hpp    two fields the profile needs.
  api.cpp     compute the residency-aware profile and the instance's peak load.
  repair.hpp  the reservation reads that profile against packFill, and only
              below the load limit.

    python3 tools/promote_rsv4.py --check    # show the diff, change nothing
    python3 tools/promote_rsv4.py --apply
"""
import pathlib
import shutil
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
SRC = REPO / "builds" / "rsv4" / "src"
DST = REPO / "solver" / "src"
FILES = ["knobs.hpp", "core.hpp", "api.cpp", "repair.hpp"]

KNOB_ENV = """
// rsv4: enable the reservation only below this peak load, in percent
// (150 = load 1.50). 0 = no limit (rsv behaviour).
inline int rsvMaxLoad() {
    static const int v = [] {
        const char* q = std::getenv("OGC_RSVMAXLOAD");
        return q ? atoi(q) : 0;
    }();
    return v;
}
"""
KNOB_FIXED = """
// Peak-load ceiling for the energetic reservation, in percent. Above 1.60 the
// guard stands down: measured 08-13 over 35 train_final instances, free
// deferral is worth -5.09% below it (65W/19L, p=0.0000, 13 of 14 instances,
// replicated 65W/19L on disjoint seeds) and turns harmful above 2.2, where
// every insertGreedy phase starts at eBot and the block loses its early days
// without finding relief. On the instances it stands down for, the run is
// measured neutral (69 pairs, median +0.00%, p=0.80).
inline constexpr int    rsvMaxLoad() { return 160; }
"""
RSV_ENV = """
// rsv candidate: repair the DEAD energetic reservation. `reserve()` has been
// on since v5 but has never once fired: the threshold is fillRatio, which is
// max(fill, max_t dayLoad) by construction, so `dayLoad[t] > thr` cannot be
// true; and dayLoad counted one area-unit per day of [R, D-P], peaking at
// 0.19-0.54 of capacity against a real occupancy of 58%. Off (0) = shipped.
inline int rsv() {
    static const int v = [] {
        const char* q = std::getenv("OGC_RSV");
        return q ? atoi(q) : 0;
    }();
    return v;
}
"""


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("--check", "--apply"):
        sys.exit(__doc__)
    apply = sys.argv[1] == "--apply"
    staged = {}
    for f in FILES:
        t = (SRC / f).read_text()
        if f == "knobs.hpp":
            if KNOB_ENV not in t or RSV_ENV not in t:
                sys.exit("knobs.hpp does not match the expected candidate text")
            t = t.replace(KNOB_ENV, KNOB_FIXED)
            t = t.replace(RSV_ENV, "")
        if f == "repair.hpp":
            # the repair becomes unconditional; only the load ceiling gates it
            t = t.replace("const bool rs = knobs::rsv() != 0 && "
                          "!c.pb->dayLoadE.empty() && loadOK;",
                          "const bool rs = !c.pb->dayLoadE.empty() && loadOK;")
        staged[f] = t
    if not apply:
        for f in FILES:
            a = (DST / f).read_text().splitlines(keepends=True)
            b = staged[f].splitlines(keepends=True)
            import difflib
            d = list(difflib.unified_diff(a, b, f"solver/src/{f}",
                                          f"promoted/{f}"))
            print("".join(d) if d else f"solver/src/{f}: unchanged")
        return
    for f in FILES:
        shutil.copy2(DST / f, DST / (f + ".bak"))
        (DST / f).write_text(staged[f])
    print("promoted; build with: sbatch -p titan tools/build_variant.sbatch solver")
    print("backups written as *.bak -- delete them once the build is verified")


if __name__ == "__main__":
    main()

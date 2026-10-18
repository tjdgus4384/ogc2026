#!/usr/bin/env python3
"""Exact final-extract shipcheck matrix (Stage 2, applied to the selected
variant per Revision 3). Machine-readable, duplicate/missing rejected.

    python3 tools/release_matrix.py            # print matrix JSON
    python3 tools/release_matrix.py --check F  # verify F covers the matrix
"""
import argparse
import json
import sys

# (gateId, dataset, instance, seed, tl, mode, expectedObjective)
_CASES = [
    ("E-CANARY", "preliminary_test", 1, 7, 20, "search", 1499),
    ("E-CANARY", "preliminary_test", 2, 7, 20, "search", 3690),

    ("E-HARD10", "train_final", 4, 7, 600, "search", None),
    ("E-HARD10", "train_final", 15, 7, 600, "search", None),
    ("E-HARD10", "train_final", 22, 7, 600, "search", None),
    ("E-HARD10", "train_final", 23, 7, 600, "search", None),
    ("E-HARD10", "train_final", 27, 7, 600, "search", None),
    ("E-HARD10", "train_final", 29, 7, 600, "search", None),
    ("E-HARD10", "train_final", 30, 7, 600, "search", None),
    ("E-HARD10", "train_final", 34, 7, 600, "search", None),
    ("E-HARD10", "train_final", 35, 7, 600, "search", None),
    ("E-HARD10", "train_final", 38, 7, 600, "search", None),

    ("E-P36", "preliminary_test", 3, 7, 240, "search", None),
    ("E-P36", "preliminary_test", 4, 7, 480, "search", None),
    ("E-P36", "preliminary_test", 5, 7, 600, "search", None),
    ("E-P36", "preliminary_test", 6, 7, 900, "search", None),

    ("E-NOORT", "preliminary_test", 1, 7, 20, "noort", 1499),
    ("E-NOORT", "preliminary_test", 2, 7, 20, "noort", 3690),
    ("E-NOORT", "preliminary_test", 3, 7, 60, "noort", 39050),

    ("E-TRIVIAL", "train", 11, 7, None, "trivial", None),
    ("E-TRIVIAL", "train_final", 35, 7, None, "trivial", None),
    ("E-TRIVIAL", "preliminary_test", 6, 7, None, "trivial", None),
]


def build():
    cases = []
    seen = set()
    for gate, dataset, inst, seed, tl, mode, expected in _CASES:
        case_id = f"{gate}.{dataset}.p{inst}.s{seed}.{mode}"
        if case_id in seen:
            raise RuntimeError(f"duplicate case: {case_id}")
        seen.add(case_id)
        cases.append({"caseId": case_id, "gateId": gate, "dataset": dataset,
                      "instance": inst, "seed": seed, "tl": tl, "mode": mode,
                      "expectedObjective": expected})
    return {"schemaVersion": 2, "kind": "matrix", "cases": cases}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", help="verify the given case-receipt list JSON "
                                    "covers the matrix exactly")
    args = ap.parse_args(argv)
    matrix = build()
    if args.check:
        got = json.load(open(args.check))
        got_ids = sorted(c["caseId"] for c in got)
        want_ids = sorted(c["caseId"] for c in matrix["cases"])
        if got_ids != want_ids:
            missing = set(want_ids) - set(got_ids)
            extra = set(got_ids) - set(want_ids)
            print(f"MATRIX FAIL: missing={sorted(missing)} extra={sorted(extra)}",
                  file=sys.stderr)
            return 1
        print(json.dumps({"coverage": "exact", "count": len(want_ids)}))
        return 0
    print(json.dumps(matrix, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Audit an energetic-reservation A/B gate against its load mechanism.

The ordinary ``abgate.py collect`` verdict treats every paired draw as a unit.
For RSV generalisation, the unit must instead be the *instance*: the treatment
has the same deterministic ``peak / pack_fill`` exposure for every seed of an
instance.  Counting two seeds as two independent load observations would make
the slope look twice as certain as it is.

This tool reproduces ``builds/rsv/src/api.cpp::computeFill`` from the problem
JSON, joins it to a completed gate, and reports:

* ordinary pair-level quality and safety statistics;
* one mean treatment delta per instance;
* the Spearman association between peak/fill and that instance delta;
* a robust Theil--Sen slope with an instance bootstrap interval;
* separate summaries inside and beyond 2.865, the source-faithful highest
  exposure on the preliminary P4--P6 boards.  The earlier ledger value 2.19
  used only layer 0's bbox, while C++ uses the union bbox over every layer.

Negative deltas mean RSV is better.  A positive load slope therefore means the
benefit decays as congestion rises, which is the pre-registered hidden-instance
risk.  The script is deterministic and uses only the standard library.

Usage::

    python3 tools/rsv_analyze.py runs/abgate/rsv-tf \
        runs/abgate/rsv-tf-srcmiss
"""

import argparse
import json
import math
import pathlib
import random
import statistics


# prob_6 under the calculation below.  Kept explicit so a result cannot move
# the support boundary; update only if the C++ feature definition changes.
PRELIMINARY_MAX_EXPOSURE = 2.864815051879244


def polygon_area(vertices):
    twice = 0.0
    for i, (x1, y1) in enumerate(vertices):
        x2, y2 = vertices[(i + 1) % len(vertices)]
        twice += x1 * y2 - x2 * y1
    return abs(twice) * 0.5


def orientation_geometry(orient):
    layers = orient["layers"]
    footprint = max(polygon_area(layer) for layer in layers)
    points = [point for layer in layers for point in layer]
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    bbox = (max(xs) - min(xs)) * (max(ys) - min(ys))
    return footprint, bbox


def cxx_ints(block):
    release = math.ceil(float(block["release_time"]) - 1e-9)
    due = round(float(block["due_date"]))
    duration = max(1, math.ceil(float(block["processing_time"]) - 1e-9))
    return release, due, duration


def exposure(problem):
    """Reproduce the RSV-only fields populated by C++ ``computeFill``."""
    capacity = sum(float(b["width"]) * float(b["height"])
                   for b in problem["bays"])
    numerator = denominator = 0.0
    load_areas = []
    times = []
    for block in problem["blocks"]:
        geometries = [orientation_geometry(o) for o in block["shape"]]
        # C++ selects the first strict minimum-bbox orientation for packFill.
        footprint, bbox = min(enumerate(geometries),
                              key=lambda item: (item[1][1], item[0]))[1]
        numerator += footprint
        denominator += bbox
        load_areas.append(min(g[0] for g in geometries))
        times.append(cxx_ints(block))

    pack_fill = min(1.0, numerator / denominator) if denominator else 1.0
    max_due = max(due for _, due, _ in times)
    demand = [0.0] * (max_due + 2)
    for area, (release, due, duration) in zip(load_areas, times):
        span = max(1, due - release)
        share = area * min(1.0, duration / span)
        for day in range(release, due + 1):  # inclusive, exactly as C++
            if 0 <= day <= max_due:
                demand[day] += share
    day_load = [value / max(1e-9, capacity) for value in demand]
    peak = max(day_load, default=0.0)

    moved = at_limit = moved_days = 0
    for release, due, duration in times:
        bottom = release
        limit = min(due - duration, len(day_load) - 1)
        while bottom < limit and day_load[bottom] > pack_fill:
            bottom += 1
        shift = bottom - release
        if shift:
            moved += 1
            moved_days += shift
            at_limit += bottom == limit
    return {
        "pack_fill": pack_fill,
        "peak": peak,
        "peak_over_fill": peak / pack_fill if pack_fill else math.inf,
        "moved_blocks": moved,
        "at_limit_blocks": at_limit,
        "mean_shift": moved_days / moved if moved else 0.0,
        "blocks": len(times),
    }


def sign_p(wins, losses):
    n = wins + losses
    if not n:
        return 1.0
    k = min(wins, losses)
    return min(1.0, 2.0 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n)


def ranks(values):
    order = sorted(range(len(values)), key=values.__getitem__)
    out = [0.0] * len(values)
    start = 0
    while start < len(order):
        end = start + 1
        while end < len(order) and values[order[end]] == values[order[start]]:
            end += 1
        rank = (start + end - 1) / 2.0
        for idx in order[start:end]:
            out[idx] = rank
        start = end
    return out


def pearson(xs, ys):
    xm, ym = statistics.mean(xs), statistics.mean(ys)
    xx = sum((x - xm) ** 2 for x in xs)
    yy = sum((y - ym) ** 2 for y in ys)
    if not xx or not yy:
        return 0.0
    return sum((x - xm) * (y - ym) for x, y in zip(xs, ys)) / math.sqrt(xx * yy)


def spearman(xs, ys):
    return pearson(ranks(xs), ranks(ys))


def theil_sen(xs, ys):
    slopes = [(ys[j] - ys[i]) / (xs[j] - xs[i])
              for i in range(len(xs)) for j in range(i + 1, len(xs))
              if xs[j] != xs[i]]
    return statistics.median(slopes) if slopes else 0.0


def permutation_p(xs, ys, observed, samples, rng):
    hits = 0
    shuffled = list(ys)
    for _ in range(samples):
        rng.shuffle(shuffled)
        hits += abs(spearman(xs, shuffled)) >= abs(observed) - 1e-15
    return (hits + 1) / (samples + 1)


def bootstrap_slope(xs, ys, samples, rng):
    slopes = []
    n = len(xs)
    for _ in range(samples):
        ids = [rng.randrange(n) for _ in range(n)]
        bx = [xs[i] for i in ids]
        by = [ys[i] for i in ids]
        slopes.append(theil_sen(bx, by))
    slopes.sort()
    lo = slopes[int(0.025 * (samples - 1))]
    hi = slopes[int(0.975 * (samples - 1))]
    return lo, hi


def summarize(rows):
    deltas = [row["delta"] for row in rows]
    wins = sum(delta < 0 for delta in deltas)
    losses = sum(delta > 0 for delta in deltas)
    ties = len(deltas) - wins - losses
    return {
        "n": len(rows), "wins": wins, "losses": losses, "ties": ties,
        "median": statistics.median(deltas) if deltas else math.nan,
        "mean": statistics.mean(deltas) if deltas else math.nan,
        "p": sign_p(wins, losses),
    }


def print_summary(label, summary):
    print(f"{label:18s} n={summary['n']:2d}  "
          f"{summary['wins']:2d}W/{summary['losses']:2d}L/{summary['ties']:2d}T  "
          f"median {summary['median']:+7.2f}%  mean {summary['mean']:+7.2f}%  "
          f"sign p={summary['p']:.4f}")


def load_gate(gate):
    manifest = json.loads((gate / "manifest.json").read_text())
    rows = []
    missing = []
    for pair in manifest["pairs"]:
        path = gate / f"pair-{pair['idx']:03d}.json"
        if not path.exists():
            missing.append(pair["idx"])
            continue
        receipt = json.loads(path.read_text())
        a, b = receipt["A"], receipt["B"]
        if not (a.get("feasible") and b.get("feasible")):
            raise SystemExit(f"unusable pair {pair['idx']}: A={a} B={b}")
        av, bv = float(a["objective"]), float(b["objective"])
        rows.append({
            "instance": pair["instance"], "seed": pair["seed"],
            "a": av, "b": bv, "delta": (bv - av) / av * 100.0,
            "dz1": float(b["obj1"]) - float(a["obj1"]),
            "wall_a": float(a["wall"]), "wall_b": float(b["wall"]),
            "tl": float(pair["tl"]),
            "prob_path": pathlib.Path(pair["probPath"]),
        })
    if missing:
        raise SystemExit(f"gate incomplete: {len(missing)} missing pairs, first {missing[:8]}")
    return manifest, rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("gate", nargs="+", type=pathlib.Path,
                        help="one gate, or disjoint companion gates to combine")
    parser.add_argument("--resamples", type=int, default=20000)
    args = parser.parse_args()
    loaded = [load_gate(gate) for gate in args.gate]
    manifests = [item[0] for item in loaded]
    pairs = [row for _, rows in loaded for row in rows]
    datasets = {manifest.get("dataset") for manifest in manifests}
    if len(datasets) != 1:
        raise SystemExit(f"cannot combine different datasets: {sorted(datasets)}")
    pair_keys = [(row["instance"], row["seed"]) for row in pairs]
    if len(pair_keys) != len(set(pair_keys)):
        raise SystemExit("combined gates contain duplicate instance/seed pairs")

    tags = "+".join(manifest.get("tag", gate.name)
                    for manifest, gate in zip(manifests, args.gate))
    print(f"== {tags}: RSV mechanism audit ==")
    print_summary("paired draws", summarize(pairs))
    sum_delta = (sum(row["b"] for row in pairs) /
                 sum(row["a"] for row in pairs) - 1.0) * 100.0
    print(f"objective-sum delta {sum_delta:+.2f}%  (descriptive; scales differ by instance)")
    best = min(pairs, key=lambda row: row["delta"])
    worst = max(pairs, key=lambda row: row["delta"])
    wall_delta = statistics.median(row["wall_b"] - row["wall_a"] for row in pairs)
    overruns = sum(row[arm] > row["tl"] + 10.0
                   for row in pairs for arm in ("wall_a", "wall_b"))
    print(f"pair tails          best P{best['instance']} s{best['seed']} "
          f"{best['delta']:+.1f}%; worst P{worst['instance']} s{worst['seed']} "
          f"{worst['delta']:+.1f}%; |delta|>50% "
          f"{sum(abs(row['delta']) > 50 for row in pairs)}")
    print(f"runtime             median(B-A) {wall_delta:+.2f}s; "
          f"wall > TL+10s {overruns}")

    by_instance = {}
    for row in pairs:
        by_instance.setdefault(row["instance"], []).append(row)
    instances = []
    for instance, group in sorted(by_instance.items()):
        problem = json.loads(group[0]["prob_path"].read_text())
        feat = exposure(problem)
        instances.append({
            "instance": instance,
            "delta": statistics.mean(row["delta"] for row in group),
            "dz1": statistics.mean(row["dz1"] for row in group),
            "seeds": len(group),
            **feat,
        })

    # Recompute activity on the whole dataset.  This catches a gate whose
    # instance subset was selected with a feature implementation that differs
    # from the candidate source (the original rsv-tf selection did exactly
    # that by using only layer 0's bbox).
    data_dir = pairs[0]["prob_path"].parent
    active = []
    for path in sorted(data_dir.glob("prob_*.json"),
                       key=lambda p: int(p.stem.split("_")[-1])):
        if exposure(json.loads(path.read_text()))["moved_blocks"]:
            active.append(int(path.stem.split("_")[-1]))
    covered = set(by_instance)
    missing_active = sorted(set(active) - covered)
    inactive_covered = sorted(covered - set(active))
    print(f"source-active coverage {len(set(active) & covered)}/{len(active)}"
          + (f"; MISSING {missing_active}" if missing_active else "")
          + (f"; inactive included {inactive_covered}" if inactive_covered else ""))

    print_summary("instances", summarize(instances))
    xs = [row["peak_over_fill"] for row in instances]
    ys = [row["delta"] for row in instances]
    rho = spearman(xs, ys)
    rng = random.Random(20260813)
    p_perm = permutation_p(xs, ys, rho, args.resamples, rng)
    slope = theil_sen(xs, ys)
    lo, hi = bootstrap_slope(xs, ys, args.resamples, rng)
    print(f"load association   Spearman rho={rho:+.3f}, permutation p={p_perm:.4f}")
    print(f"robust slope       {slope:+.2f} percentage-points / exposure "
          f"(instance bootstrap 95% [{lo:+.2f}, {hi:+.2f}])")

    inside = [row for row in instances
              if row["peak_over_fill"] <= PRELIMINARY_MAX_EXPOSURE]
    outside = [row for row in instances
               if row["peak_over_fill"] > PRELIMINARY_MAX_EXPOSURE]
    print_summary("inside prelim load", summarize(inside))
    if outside:
        print_summary("beyond prelim load", summarize(outside))

    print("\ninstance  exposure  delta(mean seeds)      dZ1  moved/blocks  at-limit  shift")
    for row in sorted(instances, key=lambda item: item["peak_over_fill"]):
        print(f"{row['instance']:8d}  {row['peak_over_fill']:8.3f}  "
              f"{row['delta']:+9.2f}% ({row['seeds']})  {row['dz1']:+7.1f}  "
              f"{row['moved_blocks']:3d}/{row['blocks']:<3d}       "
              f"{row['at_limit_blocks']:3d}      {row['mean_shift']:4.1f}")


if __name__ == "__main__":
    main()

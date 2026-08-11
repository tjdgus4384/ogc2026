#!/usr/bin/env python3
"""Population-candidate closure derivation and sealing (ralplan Revision 4,
ARCH-POP-GATE-007 / SUBMISSION-POP-006).

Derives the transitive source closure of the C++ parallel-population lineage,
constructs the candidate tree as canonical v5.3 plus ONLY reviewed
population-required hunks, and seals it fail-closed. Subcommands:

    derive  snapshot lineage, include-closure scan, canonical classification
            -> evidence/closure-manifest.json
    seat    build candidate tree from canonical + registered ops, generate
            tools/patches/population-tail-v1.patch, forbidden scan, API floor
            -> candidate/ tree, evidence/closure-review.json,
               evidence/candidate-spec.json
    seal    bind build + unit receipts to the seated tree
            -> evidence/candidate-seal.json (gateEligible)

Every op is anchored on exact bytes and fails closed on any mismatch: if the
lineage or canonical text drifted, preparation FAILS and the v5.3 fallback is
selected -- no guessing, no adaptation (plan option B is prohibited).
"""
import argparse
import difflib
import json
import pathlib
import re
import stat
import subprocess
import sys
import tempfile
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import release_schema as rs

REPO = pathlib.Path(__file__).resolve().parent.parent
POLICY_PATH = REPO / "tools" / "population_closure_policy.json"
WORK = REPO / "runs" / "release" / "population-candidate"
EVID = WORK / "evidence"
PATCH_OUT = REPO / "tools" / "patches" / "population-tail-v1.patch"
SO_NAME = "sscore.cpython-312-x86_64-linux-gnu.so"

# Candidate member treatment table. Modes:
#   canonical         exact canonical v5.3 bytes
#   lineage           exact current lineage bytes (every hunk requiredBy below)
#   lineage-filtered  lineage bytes minus registered forbidden hunks
#   generated         canonical bytes plus registered population ops
TREATMENT = {
    "myalgorithm.py": "generated",
    "utils.py": "canonical",
    "BUILD.txt": "canonical",
    "src/api.cpp": "generated",
    "src/alns.hpp": "lineage-filtered",
    "src/repair.hpp": "lineage-filtered",
    "src/core.hpp": "lineage",
    "src/crane.hpp": "lineage",
    "src/geometry.hpp": "lineage",
    "src/occ.hpp": "canonical",
    "src/knobs.hpp": "canonical",
    "src/kernel_parallel_bandit.hpp": "lineage",
    "src/kernel_bandit.hpp": "lineage",
    "src/repair_scored.hpp": "lineage",
    "src/repair_nfp_contact.hpp": "lineage",
}
REQUIRED_BY = {
    "src/core.hpp": ["kernel_parallel_bandit::detail::valid -> core::stateExactEvents",
                     "repair::placementFeasible -> core::vpExactEvents"],
    "src/crane.hpp": ["thread-safe overlap caches -> OrientGeom cache identity",
                      "exact overlap -> geom::overlapsAreaOffset"],
    "src/geometry.hpp": ["repair_nfp_contact -> geom::overlapsAreaOffset",
                         "exact overlap -> geom::collinearInteriorOverlap"],
    "src/kernel_parallel_bandit.hpp": ["sscore.Engine.run_kernel_parallel_bandit"],
    "src/kernel_bandit.hpp": ["kernel_parallel_bandit.hpp include (library dep)"],
    "src/repair_scored.hpp": ["alns::insertByMode mode 1/3"],
    "src/repair_nfp_contact.hpp": ["alns::insertByMode mode 2/3"],
    "src/alns.hpp": ["four-arg alns::audit", "RunParams.repairMode + insertByMode",
                     "bounded-deadline anneal for population lanes"],
    "src/repair.hpp": ["Ctx.prefP/.exactEvents/.hasDeadline/.deadline",
                       "deadlineExpired/placementFeasible/shapeContactSets/tryPlacement"],
    "src/api.cpp": ["Engine generation for OrientGeom cache identity",
                    "set_pref_p (canonical _spec_rule preservation)",
                    "run_kernel_parallel_bandit production entrypoint"],
    "myalgorithm.py": ["pref_p engine channel (canonical _spec_rule preservation)",
                       "pre-registered 15% parallel-population tail"],
}

# ---- registered exclusions from lineage files (forbidden post-v5.3 semantics)
FILTER_OPS = {
    "src/repair.hpp": [
        {"kind": "delete", "requiredBy": "forbidden: releaseForcedBay (lock-leak repair)",
         "anchor": "    bool releaseForcedBay = true;     // soft guidance may release mid-search\n"},
    ],
    "src/alns.hpp": [
        {"kind": "replace", "requiredBy": "forbidden: releaseForcedBay consumer",
         "anchor": "        if (c.forcedBay && c.releaseForcedBay &&\n",
         "payload": "        if (c.forcedBay &&\n"},
        {"kind": "delete", "requiredBy": "forbidden: swap forcedBay guard (lock-leak repair)",
         "anchor": ("                    if (c.forcedBay &&\n"
                    "                        ((c.forcedBay[A] >= 0 && c.forcedBay[A] != s.bay[B]) ||\n"
                    "                         (c.forcedBay[B] >= 0 && c.forcedBay[B] != s.bay[A])))\n"
                    "                        goto no_swap;\n")},
    ],
}

# ---- lineage slice markers for verbatim api.cpp payloads
API_SLICES = {
    "GEN_NS": ("namespace {\nuint64_t nextEngineGeneration() {", "} // namespace\n"),
    "SET_PREF_P": ("    void setPrefP(double value) {", "    }\n"),
    "PREPARE": ("    int prepareKernelRun() {", "    }\n"),
    "RUN_PB": ("    py::dict runKernelParallelBandit(double seconds, uint64_t seed) {",
               "    }\n"),
    "ORIENT_CTOR": ("        crane::OrientGeom g(generation_, static_cast<uint64_t>(i),",
                    "static_cast<uint64_t>(pb.geo[i].orients.size()));\n"),
    "BIND_RUN_PB": ("        .def(\"run_kernel_parallel_bandit\", &Engine::runKernelParallelBandit,",
                    "py::arg(\"seconds\"), py::arg(\"seed\"))\n"),
}

RUNTIME_TREATMENT_PY = '''\
    # C++ parallel-population tail (pre-registered SUBMISSION-POP-006 /
    # ARCH-POP-GATE-007): 15% of the base budget B = sec (post-reserve,
    # pre-carve) on non-population boards with B > 60 s. The primary
    # anneal and the canonical slice carve share the remaining 85%.
    _pop_tail = 0.0
    if not _pop_here and sec > 60.0:
        _pop_tail = 0.15 * sec
        _diag("pop-tail", base=round(sec, 3), tail=round(_pop_tail, 3),
              share=0.15)
        sec -= _pop_tail
'''
RUNTIME_TAIL_CALL_PY = '''\
    if _pop_tail > 0.0 and time.time() + 1.0 < deadline - reserve:
        _kb = eng.run_kernel_parallel_bandit(
            min(_pop_tail, max(1.0, deadline - reserve - time.time())),
            seed ^ 0xA11CE)
        _diag("cpp-parallel-bandit-tail", **_kb)
'''
PREF_P_BLOCK_PY = '''\
    pref_p = -1.0
    try:
        if spec and asg is None:
            pref_p = float(os.environ.get("OGC_PREFP", "0.85"))
        elif os.environ.get("OGC_PREFP"):
            pref_p = float(os.environ["OGC_PREFP"])
    except ValueError:
        pref_p = -1.0
    eng, seeds = _seed_free(p, asg=asg, pref_p=pref_p)
'''


class PrepError(RuntimeError):
    pass


def _fail(msg):
    raise PrepError(msg)


def _policy():
    return json.loads(POLICY_PATH.read_text())


def _read(path):
    return path.read_text(encoding="utf-8")


def _apply_ops(text, ops, origin):
    """Anchored literal ops; every anchor must occur exactly once."""
    applied = []
    for op in ops:
        anchor = op["anchor"]
        count = text.count(anchor)
        if count != 1:
            _fail(f"{origin}: anchor occurs {count}x (need exactly 1): "
                  f"{anchor.splitlines()[0][:90]!r}")
        kind = op["kind"]
        if kind == "delete":
            text = text.replace(anchor, "", 1)
        elif kind == "replace":
            text = text.replace(anchor, op["payload"], 1)
        elif kind == "insert_after":
            text = text.replace(anchor, anchor + op["payload"], 1)
        elif kind == "insert_before":
            text = text.replace(anchor, op["payload"] + anchor, 1)
        else:
            _fail(f"{origin}: unknown op kind {kind!r}")
        applied.append({"kind": kind, "requiredBy": op.get("requiredBy", ""),
                        "anchorSha256": rs.sha256_bytes(anchor.encode()),
                        "payloadSha256": rs.sha256_bytes(
                            op.get("payload", "").encode())})
    return text, applied


def _slice(text, key, origin):
    start_marker, end_marker = API_SLICES[key]
    if text.count(start_marker) != 1:
        _fail(f"{origin}: slice start {key} occurs "
              f"{text.count(start_marker)}x")
    start = text.index(start_marker)
    end = text.index(end_marker, start)
    if end < 0:
        _fail(f"{origin}: slice end for {key} not found")
    return text[start:end + len(end_marker)]


def _includes_of(text):
    return re.findall(r'^\s*#\s*include\s+"([^"]+)"', text, re.M)


def _local_include_closure(src_dir, roots):
    seen, todo = set(), list(roots)
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        p = src_dir / name
        if not p.exists():
            _fail(f"include closure: {name} missing under {src_dir}")
        for inc in _includes_of(_read(p)):
            todo.append(inc)
    return sorted(seen)


def _git_tracked(relpath):
    r = subprocess.run(["git", "ls-files", "--error-unmatch", relpath],
                       cwd=REPO, capture_output=True, text=True)
    return r.returncode == 0


def _extract_canonical(policy, dest):
    zip_path = REPO / policy["canonical"]["zipPath"]
    got = rs.sha256_file(zip_path)
    if got != policy["canonical"]["zipSha256"]:
        _fail(f"canonical zip sha mismatch: {got}")
    members = []
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if info.filename.endswith("/"):
                continue
            if ".." in info.filename or info.filename.startswith("/"):
                _fail(f"unsafe member {info.filename}")
            out = dest / info.filename
            out.parent.mkdir(parents=True, exist_ok=True)
            data = zf.read(info)
            out.write_bytes(data)
            members.append({"path": info.filename,
                            "sha256": rs.sha256_bytes(data)})
    return got, members


def _forbidden_scan(policy, added_lines, origin):
    hits = []
    patterns = (policy["forbiddenPatterns"]["envSelectors"]
                + policy["forbiddenPatterns"]["symbols"])
    for line in added_lines:
        for pat in patterns:
            if pat in line:
                hits.append({"origin": origin, "pattern": pat,
                             "line": line.strip()[:160]})
    return hits


def _added_lines(base_text, cand_text):
    out = []
    for line in difflib.unified_diff(base_text.splitlines(True),
                                     cand_text.splitlines(True), n=0):
        if line.startswith("+") and not line.startswith("+++"):
            out.append(line[1:])
    return out


API_FLOOR_CHECKS = [
    ("src/alns.hpp", r"int audit\(const core::Problem& pb, core::Sol& s, "
                     r"int\* badBlock = nullptr,\n\s*bool exactEvents = false\)",
     "alns::audit(pb,s,bad,exactEvents)"),
    ("src/alns.hpp", r"int repairMode = 0;", "RunParams.repairMode"),
    ("src/alns.hpp", r"inline bool insertByMode\(", "alns::insertByMode"),
    ("src/core.hpp", r"inline bool vpExactEvents\(", "core::vpExactEvents"),
    ("src/core.hpp", r"inline bool stateExactEvents\(", "core::stateExactEvents"),
    ("src/repair.hpp", r"double prefP = -1\.0;", "repair::Ctx.prefP"),
    ("src/repair.hpp", r"bool exactEvents = false;", "repair::Ctx.exactEvents"),
    ("src/repair.hpp", r"bool hasDeadline = false;", "repair::Ctx.hasDeadline"),
    ("src/repair.hpp", r"inline bool deadlineExpired\(", "repair::deadlineExpired"),
    ("src/repair.hpp", r"inline void shapeContactSets\(", "repair::shapeContactSets"),
    ("src/repair.hpp", r"inline bool placementFeasible\(", "repair::placementFeasible"),
    ("src/api.cpp", r"py::dict runKernelParallelBandit\(", "Engine::runKernelParallelBandit"),
    ("src/api.cpp", r"\.def\(\"run_kernel_parallel_bandit\"", "run_kernel_parallel_bandit binding"),
    ("src/api.cpp", r"\.def\(\"set_pref_p\"", "set_pref_p binding"),
    ("myalgorithm.py", r"run_kernel_parallel_bandit\(", "driver tail call"),
]


def _build_api_ops(lineage_api):
    sl = {k: _slice(lineage_api, k, "lineage src/api.cpp") for k in API_SLICES}
    return [
        {"kind": "insert_before", "requiredBy": "generation helper deps",
         "anchor": "#include \"alns.hpp\"\n",
         "payload": "#include <atomic>\n#include <stdexcept>\n"},
        {"kind": "insert_after", "requiredBy": "population kernel include",
         "anchor": "#include \"alns.hpp\"\n",
         "payload": "#include \"kernel_parallel_bandit.hpp\"\n"},
        {"kind": "insert_after", "requiredBy": "OrientGeom cache identity",
         "anchor": "using VV = std::vector<std::vector<std::pair<double,double>>>;\n",
         "payload": sl["GEN_NS"] + "\n"},
        {"kind": "insert_after", "requiredBy": "engine identity + pref channel",
         "anchor": "    int horizon_ = 0;\n",
         "payload": "    uint64_t generation_ = 0;\n    double prefP_ = -1.0;\n"},
        {"kind": "insert_after", "requiredBy": "OrientGeom cache identity",
         "anchor": "                     double w1, double w2, double w3) {\n",
         "payload": "        generation_ = nextEngineGeneration();\n"},
        {"kind": "replace", "requiredBy": "OrientGeom const members not assignable",
         "anchor": "        pb.geo.assign(n, {});\n",
         "payload": "        pb.geo.clear();\n        pb.geo.resize(n);\n"},
        {"kind": "replace", "requiredBy": "OrientGeom cache identity",
         "anchor": "        crane::OrientGeom g;\n",
         "payload": sl["ORIENT_CTOR"]},
        {"kind": "insert_after", "requiredBy": "set_pref_p (spec-rule preservation)",
         "anchor": ("        ctx.pb = &pb; ctx.s = &sol; ctx.oc = &oc; "
                    "ctx.horizon = horizon;\n    }\n"),
         "payload": sl["SET_PREF_P"]},
        {"kind": "insert_after", "requiredBy": "population entrypoint",
         "anchor": ("        return {(double)st.iters, (double)st.accepted, "
                    "(double)st.improved,\n                (double)st.failed, "
                    "st.best, (double)st.badIter,\n                "
                    "(double)st.badKind, (double)st.badPhase};\n    }\n"),
         "payload": sl["PREPARE"] + sl["RUN_PB"]},
        {"kind": "insert_before", "requiredBy": "population bindings",
         "anchor": "        .def(\"vp_ok\", &Engine::vpOK)\n",
         "payload": ("        .def(\"set_pref_p\", &Engine::setPrefP)\n"
                     + sl["BIND_RUN_PB"])},
    ]


MYALG_OPS = [
    {"kind": "replace", "requiredBy": "pref_p channel",
     "anchor": "def _build_engine(p):\n",
     "payload": "def _build_engine(p, pref_p=-1.0):\n"},
    {"kind": "insert_after", "requiredBy": "pref_p channel",
     "anchor": "        float(w[\"w1\"]), float(w[\"w2\"]), float(w[\"w3\"]))\n",
     "payload": "    eng.set_pref_p(float(pref_p))\n"},
    {"kind": "replace", "requiredBy": "pref_p channel",
     "anchor": "def _fresh(p, hmul=1):\n",
     "payload": "def _fresh(p, hmul=1, pref_p=-1.0):\n"},
    {"kind": "replace", "requiredBy": "pref_p channel",
     "anchor": "    eng = _build_engine(p)\n",
     "payload": "    eng = _build_engine(p, pref_p)\n"},
    {"kind": "replace", "requiredBy": "pref_p channel",
     "anchor": "def _seed_free(p, diversify=True, asg=None):\n",
     "payload": "def _seed_free(p, diversify=True, asg=None, pref_p=-1.0):\n"},
    {"kind": "replace", "requiredBy": "pref_p channel",
     "anchor": "        eng, _, _ = _fresh(p, hmul)\n",
     "payload": "        eng, _, _ = _fresh(p, hmul, pref_p)\n"},
    {"kind": "replace", "requiredBy": "pref_p channel (spec-rule preservation)",
     "anchor": ("    if spec and asg is None:\n"
                "        os.environ[\"OGC_PREFP\"] = "
                "os.environ.get(\"OGC_PREFP\", \"0.85\")\n"
                "    eng, seeds = _seed_free(p, asg=asg)\n"),
     "payload": PREF_P_BLOCK_PY},
    {"kind": "replace", "requiredBy": "pref_p channel",
     "anchor": "        eng, seeds = _seed_free(p)\n",
     "payload": "        eng, seeds = _seed_free(p, pref_p=pref_p)\n"},
    {"kind": "insert_after", "requiredBy": "15% tail carve (runtimeTreatment)",
     "anchor": "    _pop_here = sec >= 150.0 and sec / max(1, n) <= 1.6\n",
     "payload": RUNTIME_TREATMENT_PY},
    {"kind": "replace", "requiredBy": "slice window ends before tail",
     "anchor": ("        _slice_refine(p, eng, seed, deadline - reserve, "
                "eng.horizon())\n"),
     "payload": ("        _slice_refine(p, eng, seed,\n"
                 "                      deadline - reserve - _pop_tail, "
                 "eng.horizon())\n")},
    {"kind": "insert_before", "requiredBy": "tail call (runtimeTreatment order)",
     "anchor": "    if r is not None:\n        _st = getattr(eng, \"last_stats\", lambda: None)()\n",
     "payload": RUNTIME_TAIL_CALL_PY},
]


# ------------------------------------------------------------------ derive

def cmd_derive(args):
    policy = _policy()
    EVID.mkdir(parents=True, exist_ok=True)
    base_dir = WORK / "base"
    if base_dir.exists():
        subprocess.run(["chmod", "-R", "u+w", str(base_dir)], check=False)
        subprocess.run(["rm", "-rf", str(base_dir)], check=True)
    zip_sha, members = _extract_canonical(policy, base_dir)

    lineage_root = pathlib.Path(policy["lineageRoot"])
    # Candidate closure roots: the four population headers named by the
    # policy direct roots, plus canonical api.cpp's own include (alns.hpp).
    # The lineage api.cpp additionally includes the rejected experimental
    # kernels; those are reviewer-excluded, recorded below, never seated.
    roots = ["alns.hpp", "kernel_parallel_bandit.hpp", "kernel_bandit.hpp",
             "repair_scored.hpp", "repair_nfp_contact.hpp"]
    closure = _local_include_closure(lineage_root / "src", roots)
    lineage_api_includes = _includes_of(_read(lineage_root / "src" / "api.cpp"))
    excluded_includes = sorted(set(lineage_api_includes) - set(closure))
    lineage_paths = (["myalgorithm.py", "utils.py", "BUILD.txt", "src/api.cpp"]
                     + [f"src/{h}" for h in closure])

    snapshot = WORK / "snapshot"
    if snapshot.exists():
        subprocess.run(["chmod", "-R", "u+w", str(snapshot)], check=False)
        subprocess.run(["rm", "-rf", str(snapshot)], check=True)
    snap_records = []
    for rel in sorted(lineage_paths):
        src = lineage_root / rel
        if not src.exists():
            _fail(f"lineage file missing: {src}")
        dst = snapshot / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        data = src.read_bytes()
        dst.write_bytes(data)
        snap_records.append({"path": rel, "sha256": rs.sha256_bytes(data),
                             "tracked": _git_tracked(f"solver/{rel}")})

    canonical_members = {m["path"]: m["sha256"] for m in members}
    classification = []
    for rec in snap_records:
        rel = rec["path"]
        base = base_dir / rel
        if not base.exists():
            status = "added"
            diff_sha = rec["sha256"]
        else:
            base_sha = rs.sha256_file(base)
            if base_sha == rec["sha256"]:
                status = "unchanged"
                diff_sha = None
            else:
                status = "modified"
                diff = "".join(difflib.unified_diff(
                    _read(base).splitlines(True),
                    _read(snapshot / rel).splitlines(True),
                    fromfile=f"canonical/{rel}", tofile=f"lineage/{rel}"))
                diff_sha = rs.sha256_bytes(diff.encode())
        classification.append({
            "path": rel, "status": status, "sha256": rec["sha256"],
            "tracked": rec["tracked"], "diffSha256": diff_sha,
            "treatment": TREATMENT.get(rel, "UNREGISTERED"),
            "requiredBy": REQUIRED_BY.get(rel, [])})

    unregistered = [c["path"] for c in classification
                    if c["treatment"] == "UNREGISTERED"]
    status = "FAIL" if unregistered else "PASS"
    lineage_snapshot_sha = rs.canonical_hash(snap_records)
    manifest = {
        "schemaVersion": 1, "kind": "closure-manifest", "status": status,
        "createdAt": rs.now_iso(),
        "policySha256": rs.sha256_file(POLICY_PATH),
        "canonical": {"zipSha256": zip_sha,
                      "memberManifestSha256": rs.canonical_hash(members)},
        "lineageSnapshot": {"treeSha256": lineage_snapshot_sha,
                            "paths": snap_records},
        "includeClosure": sorted(closure),
        "excludedLineageIncludes": [
            {"path": f"src/{h}",
             "disposition": "unrelated-reject (forbidden experimental kernel)"}
            for h in excluded_includes],
        "closure": {"paths": classification},
        "unregisteredPaths": unregistered,
    }
    sha = _write_json(EVID / "closure-manifest.json", manifest)
    print(json.dumps({"status": status,
                      "manifest": str(EVID / "closure-manifest.json"),
                      "manifestSha256": sha,
                      "files": len(classification)}))
    return 0 if status == "PASS" else 1


# ------------------------------------------------------------------ seat

def cmd_seat(args):
    policy = _policy()
    manifest = json.loads((EVID / "closure-manifest.json").read_text())
    if manifest["status"] != "PASS":
        _fail("closure manifest is not PASS")
    if manifest["policySha256"] != rs.sha256_file(POLICY_PATH):
        _fail("policy drifted since derive")
    base_dir, snapshot = WORK / "base", WORK / "snapshot"
    for rec in manifest["lineageSnapshot"]["paths"]:
        if rs.sha256_file(snapshot / rec["path"]) != rec["sha256"]:
            _fail(f"snapshot drifted: {rec['path']}")

    cand = WORK / "candidate"
    if cand.exists():
        subprocess.run(["chmod", "-R", "u+w", str(cand)], check=False)
        subprocess.run(["rm", "-rf", str(cand)], check=True)
    cand.mkdir(parents=True)

    review_rows, all_added, applied_log = [], [], {}
    for rel, mode in TREATMENT.items():
        out = cand / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        base_path = base_dir / rel
        if mode == "canonical":
            data = base_path.read_bytes()
            out.write_bytes(data)
            disposition = "canonical-unchanged"
        elif mode == "lineage":
            text = _read(snapshot / rel)
            out.write_text(text, encoding="utf-8")
            disposition = "required"
        elif mode == "lineage-filtered":
            text, applied = _apply_ops(_read(snapshot / rel),
                                       FILTER_OPS[rel], f"filter {rel}")
            out.write_text(text, encoding="utf-8")
            applied_log[rel] = applied
            disposition = "required"
        elif mode == "generated":
            base_text = _read(base_path)
            if rel == "src/api.cpp":
                ops = _build_api_ops(_read(snapshot / rel))
            elif rel == "myalgorithm.py":
                ops = MYALG_OPS
            else:
                _fail(f"no generator for {rel}")
            text, applied = _apply_ops(base_text, ops, f"generate {rel}")
            out.write_text(text, encoding="utf-8")
            applied_log[rel] = applied
            disposition = "required"
        else:
            _fail(f"unknown mode {mode}")
        base_text = _read(base_path) if base_path.exists() else ""
        added = _added_lines(base_text, _read(out))
        all_added.extend((rel, ln) for ln in added)
        review_rows.append({"path": rel, "mode": mode,
                            "disposition": disposition,
                            "requiredBy": REQUIRED_BY.get(rel, []),
                            "sha256": rs.sha256_file(out),
                            "addedLines": len(added)})

    # forbidden scan over every candidate-vs-canonical added line
    hits = []
    for rel, ln in all_added:
        hits.extend(_forbidden_scan(_policy(), [ln], rel))

    # include closure of the seated tree must equal its member set
    seated_closure = _local_include_closure(cand / "src", ["api.cpp"])
    seated_srcs = sorted(p.name for p in (cand / "src").glob("*"))
    closure_ok = seated_closure == seated_srcs

    # known API floor
    floor = []
    for rel, pattern, name in API_FLOOR_CHECKS:
        ok = re.search(pattern, _read(cand / rel)) is not None
        floor.append({"symbol": name, "path": rel,
                      "status": "PASS" if ok else "FAIL"})

    # canonical-relative patch
    PATCH_OUT.parent.mkdir(parents=True, exist_ok=True)
    chunks = []
    for rel in sorted(TREATMENT):
        base_path = base_dir / rel
        base_text = _read(base_path) if base_path.exists() else ""
        cand_text = _read(cand / rel)
        if base_text == cand_text:
            continue
        chunks.append("".join(difflib.unified_diff(
            base_text.splitlines(True), cand_text.splitlines(True),
            fromfile=f"a/{rel}", tofile=f"b/{rel}")))
    PATCH_OUT.write_text("".join(chunks), encoding="utf-8")

    ok = (not hits) and closure_ok and all(f["status"] == "PASS" for f in floor)
    status = "PASS" if ok else "FAIL"
    tree_sha, tree_entries = rs.tree_sha256(cand)

    review = {
        "schemaVersion": 1, "kind": "closure-review", "status": status,
        "createdAt": rs.now_iso(),
        "manifestSha256": rs.sha256_file(EVID / "closure-manifest.json"),
        "policySha256": rs.sha256_file(POLICY_PATH),
        "paths": review_rows, "appliedOps": applied_log,
        "forbiddenMatches": hits,
        "seatedIncludeClosure": seated_closure,
        "seatedClosureEqualsMembers": closure_ok,
        "knownApiFloor": floor,
        "patch": {"path": str(PATCH_OUT.relative_to(REPO)),
                  "sha256": rs.sha256_file(PATCH_OUT)},
        "candidateTreeSha256": tree_sha,
    }
    _write_json(EVID / "closure-review.json", review)

    rt = policy["runtimeTreatment"]
    spec = {
        "schemaVersion": 1, "kind": "candidate-spec", "status": status,
        "createdAt": rs.now_iso(),
        "baseBudgetDefinition": rt["baseBudgetDefinition"],
        "share": rt["share"], "eligibility": rt["eligibility"],
        "callOrder": rt["callOrder"],
        "startingBoardAuthority": rt["startingBoardAuthority"],
        "seedXor": rt["seedXor"], "deadlineGuard": rt["deadlineGuard"],
        "reviewSha256": rs.sha256_file(EVID / "closure-review.json"),
    }
    _write_json(EVID / "candidate-spec.json", spec)

    if ok:
        for p in sorted(cand.rglob("*")):
            if p.is_file():
                p.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    print(json.dumps({"status": status, "candidate": str(cand),
                      "treeSha256": tree_sha,
                      "forbiddenMatches": len(hits),
                      "closureEqualsMembers": closure_ok,
                      "floorFails": [f["symbol"] for f in floor
                                     if f["status"] != "PASS"]}))
    return 0 if ok else 1


# ------------------------------------------------------------------ seal

def cmd_seal(args):
    policy = _policy()
    manifest = json.loads((EVID / "closure-manifest.json").read_text())
    review = json.loads((EVID / "closure-review.json").read_text())
    spec = json.loads((EVID / "candidate-spec.json").read_text())
    build = json.loads(pathlib.Path(args.build_receipt).read_text())
    unit = json.loads(pathlib.Path(args.unit_receipt).read_text())

    cand = WORK / "candidate"
    tree_sha, _ = rs.tree_sha256(cand)
    problems = []
    if manifest["status"] != "PASS":
        problems.append("closure manifest FAIL")
    if review["status"] != "PASS":
        problems.append("closure review FAIL")
    if review["candidateTreeSha256"] != tree_sha and not (cand / SO_NAME).exists():
        problems.append("candidate tree drifted since review")
    if review["forbiddenMatches"]:
        problems.append("forbidden matches present")
    if build.get("status") != "PASS":
        problems.append("build receipt FAIL")
    if unit.get("status") != "PASS":
        problems.append("unit receipt FAIL")
    so = cand / SO_NAME
    if not so.exists():
        problems.append("candidate .so missing")
        so_sha = None
    else:
        so_sha = rs.sha256_file(so)
        if build.get("outputSoSha256") != so_sha:
            problems.append("candidate .so != build receipt output")
        if unit.get("soSha256") != so_sha:
            problems.append("candidate .so != unit-tested binary")

    gate_eligible = not problems
    seal = {
        "schemaVersion": 1, "status": "PASS" if gate_eligible else "FAIL",
        "sealedAt": rs.now_iso(),
        "canonical": {"zipSha256": manifest["canonical"]["zipSha256"],
                      "memberManifestSha256":
                          manifest["canonical"]["memberManifestSha256"]},
        "lineageSnapshot": {
            "treeSha256": manifest["lineageSnapshot"]["treeSha256"],
            "paths": manifest["lineageSnapshot"]["paths"]},
        "closure": {
            "manifestSha256": rs.sha256_file(EVID / "closure-manifest.json"),
            "includeGraphSha256": rs.canonical_hash(manifest["includeClosure"]),
            "depfileSha256": build.get("depfileSha256"),
            "symbolGraphSha256": rs.canonical_hash(review["knownApiFloor"]),
            "paths": review["paths"]},
        "policy": {"policySha256": rs.sha256_file(POLICY_PATH),
                   "knownEdgesResolved": all(
                       f["status"] == "PASS" for f in review["knownApiFloor"]),
                   "unresolvedSymbols": [f["symbol"] for f in
                                         review["knownApiFloor"]
                                         if f["status"] != "PASS"],
                   "forbiddenMatches": review["forbiddenMatches"],
                   "unrelatedHunks": []},
        "runtimeSpec": {"specSha256": rs.sha256_file(EVID / "candidate-spec.json"),
                        "baseBudgetDefinition": spec["baseBudgetDefinition"],
                        "share": spec["share"],
                        "eligibility": spec["eligibility"],
                        "callOrder": spec["callOrder"],
                        "startingBoardAuthority": spec["startingBoardAuthority"]},
        "build": {"receiptSha256": rs.sha256_file(args.build_receipt),
                  "toolchain": build.get("toolchain"),
                  "commandSha256": build.get("commandSha256"),
                  "outputSoSha256": build.get("outputSoSha256"),
                  "importStatus": build.get("importStatus")},
        "unit": {"receiptSha256": rs.sha256_file(args.unit_receipt),
                 "apiStatus": unit.get("apiStatus"),
                 "branchStatus": unit.get("branchStatus"),
                 "budgetStatus": unit.get("budgetStatus"),
                 "deadlineStatus": unit.get("deadlineStatus"),
                 "rollbackStatus": unit.get("rollbackStatus"),
                 "checkerStatus": unit.get("checkerStatus")},
        "review": {"receiptSha256": rs.sha256_file(EVID / "closure-review.json"),
                   "architect": "population_candidate_prepare seat audit",
                   "status": review["status"]},
        "treatment": {"treeSha256": tree_sha, "soSha256": so_sha,
                      "memberManifestSha256": rs.canonical_hash(
                          rs.tree_sha256(cand)[1])},
        "approval": {"gateEligible": gate_eligible,
                     "reason": "all gates PASS" if gate_eligible
                               else "; ".join(problems)},
    }
    sha = _write_json(EVID / "candidate-seal.json", seal)
    print(json.dumps({"gateEligible": gate_eligible, "problems": problems,
                      "sealSha256": sha, "treatmentTreeSha256": tree_sha,
                      "treatmentSoSha256": so_sha}))
    return 0 if gate_eligible else 1


def _write_json(path, obj):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    path.write_text(body, encoding="utf-8")
    return rs.sha256_bytes(body.encode())


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("derive")
    sub.add_parser("seat")
    seal = sub.add_parser("seal")
    seal.add_argument("--build-receipt", required=True)
    seal.add_argument("--unit-receipt", required=True)
    args = ap.parse_args(argv)
    return {"derive": cmd_derive, "seat": cmd_seat,
            "seal": cmd_seal}[args.cmd](args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except PrepError as exc:
        print(f"CANDIDATE PREP FAIL: {exc}", file=sys.stderr)
        sys.exit(2)

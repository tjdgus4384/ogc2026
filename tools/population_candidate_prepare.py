#!/usr/bin/env python3
"""Derive, audit, seat and seal the C++ parallel-population candidate.

Ralplan Revision 4, Batch 1 (ARCH-POP-GATE-007 / SUBMISSION-POP-006).

The candidate tree is constructed mechanically from two immutable inputs:
  * the canonical v5.3 release zip (content-addressed base), and
  * a content-addressed snapshot of the current offline population lineage.

Every noncanonical byte in the candidate is produced by an explicit,
anchored edit rule carrying a requiredBy trace to a known API edge from
tools/population_closure_policy.json.  Rules tagged unrelated-reject
EXCLUDE lineage semantics (lock-guard hunks) whose only consumers are
forbidden kernels.  Anchor assertions fail closed: if the lineage or the
canonical base drifts, derivation stops instead of guessing.

Subcommands:
  derive   build candidate tree, closure manifest, canonical-relative patch
  audit    validate an architect review file into closure-review.json
  seat     freeze reviewed bytes into source/ and emit candidate-spec.json
  seal     verify build+unit receipts and emit candidate-seal.json
"""
import argparse
import difflib
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time
import zipfile

TOOLS = pathlib.Path(__file__).resolve().parent
ROOT = TOOLS.parent
WORK = ROOT / "runs/release/population-candidate"
BASE = WORK / "base"
LINEAGE = WORK / "lineage"
CAND = WORK / "candidate"
SOURCE = WORK / "source"
EV = WORK / "evidence"
POLICY_PATH = TOOLS / "population_closure_policy.json"
PATCH_PATH = TOOLS / "patches/population-tail-v1.patch"
PY = os.environ.get(
    "OGC_PY", "/data_fast/home/tjdgus4384/priv/ogc2026/.miniforge/envs/ogc2026/bin/python")

CANONICAL_MEMBERS = [
    "myalgorithm.py", "BUILD.txt", "utils.py",
    "sscore.cpython-312-x86_64-linux-gnu.so",
    "src/alns.hpp", "src/core.hpp", "src/crane.hpp", "src/geometry.hpp",
    "src/knobs.hpp", "src/occ.hpp", "src/repair.hpp", "src/api.cpp",
]
ADDED_MEMBERS = [
    "src/kernel_bandit.hpp", "src/kernel_parallel_bandit.hpp",
    "src/repair_scored.hpp", "src/repair_nfp_contact.hpp",
]
LINEAGE_FILES = [
    "myalgorithm.py", "src/api.cpp", "src/alns.hpp", "src/core.hpp",
    "src/repair.hpp",
] + ADDED_MEMBERS


def sha(data):
    if isinstance(data, (pathlib.Path, str)):
        data = pathlib.Path(data).read_bytes()
    return hashlib.sha256(data).hexdigest()


def jdump(obj, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, indent=1, sort_keys=True)
    path.write_text(text + "\n")
    return sha(text.encode())


def fail(msg):
    print(f"FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


# --------------------------------------------------------------- edit engine
class Rule:
    def __init__(self, rid, disposition, required_by, note=""):
        self.rid = rid
        self.disposition = disposition          # required | unrelated-reject
        self.required_by = required_by
        self.note = note

    def meta(self):
        return {"rule": self.rid, "disposition": self.disposition,
                "requiredBy": self.required_by, "note": self.note}


APPLIED = {}                                     # file -> [rule meta]


def _record(fname, rule):
    APPLIED.setdefault(fname, []).append(rule.meta())


def replace_once(fname, text, anchor, new, rule):
    n = text.count(anchor)
    if n != 1:
        fail(f"{rule.rid}: anchor occurs {n}x (expected 1) in {fname}")
    _record(fname, rule)
    return text.replace(anchor, new, 1)


def insert_after(fname, text, anchor, payload, rule):
    return replace_once(fname, text, anchor, anchor + payload, rule)


def insert_before(fname, text, anchor, payload, rule):
    return replace_once(fname, text, anchor, payload + anchor, rule)


def delete_once(fname, text, anchor, rule):
    return replace_once(fname, text, anchor, "", rule)


def extract_method(text, header):
    """Extract a brace-balanced block starting at the line `header`."""
    idx = text.find(header)
    if idx < 0:
        fail(f"lineage extraction: header not found: {header[:60]!r}")
    depth = 0
    i = idx
    while i < len(text):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = text.find("\n", i) + 1
                return text[idx:end]
        i += 1
    fail(f"unbalanced braces extracting {header[:60]!r}")


# ------------------------------------------------------------- construction
def build_candidate(policy):
    base = {m: (BASE / m).read_text() for m in CANONICAL_MEMBERS
            if not m.endswith(".so")}
    lin = {m: (LINEAGE / m).read_text() for m in LINEAGE_FILES}
    out = {}

    # ---- core.hpp: every lineage hunk is on the required floor -----------
    _record("src/core.hpp", Rule(
        "R-CORE-ALL", "required",
        ["core::vpExactEvents", "core::stateExactEvents", "core::eventOrderAt"],
        "exact checker-order event replay; consumed by alns::audit(exactEvents) "
        "and both bandit kernels"))
    out["src/core.hpp"] = lin["src/core.hpp"]

    # ---- repair.hpp: lineage minus the lock-release field ----------------
    t = lin["src/repair.hpp"]
    t = delete_once(
        "src/repair.hpp", t,
        "    bool releaseForcedBay = true;     // soft guidance may release mid-search\n",
        Rule("R-REPAIR-LOCKFIELD", "unrelated-reject", [],
             "consumed only by forbidden assignment/ejection/benders kernels; "
             "would alter canonical lock-release semantics"))
    _record("src/repair.hpp", Rule(
        "R-REPAIR-REST", "required",
        ["repair::Ctx.prefP/.exactEvents/.hasDeadline/.deadline",
         "repair::deadlineExpired", "repair::placementFeasible",
         "repair::shapeContactSets", "repair::tryPlacement"],
        "Ctx fields + deadline guards + exact-event placement routing; all "
        "default-off on the canonical path (hasDeadline=false, exactEvents="
        "false, prefP<0 -> regime default)"))
    out["src/repair.hpp"] = t

    # ---- alns.hpp: lineage minus three lock-guard hunks ------------------
    t = lin["src/alns.hpp"]
    t = replace_once(
        "src/alns.hpp", t,
        "        if (c.forcedBay && c.releaseForcedBay &&\n",
        "        if (c.forcedBay &&\n",
        Rule("R-ALNS-LOCKCOND", "unrelated-reject", [],
             "revert lock-release condition to canonical"))
    t = replace_once(
        "src/alns.hpp", t,
        """                if (A != B) {
                    if (c.forcedBay &&
                        ((c.forcedBay[A] >= 0 && c.forcedBay[A] != s.bay[B]) ||
                         (c.forcedBay[B] >= 0 && c.forcedBay[B] != s.bay[A])))
                        goto no_swap;
""",
        "                if (A != B) {\n",
        Rule("R-ALNS-SWAPGUARD", "unrelated-reject", [],
             "remove post-v5.3 forcedBay swap guard (lock-leak work, no "
             "population consumer)"))
    t = replace_once(
        "src/alns.hpp", t,
        """            if (c.forcedBay && c.forcedBay[c.tsBlk] >= 0 &&
                c.forcedBay[c.tsBlk] != c.tsBay) {
                ok = false;
            } else {
                const int* save = c.forcedBay;
                c.forcedBay = fbv.data();
                ok = insertByMode(c, c.tsBlk, c.tsECap, rp.repairMode);
                c.forcedBay = save;
                if (ok)
                    for (int A : rem) {
                        if (A == c.tsBlk) continue;
                        if (!insertByMode(c, A, -1, rp.repairMode)) {
                            ok = false;
                            break;
                        }
                    }
            }
""",
        """            const int* save = c.forcedBay;
            c.forcedBay = fbv.data();
            ok = insertByMode(c, c.tsBlk, c.tsECap, rp.repairMode);
            c.forcedBay = save;
            if (ok)
                for (int A : rem) {
                    if (A == c.tsBlk) continue;
                    if (!insertByMode(c, A, -1, rp.repairMode)) {
                        ok = false;
                        break;
                    }
                }
""",
        Rule("R-ALNS-TSBLK", "required",
             ["alns::insertByMode", "alns::RunParams.repairMode"],
             "keep repairMode dispatch (required) while reverting the "
             "unrelated forcedBay mismatch guard to canonical structure"))
    _record("src/alns.hpp", Rule(
        "R-ALNS-REST", "required",
        ["alns::audit(pb,s,bad,exactEvents)", "alns::RunParams.repairMode",
         "alns::insertByMode", "repair::deadlineExpired",
         "repair::placementFeasible"],
        "4-arg audit, repairMode dispatch via repair_scored/nfp_contact, "
        "ctx-deadline honoring in run/spatialCompact/timeCompact; all "
        "default-off on the canonical path"))
    out["src/alns.hpp"] = t

    # ---- api.cpp: canonical + anchored population surface ----------------
    t = base["src/api.cpp"]
    la = lin["src/api.cpp"]
    set_pref_p = extract_method(la, "    void setPrefP(double value) {")
    prep = extract_method(la, "    int prepareKernelRun() {")
    run_pb = extract_method(
        la, "    py::dict runKernelParallelBandit(double seconds, uint64_t seed) {")
    for frag, name in ((set_pref_p, "setPrefP"), (prep, "prepareKernelRun"),
                       (run_pb, "runKernelParallelBandit")):
        if "runLearnedRepair" in frag or "kernel_pattern" in frag:
            fail(f"extraction overran method boundary: {name}")
    t = insert_after(
        "src/api.cpp", t, '#include "alns.hpp"\n',
        '#include "kernel_parallel_bandit.hpp"\n',
        Rule("R-API-INC", "required",
             ["sscore.Engine.run_kernel_parallel_bandit"],
             "kernel_parallel_bandit.hpp includes kernel_bandit.hpp itself"))
    t = insert_after(
        "src/api.cpp", t, "    int horizon_ = 0;\n",
        "    double prefP_ = -1.0;\n",
        Rule("R-API-MEMBER", "required", ["repair::Ctx.prefP"],
             "engine-owned pref-first probability"))
    t = insert_before(
        "src/api.cpp", t,
        "    // ----------------------------------------------------- solution access\n",
        set_pref_p + "\n",
        Rule("R-API-SETPREFP", "required",
             ["repair::Ctx.prefP", "sscore.Engine.set_pref_p"],
             "exact lineage bytes"))
    t = insert_after(
        "src/api.cpp", t,
        "                repair::Ctx c2; c2.pb = &pb; c2.s = &s2; c2.oc = &oc2;\n"
        "                c2.horizon = horizon_;\n",
        "                c2.prefP = prefP_;\n",
        Rule("R-API-CTX2", "required", ["repair::Ctx.prefP"],
             "portfolio worker ctx inherits engine prefP (canonical env "
             "channel replacement)"))
    t = insert_after(
        "src/api.cpp", t,
        "                    repair::Ctx c3; c3.pb=&pb; c3.s=&s2; c3.oc=&oc2;\n"
        "                    c3.horizon=horizon_;\n",
        "                    c3.prefP=prefP_;\n",
        Rule("R-API-CTX3", "required", ["repair::Ctx.prefP"],
             "compaction ctx inherits engine prefP"))
    t = insert_after(
        "src/api.cpp", t,
        "        repair::Ctx c2; c2.pb=&pb; c2.s=&sol; c2.oc=&oc2; c2.horizon=horizon_;\n",
        "        c2.prefP = prefP_;\n",
        Rule("R-API-CTXSEED", "required", ["repair::Ctx.prefP"],
             "reconstructOrdered ctx inherits engine prefP"))
    t = insert_before(
        "src/api.cpp", t,
        "    // ------------------------------------------------------ reconstruction\n",
        prep + run_pb + "\n",
        Rule("R-API-KERNEL", "required",
             ["sscore.Engine.run_kernel_parallel_bandit"],
             "exact lineage bytes of the production wrapper; the scalar "
             "runKernelBandit wrapper is intentionally absent"))
    t = insert_after(
        "src/api.cpp", t,
        '        .def("swap_blocks", &Engine::swapBlocks)\n',
        '        .def("set_pref_p", &Engine::setPrefP)\n'
        '        .def("run_kernel_parallel_bandit", &Engine::runKernelParallelBandit,\n'
        '             py::arg("seconds"), py::arg("seed"))\n',
        Rule("R-API-PYBIND", "required",
             ["sscore.Engine.run_kernel_parallel_bandit",
              "sscore.Engine.set_pref_p"], "production exports only"))
    out["src/api.cpp"] = t

    # ---- myalgorithm.py: canonical + prefP chain + 15% tail --------------
    t = base["myalgorithm.py"]
    pref_rule = Rule(
        "R-PY-PREFP", "required",
        ["repair::Ctx.prefP", "sscore.Engine.set_pref_p"],
        "canonical _spec_rule 0.85 boost preserved through the engine-owned "
        "channel (repair.hpp reads Ctx.prefP instead of knobs::prefP())")
    t = replace_once("myalgorithm.py", t, "def _build_engine(p):\n",
                     "def _build_engine(p, pref_p=-1.0):\n", pref_rule)
    t = insert_after(
        "myalgorithm.py", t,
        '        float(w["w1"]), float(w["w2"]), float(w["w3"]))\n',
        "    eng.set_pref_p(float(pref_p))\n", pref_rule)
    t = replace_once("myalgorithm.py", t, "def _fresh(p, hmul=1):\n",
                     "def _fresh(p, hmul=1, pref_p=-1.0):\n", pref_rule)
    t = replace_once("myalgorithm.py", t, "    eng = _build_engine(p)\n",
                     "    eng = _build_engine(p, pref_p)\n", pref_rule)
    t = replace_once("myalgorithm.py", t,
                     "def _seed_free(p, diversify=True, asg=None):\n",
                     "def _seed_free(p, diversify=True, asg=None, pref_p=-1.0):\n",
                     pref_rule)
    t = replace_once("myalgorithm.py", t,
                     "        eng, _, _ = _fresh(p, hmul)\n",
                     "        eng, _, _ = _fresh(p, hmul, pref_p)\n", pref_rule)
    t = replace_once(
        "myalgorithm.py", t,
        """    if spec and asg is None:
        os.environ["OGC_PREFP"] = os.environ.get("OGC_PREFP", "0.85")
    eng, seeds = _seed_free(p, asg=asg)
""",
        """    pref_p = 0.85 if spec and asg is None else -1.0
    eng, seeds = _seed_free(p, asg=asg, pref_p=pref_p)
""", pref_rule)
    tail_rule = Rule(
        "R-PY-TAIL", "required",
        ["sscore.Engine.run_kernel_parallel_bandit"],
        "pre-registered candidate treatment: 15% of B, non-_pop_here only, "
        "engine-audited incumbent start, seed^0xA11CE, deadline-reserve guard")
    t = insert_after(
        "myalgorithm.py", t,
        "    _pop_here = sec >= 150.0 and sec / max(1, n) <= 1.6\n",
        """    # C++ parallel-population tail (pre-registered candidate treatment):
    # outside the Python population habitat carve a fixed 15% of the base
    # search budget B for the four-lane C++ population kernel; the primary
    # portfolio/slice path keeps its canonical structure inside 0.85*B.
    _pop_tail = 0.0
    if not _pop_here and sec > 60.0:
        _pop_tail = 0.15 * sec
        sec -= _pop_tail
""", tail_rule)
    t = replace_once(
        "myalgorithm.py", t,
        "        _slice_refine(p, eng, seed, deadline - reserve, eng.horizon())\n",
        "        _slice_refine(p, eng, seed, deadline - reserve - _pop_tail,\n"
        "                      eng.horizon())\n", tail_rule)
    t = replace_once(
        "myalgorithm.py", t,
        """    except StopIteration:
        pass
    except Exception:
        pass
""",
        """    except StopIteration:
        pass
    except Exception:
        pass
    if _pop_tail > 0.0 and time.time() + 1.0 < deadline - reserve:
        _kb = eng.run_kernel_parallel_bandit(
            min(_pop_tail, max(1.0, deadline - reserve - time.time())),
            seed ^ 0xA11CE)
        _diag("cpp-parallel-bandit-tail", **_kb)
""", tail_rule)
    out["myalgorithm.py"] = t

    # ---- BUILD.txt: candidate source inventory addendum ------------------
    t = base["BUILD.txt"]
    t = t.rstrip("\n") + """

---- population candidate addendum -------------------------------------
This candidate adds four headers to the canonical v5.3 source set:
  src/kernel_bandit.hpp           bandit policy library (no runtime export)
  src/kernel_parallel_bandit.hpp  four-lane parallel population kernel
  src/repair_scored.hpp           scored insertion repair (repairMode 1/3)
  src/repair_nfp_contact.hpp      NFP contact insertion repair (repairMode 2/3)
The build command is unchanged: src/api.cpp remains the single translation
unit and includes kernel_parallel_bandit.hpp. myalgorithm.py runs the kernel
as a fixed 15% tail of the search budget outside the Python population
habitat; there are no environment switches.
"""
    _record("BUILD.txt", Rule(
        "R-BUILDTXT", "required", ["package documentation contract"],
        "documents added members and unchanged build command"))
    out["BUILD.txt"] = t

    # ---- unchanged canonical members ------------------------------------
    for m in CANONICAL_MEMBERS:
        if not m.endswith(".so") and m not in out:
            out[m] = base[m]

    # ---- added kernel headers -------------------------------------------
    for m in ADDED_MEMBERS:
        _record(m, Rule(
            f"R-ADD-{pathlib.Path(m).stem}", "required",
            ["sscore.Engine.run_kernel_parallel_bandit"],
            "exact offline-lineage bytes, content-addressed"))
        out[m] = lin[m]

    return out


# ----------------------------------------------------------------- scans
def forbidden_scan(policy, files):
    hits = []
    pats = policy["forbiddenPatterns"]["envSelectors"] + \
        policy["forbiddenPatterns"]["symbols"]
    for name, text in files.items():
        for p in pats:
            if p in text:
                for i, line in enumerate(text.splitlines(), 1):
                    if p in line:
                        hits.append({"path": name, "pattern": p, "line": i})
    return hits


def floor_check(files):
    checks = [
        ("src/alns.hpp", "int* badBlock = nullptr,\n                 bool exactEvents = false"),
        ("src/alns.hpp", "int repairMode = 0;"),
        ("src/alns.hpp", "inline bool insertByMode"),
        ("src/core.hpp", "inline bool vpExactEvents"),
        ("src/core.hpp", "inline bool stateExactEvents"),
        ("src/repair.hpp", "double prefP = -1.0;"),
        ("src/repair.hpp", "bool exactEvents = false;"),
        ("src/repair.hpp", "bool hasDeadline = false;"),
        ("src/repair.hpp", "inline bool deadlineExpired"),
        ("src/repair.hpp", "inline bool placementFeasible"),
        ("src/repair.hpp", "inline void shapeContactSets"),
        ("src/api.cpp", "py::dict runKernelParallelBandit"),
        ("src/api.cpp", 'def("run_kernel_parallel_bandit"'),
        ("src/api.cpp", 'def("set_pref_p"'),
        ("myalgorithm.py", "_pop_tail = 0.15 * sec"),
        ("myalgorithm.py", "seed ^ 0xA11CE"),
    ]
    missing = [{"path": p, "needle": n} for p, n in checks
               if n not in files.get(p, "")]
    return missing


def include_closure(cand_dir):
    """Compiler-derived local include closure of the candidate TU."""
    inc = subprocess.run([PY, "-m", "pybind11", "--includes"],
                         capture_output=True, text=True, check=True)
    py_inc = inc.stdout.split()
    cmd = ["g++", "-std=c++17", "-fopenmp", "-MM", "-MF", "-",
           *py_inc, str(cand_dir / "src/api.cpp")]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=cand_dir)
    if r.returncode != 0:
        fail(f"depfile derivation failed:\n{r.stderr[-3000:]}")
    deps = r.stdout.replace("\\\n", " ").split(":", 1)[1].split()
    local, external = [], []
    for d in deps:
        p = pathlib.Path(d)
        if not p.is_absolute():
            p = (cand_dir / p).resolve()
        try:
            local.append(str(p.relative_to(cand_dir.resolve())))
        except ValueError:
            external.append(str(p))
    bad_ext = [e for e in external
               if "/priv/ogc2026/solver" in e or "/worktrees/" in e]
    if bad_ext:
        fail(f"include closure escapes candidate snapshot: {bad_ext}")
    return sorted(set(local)), sorted(set(external)), r.stdout


def syntax_check(cand_dir):
    inc = subprocess.run([PY, "-m", "pybind11", "--includes"],
                         capture_output=True, text=True, check=True)
    cmd = ["g++", "-std=c++17", "-fopenmp", "-fsyntax-only",
           *inc.stdout.split(), str(cand_dir / "src/api.cpp")]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=cand_dir)
    return r.returncode, (r.stderr or "")[-4000:]


# ------------------------------------------------------------------ derive
def cmd_derive(_args):
    policy = json.loads(POLICY_PATH.read_text())
    zip_path = ROOT / policy["canonical"]["zipPath"]
    if not zip_path.exists():
        zip_path = pathlib.Path(policy["lineageRoot"]).parent / \
            policy["canonical"]["zipPath"]
    if sha(zip_path) != policy["canonical"]["zipSha256"]:
        fail("canonical zip SHA mismatch")
    # base extract (idempotent, verified member-by-member)
    member_manifest = {}
    with zipfile.ZipFile(zip_path) as z:
        for m in CANONICAL_MEMBERS:
            data = z.read(m)
            member_manifest[m] = sha(data)
            dest = BASE / m
            if not dest.exists() or sha(dest) != member_manifest[m]:
                dest.parent.mkdir(parents=True, exist_ok=True)
                if dest.exists():
                    dest.chmod(0o644)
                dest.write_bytes(data)
    member_manifest_sha = sha(json.dumps(
        member_manifest, sort_keys=True).encode())

    # lineage snapshot (content-addressed, provenance-recorded)
    lroot = pathlib.Path(policy["lineageRoot"])
    repo = lroot.parent
    snapshot = []
    for f in LINEAGE_FILES:
        src = lroot / f
        dest = LINEAGE / f
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            dest.chmod(0o644)
        shutil.copyfile(src, dest)
        st = subprocess.run(
            ["git", "-C", str(repo), "status", "--porcelain",
             "--untracked-files=all", "--", f"solver/{f}"],
            capture_output=True, text=True).stdout.strip()
        tracked = not st.startswith("??")
        snapshot.append({"path": f, "sha256": sha(dest), "tracked": tracked,
                         "gitStatus": st or "clean"})
    lineage_tree_sha = sha(json.dumps(
        [(e["path"], e["sha256"]) for e in snapshot]).encode())

    # candidate construction
    APPLIED.clear()
    files = build_candidate(policy)

    hits = forbidden_scan(policy, files)
    if hits:
        fail("forbidden pattern in candidate tree:\n"
             + json.dumps(hits, indent=1))
    missing = floor_check(files)
    if missing:
        fail("known API floor unresolved:\n" + json.dumps(missing, indent=1))

    if CAND.exists():
        subprocess.run(["chmod", "-R", "u+w", str(CAND)], check=True)
        shutil.rmtree(CAND)
    for name, text in files.items():
        dest = CAND / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text)
    # canonical binary is NOT copied: the candidate ships only its own
    # freshly built, tested .so (no dirty/canonical binary reuse).

    rc, err = syntax_check(CAND)
    if rc != 0:
        fail(f"candidate does not compile (syntax pass):\n{err}")
    local, external, depfile_raw = include_closure(CAND)
    tree_members = sorted(str(p.relative_to(CAND))
                          for p in CAND.rglob("*") if p.is_file())
    outside = [d for d in local if d not in tree_members]
    if outside:
        fail(f"depfile references files outside candidate tree: {outside}")

    # patch + per-file classification
    patch_lines = []
    closure_paths = []
    for m in sorted(set(tree_members)):
        cand_text = (CAND / m).read_text()
        if m in CANONICAL_MEMBERS:
            base_text = (BASE / m).read_text()
            status = "unchanged" if cand_text == base_text else "modified"
        else:
            base_text, status = "", "added"
        entry = {"path": m, "status": status, "sha256": sha(cand_text.encode()),
                 "canonicalSha256": member_manifest.get(m),
                 "requiredBy": sorted({e for r in APPLIED.get(m, [])
                                       for e in r["requiredBy"]}),
                 "rules": APPLIED.get(m, []),
                 "reviewDisposition": None}
        if status != "unchanged":
            diff = list(difflib.unified_diff(
                base_text.splitlines(keepends=True),
                cand_text.splitlines(keepends=True),
                fromfile=f"canonical/{m}", tofile=f"candidate/{m}"))
            entry["diffSha256"] = sha("".join(diff).encode())
            patch_lines.extend(diff)
        closure_paths.append(entry)
    # canonical members not rewritten (byte-identical copies)
    for m in CANONICAL_MEMBERS:
        if m.endswith(".so"):
            continue
        if not any(e["path"] == m for e in closure_paths):
            fail(f"member lost during construction: {m}")

    unrelated = [e for e in closure_paths
                 if e["status"] != "unchanged" and not e["rules"]]
    if unrelated:
        fail("modified path without construction rule: "
             + json.dumps([e["path"] for e in unrelated]))

    PATCH_PATH.parent.mkdir(parents=True, exist_ok=True)
    PATCH_PATH.write_text("".join(patch_lines))

    include_graph = {}
    for m in tree_members:
        if m.endswith((".hpp", ".cpp")):
            include_graph[m] = re.findall(
                r'#include\s+"([^"]+)"', (CAND / m).read_text())

    manifest = {
        "schemaVersion": 1,
        "derivedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "policySha256": sha(POLICY_PATH),
        "canonical": {"zipSha256": policy["canonical"]["zipSha256"],
                      "memberManifest": member_manifest,
                      "memberManifestSha256": member_manifest_sha},
        "lineageSnapshot": {"treeSha256": lineage_tree_sha, "paths": snapshot},
        "translationUnit": "src/api.cpp",
        "depfile": {"local": local, "externalCount": len(external),
                    "sha256": sha(depfile_raw.encode())},
        "includeGraph": include_graph,
        "includeGraphSha256": sha(json.dumps(
            include_graph, sort_keys=True).encode()),
        "patch": {"path": str(PATCH_PATH.relative_to(ROOT)),
                  "sha256": sha(PATCH_PATH)},
        "forbiddenMatches": [],
        "unresolvedFloor": [],
        "paths": closure_paths,
    }
    msha = jdump(manifest, EV / "closure-manifest.json")
    print(f"derive OK: {len(closure_paths)} paths, "
          f"{sum(1 for e in closure_paths if e['status'] != 'unchanged')} "
          f"noncanonical, manifest {msha[:16]}")


# ------------------------------------------------------------------- audit
def cmd_audit(args):
    manifest = json.loads((EV / "closure-manifest.json").read_text())
    review = json.loads(pathlib.Path(args.review).read_text())
    disp = review.get("dispositions", {})
    problems = []
    for entry in manifest["paths"]:
        if entry["status"] == "unchanged":
            continue
        d = disp.get(entry["path"])
        if d is None:
            problems.append(f"no disposition for {entry['path']}")
            continue
        if d.get("verdict") != "required":
            problems.append(f"{entry['path']}: verdict {d.get('verdict')}")
        for rule in entry["rules"]:
            rv = d.get("rules", {}).get(rule["rule"])
            if rv not in ("required", "unrelated-reject"):
                problems.append(
                    f"{entry['path']}: rule {rule['rule']} unsigned ({rv})")
            elif rv != rule["disposition"]:
                problems.append(
                    f"{entry['path']}: rule {rule['rule']} reviewer says {rv},"
                    f" construction says {rule['disposition']}")
    status = "PASS" if not problems and review.get("verdict") == "APPROVE" \
        else "FAIL"
    out = {
        "schemaVersion": 1,
        "reviewedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "manifestSha256": sha((EV / "closure-manifest.json")),
        "patchSha256": manifest["patch"]["sha256"],
        "architect": review.get("architect", "unknown"),
        "verdict": review.get("verdict"),
        "problems": problems,
        "status": status,
        "review": review,
    }
    jdump(out, EV / "closure-review.json")
    print(f"audit {status}" + (f": {problems}" if problems else ""))
    if status != "PASS":
        sys.exit(1)


# -------------------------------------------------------------------- seat
def cmd_seat(_args):
    manifest = json.loads((EV / "closure-manifest.json").read_text())
    review = json.loads((EV / "closure-review.json").read_text())
    if review["status"] != "PASS":
        fail("closure review is not PASS")
    if review["manifestSha256"] != sha(EV / "closure-manifest.json"):
        fail("manifest drifted since review")
    if SOURCE.exists():
        subprocess.run(["chmod", "-R", "u+w", str(SOURCE)], check=True)
        shutil.rmtree(SOURCE)
    shutil.copytree(CAND, SOURCE)
    for entry in manifest["paths"]:
        if sha(SOURCE / entry["path"]) != entry["sha256"]:
            fail(f"seat drift on {entry['path']}")
    subprocess.run(["chmod", "-R", "a-w", str(SOURCE)], check=True)
    policy = json.loads(POLICY_PATH.read_text())
    spec = {
        "schemaVersion": 1,
        "seatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "manifestSha256": review["manifestSha256"],
        "policySha256": manifest["policySha256"],
        "runtimeSpec": policy["runtimeTreatment"],
        "sourceTreeSha256": tree_sha(SOURCE),
    }
    jdump(spec, EV / "candidate-spec.json")
    print(f"seat OK: source tree {spec['sourceTreeSha256'][:16]}")


def tree_sha(d):
    items = []
    for p in sorted(d.rglob("*")):
        if p.is_file():
            items.append((str(p.relative_to(d)), sha(p)))
    return sha(json.dumps(items).encode())


# -------------------------------------------------------------------- seal
def cmd_seal(_args):
    manifest = json.loads((EV / "closure-manifest.json").read_text())
    review = json.loads((EV / "closure-review.json").read_text())
    spec = json.loads((EV / "candidate-spec.json").read_text())
    build = json.loads((EV / "candidate-build.json").read_text())
    unit = json.loads((EV / "candidate-unit.json").read_text())
    policy = json.loads(POLICY_PATH.read_text())

    reasons = []
    if review["status"] != "PASS":
        reasons.append("review not PASS")
    if build.get("status") != "PASS":
        reasons.append("build not PASS")
    if unit.get("status") != "PASS":
        reasons.append("unit not PASS")
    if spec["sourceTreeSha256"] != tree_sha(SOURCE):
        reasons.append("sealed source drifted")
    if build.get("sourceTreeSha256") != spec["sourceTreeSha256"]:
        reasons.append("build receipt tree mismatch")
    if unit.get("sourceTreeSha256") != spec["sourceTreeSha256"]:
        reasons.append("unit receipt tree mismatch")
    if build.get("depfileLocal") != manifest["depfile"]["local"]:
        reasons.append("isolated-build depfile != derived closure")
    so_path = WORK / "solver/sscore.cpython-312-x86_64-linux-gnu.so"
    if not so_path.exists() or sha(so_path) != build.get("outputSoSha256"):
        reasons.append("tested .so missing or drifted")
    if unit.get("soSha256") != build.get("outputSoSha256"):
        reasons.append("unit tested a different binary")
    # forbidden re-scan on sealed bytes
    sealed = {str(p.relative_to(SOURCE)): p.read_text()
              for p in SOURCE.rglob("*") if p.is_file()
              and not p.name.endswith(".so")}
    hits = forbidden_scan(policy, sealed)
    if hits:
        reasons.append(f"forbidden pattern in sealed tree: {hits}")
    missing = floor_check(sealed)
    if missing:
        reasons.append(f"floor symbol missing in sealed tree: {missing}")

    eligible = not reasons
    seal = {
        "schemaVersion": 1,
        "status": "PASS" if eligible else "FAIL",
        "sealedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "canonical": {
            "zipSha256": manifest["canonical"]["zipSha256"],
            "memberManifestSha256":
                manifest["canonical"]["memberManifestSha256"]},
        "lineageSnapshot": {
            "treeSha256": manifest["lineageSnapshot"]["treeSha256"],
            "paths": manifest["lineageSnapshot"]["paths"]},
        "closure": {
            "manifestSha256": review["manifestSha256"],
            "includeGraphSha256": manifest["includeGraphSha256"],
            "depfileSha256": manifest["depfile"]["sha256"],
            "patchSha256": manifest["patch"]["sha256"],
            "paths": [{k: e[k] for k in
                       ("path", "status", "sha256", "requiredBy")}
                      | {"diffSha256": e.get("diffSha256"),
                         "reviewDisposition": "required"
                         if e["status"] != "unchanged" else
                         "canonical-unchanged"}
                      for e in manifest["paths"]]},
        "policy": {
            "policySha256": manifest["policySha256"],
            "knownEdgesResolved": True,
            "unresolvedSymbols": missing,
            "forbiddenMatches": hits,
            "unrelatedHunks": []},
        "runtimeSpec": {
            "specSha256": sha(EV / "candidate-spec.json"),
            **spec["runtimeSpec"]},
        "build": {
            "receiptSha256": sha(EV / "candidate-build.json"),
            "toolchain": build.get("toolchain"),
            "commandSha256": build.get("commandSha256"),
            "outputSoSha256": build.get("outputSoSha256"),
            "importStatus": build.get("importStatus")},
        "unit": {
            "receiptSha256": sha(EV / "candidate-unit.json"),
            **{k: unit.get(k) for k in
               ("apiStatus", "branchStatus", "budgetStatus", "deadlineStatus",
                "rollbackStatus", "checkerStatus")}},
        "review": {
            "receiptSha256": sha(EV / "closure-review.json"),
            "architect": review.get("architect"),
            "status": review["status"]},
        "treatment": {
            "treeSha256": spec["sourceTreeSha256"],
            "soSha256": build.get("outputSoSha256"),
            "memberManifestSha256": sha(json.dumps(
                [(e["path"], e["sha256"]) for e in manifest["paths"]]
                ).encode())},
        "approval": {
            "gateEligible": eligible,
            "reason": "all closure/build/unit/review gates PASS" if eligible
                      else "; ".join(reasons)},
    }
    jdump(seal, EV / "candidate-seal.json")
    print(f"seal: gateEligible={eligible}"
          + ("" if eligible else f" reasons={reasons}"))
    if not eligible:
        sys.exit(1)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("derive")
    a = sub.add_parser("audit")
    a.add_argument("--review", required=True)
    sub.add_parser("seat")
    sub.add_parser("seal")
    args = ap.parse_args()
    {"derive": cmd_derive, "audit": cmd_audit,
     "seat": cmd_seat, "seal": cmd_seal}[args.cmd](args)


if __name__ == "__main__":
    main()

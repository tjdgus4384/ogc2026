#!/usr/bin/env python3
"""Release receipt schema — canonical hashing helpers + fail-closed validators.

Shared by release_package.py / release_unit.py / release_build.sbatch and the
final union verification. Every receipt is schemaVersion 2 JSON. Validation is
fail-closed: a missing required key, an unknown top-level key, or a bad status
raises SchemaError.

CLI:
    python3 tools/release_schema.py validate --kind package --receipt R.json
"""
import argparse
import datetime
import hashlib
import json
import pathlib
import sys

SCHEMA_VERSION = 2
STATUSES = ("PASS", "FAIL")


class SchemaError(RuntimeError):
    pass


# ---------------------------------------------------------------- hashing

def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_json(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_hash(obj):
    return sha256_bytes(canonical_json(obj).encode("utf-8"))


def tree_sha256(root):
    """Content hash of a directory: sha256 over the canonical JSON of the
    sorted [{path, sha256}] manifest of every regular file under root."""
    root = pathlib.Path(root)
    entries = []
    for p in sorted(root.rglob("*")):
        if p.is_symlink():
            raise SchemaError(f"symlink in tree: {p}")
        if p.is_file():
            entries.append({"path": str(p.relative_to(root)), "sha256": sha256_file(p)})
    return canonical_hash(entries), entries


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- schemas

_COMMON = {"schemaVersion", "kind", "status", "createdAt", "tool", "assertions"}

# top-level required keys per kind (exact set: nothing missing, nothing extra)
_KINDS = {
    "package": _COMMON | {"variant", "source", "package", "selection"},
    "extract": _COMMON | {"archive", "extract"},
    "unit": _COMMON | {"gateId", "python", "solver", "checker", "dataRoot"},
    "build-proof": _COMMON | {"jobId", "extract", "build", "outputSoSha256",
                              "importStatus", "shipped"},
    "generic-static": _COMMON | {"gateId", "inputs", "outputs", "commandSha256"},
    "license": _COMMON | {"variant", "licenseId", "noticeRequired",
                          "shippedLegalMembers", "decisionSource", "paths",
                          "blockers"},
    "e2e-case": _COMMON | {"gateId", "caseId", "lineage", "case", "slurm",
                           "result"},
    "aggregate": _COMMON | {"runRoot", "lineage", "matrixSha256", "gates",
                            "cases"},
}

# required keys inside named nested objects, per kind
_NESTED = {
    "package": {
        "tool": {"path", "sha256"},
        "source": {"path", "sha256", "expectedSha256", "memberManifestSha256",
                   "canonicalSoSha256"},
        "package": {"path", "sha256A", "sha256B", "identical", "compression",
                    "fixedTimestamp", "members"},
        "selection": {"selectedVariant", "selectionReceiptSha256", "testedBinarySha256"},
    },
    "extract": {
        "tool": {"path", "sha256"},
        "archive": {"path", "sha256"},
        "extract": {"path", "treeSha256", "members"},
    },
    "unit": {
        "tool": {"path", "sha256"},
        "python": {"executable", "version"},
        "solver": {"path", "treeSha256"},
        "checker": {"path", "sha256"},
        "dataRoot": {"path"},
    },
    "build-proof": {
        "tool": {"path", "sha256"},
        "extract": {"path", "soSha256Pre", "soSha256Post"},
        "build": {"workTree", "command", "commandSha256", "toolchain"},
        "importStatus": {"prebuilt", "rebuilt", "exportsMatch"},
    },
    "generic-static": {
        "tool": {"path", "sha256"},
    },
    "license": {
        "tool": {"path", "sha256"},
    },
    "e2e-case": {
        "tool": {"path", "sha256"},
        "lineage": {"releaseZipSha256", "solverTreeSha256", "canonicalSoSha256",
                    "checkerSha256"},
        "case": {"dataset", "instance", "inputSha256", "mode", "seed", "tl"},
        "slurm": {"jobId", "partition"},
        "result": {"wall", "maxRssMb", "cpus", "error", "feasible", "overrun",
                   "objective", "z1", "z2", "z3", "solutionSha256",
                   "checkerReplayStatus"},
    },
    "aggregate": {
        "tool": {"path", "sha256"},
        "lineage": {"releaseZipSha256", "solverTreeSha256", "canonicalSoSha256",
                    "checkerSha256"},
    },
}


def _require(cond, msg):
    if not cond:
        raise SchemaError(msg)


def validate(receipt):
    _require(isinstance(receipt, dict), "receipt is not an object")
    kind = receipt.get("kind")
    _require(kind in _KINDS, f"unknown kind: {kind!r}")
    keys = set(receipt)
    required = _KINDS[kind]
    missing = required - keys
    unknown = keys - required
    _require(not missing, f"{kind}: missing keys {sorted(missing)}")
    _require(not unknown, f"{kind}: unknown keys {sorted(unknown)}")
    _require(receipt["schemaVersion"] == SCHEMA_VERSION,
             f"{kind}: schemaVersion must be {SCHEMA_VERSION}")
    _require(receipt["status"] in STATUSES, f"{kind}: bad status {receipt['status']!r}")
    _require(isinstance(receipt["assertions"], list) and receipt["assertions"],
             f"{kind}: assertions must be a non-empty list")
    for a in receipt["assertions"]:
        _require(isinstance(a, dict) and {"id", "status"} <= set(a),
                 f"{kind}: assertion needs id+status: {a!r}")
        _require(a["status"] in STATUSES, f"{kind}: assertion bad status: {a!r}")
    for field, req in _NESTED.get(kind, {}).items():
        obj = receipt.get(field)
        _require(isinstance(obj, dict), f"{kind}: {field} must be an object")
        miss = req - set(obj)
        _require(not miss, f"{kind}: {field} missing {sorted(miss)}")
    if kind == "build-proof":
        _require(receipt["shipped"] is False, "build-proof: shipped must be false")
    if kind == "package":
        pkg = receipt["package"]
        _require(isinstance(pkg["members"], list) and pkg["members"],
                 "package: members must be a non-empty list")
        for m in pkg["members"]:
            _require({"name", "srcName", "size", "sha256", "mode"} <= set(m),
                     f"package: member incomplete: {m!r}")
    if kind == "license":
        _require(isinstance(receipt["paths"], list) and receipt["paths"],
                 "license: paths must be a non-empty list")
        for p in receipt["paths"]:
            _require({"path", "sha256", "ownership", "source", "license",
                      "redistribution"} <= set(p),
                     f"license: path entry incomplete: {p!r}")
        _require(isinstance(receipt["blockers"], list),
                 "license: blockers must be a list")
        if receipt["status"] == "PASS":
            _require(receipt["blockers"] == [],
                     "license: PASS requires zero blockers")
    # a FAIL assertion forces overall FAIL
    if any(a["status"] == "FAIL" for a in receipt["assertions"]):
        _require(receipt["status"] == "FAIL",
                 f"{kind}: has FAIL assertion but overall status PASS")
    return True


def write_receipt(path, receipt):
    validate(receipt)
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(receipt, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    path.write_text(body, encoding="utf-8")
    return sha256_bytes(body.encode("utf-8"))


# ---------------------------------------------------------------- CLI

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("validate")
    v.add_argument("--receipt", required=True)
    v.add_argument("--kind", default=None,
                   help="optional expected kind; mismatch fails")
    args = ap.parse_args(argv)
    receipt = json.loads(pathlib.Path(args.receipt).read_text())
    validate(receipt)
    if args.kind and receipt["kind"] != args.kind:
        raise SchemaError(f"expected kind {args.kind}, got {receipt['kind']}")
    print(json.dumps({"receipt": args.receipt, "kind": receipt["kind"],
                      "status": receipt["status"],
                      "sha256": sha256_file(args.receipt)}))
    return 0 if receipt["status"] == "PASS" else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SchemaError as exc:
        print(f"SCHEMA FAIL: {exc}", file=sys.stderr)
        sys.exit(2)

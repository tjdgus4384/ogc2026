#!/usr/bin/env python3
"""Deterministic explicit-member release packager (Stage 2 / Revision 3 Batch 0).

Builds the compliant deterministic release ZIP from the canonical source zip:
the ONLY name change is root BUILD.txt -> build.txt; every member's bytes are
byte-identical to the canonical members. Optional legal members (LICENSE,
NOTICE) may be appended only via an explicit --legal flag when license signoff
requires them.

Determinism contract: two independent extractions each produce a ZIP with
ZIP_STORED, fixed DOS timestamp 1980-01-01 00:00:00, create_system=3, mode
0644, empty extra/comment, explicit member order. Both archive SHA-256 must be
identical before the atomic rename to the final path.

Subcommands:
    package-twice --source Z --expected-source-sha256 H --output OUT
                  --receipt R [--legal NAME=PATH]...
    audit         --archive Z --receipt R [--source Z --expected-source-sha256 H]
                  [--expect-legal NAME]...
    extract       --archive Z --output DIR --receipt R
"""
import argparse
import json
import os
import pathlib
import shutil
import stat
import sys
import tempfile
import unicodedata
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import release_schema as rs

CANONICAL_INPUT_MEMBERS = [
    "BUILD.txt",
    "myalgorithm.py",
    "utils.py",
    "sscore.cpython-312-x86_64-linux-gnu.so",
    "src/api.cpp",
    "src/alns.hpp",
    "src/core.hpp",
    "src/crane.hpp",
    "src/geometry.hpp",
    "src/knobs.hpp",
    "src/occ.hpp",
    "src/repair.hpp",
]
RENAMES = {"BUILD.txt": "build.txt"}
OUTPUT_ORDER = [
    "build.txt",
    "myalgorithm.py",
    "utils.py",
    "sscore.cpython-312-x86_64-linux-gnu.so",
    "src/api.cpp",
    "src/alns.hpp",
    "src/core.hpp",
    "src/crane.hpp",
    "src/geometry.hpp",
    "src/knobs.hpp",
    "src/occ.hpp",
    "src/repair.hpp",
]
ALLOWED_LEGAL = ("LICENSE", "NOTICE")
SO_NAME = "sscore.cpython-312-x86_64-linux-gnu.so"
FIXED_DOS = (1980, 1, 1, 0, 0, 0)
FILE_MODE = 0o644


class PackageError(RuntimeError):
    pass


def _fail(msg):
    raise PackageError(msg)


# ------------------------------------------------------------- name safety

def _check_member_name(name):
    if name.endswith("/"):
        _fail(f"directory entry forbidden: {name!r}")
    if "\\" in name:
        _fail(f"backslash path forbidden: {name!r}")
    if name.startswith("/") or (len(name) > 1 and name[1] == ":"):
        _fail(f"absolute path forbidden: {name!r}")
    parts = name.split("/")
    if any(p in ("", ".", "..") for p in parts):
        _fail(f"unsafe path segment: {name!r}")


def _check_name_collisions(names):
    if len(names) != len(set(names)):
        _fail("duplicate member names")
    folded = [unicodedata.normalize("NFKC", n).casefold() for n in names]
    if len(folded) != len(set(folded)):
        _fail("case/Unicode-normalization member collision")


def _audit_infos(zf, *, require_fixed_meta, allowed_compress):
    names = zf.namelist()
    _check_name_collisions(names)
    for info in zf.infolist():
        _check_member_name(info.filename)
        if info.flag_bits & 0x1:
            _fail(f"encrypted member: {info.filename}")
        mode = info.external_attr >> 16
        if mode and not stat.S_ISREG(mode):
            _fail(f"non-regular member: {info.filename} mode {oct(mode)}")
        if info.compress_type not in allowed_compress:
            _fail(f"unexpected compression {info.compress_type}: {info.filename}")
        if require_fixed_meta:
            if info.date_time != FIXED_DOS:
                _fail(f"timestamp not fixed: {info.filename} {info.date_time}")
            if info.compress_type != zipfile.ZIP_STORED:
                _fail(f"not ZIP_STORED: {info.filename}")
            if getattr(info, "comment", b""):
                _fail(f"member comment present: {info.filename}")
            if info.extra:
                _fail(f"member extra field present: {info.filename}")
            if stat.S_IMODE(info.external_attr >> 16) != FILE_MODE:
                _fail(f"mode not {oct(FILE_MODE)}: {info.filename}")
            if info.create_system != 3:
                _fail(f"create_system not 3: {info.filename}")
    if require_fixed_meta and zf.comment:
        _fail("archive comment present")
    return names


def audit_source_zip(source, expected_sha):
    src_sha = rs.sha256_file(source)
    if src_sha != expected_sha:
        _fail(f"source zip SHA mismatch: expected {expected_sha}, got {src_sha}")
    with zipfile.ZipFile(source) as zf:
        names = _audit_infos(zf, require_fixed_meta=False,
                             allowed_compress={zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED})
        if sorted(names) != sorted(CANONICAL_INPUT_MEMBERS):
            _fail(f"canonical member set mismatch: {sorted(names)}")
        member_sha = {n: rs.sha256_bytes(zf.read(n)) for n in names}
    return src_sha, member_sha


# ------------------------------------------------------------- build

def _safe_extract(source, dest):
    dest = pathlib.Path(dest)
    with zipfile.ZipFile(source) as zf:
        for name in zf.namelist():
            _check_member_name(name)
            target = dest / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(zf.read(name))
    return dest


def _member_plan(extract_dir, legal):
    plan = []
    for out_name in OUTPUT_ORDER:
        src_name = next((k for k, v in RENAMES.items() if v == out_name), out_name)
        plan.append((out_name, src_name, pathlib.Path(extract_dir) / src_name))
    for name, path in legal:
        plan.append((name, f"(external) {path}", pathlib.Path(path)))
    return plan


def _build_zip(out_path, plan):
    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_STORED,
                         allowZip64=False) as zf:
        for out_name, _src, src_path in plan:
            data = src_path.read_bytes()
            info = zipfile.ZipInfo(out_name, date_time=FIXED_DOS)
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | FILE_MODE) << 16
            info.extra = b""
            zf.writestr(info, data)


def _parse_legal(pairs):
    legal = []
    for spec in pairs or []:
        name, _, path = spec.partition("=")
        if name not in ALLOWED_LEGAL:
            _fail(f"legal member {name!r} not in allowlist {ALLOWED_LEGAL}")
        if not path or not pathlib.Path(path).is_file():
            _fail(f"legal member source missing: {spec!r}")
        legal.append((name, path))
    return legal


def cmd_package_twice(args):
    source = pathlib.Path(args.source)
    output = pathlib.Path(args.output)
    legal = _parse_legal(args.legal)
    expected_names = OUTPUT_ORDER + [n for n, _ in legal]

    src_sha, member_sha = audit_source_zip(source, args.expected_source_sha256)
    assertions = [{"id": "source-sha", "status": "PASS", "detail": src_sha},
                  {"id": "source-members-12", "status": "PASS",
                   "detail": "exact canonical member set"}]

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as tmp:
        tmp = pathlib.Path(tmp)
        ex_a = _safe_extract(source, tmp / "extract-a")
        ex_b = _safe_extract(source, tmp / "extract-b")
        zip_a = tmp / "a.zip"
        zip_b = tmp / "b.zip"
        _build_zip(zip_a, _member_plan(ex_a, legal))
        _build_zip(zip_b, _member_plan(ex_b, legal))
        sha_a = rs.sha256_file(zip_a)
        sha_b = rs.sha256_file(zip_b)
        identical = sha_a == sha_b
        assertions.append({"id": "ab-equality", "status": "PASS" if identical else "FAIL",
                           "detail": f"A={sha_a} B={sha_b}"})
        if not identical:
            _fail("A/B archive SHA mismatch")

        members = []
        with zipfile.ZipFile(zip_a) as zf:
            names = _audit_infos(zf, require_fixed_meta=True,
                                 allowed_compress={zipfile.ZIP_STORED})
            if names != expected_names:
                _fail(f"member order mismatch: {names}")
            for info in zf.infolist():
                data = zf.read(info)
                sha = rs.sha256_bytes(data)
                src_name = next((k for k, v in RENAMES.items()
                                 if v == info.filename), info.filename)
                if src_name in member_sha and sha != member_sha[src_name]:
                    _fail(f"member bytes drift vs canonical: {info.filename}")
                members.append({"name": info.filename, "srcName": src_name,
                                "size": info.file_size, "sha256": sha,
                                "mode": oct(stat.S_IMODE(info.external_attr >> 16))})
        assertions.append({"id": "byte-preservation", "status": "PASS",
                           "detail": "all members byte-identical to canonical "
                                     "(BUILD.txt renamed build.txt)"})
        assertions.append({"id": "archive-audit", "status": "PASS",
                           "detail": "fixed metadata, no denial hit"})
        os.replace(zip_a, output)

    receipt = {
        "schemaVersion": rs.SCHEMA_VERSION,
        "kind": "package",
        "status": "PASS",
        "createdAt": rs.now_iso(),
        "variant": args.variant,
        "tool": {"path": "tools/release_package.py",
                 "sha256": rs.sha256_file(__file__)},
        "source": {"path": str(source), "sha256": src_sha,
                   "expectedSha256": args.expected_source_sha256,
                   "memberManifestSha256": rs.canonical_hash(
                       sorted(member_sha.items())),
                   "canonicalSoSha256": member_sha[SO_NAME]},
        "package": {"path": str(output), "sha256A": sha_a, "sha256B": sha_b,
                    "identical": True, "compression": "ZIP_STORED",
                    "fixedTimestamp": "1980-01-01T00:00:00", "members": members},
        "selection": {"selectedVariant": None, "selectionReceiptSha256": None,
                      "testedBinarySha256": None},
        "assertions": assertions,
    }
    receipt_sha = rs.write_receipt(args.receipt, receipt)
    print(json.dumps({"output": str(output), "sha256": sha_a,
                      "receipt": args.receipt, "receiptSha256": receipt_sha}))


def cmd_package_manifest(args):
    """Deterministic A/B package of a sealed source TREE via an explicit member
    manifest: JSON list of {"name", "path", "sha256"} in exact output order.
    Used for the population-candidate variant whose member set is derived from
    candidate-seal closure paths rather than the canonical 12."""
    tree = pathlib.Path(args.tree).resolve()
    output = pathlib.Path(args.output)
    manifest = json.loads(pathlib.Path(args.manifest).read_text())
    if not isinstance(manifest, list) or not manifest:
        _fail("manifest must be a non-empty list")
    names = []
    for m in manifest:
        if not {"name", "path", "sha256"} <= set(m):
            _fail(f"manifest entry incomplete: {m!r}")
        _check_member_name(m["name"])
        names.append(m["name"])
    _check_name_collisions(names)
    if "build.txt" not in names:
        _fail("manifest must ship lowercase build.txt")
    if "BUILD.txt" in names:
        _fail("uppercase BUILD.txt forbidden in output")

    assertions = []
    for m in manifest:
        src = tree / m["path"]
        if not src.is_file():
            _fail(f"manifest source missing: {src}")
        actual = rs.sha256_file(src)
        if actual != m["sha256"]:
            _fail(f"manifest sha mismatch for {m['name']}: {actual}")
    assertions.append({"id": "manifest-sha", "status": "PASS",
                       "detail": f"{len(manifest)} members match sealed tree"})

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as tmp:
        tmp = pathlib.Path(tmp)
        # two independent copies from the sealed tree
        plans = []
        for tag in ("a", "b"):
            stage = tmp / f"stage-{tag}"
            plan = []
            for m in manifest:
                dst = stage / m["name"]
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(tree / m["path"], dst)
                if rs.sha256_file(dst) != m["sha256"]:
                    _fail(f"copy drift: {m['name']}")
                plan.append((m["name"], m["path"], dst))
            plans.append(plan)
        zip_a, zip_b = tmp / "a.zip", tmp / "b.zip"
        _build_zip(zip_a, plans[0])
        _build_zip(zip_b, plans[1])
        sha_a, sha_b = rs.sha256_file(zip_a), rs.sha256_file(zip_b)
        if sha_a != sha_b:
            _fail("A/B archive SHA mismatch")
        assertions.append({"id": "ab-equality", "status": "PASS",
                           "detail": f"A={sha_a} B={sha_b}"})
        members = []
        with zipfile.ZipFile(zip_a) as zf:
            got = _audit_infos(zf, require_fixed_meta=True,
                               allowed_compress={zipfile.ZIP_STORED})
            if got != names:
                _fail(f"member order mismatch: {got}")
            for info in zf.infolist():
                members.append({"name": info.filename,
                                "srcName": next(m["path"] for m in manifest
                                                if m["name"] == info.filename),
                                "size": info.file_size,
                                "sha256": rs.sha256_bytes(zf.read(info)),
                                "mode": oct(stat.S_IMODE(info.external_attr >> 16))})
        assertions.append({"id": "archive-audit", "status": "PASS",
                           "detail": "fixed metadata, no denial hit"})
        os.replace(zip_a, output)

    so_members = [m for m in members if m["name"].endswith(".so")]
    receipt = {
        "schemaVersion": rs.SCHEMA_VERSION,
        "kind": "package",
        "status": "PASS",
        "createdAt": rs.now_iso(),
        "variant": args.variant,
        "tool": {"path": "tools/release_package.py",
                 "sha256": rs.sha256_file(__file__)},
        "source": {"path": str(tree), "sha256": rs.tree_sha256(tree)[0],
                   "expectedSha256": args.expected_tree_sha256 or "",
                   "memberManifestSha256": rs.canonical_hash(manifest),
                   "canonicalSoSha256": so_members[0]["sha256"] if so_members else ""},
        "package": {"path": str(output), "sha256A": sha_a, "sha256B": sha_b,
                    "identical": True, "compression": "ZIP_STORED",
                    "fixedTimestamp": "1980-01-01T00:00:00", "members": members},
        "selection": {"selectedVariant": args.variant,
                      "selectionReceiptSha256": args.selection_receipt_sha256,
                      "testedBinarySha256": so_members[0]["sha256"] if so_members else None},
        "assertions": assertions,
    }
    if args.expected_tree_sha256 and receipt["source"]["sha256"] != args.expected_tree_sha256:
        _fail(f"sealed tree drift: {receipt['source']['sha256']}")
    receipt_sha = rs.write_receipt(args.receipt, receipt)
    print(json.dumps({"output": str(output), "sha256": sha_a,
                      "receipt": args.receipt, "receiptSha256": receipt_sha}))


def cmd_audit(args):
    archive = pathlib.Path(args.archive)
    expected_names = OUTPUT_ORDER + list(args.expect_legal or [])
    assertions = []
    with zipfile.ZipFile(archive) as zf:
        names = _audit_infos(zf, require_fixed_meta=True,
                             allowed_compress={zipfile.ZIP_STORED})
        if names != expected_names:
            _fail(f"member order mismatch: {names}")
        member_sha = {n: rs.sha256_bytes(zf.read(n)) for n in names}
    assertions.append({"id": "archive-audit", "status": "PASS",
                       "detail": "explicit members, fixed metadata, denial 0"})
    if args.source:
        _, canon_sha = audit_source_zip(args.source, args.expected_source_sha256)
        for name in OUTPUT_ORDER:
            src_name = next((k for k, v in RENAMES.items() if v == name), name)
            if member_sha[name] != canon_sha[src_name]:
                _fail(f"member bytes drift vs canonical: {name}")
        assertions.append({"id": "byte-preservation", "status": "PASS",
                           "detail": "members byte-identical to canonical"})
    receipt = {
        "schemaVersion": rs.SCHEMA_VERSION,
        "kind": "generic-static",
        "status": "PASS",
        "createdAt": rs.now_iso(),
        "gateId": "A-ARCHIVE",
        "tool": {"path": "tools/release_package.py",
                 "sha256": rs.sha256_file(__file__)},
        "inputs": [{"path": str(archive), "sha256": rs.sha256_file(archive)}],
        "outputs": [{"name": n, "sha256": s} for n, s in member_sha.items()],
        "commandSha256": rs.canonical_hash(sys.argv),
        "assertions": assertions,
    }
    receipt_sha = rs.write_receipt(args.receipt, receipt)
    print(json.dumps({"archive": str(archive),
                      "sha256": rs.sha256_file(archive),
                      "receipt": args.receipt, "receiptSha256": receipt_sha}))


def cmd_extract(args):
    archive = pathlib.Path(args.archive)
    output = pathlib.Path(args.output)
    if output.exists():
        _fail(f"extract target already exists: {output}")
    archive_sha = rs.sha256_file(archive)
    with zipfile.ZipFile(archive) as zf:
        _audit_infos(zf, require_fixed_meta=True,
                     allowed_compress={zipfile.ZIP_STORED})
    _safe_extract(archive, output)
    tree_sha, entries = rs.tree_sha256(output)
    for p in sorted(output.rglob("*")):
        if p.is_file():
            p.chmod(0o444)
        elif p.is_dir():
            p.chmod(0o555)
    output.chmod(0o555)
    receipt = {
        "schemaVersion": rs.SCHEMA_VERSION,
        "kind": "extract",
        "status": "PASS",
        "createdAt": rs.now_iso(),
        "tool": {"path": "tools/release_package.py",
                 "sha256": rs.sha256_file(__file__)},
        "archive": {"path": str(archive), "sha256": archive_sha},
        "extract": {"path": str(output), "treeSha256": tree_sha,
                    "members": entries},
        "assertions": [{"id": "fresh-extract", "status": "PASS",
                        "detail": f"{len(entries)} files, read-only"}],
    }
    receipt_sha = rs.write_receipt(args.receipt, receipt)
    print(json.dumps({"extract": str(output), "treeSha256": tree_sha,
                      "receipt": args.receipt, "receiptSha256": receipt_sha}))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("package-twice")
    p.add_argument("--source", required=True)
    p.add_argument("--expected-source-sha256", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--receipt", required=True)
    p.add_argument("--variant", default="v53-fallback")
    p.add_argument("--legal", action="append", metavar="NAME=PATH")
    p.set_defaults(func=cmd_package_twice)

    a = sub.add_parser("audit")
    a.add_argument("--archive", required=True)
    a.add_argument("--receipt", required=True)
    a.add_argument("--source")
    a.add_argument("--expected-source-sha256")
    a.add_argument("--expect-legal", action="append", metavar="NAME")
    a.set_defaults(func=cmd_audit)

    e = sub.add_parser("extract")
    e.add_argument("--archive", required=True)
    e.add_argument("--output", required=True)
    e.add_argument("--receipt", required=True)
    e.set_defaults(func=cmd_extract)

    m = sub.add_parser("package-manifest")
    m.add_argument("--tree", required=True)
    m.add_argument("--manifest", required=True)
    m.add_argument("--output", required=True)
    m.add_argument("--receipt", required=True)
    m.add_argument("--variant", default="population")
    m.add_argument("--expected-tree-sha256")
    m.add_argument("--selection-receipt-sha256")
    m.set_defaults(func=cmd_package_manifest)

    args = ap.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except PackageError as exc:
        print(f"PACKAGE FAIL: {exc}", file=sys.stderr)
        sys.exit(2)

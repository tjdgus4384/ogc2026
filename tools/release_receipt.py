#!/usr/bin/env python3
"""Assemble + verify the top-level release receipt (final union verification).

Consumes the automatic selection receipt and every child receipt, verifies the
shared hash-propagation set (selectedVariant, selectionReceiptSha256,
candidateSealSha256|null, closureManifestSha256|null, testedTreeSha256,
testedSoSha256, selectedMemberManifestSha256) and the attachment bytes, then
freezes release-receipt.json. Any stale/missing/drifting child fails closed.

    python3 tools/release_receipt.py \
        --selected-release runs/release/evidence/selected-release.json \
        --child package=... --child extract=... --child unit=... \
        --child build-proof=... --child license=... --child e2e=... \
        --attachment code=submissions/....zip \
        [--attachment report=docs/....pdf] [--child pdf-audit=...] \
        --output runs/release/evidence/release-receipt.json
"""
import argparse
import json
import os
import pathlib
import sys

TOOLS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
import release_schema as rs

PROPAGATION_FIELDS = ("selectedVariant", "selectionReceiptSha256",
                      "candidateSealSha256", "closureManifestSha256",
                      "testedTreeSha256", "testedSoSha256",
                      "selectedMemberManifestSha256")
REQUIRED_CHILDREN = ("package", "extract", "unit", "build-proof", "license", "e2e")
SO_SUFFIX = ".so"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--selected-release", required=True)
    ap.add_argument("--child", action="append", required=True, metavar="NAME=PATH")
    ap.add_argument("--attachment", action="append", required=True,
                    metavar="NAME=PATH")
    ap.add_argument("--output", required=True)
    args = ap.parse_args(argv)

    assertions = []

    def A(aid, ok, detail=""):
        assertions.append({"id": aid, "status": "PASS" if ok else "FAIL",
                           "detail": str(detail)[:400]})

    sel_path = pathlib.Path(args.selected_release)
    selected = json.loads(sel_path.read_text())
    sel_sha = rs.sha256_file(sel_path)
    missing = [f for f in PROPAGATION_FIELDS if f not in selected]
    A("selection-fields", not missing, f"missing={missing}")
    variant = selected.get("selectedVariant")
    A("selection-variant", variant in ("population", "v53-fallback"), variant)
    if variant == "v53-fallback":
        A("fallback-null-candidate-hashes",
          selected.get("candidateSealSha256") is None
          and selected.get("closureManifestSha256") is None,
          "candidate hashes must be null for fallback")

    children = {}
    for spec in args.child:
        name, _, path = spec.partition("=")
        p = pathlib.Path(path)
        if not p.is_file():
            A(f"child-{name}-exists", False, str(p))
            continue
        doc = json.loads(p.read_text())
        try:
            if doc.get("kind") in ("package", "extract", "unit", "build-proof",
                                   "license", "aggregate", "generic-static",
                                   "e2e-case"):
                rs.validate(doc)
            ok = doc.get("status", "PASS") == "PASS"
        except rs.SchemaError as exc:
            A(f"child-{name}-schema", False, str(exc))
            continue
        children[name] = {"path": str(p), "sha256": rs.sha256_file(p),
                          "kind": doc.get("kind"), "status": doc.get("status"),
                          "doc": doc}
        A(f"child-{name}-pass", ok, doc.get("status"))
    miss_children = [c for c in REQUIRED_CHILDREN if c not in children]
    A("children-complete", not miss_children, f"missing={miss_children}")

    attachments = {}
    for spec in args.attachment:
        name, _, path = spec.partition("=")
        p = pathlib.Path(path)
        ok = p.is_file()
        attachments[name] = {"name": p.name, "path": str(p),
                             "sha256": rs.sha256_file(p) if ok else None}
        A(f"attachment-{name}-exists", ok, str(p))
    A("attachment-code-present", "code" in attachments, "")

    # cross-artifact hash chain
    pkg = children.get("package", {}).get("doc")
    agg = children.get("e2e", {}).get("doc")
    ext = children.get("extract", {}).get("doc")
    if pkg and "code" in attachments and attachments["code"]["sha256"]:
        A("package-attachment-bytes",
          pkg["package"]["sha256A"] == attachments["code"]["sha256"],
          f"pkg={pkg['package']['sha256A']} att={attachments['code']['sha256']}")
        so = [m for m in pkg["package"]["members"] if m["name"].endswith(SO_SUFFIX)]
        A("tested-so-propagation",
          bool(so) and selected.get("testedSoSha256") == so[0]["sha256"],
          f"selected={selected.get('testedSoSha256')}")
        A("member-manifest-propagation",
          selected.get("selectedMemberManifestSha256") == rs.canonical_hash(
              [{"name": m["name"], "sha256": m["sha256"]}
               for m in pkg["package"]["members"]]),
          "selectedMemberManifestSha256 vs package members")
    if agg and pkg and "lineage" in agg:
        A("e2e-lineage-zip",
          agg["lineage"]["releaseZipSha256"] == pkg["package"]["sha256A"],
          agg["lineage"]["releaseZipSha256"])
    if agg and ext and "lineage" in agg:
        A("e2e-lineage-tree",
          agg["lineage"]["solverTreeSha256"] == ext["extract"]["treeSha256"],
          agg["lineage"]["solverTreeSha256"])
        A("tested-tree-propagation",
          selected.get("testedTreeSha256") == ext["extract"]["treeSha256"],
          f"selected={selected.get('testedTreeSha256')}")
    lic = children.get("license", {}).get("doc")
    if lic:
        A("license-variant-match", lic.get("variant") == variant,
          f"license={lic.get('variant')} selected={variant}")

    status = "PASS" if all(a["status"] == "PASS" for a in assertions) else "FAIL"
    receipt = {
        "schemaVersion": rs.SCHEMA_VERSION,
        "kind": "generic-static",
        "status": status,
        "createdAt": rs.now_iso(),
        "gateId": "RELEASE-RECEIPT",
        "tool": {"path": "tools/release_receipt.py",
                 "sha256": rs.sha256_file(__file__)},
        "inputs": ([{"path": str(sel_path), "sha256": sel_sha,
                     "role": "selected-release",
                     "selectedVariant": variant,
                     "propagation": {f: selected.get(f)
                                     for f in PROPAGATION_FIELDS}}]
                   + [{"path": c["path"], "sha256": c["sha256"], "role": name,
                       "kind": c["kind"], "status": c["status"]}
                      for name, c in sorted(children.items())]),
        "outputs": [{"path": a["path"], "sha256": a["sha256"], "role": name,
                     "name": a["name"]}
                    for name, a in sorted(attachments.items())],
        "commandSha256": rs.canonical_hash(sys.argv),
        "assertions": assertions,
    }
    out = pathlib.Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(receipt, indent=2, sort_keys=True) + "\n"
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    with os.fdopen(fd, "w") as f:
        f.write(body)
    print(json.dumps({"output": str(out), "status": status,
                      "sha256": rs.sha256_file(out)}))
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())

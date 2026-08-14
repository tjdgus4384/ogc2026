#!/usr/bin/env python3
"""Regenerate tools/README.md from each probe's first docstring line."""
import ast, pathlib
rows = []
for p in sorted(pathlib.Path(__file__).parent.glob("*.py")):
    try:
        doc = ast.get_docstring(ast.parse(p.read_text())) or ""
        first = doc.strip().splitlines()[0] if doc.strip() else "(no docstring)"
    except Exception:
        first = "(unparseable)"
    rows.append(f"- `{p.name}` — {first}")
sb = len(list(pathlib.Path(__file__).parent.glob("*.sbatch")))
head = ["# tools/ index", "",
        "Auto-generated (regenerate: python3 tools/_index.py). One line per",
        "probe: the first docstring line. Full context lives in",
        "docs/CAMPAIGN_LEDGER.md; probes are kept for reproducibility even",
        "after their hypothesis dies -- the ledger records each verdict.", ""]
tail = ["", f"Plus {sb} .sbatch launchers (one per probe, same basename)."]
pathlib.Path(__file__).parent.joinpath("README.md").write_text(
    "\n".join(head + rows + tail) + "\n")
print(f"{len(rows)} scripts indexed")

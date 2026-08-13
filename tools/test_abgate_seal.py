"""Gateway-safe regression test for immutable abgate snapshots."""

import json
import pathlib
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

import abgate


with tempfile.TemporaryDirectory(prefix="abgate-seal-test-") as tmp:
    root = pathlib.Path(tmp)
    solver = root / "solver"
    checker = root / "checker"
    solver.mkdir()
    checker.mkdir()
    (solver / "myalgorithm.py").write_text("VALUE = 1\n")
    (solver / "sscore.fake.so").write_bytes(b"kernel")
    (checker / "utils.py").write_text("VALUE = 2\n")

    old_gates = abgate.GATES
    abgate.GATES = root / "gates"
    args = SimpleNamespace(
        tag="sealed", base=str(solver), cand=None, checker=str(checker),
        inst="4", seeds="7", tl=None, data="preliminary_test",
        per_cell=12, base_env="", cand_env="OGC_TEST=1", dry=False,
        partition="titan", concurrent=1,
    )
    fake = SimpleNamespace(returncode=0, stdout="Submitted batch job TEST\n",
                           stderr="")
    try:
        with patch.object(abgate.subprocess, "run", return_value=fake) as call:
            abgate.cmd_submit(args)
    finally:
        abgate.GATES = old_gates

    gate = root / "gates" / "sealed"
    manifest = json.loads((gate / "manifest.json").read_text())
    assert manifest["baseDir"] == manifest["candDir"]
    assert pathlib.Path(manifest["baseDir"]).is_relative_to(gate)
    assert abgate.tree_seal(manifest["baseDir"]) == manifest["seals"]["base"]
    assert abgate.tree_seal(manifest["harnessDir"]) == manifest["harnessSeal"]
    pair = manifest["pairs"][0]
    assert abgate.file_sha256(pair["probPath"]) == pair["probSha256"]
    assert call.call_args.args[0][-1] == str(gate / "harness" / "abgate.sbatch")

    # A copied harness can validate an empty cell without the fake solver being
    # importable.  Generated __pycache__ is deliberately absent from its seal.
    manifest["pairs"] = []
    (gate / "manifest.json").write_text(json.dumps(manifest))
    run = subprocess.run(
        [sys.executable, str(gate / "harness" / "abgate_cell.py"),
         str(gate), "0", sys.executable], capture_output=True, text=True)
    assert run.returncode == 0, (run.stdout, run.stderr)

    before = abgate.tree_seal(manifest["baseDir"])
    (pathlib.Path(manifest["baseDir"]) / "myalgorithm.py").write_text("VALUE = 3\n")
    assert abgate.tree_seal(manifest["baseDir"]) != before

print("ABGATE SEAL PASS: solver, checker, harness, and problem are pinned")

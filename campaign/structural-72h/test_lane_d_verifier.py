#!/usr/bin/env python3
"""Contract and negative-path tests for the independent Lane-D audit."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest

MODULE_PATH = Path(__file__).with_name("lane_d_verifier.py")
SPEC = importlib.util.spec_from_file_location("lane_d_verifier", MODULE_PATH)
assert SPEC and SPEC.loader
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)


class LaneDVerifierTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        protected = self.root / "protected.bin"
        protected.write_bytes(b"frozen-product")
        authority = "a" * 64
        rows = []
        for number in range(verifier.SCORE_ROWS):
            rows.append({
                "row_id": f"score-{number:03d}", "class": "score", "stage": "discovery",
                "candidate": f"M{number % 4 + 1}", "problem": f"P{number % 4 + 3}",
                "seed": (101, 103)[number % 2], "tl_seconds": 240,
                "order": ("AB", "BA")[number % 2],
                "source_sha256": "1" * 64, "build_sha256": "2" * 64,
                "runtime_sha256": "3" * 64, "result_sha256": "4" * 64,
            })
        for number in range(verifier.BACKGROUND_ROWS):
            rows.append({
                "row_id": f"background-{number:03d}", "class": "background",
                "stage": "build", "candidate": f"M{number % 4 + 1}",
                "problem": None, "seed": None, "tl_seconds": 300,
            })
        contract = {
            "schema_version": 1,
            "campaign_id": "structural-72h-test",
            "protected_files": {"protected.bin": hashlib.sha256(b"frozen-product").hexdigest()},
            "authorities": {name: authority for name in ("checker", "reducer", "score_table", "score_tool")},
            "resource_budget": dict(verifier.RESOURCE_BUDGET),
            "seed_groups": {"discovery": [101, 103], "holdout": [107, 109],
                            "m5": [149, 151, 157], "release": [7]},
            "active_observations": [{"active_total": 8, "active_evaluation": 6, "active_background": 2}],
            "rows": rows,
        }
        ledger = []
        for row in rows:
            ledger.append({**{key: row[key] for key in ("row_id", "class", "stage", "candidate",
                                                         "problem", "seed", "tl_seconds")},
                           **({"order": row["order"]} if "order" in row else {}),
                           "status": "NOT_ADMITTED", "attempts": 0, "nodes": 1,
                           "controller_admitted": True})
        self.bundle = {"phase": "H72", "contract": contract, "ledger": ledger,
                       "results": [], "sacct": [], "eligible_candidates": [],
                       "selected_candidate": None}
        self._seal(self.bundle)

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def _seal(bundle: dict) -> None:
        bundle.pop("bundle_sha256", None)
        bundle["bundle_sha256"] = verifier.digest(bundle)

    def _audit(self, bundle: dict | None = None) -> dict:
        return verifier.audit_bundle(self.root, bundle or self.bundle)

    def _complete_first_score(self, bundle: dict) -> None:
        plan = bundle["contract"]["rows"][0]
        ledger = bundle["ledger"][0]
        ledger.update(status="COMPLETED", attempts=1, job_id="9001")
        authority = bundle["contract"]["authorities"]
        common = {"node": "node01", "cpuset": "0-3", "seed": plan["seed"],
                  "tl_seconds": plan["tl_seconds"], "checker_valid": True, "objective": 100}
        result = {
            "row_id": plan["row_id"], "job_id": "9001", "seed": plan["seed"],
            "problem": plan["problem"], "tl_seconds": plan["tl_seconds"], "order": "AB",
            **{key: plan[key] for key in verifier.IDENTITIES},
            **{f"{key}_sha256": authority[key] for key in authority},
            "baseline": common, "treatment": {**common, "objective": 99},
        }
        bundle["results"] = [result]
        bundle["sacct"] = [{"job_id": "9001", "state": "COMPLETED", "nodes": 1}]

    def test_total_110_row_no_survivor_closure(self) -> None:
        report = self._audit()
        self.assertEqual("NO_SURVIVOR", report["verdict"])
        self.assertEqual(verifier.TOTAL_ROWS, report["ledger_rows"])
        self.assertEqual([], report["errors"])

    def test_valid_checker_matched_pair_and_sacct(self) -> None:
        bundle = copy.deepcopy(self.bundle)
        self._complete_first_score(bundle)
        self._seal(bundle)
        self.assertEqual("NO_SURVIVOR", self._audit(bundle)["verdict"])

    def test_duplicate_attempt_and_missing_result_fail_closed(self) -> None:
        bundle = copy.deepcopy(self.bundle)
        bundle["ledger"][0].update(status="FAILED", attempts=2, job_id="9001")
        bundle["sacct"] = [{"job_id": "9001", "state": "FAILED", "nodes": 1}]
        self._seal(bundle)
        codes = {error["code"] for error in self._audit(bundle)["errors"]}
        self.assertIn("DUPLICATE_ATTEMPT", codes)

    def test_seed_sets_are_disjoint(self) -> None:
        bundle = copy.deepcopy(self.bundle)
        bundle["contract"]["seed_groups"]["holdout"].append(101)
        self._seal(bundle)
        self.assertIn("SEED_OVERLAP", {e["code"] for e in self._audit(bundle)["errors"]})

    def test_unmatched_pair_or_invalid_checker_cannot_reduce(self) -> None:
        bundle = copy.deepcopy(self.bundle)
        self._complete_first_score(bundle)
        bundle["results"][0]["treatment"]["cpuset"] = "4-7"
        bundle["results"][0]["treatment"]["checker_valid"] = False
        self._seal(bundle)
        codes = {error["code"] for error in self._audit(bundle)["errors"]}
        self.assertTrue({"UNMATCHED_PAIR", "CHECKER_INVALID"}.issubset(codes))

    def test_ninth_active_or_multi_node_is_resource_invalid(self) -> None:
        bundle = copy.deepcopy(self.bundle)
        bundle["contract"]["active_observations"].append(
            {"active_total": 9, "active_evaluation": 6, "active_background": 2})
        bundle["ledger"][0]["nodes"] = 2
        self._seal(bundle)
        self.assertIn("RESOURCE_CAP_INVALID", {e["code"] for e in self._audit(bundle)["errors"]})

    def test_live_h72_job_has_external_blocked_precedence(self) -> None:
        bundle = copy.deepcopy(self.bundle)
        self._complete_first_score(bundle)
        bundle["sacct"][0]["state"] = "RUNNING"
        self._seal(bundle)
        self.assertEqual("EXTERNAL_BLOCKED_LIVE_JOB", self._audit(bundle)["verdict"])

    def test_protected_drift_and_bundle_tamper_are_detected(self) -> None:
        (self.root / "protected.bin").write_bytes(b"changed")
        self.bundle["selected_candidate"] = "unsealed-change"
        codes = {error["code"] for error in self._audit()["errors"]}
        self.assertTrue({"PROTECTED_DRIFT", "BUNDLE_HASH"}.issubset(codes))

    def test_exact_resource_accounting_constants(self) -> None:
        budget = verifier.RESOURCE_BUDGET
        self.assertEqual(113596, budget["score_node_wall_seconds"] + budget["background_node_wall_seconds"])
        self.assertEqual((8, 32, 112),
                         (budget["max_slurm_jobs"], budget["max_slurm_cores"], budget["max_slurm_gib"]))


if __name__ == "__main__":
    unittest.main()

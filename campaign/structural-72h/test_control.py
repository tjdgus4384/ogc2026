import importlib.util
from pathlib import Path
import tempfile
import unittest

MODULE_PATH = Path(__file__).with_name("control.py")
SPEC = importlib.util.spec_from_file_location("structural_control", MODULE_PATH)
control = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(control)


class LedgerTests(unittest.TestCase):
    def test_exact_maximum_ledger(self):
        ledger = control.maximum_ledger(1)
        score = [row for row in ledger["rows"] if row["class"] == "evaluation"]
        background = [row for row in ledger["rows"] if row["class"] == "background"]
        self.assertEqual((len(score), len(background), len(ledger["rows"])), (88, 22, 110))
        self.assertEqual(sum(row["wall_seconds"] for row in score), 88096)
        self.assertEqual(sum(row["wall_seconds"] for row in background), 25500)
        self.assertEqual(sum(row["wall_seconds"] for row in ledger["rows"]), 113596)

    def test_allocation_union_finds_stale_and_unauthorized(self):
        ledger = control.maximum_ledger(1)
        ledger["rows"][0].update(state="LEASED", attempts=1)
        observed = [{"job_id": "999", "state": "RUNNING", "nodes": 1,
                     "comment": ledger["campaign_uuid"], "class": "evaluation"}]
        union, faults = control.allocation_union(ledger, observed)
        self.assertEqual(len(union), 2)
        self.assertIn("UNAUTHORIZED", faults)


class ReducerTests(unittest.TestCase):
    @staticmethod
    def pairs(seeds, treatment):
        return [{"candidate": "M1", "problem": problem, "seed": seed,
                 "control": 100, "treatment": treatment, "causal_events": 1,
                 "score_delta": 2,
                 "strict_board": problem == "P3" and seed == seeds[0], "flags": []}
                for seed in seeds for problem in ("P3", "P4", "P5", "P6")]

    def test_discovery_exact_boundary_and_above(self):
        at = control.reduce_gate(self.pairs((107, 109), 98), "discovery")
        above = control.reduce_gate(self.pairs((107, 109), 99), "discovery")
        self.assertTrue(at["pass"])
        self.assertEqual(at["mean"], "-1/50")
        self.assertFalse(above["pass"])

    def test_holdout_and_invalid_precedence(self):
        passing = control.reduce_gate(self.pairs((127, 131, 137), 98), "holdout")
        self.assertTrue(passing["pass"])
        pairs = self.pairs((107, 109), 98)
        pairs[0]["flags"] = ["CHECKER_ERROR", "PROTECTED_DRIFT"]
        invalid = control.reduce_gate(pairs, "discovery")
        self.assertEqual(invalid["status"], "PROTECTED_DRIFT")

    def test_total_winner_order_and_cpu_nanosecond_tie(self):
        base = {"discovery_mean": "-1/20", "holdout_mean": "-1/25",
                "worst_improvement": "1/100"}
        candidates = [
            {**base, "id": "B", "allocated_core_ns": 101},
            {**base, "id": "A", "allocated_core_ns": 100},
            {**base, "id": "M5", "kind": "M5", "allocated_core_ns": 1,
             "parent_robust": ["1/50", "1/25"], "m5_mean": "-1/10"},
        ]
        ordered = control.winner_order(candidates)
        self.assertEqual([item["id"] for item in ordered], ["A", "B", "M5"])
        self.assertEqual(ordered[-1]["robust"], "1/50")


class NoveltyAndCapsTests(unittest.TestCase):
    def test_total_novelty_authority(self):
        witness = {key: "hash" for key in {
            "abi_hash", "source_hash", "f0_hash", "inventory_hash", "tool_hash",
            "closure_ledger_hash", "fixture_hash", "source_state", "destination_state",
            "exact_event_trace", "candidate_trace", "proper_intermediates", "rollback_hash", "off_hash"}}
        witness.update(mechanism="M1", changed_blocks=[1, 2],
                       changed_dimensions=["assignment", "coordinate"],
                       baseline_status="COMPLETE", baseline_cap_hit=False,
                       destination_absent=True, f0_audit="PASS", utils_result="PASS",
                       closure_rows=[{"id": "large-ruin", "status": "DIFFERENTIATED"}])
        self.assertEqual(control.validate_novelty(witness), [])
        witness["baseline_status"] = "CAPPED"
        self.assertIn("BASELINE_NOT_COMPLETE", control.validate_novelty(witness))

    def test_cap_and_cap_plus_one(self):
        limit = control.contract()["caps"]["artifact_index_rows"]
        self.assertEqual(control.cap_check({"artifact_index_rows": limit}), [])
        self.assertEqual(control.cap_check({"artifact_index_rows": limit + 1}),
                         ["CAP_EXCEEDED:artifact_index_rows"])


class AdmissionAndClosureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.old = (control.ROOT, control.LIVE_PATH, control.LOCK_PATH,
                    control.verify_protected)
        control.ROOT = Path(self.temporary.name)
        control.LIVE_PATH = control.ROOT / "ledger.live.json"
        control.LOCK_PATH = control.ROOT / ".controller.lock"
        control.verify_protected = lambda _base: []

    def tearDown(self):
        control.ROOT, control.LIVE_PATH, control.LOCK_PATH, control.verify_protected = self.old
        self.temporary.cleanup()

    def test_h0_drift_fails_closed(self):
        control.verify_protected = lambda _base: ["PROTECTED_DRIFT"]
        with control.Controller() as controller:
            ledger = controller.initialize(0)
            self.assertTrue(ledger["promotion_disabled"])
            row_id = ledger["rows"][0]["id"]
            with self.assertRaisesRegex(RuntimeError, "campaign fenced"):
                controller.admit(row_id, Path("job.sbatch"), [], 1, True)
    def test_seven_to_eight_and_ninth_blocked(self):
        with control.Controller() as controller:
            ledger = controller.initialize(0)
            selected = ([row for row in ledger["rows"] if row["class"] == "evaluation"][:5] +
                        [row for row in ledger["rows"] if row["class"] == "background"][:2])
            observed = []
            for index, row in enumerate(selected):
                row.update(state="RUNNING", attempts=1, job_id=str(100 + index))
                observed.append({"job_id": str(100 + index), "state": "RUNNING",
                                 "nodes": 1, "class": row["class"]})
            controller.save(ledger)
            eighth_id = [row for row in ledger["rows"] if row["class"] == "evaluation" and row["state"] == "UNCLAIMED"][0]["id"]
            eighth = controller.admit(eighth_id, Path("job.sbatch"), observed, 1, True)
            self.assertEqual(eighth["state"], "LEASED")
            ninth_id = [row for row in controller.load()["rows"] if row["state"] == "UNCLAIMED"][0]["id"]
            with self.assertRaisesRegex(RuntimeError, "admission denied"):
                controller.admit(ninth_id, Path("job.sbatch"), observed, 2, True)

    def test_h70_h71_h72_total_closure(self):
        with control.Controller() as controller:
            ledger = controller.initialize(0)
            ledger["rows"][0].update(state="RUNNING", attempts=1, job_id="77")
            controller.save(ledger)
            observed = [{"job_id": "77", "state": "RUNNING", "nodes": 1,
                         "class": "evaluation"}]
            at_h70 = controller.close(observed, 70 * 3600 * 1_000_000_000, False)
            self.assertTrue(at_h70["fenced"])
            at_h72 = controller.close(observed, 72 * 3600 * 1_000_000_000, False)
            self.assertIn("LIVE_H72_ALLOCATION", at_h72["faults"])
            self.assertTrue(at_h72["promotion_disabled"])
            self.assertTrue(all(row["state"] in control.TERMINAL for row in at_h72["rows"]))


if __name__ == "__main__":
    unittest.main()

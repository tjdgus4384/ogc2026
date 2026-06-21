# myalgorithm.py -- OGC 2026 submission entry point.
#
# Pipeline:
#   FLOOR (bulletproof) : solve.serialize_floor -> ALWAYS feasible (single occupancy). The -1 insurance.
#   FOOTPRINT FLOOR     : footprint-disjoint greedy (better start) -- adopted only if feasible.
#   MAIN ENGINE         : anytime.solve_anytime -- budget-independent EPOCH ALNS + CP-SAT repair, single
#                         deep run with cp_workers=4 (CP repair depth dominates seed count, measured).
#                         Uses the FULL time budget; no mp dependency (CP-SAT uses threads -> firejail-safe).
#   GUARD (final)       : never return an infeasible/empty solution -- re-validate, fall back to the floor.
#
# Robustness lessons from hidden P3 (was infeasible): _sanitize forces processing_time>=1 (a P=0 block
# makes EXIT==ENTRY which the checker mishandles); start from a structurally-always-feasible floor; only
# replace `best` with FEASIBLE candidates; re-validate before returning. Budget = ~88% of the limit.

import time


def _sanitize(prob_info):
    """Return a working copy with processing_time >= 1. A P=0 block forces EXIT==ENTRY (same time point),
    which the operations format / checker mishandles (EXIT sorted before its own ENTRY -> 'no EXIT
    operation'). Internal P=max(1,P) makes EXIT strictly after ENTRY; valid for the original P
    (exit-entry = max(1,P) >= P). Defends against the P3-style infeasibility."""
    import copy
    need = any(b.get("processing_time", 1) < 1 for b in prob_info.get("blocks", []))
    if not need:
        return prob_info
    p = copy.deepcopy(prob_info)
    for b in p["blocks"]:
        if b.get("processing_time", 1) < 1:
            b["processing_time"] = 1
    return p


def algorithm(prob_info, timelimit=60):
    import utils
    import solve

    t0 = time.time()
    work = timelimit * 0.80
    orig = prob_info                       # the server validates against THIS (original P, incl P=0)
    prob_info = _sanitize(prob_info)       # internal copy with P>=1 (EXIT strictly after ENTRY)

    # -- bulletproof always-feasible floor (the -1 insurance) -------------------
    try:
        best = solve.serialize_floor(prob_info)
        r = utils.check_feasibility(prob_info, best)
        best_obj = r["objective"] if r["feasible"] else float("inf")
    except Exception:
        best, best_obj = {"operations": {}}, float("inf")

    # -- footprint-disjoint greedy (better start), adopt only if feasible -------
    try:
        c = solve.solve(prob_info, "pref")
        r = utils.check_feasibility(prob_info, c)
        if r["feasible"] and r["objective"] < best_obj:
            best, best_obj = c, r["objective"]
    except Exception:
        pass

    # -- HARD DEADLINE: a fast feasible `best` (the floor) is already in hand. Guard the EXPENSIVE stages
    # with a SIGALRM so that NO slow stage (large bay, huge n, congested combo) can run past the limit and
    # get the process KILLED before we return -- the reproduced P3 failure mode. _Deadline subclasses
    # BaseException so the inner `except Exception` blocks can't swallow it. -----------------------------
    class _Deadline(BaseException):
        pass

    def _on_alarm(signum, frame):
        raise _Deadline()

    remaining = work - (time.time() - t0)
    if remaining > 8:
        # SIZE-ADAPTIVE budgeting. Small bays (CP-SAT-tractable) -> the EXACT path (anytime seed ->
        # cpsat_polish drives Z1->0 -> cpsat_z3 descends Z3) is what cracks them to optimum (prob_4 33112 <
        # best 39343); BRKGA is minimized there. Large bays -> BRKGA-heavy matheuristic (CP-SAT can't pack).
        cpsat_useful = (len(prob_info["blocks"]) / max(1, len(prob_info["bays"]))) <= 52
        any_t = max(8.0, remaining * (0.30 if cpsat_useful else 0.40))   # anytime SEED budget

        # anytime is now ADAPTIVE: it internally detects improve-favored (low-congestion) instances after
        # its construct and continues a CP-free improve there (capturing the wins a parallel improve member
        # would, but WITHOUT stealing cores -> no congested regression). So no separate improve child.
        armed = False; _old = None
        try:
            import signal
            _old = signal.signal(signal.SIGALRM, _on_alarm)
            signal.setitimer(signal.ITIMER_REAL, max(1.0, timelimit * 0.90 - (time.time() - t0)))
            armed = True
        except Exception:
            armed = False
        try:
            # ============================ UNIFIED ENGINE: seeded BRKGA ============================
            # The problem = set-partitioning (assignment fixes Z2/Z3 & bounds Z1; per-bay packing fixes Z1).
            # Single-basin search gets stuck; population recombination escapes it. So: build a few diverse
            # SEED assignments (each a strong method in its own right), then let BRKGA recombine them over a
            # FAST compiled decoder, beam-polishing the elite. Every method is a seed; one frame. best-of +
            # seed_sols safety => the engine can never do worse than the best individual seed.
            def _asg(s):
                a = {}
                for tt, ops in s["operations"].items():
                    for o in ops:
                        if o["type"] == "ENTRY":
                            a[o["block_id"]] = o["bay_id"]
                return a
            seeds_asg = []; seed_sols = []

            # SEED 1 -- anytime (ALNS + CP repair): the congested workhorse.
            try:
                import anytime
                sol = anytime.solve_anytime(prob_info, any_t, seed=0, cp_workers=4)
                r = utils.check_feasibility(prob_info, sol)
                if r["feasible"]:
                    seed_sols.append(sol); seeds_asg.append(_asg(sol))
                    if r["objective"] < best_obj:
                        best, best_obj = sol, r["objective"]
            except Exception:
                pass
            # SEED 2 -- construct_z3 under SEVERAL block-orderings (preference-first -> low Z3, different
            # basins anytime misses). Each ordering samples a different column universe (no tuning).
            try:
                import assign_lns
                cseeds = assign_lns.construct_seeds(
                    prob_info, utils, time_budget=min(8.0, max(0.0, timelimit * 0.6 - (time.time() - t0))),
                    orders=("sharp", "due", "workload"))  # time-checks between orders: small gets all 3,
                #                                            large gets fewer -> protects congested budget
                for cz in cseeds:
                    r = utils.check_feasibility(prob_info, cz)
                    if r["feasible"]:
                        seed_sols.append(cz); seeds_asg.append(_asg(cz))
                        if r["objective"] < best_obj:
                            best, best_obj = cz, r["objective"]
            except Exception:
                pass
            # SEED 3 -- exact ILP assignment (Z1=0 instances: pure Z2/Z3, CP-SAT optimal). Keeps small/low-
            # congestion instances (prob_1/2) at their ILP quality inside the population.
            try:
                rb = utils.check_feasibility(prob_info, best)
                ilp_avail = timelimit * 0.62 - (time.time() - t0)
                if rb.get("feasible") and rb.get("obj1", 1) == 0 and ilp_avail > 8.0:
                    asg = solve.ilp_assign(prob_info, tl=min(12.0, ilp_avail * 0.4))
                    if asg is not None:
                        import improve
                        isol = improve.improve(prob_info, max(5.0, ilp_avail * 0.4),
                                               master_assign=asg, fix_assign=True)
                        r = utils.check_feasibility(prob_info, isol)
                        if r["feasible"]:
                            seed_sols.append(isol); seeds_asg.append(_asg(isol))
                            if r["objective"] < best_obj:
                                best, best_obj = isol, r["objective"]
            except Exception:
                pass
            # UNIFIER -- BRKGA over (assignment, per-bay order), seeded by all of the above, elite beam-polish.
            # Small bays: BRKGA is MINIMIZED (_bf=0.42) -> the exact cpsat_polish+cpsat_z3 path gets the budget
            # (it solves small instances to optimum). Large bays: BRKGA-heavy (_bf=0.90, cpsat skipped).
            try:
                import brkga2
                brk_t = timelimit * 0.90 - (time.time() - t0) - 3.0
                # SMALL bays: skip BRKGA entirely -> the exact path (cpsat_polish + cpsat_z3) gets the whole
                # budget (it solves them to/below best; BRKGA's beam base just wastes small-instance time).
                if brk_t > 8.0 and seed_sols and not cpsat_useful:
                    bsol = brkga2.solve_brkga2(prob_info, brk_t, seeds=seeds_asg, seed_sols=seed_sols,
                                               workers=4, beam_final=True)
                    r = utils.check_feasibility(prob_info, bsol)
                    if r["feasible"] and r["objective"] < best_obj:
                        best, best_obj = bsol, r["objective"]
            except Exception:
                pass
            # EXACT per-bay re-pack (CP-SAT): beam under-packs (oracle gap); CP-SAT eliminates bay tardiness
            # beam leaves (prob_4 bay 33->0, prob_24 -29% Z1). The dominant Z1 lever on the real long limits;
            # scales with budget (~10-20s/bay), best-of, same assignment -> Z2/Z3 fixed, never regresses.
            try:
                import cpsat_polish
                cp_avail = timelimit * 0.66 - (time.time() - t0)
                if cpsat_useful and cp_avail > 10.0:
                    cpp = cpsat_polish.cpsat_polish(prob_info, best, utils,
                                                    time_budget=cp_avail, per_bay_tl=min(30.0, cp_avail * 0.5))
                    r = utils.check_feasibility(prob_info, cpp)
                    if r["feasible"] and r["objective"] < best_obj:
                        best, best_obj = cpp, r["objective"]
            except Exception:
                pass
            # CP-SAT-oracle Z3 DESCENT (the integrated assignment<->per-bay lever): from the Z1=0 packing,
            # move blocks toward preferred bays (relocate + SWAP) while CP-SAT certifies Z1 stays 0 -> finds
            # the moderate-Z3 + packable sweet spot heuristics miss (prob_3 -> 0.80x, prob_24 -> 0.96x, BEATS
            # overnight best-known). Same gate (CP-SAT-tractable bays); best-of -> can't regress.
            try:
                import cpsat_z3
                z3_avail = timelimit * 0.92 - (time.time() - t0)
                if cpsat_useful and z3_avail > 12.0:
                    zz = cpsat_z3.cpsat_z3(prob_info, best, utils,
                                           time_budget=z3_avail, per_bay_tl=min(20.0, z3_avail * 0.25))
                    r = utils.check_feasibility(prob_info, zz)
                    if r["feasible"] and r["objective"] < best_obj:
                        best, best_obj = zz, r["objective"]
            except Exception:
                pass
            # final polish (cheap best-of): placement-rule swap (contact-max anti-fragmentation, helps
            # congested bays) then polygon density. Both per-bay, Z2/Z3 fixed, best-of -> can't regress.
            try:
                import assign_lns
                pp = assign_lns.placement_polish(prob_info, best, utils,
                                                 time_budget=max(2.0, timelimit * 0.93 - (time.time() - t0)))
                r = utils.check_feasibility(prob_info, pp)
                if r["feasible"] and r["objective"] < best_obj:
                    best, best_obj = pp, r["objective"]
            except Exception:
                pass
            try:
                import polypack
                pol = polypack.polish_solution(prob_info, best, utils,
                                               time_budget=max(3.0, timelimit * 0.97 - (time.time() - t0)))
                r = utils.check_feasibility(prob_info, pol)
                if r["feasible"] and r["objective"] < best_obj:
                    best, best_obj = pol, r["objective"]
            except Exception:
                pass
        except _Deadline:
            pass                                            # hard deadline hit -> stop, return best so far
        finally:
            if armed:
                try:
                    signal.setitimer(signal.ITIMER_REAL, 0)
                    signal.signal(signal.SIGALRM, _old)
                except Exception:
                    pass

    # -- final GUARD: never return an infeasible/empty solution. Validate against the ORIGINAL prob
    # (what the server checks), not the sanitized copy -- this is the P3 -1 insurance. The sanitized
    # serialize_floor (EXIT=ENTRY+max(1,P)) is feasible under the original P (incl P=0). ----------
    def _ok(sol):
        try:
            return utils.check_feasibility(orig, sol)["feasible"]
        except Exception:
            return False
    if not _ok(best):
        try:
            best = solve.serialize_floor(prob_info)        # sanitized floor: EXIT strictly after ENTRY
        except Exception:
            best = {"operations": {}}
        if not _ok(best):                                  # last resort: floor on original
            try:
                best = solve.serialize_floor(orig)
            except Exception:
                pass
    return best

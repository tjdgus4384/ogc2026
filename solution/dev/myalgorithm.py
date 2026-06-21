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
import os as _os

_TR = _os.environ.get("OGC_TRACE")


def _tr(label, prob, sol, utils, t0, best_obj):
    """env-guarded stage trace: each stage's produced obj + Z-breakdown + running best."""
    if not _TR:
        return
    try:
        r = utils.check_feasibility(prob, sol)
        o = f"{r['objective']:.0f}" if r["feasible"] else "INF"
        print(f"[trace] {label:14} obj={o:>10} Z1={r.get('obj1')} Z2={r.get('obj2')} Z3={r.get('obj3')} "
              f"| best={best_obj:.0f} t={time.time()-t0:.0f}s", flush=True)
    except Exception as e:
        print(f"[trace] {label}: {e}", flush=True)


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
            seeds_asg = []; seed_sols = []; construct_sols = []

            # SEED 1 -- anytime (ALNS + CP repair): the congested workhorse. SMALL instances run it MULTI-SEED:
            # whether an assignment is Z1=0-PACKABLE is a stochastic draw (per-bay packing is fast, ~5s, but
            # a single seed can land on a Z1_min>0 split -- prob_4 single-seed stalled at Z1=2 while 4/5 seeds
            # are Z1=0-packable). A few diverse seeds reliably yield a Z1=0-packable assignment to polish from.
            try:
                import anytime
                # seed 0 at FULL budget (strong single run -- instances that need a good anytime, e.g. prob_24);
                # SMALL adds a half-budget seed 1 purely for assignment DIVERSITY (a second Z1=0-packable draw
                # for dense instances like prob_4) without weakening seed 0.
                _budgets = [any_t, any_t * 0.5] if cpsat_useful else [any_t]
                for _sd, _b in enumerate(_budgets):
                    sol = anytime.solve_anytime(prob_info, _b, seed=_sd, cp_workers=4)
                    r = utils.check_feasibility(prob_info, sol)
                    if r["feasible"]:
                        seed_sols.append(sol); seeds_asg.append(_asg(sol))
                        if r["objective"] < best_obj:
                            best, best_obj = sol, r["objective"]
                    _tr(f"anytime[{_sd}]", prob_info, sol, utils, t0, best_obj)
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
                for ci, cz in enumerate(cseeds):
                    r = utils.check_feasibility(prob_info, cz)
                    if r["feasible"]:
                        seed_sols.append(cz); seeds_asg.append(_asg(cz)); construct_sols.append(cz)
                        if r["objective"] < best_obj:
                            best, best_obj = cz, r["objective"]
                    _tr(f"construct[{ci}]", prob_info, cz, utils, t0, best_obj)
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
                            _tr("ilp", prob_info, isol, utils, t0, best_obj)
            except Exception:
                pass
            # UNIFIER -- BRKGA over (assignment, per-bay order), seeded by all of the above, elite beam-polish.
            # Small bays: BRKGA is MINIMIZED (_bf=0.42) -> the exact cpsat_polish+cpsat_z3 path gets the budget
            # (it solves small instances to optimum). Large bays: BRKGA-heavy (_bf=0.90, cpsat skipped).
            try:
                import brkga2
                brk_t = timelimit * 0.85 - (time.time() - t0) - 3.0
                # SMALL bays: skip BRKGA entirely -> the exact path (cpsat_polish + cpsat_z3) gets the whole
                # budget (it solves them to/below best; BRKGA's beam base just wastes small-instance time).
                if brk_t > 8.0 and seed_sols and not cpsat_useful:
                    bsol = brkga2.solve_brkga2(prob_info, brk_t, seeds=seeds_asg, seed_sols=seed_sols,
                                               workers=4, beam_final=True)
                    r = utils.check_feasibility(prob_info, bsol)
                    if r["feasible"] and r["objective"] < best_obj:
                        best, best_obj = bsol, r["objective"]
                    # ASSIGNMENT polish on the brkga2 result (LARGE path). brkga2 selects assignments by its
                    # greedy decoder's Z1 -> it leaves Z2/Z3 noisy/high. asls descends Z2+Z3 by Z1-preserving
                    # insertion; LARGE multi-bay instances have room for it to relocate blocks to preferred
                    # bays, so it cuts a lot (prob_20 535k->257k, prob_17 98k->77k -- both beat best) and it
                    # absorbs brkga2's assignment variance. Fast (~1s), best-of -> can't regress.
                    try:
                        import asls as _asls_l
                        aa = _asls_l.asls(prob_info, best, utils, time_budget=max(2.0, timelimit * 0.88 - (time.time() - t0)))
                        r = utils.check_feasibility(prob_info, aa)
                        if r["feasible"] and r["objective"] < best_obj:
                            best, best_obj = aa, r["objective"]
                        _tr("asls-large", prob_info, aa, utils, t0, best_obj)
                    except Exception:
                        pass
            except Exception:
                pass
            # SMALL-bay UNIFIED exact polish. Per-bay CP-SAT packing is FAST (~1-5s/bay even at 50 blocks), so
            # whether Z1=0 is reached is about the ASSIGNMENT (which blocks share a bay), not packing speed. A
            # single seed can draw a Z1_min>0 split (prob_4 stalled at Z1=2); other seeds are Z1=0-packable.
            # So: take every distinct candidate assignment, cpsat_polish the Z1>0 ones to Z1=0 (cheap; the
            # already-Z1=0 ones cost nothing), collect the Z1=0 set, then run cpsat_z3 (Z3 descent) from the
            # LOWEST-Z3 Z1=0 candidate (construct basin for prob_3; a packable anytime split for prob_4), then
            # asls (Z2). One rule, both instances. best-of throughout -> can't regress.
            try:
                import cpsat_polish, cpsat_z3, asls as _asls

                def _ck(s):
                    return utils.check_feasibility(prob_info, s)
                if cpsat_useful and seed_sols and (timelimit * 0.82 - (time.time() - t0)) > 8.0:
                    construct_sigs = {tuple(sorted(_asg(cz).items())) for cz in construct_sols}
                    z10 = []                                          # (Z3, sol, is_construct) Z1=0 candidates
                    seen = set(); n_polished = 0
                    for cs in sorted(seed_sols, key=lambda s: _ck(s).get("objective", 1e18)):
                        rc = _ck(cs)
                        if not rc["feasible"]:
                            continue
                        sig = tuple(sorted(_asg(cs).items()))
                        if sig in seen:
                            continue
                        seen.add(sig)
                        if rc.get("obj1", 1) == 0:
                            z10.append((rc.get("obj3", 0), cs, sig in construct_sigs))   # already Z1=0 -> free
                        elif not z10 and n_polished < 3 and time.time() - t0 < timelimit * 0.62:
                            # only spend packing time when NO Z1=0 base exists yet (don't waste it on the
                            # unpackable ILP seed when construct already gave a Z1=0 base -- that cost prob_3).
                            # per-bay packing is ~1-5s, so a tight per_bay_tl lets several candidates be tried
                            # -> a Z1=0-packable assignment is found reliably (prob_4 is a stochastic draw).
                            n_polished += 1
                            cpp = cpsat_polish.cpsat_polish(prob_info, cs, utils,
                                                            time_budget=min(14.0, timelimit * 0.62 - (time.time() - t0)),
                                                            per_bay_tl=8.0)
                            rp = _ck(cpp)
                            if rp["feasible"]:
                                if rp["objective"] < best_obj:
                                    best, best_obj = cpp, rp["objective"]
                                if rp.get("obj1", 1) == 0:
                                    z10.append((rp.get("obj3", 0), cpp, sig in construct_sigs))
                            _tr(f"polish[{sig and n_polished}]", prob_info, cpp, utils, t0, best_obj)
                    # PREFER the construct basin: it is pref-sharp, so cpsat_z3 descends Z3 far deeper from it
                    # (prob_3 -> Z3 67 vs the anytime basin's 191) even when its START Z3 is a touch higher.
                    # Among Z1=0 candidates, pick the lowest-Z3 CONSTRUCT one; else the lowest-Z3 overall.
                    # ROUTE by Z1=0-reachability (the z10 set). This is the key regime split, not a tuned param:
                    if not z10:
                        # Z1-BOUND (no Z1=0-packable assignment, e.g. prob_24/34): the cpsat path can't search
                        # assignments well (cpsat_z3's per-move CP-SAT is too slow) so it stalls at the seed's
                        # Z1. brkga2's POPULATION assignment search over a fast decoder finds far better
                        # assignments (prob_24 3.1M->2.05M, prob_34 3.9M->2.58M). best-of -> can't regress.
                        try:
                            import brkga2
                            bt = timelimit * 0.80 - (time.time() - t0) - 3.0
                            if bt > 8.0 and seeds_asg:
                                bs = brkga2.solve_brkga2(prob_info, bt, seeds=seeds_asg, seed_sols=seed_sols,
                                                         workers=4, beam_final=True)
                                r = _ck(bs)
                                if r["feasible"] and r["objective"] < best_obj:
                                    best, best_obj = bs, r["objective"]
                                _tr("brkga2-bound", prob_info, bs, utils, t0, best_obj)
                        except Exception:
                            pass
                    else:
                        # Z1=0-REACHABLE: cpsat_z3 (Z3 descent) + asls (Z2). PREFER the construct basin (pref-
                        # sharp -> descends to Z3~67 vs anytime's ~191; one deep deterministic run -> prob_3
                        # stable 43250). No construct (prob_4): descent is variable, so best-of two deep
                        # attempts when budget allows (different low-Z3 base if any, else re-run -- CP-SAT non-det).
                        _cz = [e for e in z10 if e[2]]
                        if _cz:
                            bases = [min(_cz, key=lambda x: x[0])[1]]
                        else:
                            srt = [e[1] for e in sorted(z10, key=lambda x: x[0])]
                            deep = (timelimit * 0.82 - (time.time() - t0)) > 60.0
                            bases = (srt[:2] if len(srt) >= 2 else srt * 2) if deep else srt[:1]
                        per = (timelimit * 0.82 - (time.time() - t0)) / max(1, len(bases))
                        for zb in bases:
                            if timelimit * 0.82 - (time.time() - t0) < 6.0:
                                break
                            zz = cpsat_z3.cpsat_z3(prob_info, zb, utils,
                                                   time_budget=per, per_bay_tl=min(20.0, per * 0.25))
                            r = _ck(zz)
                            if r["feasible"] and r["objective"] < best_obj:
                                best, best_obj = zz, r["objective"]
                            _tr("cpsat_z3", prob_info, zz, utils, t0, best_obj)
                        aa = _asls.asls(prob_info, best, utils, time_budget=3.0)
                        r = _ck(aa)
                        if r["feasible"] and r["objective"] < best_obj:
                            best, best_obj = aa, r["objective"]
                        _tr("asls", prob_info, aa, utils, t0, best_obj)
            except Exception:
                pass
            # final polish -- SMALL bays ONLY (cpsat_useful). For LARGE instances these were (a) low-value
            # (per-bay Z2/Z3-fixed polish on already-brkga2-decoded bays) and (b) the dominant TIMEOUT risk:
            # polypack runs Shapely per bay near the deadline (0.97T originally), an uninterruptible C path
            # that can overrun the hard limit on a big bay -> process killed -> infeasible (the P3 mode). So on
            # large we end at brkga2+beam_final (internally time-bounded) and give brkga2 that freed budget.
            if cpsat_useful:
                try:
                    import assign_lns
                    pp = assign_lns.placement_polish(prob_info, best, utils,
                                                     time_budget=max(2.0, timelimit * 0.80 - (time.time() - t0)))
                    r = utils.check_feasibility(prob_info, pp)
                    if r["feasible"] and r["objective"] < best_obj:
                        best, best_obj = pp, r["objective"]
                except Exception:
                    pass
                try:
                    import polypack
                    pol = polypack.polish_solution(prob_info, best, utils,
                                                   time_budget=max(3.0, timelimit * 0.83 - (time.time() - t0)))
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

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
import sys as _sys

_TR = _os.environ.get("OGC_TRACE")
_DIAG = _os.environ.get("OGC_DIAG")    # set OGC_DIAG=1 to print one stderr diagnostic line per instance


def _dbg(msg):
    """P3 diagnostic -> STDERR (the grader is far more likely to capture stderr than stdout). Env-gated so
    it is silent in normal runs. Lets us see, on the hidden P3, WHY it floors: instance size, whether the
    pre-deadline solve.solve produced a feasible better-than-floor pack, the final obj, and wall times."""
    if not _DIAG:
        return
    try:
        print(f"[OGC_DIAG] {msg}", file=_sys.stderr, flush=True)
    except Exception:
        pass


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
    # FUNDAMENTAL time-limit guarantee: the optimization runs in a DAEMON thread; this main thread returns
    # the best-so-far at a hard wall-clock deadline regardless of what the worker is doing. A SIGALRM cannot
    # interrupt an in-flight C call (CP-SAT, Shapely, mp.Pool), so it could overrun and the grader would kill
    # the process before any output -> infeasible (the P3 mode). A thread join CANNOT overrun: we abandon the
    # worker (daemon) and return whatever it has committed to the shared state, which is at worst the floor.
    import utils, solve, threading, signal
    t0 = time.time()
    san = _sanitize(prob_info)
    S = {"best": {"operations": {}}, "obj": float("inf")}
    lock = threading.Lock()
    try:
        _nb = len(prob_info.get("bays", [])); _nbk = len(prob_info.get("blocks", []))
        _mxc = max((int(b["width"]) * int(b["height"]) for b in prob_info.get("bays", [])), default=0)
        _dbg(f"START bays={_nb} blocks={_nbk} maxcells={_mxc} cpsat_useful={_nbk/max(1,_nb)<=52} TL={timelimit}")
    except Exception:
        pass
    try:                                   # synchronous always-feasible floor FIRST (the -1 insurance)
        f = solve.serialize_floor(san)
        r = utils.check_feasibility(san, f)
        if r["feasible"]:
            S["best"], S["obj"] = f, r["objective"]
        _dbg(f"floor feas={r['feasible']} obj={r.get('objective')} t={time.time()-t0:.1f}s")
    except Exception as _e:
        _dbg(f"floor EXC {_e}")
    # PARALLEL footprint-disjoint greedy BEFORE arming the deadline -- the PROVEN 1422 structure (1422 ran
    # solve.solve before its SIGALRM; my refactor had moved it INSIDE the guard). pack_bay is bay-size-
    # INDEPENDENT (extreme-point, O(n^2) in block count, NOT bay area), so it completes fast even on a large
    # bay + a throttled grader (cpulimit 400% / firejail) and is NOT at risk of being SIGALRM-cut. Committing
    # it HERE guarantees the returned best is a good PARALLEL pack (low Z1), never the single-occupancy floor,
    # even if the deadline forcibly stops every expensive stage below. THE P3 large-bay near-floor fix:
    # on a slow grader solve.solve inside the guard could be cut -> floor only (227M); here it always lands.
    try:
        _ct = time.time()
        c0 = solve.solve(san, "best")
        r0 = utils.check_feasibility(san, c0)
        if r0["feasible"] and r0["objective"] < S["obj"]:
            S["best"], S["obj"] = c0, r0["objective"]
        _dbg(f"solve.solve feas={r0['feasible']} obj={r0.get('objective')} packwall={time.time()-_ct:.1f}s t={time.time()-t0:.1f}s")
    except Exception as _e:
        _dbg(f"solve.solve EXC {_e}")
    # HARD DEADLINE -- the PROVEN P3 fix (submission 1422 turned P3 −1 -> feasible). Run the stages on the
    # MAIN thread under a SIGALRM that raises _Deadline at 0.90T. _Deadline subclasses BaseException so the
    # stages' `except Exception` blocks CANNOT swallow it -> a runaway Python stage (large bay / huge n) is
    # forcibly stopped and we return the best-so-far -> the process is never killed for overrun. The 60000-
    # cell packer caps keep every C call short, so SIGALRM (which can't interrupt an in-flight C call) fires
    # promptly after each. Fall back to a daemon-thread join if SIGALRM can't arm (not on the main thread).
    class _Deadline(BaseException):
        pass

    def _on_alarm(signum, frame):
        raise _Deadline()
    dl = max(0.5, timelimit * 0.90 - (time.time() - t0))
    armed = False; _old = None
    try:
        _old = signal.signal(signal.SIGALRM, _on_alarm)
        signal.setitimer(signal.ITIMER_REAL, dl); armed = True
    except Exception:
        armed = False
    if armed:
        try:
            _impl(prob_info, timelimit, t0, S, lock)       # MAIN thread, SIGALRM-guarded
        except _Deadline:
            pass
        except Exception:
            pass
        finally:
            try:
                signal.setitimer(signal.ITIMER_REAL, 0); signal.signal(signal.SIGALRM, _old)
            except Exception:
                pass
    else:                                                  # off-main-thread: daemon join (also returns at dl)
        th = threading.Thread(target=_impl, args=(prob_info, timelimit, t0, S, lock), daemon=True)
        th.start(); th.join(timeout=dl)
    with lock:
        best = S["best"]
        _pool = S.get("pool")
    if _pool is not None:                  # terminate any mp.Pool (brkga) left running -> no orphan past return
        try:
            _pool.terminate(); _pool.join()
        except Exception:
            pass

    def _ok(sol):                          # final GUARD: never return infeasible (validate vs ORIGINAL prob)
        try:
            return utils.check_feasibility(prob_info, sol)["feasible"]
        except Exception:
            return False
    if not _ok(best):
        try:
            fl = solve.serialize_floor(san)
            best = fl if _ok(fl) else best
        except Exception:
            pass
        if not _ok(best):
            try:
                best = solve.serialize_floor(prob_info)
            except Exception:
                pass
    try:
        _rf = utils.check_feasibility(prob_info, best)
        _dbg(f"FINAL feas={_rf['feasible']} obj={_rf.get('objective')} wall={time.time()-t0:.1f}s TL={timelimit}")
    except Exception:
        pass
    return best


def _impl(prob_info, timelimit, t0, S, lock):
    import utils
    import solve

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

    def _sync():                           # publish best-so-far to the shared state the main thread returns
        with lock:
            if best_obj < S["obj"]:
                S["best"], S["obj"] = best, best_obj

    def _take(sol, r):                     # adopt an improvement AND publish it the same instant.
        # The main thread abandons this worker at the wall deadline; if an improvement is held locally until
        # a later stage-end _sync(), an abandon in between LOSES it -> a longer run can return a STALE earlier
        # value (the prob_4 non-monotonicity: cpsat_z3 found 33957 but the run returned the pre-cpsat 74143).
        # Publishing on every improvement makes S["best"] always the true best-so-far -> abandon-safe at any
        # instant -> monotone in budget.
        nonlocal best, best_obj
        if r.get("feasible") and r["objective"] < best_obj:
            best, best_obj = sol, r["objective"]
            _sync()
            return True
        return False
    _sync()

    # -- footprint-disjoint greedy (better start), adopt only if feasible -------
    try:
        c = solve.solve(prob_info, "pref")
        r = utils.check_feasibility(prob_info, c)
        _take(c, r)
    except Exception:
        pass
    _sync()

    # The expensive stages below run inside this daemon worker; the main thread enforces the wall-clock
    # deadline by abandoning us, so no SIGALRM is needed (and signals don't work off the main thread anyway).
    # Each stage calls _sync() so the main thread always has the latest committed best when it returns.
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
                        _take(sol, r)
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
                        _take(cz, r)
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
                            _take(isol, r)
                            _tr("ilp", prob_info, isol, utils, t0, best_obj)
            except Exception:
                pass
            # CPSAT-PATH GEOM FLOOR. The crane-aware geom floor lives inside the `not cpsat_useful` branch below,
            # so SMALL-bay instances (cpsat_useful) never got it -- and cpsat_polish is crane-BLIND, so a small
            # but CONGESTED instance (prob_24) was left with only a budget-starved final polish (+11.7%). Run the
            # floor here for the cpsat path ONLY (non-cpsat keeps its in-branch floor unchanged -> prob_20 etc.
            # untouched). Self-regulating: returns fast if the seeds already pack to Z1=0.
            if cpsat_useful:
                try:
                    if seed_sols and (timelimit * 0.55 - (time.time() - t0)) > 4.0:
                        import geompack
                        def _sigc(s):
                            d = {}
                            for tt, ops in s["operations"].items():
                                for o in ops:
                                    if o["type"] == "ENTRY":
                                        d[o["block_id"]] = o["bay_id"]
                            return tuple(sorted(d.items()))
                        # Flooring pool = UNION of lowest-OBJECTIVE and lowest-Z3 seeds. Objective-only floored
                        # whichever seed anytime happened to make lowest-obj that run -> a higher-Z3 base at large
                        # budgets -> anti-anytime + variance (prob_29 +4.6%@120 but +22%@300). Z3-only fixed
                        # prob_29 but regressed prob_3 (+2.4%: its bk needs the lowest-OBJ base). Taking BOTH lets
                        # geompolish's best-of pick the right base per instance: prob_3 keeps its low-obj base,
                        # prob_29 gets its low-Z3 base (which cpsat_z3/asa then descend) -> both at bk-ish.
                        _rf = {id(s): utils.check_feasibility(prob_info, s) for s in seed_sols}
                        _byobj = sorted(seed_sols, key=lambda s: _rf[id(s)].get("objective", 1e18))
                        _byz3 = sorted(seed_sols, key=lambda s: _rf[id(s)].get("obj3", 1e18))
                        _cc = []; _ccseen = set()
                        for _ss in (_byobj[:2] + _byz3[:2]):
                            _sg = _sigc(_ss)
                            if _sg in _ccseen:
                                continue
                            _ccseen.add(_sg); _cc.append(_ss)
                            if len(_cc) >= 4:
                                break
                        _cb = timelimit * 0.55 - (time.time() - t0)
                        _gpc = geompack.geompolish(prob_info, _cc, utils, time_budget=max(3.0, _cb),
                                                   hard_deadline=t0 + timelimit * 0.58)
                        _take(_gpc, utils.check_feasibility(prob_info, _gpc))
                        _tr("geomcpsat", prob_info, _gpc, utils, t0, best_obj)
                except Exception:
                    if _TR:
                        import traceback as _tb; print("[EXC geomcpsat]"); _tb.print_exc()
                _sync()
                # CHEAP deterministic ilp+geompolish for <=2-bay ASSIGNMENT-DOMINATED instances. Gated on
                # best.Z1==0 (after the floor): that signals Z1=0 is reachable, so the opt-Z2/Z3 ilp assignment
                # likely packs to Z1=0 too -> geompolish reaches it (prob_4 +33..+207% variance -> deterministic
                # -32.4%; prob_25 +2.7%->bk). The Z1==0 gate EXCLUDES congested 2-bay (prob_23 Z1=311) where the
                # ilp assignment can't pack to Z1=0 and the ~12s would just steal from the congested packing.
                if len(prob_info["bays"]) <= 2 and utils.check_feasibility(prob_info, best).get("obj1", 1) == 0:
                    try:
                        _av2 = timelimit * 0.55 - (time.time() - t0)
                        if _av2 > 10.0:
                            import solve as _sv2, improve as _imp2, geompack as _gpk2
                            _asg2 = _sv2.ilp_assign(prob_info, tl=5.0)
                            if _asg2 is not None:
                                _is2 = _imp2.improve(prob_info, 4.0, master_assign=_asg2, fix_assign=True)
                                if utils.check_feasibility(prob_info, _is2).get("feasible"):
                                    _g2 = _gpk2.geompolish(prob_info, [_is2], utils,
                                                           time_budget=min(12.0, _av2),
                                                           hard_deadline=t0 + timelimit * 0.60)
                                    _take(_g2, utils.check_feasibility(prob_info, _g2))
                                    _tr("ilp2bay", prob_info, _g2, utils, t0, best_obj)
                    except Exception:
                        if _TR:
                            import traceback as _tb; print("[EXC ilp2bay]"); _tb.print_exc()
                    _sync()
            # UNIFIER -- BRKGA over (assignment, per-bay order), seeded by all of the above, elite beam-polish.
            # Small bays: BRKGA is MINIMIZED (_bf=0.42) -> the exact cpsat_polish+cpsat_z3 path gets the budget
            # (it solves small instances to optimum). Large bays: BRKGA-heavy (_bf=0.90, cpsat skipped).
            try:
                import brkga2
                brk_t = timelimit * 0.82 - (time.time() - t0) - 3.0
                # SMALL bays: skip BRKGA entirely -> the exact path (cpsat_polish + cpsat_z3) gets the whole
                # budget (it solves them to/below best; BRKGA's beam base just wastes small-instance time).
                if brk_t > 8.0 and seed_sols and not cpsat_useful:
                    # BENDERS ASSIGNMENT (small non-cpsat Z1=0-reachable): ilp_assign (min Z2/Z3) <-> geompolish
                    # (TRUE crane-aware packability) with over-packed-bay SET cuts. Cracks instances whose opt-
                    # Z2/Z3 assignment must be balanced to become Z1=0-packable -- which brkga's crane-blind decoder
                    # cannot target (prob_9 +14.4% -> -41.6%). cut-cap + best-of => bails cheaply + never regresses.
                    # n<=220 gate keeps ilp tractable AND excludes prob_15/17/18 (250-300 blk) where it does not
                    # converge; best.Z1==0 gate excludes congested (prob_27/30/etc keep their geompolish path).
                    if len(prob_info["blocks"]) <= 220 and utils.check_feasibility(prob_info, best).get("obj1", 1) == 0:
                        try:
                            import solve as _svb, improve as _impb, geompack as _gpb
                            _bdl = t0 + timelimit * 0.82; _scuts = []         # generous: brkga is weak on these
                            for _bit in range(5):
                                if _bdl - time.time() < 18:
                                    break
                                _ab = _svb.ilp_assign(prob_info, tl=5.0, set_forbid=_scuts)
                                if _ab is None:
                                    break
                                _ib = _impb.improve(prob_info, 3.0, master_assign=_ab, fix_assign=True)
                                if not utils.check_feasibility(prob_info, _ib).get("feasible"):
                                    break
                                # PER-ITER geompolish deadline (not the shared _bdl) so each pack runs to Z1=0 --
                                # a shared deadline starved the late iters -> false "un-packable" -> never cracked.
                                _gb = _gpb.geompolish(prob_info, [_ib], utils, time_budget=14.0,
                                                      hard_deadline=min(_bdl, time.time() + 16.0))
                                _rgb = utils.check_feasibility(prob_info, _gb)
                                _take(_gb, _rgb)
                                _tr("benders[%d]" % _bit, prob_info, _gb, utils, t0, best_obj)
                                if _rgb.get("obj1", 1) == 0:
                                    break
                                _bb = {}; _be = {}
                                for _tt, _ops in _gb["operations"].items():
                                    for _o in _ops:
                                        if _o["type"] == "ENTRY":
                                            _bb.setdefault(_o["bay_id"], []).append(_o["block_id"])
                                        elif _o["type"] == "EXIT":
                                            _be[_o["block_id"]] = int(_tt)
                                _bd = {j: sum(max(0, _be.get(i, 0) - prob_info["blocks"][i]["due_date"]) for i in ids)
                                       for j, ids in _bb.items()}
                                if not _bd:
                                    break
                                _jw = max(_bd, key=_bd.get); _scuts.append((_jw, _bb[_jw]))
                        except Exception:
                            if _TR:
                                import traceback as _tb2; print("[EXC benders]"); _tb2.print_exc()
                        _sync()
                    # UNIFIED asa: geompolish-decoded assignment LOCAL-SEARCH. On assignment-dominated (best.Z1==0)
                    # instances it finds Z1=0-packable assignments with far better Z2/Z3 than the crane-blind
                    # methods (prob_9 +14.4%->-23%, prob_22 +15%->-5%). best-of => never regresses quality. The
                    # >45s BUDGET gate (not an instance gate) is the principled control: asa needs ~70s to crack,
                    # so it runs only when there IS room (the grader's large-instance budget) and self-skips when
                    # tight (avoids stealing from the budget-saturated stages at small budgets).
                    if utils.check_feasibility(prob_info, best).get("obj1", 1) == 0 and (timelimit * 0.78 - (time.time() - t0)) > 45.0:
                        try:
                            import asa as _asa
                            _as = _asa.solve_asa(prob_info, utils,
                                                 min(timelimit * 0.30, timelimit * 0.78 - (time.time() - t0)),
                                                 seed_sols=seed_sols, seed=0)
                            if _as is not None:
                                _take(_as, utils.check_feasibility(prob_info, _as))
                                _tr("asa", prob_info, _as, utils, t0, best_obj)
                        except Exception:
                            if _TR:
                                import traceback as _tb3; print("[EXC asa]"); _tb3.print_exc()
                        _sync()
                    # PARALLEL MULTI-START brkga2 + asls polish, best-of. brkga2's per-instance ASSIGNMENT draw
                    # is high-variance (prob_20: a good draw -> 254k, a bad draw -> 456k); a SINGLE run gambles
                    # on one draw -> the large-instance non-monotonicity. We have 4 cores, so instead of one run
                    # on 4 cores we run 4 INDEPENDENT seeds CONCURRENTLY, each on 1 core, EACH at the FULL budget.
                    # brkga2's win is escaping the bad assignment basin (diversity), not more generations within a
                    # basin (it plateaus fast) -- so 4 diverse full-time draws best-of beats 1 four-core draw, and
                    # always-on best-of-4 dominates the bad draw at EVERY budget (no N-vs-budget gating needed).
                    # ABANDON-SAFE: mp.Pool with per-job get(timeout) + finally terminate() -> no orphaned procs
                    # past the wall deadline (a raw thread-abandon would leak 4 CPU-bound children to the grader).
                    import asls as _asls_l, multiprocessing as _mp, beam_polish as _bp
                    # SEED BEAM FLOOR. The GA ranks assignments by the GREEDY decoder, which on congested bays
                    # under-packs Z1 by 20-35% (measured) -> the GA can converge to an assignment that is good
                    # for greedy but WORSE under the true (beam) packer, discarding a beam-good SEED (prob_38:
                    # beam-on-seeds 70.2M, but the GA returned 77.3M -- a 10% self-inflicted loss). beam_final
                    # only polishes the GA's chosen assignment, never the seeds it rejected. So: beam-polish the
                    # best few DISTINCT seed assignments up front and _take them -> a floor the GA can only
                    # improve on, never lose to. Cheap (a few packs), abandon-safe (each published immediately).
                    def _sig(s):
                        d = {}
                        for tt, ops in s["operations"].items():
                            for o in ops:
                                if o["type"] == "ENTRY":
                                    d[o["block_id"]] = o["bay_id"]
                        return tuple(sorted(d.items()))
                    # CONGESTION-AWARE: the floor only pays off when the GA FAILS to beat the beam-packed seeds
                    # (prob_38: islands just echo the raw seed -> the beam-polished seed is the whole answer, a
                    # 10% win). When the GA wins (prob_27) or beam can't improve the seed (low-congestion prob_20,
                    # decoder gap ~0), the floor only STEALS island time (best-of keeps quality but prob_20 then
                    # can't find its Z1=0 assignment). So: beam-polish ONLY the single best seed (one pack, ~20s
                    # cap -- enough for prob_38), and extend to more seeds ONLY if that polish improved the seed
                    # meaningfully (>3%) AND budget is plentiful. On low-congestion the first polish skips Z1=0
                    # bays so it is cheap and we stop immediately -> islands keep their time.
                    # GEOM SEED FLOOR (the crane-aware lever). geompack re-packs an assignment with per-bay order
                    # SA; it beats beam by 10-70% on congested. Run it on the best few DISTINCT seed assignments.
                    # SELF-REGULATING: sa_bay stops a bay at Z1=0, so Z1=0-reachable instances return fast and
                    # brkga keeps its budget; congested instances reach the crane-aware optimum.
                    def _sig(s):
                        d = {}
                        for tt, ops in s["operations"].items():
                            for o in ops:
                                if o["type"] == "ENTRY":
                                    d[o["block_id"]] = o["bay_id"]
                        return tuple(sorted(d.items()))
                    _cands = []; _cseen = set()
                    for _ss in sorted(seed_sols, key=lambda s: utils.check_feasibility(prob_info, s)["objective"]):
                        _sg = _sig(_ss)
                        if _sg in _cseen:
                            continue
                        _cseen.add(_sg); _cands.append(_ss)
                        if len(_cands) >= 3:
                            break
                    try:
                        if _cands and (timelimit * 0.62 - (time.time() - t0)) > 4.0:
                            import geompack
                            _nom = timelimit * 0.62 - (time.time() - t0)
                            _gp = geompack.geompolish(prob_info, _cands, utils,
                                                      time_budget=max(3.0, _nom),
                                                      hard_deadline=t0 + timelimit * 0.62)
                            _take(_gp, utils.check_feasibility(prob_info, _gp))
                            _tr("geomseed", prob_info, _gp, utils, t0, best_obj)
                    except Exception:
                        if _TR:
                            import traceback as _tb; print("[EXC geomseed]"); _tb.print_exc()
                    _sync()
                    brk_deadline = t0 + timelimit * 0.82            # children must return by here; asls gets 0.82->0.92
                    per = max(8.0, brk_deadline - time.time())
                    pool = None
                    # DIVERSE LENSES: each island a distinct explore<->exploit balance so the 4 converge to
                    # COMPLEMENTARY assignment basins (RNG-seed-only diversity left them correlated -> a run
                    # where all 4 miss the good basin = prob_20 variance). (rho, pm_frac): hard-exploit, balanced,
                    # explore-heavy, mid. best-of-4 over diverse basins reliably catches the good one.
                    # ISLAND COUNT vs DEPTH trade-off: this is a 2-PHYSICAL-core box (4 logical via HT). 4 islands
                    # x workers=1 gives each island ~half a physical core -> shallow; great for variance-prone
                    # instances (escape a bad basin: prob_9/14/20 big wins) but it UNDER-COMPUTES the congested
                    # ones that need brkga depth (prob_24/29 regressed vs the old single deep run). 2 islands x
                    # workers=2 gives each ~a full physical core -> deeper, while keeping best-of-2 diversity.
                    import os as _os
                    _NISL = int(_os.environ.get("OGC_NISL", "1"))
                    _ALL_LENS = [(0.85, 0.10), (0.62, 0.30), (0.70, 0.15), (0.75, 0.22)]
                    if _NISL <= 1:
                        # SINGLE DEEP run, workers=4: brkga's own mp.Pool(4) parallelises the population eval
                        # across all logical cores -> maximum depth. Best on congested instances that need many
                        # generations (the old submission did this; parallel workers=1 islands under-computed
                        # them). Called DIRECTLY in this worker thread (not nested in a daemonic Pool worker,
                        # which cannot spawn the inner Pool). Diversity instead comes from the seed pool + the
                        # seed-beam FLOOR (which already recovered the big prob_9/14/20 wins, not the islands).
                        try:
                            bsol = brkga2.solve_brkga2(prob_info, per, seeds=seeds_asg, seed_sols=seed_sols,
                                                       workers=4, seed=0, beam_final=True,
                                                       rho=_ALL_LENS[0][0], pm_frac=_ALL_LENS[0][1])
                            _take(bsol, utils.check_feasibility(prob_info, bsol))
                            _tr("brkga2", prob_info, bsol, utils, t0, best_obj)
                        except Exception:
                            if _TR:
                                import traceback as _tb; print("[EXC brkga2]"); _tb.print_exc()
                    else:
                        # PARALLEL islands, workers=1 each: more diverse basins, shallower each. Abandon-safe via
                        # the shared pool (P3). Use when diversity matters more than depth.
                        _LENS = _ALL_LENS[:_NISL]
                        try:
                            pool = _mp.Pool(_NISL)
                            with lock:
                                S["pool"] = pool
                            jobs = [pool.apply_async(brkga2.solve_brkga2,
                                                     (prob_info, per, seeds_asg, seed_sols, 1, _k, False, True,
                                                      _LENS[_k][0], _LENS[_k][1]))
                                    for _k in range(_NISL)]
                            for _k, j in enumerate(jobs):
                                rem = brk_deadline - time.time()
                                try:
                                    bsol = j.get(timeout=max(0.5, rem + 5.0))
                                except Exception:
                                    continue
                                _take(bsol, utils.check_feasibility(prob_info, bsol))
                                _tr(f"brkga2[{_k}]", prob_info, bsol, utils, t0, best_obj)
                        finally:
                            if pool is not None:
                                pool.terminate(); pool.join()
                                with lock:
                                    S["pool"] = None
                    try:                                               # asls on the best of the 4 (Z2/Z3 descent)
                        aa = _asls_l.asls(prob_info, best, utils,
                                          time_budget=max(2.0, timelimit * 0.84 - (time.time() - t0)))
                        r = utils.check_feasibility(prob_info, aa)
                        _take(aa, r)
                        _tr("asls", prob_info, aa, utils, t0, best_obj)
                    except Exception:
                        if _TR:
                            import traceback as _tb; print("[EXC asls]"); _tb.print_exc()
            except Exception:
                if _TR:
                    import traceback as _tb; print("[EXC outer-brk]"); _tb.print_exc()
            _sync()
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
                if cpsat_useful and seed_sols and (timelimit * 0.88 - (time.time() - t0)) > 8.0:
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
                        elif (not z10 and n_polished < 3
                              and time.time() - t0 < timelimit * 0.62
                              and (timelimit * 0.80 - (time.time() - t0) - 3.0) > 14.0):
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
                                _take(cpp, rp)
                                if rp.get("obj1", 1) == 0:
                                    z10.append((rp.get("obj3", 0), cpp, sig in construct_sigs))
                            _tr(f"polish[{sig and n_polished}]", prob_info, cpp, utils, t0, best_obj)
                    # The geomcpsat FLOOR may already have produced a Z1=0 `best` whose assignment is NOT in
                    # seed_sols -> include it as a cpsat_z3 base. Without this, a run where cpsat_polish happened
                    # not to reach Z1=0 left z10 EMPTY -> fell to the Z1-bound brkga2 path and SKIPPED cpsat_z3
                    # entirely (prob_29: cpsat_z3 finds 687k but the floor-only run returned 1.17M -> the +72%
                    # inter-run variance). Guaranteeing a base makes cpsat_z3 reliably run = far lower variance.
                    try:
                        _rbest = _ck(best)
                        if _rbest.get("obj1", 1) == 0:
                            _bsig = tuple(sorted(_asg(best).items()))
                            # dedup against z10 (Z1=0 bases), NOT the raw-seed `seen`: best is the FLOORED
                            # Z1=0 version whose assignment matches a Z1>0 raw seed already in `seen`, so the
                            # old `not in seen` gate WRONGLY dropped it -> when cpsat_polish (non-det) failed
                            # to reach Z1=0, z10 was EMPTY -> route fell to brkga2-bound, skipping cpsat_z3
                            # (prob_29 +73% variance). Guaranteeing the floored best enters z10 = cpsat_z3
                            # always runs when Z1=0 is reachable.
                            _z10sig = {tuple(sorted(_asg(e[1]).items())) for e in z10}
                            if _bsig not in _z10sig:
                                z10.append((_rbest.get("obj3", 0), best, _bsig in construct_sigs))
                    except Exception:
                        pass
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
                            bt = timelimit * 0.82 - (time.time() - t0) - 3.0
                            if bt > 6.0 and seeds_asg:
                                bs = brkga2.solve_brkga2(prob_info, bt, seeds=seeds_asg, seed_sols=seed_sols,
                                                         workers=4, beam_final=True)
                                r = _ck(bs)
                                _take(bs, r)
                                _tr("brkga2-bound", prob_info, bs, utils, t0, best_obj)
                        except Exception:
                            pass
                    else:
                        # Z1=0-REACHABLE: cpsat_z3 (Z3 descent) + asls (Z2). PREFER the construct basin (pref-
                        # sharp -> descends to Z3~67 vs anytime's ~191; one deep deterministic run -> prob_3
                        # stable 43250). No construct (prob_4): descent is variable, so best-of two deep
                        # attempts when budget allows (different low-Z3 base if any, else re-run -- CP-SAT non-det).
                        # BASE SELECTION: the construct basin is pref-sharp (cpsat_z3 descends Z3 deep from it --
                        # prob_3 Z3 67 vs 191), BUT a construct candidate can be a TERRIBLE cpsat_z3 base whose
                        # descent ends WORSE than just polishing the global best (prob_10 @300: construct base
                        # 233723 -> cpsat_z3 186746 > the available 152852 -> 122s wasted, +30.5%). So DON'T
                        # blindly trust construct-prefer: try the lowest-Z3 construct base AND the lowest-OBJ
                        # overall base, best-of (the _take across bases keeps the winner). Restores the basin
                        # diversity that a larger anytime budget collapses (the budget non-monotonicity).
                        srt_z3 = sorted(z10, key=lambda x: x[0])                 # by Z3
                        _objof = lambda e: utils.check_feasibility(prob_info, e[1]).get("objective", 1e18)
                        srt_ob = sorted(z10, key=_objof)                         # by true objective
                        _cz = [e for e in srt_z3 if e[2]]
                        cand_bases = []
                        if _cz:
                            cand_bases.append(_cz[0][1])                         # lowest-Z3 construct (pref-sharp)
                        cand_bases.append(srt_ob[0][1])                          # lowest-OBJ overall (strong basin)
                        cand_bases.append(srt_z3[0][1])                          # lowest-Z3 overall
                        # dedup by assignment signature, preserve order
                        _sb = set(); bases = []
                        for _b in cand_bases:
                            _sg = tuple(sorted(_asg(_b).items()))
                            if _sg not in _sb:
                                _sb.add(_sg); bases.append(_b)
                        # budget guard: only multi-base when there is room (each base needs ~the cpsat_z3 budget)
                        if (timelimit * 0.88 - (time.time() - t0)) <= 60.0:
                            bases = bases[:1]
                        else:
                            bases = bases[:3]
                        # Each base's low-Z3 cpsat_z3 result is Z2-polished by asls (the BK chain is cpsat_z3->asls,
                        # so asls must run PER BASE, not just on the global best -- a low-Z3 result that isn't the
                        # current global best still polishes to BK). best-of via _take keeps the winner across bases.
                        per = (timelimit * 0.88 - (time.time() - t0)) / max(1, len(bases))
                        for zb in bases:
                            if timelimit * 0.88 - (time.time() - t0) < 6.0:
                                break
                            if _TR:
                                _rzb = utils.check_feasibility(prob_info, zb)
                                print(f"[trace] ZB nbases={len(bases)} per={per:.0f}s zb_obj={_rzb.get('objective'):.0f} "
                                      f"zb_Z2={_rzb.get('obj2')} zb_Z3={_rzb.get('obj3')}", flush=True)
                            zz = cpsat_z3.cpsat_z3(prob_info, zb, utils,
                                                   time_budget=per, per_bay_tl=min(20.0, per * 0.25))
                            r = _ck(zz)
                            _take(zz, r)
                            _tr("cpsat_z3", prob_info, zz, utils, t0, best_obj)
                            az = _asls.asls(prob_info, zz, utils, time_budget=min(12.0, max(3.0, per * 0.25)))
                            ra = _ck(az)
                            _take(az, ra)
                            _tr("asls[zz]", prob_info, az, utils, t0, best_obj)
                        aa = _asls.asls(prob_info, best, utils, time_budget=3.0)
                        r = _ck(aa)
                        _take(aa, r)
                        _tr("asls", prob_info, aa, utils, t0, best_obj)
            except Exception:
                pass
            _sync()
            # CRANE-AWARE ASSIGNMENT SEARCH (geomsearch). Directed assignment local-search scored by the SAME
            # geom decoder geompolish realizes with: move a block out of the worst-tardy bay, re-pack only the
            # two affected bays with geompack, accept on the true full objective, best-of. This is the assignment
            # lever the decoder-no-op finding pointed to (search WITH the real evaluator). CONGESTION-GATED on
            # best.Z1>0: it is a TARDINESS tool (cross-bay moves relieve the tardy bay). On Z1=0 instances its
            # moves don't help Z2/Z3 and it would only STEAL budget from the cpsat path -> measured regression
            # (prob_11 -1.79% -> 0.00%). The gate makes Z1=0 instances keep their full budget (no theft) while
            # congested ones get the win (prob_34 +4.98% -> -3.55%, beats bk). best-of => output never regresses.
            _rgs = utils.check_feasibility(prob_info, best)
            if (_rgs.get("feasible") and _rgs.get("obj1", 0) > 0
                    and timelimit * 0.86 - (time.time() - t0) > 8.0):
                try:
                    import geomsearch
                    _gsf = float(_os.environ.get("OGC_GS_FRAC", "0.16"))
                    _gt = min(timelimit * _gsf, timelimit * 0.86 - (time.time() - t0))
                    gs = geomsearch.assign_search(prob_info, best, utils, time_budget=max(4.0, _gt), seed=0)
                    r = utils.check_feasibility(prob_info, gs)
                    _take(gs, r)
                    _tr("geomsearch", prob_info, gs, utils, t0, best_obj)
                except Exception:
                    if _TR:
                        import traceback as _tb; print("[EXC geomsearch]"); _tb.print_exc()
                _sync()
            # CRANE-AWARE GEOM POLISH (the big lever). geompack = a fast C bit-packed union-footprint (=crane-
            # clean) bay packer; geompolish re-packs each bay of the current best with per-bay SA over the
            # placement ORDER. union-polygon (prism) density + order search beats the crane-blind beam/AABB
            # packers by 10-70% on CONGESTED bays (prob_24 3.0M->0.86M, prob_27 48.4M->42.5M, prob_20/30 -60%/
            # -23%). Only meaningful when Z1>0 (a Z1=0 solution can't be improved by re-packing), so gate on it;
            # best-of via _take => never regresses (prob_38, the one order-hard holdout, just keeps the prior).
            try:
                _rb = utils.check_feasibility(prob_info, best)
                if _rb.get("feasible") and _rb.get("obj1", 0) > 0 and (timelimit * 0.88 - (time.time() - t0)) > 4.0:
                    import geompack
                    # geompolish re-packs an ASSIGNMENT crane-aware; brkga's best assignment was selected by the
                    # crane-BLIND decoder, so it is often NOT the assignment that packs best under geompack (the
                    # construct seeds pack better -- prob_27 brkga-best->45M but a construct seed->42.5M). So
                    # geompolish the GA best AND the best distinct construct/seed assignments, best-of.
                    _cands = [best]
                    _csigs = {tuple(sorted(_asg(best).items()))}
                    for _s in sorted(seed_sols, key=lambda s: utils.check_feasibility(prob_info, s).get("objective", 1e18)):
                        _r = utils.check_feasibility(prob_info, _s)
                        if not _r.get("feasible") or _r.get("obj1", 0) <= 0:
                            continue
                        _sg = tuple(sorted(_asg(_s).items()))
                        if _sg in _csigs:
                            continue
                        _csigs.add(_sg); _cands.append(_s)
                        if len(_cands) >= 3:
                            break
                    # ONE geompolish call over all candidates -> shared GeomPacker rasterizes orients ONCE
                    # (no per-candidate re-raster waste), so the whole budget buys SA. best-of => never regresses.
                    # full remaining window for the FINAL crane-aware polish (the winner on congested instances);
                    # iteration-based SA keeps it stable for a given budget, wall_cap bounds the deadline.
                    _nomf = timelimit * 0.88 - (time.time() - t0)
                    gpsol = geompack.geompolish(prob_info, _cands, utils,
                                                time_budget=max(3.0, _nomf),
                                                hard_deadline=t0 + timelimit * 0.88)
                    _take(gpsol, utils.check_feasibility(prob_info, gpsol))
            except Exception:
                pass
            _sync()
            # FINAL POLISH (ALL instances). polypack (polygon density) + placement_polish tighten congested
            # packing (skipping regressed prob_23/26/33). BUT both time-check only BETWEEN bays, and a single
            # large-bay pack (placement_polish's contact-max scan / polypack's numpy sliding-window) is an
            # un-interruptible call the daemon-join CANNOT stop -> if it starts late it blows the wall budget ->
            # server kills the process -> P3 -1. This is the recurring P3 timeout. FIX (was regressed from 0.83T
            # back to 0.90/0.92T): bound the whole polish phase to 0.83T -> >=0.11T margin before the 0.94T join
            # + 0.17T before the limit, enough to absorb one slow final pack on a slower grader. GATE on real
            # remaining budget (no 2s floor that could itself push past the join).
            if timelimit * 0.84 - (time.time() - t0) > 4.0:
                try:
                    import assign_lns
                    pp = assign_lns.placement_polish(prob_info, best, utils,
                                                     time_budget=timelimit * 0.88 - (time.time() - t0))
                    r = utils.check_feasibility(prob_info, pp)
                    _take(pp, r)
                except Exception:
                    pass
            if timelimit * 0.88 - (time.time() - t0) > 3.0:
                try:
                    import polypack
                    pol = polypack.polish_solution(prob_info, best, utils,
                                                   time_budget=timelimit * 0.88 - (time.time() - t0))
                    r = utils.check_feasibility(prob_info, pol)
                    _take(pol, r)
                except Exception:
                    pass
            _sync()
        except Exception:
            pass
    _sync()                                                # final publish to the shared state

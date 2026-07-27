#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include "alns.hpp"
#ifdef _OPENMP
#include <omp.h>
#endif
namespace py = pybind11;
using VV = std::vector<std::vector<std::pair<double,double>>>;

struct Engine {
    core::Problem pb;
    core::Sol sol;

    void initProblem(int n, int m,
                     std::vector<int> R, std::vector<int> D, std::vector<int> P,
                     std::vector<double> workload,
                     std::vector<std::vector<double>> pref,
                     std::vector<double> bayW, std::vector<double> bayH,
                     double w1, double w2, double w3) {
        pb.n = n; pb.m = m;
        pb.R = std::move(R); pb.D = std::move(D); pb.P = std::move(P);
        pb.workload = std::move(workload);
        pb.pref = std::move(pref);
        pb.bayW = std::move(bayW); pb.bayH = std::move(bayH);
        pb.w1 = w1; pb.w2 = w2; pb.w3 = w3;
        pb.Smax.resize(n);
        for (int i = 0; i < n; i++) {
            double s = -1e18;
            for (double v : pb.pref[i]) s = std::max(s, v);
            pb.Smax[i] = s;
        }
        double avg = 0;
        for (int j = 0; j < m; j++) avg += pb.bayW[j] * pb.bayH[j];
        avg /= m;
        pb.u.resize(m);
        for (int j = 0; j < m; j++) pb.u[j] = avg / (pb.bayW[j] * pb.bayH[j]);
        pb.geo.assign(n, {});
        sol.init(n, m);
    }

    // add one orientation's layers for block i (local coords, ref at origin)
    void addOrient(int i, const VV& layers) {
        crane::OrientGeom g;
        geom::BBox bb{1e18,1e18,-1e18,-1e18};
        for (auto& L : layers) {
            geom::Poly p;
            for (auto& v : L) p.push_back({v.first, v.second});
            auto b = geom::bbox(p);
            bb.x0=std::min(bb.x0,b.x0); bb.y0=std::min(bb.y0,b.y0);
            bb.x1=std::max(bb.x1,b.x1); bb.y1=std::max(bb.y1,b.y1);
            g.lbb.push_back(b);                          // sec 166 cache
            g.ip.push_back(geom::interiorPoint(p));      // sec 166 cache
            g.layers.push_back(std::move(p));
        }
        g.bb = bb;
        pb.geo[i].orients.push_back(std::move(g));
    }

    void setBlock(int i, int bay, int ori, int x, int y, int e, int f, bool placed) {
        sol.bay[i]=bay; sol.ori[i]=ori; sol.x[i]=x; sol.y[i]=y;
        sol.e[i]=e; sol.f[i]=f; sol.placed[i]=placed?1:0;
    }
    void finalizeLoad() { sol.rebuildIdx(pb.m); }
    std::vector<int> getBlock(int i) {
        return {sol.bay[i], sol.ori[i], sol.x[i], sol.y[i], sol.e[i], sol.f[i],
                (int)sol.placed[i]};
    }
    bool vpOK(int A) { return core::vpConservative(pb, sol, A); }
    std::vector<double> profCounters() {
        auto& p = repair::prof();
        std::vector<double> out = {(double)p.calls, (double)p.fails,
            p.t1, p.t2, p.t3, (double)p.succR, (double)p.succEvt,
            (double)p.succFall, (double)p.evtScans,
            p.g, p.h, p.v, (double)p.hN, (double)p.vN};
        p = repair::Prof();
        return out;
    }
    std::vector<int> auditState() {
        int bb = -1; int code = alns::audit(pb, sol, &bb);
        int sz0 = pb.m > 0 ? (int)sol.byBay[0].size() : -1;
        return {code, bb, sz0};
    }

    occ::OccIndex oc;
    repair::Ctx ctx;
    void buildOcc(int horizon) {
        horizon_ = horizon;
        sol.rebuildIdx(pb.m);           // defensive: never trust caller sync
        oc.build(pb, horizon);
        // stamp all placed blocks
        for (int i = 0; i < pb.n; i++)
            if (sol.placed[i]) oc.stamp(pb, sol, i, true);
        ctx.pb = &pb; ctx.s = &sol; ctx.oc = &oc; ctx.horizon = horizon;
    }
    void setEventLocal(bool v) { ctx.eventLocal = v; }
    void setNestAnchors(bool v) { ctx.nestAnchors = v; }
    void setDiagNoCrane(bool v) { core::diagNoCrane() = v; }  // DIAGNOSTIC
    void setWaveGrid(int g) { ctx.waveG = g; }
    // assignment lock: persistent forcedBay for the whole run -> repairs
    // keep every block in its locked bay, so Z2/Z3 are frozen at the locked
    // assignment's exact values and the search optimizes pure Z1 geometry.
    std::vector<int> lockVec;
    int lastMissed = 0;
    int getLastMissed() { return lastMissed; }
    void setAssignmentLock(std::vector<int> v) {
        lockVec = std::move(v);
        ctx.forcedBay = lockVec.empty() ? nullptr : lockVec.data();
    }
    void setScoreLocal(bool v) { ctx.scoreLocal = v; }
    std::vector<double> runALNS(double seconds, double qlo, double qhi,
                                double T0mul, double cooling, uint64_t seed,
                                int auditEvery = 0, double hcFrac = 0.75,
                                double bigQProb = 0.04, int opMask = 0xFF) {
        alns::RunParams rp;
        rp.qloF = qlo; rp.qhiF = qhi; rp.T0mul = T0mul; rp.cooling = cooling;
        rp.hcFrac = hcFrac; rp.bigQProb = bigQProb;
        rp.opMask = (uint32_t)opMask;
        auto st = alns::run(ctx, seconds, rp, seed, auditEvery);
        return {(double)st.iters, (double)st.accepted, (double)st.improved,
                (double)st.failed, st.best, (double)st.badIter,
                (double)st.badKind, (double)st.badPhase};
    }
    int horizon_ = 0;
    std::vector<double> runPortfolio(int threads, double seconds, int rounds,
                                     double qlo, double qhi, double T0mul,
                                     double cooling, uint64_t seed,
                                     int multiStart = 1) {
        core::Sol globalBest = sol;
        double bestObj = core::evaluate(pb, sol).total;
        long totIters = 0;
        rounds = std::max(1, rounds);
        double perRound = seconds / rounds;
        std::vector<core::Sol> perThread((size_t)threads, sol);
        const bool dbgRounds = (getenv("SMAX_DEBUG") != nullptr);
        for (int r = 0; r < rounds; r++) {
            core::Sol roundStart = globalBest;
            double roundEnterBest = bestObj;
            #pragma omp parallel num_threads(threads)
            {
                int tid = 0;
                #ifdef _OPENMP
                tid = omp_get_thread_num();
                #endif
                // half the threads follow the global best (exploit); the
                // other half continue their own line (explore, FAST-style
                // diversity preserved across migration rounds).
                core::Sol s2 = roundStart;   // continuity removed: fired
                // server-first (threads>=6) and board P4-P6 regressed.
                occ::OccIndex oc2; oc2.build(pb, horizon_);
                repair::Ctx c2; c2.pb = &pb; c2.s = &s2; c2.oc = &oc2;
                c2.horizon = horizon_;
                c2.eventLocal = ctx.eventLocal;   // inherit engine setting
                c2.nestAnchors = ctx.nestAnchors;
                c2.waveG = ctx.waveG;
                c2.scoreLocal = ctx.scoreLocal;
                c2.forcedBay = ctx.forcedBay;   // assignment lock -> threads
                bool constructed = false;
                if (r == 0 && multiStart && (tid % 2 == 1)) {
                    constructed = alns::constructInitial(
                        c2, seed + 77ULL * (tid + 1));
                    if (!constructed) {
                        s2 = roundStart;
                        oc2.clearBits();
                    }
                }
                if (!constructed) {
                    for (int i = 0; i < pb.n; i++)
                        if (s2.placed[i]) oc2.stamp(pb, s2, i, true);
                }
                alns::RunParams rp;
                double mult = (tid % 3 == 0 ? 0.6 : (tid % 3 == 1 ? 1.0 : 1.5));
                rp.qloF = qlo * mult; rp.qhiF = qhi * mult;
                rp.T0mul = T0mul * ((tid/3) % 3 == 0 ? 0.5 :
                                    ((tid/3) % 3 == 1 ? 1.0 : 2.0));
                rp.cooling = cooling;
                rp.hcFrac  = (tid % 2 == 0) ? 1.0 : 0.75;   // half pure-SA (v1)
                rp.bigQProb = (tid % 2 == 0) ? 0.0 : 0.06;
                rp.opMask  = (tid % 2 == 0) ? 0x1F : 0xFF;  // smax27 split:
                // kinds 8-10 masked off — A/B showed harm on P4-proxy (p28)
                // and no benefit on small (p22); code kept dormant.
                auto st = alns::run(c2, perRound, rp,
                                    seed + 1000003ULL * (r * 64 + tid + 1));
                {   // post-round left compaction on this thread's best.
                    // alns::run returned bestSol into s2 but oc2 reflects the
                    // last current state -> re-stamp cleanly first.
                    oc2.clearBits();
                    for (int i = 0; i < pb.n; i++)
                        if (s2.placed[i]) oc2.stamp(pb, s2, i, true);
                    repair::Ctx c3; c3.pb=&pb; c3.s=&s2; c3.oc=&oc2;
                    c3.horizon=horizon_;
                    alns::compactLeft(c3, 3);
                }
                perThread[tid] = s2;
                #pragma omp critical
                {
                    totIters += st.iters;
                    if (st.best < bestObj - 1e-9) {
                        bestObj = st.best; globalBest = s2;
                    }
                }
            }
            if (dbgRounds)
                fprintf(stderr, "[round %d] best %.6g -> %.6g (%+.3g%%)\n",
                        r, roundEnterBest, bestObj,
                        roundEnterBest > 0 ?
                        100.0 * (bestObj - roundEnterBest) / roundEnterBest
                        : 0.0);
        }
        sol = globalBest;
        return {(double)totIters, bestObj};
    }
    // rebuild from empty with a fixed bay assignment and an EXPLICIT
    // insertion order (order-space search: construction quality dominates
    // on heavy instances where the ALNS moves only +-3%).
    int reconstructOrdered(std::vector<int> asg, std::vector<int> order,
                           bool tight, uint64_t seed) {
        if ((int)asg.size() != pb.n || (int)order.size() != pb.n) return -2;
        core::Sol backup = sol;
        occ::OccIndex oc2; oc2.build(pb, horizon_);
        repair::Ctx c2; c2.pb=&pb; c2.s=&sol; c2.oc=&oc2; c2.horizon=horizon_;
        c2.forcedBay = asg.data();
        c2.rng.seed(seed);
        for (int i = 0; i < pb.n; i++) sol.placed[i] = 0;
        sol.rebuildIdx(pb.m);
        int bad = -1;
        for (int A : order) {
            int eCap = tight ? (pb.D[A] - pb.P[A]) : -1;
            if (tight && eCap < pb.R[A]) { bad = A; break; }
            if (!repair::insertGreedy(c2, A, eCap)) { bad = A; break; }
        }
        if (bad >= 0) { sol = backup; oc.build(pb, horizon_);
            for (int i = 0; i < pb.n; i++)
                if (sol.placed[i]) oc.stamp(pb, sol, i, true);
            return bad; }
        oc.build(pb, horizon_);
        for (int i = 0; i < pb.n; i++)
            if (sol.placed[i]) oc.stamp(pb, sol, i, true);
        return -1;
    }
    // master-plan realization: rebuild honouring PER-BLOCK entry caps
    // (eCap[i] = master e*_i + band). Processed in the caller's order
    // (ascending e*), so early cohorts occupy space first and the realized
    // timing tracks the plan.
    int reconstructTargeted(std::vector<int> asg, std::vector<int> order,
                            std::vector<int> eCaps, uint64_t seed,
                            std::vector<int> eMins = {}, int ladder = 0) {
        if ((int)asg.size() != pb.n || (int)order.size() != pb.n ||
            (int)eCaps.size() != pb.n) return -2;
        lastMissed = 0;
        core::Sol backup = sol;
        occ::OccIndex oc2; oc2.build(pb, horizon_);
        repair::Ctx c2; c2.pb=&pb; c2.s=&sol; c2.oc=&oc2; c2.horizon=horizon_;
        c2.forcedBay = asg.data();
        c2.rng.seed(seed);
        for (int i = 0; i < pb.n; i++) sol.placed[i] = 0;
        sol.rebuildIdx(pb.m);
        int bad = -1;
        if ((int)eMins.size() == pb.n) c2.eMin = eMins.data();
        for (int A : order) {
            int cap = eCaps[A] > 0 ? eCaps[A] : -1;   // entry-time cap
            bool ok = repair::insertGreedy(c2, A, cap);
            // fallback ladder: widen the cap geometrically before giving
            // the block up to "anywhere" (which lands it very late and is
            // the main realization leak). eMin drops with the ladder.
            for (int L = 1; !ok && L <= ladder && cap > 0; L++) {
                c2.eMin = nullptr;
                ok = repair::insertGreedy(c2, A, cap + (8 << L));
                if ((int)eMins.size() == pb.n) c2.eMin = eMins.data();
            }
            if (!ok) {
                c2.eMin = nullptr;
                ok = repair::insertGreedy(c2, A, -1);
                if ((int)eMins.size() == pb.n) c2.eMin = eMins.data();
                lastMissed++;
            }
            if (!ok) { bad = A; break; }
        }
        c2.eMin = nullptr;
        if (bad >= 0) { sol = backup; oc.build(pb, horizon_);
            for (int i = 0; i < pb.n; i++)
                if (sol.placed[i]) oc.stamp(pb, sol, i, true);
            return bad; }
        oc.build(pb, horizon_);
        for (int i = 0; i < pb.n; i++)
            if (sol.placed[i]) oc.stamp(pb, sol, i, true);
        return -1;
    }

    // rebuild from empty with a fixed bay assignment (due order + noise);
    // eCap=due enforces zero-tardiness placements when tight=true.
    int reconstructAssigned(std::vector<int> asg, bool tight, uint64_t seed) {
        if ((int)asg.size() != pb.n) return -2;
        core::Sol backup = sol;
        occ::OccIndex oc2; oc2.build(pb, horizon_);
        repair::Ctx c2; c2.pb=&pb; c2.s=&sol; c2.oc=&oc2; c2.horizon=horizon_;
        c2.forcedBay = asg.data();
        c2.rng.seed(seed);
        for (int i = 0; i < pb.n; i++) sol.placed[i] = 0;
        sol.rebuildIdx(pb.m);
        std::vector<int> order(pb.n);
        for (int i = 0; i < pb.n; i++) order[i] = i;
        auto slack = [&](int i){ return pb.D[i] - pb.P[i] - pb.R[i]; };
        auto areaOf = [&](int i){
            const auto& g = pb.geo[i].orients[0];
            return (g.bb.x1 - g.bb.x0) * (g.bb.y1 - g.bb.y0);
        };
        std::sort(order.begin(), order.end(), [&](int a, int b){
            int sa = slack(a), sb = slack(b);
            if (sa != sb) return sa < sb;              // least slack first
            double da = areaOf(a), db = areaOf(b);
            if (da != db) return da > db;              // big blocks first
            return pb.D[a] < pb.D[b];
        });
        int bad = -1;
        for (int A : order) {
            // tight: entry must be <= D - P  => eCap = D - P
            int eCap = tight ? (pb.D[A] - pb.P[A]) : -1;
            if (tight && eCap < pb.R[A]) { bad = A; break; }
            if (!repair::insertGreedy(c2, A, eCap)) { bad = A; break; }
        }
        if (bad >= 0) { sol = backup; return bad; }
        return -1;
    }
    // left time-compaction: for each placed block (ascending e), try earlier
    // entries at the SAME (bay,x,y,ori); take the earliest vp-feasible one.
    // Directly squeezes Z1; cheap; strictly non-regressing per move.
    int compactLeft(int maxPasses) {
        return alns::compactLeft(ctx, maxPasses);
    }
    int compactLeftReplace(int maxPasses) {
        return alns::compactLeftReplace(ctx, maxPasses);
    }

    // move one block to a target bay with entry <= D-P (tight). Returns true
    // on success; restores the block on failure.
    // Windowed repack: pull out blocks entering in [t0,t1), reinsert them
    // EDD-first with the event sweep capped at t1+slack (bounded cost at
    // large n), uncapped fallback per block. Returns 0 on success, -1 if any
    // block could not be reinserted (caller restores its snapshot then).

    // two-phase migration: remove every listed block first (freeing target
    // space on BOTH sides), then reinsert in due order with forced bays.
    // One-way sequential tight moves deadlock (measured p24: todo=21,
    // moved=1-2, hybrid states rejected 100%); simultaneous exchange is the
    // point. Returns number placed; caller checker-gates and restores via
    // its snapshot on rejection.
    int migrateBlocks(std::vector<int> ids, std::vector<int> bays,
                      bool tight, uint64_t seed, bool strict = false) {
        if (ids.size() != bays.size()) return -2;
        std::vector<int> asg(pb.n, -1);
        for (size_t t = 0; t < ids.size(); t++) asg[ids[t]] = bays[t];
        for (int i : ids)
            if (sol.placed[i]) repair::removeBlock(ctx, i);
        std::vector<int> order = ids;
        std::sort(order.begin(), order.end(),
                  [&](int a, int b){ return pb.D[a] < pb.D[b]; });
        const int* fbPrev2 = ctx.forcedBay;
        ctx.forcedBay = asg.data();
        ctx.rng.seed(seed);
        int placedN = 0;
        for (int A : order) {
            int eCap = tight ? (pb.D[A] - pb.P[A]) : -1;
            bool ok = false;
            if (!tight || eCap >= pb.R[A])
                ok = repair::insertGreedy(ctx, A, eCap);
            if (!ok && strict) {
                // strict: tight-only realization. A loose fallback parks
                // movers very late (measured: candidates land 12x worse),
                // so a single tight failure voids the candidate instead.
                ctx.forcedBay = fbPrev2;
                return -1;
            }
            if (!ok && tight)
                ok = repair::insertGreedy(ctx, A, -1);
            if (!ok) {
                asg[A] = -1;
                ok = repair::insertGreedy(ctx, A, -1);
            }
            if (ok) placedN++;
        }
        ctx.forcedBay = fbPrev2;
        return placedN;
    }

    // exact micro-translation for legalization: shift block i by (dx,dy)
    // in place, vp-gated for i and its time-overlap neighbours. The checker
    // catches sub-milli float overlaps the integer grid cannot see; a 1-unit
    // nudge separates any <1-unit intersection deterministically.
    bool nudgeBlock(int i, int dx, int dy) {
        if (i < 0 || i >= pb.n || !sol.placed[i]) return false;
        int ox = sol.x[i], oy = sol.y[i];
        oc.stamp(pb, sol, i, false);
        sol.x[i] = ox + dx; sol.y[i] = oy + dy;
        bool ok = core::vpConservative(pb, sol, i);
        if (ok) {
            for (int k : sol.byBay[sol.bay[i]]) {
                if (k == i || !sol.placed[k]) continue;
                if (!(sol.e[k] < sol.f[i] && sol.e[i] < sol.f[k])) continue;
                if (!core::vpConservative(pb, sol, k)) { ok = false; break; }
            }
        }
        if (ok) { oc.stamp(pb, sol, i, true); return true; }
        sol.x[i] = ox; sol.y[i] = oy;
        oc.stamp(pb, sol, i, true);
        return false;
    }

    bool moveBlock(int i, int targetBay, bool tight, uint64_t seed) {
        if (!sol.placed[i]) return false;
        int oldBay=sol.bay[i], oldOri=sol.ori[i], oldX=sol.x[i], oldY=sol.y[i],
            oldE=sol.e[i], oldF=sol.f[i];
        repair::removeBlock(ctx, i);
        int fb[1]; std::vector<int> asg(pb.n, -1); asg[i]=targetBay;
        const int* fbPrev = ctx.forcedBay;
        ctx.forcedBay = asg.data();
        ctx.rng.seed(seed);
        int eCap = tight ? (pb.D[i] - pb.P[i]) : -1;
        bool ok = (!tight || eCap >= pb.R[i]) &&
                  repair::insertGreedy(ctx, i, eCap);
        ctx.forcedBay = fbPrev;
        (void)fb;
        if (!ok) {
            sol.bay[i]=oldBay; sol.ori[i]=oldOri; sol.x[i]=oldX; sol.y[i]=oldY;
            sol.e[i]=oldE; sol.f[i]=oldF; sol.placed[i]=1;
            sol.addIdx(i);
            oc.stamp(pb, sol, i, true);
            return false;
        }
        return true;
    }
    void setSolution(std::vector<int> flat) {
        // flat: n*7 = bay,ori,x,y,e,f,placed
        for (int i = 0; i < pb.n; i++) {
            const int* b = &flat[i*7];
            sol.bay[i]=b[0]; sol.ori[i]=b[1]; sol.x[i]=b[2]; sol.y[i]=b[3];
            sol.e[i]=b[4]; sol.f[i]=b[5]; sol.placed[i]=(uint8_t)b[6];
        }
        sol.rebuildIdx(pb.m);
    }
    std::vector<int> getSolution() {
        std::vector<int> out; out.reserve(pb.n*7);
        for (int i = 0; i < pb.n; i++) {
            out.push_back(sol.bay[i]); out.push_back(sol.ori[i]);
            out.push_back(sol.x[i]);   out.push_back(sol.y[i]);
            out.push_back(sol.e[i]);   out.push_back(sol.f[i]);
            out.push_back((int)sol.placed[i]);
        }
        return out;
    }
    std::vector<double> objective() {
        auto o = core::evaluate(pb, sol);
        return {o.z1, o.z2, o.z3, o.total};
    }
};

PYBIND11_MODULE(sscore, m) {
    py::class_<Engine>(m, "Engine")
        .def(py::init<>())
        .def("init_problem", &Engine::initProblem)
        .def("add_orient", &Engine::addOrient)
        .def("set_block", &Engine::setBlock)
        .def("finalize_load", &Engine::finalizeLoad)
        .def("get_block", &Engine::getBlock)
        .def("vp_ok", &Engine::vpOK)
        .def("prof_counters", &Engine::profCounters)
        .def("audit_state", &Engine::auditState)
        .def("build_occ", &Engine::buildOcc)
        .def("set_event_local", &Engine::setEventLocal)
        .def("set_nest_anchors", &Engine::setNestAnchors)
        .def("set_diag_no_crane", &Engine::setDiagNoCrane)
        .def("set_wave_grid", &Engine::setWaveGrid)
        .def("set_assignment_lock", &Engine::setAssignmentLock)
        .def("set_score_local", &Engine::setScoreLocal)
        .def("run_alns", &Engine::runALNS, pybind11::arg("seconds"), pybind11::arg("qlo"), pybind11::arg("qhi"), pybind11::arg("T0mul"), pybind11::arg("cooling"), pybind11::arg("seed"), pybind11::arg("auditEvery") = 0, pybind11::arg("hcFrac") = 0.75, pybind11::arg("bigQProb") = 0.04, pybind11::arg("opMask") = 255)
        .def("run_portfolio", &Engine::runPortfolio, pybind11::arg("threads"), pybind11::arg("seconds"), pybind11::arg("rounds"), pybind11::arg("qlo"), pybind11::arg("qhi"), pybind11::arg("T0mul"), pybind11::arg("cooling"), pybind11::arg("seed"), pybind11::arg("multiStart") = 1)
        .def("reconstruct_assigned", &Engine::reconstructAssigned)
        .def("move_block", &Engine::moveBlock)
        .def("nudge_block", &Engine::nudgeBlock)
        .def("migrate_blocks", &Engine::migrateBlocks,
             pybind11::arg("ids"), pybind11::arg("bays"),
             pybind11::arg("tight"), pybind11::arg("seed"),
             pybind11::arg("strict") = false)
        .def("compact_left", &Engine::compactLeft)
        .def("compact_left_replace", &Engine::compactLeftReplace)
        .def("reconstruct_ordered", &Engine::reconstructOrdered)
        .def("reconstruct_targeted", &Engine::reconstructTargeted,
             pybind11::arg("asg"), pybind11::arg("order"),
             pybind11::arg("eCaps"), pybind11::arg("seed"),
             pybind11::arg("eMins") = std::vector<int>(),
             pybind11::arg("ladder") = 0)
        .def("get_last_missed", &Engine::getLastMissed)
        .def("set_solution", &Engine::setSolution)
        .def("get_solution", &Engine::getSolution)
        .def("objective", &Engine::objective);
}

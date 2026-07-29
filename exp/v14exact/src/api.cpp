// api.cpp -- the Engine: problem loading, the thread portfolio, the
// reconstruction entry points, and the two micro-repairs the checker gate
// uses. Everything the shipped pipeline calls lives here; the pybind block at
// the bottom is grouped into "shipped path" and "experiment tools" so the API
// surface states which is which.
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include "alns.hpp"
#include "exact.hpp"
#ifdef _OPENMP
#include <omp.h>
#endif
namespace py = pybind11;
using VV = std::vector<std::vector<std::pair<double,double>>>;

struct Engine {
    core::Problem pb;
    core::Sol sol;
    occ::OccIndex oc;
    repair::Ctx ctx;
    int horizon_ = 0;

    // ------------------------------------------------------------- loading
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
            g.lbb.push_back(b);                          // per-layer caches
            g.ip.push_back(geom::interiorPoint(p));
            g.layers.push_back(std::move(p));
        }
        g.bb = bb;
        pb.geo[i].orients.push_back(std::move(g));
    }

    // occupancy grid over [0, horizon); stamps whatever is already placed
    void buildOcc(int horizon) {
        horizon_ = horizon;
        sol.rebuildIdx(pb.m);           // defensive: never trust caller sync
        oc.build(pb, horizon);
        for (int i = 0; i < pb.n; i++)
            if (sol.placed[i]) oc.stamp(pb, sol, i, true);
        ctx.pb = &pb; ctx.s = &sol; ctx.oc = &oc; ctx.horizon = horizon;
    }

    // ----------------------------------------------------- solution access
    void setBlock(int i, int bay, int ori, int x, int y, int e, int f, bool placed) {
        sol.bay[i]=bay; sol.ori[i]=ori; sol.x[i]=x; sol.y[i]=y;
        sol.e[i]=e; sol.f[i]=f; sol.placed[i]=placed?1:0;
    }
    void finalizeLoad() { sol.rebuildIdx(pb.m); }
    std::vector<int> getBlock(int i) {
        return {sol.bay[i], sol.ori[i], sol.x[i], sol.y[i], sol.e[i], sol.f[i],
                (int)sol.placed[i]};
    }
    void setSolution(std::vector<int> flat) {   // flat: n*7 = bay,ori,x,y,e,f,placed
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
    bool vpOK(int A) { return core::vpConservative(pb, sol, A); }
    std::vector<int> auditState() {
        int bb = -1; int code = alns::audit(pb, sol, &bb);
        return {code, bb};
    }

    // assignment lock: persistent forcedBay for the whole engine -> repairs
    // keep every block in its locked bay, freezing Z2/Z3 at the locked
    // assignment's values so the search optimizes pure Z1 geometry.
    std::vector<int> lockVec;
    // insert ONE unplaced block with an entry cap and an optional forced
    // bay -- the primitive for chasing an external plan (plan seeds,
    // tools/plan_pack.py).
    bool insertCapped(int A, int eCap, int bay) {
        std::vector<int> fbv(pb.n, -1);
        if (bay >= 0) fbv[A] = bay;
        const int* save = ctx.forcedBay;
        ctx.forcedBay = fbv.data();
        bool ok = repair::insertGreedy(ctx, A, eCap);
        ctx.forcedBay = save;
        return ok;
    }
    void setAssignmentLock(std::vector<int> v) {
        lockVec = std::move(v);
        ctx.forcedBay = lockVec.empty() ? nullptr : lockVec.data();
    }

    // ------------------------------------------------------------ annealing
    // single-arm anneal on the engine's own state: the experiment instrument
    // (arm-level A/B needs one thread, one parameter set).
    std::vector<double> runALNS(double seconds, double qlo, double qhi,
                                double T0mul, double cooling, uint64_t seed,
                                int auditEvery = 0, double hcFrac = 0.75,
                                double bigQProb = 0.04, int opMask = 0xFF,
                                long maxIters = 0) {
        alns::RunParams rp;
        rp.qloF = qlo; rp.qhiF = qhi; rp.T0mul = T0mul; rp.cooling = cooling;
        rp.hcFrac = hcFrac; rp.bigQProb = bigQProb;
        rp.opMask = (uint32_t)opMask;
        rp.maxIters = maxIters;
        auto st = alns::run(ctx, seconds, rp, seed, auditEvery);
        return {(double)st.iters, (double)st.accepted, (double)st.improved,
                (double)st.failed, st.best, (double)st.badIter,
                (double)st.badKind, (double)st.badPhase};
    }

    // the production search: N threads anneal in parallel; per-thread
    // parameters split exploit/explore (removal size, start temperature,
    // pure-SA vs hill-climb tail, op subset); global best wins. rounds>1
    // restarts every thread from the round's global best.
    //
    // `starts` (optional): per-thread round-0 starting solutions, a BASIN
    // HEDGE. Measured: which basin a run lands in swings an instance ~10%
    // (max 80%) between draws, and a better-objective seed can anneal WORSE
    // (p30 cross-seed: -13% seed -> +9.8% final, 6/6 arms). Betting all
    // threads on the single best seed maximises that lottery's variance;
    // starting them from DISTINCT seeds takes a max over basins instead.
    // starts[tid % k]: a flat n*7 solution, or an empty vector meaning
    // "construct fresh" (noisy-EDD). Empty `starts` = legacy behavior
    // (all from the engine's solution, odd threads construct).
    std::vector<double> runPortfolio(int threads, double seconds, int rounds,
                                     double qlo, double qhi, double T0mul,
                                     double cooling, uint64_t seed,
                                     int multiStart = 1,
                                     std::vector<std::vector<int>> starts = {}) {
        core::Sol globalBest = sol;
        double bestObj = core::evaluate(pb, sol).total;
        long totIters = 0;
        int winnerTid = -1;                       // -1: no thread beat the seed
        std::vector<double> threadBest((size_t)threads, -1.0);
        candSols.assign((size_t)threads, {});     // per-thread candidates
        rounds = std::max(1, rounds);
        double perRound = seconds / rounds;
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
                core::Sol s2 = roundStart;
                occ::OccIndex oc2; oc2.build(pb, horizon_);
                repair::Ctx c2; c2.pb = &pb; c2.s = &s2; c2.oc = &oc2;
                c2.horizon = horizon_;
                c2.forcedBay = ctx.forcedBay;   // assignment lock -> threads
                bool constructed = false;
                if (r == 0 && !starts.empty()) {
                    const auto& st0 = starts[tid % (int)starts.size()];
                    if (st0.size() == (size_t)pb.n * 7) {
                        for (int i = 0; i < pb.n; i++) {
                            const int* b = &st0[i*7];
                            s2.bay[i]=b[0]; s2.ori[i]=b[1]; s2.x[i]=b[2];
                            s2.y[i]=b[3];  s2.e[i]=b[4];   s2.f[i]=b[5];
                            s2.placed[i]=(uint8_t)b[6];
                        }
                        s2.rebuildIdx(pb.m);
                    } else {              // empty slot = construct fresh
                        constructed = alns::constructInitial(
                            c2, seed + 77ULL * (tid + 1));
                        if (!constructed) { s2 = roundStart; oc2.clearBits(); }
                    }
                } else if (r == 0 && multiStart && (tid % 2 == 1)) {
                    // legacy: odd threads restart from a fresh noisy-EDD
                    // construction in round 0
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
                // judgment screeners ([유산] re-judgment): env-gated persona
                // overrides, inert when unset (grading server).
                static const double HCF = [] { const char* v =
                    getenv("OGC_HCFRAC"); return v ? atof(v) : -1.0; }();
                static const double BQ = [] { const char* v =
                    getenv("OGC_BIGQ"); return v ? atof(v) : -1.0; }();
                rp.hcFrac  = HCF >= 0 ? HCF
                             : ((tid % 2 == 0) ? 1.0 : 0.75); // half pure-SA
                rp.bigQProb = (tid % 2 == 0) ? 0.0
                              : (BQ >= 0 ? BQ : 0.06);
                rp.opMask  = (tid % 2 == 0) ? 0x1F : 0x3FF; // exploit / explore+ejections
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
                #pragma omp critical
                {
                    totIters += st.iters;
                    double o2 = core::evaluate(pb, s2).total;
                    threadBest[tid] = o2;
                    candSols[tid].reserve((size_t)pb.n * 7);
                    for (int i = 0; i < pb.n; i++) {
                        candSols[tid].push_back(s2.bay[i]);
                        candSols[tid].push_back(s2.ori[i]);
                        candSols[tid].push_back(s2.x[i]);
                        candSols[tid].push_back(s2.y[i]);
                        candSols[tid].push_back(s2.e[i]);
                        candSols[tid].push_back(s2.f[i]);
                        candSols[tid].push_back((int)s2.placed[i]);
                    }
                    if (o2 < bestObj - 1e-9) {
                        bestObj = o2; globalBest = s2; winnerTid = tid;
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
        // [totIters, bestObj, winnerTid, perThreadBest...] -- winnerTid tells
        // which start (seed slot / construction) actually won the merge:
        // the observable that decides future portfolio composition.
        std::vector<double> out = {(double)totIters, bestObj, (double)winnerTid};
        out.insert(out.end(), threadBest.begin(), threadBest.end());
        return out;
    }

    // ------------------------------------------------------ reconstruction
    // rebuild from empty with a fixed bay assignment and an EXPLICIT
    // insertion order (order-space search: construction quality dominates on
    // heavy instances). tight=true caps every entry at D-P (zero tardiness by
    // construction). Returns -1 on success, else the block that failed; the
    // previous solution is restored on failure.
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
        if (bad >= 0) sol = backup;
        oc.build(pb, horizon_);
        for (int i = 0; i < pb.n; i++)
            if (sol.placed[i]) oc.stamp(pb, sol, i, true);
        return bad >= 0 ? bad : -1;
    }

    // rebuild from empty with a fixed bay assignment, least-slack-first order
    // (big blocks break ties). The realizability probe for assignment
    // experiments: tight=true asks "does this assignment admit a
    // zero-tardiness packing?"
    int reconstructAssigned(std::vector<int> asg, bool tight, uint64_t seed) {
        if ((int)asg.size() != pb.n) return -2;
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
        return reconstructOrdered(std::move(asg), std::move(order), tight, seed);
    }

    // ------------------------------------------------------- micro-repairs
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

    // shift block i's stay in time: entry += de, exit += df (de<=df keeps
    // duration >= P legal -- a block MAY remain past its processing time).
    // vp-gated for i and every neighbour overlapping the old or new stay.
    // The gate's cheapest structural rescue: an entry/exit obstructed at an
    // exact-touch instant usually clears one day later at zero geometric
    // cost, where a relocation could park the block arbitrarily late.
    bool retimeBlock(int i, int de, int df) {
        if (i < 0 || i >= pb.n || !sol.placed[i] || (de == 0 && df == 0))
            return false;
        int oe = sol.e[i], of = sol.f[i];
        int ne = oe + de, nf = of + df;
        if (ne < pb.R[i] || nf - ne < pb.P[i] || nf > horizon_) return false;
        oc.stamp(pb, sol, i, false);
        sol.e[i] = ne; sol.f[i] = nf;
        bool ok = core::vpConservative(pb, sol, i);
        if (ok) {
            int lo = std::min(oe, ne), hi = std::max(of, nf);
            for (int k : sol.byBay[sol.bay[i]]) {
                if (k == i || !sol.placed[k]) continue;
                if (!(sol.e[k] < hi && lo < sol.f[k])) continue;
                if (!core::vpConservative(pb, sol, k)) { ok = false; break; }
            }
        }
        if (ok) { oc.stamp(pb, sol, i, true); return true; }
        sol.e[i] = oe; sol.f[i] = of;
        oc.stamp(pb, sol, i, true);
        return false;
    }

    // move one block to a target bay (tight: entry <= D-P). Restores the
    // block on failure.
    bool moveBlock(int i, int targetBay, bool tight, uint64_t seed) {
        if (!sol.placed[i]) return false;
        int oldBay=sol.bay[i], oldOri=sol.ori[i], oldX=sol.x[i], oldY=sol.y[i],
            oldE=sol.e[i], oldF=sol.f[i];
        repair::removeBlock(ctx, i);
        std::vector<int> asg(pb.n, -1); asg[i]=targetBay;
        const int* fbPrev = ctx.forcedBay;
        ctx.forcedBay = asg.data();
        ctx.rng.seed(seed);
        int eCap = tight ? (pb.D[i] - pb.P[i]) : -1;
        bool ok = (!tight || eCap >= pb.R[i]) &&
                  repair::insertGreedy(ctx, i, eCap);
        ctx.forcedBay = fbPrev;
        if (!ok) {
            sol.bay[i]=oldBay; sol.ori[i]=oldOri; sol.x[i]=oldX; sol.y[i]=oldY;
            sol.e[i]=oldE; sol.f[i]=oldF; sol.placed[i]=1;
            sol.addIdx(i);
            oc.stamp(pb, sol, i, true);
            return false;
        }
        return true;
    }

    int compactLeft(int maxPasses) { return alns::compactLeft(ctx, maxPasses); }
    int windowExact(double seconds, long nodeBudget = 60000) {
        return exact::windowExact(ctx, seconds, nodeBudget);
    }

    // The portfolio's candidate SET, objective-ascending. The checker gate
    // consumes candidates in order: whether a layout carries a measure-zero
    // exact-touch artifact is an independent lottery per candidate, so
    // P(all fail) decays multiplicatively -- and a rescued run returns the
    // runner-up's quality instead of a fresh-seed's (measured: p22@T60 s8
    // returned 1.47M while a clean 743k candidate sat discarded).
    std::vector<std::vector<int>> candSols;
    std::vector<std::vector<int>> getCandidates() {
        std::vector<std::pair<double,int>> ord;
        for (size_t t = 0; t < candSols.size(); t++) {
            if (candSols[t].empty()) continue;
            // objective of candidate t
            core::Sol tmp; tmp.init(pb.n, pb.m);
            const auto& f = candSols[t];
            for (int i = 0; i < pb.n; i++) {
                const int* b = &f[i*7];
                tmp.bay[i]=b[0]; tmp.ori[i]=b[1]; tmp.x[i]=b[2]; tmp.y[i]=b[3];
                tmp.e[i]=b[4]; tmp.f[i]=b[5]; tmp.placed[i]=(uint8_t)b[6];
            }
            ord.push_back({core::evaluate(pb, tmp).total, (int)t});
        }
        std::sort(ord.begin(), ord.end());
        std::vector<std::vector<int>> out;
        for (auto& pr : ord) out.push_back(candSols[pr.second]);
        return out;
    }
};

PYBIND11_MODULE(sscore, m) {
    py::class_<Engine>(m, "Engine")
        .def(py::init<>())
        // ---- shipped path (what myalgorithm.py calls) ----
        .def("init_problem", &Engine::initProblem)
        .def("add_orient", &Engine::addOrient)
        .def("build_occ", &Engine::buildOcc)
        .def("reconstruct_ordered", &Engine::reconstructOrdered)
        .def("run_portfolio", &Engine::runPortfolio,
             py::arg("threads"), py::arg("seconds"), py::arg("rounds"),
             py::arg("qlo"), py::arg("qhi"), py::arg("T0mul"),
             py::arg("cooling"), py::arg("seed"), py::arg("multiStart") = 1,
             py::arg("starts") = std::vector<std::vector<int>>{})
        .def("get_block", &Engine::getBlock)
        .def("get_candidates", &Engine::getCandidates)
        .def("objective", &Engine::objective)
        .def("nudge_block", &Engine::nudgeBlock)
        .def("retime_block", &Engine::retimeBlock)
        .def("move_block", &Engine::moveBlock)
        // ---- experiment tools (probes, arm-level A/B, state I/O) ----
        .def("run_alns", &Engine::runALNS,
             py::arg("seconds"), py::arg("qlo"), py::arg("qhi"),
             py::arg("T0mul"), py::arg("cooling"), py::arg("seed"),
             py::arg("auditEvery") = 0, py::arg("hcFrac") = 0.75,
             py::arg("bigQProb") = 0.04, py::arg("opMask") = 255,
             py::arg("maxIters") = 0)
        .def("reconstruct_assigned", &Engine::reconstructAssigned)
        .def("insert_capped", &Engine::insertCapped)
        .def("set_assignment_lock", &Engine::setAssignmentLock)
        .def("compact_left", &Engine::compactLeft)
        .def("window_exact", &Engine::windowExact,
             py::arg("seconds"), py::arg("nodeBudget") = 60000)
        .def("set_block", &Engine::setBlock)
        .def("finalize_load", &Engine::finalizeLoad)
        .def("set_solution", &Engine::setSolution)
        .def("get_solution", &Engine::getSolution)
        .def("vp_ok", &Engine::vpOK)
        .def("audit_state", &Engine::auditState);
}

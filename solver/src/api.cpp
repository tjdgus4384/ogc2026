// api.cpp -- the Engine: problem loading, the thread portfolio, the
// reconstruction entry points, and the two micro-repairs the checker gate
// uses. Everything the shipped pipeline calls lives here; the pybind block at
// the bottom is grouped into "shipped path" and "experiment tools" so the API
// surface states which is which.
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include "alns.hpp"
#include <limits>
#include <map>
#include <stdexcept>
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
        // true footprint = max polygon area over layers (layers stack
        // vertically, so the footprint is the widest single layer)
        double pa = 0.0;
        for (auto& P : g.layers) {
            double s2 = 0.0;
            for (size_t k = 0; k < P.size(); k++) {
                const auto& p1 = P[k];
                const auto& p2 = P[(k + 1) % P.size()];
                s2 += p1.x * p2.y - p2.x * p1.y;
            }
            pa = std::max(pa, std::fabs(s2) * 0.5);
        }
        g.parea = pa;
        pb.geo[i].orients.push_back(std::move(g));
    }

    // derive the instance's achievable polygon density once all
    // geometry is loaded (called from build())
    void computeFill() {
        double sp = 0.0, sb = 0.0;
        for (int i = 0; i < pb.n; i++) {
            double bp = 1e18, bb2 = 1e18;
            for (const auto& g : pb.geo[i].orients) {
                double a = (g.bb.x1-g.bb.x0)*(g.bb.y1-g.bb.y0);
                if (a < bb2) { bb2 = a; bp = g.parea; }
            }
            if (bb2 < 1e17) { sp += bp; sb += bb2; }
        }
        double fill = (sb > 0.0) ? std::min(1.0, sp / sb) : 1.0;
        // The cap can never sit below what the instance REQUIRES: on
        // any day, blocks whose [release, due) window covers that day
        // must be somewhere, so their polygon area over total bay area
        // is a hard lower bound on the density a punctual board needs.
        // Saturated instances (P3-class) therefore relax the screener
        // automatically instead of having their dense packings
        // rejected, while roomy ones keep the tight, fast filter.
        double capArea = 0.0;
        for (int j = 0; j < pb.m; j++) capArea += pb.bayW[j]*pb.bayH[j];
        int Tmax = 0;
        for (int i = 0; i < pb.n; i++) Tmax = std::max(Tmax, pb.D[i]);
        std::vector<double> need((size_t)Tmax + 2, 0.0);
        for (int i = 0; i < pb.n; i++) {
            double a = 1e18;
            for (const auto& g : pb.geo[i].orients)
                a = std::min(a, g.parea > 0 ? g.parea :
                             (g.bb.x1-g.bb.x0)*(g.bb.y1-g.bb.y0));
            int lo = pb.R[i], hi = std::max(pb.R[i], pb.D[i] - pb.P[i]);
            for (int t = lo; t <= hi && t <= Tmax; t++) need[t] += a;
        }
        double req = 0.0;
        pb.dayLoad.assign((size_t)Tmax + 2, 0.0);
        for (int t = 0; t <= Tmax; t++) {
            double u = need[t] / std::max(1e-9, capArea);
            pb.dayLoad[t] = u;
            req = std::max(req, u);
        }
        pb.fillRatio = std::min(1.0, std::max(fill, req));
    }

    // occupancy grid over [0, horizon); stamps whatever is already placed
    void buildOcc(int horizon) {
        horizon_ = horizon;
        if (pb.fillRatio >= 1.0) computeFill();
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
    // Intra-day ENTRY rank per block (-1 = unplaced). vpConservative rule 2b
    // only proves that SOME order of a same-day, same-bay batch is legal; the
    // emitted solution has to actually list one, and it must be the same
    // geometry that proved it -- so the kernel hands the order to the driver
    // rather than have _recs_to_sol re-derive it in Python.
    std::vector<int> entryRanks() {
        std::vector<int> rank(pb.n, -1);
        std::map<std::pair<int, int>, std::vector<int>> groups;
        for (int i = 0; i < pb.n; i++)
            if (sol.placed[i]) groups[{sol.bay[i], sol.e[i]}].push_back(i);
        for (auto& kv : groups) {
            std::vector<int>& g = kv.second;
            std::sort(g.begin(), g.end());
            const int m = (int)g.size();
            if (m == 1) { rank[g[0]] = 0; continue; }
            std::vector<int> order;
            if (!core::entryOrderAt(pb, sol, kv.first.first,
                                    kv.first.second, order)) {
                // A legal engine state cannot reach this branch.  Preserve a
                // total output order so the checker gate, not a driver crash,
                // remains the final authority if a future operator regresses.
                order = g;
            }
            for (int r = 0; r < m; r++) rank[order[r]] = r;
        }
        return rank;
    }
    // Canonical checker-legal EXIT order.  Later entry leaves first; blocks
    // sharing an entry day leave in reverse of the actual ENTRY topological
    // order.  The latter is the missing half of the LIFO rule: the later ENTRY
    // cleared every earlier peer on descent, and exit uses identical geometry.
    std::vector<int> exitRanks() {
        std::vector<int> rank(pb.n, -1);
        const std::vector<int> erank = entryRanks();
        std::map<std::pair<int, int>, std::vector<int>> groups;
        for (int i = 0; i < pb.n; i++)
            if (sol.placed[i]) groups[{sol.bay[i], sol.f[i]}].push_back(i);
        for (auto& kv : groups) {
            std::vector<int>& g = kv.second;
            std::sort(g.begin(), g.end(), [&](int a, int b) {
                if (sol.e[a] != sol.e[b]) return sol.e[a] > sol.e[b];
                if (erank[a] != erank[b]) return erank[a] > erank[b];
                return a < b;
            });
            for (size_t r = 0; r < g.size(); r++) rank[g[r]] = (int)r;
        }
        return rank;
    }
    // Development differential only: independently search an EXIT order and
    // compare it with the closed-form LIFO construction above.
    std::vector<int> greedyExitRanks() {
        std::vector<int> rank(pb.n, -1), order, times;
        for (int bay = 0; bay < pb.m; bay++) {
            times.clear();
            for (int i : sol.byBay[bay])
                if (sol.placed[i]) times.push_back(sol.f[i]);
            std::sort(times.begin(), times.end());
            times.erase(std::unique(times.begin(), times.end()), times.end());
            for (int t : times) {
                if (!core::exitOrderAt(pb, sol, bay, t, order)) return {};
                for (size_t r = 0; r < order.size(); r++) rank[order[r]] = (int)r;
            }
        }
        return rank;
    }
    std::vector<double> objective() {
        auto o = core::evaluate(pb, sol);
        return {o.z1, o.z2, o.z3, o.total};
    }
    bool vpOK(int A) {
        if (core::vpConservative(pb, sol, A)) return true;
        return knobs::exitTwin() &&
               core::vpFlexibleExits(pb, sol, A);
    }
    std::vector<int> auditState(bool flexibleExits = false) {
        int bb = -1; int code = alns::audit(pb, sol, &bb, flexibleExits);
        return {code, bb};
    }

    // assignment lock: persistent forcedBay for the whole engine -> repairs
    // keep every block in its locked bay, freezing Z2/Z3 at the locked
    // assignment's values so the search optimizes pure Z1 geometry.
    std::vector<int> capVec;
    std::vector<int> lockVec;
    std::vector<int> checkpointFlat_;          // experimental in-run snapshot
    int checkpointOfficial_ = 0;
    std::vector<int> archiveCheckpointFlat_;   // archive-arm result at snapshot
    int archiveCheckpointOfficial_ = 0;
    std::vector<int> controlBestFlat_;          // final unmodified control arm
    double controlBestObjective_ = 0.0;
    std::string finalRngState_;                 // complete serialized mt19937
    std::vector<int> archiveSolutionFlat_;      // final winning sidecar only
    int archiveReturned_ = 0;
    int returnedOfficial_ = 0;
    int lastChunks_ = 0;                 // rounds completed by the last
    int lastChunks() const { return lastChunks_; }   // portfolio call
    double phaseNs_[4] = {0, 0, 0, 0};   // remove/insert/eval/samples
    double opUsed_[10] = {0}, opImp_[10] = {0};
    double totIters_ = 0, totFailed_ = 0;
    double totExitTwinTests_ = 0, totExitTwinPass_ = 0;
    double totExitTwinCompared_ = 0, totExitTwinDominant_ = 0;
    double totExitTwinRescued_ = 0;
    double totPhase2Calls_ = 0, totPhase2Success_ = 0;
    long reconstructPhase2Calls_ = 0, reconstructPhase2Success_ = 0;
    std::vector<double> lastStats() const {
        return {totIters_, totFailed_, totExitTwinTests_, totExitTwinPass_,
                totExitTwinCompared_, totExitTwinDominant_,
                totExitTwinRescued_, totPhase2Calls_, totPhase2Success_};
    }
    std::vector<long> reconstructStats() const {
        return {reconstructPhase2Calls_, reconstructPhase2Success_};
    }
    std::vector<double> lastOps() const {
        std::vector<double> v;
        for (int k = 0; k < 10; k++) v.push_back(opUsed_[k]);
        for (int k = 0; k < 10; k++) v.push_back(opImp_[k]);
        return v;
    }
    std::vector<double> lastPhase() const {
        return {phaseNs_[0], phaseNs_[1], phaseNs_[2], phaseNs_[3]};
    }
    std::vector<int> flattenSolution(const core::Sol& src) const {
        std::vector<int> out;
        if (src.placed.empty()) return out;
        out.reserve((size_t)pb.n * 7);
        for (int i = 0; i < pb.n; i++) {
            out.push_back(src.bay[i]);
            out.push_back(src.ori[i]);
            out.push_back(src.x[i]);
            out.push_back(src.y[i]);
            out.push_back(src.e[i]);
            out.push_back(src.f[i]);
            out.push_back((int)src.placed[i]);
        }
        return out;
    }
    // insert ONE unplaced block with an entry cap and an optional forced
    // bay -- the primitive for chasing an external plan (plan seeds,
    // tools/plan_pack.py).
    bool insertCapped(int A, int eCap, int bay) {
        std::vector<int> fbv;
        if (knobs::keepAssignmentLock() && ctx.forcedBay)
            fbv.assign(ctx.forcedBay, ctx.forcedBay + pb.n);
        else
            fbv.assign(pb.n, -1);
        if (bay >= 0 && !(knobs::keepAssignmentLock() && ctx.forcedBay &&
                          ctx.forcedBay[A] >= 0))
            fbv[A] = bay;
        const int* save = ctx.forcedBay;
        ctx.forcedBay = fbv.data();
        bool ok = repair::insertGreedy(ctx, A, eCap);
        ctx.forcedBay = save;
        return ok;
    }
    // enumerate up to k vp-feasible placements of UNPLACED block A at its
    // earliest feasible entry per bay (probe primitive for placement-depth
    // experiments): returns flat rows [bay, ori, x, y, e] x k.
    std::vector<int> feasiblePositions(int A, int k, int eMin = 0) {
        std::vector<int> out;
        if (sol.placed[A]) return out;
        std::vector<int> xs, ys, oris;
        for (size_t o = 0; o < pb.geo[A].orients.size(); o++)
            oris.push_back((int)o);
        for (int j = 0; j < pb.m && (int)out.size() / 5 < k; j++) {
            for (int e = std::max(pb.R[A], eMin); e + pb.P[A] <= horizon_; e++) {
                int f = e + pb.P[A];
                occ::OccIndex::Snap sn;
                oc.buildSnap(j, e, f, sn);
                thread_local std::vector<repair::RB> rbs;
                repair::rbCollect(ctx, j, e, f, rbs);
                bool any = false;
                for (int oi : oris) {
                    repair::cornerSetsRB(ctx, A, oi, j, rbs, xs, ys);
                    for (int y : ys) { for (int x : xs) {
                        if (oc.hitsSnap(sn, A, oi, x, y)) continue;
                        sol.bay[A]=j; sol.ori[A]=oi; sol.x[A]=x;
                        sol.y[A]=y; sol.e[A]=e; sol.f[A]=f;
                        sol.placed[A]=1;
                        bool ok = core::vpConservative(pb, sol, A);
                        sol.placed[A]=0;
                        if (ok) {
                            out.insert(out.end(), {j, oi, x, y, e});
                            any = true;
                            if ((int)out.size() / 5 >= k) return out;
                        }
                    } }
                }
                if (any) break;      // earliest feasible day per bay only
            }
        }
        return out;
    }
    // Raw event-corner vocabulary for the flexible-coalition experiment.
    // Unlike feasiblePositions, this deliberately applies no occupancy mask
    // and no crane entry/exit oracle: a one-way higher-layer obstruction is a
    // relation for the joint timing model, not a reason to erase a geometry.
    // Every placed incumbent peer except A contributes its bbox when its stay
    // overlaps A's minimum-duration window.  Only exact same-layer collisions
    // against those peers are rejected.  Returns [bay,ori,x,y,entry] rows in
    // ori/y/x order.
    std::vector<int> eventCornerCandidates(int A, int bay, int entry,
                                           int cap) {
        const size_t n = (size_t)std::max(0, pb.n);
        const bool stateShape =
            pb.n > 0 && pb.m > 0 && pb.geo.size() == n &&
            pb.R.size() == n && pb.P.size() == n &&
            pb.bayW.size() == (size_t)pb.m &&
            pb.bayH.size() == (size_t)pb.m &&
            sol.placed.size() == n && sol.bay.size() == n &&
            sol.ori.size() == n && sol.x.size() == n &&
            sol.y.size() == n && sol.e.size() == n && sol.f.size() == n &&
            sol.byBay.size() == (size_t)pb.m && ctx.pb == &pb &&
            ctx.s == &sol && ctx.oc == &oc && ctx.horizon == horizon_ &&
            horizon_ > 0;
        if (!stateShape)
            throw std::logic_error(
                "event_corner_candidates called on an unbuilt engine");
        constexpr int maxCandidateCap = 4096;
        if (A < 0 || A >= pb.n || bay < 0 || bay >= pb.m || cap < 1 ||
            cap > maxCandidateCap)
            throw std::invalid_argument(
                "event_corner_candidates argument is out of range");
        if (pb.P[A] <= 0 || entry < pb.R[A] || entry > horizon_ - pb.P[A])
            throw std::invalid_argument(
                "event_corner_candidates entry is outside release/horizon");
        if (pb.geo[A].orients.empty())
            throw std::invalid_argument(
                "event_corner_candidates block has no orientation");

        const int exit = entry + pb.P[A];
        std::vector<int> residents;
        std::vector<repair::RB> rbs;
        residents.reserve(sol.byBay[bay].size());
        rbs.reserve(sol.byBay[bay].size());
        for (int k : sol.byBay[bay]) {
            if (k == A) continue;
            if (k < 0 || k >= pb.n || !sol.placed[k] || sol.bay[k] != bay ||
                pb.P[k] <= 0 ||
                sol.ori[k] < 0 ||
                sol.ori[k] >= (int)pb.geo[k].orients.size() ||
                sol.e[k] < pb.R[k] || sol.f[k] <= sol.e[k] ||
                (long long)sol.f[k] - sol.e[k] < pb.P[k] ||
                sol.f[k] > horizon_)
                throw std::logic_error(
                    "event_corner_candidates found an invalid resident");
            const auto& residentGeometry =
                pb.geo[k].orients[sol.ori[k]];
            if (residentGeometry.layers.empty() ||
                residentGeometry.lbb.size() != residentGeometry.layers.size() ||
                residentGeometry.ip.size() != residentGeometry.layers.size())
                throw std::logic_error(
                    "event_corner_candidates found invalid resident geometry");
            if (!(sol.e[k] < exit && entry < sol.f[k])) continue;
            residents.push_back(k);
            rbs.push_back({residentGeometry.bb.x0 + sol.x[k],
                           residentGeometry.bb.x1 + sol.x[k],
                           residentGeometry.bb.y0 + sol.y[k],
                           residentGeometry.bb.y1 + sol.y[k]});
        }

        crane::ScopedMemoBypass memoBypass(true);
        core::Sol pair;
        pair.init(pb.n, pb.m);
        for (int k : residents) {
            pair.bay[k] = bay; pair.ori[k] = sol.ori[k];
            pair.x[k] = sol.x[k]; pair.y[k] = sol.y[k];
        }
        pair.bay[A] = bay;

        std::vector<int> out, xs, ys;
        for (int oi = 0; oi < (int)pb.geo[A].orients.size(); oi++) {
            const auto& g = pb.geo[A].orients[oi];
            if (g.layers.empty() || g.lbb.size() != g.layers.size() ||
                g.ip.size() != g.layers.size())
                throw std::logic_error(
                    "event_corner_candidates found an invalid orientation");
            repair::cornerSetsRB(ctx, A, oi, bay, rbs, xs, ys);
            pair.ori[A] = oi;
            for (int y : ys) {
                pair.y[A] = y;
                for (int x : xs) {
                    pair.x[A] = x;
                    if (g.bb.x0 + x < -geom::EPS ||
                        g.bb.y0 + y < -geom::EPS ||
                        g.bb.x1 + x > pb.bayW[bay] + geom::EPS ||
                        g.bb.y1 + y > pb.bayH[bay] + geom::EPS)
                        throw std::logic_error(
                            "event_corner_candidates generated an out-of-bay corner");
                    bool rest = false;
                    for (int k : residents)
                        if (core::restOverlap(pb, pair, A, k)) {
                            rest = true;
                            break;
                        }
                    if (rest) continue;
                    out.insert(out.end(), {bay, oi, x, y, entry});
                    if (out.size() / 5 == (size_t)cap) return out;
                }
            }
        }
        return out;
    }
    // vp-verified commit of A at an explicit placement (probe primitive).
    bool placeAt(int A, int bay, int ori, int x, int y, int e) {
        if (sol.placed[A]) return false;
        sol.bay[A]=bay; sol.ori[A]=ori; sol.x[A]=x; sol.y[A]=y;
        sol.e[A]=e; sol.f[A]=e + pb.P[A]; sol.placed[A]=1;
        if (!core::vpConservative(pb, sol, A)) { sol.placed[A]=0; return false; }
        sol.addIdx(A);
        oc.stamp(pb, sol, A, true);
        return true;
    }
    // Compare the shipped fixed-EXIT oracle with the official-checker oracle
    // at one explicit, uncommitted placement.  [fixed, flexible].
    std::vector<int> placementVerdicts(int A, int bay, int ori,
                                       int x, int y, int e) {
        if (A < 0 || A >= pb.n || sol.placed[A] || bay < 0 || bay >= pb.m ||
            ori < 0 || ori >= (int)pb.geo[A].orients.size()) return {};
        sol.bay[A]=bay; sol.ori[A]=ori; sol.x[A]=x; sol.y[A]=y;
        sol.e[A]=e; sol.f[A]=e + pb.P[A]; sol.placed[A]=1;
        const bool fixed = core::vpConservative(pb, sol, A);
        const bool flexible = core::vpFlexibleExits(pb, sol, A);
        sol.placed[A]=0;
        return {(int)fixed, (int)flexible};
    }
    // Commit an explicit placement under flexible same-day EXIT semantics.
    bool placeAtFlexible(int A, int bay, int ori, int x, int y, int e) {
        if (sol.placed[A]) return false;
        sol.bay[A]=bay; sol.ori[A]=ori; sol.x[A]=x; sol.y[A]=y;
        sol.e[A]=e; sol.f[A]=e + pb.P[A]; sol.placed[A]=1;
        if (!core::vpFlexibleExits(pb, sol, A)) {
            sol.placed[A]=0;
            return false;
        }
        sol.addIdx(A);
        oc.stamp(pb, sol, A, true);
        return true;
    }
    // Exhaust the CURRENT corner vocabulary on [eLo,eHi] and classify every
    // raster-free candidate by the two oracles.  Header:
    // [tested,fixed_ok,flexible_ok,flexible_only,fixed_only,truncated], then
    // flexible-only witnesses [bay,ori,x,y,e].  This isolates semantics from
    // vocabulary: no new coordinates are introduced by the probe.
    std::vector<int> cornerOracleGap(int A, int eLo, int eHi,
                                     int maxWitnesses = 8) {
        std::vector<int> out(6, 0);
        if (A < 0 || A >= pb.n || sol.placed[A]) return out;
        eLo = std::max(eLo, pb.R[A]);
        eHi = std::min(eHi, horizon_ - pb.P[A] - 1);
        if (eHi < eLo) return out;
        std::vector<int> xs, ys;
        thread_local std::vector<repair::RB> rbs;
        thread_local occ::OccIndex::Snap sn;
        int witnesses = 0;
        for (int e = eLo; e <= eHi; e++) {
            const int f = e + pb.P[A];
            for (int bay = 0; bay < pb.m; bay++) {
                oc.buildSnap(bay, e, f, sn);
                repair::rbCollect(ctx, bay, e, f, rbs);
                for (int ori = 0; ori < (int)pb.geo[A].orients.size(); ori++) {
                    repair::cornerSetsRB(ctx, A, ori, bay, rbs, xs, ys);
                    for (int y : ys) for (int x : xs) {
                        if (oc.hitsSnap(sn, A, ori, x, y)) continue;
                        sol.bay[A]=bay; sol.ori[A]=ori; sol.x[A]=x; sol.y[A]=y;
                        sol.e[A]=e; sol.f[A]=f; sol.placed[A]=1;
                        const bool fixed = core::vpConservative(pb, sol, A);
                        const bool flexible = core::vpFlexibleExits(pb, sol, A);
                        sol.placed[A]=0;
                        out[0]++;
                        out[1] += fixed;
                        out[2] += flexible;
                        out[3] += flexible && !fixed;
                        out[4] += fixed && !flexible;
                        if (flexible && !fixed) {
                            out.insert(out.end(), {bay, ori, x, y, e});
                            witnesses++;
                            if (maxWitnesses > 0 && witnesses >= maxWitnesses) {
                                out[5] = 1;
                                return out;
                            }
                        }
                    }
                }
            }
        }
        return out;
    }
    // Exact habitat of the EXIT-order relaxation.  Descending entry time is a
    // legal unwind for unequal entry times, so fixed and flexible can differ
    // only when another block shares BOTH entry and exit with A.  Equivalently,
    // candidate A shares (bay,entry,processing time) with a resident.  Scan
    // only those event cells; this is the production-priced version of the
    // exhaustive diagnostic above.
    std::vector<int> twinOracleGap(int A, int eLo, int eHi,
                                   int maxWitnesses = 32) {
        std::vector<int> out(6, 0);
        if (A < 0 || A >= pb.n || sol.placed[A]) return out;
        eLo = std::max(eLo, pb.R[A]);
        eHi = std::min(eHi, horizon_ - pb.P[A] - 1);
        if (eHi < eLo) return out;

        std::vector<std::pair<int,int>> cells;       // (entry,bay)
        for (int bay = 0; bay < pb.m; bay++)
            for (int k : sol.byBay[bay]) {
                if (!sol.placed[k] || pb.P[k] != pb.P[A]) continue;
                if (eLo <= sol.e[k] && sol.e[k] <= eHi)
                    cells.push_back({sol.e[k], bay});
            }
        std::sort(cells.begin(), cells.end());
        cells.erase(std::unique(cells.begin(), cells.end()), cells.end());

        std::vector<int> xs, ys;
        thread_local std::vector<repair::RB> rbs;
        thread_local occ::OccIndex::Snap sn;
        int witnesses = 0;
        for (auto [e, bay] : cells) {
            const int f = e + pb.P[A];
            oc.buildSnap(bay, e, f, sn);
            repair::rbCollect(ctx, bay, e, f, rbs);
            for (int ori = 0; ori < (int)pb.geo[A].orients.size(); ori++) {
                repair::cornerSetsRB(ctx, A, ori, bay, rbs, xs, ys);
                for (int y : ys) for (int x : xs) {
                    if (oc.hitsSnap(sn, A, ori, x, y)) continue;
                    sol.bay[A]=bay; sol.ori[A]=ori; sol.x[A]=x; sol.y[A]=y;
                    sol.e[A]=e; sol.f[A]=f; sol.placed[A]=1;
                    const bool fixed = core::vpConservative(pb, sol, A);
                    const bool flexible = core::vpFlexibleExits(pb, sol, A);
                    sol.placed[A]=0;
                    out[0]++;
                    out[1] += fixed;
                    out[2] += flexible;
                    out[3] += flexible && !fixed;
                    out[4] += fixed && !flexible;
                    if (flexible && !fixed) {
                        out.insert(out.end(), {bay, ori, x, y, e});
                        witnesses++;
                        if (maxWitnesses > 0 && witnesses >= maxWitnesses) {
                            out[5] = 1;
                            return out;
                        }
                    }
                }
            }
        }
        return out;
    }
    // One monotone completed-board polish in the exact habitat above.  The
    // state changes only when an official-semantic, fixed-oracle-rejected move
    // strictly lowers the true objective.  Running after the stochastic search
    // therefore cannot degrade its incumbent; `seconds` bounds the only cost.
    std::vector<double> pullTwinFlexible(double seconds, int maxMoves = 1) {
        using Clock = std::chrono::steady_clock;
        const auto started = Clock::now();
        const auto deadline = started + std::chrono::duration_cast<Clock::duration>(
            std::chrono::duration<double>(std::max(0.0, seconds)));
        const double startObj = core::evaluate(pb, sol).total;
        double currentObj = startObj;
        long tested = 0, fixedOk = 0, flexibleOk = 0, flexibleOnly = 0;
        long improving = 0, accepted = 0;

        for (int pass = 0; pass < maxMoves && Clock::now() < deadline; pass++) {
            std::vector<int> blocks;
            for (int i = 0; i < pb.n; i++)
                if (sol.placed[i] && sol.e[i] > pb.R[i]) blocks.push_back(i);
            std::sort(blocks.begin(), blocks.end(), [&](int a, int b) {
                const int la = std::max(0, sol.f[a] - pb.D[a]);
                const int lb = std::max(0, sol.f[b] - pb.D[b]);
                if (la != lb) return la > lb;
                return a < b;
            });

            bool moved = false;
            for (int A : blocks) {
                if (Clock::now() >= deadline) break;
                const int oldBay=sol.bay[A], oldOri=sol.ori[A];
                const int oldX=sol.x[A], oldY=sol.y[A];
                const int oldE=sol.e[A], oldF=sol.f[A];
                repair::removeBlock(ctx, A);

                std::vector<std::pair<int,int>> cells;
                for (int bay = 0; bay < pb.m; bay++)
                    for (int k : sol.byBay[bay]) {
                        if (!sol.placed[k] || pb.P[k] != pb.P[A]) continue;
                        if (pb.R[A] <= sol.e[k] && sol.e[k] < oldE)
                            cells.push_back({sol.e[k], bay});
                    }
                std::sort(cells.begin(), cells.end());
                cells.erase(std::unique(cells.begin(), cells.end()), cells.end());

                bool haveBest = false;
                int bestBay=oldBay, bestOri=oldOri, bestX=oldX, bestY=oldY, bestE=oldE;
                double bestObj = currentObj;
                std::vector<int> xs, ys;
                thread_local std::vector<repair::RB> rbs;
                thread_local occ::OccIndex::Snap sn;
                for (auto [e, bay] : cells) {
                    if (Clock::now() >= deadline) break;
                    const int f = e + pb.P[A];
                    oc.buildSnap(bay, e, f, sn);
                    repair::rbCollect(ctx, bay, e, f, rbs);
                    for (int ori = 0; ori < (int)pb.geo[A].orients.size(); ori++) {
                        repair::cornerSetsRB(ctx, A, ori, bay, rbs, xs, ys);
                        for (int y : ys) for (int x : xs) {
                            if (oc.hitsSnap(sn, A, ori, x, y)) continue;
                            sol.bay[A]=bay; sol.ori[A]=ori; sol.x[A]=x; sol.y[A]=y;
                            sol.e[A]=e; sol.f[A]=f; sol.placed[A]=1;
                            const bool fixed = core::vpConservative(pb, sol, A);
                            const bool flexible = core::vpFlexibleExits(pb, sol, A);
                            tested++;
                            fixedOk += fixed;
                            flexibleOk += flexible;
                            flexibleOnly += flexible && !fixed;
                            if (flexible && !fixed) {
                                const double obj = core::evaluate(pb, sol).total;
                                if (obj + 1e-9 < bestObj) {
                                    bestObj=obj; bestBay=bay; bestOri=ori;
                                    bestX=x; bestY=y; bestE=e;
                                    haveBest=true;
                                }
                            }
                            sol.placed[A]=0;
                        }
                    }
                }

                if (haveBest) {
                    sol.bay[A]=bestBay; sol.ori[A]=bestOri;
                    sol.x[A]=bestX; sol.y[A]=bestY;
                    sol.e[A]=bestE; sol.f[A]=bestE + pb.P[A]; sol.placed[A]=1;
                    sol.addIdx(A);
                    oc.stamp(pb, sol, A, true);
                    currentObj = bestObj;
                    improving++;
                    accepted++;
                    moved = true;
                    break;                         // rebuild priority after move
                }

                sol.bay[A]=oldBay; sol.ori[A]=oldOri; sol.x[A]=oldX; sol.y[A]=oldY;
                sol.e[A]=oldE; sol.f[A]=oldF; sol.placed[A]=1;
                sol.addIdx(A);
                oc.stamp(pb, sol, A, true);
            }
            if (!moved) break;
        }
        const double elapsed = std::chrono::duration<double>(Clock::now() - started).count();
        return {(double)tested, (double)fixedOk, (double)flexibleOk,
                (double)flexibleOnly, (double)improving, (double)accepted,
                startObj, currentObj, elapsed};
    }
    // EXACT PAIRWISE CONFLICTS between candidate placements.
    // The CP-SAT sub-solve (tools/cpsat_loop.py) is the one mechanism
    // measured to improve boards that local search provably cannot --
    // 20-30 block neighbourhoods come back OPTIMAL and unchanged, yet
    // random K-subsets re-placed exactly gave P6 -1.5% at K=15 and
    // -2.6% at K=25. Its only blocker is cost, and the cost is not
    // CP-SAT (0.2-0.7s per solve) but building the conflict clauses in
    // python/shapely (2-3s). Every step that made an iteration cheaper
    // moved the verdict: candidates 30 -> 14 -> 8 took the polish from
    // +0.55% to -0.21% to -0.37% against a full anneal. This does the
    // same test with the engine's own geometry, which is where the
    // remaining order of magnitude is.
    // cand = flat [block, bay, ori, x, y, entry] * N.
    // returns flat pairs (a, b) of conflicting candidate indices.
    int horizonValue() const { return horizon_; }
    std::vector<int> pairConflicts(const std::vector<int>& cand) {
        const int N = (int)cand.size() / 6;
        std::vector<int> out;
        for (int a = 0; a < N; a++) {
            const int* A = &cand[a * 6];
            const auto& ga = pb.geo[A[0]].orients[A[2]];
            const int fa = A[5] + pb.P[A[0]];
            for (int b = a + 1; b < N; b++) {
                const int* B = &cand[b * 6];
                if (A[0] == B[0]) continue;      // same block, handled by
                if (A[1] != B[1]) continue;      // exactly-one; other bay
                const int fb = B[5] + pb.P[B[0]];
                if (!(A[5] < fb && B[5] < fa)) continue;   // disjoint stays
                const auto& gb = pb.geo[B[0]].orients[B[2]];
                if (!crane::bboxOverlapOff(ga.bb, A[3], A[4],
                                           gb.bb, B[3], B[4])) continue;
                bool bad = false;
                const int La = (int)ga.layers.size();
                const int Lb = (int)gb.layers.size();
                for (int l = 0; l < std::min(La, Lb) && !bad; l++)
                    bad = crane::overlapsLay(ga, l, A[3], A[4],
                                             gb, l, B[3], B[4]);
                if (!bad) {
                    // vertical descent: the later entrant's layer k has
                    // to clear the resident's layers j >= k
                    // equal entries: the day's ENTRY order is ours to
                    // choose, so neither direction may be assumed -- the
                    // pair conflicts if EITHER descent is blocked.
                    const bool tie = (A[5] == B[5]);
                    const bool aLate = A[5] >= B[5];
                    const auto& gl = aLate ? ga : gb;
                    const auto& ge = aLate ? gb : ga;
                    const int* L = aLate ? A : B;
                    const int* E = aLate ? B : A;
                    const int nl = (int)gl.layers.size();
                    const int ne = (int)ge.layers.size();
                    for (int k = 0; k < nl && !bad; k++)
                        for (int j = k; j < ne && !bad; j++)
                            bad = crane::overlapsLay(gl, k, L[3], L[4],
                                                     ge, j, E[3], E[4]);
                    if (!bad && tie)
                        for (int k = 0; k < ne && !bad; k++)
                            for (int j = k; j < nl && !bad; j++)
                                bad = crane::overlapsLay(ge, k, E[3], E[4],
                                                         gl, j, L[3], L[4]);
                }
                if (!bad) {
                    // vertical ASCENT -- the half this model was missing.
                    // Whichever block leaves FIRST sweeps up through the
                    // other, which is still resident, so its layer k must
                    // clear the stayer's layers j >= k. Simultaneous exits
                    // resolve by LOCKSTEP (later entry leaves first,
                    // invariant 7). Without it the CP arm proposed dense
                    // rearrangements the checker then rejected -- measured
                    // 08-04: every forced room-making solve came back
                    // vp-infeasible, always on an exit sweep between two
                    // FREED blocks (e.g. "block 56 exit obstructed by
                    // block 88, kind=sweep"), never on a frozen one.
                    const bool aFirst = (fa < fb) || (fa == fb && A[5] > B[5]);
                    const auto& gx = aFirst ? ga : gb;      // exiter
                    const auto& gs = aFirst ? gb : ga;      // stayer
                    const int* X = aFirst ? A : B;
                    const int* Y = aFirst ? B : A;
                    const int nx = (int)gx.layers.size();
                    const int ns = (int)gs.layers.size();
                    for (int k = 0; k < nx && !bad; k++)
                        for (int j = k; j < ns && !bad; j++)
                            bad = crane::overlapsLay(gx, k, X[3], X[4],
                                                     gs, j, Y[3], Y[4]);
                }
                if (bad) { out.push_back(a); out.push_back(b); }
            }
        }
        return out;
    }
    // Exact, time-independent geometry relation between candidate placements.
    // cand = flat [block, bay, ori, x, y, entry] * N.  The entry field is
    // deliberately ignored: the flexible-stay CP decides whether two related
    // placements are disjoint in time or legally nested.  Returned triples
    // are [candidateA, candidateB, code], where
    //   1 = hard-disjoint (rest collision or mutual obstruction),
    //   2 = A is outer, B is inner (B obstructs moving A),
    //   3 = B is outer, A is inner (A obstructs moving B).
    // Same-block, different-bay, and geometrically clear pairs are omitted.
    // This is an experimental classifier only; pairConflicts and the shipped
    // fixed-duration path above remain unchanged.
    std::vector<int> pairRelations(const std::vector<int>& cand) {
        constexpr size_t stride = 6;
        if (cand.size() % stride != 0)
            throw std::invalid_argument(
                "pair_relations expects flat [block,bay,ori,x,y,entry] rows");
        const size_t count = cand.size() / stride;
        constexpr size_t maxCandidateRows = 4096;
        if (count == 0)
            throw std::invalid_argument(
                "pair_relations requires at least one candidate row");
        if (count > maxCandidateRows)
            throw std::invalid_argument(
                "pair_relations exceeds its 4096-row hard cap");
        if (pb.n <= 0 || pb.m <= 0 || pb.geo.size() != (size_t)pb.n ||
            pb.bayW.size() != (size_t)pb.m ||
            pb.bayH.size() != (size_t)pb.m)
            throw std::logic_error("pair_relations called on an invalid problem");

        // Validate every row before producing a partial answer.  Coordinates
        // must describe an in-bay placement, but entry is intentionally not
        // range-checked because it cannot affect this geometry-only relation.
        static const std::vector<crane::Placement> empty;
        for (size_t q = 0; q < count; q++) {
            const int* C = &cand[q * stride];
            const int block = C[0], bay = C[1], ori = C[2];
            if (block < 0 || block >= pb.n || bay < 0 || bay >= pb.m ||
                ori < 0 || ori >= (int)pb.geo[block].orients.size())
                throw std::invalid_argument(
                    "pair_relations candidate index is out of range");
            const auto& g = pb.geo[block].orients[ori];
            if (g.layers.empty() ||
                !crane::entryOK(pb.bayW[bay], pb.bayH[bay], empty,
                                g, C[3], C[4]))
                throw std::invalid_argument(
                    "pair_relations candidate is outside its bay");
        }

        // Speculation must neither consult nor evict the live trajectory's
        // direct-mapped geometry memo.  RAII restores the caller's prior state.
        crane::ScopedMemoBypass memoBypass(true);
        core::Sol pair;
        pair.init(pb.n, pb.m);
        std::vector<int> out;
        for (int a = 0; a < (int)count; a++) {
            const int* A = &cand[(size_t)a * stride];
            for (int b = a + 1; b < (int)count; b++) {
                const int* B = &cand[(size_t)b * stride];
                if (A[0] == B[0] || A[1] != B[1]) continue;

                pair.bay[A[0]] = A[1]; pair.ori[A[0]] = A[2];
                pair.x[A[0]] = A[3]; pair.y[A[0]] = A[4];
                pair.bay[B[0]] = B[1]; pair.ori[B[0]] = B[2];
                pair.x[B[0]] = B[3]; pair.y[B[0]] = B[4];

                int code = 0;
                if (core::restOverlap(pb, pair, A[0], B[0])) {
                    code = 1;
                } else {
                    const bool bBlocksA =
                        core::entryBlocked(pb, pair, B[0], A[0]);
                    const bool aBlocksB =
                        core::entryBlocked(pb, pair, A[0], B[0]);
                    if (bBlocksA && aBlocksB) code = 1;
                    else if (bBlocksA) code = 2;
                    else if (aBlocksB) code = 3;
                }
                if (code != 0)
                    out.insert(out.end(), {a, b, code});
            }
        }
        return out;
    }
    void setAssignmentLock(std::vector<int> v) {
        lockVec = std::move(v);
        ctx.forcedBay = lockVec.empty() ? nullptr : lockVec.data();
    }
    void setEntryLock(std::vector<int> v) {
        capVec = std::move(v);
        ctx.entryCap = capVec.empty() ? nullptr : capVec.data();
    }

    // ------------------------------------------------------------ annealing
    // single-arm anneal on the engine's own state: the experiment instrument
    // (arm-level A/B needs one thread, one parameter set).
    std::vector<double> runALNS(double seconds, double qlo, double qhi,
                                double T0mul, double cooling, uint64_t seed,
                                int auditEvery = 0, double hcFrac = 0.75,
                                double bigQProb = 0.04, int opMask = 0xFF,
                                long maxIters = 0, bool stayRollout = false,
                                bool exitClosure = false,
                                long checkpointIter = 0,
                                bool exitClosureArchive = false) {
        if (exitClosure && exitClosureArchive)
            throw std::invalid_argument(
                "exitClosure and exitClosureArchive are mutually exclusive");
        alns::RunParams rp;
        rp.qloF = qlo; rp.qhiF = qhi; rp.T0mul = T0mul; rp.cooling = cooling;
        rp.hcFrac = hcFrac; rp.bigQProb = bigQProb;
        rp.opMask = (uint32_t)opMask;
        rp.maxIters = maxIters;
        rp.stayRollout = stayRollout;
        rp.exitClosure = exitClosure;
        rp.exitClosureArchive = exitClosureArchive;
        core::Sol checkpoint, archiveCheckpoint, controlBest, archiveReturned;
        checkpointFlat_.clear();
        checkpointOfficial_ = 0;
        archiveCheckpointFlat_.clear();
        archiveCheckpointOfficial_ = 0;
        controlBestFlat_.clear();
        controlBestObjective_ = 0.0;
        finalRngState_.clear();
        archiveSolutionFlat_.clear();
        archiveReturned_ = 0;
        returnedOfficial_ = 0;
        rp.checkpointIter = checkpointIter;
        rp.checkpointOut = checkpointIter > 0 ? &checkpoint : nullptr;
        rp.checkpointOfficialOut = checkpointIter > 0
            ? &checkpointOfficial_ : nullptr;
        rp.archiveCheckpointOut = checkpointIter > 0 && exitClosureArchive
            ? &archiveCheckpoint : nullptr;
        rp.archiveCheckpointOfficialOut =
            checkpointIter > 0 && exitClosureArchive
                ? &archiveCheckpointOfficial_ : nullptr;
        rp.controlBestOut = &controlBest;
        rp.controlBestObjectiveOut = &controlBestObjective_;
        rp.finalRngStateOut = &finalRngState_;
        rp.archiveReturnedOut = exitClosureArchive
            ? &archiveReturned : nullptr;
        auto st = alns::run(ctx, seconds, rp, seed, auditEvery);
        checkpointFlat_ = flattenSolution(checkpoint);
        archiveCheckpointFlat_ = flattenSolution(archiveCheckpoint);
        controlBestFlat_ = flattenSolution(controlBest);
        archiveSolutionFlat_ = flattenSolution(archiveReturned);
        archiveReturned_ = st.archiveReturned ? 1 : 0;
        returnedOfficial_ = st.finalOfficial;
        // Stable telemetry ABI: existing indices 0..43 are unchanged;
        // archive tail = bound-pruned, updates, returned, gain, objective,
        // full-audit rejects at 44..49.
        return {(double)st.iters, (double)st.accepted, (double)st.improved,
                (double)st.failed, st.best, (double)st.badIter,
                (double)st.badKind, (double)st.badPhase,
                (double)st.exitTwinTests, (double)st.exitTwinPass,
                (double)st.exitTwinCompared, (double)st.exitTwinDominant,
                (double)st.exitTwinRescued,
                (double)st.phase2Calls, (double)st.phase2Success,
                (double)st.stayNeighbourhoods, (double)st.stayTried,
                (double)st.stayBuilt, (double)st.staySelected,
                (double)st.stayRescued, st.stayGain,
                (double)st.closureNeighbourhoods,
                (double)st.closureCandidates, (double)st.closureSeeds,
                (double)st.closureAttempts, (double)st.closureBuilt,
                (double)st.closureHardRejects,
                (double)st.closureCycleRejects,
                (double)st.closurePhase3Seeds,
                (double)st.closurePhase3Windows,
                (double)st.closureBudgetHits,
                (double)st.closureSuffixTried,
                (double)st.closureSuffixBuilt,
                (double)st.closureSelected,
                (double)st.closureAccepted,
                (double)st.closureNewBest,
                (double)st.closureRescued,
                (double)st.closureExtendedBlocks,
                (double)st.closureExtendedDays,
                (double)st.closurePaidDays, st.closureGain,
                (double)st.finalOfficial,
                (double)st.closureDetectorNs,
                (double)st.closureSuffixNs,
                (double)st.archiveBoundPruned,
                (double)st.archiveUpdates,
                (double)st.archiveReturned,
                st.archiveGain,
                st.archiveObjective,
                (double)st.archiveAuditRejects};
    }
    std::vector<int> getCheckpointSolution() const { return checkpointFlat_; }
    int checkpointOfficial() const { return checkpointOfficial_; }
    std::vector<int> getArchiveCheckpointSolution() const {
        return archiveCheckpointFlat_;
    }
    int archiveCheckpointOfficial() const {
        return archiveCheckpointOfficial_;
    }
    std::vector<int> getControlBestSolution() const {
        return controlBestFlat_;
    }
    double controlBestObjective() const { return controlBestObjective_; }
    std::string finalRngState() const { return finalRngState_; }
    // Archive mode intentionally leaves get_solution()/Occ on the control
    // trajectory.  Only a strict, audited winner appears in this sidecar.
    std::vector<int> getArchiveSolution() const {
        return archiveSolutionFlat_;
    }
    int archiveReturned() const { return archiveReturned_; }
    int returnedOfficial() const { return returnedOfficial_; }

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
    // WALL-BUDGET rounds: each round anneals seconds/rounds and restarts
    // every thread from the round's global best. (v3.x also carried an
    // iteration-governed mode for machine-independent trajectories; the
    // v4 driver stopped passing a core budget and the v5 audit found the
    // branch unreachable. Deleted rather than revived: on the grading
    // server -- faster than any node we own -- wall budget converts raw
    // speed into DEEPER annealing on the same trajectory, while
    // governance would convert it into extra rounds, and extra rounds
    // measured "rarely gain" (sticky optima). Reproducibility across
    // machines is not worth paying quality for on a one-draw server.)
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
        auto wall0 = std::chrono::steady_clock::now();
        auto elapsed = [&] { return std::chrono::duration<double>(
            std::chrono::steady_clock::now() - wall0).count(); };
        double perRound = seconds / rounds;
        const bool dbgRounds = (getenv("SMAX_DEBUG") != nullptr);
        int chunks = 0;

        for (int r = 0; r < rounds; r++) {
            double roundT0 = elapsed();
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
                c2.densMode = tid % 2;
                c2.forcedBay = ctx.forcedBay;   // assignment lock -> threads
                c2.entryCap = ctx.entryCap;     // entry lock -> threads
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
                rp.hcFrac  = (tid % 2 == 0) ? 1.0 : 0.75;   // half pure-SA tail
                rp.bigQProb = (tid % 2 == 0) ? 0.0 : 0.06;
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
                    alns::spatialCompact(c3, 6);
                    alns::compactLeft(c3, 3);
                }
                #pragma omp critical
                {
                    totIters += st.iters;
                    totIters_ += (double)st.iters;
                    totFailed_ += (double)st.failed;
                    totExitTwinTests_ += (double)st.exitTwinTests;
                    totExitTwinPass_ += (double)st.exitTwinPass;
                    totExitTwinCompared_ += (double)st.exitTwinCompared;
                    totExitTwinDominant_ += (double)st.exitTwinDominant;
                    totExitTwinRescued_ += (double)st.exitTwinRescued;
                    totPhase2Calls_ += (double)st.phase2Calls;
                    totPhase2Success_ += (double)st.phase2Success;
                    for (int k = 0; k < 10; k++) {
                        opUsed_[k] += (double)st.opUsed[k];
                        opImp_[k]  += (double)st.opImp[k];
                    }
                    phaseNs_[0] += (double)st.tRemove;
                    phaseNs_[1] += (double)st.tInsert;
                    phaseNs_[2] += (double)st.tEval;
                    phaseNs_[3] += (double)st.tSamples;
                    double o2 = core::evaluate(pb, s2).total;
                    threadBest[tid] = o2;
                    // keep only the LATEST round's board per thread --
                    // without the clear, rounds>=2 appended and every
                    // consumer sliced the stale round-0 snapshot.
                    candSols[tid].clear();
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
            chunks++;
            if (dbgRounds)
                fprintf(stderr, "[round %d] best %.6g -> %.6g (%+.3g%%)\n",
                        r, roundEnterBest, bestObj,
                        roundEnterBest > 0 ?
                        100.0 * (bestObj - roundEnterBest) / roundEnterBest
                        : 0.0);
        }
        lastChunks_ = chunks;
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
        reconstructPhase2Calls_ = c2.phase2Calls;
        reconstructPhase2Success_ = c2.phase2Success;
        if (bad >= 0) sol = backup;
        oc.build(pb, horizon_);
        for (int i = 0; i < pb.n; i++)
            if (sol.placed[i]) oc.stamp(pb, sol, i, true);
        return bad >= 0 ? bad : -1;
    }

    // Causal construction probe for the official freedom f-e >= P.  A target
    // of -1 leaves a block at the normal minimum stay; a target equal to the
    // just-built exit is a semantic no-op; a larger target voluntarily keeps
    // the block resident before constructing the suffix.  Extending consumes
    // no RNG, so a no-op vector must replay reconstructOrdered byte-for-byte.
    // This is deliberately an experiment API: the shipped path never calls it.
    int reconstructOrderedStays(std::vector<int> asg, std::vector<int> order,
                                std::vector<int> exitTarget, bool tight,
                                uint64_t seed) {
        if ((int)asg.size() != pb.n || (int)order.size() != pb.n ||
            (int)exitTarget.size() != pb.n) return -2;
        core::Sol backup = sol;
        occ::OccIndex oc2; oc2.build(pb, horizon_);
        repair::Ctx c2; c2.pb=&pb; c2.s=&sol; c2.oc=&oc2;
        c2.horizon=horizon_; c2.forcedBay=asg.data(); c2.rng.seed(seed);
        for (int i = 0; i < pb.n; i++) sol.placed[i] = 0;
        sol.rebuildIdx(pb.m);
        int bad = -1;
        for (int A : order) {
            int eCap = tight ? (pb.D[A] - pb.P[A]) : -1;
            if (tight && eCap < pb.R[A]) { bad = A; break; }
            if (!repair::insertGreedy(c2, A, eCap)) { bad = A; break; }

            const int target = exitTarget[A];
            if (target < 0 || target == sol.f[A]) continue;
            if (target < sol.f[A] || target > horizon_) { bad = A; break; }

            const int oldF = sol.f[A];
            c2.oc->stamp(pb, sol, A, false);
            sol.f[A] = target;
            bool ok = repair::stateFeasible(c2, A);
            if (ok) {
                // A longer stay can change the entry/exit legality of every
                // existing bay mate, not merely A's own crane path.
                for (int k : sol.byBay[sol.bay[A]]) {
                    if (k == A || !sol.placed[k]) continue;
                    if (!repair::stateFeasible(c2, k)) { ok = false; break; }
                }
            }
            if (!ok) {
                sol.f[A] = oldF;
                c2.oc->stamp(pb, sol, A, true);
                bad = A;
                break;
            }
            c2.oc->stamp(pb, sol, A, true);
        }
        reconstructPhase2Calls_ = c2.phase2Calls;
        reconstructPhase2Success_ = c2.phase2Success;
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
        bool ok = repair::stateFeasible(ctx, i);
        if (ok) {
            for (int k : sol.byBay[sol.bay[i]]) {
                if (k == i || !sol.placed[k]) continue;
                if (!(sol.e[k] < sol.f[i] && sol.e[i] < sol.f[k])) continue;
                if (!repair::stateFeasible(ctx, k)) { ok = false; break; }
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
        bool ok = repair::stateFeasible(ctx, i);
        if (ok) {
            int lo = std::min(oe, ne), hi = std::max(of, nf);
            for (int k : sol.byBay[sol.bay[i]]) {
                if (k == i || !sol.placed[k]) continue;
                if (!(sol.e[k] < hi && lo < sol.f[k])) continue;
                if (!repair::stateFeasible(ctx, k)) { ok = false; break; }
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
        std::vector<int> asg;
        if (knobs::keepAssignmentLock() && ctx.forcedBay)
            asg.assign(ctx.forcedBay, ctx.forcedBay + pb.n);
        else
            asg.assign(pb.n, -1);
        if (knobs::keepAssignmentLock() && ctx.forcedBay &&
            ctx.forcedBay[i] >= 0)
            targetBay = ctx.forcedBay[i];
        asg[i]=targetBay;
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

    // Exchange two blocks' slots. moveBlock cannot help on a saturated
    // board -- there is no free space to move INTO -- but a swap needs
    // none, which is exactly why the same move is the anneal's biggest
    // win. Exposed so the preference polish can trade assignments
    // instead of giving up when a bay is full. Returns false and
    // restores everything if the exchange is not legal.
    bool swapBlocks(int i, int j) {
        if (i == j || !sol.placed[i] || !sol.placed[j]) return false;
        int aB=sol.bay[i], aX=sol.x[i], aY=sol.y[i], aE=sol.e[i],
            aF=sol.f[i];
        int bB=sol.bay[j], bX=sol.x[j], bY=sol.y[j], bE=sol.e[j],
            bF=sol.f[j];
        oc.stamp(pb, sol, i, false);
        oc.stamp(pb, sol, j, false);
        int nAe = std::max(pb.R[i], bE), nBe = std::max(pb.R[j], aE);
        sol.bay[i]=bB; sol.x[i]=bX; sol.y[i]=bY;
        sol.e[i]=nAe; sol.f[i]=nAe+pb.P[i];
        sol.bay[j]=aB; sol.x[j]=aX; sol.y[j]=aY;
        sol.e[j]=nBe; sol.f[j]=nBe+pb.P[j];
        sol.rebuildIdx(pb.m);
        oc.stamp(pb, sol, i, true);
        oc.stamp(pb, sol, j, true);
        bool ok = repair::stateFeasible(ctx, i)
               && repair::stateFeasible(ctx, j);
        if (ok)
            for (int b2 : {aB, bB}) {
                for (int k : sol.byBay[b2]) {
                    if (k == i || k == j || !sol.placed[k]) continue;
                    if (!repair::stateFeasible(ctx, k)) { ok=false; break; }
                }
                if (!ok) break;
            }
        if (!ok) {
            oc.stamp(pb, sol, i, false);
            oc.stamp(pb, sol, j, false);
            sol.bay[i]=aB; sol.x[i]=aX; sol.y[i]=aY;
            sol.e[i]=aE; sol.f[i]=aF;
            sol.bay[j]=bB; sol.x[j]=bX; sol.y[j]=bY;
            sol.e[j]=bE; sol.f[j]=bF;
            sol.rebuildIdx(pb.m);
            oc.stamp(pb, sol, i, true);
            oc.stamp(pb, sol, j, true);
            return false;
        }
        return true;
    }

    int compactLeft(int maxPasses) { return alns::compactLeft(ctx, maxPasses); }

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
        .def("swap_blocks", &Engine::swapBlocks)
        // ---- experiment tools (probes, arm-level A/B, state I/O) ----
        .def("run_alns", &Engine::runALNS,
             py::arg("seconds"), py::arg("qlo"), py::arg("qhi"),
             py::arg("T0mul"), py::arg("cooling"), py::arg("seed"),
             py::arg("auditEvery") = 0, py::arg("hcFrac") = 0.75,
             py::arg("bigQProb") = 0.04, py::arg("opMask") = 255,
             py::arg("maxIters") = 0, py::arg("stayRollout") = false,
             py::arg("exitClosure") = false,
             py::arg("checkpointIter") = 0,
             py::arg("exitClosureArchive") = false)
        .def("get_checkpoint_solution", &Engine::getCheckpointSolution)
        .def("checkpoint_official", &Engine::checkpointOfficial)
        .def("get_archive_checkpoint_solution",
             &Engine::getArchiveCheckpointSolution)
        .def("archive_checkpoint_official",
             &Engine::archiveCheckpointOfficial)
        .def("get_control_best_solution", &Engine::getControlBestSolution)
        .def("control_best_objective", &Engine::controlBestObjective)
        .def("final_rng_state", &Engine::finalRngState)
        .def("get_archive_solution", &Engine::getArchiveSolution)
        .def("archive_returned", &Engine::archiveReturned)
        .def("returned_official", &Engine::returnedOfficial)
        .def("reconstruct_assigned", &Engine::reconstructAssigned)
        .def("reconstruct_ordered_stays", &Engine::reconstructOrderedStays)
        .def("insert_capped", &Engine::insertCapped)
        .def("feasible_positions", &Engine::feasiblePositions,
             py::arg("A"), py::arg("k"), py::arg("eMin") = 0)
        .def("event_corner_candidates", &Engine::eventCornerCandidates,
             py::arg("block"), py::arg("bay"), py::arg("entry"),
             py::arg("cap"))
        .def("place_at", &Engine::placeAt)
        .def("placement_verdicts", &Engine::placementVerdicts)
        .def("place_at_flexible", &Engine::placeAtFlexible)
        .def("corner_oracle_gap", &Engine::cornerOracleGap,
             py::arg("A"), py::arg("eLo"), py::arg("eHi"),
             py::arg("maxWitnesses") = 8)
        .def("twin_oracle_gap", &Engine::twinOracleGap,
             py::arg("A"), py::arg("eLo"), py::arg("eHi"),
             py::arg("maxWitnesses") = 32)
        .def("pull_twin_flexible", &Engine::pullTwinFlexible,
             py::arg("seconds"), py::arg("maxMoves") = 1)
        .def("last_chunks", &Engine::lastChunks)
        .def("last_phase", &Engine::lastPhase)
        .def("last_ops", &Engine::lastOps)
        .def("last_stats", &Engine::lastStats)
        .def("reconstruct_stats", &Engine::reconstructStats)
        .def("horizon", [](Engine& e){ return e.horizonValue(); })
        .def("pair_conflicts", &Engine::pairConflicts)
        .def("pair_relations", &Engine::pairRelations)
        .def("set_assignment_lock", &Engine::setAssignmentLock)
        .def("set_entry_lock", &Engine::setEntryLock)
        .def("compact_left", &Engine::compactLeft)
        .def("set_block", &Engine::setBlock)
        .def("finalize_load", &Engine::finalizeLoad)
        .def("set_solution", &Engine::setSolution)
        .def("get_solution", &Engine::getSolution)
        .def("entry_ranks", &Engine::entryRanks)
        .def("exit_ranks", &Engine::exitRanks)
        .def("greedy_exit_ranks", &Engine::greedyExitRanks)
        .def("vp_ok", &Engine::vpOK)
        .def("audit_state", &Engine::auditState,
             py::arg("flexibleExits") = false);
}

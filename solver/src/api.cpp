// api.cpp -- the Engine: problem loading, the thread portfolio, the
// reconstruction entry points, and the two micro-repairs the checker gate
// uses. Everything the shipped pipeline calls lives here; the pybind block at
// the bottom is grouped into "shipped path" and "experiment tools" so the API
// surface states which is which.
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include <atomic>
#include <stdexcept>
#include <tuple>
#include "alns.hpp"
#ifdef _OPENMP
#include <omp.h>
#endif
namespace py = pybind11;
using VV = std::vector<std::vector<std::pair<double,double>>>;
namespace {
uint64_t nextEngineGeneration() {
    static std::atomic<uint64_t> next{1};
    uint64_t generation = next.load(std::memory_order_relaxed);
    for (;;) {
        if (generation == 0)
            throw std::overflow_error("Engine generation exhausted");
        if (next.compare_exchange_weak(generation, generation + 1,
                                       std::memory_order_relaxed,
                                       std::memory_order_relaxed))
            return generation;
    }
}
} // namespace


struct Engine {
    core::Problem pb;
    core::Sol sol;
    occ::OccIndex oc;
    repair::Ctx ctx;
    int horizon_ = 0;
    uint64_t generation_ = 0;
    double prefP_ = -1.0;
    bool exactEvents_ = false;

    // ------------------------------------------------------------- loading
    void initProblem(int n, int m,
                     std::vector<int> R, std::vector<int> D, std::vector<int> P,
                     std::vector<double> workload,
                     std::vector<std::vector<double>> pref,
                     std::vector<double> bayW, std::vector<double> bayH,
                     double w1, double w2, double w3) {
        generation_ = nextEngineGeneration();
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
        pb.geo.clear();
        pb.geo.resize(n);
        sol.init(n, m);
    }

    // add one orientation's layers for block i (local coords, ref at origin)
    void addOrient(int i, const VV& layers) {
        crane::OrientGeom g(generation_, static_cast<uint64_t>(i),
                            static_cast<uint64_t>(pb.geo[i].orients.size()));
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
        // A punctual block with start in [R,D-P] occupies every discrete
        // day in the compulsory interval [D-P,R+P).  Only that intersection
        // is a valid hard density lower bound; the whole start window is not.
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
            int lo = std::max(pb.R[i], pb.D[i] - pb.P[i]);
            int hi = std::min(pb.D[i] - 1, pb.R[i] + pb.P[i] - 1);
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
    void setPrefP(double value) {
        prefP_ = (value >= 0.0 && value <= 1.0) ? value : -1.0;
        ctx.prefP = prefP_;
    }
    void setExactEvents(bool enabled) {
        exactEvents_ = enabled;
        ctx.exactEvents = enabled;
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
    std::vector<std::vector<int>> orderedEvents() {
        std::vector<int> times;
        for (int i = 0; i < pb.n; i++) if (sol.placed[i]) {
            times.push_back(sol.e[i]);
            times.push_back(sol.f[i]);
        }
        std::sort(times.begin(), times.end());
        times.erase(std::unique(times.begin(), times.end()), times.end());
        std::vector<std::vector<int>> rows;
        std::vector<int> exits, entries;
        for (int t : times) {
            std::vector<std::vector<int>> dayExits, dayEntries;
            for (int j = 0; j < pb.m; j++) {
                if (!core::eventOrderAt(pb, sol, j, t, exits, entries))
                    return {};
                for (int i : exits) dayExits.push_back({t, 0, i, j});
                for (int i : entries) dayEntries.push_back({t, 1, i, j});
            }
            rows.insert(rows.end(), dayExits.begin(), dayExits.end());
            rows.insert(rows.end(), dayEntries.begin(), dayEntries.end());
        }
        return rows;
    }
    // Globally optimize the time axis for the incumbent geometric shell while
    // preserving each bay's exact event sequence.  With positions, orientations,
    // assignments, and event order fixed, the earliest feasible schedule is a
    // longest-path pass: entry >= release, exit >= entry + processing, and every
    // event >= its predecessor in that bay.  The present set at every event is
    // unchanged, so exact crane/geometry feasibility is preserved.
    int compactEventSchedule() {
        if (!exactEvents_) return -1;
        const core::Sol old = sol;
        const double oldObj = core::evaluate(pb, sol).total;
        std::vector<int> ne(pb.n, -1), nf(pb.n, -1);
        std::vector<int> exits, entries;
        for (int j = 0; j < pb.m; j++) {
            std::vector<int> times;
            for (int i : sol.byBay[j]) if (sol.placed[i]) {
                times.push_back(sol.e[i]);
                times.push_back(sol.f[i]);
            }
            std::sort(times.begin(), times.end());
            times.erase(std::unique(times.begin(), times.end()), times.end());
            int clock = 0;
            for (int t : times) {
                if (!core::eventOrderAt(pb, sol, j, t, exits, entries)) {
                    sol = old; buildOcc(horizon_); return -2;
                }
                for (int i : exits) {
                    if (ne[i] < 0) {
                        sol = old; buildOcc(horizon_); return -2;
                    }
                    nf[i] = std::max(clock, ne[i] + pb.P[i]);
                    clock = nf[i];
                }
                for (int i : entries) {
                    ne[i] = std::max(clock, pb.R[i]);
                    clock = ne[i];
                }
            }
        }
        int moved = 0;
        for (int i = 0; i < pb.n; i++) if (sol.placed[i]) {
            if (ne[i] < 0 || nf[i] < ne[i] + pb.P[i]) {
                sol = old; buildOcc(horizon_); return -2;
            }
            moved += (ne[i] != sol.e[i] || nf[i] != sol.f[i]);
            sol.e[i] = ne[i]; sol.f[i] = nf[i];
        }
        sol.rebuildIdx(pb.m);
        buildOcc(horizon_);
        for (int i = 0; i < pb.n; i++)
            if (sol.placed[i] && !core::vpExactEvents(pb, sol, i)) {
                sol = old; buildOcc(horizon_); return -3;
            }
        if (core::evaluate(pb, sol).total > oldObj + 1e-9) {
            sol = old; buildOcc(horizon_); return -4;
        }
        return moved;
    }
    std::vector<int> accessCohort(int seed, int limit) {
        std::vector<int> out;
        if (seed < 0 || seed >= pb.n || limit <= 0 || !sol.placed[seed])
            return out;
        std::vector<uint8_t> seen(pb.n, 0);
        std::vector<int> queue{seed};
        seen[seed] = 1;
        for (size_t q = 0; q < queue.size() && (int)out.size() < limit; q++) {
            const int a = queue[q];
            out.push_back(a);
            std::vector<int> blockers;
            const int target = std::max(pb.R[a], pb.D[a] - pb.P[a]);
            core::collectEntryBlockers(pb, sol, a, target, blockers);
            std::sort(blockers.begin(), blockers.end(), [&](int x, int y) {
                const int tx = std::max(0, sol.f[x] - pb.D[x]);
                const int ty = std::max(0, sol.f[y] - pb.D[y]);
                return std::make_pair(-tx, x) < std::make_pair(-ty, y);
            });
            for (int b : blockers)
                if (!seen[b]) { seen[b] = 1; queue.push_back(b); }
        }
        std::vector<int> rest;
        rest.reserve(pb.n);
        for (int i = 0; i < pb.n; i++) if (sol.placed[i] && !seen[i])
            rest.push_back(i);
        const int seedBay = sol.bay[seed];
        const int seedTarget = std::max(pb.R[seed], pb.D[seed] - pb.P[seed]);
        std::sort(rest.begin(), rest.end(), [&](int a, int b) {
            auto key = [&](int i) {
                const int sameBay = sol.bay[i] == seedBay ? 0 : 1;
                const int distance = std::abs(sol.e[i] - seedTarget);
                const int tardy = -std::max(0, sol.f[i] - pb.D[i]);
                return std::make_tuple(sameBay, distance, tardy, i);
            };
            return key(a) < key(b);
        });
        for (int i : rest) {
            if ((int)out.size() >= limit) break;
            out.push_back(i);
        }
        return out;
    }
    bool vpOK(int A) {
        return exactEvents_ ? core::vpExactEvents(pb, sol, A)
                            : core::vpConservative(pb, sol, A);
    }
    std::vector<int> auditState() {
        int bb = -1; int code = alns::audit(pb, sol, &bb, exactEvents_);
        return {code, bb};
    }
    bool exactStateOK() {
        return core::stateExactEvents(pb, sol);
    }

    // assignment lock: persistent forcedBay for the whole engine -> repairs
    // keep every block in its locked bay, freezing Z2/Z3 at the locked
    // assignment's values so the search optimizes pure Z1 geometry.
    std::vector<int> capVec;
    std::vector<int> lockVec;
    int lastChunks_ = 0;                 // rounds completed by the last
    int lastChunks() const { return lastChunks_; }   // portfolio call
    double phaseNs_[4] = {0, 0, 0, 0};   // remove/insert/eval/samples
    double opUsed_[10] = {0}, opImp_[10] = {0};
    double totIters_ = 0, totFailed_ = 0;
    std::vector<double> lastStats() const {
        return {totIters_, totFailed_};
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
    // Enumerate checker-feasible site/time columns. Defaults preserve the
    // historical earliest-day scan; optional caps stratify the vocabulary
    // across bays and several feasible days so one roomy bay cannot consume
    // the entire exact-neighborhood domain.
    std::vector<int> feasiblePositions(int A, int k, int eMin = 0,
                                       double maxSeconds = 0.0,
                                       int maxDaysPerBay = 1,
                                       int maxPerBay = 0,
                                       bool shapeContacts = false) {
        std::vector<int> out;
        const auto started = std::chrono::steady_clock::now();
        auto expired = [&] {
            return maxSeconds > 0.0 && std::chrono::duration<double>(
                std::chrono::steady_clock::now() - started).count() >= maxSeconds;
        };
        if (sol.placed[A]) return out;
        maxDaysPerBay = std::max(1, maxDaysPerBay);
        const int bayCap = maxPerBay > 0 ? maxPerBay : k;
        const int dayCap = std::max(1, (bayCap + maxDaysPerBay - 1)
                                       / maxDaysPerBay);
        std::vector<int> xs, ys, oris;
        for (size_t o = 0; o < pb.geo[A].orients.size(); o++)
            oris.push_back((int)o);
        for (int j = 0; j < pb.m && (int)out.size() / 5 < k; j++) {
            if (expired()) return out;
            const int bayStart = (int)out.size() / 5;
            int feasibleDays = 0;
            for (int e = std::max(pb.R[A], eMin);
                 e + pb.P[A] <= horizon_ &&
                 (int)out.size()/5 - bayStart < bayCap; e++) {
                if (expired()) return out;
                const int f = e + pb.P[A];
                occ::OccIndex::Snap sn;
                oc.buildSnap(j, e, f, sn);
                thread_local std::vector<repair::RB> rbs;
                repair::rbCollect(ctx, j, e, f, rbs);
                const int dayStart = (int)out.size() / 5;
                for (int oi : oris) {
                    if ((int)out.size()/5 - bayStart >= bayCap ||
                        (int)out.size()/5 - dayStart >= dayCap) break;
                    repair::cornerSetsRB(ctx, A, oi, j, rbs, xs, ys);
                    if (shapeContacts)
                        repair::shapeContactSets(ctx, A, oi, j, e, f, xs, ys);
                    for (int y : ys) {
                        if ((int)out.size()/5 - bayStart >= bayCap ||
                            (int)out.size()/5 - dayStart >= dayCap) break;
                        for (int x : xs) {
                            if ((int)out.size()/5 - bayStart >= bayCap ||
                                (int)out.size()/5 - dayStart >= dayCap) break;
                            if (expired()) return out;
                            if (oc.hitsSnap(sn, A, oi, x, y)) continue;
                            sol.bay[A]=j; sol.ori[A]=oi; sol.x[A]=x;
                            sol.y[A]=y; sol.e[A]=e; sol.f[A]=f;
                            sol.placed[A]=1;
                            if (exactEvents_) sol.addIdx(A);
                            const bool ok = exactEvents_
                                ? core::vpExactEvents(pb, sol, A)
                                : core::vpConservative(pb, sol, A);
                            if (exactEvents_) sol.delIdx(A);
                            sol.placed[A]=0;
                            if (!ok) continue;
                            out.insert(out.end(), {j, oi, x, y, e});
                            if ((int)out.size() / 5 >= k) return out;
                        }
                    }
                }
                if ((int)out.size()/5 > dayStart &&
                    ++feasibleDays >= maxDaysPerBay) break;
            }
        }
        return out;
    }
    // vp-verified commit of A at an explicit placement (probe primitive).
    bool placeAt(int A, int bay, int ori, int x, int y, int e) {
        if (sol.placed[A]) return false;
        sol.bay[A]=bay; sol.ori[A]=ori; sol.x[A]=x; sol.y[A]=y;
        sol.e[A]=e; sol.f[A]=e + pb.P[A]; sol.placed[A]=1;
        sol.addIdx(A);
        const bool ok = exactEvents_ ? core::vpExactEvents(pb, sol, A)
                                     : core::vpConservative(pb, sol, A);
        if (!ok) {
            sol.delIdx(A);
            sol.placed[A]=0;
            return false;
        }
        oc.stamp(pb, sol, A, true);
        return true;
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
    std::vector<int> pairConflicts(const std::vector<int>& cand,
                                   double maxSeconds = 0.0) {
        const int N = (int)cand.size() / 6;
        std::vector<int> out;
        const auto started = std::chrono::steady_clock::now();
        auto expired = [&] {
            return maxSeconds > 0.0 && std::chrono::duration<double>(
                std::chrono::steady_clock::now() - started).count() >= maxSeconds;
        };
        for (int a = 0; a < N; a++) {
            const int* A = &cand[a * 6];
            const auto& ga = pb.geo[A[0]].orients[A[2]];
            const int fa = A[5] + pb.P[A[0]];
            for (int b = a + 1; b < N; b++) {
                if (expired()) return std::vector<int>{-1};
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
                const bool restingConflict = bad;
                if (!bad) {
                    // vertical descent: the later entrant's layer k has
                    // to clear the resident's layers j >= k
                    // Equal-entry directions are only a conservative pairwise
                    // screen.  The exact event oracle below decides selectable
                    // ordering against both freed and frozen residents.
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
                if (bad && !restingConflict &&
                    !sol.placed[A[0]] && !sol.placed[B[0]]) {
                    const int ai=A[0], bi=B[0];
                    const auto oldA = getBlock(ai), oldB = getBlock(bi);
                    sol.bay[ai]=A[1]; sol.ori[ai]=A[2]; sol.x[ai]=A[3];
                    sol.y[ai]=A[4]; sol.e[ai]=A[5]; sol.f[ai]=fa;
                    sol.bay[bi]=B[1]; sol.ori[bi]=B[2]; sol.x[bi]=B[3];
                    sol.y[bi]=B[4]; sol.e[bi]=B[5]; sol.f[bi]=fb;
                    sol.placed[ai]=sol.placed[bi]=1;
                    sol.addIdx(ai); sol.addIdx(bi);
                    bad = !(core::vpExactEvents(pb, sol, ai) &&
                            core::vpExactEvents(pb, sol, bi));
                    sol.delIdx(ai); sol.delIdx(bi);
                    sol.bay[ai]=oldA[0]; sol.ori[ai]=oldA[1];
                    sol.x[ai]=oldA[2]; sol.y[ai]=oldA[3];
                    sol.e[ai]=oldA[4]; sol.f[ai]=oldA[5];
                    sol.bay[bi]=oldB[0]; sol.ori[bi]=oldB[1];
                    sol.x[bi]=oldB[2]; sol.y[bi]=oldB[3];
                    sol.e[bi]=oldB[4]; sol.f[bi]=oldB[5];
                    sol.placed[ai]=sol.placed[bi]=0;
                }
                if (bad) { out.push_back(a); out.push_back(b); }
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
        const auto absoluteDeadline = wall0 + std::chrono::duration_cast<
            std::chrono::steady_clock::duration>(
                std::chrono::duration<double>(std::max(0.0, seconds)));
        auto elapsed = [&] { return std::chrono::duration<double>(
            std::chrono::steady_clock::now() - wall0).count(); };
        double perRound = seconds / rounds;
        const bool dbgRounds = (getenv("SMAX_DEBUG") != nullptr);
        int chunks = 0;

        for (int r = 0; r < rounds; r++) {
            if (std::chrono::steady_clock::now() >= absoluteDeadline) break;
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
                c2.prefP = prefP_;
                c2.exactEvents = exactEvents_;
                c2.hasDeadline = true;
                c2.deadline = absoluteDeadline;
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
                    c3.prefP=prefP_;
                    c3.exactEvents=exactEvents_;
                    c3.hasDeadline=true;
                    c3.deadline=absoluteDeadline;
                    alns::spatialCompact(c3, 6);
                    alns::compactLeft(c3, 3);
                }
                #pragma omp critical
                {
                    totIters += st.iters;
                    totIters_ += (double)st.iters;
                    totFailed_ += (double)st.failed;
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
        buildOcc(horizon_);
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
                           bool tight, uint64_t seed, double maxSeconds = 0.0) {
        const auto callStart = std::chrono::steady_clock::now();
        if ((int)asg.size() != pb.n || (int)order.size() != pb.n) return -2;
        core::Sol backup = sol;
        occ::OccIndex oc2; oc2.build(pb, horizon_);
        repair::Ctx c2; c2.pb=&pb; c2.s=&sol; c2.oc=&oc2; c2.horizon=horizon_;
        c2.prefP = prefP_;
        c2.exactEvents = exactEvents_;
        c2.forcedBay = asg.data();
        c2.rng.seed(seed);
        if (maxSeconds > 0.0) {
            c2.hasDeadline = true;
            c2.deadline = callStart
                + std::chrono::duration_cast<std::chrono::steady_clock::duration>(
                    std::chrono::duration<double>(maxSeconds));
        }
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
        bool ok = vpOK(i);
        if (ok) {
            for (int k : sol.byBay[sol.bay[i]]) {
                if (k == i || !sol.placed[k]) continue;
                if (!(sol.e[k] < sol.f[i] && sol.e[i] < sol.f[k])) continue;
                if (!vpOK(k)) { ok = false; break; }
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
        bool ok = vpOK(i);
        if (ok) {
            int lo = std::min(oe, ne), hi = std::max(of, nf);
            for (int k : sol.byBay[sol.bay[i]]) {
                if (k == i || !sol.placed[k]) continue;
                if (!(sol.e[k] < hi && lo < sol.f[k])) continue;
                if (!vpOK(k)) { ok = false; break; }
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

    // Exchange two blocks' slots. moveBlock cannot help on a saturated
    // board -- there is no free space to move INTO -- but a swap needs
    // none, which is exactly why the same move is the anneal's biggest
    // win. Exposed so the preference polish can trade assignments
    // instead of giving up when a bay is full. Returns false and
    // restores everything if the exchange is not legal.
    bool swapBlocks(int i, int j) {
        if (i == j || !sol.placed[i] || !sol.placed[j]) return false;
        int aB=sol.bay[i], aX=sol.x[i], aY=sol.y[i], aE=sol.e[i];
        int bB=sol.bay[j], bX=sol.x[j], bY=sol.y[j], bE=sol.e[j];
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
        bool ok = vpOK(i) && vpOK(j);
        if (ok)
            for (int b2 : {aB, bB}) {
                for (int k : sol.byBay[b2]) {
                    if (k == i || k == j || !sol.placed[k]) continue;
                    if (!vpOK(k)) { ok=false; break; }
                }
                if (!ok) break;
            }
        if (!ok) {
            oc.stamp(pb, sol, i, false);
            oc.stamp(pb, sol, j, false);
            sol.bay[i]=aB; sol.x[i]=aX; sol.y[i]=aY;
            sol.e[i]=aE; sol.f[i]=aE+pb.P[i];
            sol.bay[j]=bB; sol.x[j]=bX; sol.y[j]=bY;
            sol.e[j]=bE; sol.f[j]=bE+pb.P[j];
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
        .def("reconstruct_ordered", &Engine::reconstructOrdered,
             py::arg("assignment"), py::arg("order"), py::arg("tight"),
             py::arg("seed"), py::arg("maxSeconds") = 0.0)
        .def("set_pref_p", &Engine::setPrefP)
        .def("set_exact_events", &Engine::setExactEvents)
        .def("ordered_events", &Engine::orderedEvents)
        .def("exact_state_ok", &Engine::exactStateOK)
        .def("access_cohort", &Engine::accessCohort)
        .def("compact_event_schedule", &Engine::compactEventSchedule)
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
             py::arg("maxIters") = 0)
        .def("reconstruct_assigned", &Engine::reconstructAssigned)
        .def("insert_capped", &Engine::insertCapped)
        .def("feasible_positions", &Engine::feasiblePositions,
             py::arg("A"), py::arg("k"), py::arg("eMin") = 0,
             py::arg("maxSeconds") = 0.0,
             py::arg("maxDaysPerBay") = 1, py::arg("maxPerBay") = 0,
             py::arg("shapeContacts") = false)
        .def("place_at", &Engine::placeAt)
        .def("last_chunks", &Engine::lastChunks)
        .def("last_phase", &Engine::lastPhase)
        .def("last_ops", &Engine::lastOps)
        .def("last_stats", &Engine::lastStats)
        .def("horizon", [](Engine& e){ return e.horizonValue(); })
        .def("pair_conflicts", &Engine::pairConflicts,
             py::arg("cand"), py::arg("maxSeconds") = 0.0)
        .def("set_assignment_lock", &Engine::setAssignmentLock)
        .def("set_entry_lock", &Engine::setEntryLock)
        .def("compact_left", &Engine::compactLeft)
        .def("set_block", &Engine::setBlock)
        .def("finalize_load", &Engine::finalizeLoad)
        .def("set_solution", &Engine::setSolution)
        .def("get_solution", &Engine::getSolution)
        .def("vp_ok", &Engine::vpOK)
        .def("audit_state", &Engine::auditState);
}

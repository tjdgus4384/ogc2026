// core.hpp -- problem data, solution state, feasibility (the checker's five
// rules, evaluated for one block against its bay), objective.
#pragma once
#include "crane.hpp"
#include <algorithm>
#include <vector>
#include <cmath>
#include <cstdint>
#include <chrono>

namespace core {
using crane::OrientGeom; using crane::BlockGeom; using crane::Placement;

struct Problem {
    int n = 0, m = 0;
    std::vector<BlockGeom> geo;            // [block].orients
    std::vector<int> R, D, P;              // integer days
    std::vector<double> workload;
    std::vector<std::vector<double>> pref; // [block][bay]
    std::vector<double> Smax;              // per block
    std::vector<double> bayW, bayH, u;     // u_j = avgArea / (W_j*H_j)
    double w1 = 0, w2 = 0, w3 = 0;
    // achievable polygon density: mean polygon/bbox fill of this
    // instance's pieces. With polygon-area accounting the screener
    // must compare against what polygons can ACTUALLY reach in a bay
    // (bbox packing tops out near 100%, so polygons top out near this
    // ratio) -- otherwise it never rejects and every insertion scans
    // every bay (measured: 65% of the iteration budget lost on P4).
    double fillRatio = 1.0;
    // per-day mandatory demand / total bay area: blocks whose
    // [release, punctual-entry] window covers a day MUST occupy space
    // somewhere that day. Days where this exceeds what polygons can
    // actually reach are provably contended -- a slack-rich block
    // entering there displaces someone who has no alternative.
    std::vector<double> dayLoad;
};

struct Sol {
    // per block; placed[i]==false => not in solution (mid-destroy)
    std::vector<uint8_t> placed;
    std::vector<int> bay, ori, x, y, e, f;
    std::vector<std::vector<int>> byBay;      // resident index (placed only)
    void init(int n, int m = 0) {
        placed.assign(n, 0);
        bay.assign(n, -1); ori.assign(n, 0);
        x.assign(n, 0); y.assign(n, 0); e.assign(n, 0); f.assign(n, 0);
        byBay.assign(m, {});
    }
    void addIdx(int i) { byBay[bay[i]].push_back(i); }
    void delIdx(int i) {
        auto& v = byBay[bay[i]];
        for (size_t k = 0; k < v.size(); k++)
            if (v[k] == i) { v[k] = v.back(); v.pop_back(); break; }
    }
    void rebuildIdx(int m) {
        byBay.assign(m, {});
        for (int i = 0; i < (int)placed.size(); i++)
            if (placed[i]) byBay[bay[i]].push_back(i);
    }
};

// same-layer positive-area overlap between two placed blocks (rule 1 helper)
inline bool restOverlap(const Problem& pb, const Sol& s, int a, int b) {
    const OrientGeom& ga = pb.geo[a].orients[s.ori[a]];
    const OrientGeom& gb = pb.geo[b].orients[s.ori[b]];
    if (!crane::bboxOverlapOff(ga.bb, s.x[a], s.y[a], gb.bb, s.x[b], s.y[b]))
        return false;
    bool hit;
    if (crane::pairMemo(ga, s.x[a], s.y[a], gb, s.x[b], s.y[b], 0, hit))
        return hit;
    int L = std::min(ga.layers.size(), gb.layers.size());
    for (int l = 0; l < L; l++)
        if (crane::overlapsLay(ga, l, s.x[a], s.y[a],
                               gb, l, s.x[b], s.y[b])) return true;
    return false;
}

// Feasibility of block A at its current (bay, ori, x, y, e, f) against every
// resident of its bay -- the five checker rules from A's point of view:
//   1  resting collision: same-layer overlap with any time-overlapping resident
//   2  A's own entry at e: crane rule vs blocks present at e
//   3  A's own exit at f: crane rule vs blocks still present at f
//      (same-day LIFO: an earlier entrant leaving the same day stays "present")
//   4  A must not block the entry of any resident entering during [e, f)
//   5  A must not block the exit of any resident exiting during (e, f]
// Rules 4/5 skip residents whose bbox does not even touch A: A cannot be a
// blocker without positive-area overlap, and every prior insertion was
// verified here, so any remaining failure would be A-independent and thus
// pre-existing -- impossible. Exact, not a heuristic (sec 171). The early-out
// is only valid while every placement passes through this function; an
// operator writing placements directly must re-verify them (sec 176).

// Does `r`, resting in the bay, obstruct newcomer `nu`'s descent? (crane rule:
// nu's layer k is blocked by r's layer j >= k). Bay-boundary failures of `nu`
// are caught by rule 2a, so a single-resident call here only reports the
// pairwise relation.
inline bool entryBlocked(const Problem& pb, const Sol& s, int r, int nu) {
    thread_local std::vector<Placement> one;
    one.clear();
    one.push_back({&pb.geo[r].orients[s.ori[r]],
                   (double)s.x[r], (double)s.y[r]});
    const int bay = s.bay[nu];
    return !crane::entryOK(pb.bayW[bay], pb.bayH[bay], one,
                           pb.geo[nu].orients[s.ori[nu]],
                           (double)s.x[nu], (double)s.y[nu]);
}

// Topologically order a same-day ENTRY batch.  If resident v blocks newcomer
// u, u has to be emitted before v.  Real batches stay on the allocation-free
// bit-mask path; the dynamic path removes the old hidden-instance limit.
inline bool entryTopoOrder(const Problem& pb, const Sol& s,
                           const std::vector<int>& ids,
                           std::vector<int>* order = nullptr) {
    const int n = (int)ids.size();
    if (order) { order->clear(); order->reserve(n); }
    if (n <= 64) {
        uint64_t before[64] = {0};
        for (int u = 0; u < n; u++)
            for (int v = 0; v < n; v++)
                if (u != v && entryBlocked(pb, s, ids[v], ids[u]))
                    before[v] |= 1ULL << u;
        uint64_t done = 0;
        for (int step = 0; step < n; step++) {
            int pick = -1;
            for (int v = 0; v < n; v++)
                if (!(done & (1ULL << v)) && (before[v] & ~done) == 0) {
                    pick = v; break;
                }
            if (pick < 0) return false;
            done |= 1ULL << pick;
            if (order) order->push_back(ids[pick]);
        }
        return true;
    }

    std::vector<std::vector<int>> after(n);
    std::vector<int> indeg(n, 0);
    for (int u = 0; u < n; u++) {
        for (int v = 0; v < n; v++) {
            if (u == v || !entryBlocked(pb, s, ids[v], ids[u])) continue;
            after[u].push_back(v);
            indeg[v]++;
        }
    }
    std::vector<uint8_t> done(n, 0);
    for (int step = 0; step < n; step++) {
        int pick = -1;
        for (int v = 0; v < n; v++)
            if (!done[v] && indeg[v] == 0) { pick = v; break; }
        if (pick < 0) return false;
        done[pick] = 1;
        if (order) order->push_back(ids[pick]);
        for (int v : after[pick]) indeg[v]--;
    }
    return true;
}

// Find one legal ENTRY order for (bay,t).  This used to stop reasoning at a
// batch of 16 and silently emitted id order beyond it.  Hidden boards have no
// such batch-size promise, so the same deterministic smallest-id tie break is
// now defined for every size.
//
// `extra` is an insertion candidate that is placed but not yet indexed in
// byBay.  Omitting it here was the soundness bug in the old m15 experiment.
inline bool entryOrderAt(const Problem& pb, const Sol& s, int bay, int t,
                         std::vector<int>& order, int extra = -1) {
    std::vector<int> ids;
    for (int i : s.byBay[bay])
        if (s.placed[i] && s.e[i] == t) ids.push_back(i);
    if (extra >= 0 && s.placed[extra] && s.bay[extra] == bay &&
        s.e[extra] == t &&
        std::find(ids.begin(), ids.end(), extra) == ids.end())
        ids.push_back(extra);
    std::sort(ids.begin(), ids.end());

    return entryTopoOrder(pb, s, ids, &order);
}

// Find one checker-legal order for every EXIT in (bay,t).  The checker replays
// the order written by the solution, so a same-day batch is feasible when ANY
// order works.  Greedy removal is exact here: lifting a block only removes an
// obstruction and can never make a later lift harder.  `extra` lets insertion
// probes test A before it has been added to byBay.
inline bool exitOrderAt(const Problem& pb, const Sol& s, int bay, int t,
                        std::vector<int>& order, int extra = -1) {
    std::vector<int> ids = s.byBay[bay];
    if (extra >= 0 && s.placed[extra] && s.bay[extra] == bay &&
        std::find(ids.begin(), ids.end(), extra) == ids.end())
        ids.push_back(extra);

    std::vector<int> present, exits;
    for (int i : ids) {
        if (!s.placed[i]) continue;
        if (s.e[i] < t && t <= s.f[i]) present.push_back(i);
        if (s.f[i] == t) exits.push_back(i);
    }
    std::sort(exits.begin(), exits.end());
    order.clear();
    order.reserve(exits.size());
    while (!exits.empty()) {
        bool progressed = false;
        for (size_t q = 0; q < exits.size(); q++) {
            const int id = exits[q];
            auto it = std::find(present.begin(), present.end(), id);
            if (it == present.end()) return false;
            std::vector<Placement> ps;
            ps.reserve(present.size());
            for (int k : present)
                ps.push_back({&pb.geo[k].orients[s.ori[k]],
                              (double)s.x[k], (double)s.y[k]});
            if (!crane::exitOK(ps, (int)(it - present.begin()))) continue;
            order.push_back(id);
            present.erase(it);
            exits.erase(exits.begin() + (long)q);
            progressed = true;
            break;
        }
        if (!progressed) return false;
    }
    return true;
}

inline bool vpExitPolicy(const Problem& pb, const Sol& s, int A,
                         bool flexibleExits) {
    int jT = s.bay[A], eN = s.e[A], fN = s.f[A];
    const OrientGeom& gA = pb.geo[A].orients[s.ori[A]];
    double W = pb.bayW[jT], H = pb.bayH[jT];

    const std::vector<int>& nb = s.byBay[jT];
    // rule 1
    for (int k : nb) {
        if (k == A || !s.placed[k]) continue;
        if (!(s.e[k] < fN && eN < s.f[k])) continue;
        if (restOverlap(pb, s, A, k)) return false;
    }
    // pres is reused across calls: vpConservative runs millions of times per
    // anneal and entryOK/exitOK only read the buffer within the call.
    thread_local std::vector<Placement> pres;
    // rule 2: A's entry at eN, split exactly as the official checker splits it.
    //
    //   Stage 2  present_at_entry = { b : b.entry < entry < b.exit } -- blocks
    //            entering on the SAME day are excluded outright.
    //   Stage 5  the day's ENTRY events replay in the order the solution lists
    //            them, so only ONE order has to work.
    //
    // The old rule merged the two and demanded that EVERY intra-day order be
    // legal (rule 2 put the whole batch under A, rule 4 put A under each of
    // them). 57-76% of blocks enter in a batch of two or more, so that surplus
    // strictness was rejecting reachable boards on exactly the congested days
    // where tardiness is made. Here 2a is Stage 2 and 2b is Stage 5: the
    // "must enter before" relation on the batch has to stay acyclic, which is
    // precisely the condition for an emittable order to exist. _recs_to_sol
    // reproduces that order by the same rule.
    thread_local std::vector<int> batch;
    {
        // 2a -- clear everything resting from strictly earlier days.
        pres.clear();
        batch.clear();
        for (int k : nb) {
            if (k == A || !s.placed[k]) continue;
            if (s.e[k] == eN) { batch.push_back(k); continue; }
            if (s.e[k] < eN && eN < s.f[k])
                pres.push_back({&pb.geo[k].orients[s.ori[k]],
                                (double)s.x[k], (double)s.y[k]});
        }
        if (!crane::entryOK(W, H, pres, gA, s.x[A], s.y[A])) return false;
        // 2b -- an entry order for {batch, A} must exist. Fast path first: if A
        // clears the whole batch, then entering LAST is a legal position for
        // it, whatever the peers do among themselves (they were ordered when
        // they were placed). That test is exactly the old order-blind rule 2,
        // so every placement today's kernel accepts costs the same as today --
        // and note it also makes the old rule 4 redundant at this day, since a
        // block entering last obstructs nobody. Only where the old rule
        // REJECTED do we pay for the relation, which is where the prize is.
        if (!batch.empty()) {
            for (int k : batch)
                pres.push_back({&pb.geo[k].orients[s.ori[k]],
                                (double)s.x[k], (double)s.y[k]});
            if (crane::entryOK(W, H, pres, gA, s.x[A], s.y[A]))
                batch.clear();
        }
        if (!batch.empty()) {
            batch.push_back(A);
            if (!entryTopoOrder(pb, s, batch)) return false;
        }
    }
    // rule 3: A's exit at fN.  The legacy path mirrors its fixed id tie-break.
    // Under official semantics, only blocks leaving strictly later remain:
    // among exits at fN, later entrants leave first and equal-entry peers leave
    // in reverse ENTRY order.  This is always legal because entry and exit use
    // identical crane geometry.  No EXIT-order search is needed.
    if (!flexibleExits) {
        pres.clear();
        pres.push_back({&gA, (double)s.x[A], (double)s.y[A]});  // target at [0]
        for (int k : nb) {
            if (k == A || !s.placed[k]) continue;
            // Still resting when A lifts: leaves later, or leaves the same
            // day but AFTER A in the emitted EXIT order. _recs_to_sol sorts a
            // day's EXITs by descending entry day and -- Python's sort being
            // stable over a list built in id order -- by ascending id within
            // an equal entry day. Without that second tie a pair sharing BOTH
            // days was absent from each other's present set, so the sweep
            // between them was never checked and the checker rejected it
            // (Stage5 "EXIT block X obstructed by block Y (sweep)").
            if (s.e[k] < fN && (s.f[k] > fN ||
                                (s.f[k] == fN && (s.e[k] < eN ||
                                                  (s.e[k] == eN && k > A)))))
                pres.push_back({&pb.geo[k].orients[s.ori[k]],
                                (double)s.x[k], (double)s.y[k]});
        }
        if (!crane::exitOK(pres, 0)) return false;
    } else {
        for (int k : nb) {
            if (k == A || !s.placed[k]) continue;
            if (s.e[k] < fN && fN < s.f[k] && entryBlocked(pb, s, k, A))
                return false;
        }
    }
    // rule 4: A rests through (eN, fN), so it joins the Stage-2 set of every
    // block entering in that window -- re-verify those entries with A present.
    // Blocks entering ON eN are A's own batch and are rule 2b's job, and k's
    // Stage-2 set excludes k's own same-day peers exactly as the checker does.
    for (int k : nb) {
        if (k == A || !s.placed[k]) continue;
        int t = s.e[k];
        if (!(eN < t && t < fN)) continue;
        if (!crane::bboxOverlapOff(gA.bb, s.x[A], s.y[A],
                                   pb.geo[k].orients[s.ori[k]].bb,
                                   s.x[k], s.y[k])) continue;
        pres.clear();
        pres.push_back({&gA, (double)s.x[A], (double)s.y[A]});
        for (int k2 : nb) {
            if (k2 == A || k2 == k || !s.placed[k2]) continue;
            if (s.e[k2] < t && t < s.f[k2])
                pres.push_back({&pb.geo[k2].orients[s.ori[k2]],
                                (double)s.x[k2], (double)s.y[k2]});
        }
        const OrientGeom& gk = pb.geo[k].orients[s.ori[k]];
        if (!crane::entryOK(W, H, pres, gk, s.x[k], s.y[k])) return false;
    }
    // rule 5: A must not block exit of any k exiting during (eN, fN].
    if (!flexibleExits) {
        for (int k : nb) {
            if (k == A || !s.placed[k]) continue;
            int t = s.f[k], ek = s.e[k];
            if (!(eN < t && t <= fN)) continue;
            if (!crane::bboxOverlapOff(gA.bb, s.x[A], s.y[A],
                                       pb.geo[k].orients[s.ori[k]].bb,
                                       s.x[k], s.y[k])) continue;
            pres.clear();
            pres.push_back({&pb.geo[k].orients[s.ori[k]],
                            (double)s.x[k], (double)s.y[k]}); // target k at [0]
            if (fN > t || (fN == t && (eN < ek || (eN == ek && A > k))))
                pres.push_back({&gA, (double)s.x[A], (double)s.y[A]});
            for (int k2 : nb) {
                if (k2 == A || k2 == k || !s.placed[k2]) continue;
                if (s.e[k2] < t && (s.f[k2] > t ||
                                    (s.f[k2] == t && (s.e[k2] < ek ||
                                                      (s.e[k2] == ek && k2 > k)))))
                    pres.push_back({&pb.geo[k2].orients[s.ori[k2]],
                                    (double)s.x[k2], (double)s.y[k2]});
            }
            if (pres.size() > 1 && !crane::exitOK(pres, 0)) return false;
        }
    } else {
        // At t<fN, A is the only newly-added strict stayer.  Existing blockers
        // were already checked when k was placed, so the exact incremental
        // test is just whether A blocks k's ascent.  At t==fN, A belongs to the
        // same EXIT batch and the reverse-entry construction above is a proof;
        // checking the batch again was pure hot-path overhead.
        for (int k : nb) {
            if (k == A || !s.placed[k]) continue;
            if (eN < s.f[k] && s.f[k] < fN && entryBlocked(pb, s, A, k))
                return false;
        }
    }
    return true;
}

// The shipped oracle and the official-checker oracle differ only in how a
// same-day EXIT batch is ordered.  Keep both names explicit so probes can
// measure the semantic gap without changing the control path.
inline bool vpConservative(const Problem& pb, const Sol& s, int A) {
    return vpExitPolicy(pb, s, A, false);
}
inline bool vpFlexibleExits(const Problem& pb, const Sol& s, int A) {
    return vpExitPolicy(pb, s, A, true);
}
inline bool hasExitTwin(const Sol& s, int A) {
    for (int k : s.byBay[s.bay[A]])
        if (k != A && s.placed[k] && s.e[k] == s.e[A] && s.f[k] == s.f[A])
            return true;
    return false;
}

struct Objective { double z1, z2, z3, total; };

inline Objective evaluate(const Problem& pb, const Sol& s);
// GLS penalties (guided local search): optional per-block tardiness
// multipliers. The anneal optimizes the PENALIZED objective while the
// caller keeps its incumbent by the TRUE one, so the search is pushed
// out of deep local optima without changing what "better" means.
// Null pointer = classic behaviour, bit-identical.
inline const double*& glsPen() {
    static thread_local const double* p = nullptr;
    return p;
}

// SLACK SURROGATE weight, always 0.0 since the 08-02 ablation refuted
// the term (the env knob was removed with the v5 pass); the machinery
// stays because evaluatePen reduces exactly to evaluate at w=0.
inline double& slackW() {
    static thread_local double w = 0.0;
    return w;
}

inline Objective evaluatePen(const Problem& pb, const Sol& s) {
    const double* pen = glsPen();
    const double sw = slackW();
    if (sw > 0.0) {
        double z1 = 0, z3 = 0, sl = 0;
        std::vector<double> load(pb.m, 0.0);
        for (int i = 0; i < pb.n; i++) {
            if (!s.placed[i]) continue;
            int d = s.f[i] - pb.D[i];
            z1 += std::max(0, d);
            if (d < 0) {
                double room = (double)(-d);
                double span = std::max(1.0, (double)(pb.D[i]
                                                     - pb.R[i]));
                sl += 1.0 - std::min(1.0, room / span);
            } else sl += 1.0;
            z3 += pb.Smax[i] - pb.pref[i][s.bay[i]];
            load[s.bay[i]] += pb.workload[i];
        }
        double z2 = 0;
        for (int a = 0; a < pb.m; a++)
            for (int b = a + 1; b < pb.m; b++)
                z2 = std::max(z2, std::fabs(pb.u[a]*load[a]
                                            - pb.u[b]*load[b]));
        z2 = std::floor(z2);
        double base = pb.w1*z1 + pb.w2*z2 + pb.w3*z3;
        return {z1, z2, z3, base + sw * pb.w1 * sl};
    }
    if (!pen) return evaluate(pb, s);
    double z1 = 0, z3 = 0, z1p = 0;
    std::vector<double> load(pb.m, 0.0);
    for (int i = 0; i < pb.n; i++) {
        if (!s.placed[i]) continue;
        double t = std::max(0, s.f[i] - pb.D[i]);
        z1 += t;
        z1p += t * (1.0 + pen[i]);
        z3 += pb.Smax[i] - pb.pref[i][s.bay[i]];
        load[s.bay[i]] += pb.workload[i];
    }
    double z2 = 0;
    for (int a = 0; a < pb.m; a++)
        for (int b = a + 1; b < pb.m; b++)
            z2 = std::max(z2, std::fabs(pb.u[a]*load[a] - pb.u[b]*load[b]));
    z2 = std::floor(z2);
    return {z1, z2, z3, pb.w1*z1p + pb.w2*z2 + pb.w3*z3};
}

inline Objective evaluate(const Problem& pb, const Sol& s) {
    double z1 = 0, z3 = 0;
    std::vector<double> load(pb.m, 0.0);
    for (int i = 0; i < pb.n; i++) {
        if (!s.placed[i]) continue;
        z1 += std::max(0, s.f[i] - pb.D[i]);
        z3 += pb.Smax[i] - pb.pref[i][s.bay[i]];
        load[s.bay[i]] += pb.workload[i];
    }
    double z2 = 0;
    for (int a = 0; a < pb.m; a++)
        for (int b = a + 1; b < pb.m; b++)
            z2 = std::max(z2, std::fabs(pb.u[a]*load[a] - pb.u[b]*load[b]));
    z2 = std::floor(z2);
    return {z1, z2, z3, pb.w1*z1 + pb.w2*z2 + pb.w3*z3};
}

// blockers of A entering at time tgt at its CURRENT (x,y,ori): placed k in
// same bay, present at tgt, with any (kLayer, j>=kLayer) positive overlap.
// Used by the crane-exact destroy operator.
inline void collectEntryBlockers(const Problem& pb, const Sol& s, int A,
                                 int tgt, std::vector<int>& out) {
    int jT = s.bay[A];
    const OrientGeom& gA = pb.geo[A].orients[s.ori[A]];
    for (int k : s.byBay[jT]) {
        if (k == A || !s.placed[k]) continue;
        if (!(s.e[k] <= tgt && tgt < s.f[k])) continue;
        const OrientGeom& gk = pb.geo[k].orients[s.ori[k]];
        if (!crane::bboxOverlapOff(gA.bb, s.x[A], s.y[A], gk.bb, s.x[k], s.y[k]))
            continue;
        bool hit = false;
        int nNew = (int)gA.layers.size(), nEx = (int)gk.layers.size();
        for (int kk = 0; kk < nNew && !hit; kk++)
            for (int j = kk; j < nEx && !hit; j++)
                if (crane::overlapsLay(gA, kk, s.x[A], s.y[A],
                                       gk, j, s.x[k], s.y[k])) hit = true;
        if (hit) out.push_back(k);
    }
}
} // namespace core

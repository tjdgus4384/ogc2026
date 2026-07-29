// exact.hpp -- the exact window subproblem: a deterministic large
// neighbourhood for congested bay-windows.
//
// Motive (docs/PIPELINE.md, 07-30 convergent diagnosis): every real-board
// gap reduces to packing more blocks into on-time positions at congested
// windows; the anneal's fixed points sit 25-40% above rank-1 and every
// stochastic/local lever measured dead around them. This solves the window
// EXACTLY-ish instead: pick a tardy block, take the residents of its bay
// overlapping its on-time interval (cap WMAX), and branch & bound over the
// JOINT (entry, orientation, position) schedule of that set against the
// frozen rest of the world.
//
// Soundness anchors:
// - bay membership never changes => Z2/Z3 are invariant (invariant 6);
//   the subproblem objective is pure Z1 (tardy days of the set).
// - every placement passes vpConservative against fixed blocks AND the
//   partially scheduled set (invariant 1); occ stamps flow through
//   stamp/removeBlock only (invariant 2).
// - the incumbent starts at the CURRENT layout's tardiness, and the
//   original placements are restored unless a strictly better complete
//   schedule is found -- no-regression by construction.
// - deterministic: no rng, fixed DFS order (EDD outer, event-ascending
//   entries, bottom-left corner order).
//
// Branching (per block, EDD order):
//   entries  : R_i plus the exits of currently placed blocks in the bay
//              (event-driven -- space only opens when someone leaves,
//              sec 48), ascending, first EMAX with >= 1 feasible position
//   positions: cornerSets at that entry, crane-aware hits() prefiltered,
//              vp-confirmed, first CMAX in scan order
// Bound: sum over unscheduled blocks of max(0, R+P-D) (release bound).
#pragma once
#include "alns.hpp"

namespace exact {
using core::Problem; using core::Sol;

struct WinBnB {
    repair::Ctx& c;
    const Problem& pb;
    Sol& s;
    std::vector<int> S;            // the window's blocks, EDD order
    std::vector<double> lbTail;    // lbTail[k] = release-bound of S[k..]
    long nodes = 0, nodeBudget;
    double tBest;                  // best total tardiness found (days)
    std::vector<alns::Backup> best;
    bool improved = false;

    static constexpr int EMAX = 5, CMAX = 3;

    WinBnB(repair::Ctx& ctx, long budget)
        : c(ctx), pb(*ctx.pb), s(*ctx.s), nodeBudget(budget) {}

    double tardyAt(int i, int e) const {
        return std::max(0, e + pb.P[i] - pb.D[i]);
    }

    void snap(std::vector<alns::Backup>& out) const {
        out.clear();
        for (int i : S)
            out.push_back({i, s.bay[i], s.ori[i], s.x[i], s.y[i],
                           s.e[i], s.f[i]});
    }

    void dfs(int k, double acc, int j) {
        if (nodes++ >= nodeBudget) return;
        if (acc + lbTail[k] >= tBest - 1e-9) return;       // bound
        if (k == (int)S.size()) {
            tBest = acc; snap(best); improved = true;
            return;
        }
        int i = S[k];
        // event-driven entry candidates: R_i plus exits of everything
        // currently placed in the bay (fixed blocks and S[0..k-1])
        thread_local std::vector<int> evs;
        evs.clear();
        evs.push_back(pb.R[i]);
        for (int b : s.byBay[j])
            if (s.placed[b] && s.f[b] > pb.R[i]) evs.push_back(s.f[b]);
        std::sort(evs.begin(), evs.end());
        evs.erase(std::unique(evs.begin(), evs.end()), evs.end());
        thread_local std::vector<int> xs, ys;
        int tried = 0;
        for (int e : evs) {
            if (tried >= EMAX) break;
            if (acc + tardyAt(i, e) + lbTail[k + 1] >= tBest - 1e-9)
                break;                       // entries ascend: all later worse
            int f = e + pb.P[i];
            int placed = 0;
            for (size_t oo = 0; oo < pb.geo[i].orients.size() && placed < CMAX;
                 oo++) {
                int oi = (int)oo;
                repair::cornerSets(c, i, oi, j, e, f, xs, ys);
                for (int y : ys) {
                    if (placed >= CMAX) break;
                    for (int x : xs) {
                        if (placed >= CMAX) break;
                        if (c.oc->hits(i, oi, j, x, y, e, f)) continue;
                        // solver-lineage placement idiom (phase 3): tentative
                        // fields -> full vp -> commit or roll back
                        s.bay[i]=j; s.ori[i]=oi; s.x[i]=x; s.y[i]=y;
                        s.e[i]=e; s.f[i]=f; s.placed[i]=1;
                        if (!core::vpConservative(pb, s, i)) {
                            s.placed[i] = 0;
                            continue;
                        }
                        s.addIdx(i);
                        c.oc->stamp(pb, s, i, true);
                        dfs(k + 1, acc + tardyAt(i, e), j);
                        repair::removeBlock(c, i);
                        placed++;
                        if (nodes >= nodeBudget) return;
                    }
                }
            }
            if (placed) tried++;
        }
    }
};

// One pass: for each bay's worst still-tardy block, solve its on-time
// window exactly (sets capped at WMAX blocks, nodeBudget nodes, wall
// seconds overall). Returns the number of windows improved.
inline int windowExact(repair::Ctx& c, double seconds, long nodeBudget = 60000,
                       int WMAX = 12) {
    const Problem& pb = *c.pb; Sol& s = *c.s;
    auto t0 = std::chrono::steady_clock::now();
    auto left = [&] {
        return seconds - std::chrono::duration<double>(
            std::chrono::steady_clock::now() - t0).count();
    };
    int wins = 0;
    std::vector<uint8_t> done(pb.n, 0);      // one window per seed block
    while (left() > 0.05) {
        // most tardy unprocessed block
        int b = -1; double worst = 0;
        for (int i = 0; i < pb.n; i++) {
            if (!s.placed[i] || done[i]) continue;
            double td = s.f[i] - pb.D[i];
            if (td > worst) { worst = td; b = i; }
        }
        if (b < 0) break;
        done[b] = 1;
        int j = s.bay[b];
        int es = std::max(pb.R[b], pb.D[b] - pb.P[b]);
        // the set: b plus residents of j overlapping [es, f_b)
        std::vector<std::pair<int,int>> cand;    // (due, block)
        for (int i : s.byBay[j])
            if (s.placed[i] && s.e[i] < s.f[b] && es < s.f[i])
                cand.push_back({pb.D[i], i});
        if ((int)cand.size() < 2) continue;
        std::sort(cand.begin(), cand.end());
        WinBnB w(c, nodeBudget);
        w.S.push_back(b);                        // the seed always solves
        for (auto& pr : cand) {
            if ((int)w.S.size() >= WMAX) break;
            if (pr.second != b) w.S.push_back(pr.second);
        }
        // incumbent = current layout; remember it for restore
        std::vector<alns::Backup> orig;
        w.snap(orig);
        double t0d = 0;
        for (int i : w.S) t0d += std::max(0, s.f[i] - pb.D[i]);
        if (t0d <= 0) continue;
        w.tBest = t0d;
        w.lbTail.assign(w.S.size() + 1, 0.0);
        for (int k = (int)w.S.size() - 1; k >= 0; k--)
            w.lbTail[k] = w.lbTail[k + 1] +
                std::max(0, pb.R[w.S[k]] + pb.P[w.S[k]] - pb.D[w.S[k]]);
        if (w.lbTail[0] >= t0d - 1e-9) continue;    // no room below incumbent
        for (int i : w.S) repair::removeBlock(c, i);
        w.dfs(0, 0.0, j);
        // clear any partial placements, then apply best (found or original)
        for (int i : w.S) if (s.placed[i]) repair::removeBlock(c, i);
        const auto& fin = w.improved ? w.best : orig;
        for (const auto& bk : fin) {
            s.bay[bk.A]=bk.bay; s.ori[bk.A]=bk.ori; s.x[bk.A]=bk.x;
            s.y[bk.A]=bk.y; s.e[bk.A]=bk.e; s.f[bk.A]=bk.f; s.placed[bk.A]=1;
            s.addIdx(bk.A);
            c.oc->stamp(pb, s, bk.A, true);
        }
        if (w.improved) wins++;
    }
    return wins;
}

} // namespace exact

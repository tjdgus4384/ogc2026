// repair_lookahead.hpp -- bounded combinatorial repair over scored placements.
#pragma once

#include "repair.hpp"
#include <chrono>
#include <cstdint>
#include <limits>
#include <tuple>
#include <unordered_map>
#include <utility>

namespace repair {
namespace lookahead {

struct Config {
    int candidatesPerBlock = 8;       // scored legal placements retained per block
    int branchWidth = 4;              // children expanded at a search node
    int rolloutCandidates = 3;        // scored choices sampled during a rollout
    int maxNodes = 384;
    int maxRollouts = 96;
    int discrepancy = 3;              // non-best branch budget on a root path
    std::chrono::milliseconds wall = std::chrono::milliseconds(80);
    uint32_t seed = 1;
};

struct Stats {
    uint64_t nodes = 0, expanded = 0, prunedBound = 0, transpositionHits = 0;
    uint64_t rollouts = 0, rolloutSteps = 0, complete = 0, audited = 0;
    uint64_t candidateChecks = 0, candidateRejected = 0;
    uint64_t infeasible = 0, deadlineStops = 0;
    double bestObjective = std::numeric_limits<double>::infinity();
};

struct Result {
    bool committed = false;
    Stats stats;
};

struct Candidate {
    int block = -1, bay = -1, ori = 0, x = 0, y = 0, e = 0;
    double score = std::numeric_limits<double>::infinity();
};

inline uint64_t mix(uint64_t h, uint64_t x) {
    x += 0x9e3779b97f4a7c15ULL;
    x = (x ^ (x >> 30)) * 0xbf58476d1ce4e5b9ULL;
    x = (x ^ (x >> 27)) * 0x94d049bb133111ebULL;
    return (h ^ (x ^ (x >> 31))) * 0x9e3779b97f4a7c15ULL;
}

inline bool expired(const std::chrono::steady_clock::time_point& deadline) {
    return std::chrono::steady_clock::now() >= deadline;
}

// An admissible objective lower bound for unplaced blocks.  The load intervals
// include every possible destination for flexible blocks, so their pairwise
// separation is a safe capacity/imbalance lower bound.
inline double lowerBound(const Ctx& c, const std::vector<int>& remaining) {
    const Problem& pb = *c.pb;
    core::Objective now = core::evaluatePen(pb, *c.s);
    double z1 = now.z1, z3 = now.z3;
    std::vector<double> lo(pb.m, 0.0), hi(pb.m, 0.0);
    for (int i = 0; i < pb.n; ++i) if (c.s->placed[i])
        lo[c.s->bay[i]] += pb.workload[i];
    hi = lo;
    for (int a : remaining) {
        z1 += std::max(0, pb.R[a] + pb.P[a] - pb.D[a]);
        double best = pb.Smax[a];
        if (c.forcedBay && c.forcedBay[a] >= 0) {
            const int j = c.forcedBay[a];
            best = pb.Smax[a] - pb.pref[a][j];
            lo[j] += pb.workload[a];
            hi[j] += pb.workload[a];
        } else {
            for (int j = 0; j < pb.m; ++j) {
                best = std::min(best, pb.Smax[a] - pb.pref[a][j]);
                hi[j] += pb.workload[a];
            }
        }
        z3 += best;
    }
    double z2 = 0.0;
    for (int a = 0; a < pb.m; ++a) for (int b = a + 1; b < pb.m; ++b) {
        const double ab = pb.u[a] * lo[a] - pb.u[b] * hi[b];
        const double ba = pb.u[b] * lo[b] - pb.u[a] * hi[a];
        z2 = std::max(z2, std::max(0.0, std::max(ab, ba)));
    }
    return pb.w1 * z1 + pb.w2 * std::floor(z2) + pb.w3 * z3;
}

inline double candidateScore(const Ctx& c, int a, int j, int oi, int x, int y,
                             int e) {
    const Problem& pb = *c.pb;
    const auto& bb = pb.geo[a].orients[oi].bb;
    const double late = std::max(0, e + pb.P[a] - pb.D[a]);
    const double regret = pb.Smax[a] - pb.pref[a][j];
    const double area = (bb.x1 - bb.x0) * (bb.y1 - bb.y0);
    // This is a ranking policy only; every generated candidate is vp-confirmed.
    return pb.w1 * late + pb.w3 * regret + 1e-5 * area +
           1e-7 * (double)(x + y + e);
}

// Generates a diverse, scored set. It deliberately enumerates candidates and
// ranks them instead of taking the first feasible position.
inline void candidates(Ctx& c, int a, int limit,
                       const std::chrono::steady_clock::time_point& deadline,
                       std::vector<Candidate>& out, Stats* st = nullptr) {
    out.clear();
    const Problem& pb = *c.pb;
    const int cap = c.entryCap && c.entryCap[a] >= 0 ? c.entryCap[a] : -1;
    const int last = cap >= 0 ? cap : c.horizon - pb.P[a] - 1;
    if (last < pb.R[a]) return;
    std::vector<int> days{pb.R[a]};
    const int due = pb.D[a] - pb.P[a];
    if (due > pb.R[a] && due <= last) days.push_back(due);
    if (last > pb.R[a]) days.push_back(last);
    for (int d = pb.R[a] + 1; d < last && (int)days.size() < 6;
         d += std::max(1, (last - pb.R[a]) / 5)) days.push_back(d);
    std::sort(days.begin(), days.end());
    days.erase(std::unique(days.begin(), days.end()), days.end());
    std::vector<int> bays;
    if (c.forcedBay && c.forcedBay[a] >= 0) bays.push_back(c.forcedBay[a]);
    else for (int j = 0; j < pb.m; ++j) bays.push_back(j);
    std::sort(bays.begin(), bays.end(), [&](int u, int v) {
        return pb.pref[a][u] != pb.pref[a][v] ? pb.pref[a][u] > pb.pref[a][v] : u < v;
    });
    std::vector<int> xs, ys;
    for (int e : days) for (int j : bays) {
        if (expired(deadline)) return;
        const int f = e + pb.P[a];
        for (int oi = 0; oi < (int)pb.geo[a].orients.size(); ++oi) {
            cornerSets(c, a, oi, j, e, f, xs, ys);
            for (int y : ys) for (int x : xs) {
                if (expired(deadline)) return;
                if (st) ++st->candidateChecks;
                if (c.oc->hits(a, oi, j, x, y, e, f)) {
                    if (st) ++st->candidateRejected;
                    continue;
                }
                if (!tryPlacement(c, a, j, oi, x, y, e)) {
                    if (st) ++st->candidateRejected;
                    continue;
                }
                out.push_back({a, j, oi, x, y, e, candidateScore(c, a, j, oi, x, y, e)});
                removeBlock(c, a);
            }
        }
    }
    std::sort(out.begin(), out.end(), [](const Candidate& u, const Candidate& v) {
        return u.score != v.score ? u.score < v.score :
               std::tie(u.e, u.bay, u.ori, u.y, u.x) < std::tie(v.e, v.bay, v.ori, v.y, v.x);
    });
    if ((int)out.size() > limit) out.resize(limit);
}

inline bool apply(Ctx& c, const Candidate& q) {
    return tryPlacement(c, q.block, q.bay, q.ori, q.x, q.y, q.e);
}

inline uint64_t stateKey(const Sol& s, const std::vector<int>& remaining) {
    uint64_t h = 0x243f6a8885a308d3ULL;
    for (int a : remaining) h = mix(h, (uint64_t)(unsigned)a + 1);
    for (int a = 0; a < (int)s.placed.size(); ++a) if (s.placed[a]) {
        h = mix(h, (uint64_t)(unsigned)(a + 1));
        h = mix(h, (uint64_t)(unsigned)(s.bay[a] + 1));
        h = mix(h, (uint64_t)(unsigned)(s.ori[a] + 1));
        h = mix(h, (uint64_t)(unsigned)(s.x[a] + 65537));
        h = mix(h, (uint64_t)(unsigned)(s.y[a] + 65537));
        h = mix(h, (uint64_t)(unsigned)(s.e[a] + 1));
    }
    return h;
}

struct Search {
    const Config& cfg; Stats& st;
    std::chrono::steady_clock::time_point deadline;
    std::mt19937 rng;
    std::unordered_map<uint64_t, double> seen;
    Sol best;
    occ::OccIndex bestOcc;
    bool haveBest = false;

    Search(const Config& x, Stats& y, std::chrono::steady_clock::time_point d)
        : cfg(x), st(y), deadline(d), rng(x.seed) {}

    void consider(Ctx& c) {
        if (expired(deadline)) { ++st.deadlineStops; return; }
        if (!core::stateExactEvents(*c.pb, *c.s)) { ++st.infeasible; return; }
        if (expired(deadline)) { ++st.deadlineStops; return; }
        ++st.audited;
        const double value = core::evaluatePen(*c.pb, *c.s).total;
        if (!haveBest || value < st.bestObjective) {
            best = *c.s; bestOcc = *c.oc; st.bestObjective = value; haveBest = true;
        }
    }

    void rollout(Ctx c, std::vector<int> rem) {
        if (expired(deadline) || st.rollouts >= (uint64_t)cfg.maxRollouts) return;
        ++st.rollouts;
        while (!rem.empty() && !expired(deadline)) {
            std::vector<Candidate> pool, all;
            for (int a : rem) {
                candidates(c, a, cfg.rolloutCandidates, deadline, pool, &st);
                all.insert(all.end(), pool.begin(), pool.end());
            }
            if (all.empty()) { ++st.infeasible; return; }
            std::sort(all.begin(), all.end(), [](const Candidate& a, const Candidate& b) { return a.score < b.score; });
            const int take = std::min((int)all.size(), std::max(1, cfg.rolloutCandidates));
            const Candidate& q = all[(int)(rng() % (uint32_t)take)];
            if (!apply(c, q)) { ++st.infeasible; return; }
            rem.erase(std::find(rem.begin(), rem.end(), q.block));
            ++st.rolloutSteps;
        }
        if (rem.empty()) { ++st.complete; consider(c); }
    }

    void expand(Ctx c, std::vector<int> rem, int discrepancies) {
        if (expired(deadline)) { ++st.deadlineStops; return; }
        if (st.nodes >= (uint64_t)cfg.maxNodes) return;
        ++st.nodes;
        const double lb = lowerBound(c, rem);
        if (haveBest && lb >= st.bestObjective - 1e-9) { ++st.prunedBound; return; }
        const uint64_t key = stateKey(*c.s, rem);
        auto it = seen.find(key);
        if (it != seen.end() && it->second <= lb + 1e-9) { ++st.transpositionHits; return; }
        seen[key] = lb;
        if (rem.empty()) { ++st.complete; consider(c); return; }

        std::vector<Candidate> choices, pool;
        // Each action contains both the selected next block and a scored
        // placement. Retaining per-block top sets prevents a single easy block
        // from hiding a constrained block's alternatives.
        for (int a : rem) {
            candidates(c, a, cfg.candidatesPerBlock, deadline, pool, &st);
            choices.insert(choices.end(), pool.begin(), pool.end());
        }
        if (choices.empty()) { ++st.infeasible; return; }
        std::sort(choices.begin(), choices.end(), [](const Candidate& a,
                                                      const Candidate& b) {
            return a.score != b.score ? a.score < b.score :
                   std::tie(a.block, a.e, a.bay, a.ori, a.y, a.x) <
                   std::tie(b.block, b.e, b.bay, b.ori, b.y, b.x);
        });
        const int width = std::min((int)choices.size(), cfg.branchWidth);
        for (int k = 0; k < width && !expired(deadline); ++k) {
            if (k > 0 && discrepancies >= cfg.discrepancy) break;
            Sol childSol = *c.s; occ::OccIndex childOcc = *c.oc; Ctx child = c;
            child.s = &childSol; child.oc = &childOcc;
            if (!apply(child, choices[k])) { ++st.infeasible; continue; }
            std::vector<int> next = rem;
            next.erase(std::find(next.begin(), next.end(), choices[k].block));
            ++st.expanded;
            rollout(child, next);
            expand(child, next, discrepancies + (k != 0));
        }
    }
};

// Transactional entry point. On failure or deadline the input solution and
// occupancy index are untouched; only a wholly placed, exact-audited state is committed.
inline Result repairLookahead(Ctx& c, const std::vector<int>& removed,
                              const Config& cfg = Config()) {
    Result out;
    if (cfg.candidatesPerBlock <= 0 || cfg.branchWidth <= 0 ||
        cfg.rolloutCandidates <= 0 || cfg.maxNodes <= 0 ||
        cfg.maxRollouts <= 0 || cfg.discrepancy < 0 ||
        cfg.wall.count() <= 0) return out;
    if (deadlineExpired(c)) return out;
    std::vector<int> rem = removed;
    std::sort(rem.begin(), rem.end());
    rem.erase(std::unique(rem.begin(), rem.end()), rem.end());
    if (rem.size() < 6 || rem.size() > 32) return out;
    for (int a : rem) if (a < 0 || a >= c.pb->n || c.s->placed[a]) return out;
    const auto localDeadline = std::chrono::steady_clock::now() + cfg.wall;
    const auto deadline = c.hasDeadline ? std::min(c.deadline, localDeadline) : localDeadline;
    Sol rootSol = *c.s; occ::OccIndex rootOcc = *c.oc; Ctx root = c;
    root.s = &rootSol; root.oc = &rootOcc; root.hasDeadline = true; root.deadline = deadline;
    Search search(cfg, out.stats, deadline);
    search.expand(root, rem, 0);
    if (expired(deadline)) ++out.stats.deadlineStops;
    if (search.haveBest && !expired(deadline) &&
        core::stateExactEvents(*c.pb, search.best) && !expired(deadline)) {
        *c.s = std::move(search.best); *c.oc = std::move(search.bestOcc);
        ++out.stats.audited; out.committed = true;
    }
    return out;
}

} // namespace lookahead
} // namespace repair

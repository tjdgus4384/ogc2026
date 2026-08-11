// kernel_assignment_flow.hpp -- bounded Lagrangian assignment-flow neighbourhood.
#pragma once

#include "alns.hpp"
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <limits>
#include <numeric>
#include <queue>
#include <random>
#include <utility>
#include <vector>

namespace kernel_assignment_flow {

struct Result {
    long flowSolves = 0;
    long assignmentDiversity = 0;
    long reconstructionAttempts = 0;
    long provenCuts = 0;
    long unknowns = 0;
    long completeFeasibleBoards = 0;
    long exactAudits = 0;
    long acceptedGains = 0;
    double initialObjective = std::numeric_limits<double>::infinity();
    double bestObjective = std::numeric_limits<double>::infinity();
    double acceptedGain = 0.0;
    bool improved = false;
    bool deadlineHit = false;
};

namespace detail {

struct Edge { int to, rev, cap; double cost; };

// Successive-shortest-path is deliberately kept local: all supplies are one and
// the bay capacities are the relaxed assignment capacities.  Area, event-day,
// and balance constraints remain Lagrangian terms, never feasibility claims.
class MinCostFlow {
public:
    explicit MinCostFlow(int n) : g_(n) {}
    void add(int a, int b, int cap, double cost) {
        Edge x{b, (int)g_[b].size(), cap, cost};
        Edge y{a, (int)g_[a].size(), 0, -cost};
        g_[a].push_back(x); g_[b].push_back(y);
    }
    bool solve(int source, int sink, int need,
               std::chrono::steady_clock::time_point deadline) {
        const int n = (int)g_.size();
        std::vector<double> dist(n);
        std::vector<int> pv(n), pe(n);
        for (int sent = 0; sent < need; ++sent) {
            if (std::chrono::steady_clock::now() >= deadline) return false;
            std::fill(dist.begin(), dist.end(), std::numeric_limits<double>::infinity());
            dist[source] = 0.0;
            // Residual reverse arcs are negative.  Bellman-Ford is bounded by
            // this small assignment graph and remains correct without claiming
            // that the surrogate costs have a non-negative reduced-cost dual.
            for (int pass = 1; pass < n; ++pass) {
                bool changed = false;
                if (std::chrono::steady_clock::now() >= deadline) return false;
                for (int v = 0; v < n; ++v) if (std::isfinite(dist[v]))
                    for (int k = 0; k < (int)g_[v].size(); ++k) {
                        const Edge& e = g_[v][k];
                        if (!e.cap || dist[e.to] <= dist[v] + e.cost) continue;
                        dist[e.to] = dist[v] + e.cost; pv[e.to] = v; pe[e.to] = k;
                        changed = true;
                    }
                if (!changed) break;
            }
            if (!std::isfinite(dist[sink])) return false;
            for (int v = sink; v != source; v = pv[v]) {
                Edge& e = g_[pv[v]][pe[v]];
                --e.cap; ++g_[v][e.rev].cap;
            }
        }
        return true;
    }
    const std::vector<Edge>& edges(int v) const { return g_[v]; }
private:
    std::vector<std::vector<Edge>> g_;
};

inline bool bayCanContain(const core::Problem& pb, int i, int j) {
    for (const crane::OrientGeom& g : pb.geo[i].orients)
        if (g.bb.x1 - g.bb.x0 <= pb.bayW[j] && g.bb.y1 - g.bb.y0 <= pb.bayH[j]) return true;
    return false;
}

inline double footprint(const core::Problem& pb, int i) {
    double a = std::numeric_limits<double>::infinity();
    for (const crane::OrientGeom& g : pb.geo[i].orients) a = std::min(a, g.parea);
    return std::isfinite(a) ? a : 0.0;
}

inline bool completeExactReplay(const core::Problem& pb, core::Sol& s, int horizon) {
    if ((int)s.placed.size() != pb.n) return false;
    for (int i = 0; i < pb.n; ++i)
        if (!s.placed[i] || s.bay[i] < 0 || s.bay[i] >= pb.m ||
            s.ori[i] < 0 || s.ori[i] >= (int)pb.geo[i].orients.size() ||
            s.e[i] < pb.R[i] || s.f[i] != s.e[i] + pb.P[i] || s.f[i] > horizon) return false;
    s.rebuildIdx(pb.m);
    return core::stateExactEvents(pb, s) && alns::audit(pb, s, nullptr, true) == 0;
}

inline void buildOcc(const core::Problem& pb, core::Sol& s, int horizon, occ::OccIndex& oc) {
    s.rebuildIdx(pb.m); oc.build(pb, horizon);
    for (int i = 0; i < pb.n; ++i) if (s.placed[i]) oc.stamp(pb, s, i, true);
}

inline bool reconstruct(const core::Problem& pb, const std::vector<int>& assignment,
                        int horizon, std::chrono::steady_clock::time_point deadline,
                        uint64_t seed, int mode, core::Sol& out) {
    out.init(pb.n, pb.m);
    occ::OccIndex oc; buildOcc(pb, out, horizon, oc);
    repair::Ctx c{};
    c.pb = &pb; c.s = &out; c.oc = &oc; c.horizon = horizon;
    c.rng.seed((uint32_t)(seed ^ (seed >> 32))); c.forcedBay = assignment.data();
    c.releaseForcedBay = false; c.exactEvents = true; c.hasDeadline = true; c.deadline = deadline;
    std::vector<int> order(pb.n); std::iota(order.begin(), order.end(), 0);
    std::sort(order.begin(), order.end(), [&](int a, int b) {
        const int sa = pb.D[a] - pb.P[a], sb = pb.D[b] - pb.P[b];
        return sa != sb ? sa < sb : (pb.P[a] != pb.P[b] ? pb.P[a] > pb.P[b] : a < b);
    });
    for (int i : order) {
        bool ok = false;
        if (mode == 0) ok = repair::insertGreedy(c, i);
        else if (mode == 1) {
            repair_scored::Config cfg; cfg.topK = 12; cfg.maxCandidates = 256;
            cfg.maxTimes = 24; cfg.contactAxisCap = 40;
            ok = repair_scored::insertBest(c, i, cfg, nullptr);
        } else {
            nfp_contact::Config cfg; cfg.maxEntryTimes = 24; cfg.maxCandidates = 256;
            cfg.maxContactsPerResident = 40; cfg.includeGeneral = true;
            ok = nfp_contact::insert(c, i, cfg, nullptr);
        }
        if (!ok || repair::deadlineExpired(c)) return false;
    }
    return completeExactReplay(pb, out, horizon);
}

inline bool differs(const std::vector<int>& a, const std::vector<int>& b) {
    return a.size() == b.size() && !std::equal(a.begin(), a.end(), b.begin());
}

} // namespace detail

template<class EngineT>
Result run(EngineT& engine, double seconds, uint64_t seed) {
    using Clock = std::chrono::steady_clock;
    Result out;
    const core::Problem& pb = engine.pb;
    const core::Sol saved = engine.sol;
    out.initialObjective = out.bestObjective = core::evaluate(pb, saved).total;
    int horizon = engine.horizon_;
    for (int i = 0; i < pb.n; ++i) horizon = std::max(horizon, pb.D[i] + pb.P[i] + 1);
    horizon = std::max(1, horizon);
    core::Sol initial = saved;
    if (seconds <= 0.0 || pb.n == 0 || pb.m == 0 || !detail::completeExactReplay(pb, initial, horizon)) return out;

    const auto deadline = Clock::now() + std::chrono::duration_cast<Clock::duration>(std::chrono::duration<double>(seconds));
    std::mt19937_64 rng(seed ^ 0xa0761d6478bd642fULL);
    core::Sol best = initial;
    std::vector<int> incumbent = initial.bay, previous;
    std::vector<std::vector<uint8_t>> forbidden(pb.n, std::vector<uint8_t>(pb.m, 0));
    std::vector<double> area(pb.n), loadPrice(pb.m, 0.0);
    std::vector<std::vector<double>> dayPrice(pb.m, std::vector<double>(horizon, 0.0));
    for (int i = 0; i < pb.n; ++i) {
        area[i] = detail::footprint(pb, i);
        for (int j = 0; j < pb.m; ++j) if (!detail::bayCanContain(pb, i, j)) {
            forbidden[i][j] = 1; ++out.provenCuts;
        }
    }

    const int attemptCap = std::max(4, std::min(48, 4 + 3 * pb.m));
    for (int round = 0; round < attemptCap && Clock::now() < deadline; ++round) {
        const int source = 0, firstBlock = 1, firstBay = firstBlock + pb.n, sink = firstBay + pb.m;
        detail::MinCostFlow flow(sink + 1);
        for (int i = 0; i < pb.n; ++i) {
            flow.add(source, firstBlock + i, 1, 0.0);
            for (int j = 0; j < pb.m; ++j) if (!forbidden[i][j]) {
                const int e = std::max(pb.R[i], best.e[i]);
                const int f = std::min(horizon, e + pb.P[i]);
                double temporal = 0.0;
                for (int t = e; t < f; ++t) temporal += dayPrice[j][t] * area[i];
                const double z1 = pb.w1 * std::max(0, e + pb.P[i] - pb.D[i]);
                const double z3 = pb.w3 * (pb.Smax[i] - pb.pref[i][j]);
                const double trust = (j == incumbent[i]) ? 0.0 : 0.02 * (1 + round / 4);
                const double noise = (double)((rng() >> 11) % 1009) * 1e-10;
                flow.add(firstBlock + i, firstBay + j, 1, z1 + z3 + temporal + loadPrice[j] * pb.workload[i] + trust + noise);
            }
        }
        for (int j = 0; j < pb.m; ++j) flow.add(firstBay + j, sink, pb.n, 0.0);
        ++out.flowSolves;
        if (!flow.solve(source, sink, pb.n, deadline)) { ++out.unknowns; continue; }
        std::vector<int> assignment(pb.n, -1);
        for (int i = 0; i < pb.n; ++i) for (const detail::Edge& e : flow.edges(firstBlock + i))
            if (e.to >= firstBay && e.to < sink && e.cap == 0) { assignment[i] = e.to - firstBay; break; }
        if (std::find(assignment.begin(), assignment.end(), -1) != assignment.end()) { ++out.unknowns; continue; }
        if (!previous.empty() && detail::differs(assignment, previous)) ++out.assignmentDiversity;
        previous = assignment;
        const auto now = Clock::now();
        const auto localDeadline = now + (deadline - now) / std::max(1, attemptCap - round);
        core::Sol candidate;
        ++out.reconstructionAttempts;
        if (!detail::reconstruct(pb, assignment, horizon, localDeadline, seed + (uint64_t)round, round % 3, candidate)) {
            // Failure is unknown geometry/time feasibility, not a cut certificate.
            ++out.unknowns;
        } else {
            ++out.completeFeasibleBoards; ++out.exactAudits;
            const double objective = core::evaluate(pb, candidate).total;
            if (objective + 1e-12 < out.bestObjective) {
                out.acceptedGain += out.bestObjective - objective;
                out.bestObjective = objective; out.improved = true; ++out.acceptedGains;
                best = std::move(candidate); incumbent = best.bay;
            }
        }
        // Subgradient-like surrogate update from the master assignment.  It only
        // changes prices: it cannot exclude a feasible geometry or event order.
        std::vector<double> load(pb.m, 0.0);
        std::vector<std::vector<double>> temporalArea(pb.m, std::vector<double>(horizon, 0.0));
        for (int i = 0; i < pb.n; ++i) {
            const int j = assignment[i];
            load[j] += pb.workload[i];
            const int e = std::max(pb.R[i], best.e[i]);
            const int f = std::min(horizon, e + pb.P[i]);
            for (int t = e; t < f; ++t) temporalArea[j][t] += area[i];
        }
        const double meanLoad = std::accumulate(load.begin(), load.end(), 0.0) / pb.m;
        for (int j = 0; j < pb.m; ++j) {
            loadPrice[j] = std::max(0.0, loadPrice[j] + 0.01 * (pb.u[j] * load[j] - meanLoad));
            const double cap = pb.bayW[j] * pb.bayH[j];
            for (int t = 0; t < horizon; ++t) {
                const double excess = std::max(0.0, temporalArea[j][t] - cap);
                dayPrice[j][t] = std::max(0.0, dayPrice[j][t] * 0.98 + 0.001 * excess);
            }
        }
    }
    out.deadlineHit = Clock::now() >= deadline;
    if (out.improved) { engine.sol = best; engine.buildOcc(horizon); }
    return out;
}

} // namespace kernel_assignment_flow

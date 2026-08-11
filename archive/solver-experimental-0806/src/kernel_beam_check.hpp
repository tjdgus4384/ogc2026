// kernel_beam_check.hpp -- bounded best-first construction over exact partial states.
#pragma once

#include "alns.hpp"
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <limits>
#include <random>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

namespace kernel_beam_check {

struct Result {
    uint64_t expandedStates = 0;
    uint64_t prunedStates = 0;
    uint64_t completeFeasibleBoards = 0;
    double initialObjective = std::numeric_limits<double>::infinity();
    double bestObjective = std::numeric_limits<double>::infinity();
    double improvement = 0.0;
    bool improved = false;
    bool deadlineHit = false;
};

namespace detail {

struct State {
    core::Sol sol;
    double admissibleLowerBound = 0.0;
    double heuristicPenalty = 0.0;
    double rank = 0.0;
    int placedCount = 0;
};

inline int horizonFor(const core::Problem& pb, int supplied) {
    int horizon = supplied;
    for (int i = 0; i < pb.n; ++i)
        horizon = std::max(horizon, std::max(pb.D[i], pb.R[i]) + pb.P[i] + 1);
    return std::max(1, horizon);
}

inline double minPreferenceLoss(const core::Problem& pb, int i) {
    double best = std::numeric_limits<double>::infinity();
    for (int j = 0; j < pb.m; ++j) best = std::min(best, pb.Smax[i] - pb.pref[i][j]);
    return std::isfinite(best) ? best : 0.0;
}

// This bound retains every irreversible tardiness/preference contribution and
// relaxes only unplaced blocks and the non-negative balance term.
inline double admissibleLowerBound(const core::Problem& pb, const core::Sol& s) {
    double z1 = 0.0, z3 = 0.0;
    for (int i = 0; i < pb.n; ++i) {
        if (s.placed[i]) {
            z1 += std::max(0, s.f[i] - pb.D[i]);
            z3 += pb.Smax[i] - pb.pref[i][s.bay[i]];
        } else {
            z3 += minPreferenceLoss(pb, i);
        }
    }
    return pb.w1 * z1 + pb.w3 * z3;
}

inline double heuristicPenalty(const core::Problem& pb, const core::Sol& s,
                               int horizon) {
    std::vector<std::vector<double>> load(pb.m, std::vector<double>(horizon, 0.0));
    std::vector<int> events((size_t)pb.m * horizon, 0);
    for (int i = 0; i < pb.n; ++i) if (s.placed[i]) {
        const auto& g = pb.geo[i].orients[s.ori[i]];
        const double area = (g.bb.x1 - g.bb.x0) * (g.bb.y1 - g.bb.y0);
        for (int t = std::max(0, s.e[i]); t < std::min(horizon, s.f[i]); ++t)
            load[s.bay[i]][t] += area;
        if (s.e[i] >= 0 && s.e[i] < horizon) ++events[(size_t)s.bay[i] * horizon + s.e[i]];
        if (s.f[i] >= 0 && s.f[i] < horizon) ++events[(size_t)s.bay[i] * horizon + s.f[i]];
    }
    double penalty = 0.0;
    for (int j = 0; j < pb.m; ++j) {
        const double cap = std::max(1.0, pb.bayW[j] * pb.bayH[j]);
        for (int t = 0; t < horizon; ++t) {
            penalty += std::max(0.0, load[j][t] / cap - 0.80);
            penalty += 0.02 * std::max(0, events[(size_t)j * horizon + t] - 1);
        }
    }
    return penalty;
}

inline void score(const core::Problem& pb, State& state, int horizon) {
    state.admissibleLowerBound = admissibleLowerBound(pb, state.sol);
    state.heuristicPenalty = heuristicPenalty(pb, state.sol, horizon);
    state.rank = state.admissibleLowerBound + state.heuristicPenalty;
}

// A dominance key deliberately includes every placement coordinate, orientation,
// and event.  It can merge only genuinely identical partial boards, never two
// geometrically distinct packings that merely have the same objective.
inline std::string exactSummary(const core::Problem& pb, const core::Sol& s) {
    std::string out;
    out.reserve((size_t)pb.n * 28);
    for (int i = 0; i < pb.n; ++i) {
        if (!s.placed[i]) continue;
        out += std::to_string(i); out += ':';
        out += std::to_string(s.bay[i]); out += ':';
        out += std::to_string(s.ori[i]); out += ':';
        out += std::to_string(s.x[i]); out += ':';
        out += std::to_string(s.y[i]); out += ':';
        out += std::to_string(s.e[i]); out += ':';
        out += std::to_string(s.f[i]); out += ';';
    }
    return out;
}

inline int overlapPressure(const core::Sol& s, int a, int b) {
    return s.bay[a] == s.bay[b] && s.e[a] < s.f[b] && s.e[b] < s.f[a];
}

// Select a coalition from a complete incumbent.  Tardy blocks are primary;
// blocks occupying their time/bay neighbourhood are promoted as the conflict
// set that can actually make a tardy move feasible.
inline std::vector<int> coalition(const core::Problem& pb, const core::Sol& s,
                                  size_t width, size_t rotation) {
    std::vector<std::pair<double, int>> keyed;
    keyed.reserve(pb.n);
    for (int i = 0; i < pb.n; ++i) {
        const int tardy = std::max(0, s.f[i] - pb.D[i]);
        int blockers = 0;
        for (int k = 0; k < pb.n; ++k) if (k != i) blockers += overlapPressure(s, i, k);
        const int slack = std::max(0, pb.D[i] - pb.R[i] - pb.P[i]);
        keyed.push_back({-1000.0 * tardy - 12.0 * blockers + 0.01 * slack, i});
    }
    std::sort(keyed.begin(), keyed.end());
    if (!keyed.empty() && rotation) {
        const size_t pivot = rotation % keyed.size();
        std::rotate(keyed.begin(), keyed.begin() + pivot, keyed.end());
    }
    std::vector<int> out;
    for (size_t k = 0; k < keyed.size() && k < width; ++k) out.push_back(keyed[k].second);
    std::sort(out.begin(), out.end());
    return out;
}

// Conflict-directed variable ordering for a partial rolling window.
inline int selectBlock(const core::Problem& pb, const core::Sol& s) {
    int chosen = -1;
    double best = -std::numeric_limits<double>::infinity();
    for (int i = 0; i < pb.n; ++i) if (!s.placed[i]) {
        const int slack = std::max(0, pb.D[i] - pb.R[i] - pb.P[i]);
        int blockers = 0;
        for (int k = 0; k < pb.n; ++k) if (s.placed[k] && s.bay[k] >= 0 &&
            s.e[k] < pb.D[i] + pb.P[i] && pb.R[i] < s.f[k]) ++blockers;
        const double choices = std::max(1, pb.m * (int)pb.geo[i].orients.size());
        const double urgency = 1000.0 * std::max(0, pb.R[i] + pb.P[i] - pb.D[i]);
        const double score = urgency + 20.0 * blockers - 0.1 * slack - choices;
        if (score > best || (score == best && i < chosen)) { best = score; chosen = i; }
    }
    return chosen;
}

inline std::vector<int> eventTimes(const core::Problem& pb, const core::Sol& s,
                                   const core::Sol& incumbent, int i, int horizon) {
    std::vector<int> times{pb.R[i], std::max(pb.R[i], pb.D[i] - pb.P[i]), incumbent.e[i]};
    for (int k = 0; k < pb.n; ++k) if (s.placed[k]) {
        times.push_back(s.e[k]);
        times.push_back(s.f[k]);
    }
    const int latest = horizon - pb.P[i];
    if (latest >= pb.R[i]) times.push_back(latest);
    std::sort(times.begin(), times.end());
    times.erase(std::unique(times.begin(), times.end()), times.end());
    times.erase(std::remove_if(times.begin(), times.end(), [&](int e) {
        return e < pb.R[i] || e + pb.P[i] > horizon;
    }), times.end());
    return times;
}

inline void rebuildOcc(const core::Problem& pb, core::Sol& s, int horizon,
                       occ::OccIndex& oc) {
    s.rebuildIdx(pb.m);
    oc.build(pb, horizon);
    for (int i = 0; i < pb.n; ++i) if (s.placed[i]) oc.stamp(pb, s, i, true);
}

inline bool completeAndAudited(const core::Problem& pb, core::Sol& s, int horizon) {
    if ((int)s.placed.size() != pb.n || (int)s.byBay.size() != pb.m) return false;
    for (int i = 0; i < pb.n; ++i)
        if (!s.placed[i] || s.bay[i] < 0 || s.bay[i] >= pb.m ||
            s.ori[i] < 0 || s.ori[i] >= (int)pb.geo[i].orients.size() ||
            s.e[i] < pb.R[i] || s.f[i] != s.e[i] + pb.P[i] || s.f[i] > horizon) return false;
    s.rebuildIdx(pb.m);
    return core::stateExactEvents(pb, s) && alns::audit(pb, s, nullptr, true) == 0;
}
} // namespace detail

template<class EngineT>
Result run(EngineT& engine, double seconds, uint64_t seed) {
    using Clock = std::chrono::steady_clock;
    Result result;
    const core::Problem& pb = engine.pb;
    const int horizon = detail::horizonFor(pb, engine.horizon_);
    core::Sol audited = engine.sol;
    if (seconds <= 0.0 || pb.n == 0 || !detail::completeAndAudited(pb, audited, horizon))
        return result;

    result.initialObjective = core::evaluate(pb, audited).total;
    result.bestObjective = result.initialObjective;
    core::Sol best = audited;
    const auto deadline = Clock::now() + std::chrono::duration_cast<Clock::duration>(
        std::chrono::duration<double>(seconds));
    auto expired = [&] { return Clock::now() >= deadline; };
    const size_t maxWindow = pb.n > 1 ? (size_t)pb.n - 1 : 1;
    size_t window = std::min<size_t>(std::max<size_t>(3, pb.n / 40 + 2), maxWindow);
    size_t round = 0;
    constexpr size_t kBeamWidth = 24;
    constexpr size_t kChildLimit = 192;
    constexpr size_t kPerBlock = 56;

    while (!expired() && window > 0) {
        // The incumbent supplies a realizable frozen exterior only.  Search
        // branches from the resulting partial board, not along its placement
        // order, and each completion is independently checker-audited.
        const std::vector<int> removed = detail::coalition(pb, best, window, round++);
        core::Sol rootSol = best;
        for (int i : removed) rootSol.placed[i] = 0;
        rootSol.rebuildIdx(pb.m);
        detail::State root;
        root.sol = std::move(rootSol);
        root.placedCount = pb.n - (int)removed.size();
        detail::score(pb, root, horizon);
        std::vector<detail::State> beam{std::move(root)};
        std::unordered_map<std::string, double> dominance;
        dominance.emplace(detail::exactSummary(pb, beam.front().sol), beam.front().rank);
        bool completedWindow = false;

        while (!beam.empty() && !expired()) {
            std::vector<detail::State> next;
            for (const detail::State& parent : beam) {
                if (expired()) break;
                if (parent.admissibleLowerBound > result.bestObjective) {
                    ++result.prunedStates;
                    continue;
                }
                const int i = detail::selectBlock(pb, parent.sol);
                if (i < 0) continue;
                ++result.expandedStates;
                core::Sol partial = parent.sol;
                occ::OccIndex oc;
                detail::rebuildOcc(pb, partial, horizon, oc);
                repair::Ctx ctx{&pb, &partial, &oc, std::mt19937(0), horizon};
                ctx.rng.seed((uint32_t)(seed ^ (uint64_t)result.expandedStates));
                ctx.exactEvents = true;
                ctx.hasDeadline = true;
                ctx.deadline = deadline;
                const std::vector<int> times = detail::eventTimes(pb, partial, best, i, horizon);
                std::vector<int> bays(pb.m), oris(pb.geo[i].orients.size());
                for (int j = 0; j < pb.m; ++j) bays[j] = j;
                for (int oi = 0; oi < (int)oris.size(); ++oi) oris[oi] = oi;
                std::sort(bays.begin(), bays.end(), [&](int a, int b) {
                    return pb.pref[i][a] != pb.pref[i][b] ? pb.pref[i][a] > pb.pref[i][b] : a < b;
                });
                std::sort(oris.begin(), oris.end());
                size_t accepted = 0;
                for (int e : times) for (int j : bays) {
                    if (expired() || next.size() >= kChildLimit || accepted >= kPerBlock) break;
                    const int f = e + pb.P[i];
                    occ::OccIndex::Snap snap;
                    oc.buildSnap(j, e, f, snap);
                    std::vector<repair::RB> rbs;
                    repair::rbCollect(ctx, j, e, f, rbs);
                    for (int oi : oris) {
                        std::vector<int> xs, ys;
                        repair::cornerSetsRB(ctx, i, oi, j, rbs, xs, ys);
                        repair::shapeContactSets(ctx, i, oi, j, e, f, xs, ys, 64);
                        // Preserve the incumbent placement among the exact
                        // contact candidates so a valid window is never lost
                        // solely due to candidate ordering.
                        if (j == best.bay[i] && oi == best.ori[i] && e == best.e[i]) {
                            xs.push_back(best.x[i]); ys.push_back(best.y[i]);
                            std::sort(xs.begin(), xs.end()); xs.erase(std::unique(xs.begin(), xs.end()), xs.end());
                            std::sort(ys.begin(), ys.end()); ys.erase(std::unique(ys.begin(), ys.end()), ys.end());
                        }
                        for (int x : xs) for (int y : ys) {
                            if (expired() || next.size() >= kChildLimit || accepted >= kPerBlock) break;
                            if (oc.hitsSnap(snap, i, oi, x, y)) { ++result.prunedStates; continue; }
                            detail::State child;
                            child.sol = parent.sol;
                            child.sol.bay[i] = j; child.sol.ori[i] = oi;
                            child.sol.x[i] = x; child.sol.y[i] = y;
                            child.sol.e[i] = e; child.sol.f[i] = f; child.sol.placed[i] = 1;
                            child.sol.addIdx(i);
                            if (!core::vpExactEvents(pb, child.sol, i)) {
                                ++result.prunedStates;
                                continue;
                            }
                            child.placedCount = parent.placedCount + 1;
                            detail::score(pb, child, horizon);
                            if (child.admissibleLowerBound > result.bestObjective) {
                                ++result.prunedStates;
                                continue;
                            }
                            std::string key = detail::exactSummary(pb, child.sol);
                            const auto found = dominance.find(key);
                            if (found != dominance.end() && found->second <= child.rank) {
                                ++result.prunedStates;
                                continue;
                            }
                            dominance[std::move(key)] = child.rank;
                            ++accepted;
                            if (child.placedCount == pb.n) {
                                core::Sol candidate = std::move(child.sol);
                                if (!detail::completeAndAudited(pb, candidate, horizon)) {
                                    ++result.prunedStates;
                                    continue;
                                }
                                ++result.completeFeasibleBoards;
                                completedWindow = true;
                                const double objective = core::evaluate(pb, candidate).total;
                                if (objective < result.bestObjective) {
                                    result.bestObjective = objective;
                                    best = std::move(candidate);
                                }
                            } else {
                                next.push_back(std::move(child));
                            }
                        }
                    }
                }
            }
            std::sort(next.begin(), next.end(), [](const detail::State& a, const detail::State& b) {
                if (a.rank != b.rank) return a.rank < b.rank;
                return a.placedCount > b.placedCount;
            });
            if (next.size() > kBeamWidth) {
                result.prunedStates += next.size() - kBeamWidth;
                next.resize(kBeamWidth);
            }
            beam.swap(next);
        }
        // A completed exact window proves the exterior can support a larger
        // coalition; grow gradually rather than reverting to empty-board beam.
        if (completedWindow && window < maxWindow)
            window = std::min<size_t>(maxWindow, window + std::max<size_t>(1, window / 2));
    }

    result.deadlineHit = expired();
    result.improvement = result.initialObjective - result.bestObjective;
    result.improved = result.bestObjective < result.initialObjective;
    if (result.improved && detail::completeAndAudited(pb, best, horizon)) {
        engine.sol = std::move(best);
        engine.buildOcc(horizon);
    }
    return result;
}

} // namespace kernel_beam_check

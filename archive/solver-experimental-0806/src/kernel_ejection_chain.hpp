// kernel_ejection_chain.hpp -- bounded exact ejection-cycle reassignment search.
#pragma once

#include "alns.hpp"
#include "repair_scored.hpp"
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <limits>
#include <random>
#include <utility>
#include <vector>

namespace kernel_ejection_chain {

struct Result {
    long chains = 0;
    long cycles = 0;
    long affectedBayRepacks = 0;
    long completeFeasibleCandidates = 0;
    long exactAudits = 0;
    long acceptedGains = 0;
    long nodes = 0;
    double initialObjective = std::numeric_limits<double>::infinity();
    double bestObjective = std::numeric_limits<double>::infinity();
    bool improved = false;
    bool nodeCapHit = false;
    bool deadlineHit = false;
};

namespace detail {

struct Move {
    int block = -1;
    int from = -1;
    int to = -1;
    double reducedCost = 0.0;
};

inline bool completeAndAudited(const core::Problem& pb, core::Sol& s, int horizon) {
    if ((int)s.placed.size() != pb.n || (int)s.byBay.size() != pb.m) return false;
    for (int i = 0; i < pb.n; ++i) {
        if (!s.placed[i] || s.bay[i] < 0 || s.bay[i] >= pb.m ||
            s.ori[i] < 0 || s.ori[i] >= (int)pb.geo[i].orients.size() ||
            s.e[i] < pb.R[i] || s.f[i] != s.e[i] + pb.P[i] || s.f[i] > horizon)
            return false;
    }
    s.rebuildIdx(pb.m);
    return alns::audit(pb, s, nullptr, true) == 0 && core::stateExactEvents(pb, s);
}

inline void rebuildOcc(const core::Problem& pb, core::Sol& s, int horizon,
                       occ::OccIndex& oc) {
    s.rebuildIdx(pb.m);
    oc.build(pb, horizon);
    for (int i = 0; i < pb.n; ++i) if (s.placed[i]) oc.stamp(pb, s, i, true);
}

inline repair::Ctx context(const core::Problem& pb, core::Sol& s, occ::OccIndex& oc,
                           int horizon,
                           std::chrono::steady_clock::time_point deadline,
                           uint64_t seed) {
    repair::Ctx c{};
    c.pb = &pb;
    c.s = &s;
    c.oc = &oc;
    c.horizon = horizon;
    c.exactEvents = true;
    c.hasDeadline = true;
    c.deadline = deadline;
    c.rng.seed((uint32_t)(seed ^ (seed >> 32)));
    return c;
}

inline double footprint(const core::Problem& pb, int i, int ori) {
    const auto& g = pb.geo[i].orients[ori];
    const double box = (g.bb.x1 - g.bb.x0) * (g.bb.y1 - g.bb.y0);
    return g.parea > 0.0 ? g.parea : box;
}

inline double bayLoad(const core::Problem& pb, const core::Sol& s, int bay,
                      int begin, int end) {
    const double cap = std::max(1.0, pb.bayW[bay] * pb.bayH[bay]);
    double load = 0.0;
    int days = 0;
    for (int t = begin; t < end; ++t) {
        double used = 0.0;
        for (int k : s.byBay[bay]) if (s.placed[k] && s.e[k] <= t && t < s.f[k])
            used += footprint(pb, k, s.ori[k]);
        load += used / cap;
        ++days;
    }
    return days ? load / days : 0.0;
}

inline uint64_t tieKey(uint64_t seed, int block, int bay) {
    uint64_t x = seed ^ ((uint64_t)(uint32_t)block << 32) ^ (uint32_t)bay;
    x ^= x >> 30; x *= 0xbf58476d1ce4e5b9ULL;
    x ^= x >> 27; x *= 0x94d049bb133111ebULL;
    return x ^ (x >> 31);
}

inline void makeMoves(const core::Problem& pb, const core::Sol& s, uint64_t seed,
                      const std::vector<int>& tabuUntil, long round,
                      std::vector<Move>& moves) {
    moves.clear();
    for (int i = 0; i < pb.n; ++i) {
        const int from = s.bay[i];
        const int finish = s.f[i];
        const double oldRegret = pb.Smax[i] - pb.pref[i][from];
        std::vector<Move> perBlock;
        for (int to = 0; to < pb.m; ++to) {
            if (to == from || tabuUntil[(size_t)i * pb.m + to] > round) continue;
            // The target's current occupancy is a surrogate only. Exact placement
            // and full checker replay below remain the acceptance authority.
            const double targetLoad = bayLoad(pb, s, to, s.e[i], s.f[i]);
            const double sourceLoad = bayLoad(pb, s, from, s.e[i], s.f[i]);
            const double prefDelta = pb.w3 * ((pb.Smax[i] - pb.pref[i][to]) - oldRegret);
            const double timePressure = pb.w1 * (double)std::max(0, finish - pb.D[i]) /
                                        std::max(1, pb.P[i]);
            const double timeDelta = timePressure * (targetLoad - sourceLoad);
            const double loadDelta = 0.35 * (targetLoad - sourceLoad);
            const double rc = prefDelta + timeDelta + loadDelta;
            perBlock.push_back({i, from, to, rc});
        }
        std::sort(perBlock.begin(), perBlock.end(), [&](const Move& a, const Move& b) {
            if (a.reducedCost != b.reducedCost) return a.reducedCost < b.reducedCost;
            return tieKey(seed, a.block, a.to) < tieKey(seed, b.block, b.to);
        });
        const int keep = std::min<int>(3, perBlock.size());
        moves.insert(moves.end(), perBlock.begin(), perBlock.begin() + keep);
    }
    std::sort(moves.begin(), moves.end(), [&](const Move& a, const Move& b) {
        if (a.reducedCost != b.reducedCost) return a.reducedCost < b.reducedCost;
        return tieKey(seed, a.block, a.to) < tieKey(seed, b.block, b.to);
    });
}

inline void orderForPolicy(const core::Problem& pb, const core::Sol& base,
                           std::vector<int>& ids, int policy, uint64_t seed) {
    std::sort(ids.begin(), ids.end(), [&](int a, int b) {
        if (policy == 0) { // earliest due date then longest duration
            if (pb.D[a] != pb.D[b]) return pb.D[a] < pb.D[b];
            if (pb.P[a] != pb.P[b]) return pb.P[a] > pb.P[b];
        } else if (policy == 1) { // largest/currently tight footprint first
            const double aa = footprint(pb, a, base.ori[a]);
            const double ab = footprint(pb, b, base.ori[b]);
            if (aa != ab) return aa > ab;
            if (pb.R[a] != pb.R[b]) return pb.R[a] < pb.R[b];
        } else { // preference-regret first; deterministic salt breaks plateaus
            const double ra = pb.Smax[a] - pb.pref[a][base.bay[a]];
            const double rb = pb.Smax[b] - pb.pref[b][base.bay[b]];
            if (ra != rb) return ra > rb;
            const uint64_t ka = tieKey(seed, a, policy), kb = tieKey(seed, b, policy);
            if (ka != kb) return ka < kb;
        }
        return a < b;
    });
}

inline bool repackCycle(const core::Problem& pb, const core::Sol& base, int horizon,
                        const std::vector<Move>& cycle, int policy,
                        std::chrono::steady_clock::time_point deadline, uint64_t seed,
                        core::Sol& out, Result& result) {
    out = base;
    occ::OccIndex oc;
    rebuildOcc(pb, out, horizon, oc);
    repair::Ctx c = context(pb, out, oc, horizon, deadline, seed);
    std::vector<int> forced(pb.n, -1), ids, affected;
    ids.reserve(cycle.size());
    for (const Move& move : cycle) {
        if (move.block < 0 || move.block >= pb.n || forced[move.block] >= 0) return false;
        forced[move.block] = move.to;
        ids.push_back(move.block);
        affected.push_back(move.from);
        affected.push_back(move.to);
    }
    std::sort(affected.begin(), affected.end());
    affected.erase(std::unique(affected.begin(), affected.end()), affected.end());
    result.affectedBayRepacks += (long)affected.size();
    for (int i : ids) repair::removeBlock(c, i);
    c.forcedBay = forced.data();
    c.releaseForcedBay = false;
    orderForPolicy(pb, base, ids, policy, seed);
    repair_scored::Config cfg;
    cfg.topK = policy == 0 ? 18 : (policy == 1 ? 24 : 20);
    cfg.maxCandidates = policy == 0 ? 360 : (policy == 1 ? 480 : 420);
    cfg.maxTimes = 56;
    cfg.contactAxisCap = 72;
    cfg.residualWeight = policy == 1 ? 0.32 : 0.20;
    cfg.futureBlockingWeight = policy == 2 ? 0.48 : 0.35;
    cfg.compactnessWeight = policy == 0 ? 0.10 : 0.18;
    for (int i : ids) {
        repair_scored::Stats stats;
        if (repair::deadlineExpired(c) || !repair_scored::insertBest(c, i, cfg, &stats)) return false;
    }
    if (repair::deadlineExpired(c)) return false;
    ++result.exactAudits;
    if (!completeAndAudited(pb, out, horizon)) return false;
    ++result.completeFeasibleCandidates;
    return true;
}

} // namespace detail

template<class EngineT>
Result run(EngineT& engine, double seconds, uint64_t seed) {
    using Clock = std::chrono::steady_clock;
    Result result;
    const core::Problem& pb = engine.pb;
    const int horizon = engine.horizon_;
    core::Sol initial = engine.sol;
    if (seconds <= 0.0 || pb.n < 2 || pb.m < 2 || horizon <= 0 ||
        !detail::completeAndAudited(pb, initial, horizon)) return result;

    result.initialObjective = core::evaluate(pb, initial).total;
    result.bestObjective = result.initialObjective;
    const auto deadline = Clock::now() + std::chrono::duration_cast<Clock::duration>(
        std::chrono::duration<double>(seconds));
    const long nodeLimit = std::max<long>(256, std::min<long>(24000, 64L * pb.n));
    std::mt19937_64 rng(seed ^ 0xd1b54a32d192ed03ULL);
    core::Sol incumbent = initial, best = initial;
    std::vector<int> tabuUntil((size_t)pb.n * pb.m, 0);
    std::vector<detail::Move> moves;
    long round = 0;

    while (Clock::now() < deadline && result.nodes < nodeLimit) {
        ++round;
        detail::makeMoves(pb, incumbent, seed ^ (uint64_t)round, tabuUntil, round, moves);
        if (moves.empty()) break;
        const size_t start = (size_t)(rng() % moves.size());
        bool attempted = false;
        for (size_t offset = 0; offset < moves.size() && result.nodes < nodeLimit; ++offset) {
            if (Clock::now() >= deadline) break;
            const detail::Move first = moves[(start + offset) % moves.size()];
            std::vector<detail::Move> chain{first};
            std::vector<uint8_t> used(pb.n, 0);
            used[first.block] = 1;
            int needFrom = first.to;
            bool closed = false;
            // A path is grown by bay continuity. Its closing arc is a genuine
            // assignment cycle, hence resident counts are preserved at every bay.
            for (int length = 2; length <= 12; ++length) {
                int pick = -1;
                double pickCost = std::numeric_limits<double>::infinity();
                for (size_t q = 0; q < moves.size(); ++q) {
                    const detail::Move& candidate = moves[(q + start + length) % moves.size()];
                    if (candidate.from != needFrom || used[candidate.block]) continue;
                    const double diversification = ((round % 7) == 0)
                        ? 0.02 * (double)(detail::tieKey(seed ^ round, candidate.block, candidate.to) & 15ULL)
                        : 0.0;
                    if (candidate.reducedCost + diversification < pickCost) {
                        pickCost = candidate.reducedCost + diversification;
                        pick = (int)q;
                    }
                }
                if (pick < 0) break;
                const detail::Move next = moves[(pick + start + length) % moves.size()];
                chain.push_back(next);
                used[next.block] = 1;
                needFrom = next.to;
                ++result.nodes;
                if ((int)chain.size() >= 2) ++result.chains;
                if (needFrom == first.from) { closed = true; ++result.cycles; break; }
            }
            if (!closed) continue;
            attempted = true;
            for (int policy = 0; policy != 3 && Clock::now() < deadline; ++policy) {
                core::Sol trial;
                if (!detail::repackCycle(pb, incumbent, horizon, chain, policy, deadline,
                                         seed ^ (uint64_t)round ^ ((uint64_t)policy << 48),
                                         trial, result)) continue;
                const double value = core::evaluate(pb, trial).total;
                if (value + 1e-9 < result.bestObjective) {
                    result.bestObjective = value;
                    best = trial;
                    incumbent = trial;
                    ++result.acceptedGains;
                    result.improved = true;
                    for (const detail::Move& m : chain)
                        tabuUntil[(size_t)m.block * pb.m + m.from] = (int)(round + 3 + rng() % 5);
                    break;
                }
            }
            if (result.improved && incumbent.bay != initial.bay) break;
            for (const detail::Move& m : chain)
                tabuUntil[(size_t)m.block * pb.m + m.to] = (int)(round + 2);
        }
        if (!attempted) break;
    }
    result.deadlineHit = Clock::now() >= deadline;
    result.nodeCapHit = result.nodes >= nodeLimit;
    if (result.improved) {
        // The only live mutation occurs after a strict-gain exact audit. buildOcc
        // restores the engine's occupancy/context invariants for its next caller.
        ++result.exactAudits;
        if (detail::completeAndAudited(pb, best, horizon) &&
            core::evaluate(pb, best).total + 1e-9 < result.initialObjective) {
            engine.sol = std::move(best);
            engine.buildOcc(horizon);
        } else {
            result.improved = false;
            result.bestObjective = result.initialObjective;
            result.acceptedGains = 0;
        }
    }
    return result;
}

} // namespace kernel_ejection_chain

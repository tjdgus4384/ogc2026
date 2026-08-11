// kernel_large_ruin.hpp -- bounded large-neighbourhood ruin/recreate with exact replay.
#pragma once

#include "alns.hpp"
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <limits>
#include <random>
#include <utility>
#include <vector>

namespace kernel_large_ruin {

struct Options {
    size_t beamWidth = 10;
    size_t actionCap = 36;
    size_t bayCap = 4;
    size_t eventCap = 10;
    size_t populationCap = 6;
    size_t localPolishBlocks = 4;
    uint64_t iterationCap = 0; // zero is wall-clock bounded only
};

struct Result {
    uint64_t ruinAttempts = 0;
    uint64_t ruinBlocks = 0;
    uint64_t beamStates = 0;
    uint64_t completeReconstructions = 0;
    uint64_t exactAudits = 0;
    uint64_t populationStores = 0;
    uint64_t backboneReuses = 0;
    uint64_t acceptedGains = 0;
    uint64_t deadlineExits = 0;
    uint64_t capExits = 0;
    size_t smallestRuin = 0;
    size_t largestRuin = 0;
    double initialObjective = std::numeric_limits<double>::infinity();
    double bestObjective = std::numeric_limits<double>::infinity();
    double acceptedGain = 0.0;
    bool improved = false;
    bool deadlineHit = false;
    bool capHit = false;
};

namespace detail {
using Clock = std::chrono::steady_clock;

struct State {
    core::Sol sol;
    double rank = std::numeric_limits<double>::infinity();
};
struct Backbone {
    core::Sol sol;
    std::vector<int> retained;
    double value = std::numeric_limits<double>::infinity();
};

inline int horizonFor(const core::Problem& pb, int supplied) {
    int h = supplied;
    for (int i = 0; i < pb.n; ++i) h = std::max(h, std::max(pb.R[i], pb.D[i]) + pb.P[i] + 1);
    return std::max(1, h);
}

inline bool completeReplay(const core::Problem& pb, core::Sol& s, int horizon) {
    if ((int)s.placed.size() != pb.n) return false;
    for (int i = 0; i < pb.n; ++i)
        if (!s.placed[i] || s.bay[i] < 0 || s.bay[i] >= pb.m ||
            s.ori[i] < 0 || s.ori[i] >= (int)pb.geo[i].orients.size() ||
            s.e[i] < pb.R[i] || s.f[i] != s.e[i] + pb.P[i] || s.f[i] > horizon)
            return false;
    s.rebuildIdx(pb.m);
    return core::stateExactEvents(pb, s) && alns::audit(pb, s, nullptr, true) == 0;
}

inline double pressure(const core::Problem& pb, const core::Sol& s, int i) {
    const double tardy = std::max(0, s.f[i] - pb.D[i]);
    const double pref = pb.Smax[i] - pb.pref[i][s.bay[i]];
    int timeNeighbours = 0;
    for (int k = 0; k < pb.n; ++k) if (k != i && s.placed[k] && s.bay[k] == s.bay[i] &&
        s.e[k] < s.f[i] && s.e[i] < s.f[k]) ++timeNeighbours;
    const int slack = std::max(0, pb.D[i] - pb.R[i] - pb.P[i]);
    return 1000.0 * tardy + 25.0 * pref + 5.0 * timeNeighbours - 0.02 * slack;
}

inline std::vector<int> correlatedRuin(const core::Problem& pb, const core::Sol& s, size_t q,
                                        uint64_t salt, const std::vector<int>* protectedIds = nullptr) {
    std::vector<std::pair<double, int>> seeds;
    seeds.reserve(pb.n);
    for (int i = 0; i < pb.n; ++i) seeds.push_back({pressure(pb, s, i), i});
    std::sort(seeds.begin(), seeds.end(), [](const auto& a, const auto& b) {
        return a.first != b.first ? a.first > b.first : a.second < b.second;
    });
    const int pivot = seeds[(size_t)(salt % seeds.size()) % std::min<size_t>(seeds.size(), 8)].second;
    std::vector<std::pair<double, int>> scored;
    std::vector<char> protectedBlock(pb.n, 0);
    if (protectedIds) for (int i : *protectedIds) protectedBlock[i] = 1;
    scored.reserve(pb.n);
    for (int i = 0; i < pb.n; ++i) {
        const bool sameBay = s.bay[i] == s.bay[pivot];
        const bool timeNear = s.e[i] < s.f[pivot] + pb.P[i] && s.e[pivot] < s.f[i] + pb.P[pivot];
        const double dx = std::abs(s.x[i] - s.x[pivot]);
        const double dy = std::abs(s.y[i] - s.y[pivot]);
        const double spatial = 1.0 / (1.0 + dx + dy);
        const double assignment = pb.pref[i][s.bay[pivot]] - pb.pref[i][s.bay[i]];
        const double protectedPenalty = protectedBlock[i] ? -1e12 : 0.0;
        scored.push_back({pressure(pb, s, i) + 30.0 * sameBay + 20.0 * timeNear +
                          10.0 * spatial + 3.0 * assignment + protectedPenalty, i});
    }
    std::sort(scored.begin(), scored.end(), [](const auto& a, const auto& b) {
        return a.first != b.first ? a.first > b.first : a.second < b.second;
    });
    std::vector<int> out;
    for (size_t k = 0; k < q; ++k) out.push_back(scored[k].second);
    return out;
}

inline double rank(const core::Problem& pb, const core::Sol& s) {
    double value = 0.0;
    for (int i = 0; i < pb.n; ++i) if (s.placed[i]) {
        value += pb.w1 * std::max(0, s.f[i] - pb.D[i]);
        value += pb.w3 * (pb.Smax[i] - pb.pref[i][s.bay[i]]);
    }
    return value;
}

inline void rebuildOcc(const core::Problem& pb, core::Sol& s, int horizon, occ::OccIndex& oc) {
    s.rebuildIdx(pb.m);
    oc.build(pb, horizon);
    for (int i = 0; i < pb.n; ++i) if (s.placed[i]) oc.stamp(pb, s, i, true);
}

inline std::vector<int> orderFor(const core::Problem& pb, const core::Sol& guide,
                                 std::vector<int> ids, int mode) {
    std::sort(ids.begin(), ids.end(), [&](int a, int b) {
        if (mode == 0) { // urgency first
            const int sa = pb.D[a] - pb.R[a] - pb.P[a], sb = pb.D[b] - pb.R[b] - pb.P[b];
            return sa != sb ? sa < sb : a < b;
        }
        if (mode == 1) { // pressure first
            const double pa = pressure(pb, guide, a), pbv = pressure(pb, guide, b);
            return pa != pbv ? pa > pbv : a < b;
        }
        // Regret order protects blocks whose best assignment is much better
        // than their runner-up; beam alternatives then realize that estimate
        // with exact geometry/event actions rather than trusting it as a cut.
        auto regret = [&](int i) {
            double first = -std::numeric_limits<double>::infinity();
            double second = first;
            for (int j = 0; j < pb.m; ++j) {
                const double v = pb.pref[i][j];
                if (v > first) { second = first; first = v; }
                else if (v > second) second = v;
            }
            if (!std::isfinite(second)) second = first;
            return first - second + 1.0 / (1.0 + std::max(0, pb.D[i] - pb.R[i] - pb.P[i]));
        };
        const double ra = regret(a), rb = regret(b);
        return ra != rb ? ra > rb : a < b;
    });
    return ids;
}

inline std::vector<int> eventTimes(const core::Problem& pb, const core::Sol& s,
                                   const core::Sol& guide, int block, int horizon, size_t cap) {
    std::vector<int> times{pb.R[block], std::max(pb.R[block], pb.D[block] - pb.P[block]), guide.e[block]};
    for (int k = 0; k < pb.n; ++k) if (s.placed[k]) { times.push_back(s.e[k]); times.push_back(s.f[k]); }
    const int latest = horizon - pb.P[block];
    if (latest >= pb.R[block]) times.push_back(latest);
    std::sort(times.begin(), times.end());
    times.erase(std::unique(times.begin(), times.end()), times.end());
    times.erase(std::remove_if(times.begin(), times.end(), [&](int e) {
        return e < pb.R[block] || e + pb.P[block] > horizon;
    }), times.end());
    if (times.size() > cap) times.resize(cap);
    return times;
}

inline void appendChild(const core::Problem& pb, const State& parent, const core::Sol& guide,
                        int block, int bay, int ori, int x, int y, int entry, int horizon,
                        Clock::time_point deadline, std::vector<State>& out, size_t cap) {
    if (out.size() >= cap || Clock::now() >= deadline) return;
    State child; child.sol = parent.sol;
    occ::OccIndex oc; rebuildOcc(pb, child.sol, horizon, oc);
    repair::Ctx ctx{&pb, &child.sol, &oc, std::mt19937(0), horizon};
    ctx.exactEvents = true; ctx.hasDeadline = true; ctx.deadline = deadline;
    if (!repair::tryPlacement(ctx, block, bay, ori, x, y, entry)) return;
    child.rank = rank(pb, child.sol);
    out.push_back(std::move(child));
}

inline void expand(const core::Problem& pb, const State& parent, const core::Sol& guide, int block,
                   int horizon, Clock::time_point deadline, const Options& opt,
                   std::vector<State>& out) {
    if (Clock::now() >= deadline) return;
    // The incumbent action is an exact witness and prevents contact enumeration
    // from making a feasible reconstruction disappear merely through truncation.
    if (guide.placed[block]) appendChild(pb, parent, guide, block, guide.bay[block], guide.ori[block],
                                         guide.x[block], guide.y[block], guide.e[block], horizon,
                                         deadline, out, opt.actionCap);
    std::vector<int> bays(pb.m);
    for (int j = 0; j < pb.m; ++j) bays[j] = j;
    std::sort(bays.begin(), bays.end(), [&](int a, int b) {
        return pb.pref[block][a] != pb.pref[block][b] ? pb.pref[block][a] > pb.pref[block][b] : a < b;
    });
    if (bays.size() > opt.bayCap) bays.resize(opt.bayCap);
    const std::vector<int> times = eventTimes(pb, parent.sol, guide, block, horizon, opt.eventCap);
    for (int entry : times) for (int bay : bays) {
        if (Clock::now() >= deadline || out.size() >= opt.actionCap) return;
        occ::OccIndex oc; core::Sol basis = parent.sol; rebuildOcc(pb, basis, horizon, oc);
        repair::Ctx ctx{&pb, &basis, &oc, std::mt19937(0), horizon};
        ctx.exactEvents = true; ctx.hasDeadline = true; ctx.deadline = deadline;
        std::vector<repair::RB> rbs; repair::rbCollect(ctx, bay, entry, entry + pb.P[block], rbs);
        for (int ori = 0; ori < (int)pb.geo[block].orients.size(); ++ori) {
            std::vector<int> xs, ys;
            repair::cornerSetsRB(ctx, block, ori, bay, rbs, xs, ys);
            repair::shapeContactSets(ctx, block, ori, bay, entry, entry + pb.P[block], xs, ys, 48);
            for (int x : xs) for (int y : ys) {
                appendChild(pb, parent, guide, block, bay, ori, x, y, entry, horizon, deadline, out, opt.actionCap);
                if (Clock::now() >= deadline || out.size() >= opt.actionCap) return;
            }
        }
    }
}

inline bool reconstruct(const core::Problem& pb, const core::Sol& root, const core::Sol& guide,
                        const std::vector<int>& order, int horizon, Clock::time_point deadline,
                        const Options& opt, Result& result, core::Sol& out) {
    State first; first.sol = root; first.rank = rank(pb, root);
    std::vector<State> beam{std::move(first)};
    for (int block : order) {
        std::vector<State> next;
        for (const State& state : beam) {
            expand(pb, state, guide, block, horizon, deadline, opt, next);
            if (Clock::now() >= deadline) return false;
        }
        result.beamStates += next.size();
        std::sort(next.begin(), next.end(), [](const State& a, const State& b) { return a.rank < b.rank; });
        if (next.size() > opt.beamWidth) next.resize(opt.beamWidth);
        beam.swap(next);
        if (beam.empty()) return false;
    }
    for (State& state : beam) {
        ++result.completeReconstructions; ++result.exactAudits;
        if (completeReplay(pb, state.sol, horizon)) { out = std::move(state.sol); return true; }
    }
    return false;
}

inline bool polishAffected(const core::Problem& pb, const core::Sol& candidate,
                           const std::vector<int>& ruined, int horizon, Clock::time_point deadline,
                           const Options& opt, Result& result, core::Sol& polished) {
    std::vector<char> affected(pb.m, 0);
    for (int i : ruined) affected[candidate.bay[i]] = 1;
    std::vector<int> local;
    for (int i = 0; i < pb.n; ++i) if (affected[candidate.bay[i]]) local.push_back(i);
    std::sort(local.begin(), local.end(), [&](int a, int b) { return pressure(pb, candidate, a) > pressure(pb, candidate, b); });
    if (local.size() > opt.localPolishBlocks) local.resize(opt.localPolishBlocks);
    if (local.empty()) { polished = candidate; return true; }
    core::Sol root = candidate;
    for (int i : local) root.placed[i] = 0;
    root.rebuildIdx(pb.m);
    return reconstruct(pb, root, candidate, orderFor(pb, candidate, local, 1), horizon, deadline, opt, result, polished);
}
} // namespace detail

template<class EngineT>
Result run(EngineT& engine, double seconds, uint64_t seed, const Options& options = Options{}) {
    using Clock = std::chrono::steady_clock;
    Result result;
    const core::Problem& pb = engine.pb;
    const int horizon = detail::horizonFor(pb, engine.horizon_);
    core::Sol initial = engine.sol;
    if (seconds <= 0.0 || pb.n < 2 || options.beamWidth == 0 || options.actionCap == 0 ||
        options.bayCap == 0 || options.eventCap == 0 || !detail::completeReplay(pb, initial, horizon))
        return result;
    result.initialObjective = core::evaluate(pb, initial).total;
    result.bestObjective = result.initialObjective;
    core::Sol best = initial;
    std::vector<detail::Backbone> population{{initial, {}, result.initialObjective}};
    const size_t populationCap = std::max<size_t>(1, options.populationCap);
    const auto deadline = Clock::now() + std::chrono::duration_cast<Clock::duration>(std::chrono::duration<double>(seconds));
    std::mt19937_64 rng(seed);
    size_t ruinPercent = 15;
    uint64_t iteration = 0;

    while (Clock::now() < deadline && (!options.iterationCap || iteration < options.iterationCap)) {
        ++iteration; ++result.ruinAttempts;
        const size_t lo = std::max<size_t>(1, (size_t)std::ceil(pb.n * 0.15));
        const size_t hi = std::max(lo, std::min<size_t>((size_t)std::floor(pb.n * 0.40), (size_t)pb.n - 1));
        const size_t q = std::min(hi, std::max(lo, (size_t)((pb.n * ruinPercent + 99) / 100)));
        const size_t sampled = population.size() > 1 ? (size_t)(rng() % population.size()) : 0;
        const bool reuse = population.size() > 1 && iteration % 3 == 0 &&
                           population[sampled].retained.size() <= (size_t)pb.n - q;
        const detail::Backbone& source = reuse ? population[sampled] : population.front();
        if (reuse) ++result.backboneReuses;
        const std::vector<int> ruined = detail::correlatedRuin(pb, source.sol, q, rng(),
                                                                 reuse ? &source.retained : nullptr);
        result.ruinBlocks += ruined.size();
        result.smallestRuin = result.smallestRuin ? std::min(result.smallestRuin, ruined.size()) : ruined.size();
        result.largestRuin = std::max(result.largestRuin, ruined.size());
        core::Sol root = source.sol;
        for (int i : ruined) root.placed[i] = 0;
        root.rebuildIdx(pb.m);
        core::Sol rebuilt;
        const int orderMode = (int)(iteration % 3);
        if (!detail::reconstruct(pb, root, source.sol, detail::orderFor(pb, source.sol, ruined, orderMode),
                                 horizon, deadline, options, result, rebuilt)) {
            ruinPercent = std::max<size_t>(15, ruinPercent > 3 ? ruinPercent - 3 : 15);
            continue;
        }
        core::Sol polished;
        if (!detail::polishAffected(pb, rebuilt, ruined, horizon, deadline, options, result, polished)) polished = rebuilt;
        ++result.exactAudits;
        if (!detail::completeReplay(pb, polished, horizon)) continue;
        const double value = core::evaluate(pb, polished).total;
        if (value + 1e-12 < result.bestObjective) {
            result.acceptedGain += result.bestObjective - value;
            result.bestObjective = value; best = polished; ++result.acceptedGains;
            detail::Backbone saved{best, {}, value};
            std::vector<char> removed(pb.n, 0); for (int i : ruined) removed[i] = 1;
            for (int i = 0; i < pb.n; ++i) if (!removed[i]) saved.retained.push_back(i);
            population.insert(population.begin(), std::move(saved));
            if (population.size() > populationCap) population.pop_back();
            ++result.populationStores;
            ruinPercent = std::min<size_t>(40, ruinPercent + 5);
        } else {
            ruinPercent = std::max<size_t>(15, ruinPercent > 2 ? ruinPercent - 2 : 15);
        }
    }
    result.deadlineHit = Clock::now() >= deadline;
    result.capHit = options.iterationCap && iteration >= options.iterationCap;
    result.deadlineExits = result.deadlineHit ? 1 : 0;
    result.capExits = result.capHit ? 1 : 0;
    result.improved = result.bestObjective + 1e-12 < result.initialObjective;
    if (result.improved) { engine.sol = std::move(best); engine.buildOcc(horizon); }
    return result;
}
} // namespace kernel_large_ruin

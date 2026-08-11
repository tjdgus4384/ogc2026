// kernel_parallel_bandit.hpp -- synchronized four-lane contextual ALNS portfolio.
#pragma once

#include "kernel_bandit.hpp"
#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <limits>
#include <thread>
#include <vector>

namespace kernel_parallel_bandit {

using Policy = kernel_bandit::Policy;

struct Result {
    long batches = 0;
    long laneRuns = 0;
    long feasibleLanes = 0;
    long exchanges = 0;
    long modelUpdates = 0;
    double diversity = 0.0;
    double peakDiversity = 0.0;
    long populationSize = 0;
    std::array<Policy, 4> selectedPolicies{};
    double initialObjective = std::numeric_limits<double>::infinity();
    double bestObjective = std::numeric_limits<double>::infinity();
    bool improved = false;
    bool deadlineHit = false;
};

namespace detail {
constexpr size_t LANES = 4;
constexpr size_t POPULATION = 8;

inline uint64_t splitSeed(uint64_t seed, uint64_t stream) {
    uint64_t z = seed + 0x9e3779b97f4a7c15ull * (stream + 1ull);
    z = (z ^ (z >> 30)) * 0xbf58476d1ce4e5b9ull;
    z = (z ^ (z >> 27)) * 0x94d049bb133111ebull;
    return z ^ (z >> 31);
}

inline void rebuild(const core::Problem& pb, core::Sol& s, occ::OccIndex& oc,
                    repair::Ctx& c) {
    if (oc.T != c.horizon || (int)oc.bay.size() != pb.m)
        oc.build(pb, c.horizon);
    s.rebuildIdx(pb.m);
    oc.clearBits();
    for (int i = 0; i < pb.n; ++i)
        if (s.placed[i]) oc.stamp(pb, s, i, true);
    c.pb = &pb;
    c.s = &s;
    c.oc = &oc;
}

inline bool completeAndValid(const core::Problem& pb, core::Sol& s, int horizon) {
    if ((int)s.placed.size() != pb.n || (int)s.byBay.size() != pb.m) return false;
    for (int i = 0; i < pb.n; ++i) {
        if (!s.placed[i] || s.bay[i] < 0 || s.bay[i] >= pb.m ||
            s.ori[i] < 0 || s.ori[i] >= (int)pb.geo[i].orients.size() ||
            s.e[i] < pb.R[i] || s.f[i] != s.e[i] + pb.P[i] || s.f[i] > horizon)
            return false;
    }
    s.rebuildIdx(pb.m);
    return alns::audit(pb, s, nullptr, true) == 0;
}

inline double objective(const core::Problem& pb, const core::Sol& s) {
    return core::evaluatePen(pb, s).total;
}

inline double distance(const core::Problem& pb, const core::Sol& a, const core::Sol& b) {
    if (pb.n == 0) return 0.0;
    int different = 0;
    for (int i = 0; i < pb.n; ++i)
        different += a.bay[i] != b.bay[i] || a.ori[i] != b.ori[i] ||
                     a.e[i] != b.e[i] || a.x[i] != b.x[i] || a.y[i] != b.y[i];
    return (double)different / (double)pb.n;
}

inline double diversity(const core::Problem& pb, const std::vector<core::Sol>& pop) {
    if (pop.size() < 2) return 0.0;
    double sum = 0.0;
    long pairs = 0;
    for (size_t i = 0; i < pop.size(); ++i)
        for (size_t j = 0; j < i; ++j) { sum += distance(pb, pop[i], pop[j]); ++pairs; }
    return sum / (double)pairs;
}

struct Lane {
    core::Sol sol;
    occ::OccIndex oc;
    repair::Ctx ctx{};
    double before = std::numeric_limits<double>::infinity();
    double objective = std::numeric_limits<double>::infinity();
    double elapsed = 0.0;
    bool feasible = false;
};

inline void configure(repair::Ctx& c, const Policy& p,
                      std::chrono::steady_clock::time_point deadline,
                      int originalDens, double originalPref) {
    c.exactEvents = p.exactEvents;
    c.densMode = p.assignmentPressure == 2 ? 1 :
                 (p.assignmentPressure == 0 ? 0 : originalDens);
    c.prefP = p.assignmentPressure == 1 ? 0.92 : originalPref;
    c.hasDeadline = true;
    c.deadline = deadline;
}
} // namespace detail

template<class EngineT>
Result run(EngineT& engine, double seconds, uint64_t seed) {
    using Clock = std::chrono::steady_clock;
    Result out;
    if (seconds <= 0.0 || engine.pb.n <= 0 || engine.pb.m <= 0) return out;

    const core::Problem& pb = engine.pb;
    const int horizon = engine.horizon_;
    const core::Sol entry = engine.sol;
    core::Sol audited = entry;
    if (horizon <= 0 || !detail::completeAndValid(pb, audited, horizon)) return out;

    const auto started = Clock::now();
    const auto deadline = started + std::chrono::duration_cast<Clock::duration>(
        std::chrono::duration<double>(seconds));
    out.initialObjective = detail::objective(pb, entry);
    out.bestObjective = out.initialObjective;
    core::Sol best = entry;
    std::vector<core::Sol> population{entry};
    std::vector<double> populationObjectives{out.initialObjective};

    const auto policies = kernel_bandit::detail::policies();
    std::vector<kernel_bandit::detail::LinearUcb> models(policies.size());
    std::array<long, 4> repairModePulls{};
    std::array<double, 4> repairModeRewards{};
    const int originalDens = engine.ctx.densMode;
    const double originalPref = engine.ctx.prefP;
    const repair::Ctx baseCtx = engine.ctx;

    while (Clock::now() < deadline) {
        const double remaining = std::chrono::duration<double>(deadline - Clock::now()).count();
        if (remaining <= 0.002) break;
        const auto features = kernel_bandit::detail::features(pb, best, out.bestObjective);
        std::array<size_t, detail::LANES> chosen{};
        std::array<bool, 64> used{};
        for (size_t lane = 0; lane < detail::LANES; ++lane) {
            size_t pick = lane % policies.size();
            if (out.batches >= 1) {
                double top = -std::numeric_limits<double>::infinity();
                const double bonus = 0.20 * std::sqrt(std::log(2.0 + out.modelUpdates));
                for (size_t arm = 0; arm < policies.size(); ++arm) {
                    if (used[arm]) continue;
                    const double score = models[arm].score(features, bonus);
                    if (score > top || (score == top && arm < pick)) { top = score; pick = arm; }
                }
            }
            chosen[lane] = pick;
            used[pick] = true;
            out.selectedPolicies[lane] = policies[pick];
        }
        std::array<int, detail::LANES> repairModes{};
        if (out.batches == 0) {
            for (size_t lane = 0; lane < detail::LANES; ++lane)
                repairModes[lane] = (int)lane;
        } else {
            int bestMode = 0;
            double bestMean = -std::numeric_limits<double>::infinity();
            for (int mode = 0; mode < 4; ++mode) {
                const double mean = repairModeRewards[mode] /
                    (double)std::max(1L, repairModePulls[mode]);
                if (mean > bestMean) { bestMean = mean; bestMode = mode; }
            }
            repairModes = {bestMode, bestMode, 0, (int)(out.batches % 4)};
        }

        const double batchSeconds = std::min(remaining, std::max(0.004, seconds / 32.0));
        std::array<detail::Lane, detail::LANES> lanes;
        std::array<std::thread, detail::LANES> workers;
        for (size_t lane = 0; lane < detail::LANES; ++lane) {
            const core::Sol start = population[(out.batches + lane) % population.size()];
            const Policy policy = policies[chosen[lane]];
            const uint64_t laneSeed = detail::splitSeed(seed, (uint64_t)out.batches * detail::LANES + lane);
            workers[lane] = std::thread([&, lane, start, policy, laneSeed] {
                detail::Lane& state = lanes[lane];
                state.sol = start;
                state.ctx = baseCtx;
                detail::rebuild(pb, state.sol, state.oc, state.ctx);
                detail::configure(state.ctx, policy, deadline, originalDens, originalPref);
                state.before = detail::objective(pb, state.sol);
                const auto laneStarted = Clock::now();
                alns::RunParams rp;
                rp.repairMode = repairModes[lane];
                rp.qloF = policy.qlo;
                rp.qhiF = policy.qhi;
                rp.opMask = policy.opMask;
                rp.bigQProb = policy.repairOrder == 2 ? 0.12 :
                              (policy.repairOrder == 1 ? 0.06 : 0.02);
                alns::run(state.ctx, batchSeconds, rp, laneSeed, 0);
                if (policy.localPasses && Clock::now() < deadline) {
                    alns::spatialCompact(state.ctx, policy.localPasses);
                    alns::compactLeft(state.ctx, std::max(1, policy.localPasses / 2));
                }
                state.elapsed = std::max(1e-6,
                    std::chrono::duration<double>(Clock::now() - laneStarted).count());
                detail::rebuild(pb, state.sol, state.oc, state.ctx);
                state.feasible = Clock::now() <= deadline &&
                    detail::completeAndValid(pb, state.sol, horizon);
                if (state.feasible) state.objective = detail::objective(pb, state.sol);
            });
        }
        for (size_t lane = 0; lane < detail::LANES; ++lane) workers[lane].join();
        ++out.batches;

        // This ordered coordinator is the sole owner of population and models.
        for (size_t lane = 0; lane < detail::LANES; ++lane) {
            ++out.laneRuns;
            detail::Lane& state = lanes[lane];
            const double gain = state.feasible ? std::max(0.0, state.before - state.objective) : 0.0;
            const double reward = (state.feasible ? gain / state.elapsed : -1.0 / state.elapsed) /
                                  std::max(1.0, std::fabs(state.before));
            models[chosen[lane]].update(features, reward);
            ++repairModePulls[repairModes[lane]];
            repairModeRewards[repairModes[lane]] += reward;
            ++out.modelUpdates;
            if (!state.feasible) continue;
            ++out.feasibleLanes;
            size_t nearest = 0;
            double nearestDistance = std::numeric_limits<double>::infinity();
            for (size_t i = 0; i < population.size(); ++i) {
                const double d = detail::distance(pb, population[i], state.sol);
                if (d < nearestDistance) { nearestDistance = d; nearest = i; }
            }
            if (state.objective < out.bestObjective - 1e-9) {
                best = state.sol;
                out.bestObjective = state.objective;
                out.selectedPolicies[lane] = policies[chosen[lane]];
            }
            const bool distinct = nearestDistance >= 0.04;
            if (distinct && population.size() < detail::POPULATION) {
                population.push_back(state.sol);
                populationObjectives.push_back(state.objective);
                ++out.exchanges;
            } else if (distinct) {
                const size_t worst = (size_t)(std::max_element(populationObjectives.begin(),
                    populationObjectives.end()) - populationObjectives.begin());
                if (state.objective < populationObjectives[worst]) {
                    population[worst] = state.sol;
                    populationObjectives[worst] = state.objective;
                    ++out.exchanges;
                }
            } else if (state.objective < populationObjectives[nearest] - 1e-9) {
                population[nearest] = state.sol;
                populationObjectives[nearest] = state.objective;
                ++out.exchanges;
            }
        }
        out.peakDiversity = std::max(out.peakDiversity, detail::diversity(pb, population));
    }

    out.populationSize = (long)population.size();
    out.diversity = detail::diversity(pb, population);
    out.improved = out.bestObjective < out.initialObjective - 1e-9;
    out.deadlineHit = Clock::now() >= deadline;
    if (out.improved) {
        core::Sol candidate = best;
        if (detail::completeAndValid(pb, candidate, horizon) &&
            detail::objective(pb, candidate) < out.initialObjective - 1e-9) {
            engine.sol = candidate;
            engine.sol.rebuildIdx(pb.m);
            engine.oc.clearBits();
            for (int i = 0; i < pb.n; ++i) engine.oc.stamp(pb, engine.sol, i, true);
            engine.ctx.pb = &pb;
            engine.ctx.s = &engine.sol;
            engine.ctx.oc = &engine.oc;
        } else {
            out.bestObjective = out.initialObjective;
            out.improved = false;
        }
    }
    return out;
}

} // namespace kernel_parallel_bandit

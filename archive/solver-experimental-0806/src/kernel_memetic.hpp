// kernel_memetic.hpp -- bounded memetic search over complete, checker-audited states.
#pragma once

#include <algorithm>
#include <cmath>
#include "alns.hpp"
#include <chrono>
#include <cstdint>
#include <limits>
#include <random>
#include <utility>
#include <vector>

namespace kernel_memetic {

struct Result {
    long iterations = 0;
    long feasibleOffspring = 0;
    long migrations = 0;
    long restarts = 0;
    double initialObjective = 0.0;
    double bestObjective = 0.0;
    double diversity = 0.0;
    double peakDiversity = 0.0;
    double offspringNovelty = 0.0;
    long paretoSurvivors = 0;
    bool improved = false;
    bool deadlineHit = false;
};

namespace detail {

inline bool completeAndValid(const core::Problem& pb, core::Sol& s, int horizon) {
    if ((int)s.placed.size() != pb.n || (int)s.byBay.size() != pb.m) return false;
    for (int i = 0; i < pb.n; ++i) {
        if (!s.placed[i] || s.bay[i] < 0 || s.bay[i] >= pb.m ||
            s.ori[i] < 0 || s.ori[i] >= (int)pb.geo[i].orients.size() ||
            s.e[i] < pb.R[i] || s.f[i] != s.e[i] + pb.P[i] ||
            s.f[i] > horizon) return false;
    }
    s.rebuildIdx(pb.m);
    return alns::audit(pb, s, nullptr, true) == 0;
}

inline double distance(const core::Problem& pb, const core::Sol& a,
                       const core::Sol& b) {
    if (pb.n == 0) return 0.0;
    int changed = 0;
    for (int i = 0; i < pb.n; ++i)
        changed += a.bay[i] != b.bay[i] || a.ori[i] != b.ori[i] ||
                   a.e[i] != b.e[i] || a.x[i] != b.x[i] || a.y[i] != b.y[i];
    return (double)changed / (double)pb.n;
}

inline double populationDiversity(const core::Problem& pb,
                                  const std::vector<core::Sol>& pop) {
    if (pop.size() < 2) return 0.0;
    double sum = 0.0;
    long pairs = 0;
    for (size_t a = 0; a < pop.size(); ++a)
        for (size_t b = 0; b < a; ++b) { sum += distance(pb, pop[a], pop[b]); ++pairs; }
    return sum / (double)pairs;
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
    c.rng.seed((uint32_t)(seed ^ (seed >> 32)));
    c.horizon = horizon;
    c.exactEvents = true;
    c.hasDeadline = true;
    c.deadline = deadline;
    return c;
}

// Recombine assignment genes independently, while preserving a coherent event
// order from both parents.  This deliberately breaks whole-bay cloning.
inline bool crossover(const core::Problem& pb, const core::Sol& a, const core::Sol& b,
                      int horizon, std::mt19937_64& rng,
                      std::chrono::steady_clock::time_point deadline,
                      core::Sol& out, occ::OccIndex& oc, uint64_t salt) {
    out.init(pb.n, pb.m);
    oc.build(pb, horizon);
    std::vector<uint8_t> sourceA(pb.n, 0);
    std::vector<int> rankA(pb.n), rankB(pb.n), order(pb.n);
    for (int j = 0; j < pb.m; ++j) {
        for (size_t k = 0; k < a.byBay[j].size(); ++k) rankA[a.byBay[j][k]] = (int)k;
        for (size_t k = 0; k < b.byBay[j].size(); ++k) rankB[b.byBay[j][k]] = (int)k;
    }
    for (int i = 0; i < pb.n; ++i) {
        // Alternate parental assignment inside bays and flip a small random
        // segment, so a child cannot just reproduce a parent bay partition.
        sourceA[i] = (uint8_t)(((i + (int)(rng() & 3ull)) % 3) != 0);
        if ((rng() & 7ull) == 0) sourceA[i] = !sourceA[i];
        order[i] = i;
    }
    std::sort(order.begin(), order.end(), [&](int x, int y) {
        const int ex = (a.e[x] + b.e[x]) / 2;
        const int ey = (a.e[y] + b.e[y]) / 2;
        if (ex != ey) return ex < ey;
        const int rx = std::min(rankA[x], rankB[x]);
        const int ry = std::min(rankA[y], rankB[y]);
        if (rx != ry) return rx < ry;
        return x < y;
    });
    repair::Ctx c = context(pb, out, oc, horizon, deadline, salt);
    for (int i : order) {
        if (repair::deadlineExpired(c)) return false;
        const core::Sol& src = sourceA[i] ? a : b;
        out.bay[i] = src.bay[i]; out.ori[i] = src.ori[i];
        out.x[i] = src.x[i]; out.y[i] = src.y[i]; out.e[i] = src.e[i];
        out.f[i] = src.f[i]; out.placed[i] = 1; out.addIdx(i);
        if (repair::placementFeasible(c, i)) oc.stamp(pb, out, i, true);
        else { out.delIdx(i); out.placed[i] = 0; }
    }
    std::vector<int> missing;
    for (int i = 0; i < pb.n; ++i) if (!out.placed[i]) missing.push_back(i);
    std::sort(missing.begin(), missing.end(), [&](int x, int y) {
        const int sx = pb.D[x] - pb.R[x] - pb.P[x];
        const int sy = pb.D[y] - pb.R[y] - pb.P[y];
        return sx != sy ? sx < sy : rankA[x] + rankB[x] < rankA[y] + rankB[y];
    });
    for (int i : missing)
        if (repair::deadlineExpired(c) || !repair::insertGreedy(c, i)) return false;
    return completeAndValid(pb, out, horizon);
}

// Rebuild a complete island by exact greedy insertion in a deliberately
// different event order.  It is used only after measured population stagnation.
inline bool alternateConstruction(const core::Problem& pb, const core::Sol& base,
                                  int horizon, std::mt19937_64& rng,
                                  std::chrono::steady_clock::time_point deadline,
                                  core::Sol& out, occ::OccIndex& oc, uint64_t salt) {
    out.init(pb.n, pb.m);
    oc.build(pb, horizon);
    repair::Ctx c = context(pb, out, oc, horizon, deadline, salt);
    std::vector<int> order(pb.n);
    for (int i = 0; i < pb.n; ++i) order[i] = i;
    const uint64_t tieSalt = salt ^ rng();
    std::sort(order.begin(), order.end(), [&](int x, int y) {
        const int lx = pb.D[x] - base.e[x];
        const int ly = pb.D[y] - base.e[y];
        if (lx != ly) return lx < ly;
        if (base.bay[x] != base.bay[y]) return base.bay[x] < base.bay[y];
        return ((uint64_t)x ^ tieSalt) < ((uint64_t)y ^ tieSalt);
    });
    for (int i : order)
        if (repair::deadlineExpired(c) || !repair::insertGreedy(c, i)) return false;
    return completeAndValid(pb, out, horizon);
}

inline bool mutateAndPolish(const core::Problem& pb, core::Sol& s, int horizon,
                            int radius, std::mt19937_64& rng,
                            std::chrono::steady_clock::time_point deadline,
                            uint64_t salt) {
    const core::Sol backup = s;
    occ::OccIndex oc;
    rebuildOcc(pb, s, horizon, oc);
    repair::Ctx c = context(pb, s, oc, horizon, deadline, salt);
    const int n = pb.n;
    const int denom = radius <= 0 ? 35 : (radius == 1 ? 18 : (radius == 2 ? 9 : 5));
    const int q = std::min(std::max(2, n / denom), std::max(2, n - 1));
    std::vector<int> removed = alns::pickRemoval(c, radius == 0 ? 1 : (radius == 1 ? 2 : 5), q);
    if (removed.empty()) return false;
    // Complete the destroyed seed with its event/bay coalition.  Repair is
    // exact-audited before the bounded ALNS pass can retain any move.
    const std::vector<int> seedBlocks = removed;
    for (int x : seedBlocks) for (int i = 0; i < n && (int)removed.size() < 2 * q; ++i)
        if (s.bay[i] == s.bay[x] || std::abs(s.e[i] - s.e[x]) < pb.P[x] + pb.P[i])
            removed.push_back(i);
    std::sort(removed.begin(), removed.end());
    removed.erase(std::unique(removed.begin(), removed.end()), removed.end());
    for (int i : removed) repair::removeBlock(c, i);
    std::shuffle(removed.begin(), removed.end(), c.rng);
    std::sort(removed.begin(), removed.end(), [&](int x, int y) {
        return (pb.D[x] - pb.R[x] - pb.P[x]) < (pb.D[y] - pb.R[y] - pb.P[y]);
    });
    for (int i : removed)
        if (repair::deadlineExpired(c) || !repair::insertGreedy(c, i)) { s = backup; return false; }
    if (!completeAndValid(pb, s, horizon)) { s = backup; return false; }
    const auto now = std::chrono::steady_clock::now();
    if (now >= deadline) return true;
    alns::RunParams rp;
    rp.qloF = 0.02; rp.qhiF = radius < 2 ? 0.07 : 0.12;
    rp.bigQProb = 0.0; rp.hcFrac = 0.7; rp.opMask = 0x3FF;
    rp.maxIters = radius < 2 ? 5 : 9;
    alns::run(c, std::min(std::chrono::duration<double>(deadline - now).count(), 0.030), rp,
              salt ^ rng(), 1);
    if (repair::deadlineExpired(c) || !completeAndValid(pb, s, horizon)) { s = backup; return false; }
    return true;
}

} // namespace detail

template<class EngineT>
Result run(EngineT& engine, double seconds, uint64_t seed) {
    using clock = std::chrono::steady_clock;
    Result result;
    const core::Problem& pb = engine.pb;
    const int horizon = engine.horizon_;
    const core::Sol original = engine.sol;
    core::Sol auditedOriginal = original;
    if (seconds <= 0.0 || pb.n == 0 || horizon <= 0 ||
        !detail::completeAndValid(pb, auditedOriginal, horizon)) return result;

    result.initialObjective = core::evaluate(pb, original).total;
    result.bestObjective = result.initialObjective;
    const auto deadline = clock::now() + std::chrono::duration_cast<clock::duration>(
        std::chrono::duration<double>(seconds));
    std::mt19937_64 rng(seed);
    constexpr size_t kPopulation = 6;
    std::vector<core::Sol> pop{original};
    std::vector<double> score{result.initialObjective};
    core::Sol best = original;
    long stagnant = 0;

    // Select on the two non-commensurate axes directly: lower objective and
    // greater nearest-neighbour distance.  No arbitrary objective/diversity
    // scalar can turn clones into survivors.
    auto paretoInsert = [&](core::Sol&& child, double childScore) {
        std::vector<core::Sol> pool = pop;
        std::vector<double> values = score;
        pool.push_back(std::move(child));
        values.push_back(childScore);
        const size_t childIndex = pool.size() - 1;
        std::vector<double> novelty(pool.size(), 1.0);
        for (size_t i = 0; i < pool.size(); ++i)
            for (size_t j = 0; j < pool.size(); ++j) if (i != j)
                novelty[i] = std::min(novelty[i], detail::distance(pb, pool[i], pool[j]));
        result.offspringNovelty = novelty[childIndex];
        std::vector<uint8_t> chosen(pool.size(), 0);
        std::vector<size_t> keep;
        while (keep.size() < kPopulation && keep.size() < pool.size()) {
            size_t pick = pool.size();
            for (size_t i = 0; i < pool.size(); ++i) if (!chosen[i]) {
                bool dominated = false;
                for (size_t j = 0; j < pool.size(); ++j) if (!chosen[j] && i != j &&
                    values[j] <= values[i] && novelty[j] >= novelty[i] &&
                    (values[j] < values[i] || novelty[j] > novelty[i])) {
                    dominated = true; break;
                }
                if (dominated) continue;
                if (pick == pool.size() || novelty[i] > novelty[pick] ||
                    (novelty[i] == novelty[pick] && values[i] < values[pick])) pick = i;
            }
            if (pick == pool.size()) break;
            chosen[pick] = 1; keep.push_back(pick);
        }
        bool keptChild = false;
        pop.clear(); score.clear();
        for (size_t i : keep) {
            keptChild = keptChild || i == childIndex;
            pop.push_back(std::move(pool[i]));
            score.push_back(values[i]);
        }
        if (keptChild) ++result.paretoSurvivors;
    };

    while (clock::now() < deadline) {
        const size_t ia = (size_t)(rng() % pop.size());
        size_t ib = ia;
        double farthest = -1.0;
        for (size_t i = 0; i < pop.size(); ++i) if (i != ia) {
            const double d = detail::distance(pb, pop[ia], pop[i]);
            if (d > farthest) { farthest = d; ib = i; }
        }
        core::Sol child;
        occ::OccIndex childOcc;
        if (!detail::crossover(pb, pop[ia], pop[ib], horizon, rng, deadline, child,
                               childOcc, seed ^ (uint64_t)result.iterations)) break;
        ++result.iterations;
        const int radius = stagnant < 4 ? 0 : (stagnant < 10 ? 1 : (stagnant < 20 ? 2 : 3));
        if (!detail::mutateAndPolish(pb, child, horizon, radius, rng, deadline,
                                     seed + (uint64_t)result.iterations)) break;
        ++result.feasibleOffspring;
        const double childScore = core::evaluate(pb, child).total;
        if (childScore < result.bestObjective) {
            best = child; result.bestObjective = childScore; stagnant = 0;
        } else {
            ++stagnant;
        }
        paretoInsert(std::move(child), childScore);
        result.peakDiversity = std::max(result.peakDiversity, detail::populationDiversity(pb, pop));

        // A stagnant, collapsed island is rebuilt from the elite using a
        // different event order; the replacement is audited before admission.
        if (stagnant >= 12 && pop.size() == kPopulation && clock::now() < deadline &&
            detail::populationDiversity(pb, pop) < 0.10) {
            size_t elite = 0;
            for (size_t i = 1; i < score.size(); ++i) if (score[i] < score[elite]) elite = i;
            core::Sol island;
            occ::OccIndex islandOcc;
            if (detail::alternateConstruction(pb, pop[elite], horizon, rng, deadline, island,
                                              islandOcc, seed ^ rng()) &&
                detail::completeAndValid(pb, island, horizon)) {
                const double islandScore = core::evaluate(pb, island).total;
                paretoInsert(std::move(island), islandScore);
                ++result.restarts;
            }
            stagnant = 0;
        }
    }
    result.deadlineHit = clock::now() >= deadline;
    result.diversity = detail::populationDiversity(pb, pop);
    result.improved = result.bestObjective < result.initialObjective;
    if (result.improved && detail::completeAndValid(pb, best, horizon)) {
        engine.sol = best;
        detail::rebuildOcc(pb, engine.sol, horizon, engine.oc);
    }
    return result;
}

} // namespace kernel_memetic

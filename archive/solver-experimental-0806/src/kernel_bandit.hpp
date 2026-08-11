// kernel_bandit.hpp -- contextual portfolio control over complete ALNS policies.
#pragma once

#include "alns.hpp"
#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <limits>
#include <random>
#include <vector>

namespace kernel_bandit {

struct Policy {
    int construction = 0;       // 0 incumbent, 1 noisy EDD reconstruction
    double qlo = 0.03, qhi = 0.10;
    int repairOrder = 0;        // encoded by destroy family and construction seed
    int assignmentPressure = 0; // 0 none, 1 preference, 2 dense-screen split
    bool exactEvents = false;
    double populationShare = 0.0;
    int localPasses = 0;
    uint32_t opMask = 0x3ff;
};

struct ArmReport {
    Policy policy;
    long pulls = 0;
    long feasible = 0;
    double reward = 0.0;
    double seconds = 0.0;
};

struct Result {
    long armPulls = 0;
    long modelUpdates = 0;
    long feasibleRuns = 0;
    double initialObjective = std::numeric_limits<double>::infinity();
    double bestObjective = std::numeric_limits<double>::infinity();
    Policy chosenPolicy;
    std::vector<ArmReport> arms;
    bool improved = false;
};

namespace detail {
constexpr int D = 8;
constexpr int POP = 6;

inline double objective(const core::Problem& pb, const core::Sol& s) {
    return core::evaluatePen(pb, s).total;
}

inline bool valid(const core::Problem& pb, core::Sol& s, bool exactEvents) {
    return std::isfinite(objective(pb, s)) && alns::audit(pb, s, nullptr, exactEvents) == 0;
}

template<class EngineT>
inline void rebuild(EngineT& engine) {
    engine.sol.rebuildIdx(engine.pb.m);
    engine.oc.clearBits();
    for (int i = 0; i < engine.pb.n; ++i)
        if (engine.sol.placed[i]) engine.oc.stamp(engine.pb, engine.sol, i, true);
    engine.ctx.pb = &engine.pb;
    engine.ctx.s = &engine.sol;
    engine.ctx.oc = &engine.oc;
}

inline std::array<double, D> features(const core::Problem& pb, const core::Sol& s,
                                      double current) {
    std::array<double, D> x{};
    double tardy = 0.0, prefLoss = 0.0, placed = 0.0, duration = 0.0;
    double horizon = 1.0;
    for (int i = 0; i < pb.n; ++i) {
        horizon = std::max(horizon, (double)std::max(pb.D[i], pb.R[i] + pb.P[i]));
        duration += pb.P[i];
        if (!s.placed[i]) continue;
        placed += 1.0;
        tardy += std::max(0, s.f[i] - pb.D[i]);
        prefLoss += pb.Smax[i] - pb.pref[i][s.bay[i]];
    }
    const double n = std::max(1, pb.n);
    x[0] = 1.0;
    x[1] = std::min(1.0, placed / n);
    x[2] = std::min(1.0, tardy / (n * horizon));
    x[3] = std::min(1.0, std::max(0.0, prefLoss) / n);
    x[4] = std::min(1.0, duration / (n * horizon));
    x[5] = std::min(1.0, std::max(0.0, pb.fillRatio));
    x[6] = std::min(1.0, (double)pb.m / n);
    x[7] = std::min(1.0, std::log1p(std::max(0.0, current)) / 20.0);
    return x;
}

struct LinearUcb {
    std::array<std::array<double, D>, D> a{};
    std::array<double, D> b{};
    LinearUcb() { for (int i = 0; i < D; ++i) a[i][i] = 1.0; }

    std::array<double, D> solve(const std::array<double, D>& rhs) const {
        std::array<std::array<double, D + 1>, D> w{};
        for (int i = 0; i < D; ++i) {
            for (int j = 0; j < D; ++j) w[i][j] = a[i][j];
            w[i][D] = rhs[i];
        }
        for (int col = 0; col < D; ++col) {
            int pivot = col;
            for (int r = col + 1; r < D; ++r)
                if (std::fabs(w[r][col]) > std::fabs(w[pivot][col])) pivot = r;
            if (std::fabs(w[pivot][col]) < 1e-12) continue;
            std::swap(w[pivot], w[col]);
            const double inv = 1.0 / w[col][col];
            for (int j = col; j <= D; ++j) w[col][j] *= inv;
            for (int r = 0; r < D; ++r) if (r != col) {
                const double q = w[r][col];
                for (int j = col; j <= D; ++j) w[r][j] -= q * w[col][j];
            }
        }
        std::array<double, D> out{};
        for (int i = 0; i < D; ++i) out[i] = w[i][D];
        return out;
    }

    double score(const std::array<double, D>& x, double bonus) const {
        const auto theta = solve(b);
        const auto invx = solve(x);
        double mean = 0.0, variance = 0.0;
        for (int i = 0; i < D; ++i) { mean += theta[i] * x[i]; variance += x[i] * invx[i]; }
        return mean + bonus * std::sqrt(std::max(0.0, variance));
    }

    void update(const std::array<double, D>& x, double reward) {
        for (int i = 0; i < D; ++i) {
            b[i] += reward * x[i];
            for (int j = 0; j < D; ++j) a[i][j] += x[i] * x[j];
        }
    }
};

inline std::vector<Policy> policies() {
    return {
        {0,.02,.07,0,0,false,.00,0,0x01f}, {0,.03,.12,1,1,false,.35,1,0x0df},
        {1,.04,.16,2,2,true, .50,2,0x3ff}, {0,.06,.22,2,1,true, .70,2,0x3f0},
        {1,.02,.10,1,2,false,.25,3,0x1ff}, {0,.08,.28,0,0,true, .80,1,0x3ff},
        {1,.05,.18,2,1,false,.60,3,0x2ff}, {0,.03,.14,1,2,true, .45,4,0x3ff},
        // Basin-escape arms for repeatedly intensified incumbents.  These keep
        // exact replay and transactional acceptance but destroy enough of a
        // geometry/time critical region to cross the small-q local optimum.
        {0,.12,.32,2,0,true, .65,3,0x3ff}, {1,.18,.40,1,2,true, .75,4,0x3ff},
        {0,.25,.50,2,1,true, .85,5,0x3ff}, {1,.32,.58,0,0,true, .90,6,0x3ff}
    };
}

inline int distance(const core::Sol& a, const core::Sol& b) {
    int d = 0;
    for (size_t i = 0; i < a.placed.size(); ++i)
        d += a.placed[i] != b.placed[i] || a.bay[i] != b.bay[i] || a.e[i] != b.e[i];
    return d;
}
} // namespace detail

template<class EngineT>
Result run(EngineT& engine, double seconds, uint64_t seed) {
    using Clock = std::chrono::steady_clock;
    Result out;
    const auto started = Clock::now();
    const auto deadline = started + std::chrono::duration_cast<Clock::duration>(
        std::chrono::duration<double>(std::max(0.0, seconds)));
    auto remaining = [&] { return std::chrono::duration<double>(deadline - Clock::now()).count(); };
    if (seconds <= 0.0 || engine.pb.n <= 0) return out;

    detail::rebuild(engine);
    const bool originalExact = engine.ctx.exactEvents;
    const int originalDens = engine.ctx.densMode;
    const double originalPref = engine.ctx.prefP;
    const bool originalHasDeadline = engine.ctx.hasDeadline;
    const auto originalDeadline = engine.ctx.deadline;
    const core::Sol entry = engine.sol;
    if (!detail::valid(engine.pb, engine.sol, true)) { engine.sol = entry; detail::rebuild(engine); return out; }
    out.initialObjective = detail::objective(engine.pb, engine.sol);
    out.bestObjective = out.initialObjective;
    core::Sol best = engine.sol;
    std::vector<core::Sol> population{best};
    std::vector<double> popObj{out.bestObjective};
    const auto ps = detail::policies();
    out.arms.resize(ps.size());
    for (size_t i = 0; i < ps.size(); ++i) out.arms[i].policy = ps[i];
    out.chosenPolicy = ps[0];
    std::vector<detail::LinearUcb> models(ps.size());
    std::mt19937_64 rng(seed);

    while (remaining() > 0.002) {
        const auto x = detail::features(engine.pb, best, out.bestObjective);
        size_t arm = 0;
        long least = out.arms[0].pulls;
        for (size_t i = 1; i < ps.size(); ++i) least = std::min(least, out.arms[i].pulls);
        if (least == 0) {
            for (size_t i = 0; i < ps.size(); ++i) if (!out.arms[i].pulls) { arm = i; break; }
        } else {
            double top = -std::numeric_limits<double>::infinity();
            const double bonus = 0.20 * std::sqrt(std::log(1.0 + out.armPulls));
            for (size_t i = 0; i < ps.size(); ++i) {
                const double v = models[i].score(x, bonus);
                if (v > top) { top = v; arm = i; }
            }
        }
        const Policy& p = ps[arm];
        if (population.size() > 1 && std::uniform_real_distribution<double>(0, 1)(rng) < p.populationShare) {
            const size_t pick = (size_t)(rng() % population.size());
            engine.sol = population[pick];
        } else engine.sol = best;
        detail::rebuild(engine);
        engine.ctx.exactEvents = p.exactEvents;
        engine.ctx.densMode = p.assignmentPressure == 2 ? 1 : (p.assignmentPressure == 0 ? 0 : originalDens);
        engine.ctx.prefP = p.assignmentPressure == 1 ? 0.92 : originalPref;
        engine.ctx.hasDeadline = true;
        engine.ctx.deadline = deadline;
        if (p.construction == 1 && remaining() > 0.004) {
            const core::Sol beforeConstruct = engine.sol;
            if (!alns::constructInitial(engine.ctx, rng())) engine.sol = beforeConstruct;
            detail::rebuild(engine);
        }
        const double before = detail::objective(engine.pb, engine.sol);
        const double slice = std::min(remaining(), std::max(0.003, seconds / 24.0));
        alns::RunParams rp;
        rp.qloF = p.qlo; rp.qhiF = p.qhi; rp.opMask = p.opMask;
        rp.bigQProb = p.repairOrder == 2 ? 0.12 : (p.repairOrder == 1 ? 0.06 : 0.02);
        const auto pullStart = Clock::now();
        alns::run(engine.ctx, slice, rp, rng(), 0);
        if (p.localPasses && remaining() > 0.001) {
            engine.ctx.hasDeadline = true; engine.ctx.deadline = deadline;
            alns::spatialCompact(engine.ctx, p.localPasses);
            alns::compactLeft(engine.ctx, std::max(1, p.localPasses / 2));
        }
        const double elapsed = std::max(1e-6, std::chrono::duration<double>(Clock::now() - pullStart).count());
        detail::rebuild(engine);
        const bool feasible = detail::valid(engine.pb, engine.sol, p.exactEvents) &&
                              detail::valid(engine.pb, engine.sol, true);
        const double candidate = feasible ? detail::objective(engine.pb, engine.sol) : std::numeric_limits<double>::infinity();
        const double gain = feasible ? std::max(0.0, before - candidate) : 0.0;
        const double reward = (feasible ? gain / elapsed : -1.0 / elapsed) / std::max(1.0, std::fabs(before));
        models[arm].update(x, reward);
        ++out.modelUpdates; ++out.armPulls; ++out.arms[arm].pulls;
        out.arms[arm].reward += reward; out.arms[arm].seconds += elapsed;
        if (feasible) { ++out.feasibleRuns; ++out.arms[arm].feasible; }
        if (feasible && candidate < out.bestObjective - 1e-9) { best = engine.sol; out.bestObjective = candidate; out.chosenPolicy = p; }
        if (feasible) {
            bool distinct = true;
            for (const auto& member : population) if (detail::distance(member, engine.sol) < std::max(2, engine.pb.n / 25)) { distinct = false; break; }
            if (distinct && population.size() < detail::POP) { population.push_back(engine.sol); popObj.push_back(candidate); }
            else if (distinct) {
                const size_t worst = (size_t)(std::max_element(popObj.begin(), popObj.end()) - popObj.begin());
                if (candidate < popObj[worst]) { population[worst] = engine.sol; popObj[worst] = candidate; }
            }
        }
        engine.sol = best;
        detail::rebuild(engine);
    }
    engine.ctx.exactEvents = originalExact;
    engine.ctx.densMode = originalDens;
    engine.ctx.prefP = originalPref;
    engine.ctx.hasDeadline = originalHasDeadline;
    engine.ctx.deadline = originalDeadline;
    engine.sol = best;
    out.improved = out.bestObjective < out.initialObjective;
    detail::rebuild(engine);
    return out;
}

} // namespace kernel_bandit

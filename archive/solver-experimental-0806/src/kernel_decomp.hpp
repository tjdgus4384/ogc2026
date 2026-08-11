// kernel_decomp.hpp -- bounded logic/Lagrangian decomposition over bay-time plans.
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

namespace kernel_decomp {

struct Result {
    long rounds = 0;
    long masterCandidates = 0;
    long feasibleSubproblems = 0;
    long subproblemFailures = 0;
    long cuts = 0;
    long dualUpdates = 0;
    double initialObjective = std::numeric_limits<double>::infinity();
    double bestObjective = std::numeric_limits<double>::infinity();
    bool improved = false;
};

struct NoGoodCut {
    int first = -1, second = -1, bay = -1, begin = 0, end = 0;
};
struct CapacityCut {
    int bay = -1, begin = 0, end = 0;
    double limit = 0.0;
};
struct MasterChoice { int block = -1, bay = -1, entry = 0; };

inline double footprint(const core::Problem& pb, int i) {
    double a = std::numeric_limits<double>::infinity();
    for (const auto& g : pb.geo[i].orients) {
        const double box = (g.bb.x1 - g.bb.x0) * (g.bb.y1 - g.bb.y0);
        a = std::min(a, g.parea > 0.0 ? g.parea : box);
    }
    return std::isfinite(a) ? a : 0.0;
}

// The master deliberately works on several independent coalitions.  Its capacity
// model is only a surrogate; geometry and event-order predicates below remain the
// authority and feed failed assignments back as explicit cuts.
template<class EngineT>
Result run(EngineT& eng, double seconds, uint64_t seed) {
    using Clock = std::chrono::steady_clock;
    Result out;
    const auto start = Clock::now();
    const auto deadline = start + std::chrono::duration_cast<Clock::duration>(
        std::chrono::duration<double>(std::max(0.0, seconds)));
    const core::Problem& pb = eng.pb;
    if (pb.n == 0 || pb.m == 0 || seconds <= 0.0) {
        out.initialObjective = out.bestObjective = core::evaluate(pb, eng.sol).total;
        return out;
    }

    int horizon = eng.horizon_;
    for (int i = 0; i < pb.n; ++i) horizon = std::max(horizon, pb.D[i] + pb.P[i] + 1);
    horizon = std::max(1, horizon);
    const repair::Ctx savedCtx = eng.ctx;
    eng.buildOcc(horizon);
    eng.ctx.hasDeadline = true;
    eng.ctx.deadline = deadline;
    eng.ctx.exactEvents = true; // every accepted board is checker-order audited.
    eng.ctx.rng.seed(static_cast<uint32_t>(seed));

    core::Sol auditedInitial = eng.sol;
    const bool initiallyComplete = std::all_of(auditedInitial.placed.begin(), auditedInitial.placed.end(),
                                               [](uint8_t v) { return v != 0; }) &&
                                   alns::audit(pb, auditedInitial, nullptr, true) == 0;
    out.initialObjective = core::evaluate(pb, eng.sol).total;
    out.bestObjective = out.initialObjective;
    if (!initiallyComplete) {
        eng.ctx = savedCtx;
        eng.ctx.pb = &eng.pb; eng.ctx.s = &eng.sol; eng.ctx.oc = &eng.oc;
        eng.ctx.horizon = horizon;
        return out;
    }
    core::Sol best = eng.sol;
    std::vector<core::Sol> auditedIncumbents(1, eng.sol);
    std::mt19937_64 rng(seed ^ 0x9e3779b97f4a7c15ULL);
    std::vector<double> price(static_cast<size_t>(pb.m) * horizon, 0.0);
    std::vector<double> tardyPrice(pb.n, 0.0);
    std::vector<double> preferencePrice(static_cast<size_t>(pb.n) * pb.m, 0.0);
    std::vector<NoGoodCut> noGoods;
    std::vector<CapacityCut> capacities;
    std::vector<double> area(pb.n);
    for (int i = 0; i < pb.n; ++i) area[i] = footprint(pb, i);

    auto pAt = [&](int bay, int t) -> double& { return price[static_cast<size_t>(bay) * horizon + t]; };
    auto prefAt = [&](int block, int bay) -> double& {
        return preferencePrice[static_cast<size_t>(block) * pb.m + bay];
    };
    auto conflictsCut = [&](int a, int b, int bay, int e, int f) {
        for (const auto& c : noGoods)
            if (c.bay == bay && c.begin < f && e < c.end &&
                ((c.first == a && c.second == b) || (c.first == b && c.second == a))) return true;
        return false;
    };

    // Feasibility starts with a single audited block and grows a coalition only
    // after an exact reconstruction.  Thus every expansion has a realizable
    // event-order witness instead of relying on the area surrogate.
    int coalitionLimit = 1;
    int trustTime = 0;
    while (Clock::now() < deadline) {
        ++out.rounds;
        const bool feasibilityPhase = (out.rounds & 1L) != 0;
        const int parts = 2 + static_cast<int>(rng() % 3);
        for (int part = 0; part < parts && Clock::now() < deadline; ++part) {
            std::vector<int> coalition;
            const int residue = static_cast<int>((out.rounds + part) % parts);
            for (int i = 0; i < pb.n; ++i)
                if (static_cast<int>((static_cast<uint64_t>(i) + out.rounds) % parts) == residue)
                    coalition.push_back(i);
            if (coalition.empty()) continue;
            std::shuffle(coalition.begin(), coalition.end(), rng);
            coalition.resize(std::min<int>(coalition.size(), coalitionLimit));
            const core::Sol before = eng.sol;
            // The incumbent order is an exact-event witness.  Replaying it is
            // deliberately preferred to EDD during feasibility restoration.
            std::sort(coalition.begin(), coalition.end(), [&](int a, int b) {
                return before.e[a] != before.e[b] ? before.e[a] < before.e[b] : a < b;
            });

            std::vector<MasterChoice> plan;
            std::vector<double> masterLoad(static_cast<size_t>(pb.m) * horizon, 0.0);
            bool masterOK = true;
            for (int a : coalition) {
                if (Clock::now() >= deadline) { masterOK = false; break; }
                MasterChoice chosen;
                double bestCost = std::numeric_limits<double>::infinity();
                const int baseBay = before.bay[a], baseEntry = before.e[a];
                const int latest = std::min(horizon - pb.P[a] - 1, pb.D[a] + pb.P[a]);
                const int lo = std::max(pb.R[a], baseEntry - trustTime);
                const int hi = std::min(latest, baseEntry + trustTime);
                for (int j = 0; j < pb.m && Clock::now() < deadline; ++j) {
                    // Bay changes are admitted only after the zero-radius
                    // incumbent trust region has reconstructed successfully.
                    if (j != baseBay && coalitionLimit == 1) continue;
                    for (int e = lo; e <= hi && Clock::now() < deadline; ++e) {
                        const int f = e + pb.P[a];
                        double elastic = 0.0;
                        for (const auto& c : capacities)
                            if (c.bay == j && c.begin < f && e < c.end)
                                elastic += std::max(0.0, area[a] - c.limit);
                        for (const auto& q : plan)
                            if (q.bay == j && q.entry < f && e < q.entry + pb.P[q.block] &&
                                conflictsCut(a, q.block, j, e, f)) elastic += 1.0;
                        double loadCost = 0.0;
                        for (int t = e; t < f; ++t)
                            loadCost += pAt(j, t) + masterLoad[static_cast<size_t>(j) * horizon + t];
                        const double tardy = std::max(0, f - pb.D[a]);
                        const double trust = (j == baseBay ? 0.0 : 4.0) +
                                             0.25 * std::abs(e - baseEntry);
                        const double cost = pb.w1 * tardy + tardyPrice[a] * tardy +
                            pb.w3 * (pb.Smax[a] - pb.pref[a][j]) + prefAt(a, j) +
                            loadCost + trust + 10.0 * elastic;
                        if (cost < bestCost) { bestCost = cost; chosen = {a, j, e}; }
                    }
                }
                if (chosen.bay < 0) { masterOK = false; break; }
                for (int t = chosen.entry; t < chosen.entry + pb.P[a]; ++t)
                    masterLoad[static_cast<size_t>(chosen.bay) * horizon + t] += area[a];
                plan.push_back(chosen);
            }
            ++out.masterCandidates;
            if (!masterOK) { ++out.subproblemFailures; continue; }

            for (int a : coalition) repair::removeBlock(eng.ctx, a);
            std::vector<int> forced(pb.n, -1), caps(pb.n, -1);
            for (const auto& q : plan) {
                forced[q.block] = q.bay;
                // This cap preserves the master event trust region.  The old
                // P4/P5 code accidentally granted an extra processing window.
                caps[q.block] = q.entry;
            }
            const int* oldForced = eng.ctx.forcedBay;
            const int* oldCaps = eng.ctx.entryCap;
            eng.ctx.forcedBay = forced.data(); eng.ctx.entryCap = caps.data();
            bool reconstructed = true;
            int failed = -1;
            std::vector<int> exactOris, xs, ys;
            for (const auto& q : plan) {
                exactOris.resize(pb.geo[q.block].orients.size());
                for (size_t o = 0; o < exactOris.size(); ++o) exactOris[o] = static_cast<int>(o);
                const bool replay = q.bay == before.bay[q.block] && q.entry == before.e[q.block];
                const bool inserted = replay
                    ? repair::tryPlacement(eng.ctx, q.block, q.bay, before.ori[q.block],
                                           before.x[q.block], before.y[q.block], q.entry)
                    : repair::scanBayAt(eng.ctx, q.block, q.bay, q.entry, exactOris, xs, ys);
                if (Clock::now() >= deadline || !inserted) {
                    reconstructed = false; failed = q.block; break;
                }
            }
            eng.ctx.forcedBay = oldForced; eng.ctx.entryCap = oldCaps;

            int bad = -1;
            const bool complete = std::all_of(eng.sol.placed.begin(), eng.sol.placed.end(),
                                              [](uint8_t v) { return v != 0; });
            const bool audited = reconstructed && complete && Clock::now() < deadline &&
                                 alns::audit(pb, eng.sol, &bad, true) == 0;
            if (!audited) {
                if (Clock::now() >= deadline) {
                    eng.sol = before; eng.buildOcc(horizon);
                    eng.ctx.hasDeadline = true; eng.ctx.deadline = deadline; eng.ctx.exactEvents = true;
                    break;
                }
                ++out.subproblemFailures;
                coalitionLimit = std::max(1, coalitionLimit - 1);
                trustTime = 0;
                const int culprit = failed >= 0 ? failed : (bad >= 0 ? bad : coalition.front());
                const int cutBay = forced[culprit] >= 0 ? forced[culprit] : before.bay[culprit];
                const int ce = std::max(pb.R[culprit], caps[culprit]);
                const int cf = ce + pb.P[culprit];
                int blocker = -1;
                if (cutBay >= 0 && cutBay < pb.m) {
                    // One actual resident is a minimal, realizable conflict
                    // witness; adding every overlapping incumbent was the
                    // source of the unusable v1 master cuts.
                    for (int b : eng.sol.byBay[cutBay])
                        if (b != culprit && eng.sol.placed[b] && eng.sol.e[b] < cf && ce < eng.sol.f[b]) {
                            blocker = b; break;
                        }
                    if (blocker >= 0 && !conflictsCut(culprit, blocker, cutBay, ce, cf)) {
                        noGoods.push_back({culprit, blocker, cutBay, ce, cf});
                        ++out.cuts;
                    }
                    const double bayArea = pb.bayW[cutBay] * pb.bayH[cutBay];
                    double remaining = bayArea;
                    for (int t = std::max(0, ce); t < std::min(horizon, cf); ++t) {
                        double occupied = 0.0;
                        for (int b : eng.sol.byBay[cutBay])
                            if (eng.sol.placed[b] && eng.sol.e[b] <= t && t < eng.sol.f[b]) occupied += area[b];
                        remaining = std::min(remaining, std::max(0.0, bayArea - occupied));
                    }
                    capacities.push_back({cutBay, ce, cf, remaining});
                    ++out.cuts;
                    for (int t = std::max(0, ce); t < std::min(horizon, cf); ++t) pAt(cutBay, t) += 0.25;
                    prefAt(culprit, cutBay) += 0.1 * std::max(1.0, pb.Smax[culprit] - pb.pref[culprit][cutBay]);
                }
                tardyPrice[culprit] += 0.1;
                ++out.dualUpdates;
                eng.sol = before; eng.buildOcc(horizon);
                eng.ctx.hasDeadline = true; eng.ctx.deadline = deadline; eng.ctx.exactEvents = true;
                continue;
            }

            ++out.feasibleSubproblems;
            const double value = core::evaluate(pb, eng.sol).total;
            if (auditedIncumbents.size() == 4) auditedIncumbents.erase(auditedIncumbents.begin());
            auditedIncumbents.push_back(eng.sol);
            if (value + 1e-9 < out.bestObjective) {
                out.bestObjective = value;
                best = eng.sol;
            }
            // Feasibility rounds retain a complete audited state as the next
            // trust centre; objective rounds roll back unless non-worsening.
            const bool keep = feasibilityPhase || value + 1e-9 < core::evaluate(pb, before).total;
            for (int j = 0; j < pb.m && Clock::now() < deadline; ++j)
                for (int t = 0; t < horizon && Clock::now() < deadline; ++t) {
                    double used = 0.0;
                    for (int i : eng.sol.byBay[j])
                        if (eng.sol.placed[i] && eng.sol.e[i] <= t && t < eng.sol.f[i]) used += area[i];
                    const double target = 0.82 * pb.bayW[j] * pb.bayH[j];
                    pAt(j, t) = std::max(0.0, pAt(j, t) + 0.02 * (used - target));
                }
            ++out.dualUpdates;
            coalitionLimit = std::min(std::max(2, std::min(16, pb.n / 3 + 1)), coalitionLimit + 1);
            trustTime = std::min(3, trustTime + 1);
            if (!keep) {
                eng.sol = before; eng.buildOcc(horizon);
                eng.ctx.hasDeadline = true; eng.ctx.deadline = deadline; eng.ctx.exactEvents = true;
            }
        }
    }
    out.improved = out.bestObjective < out.initialObjective;
    eng.sol = best;
    eng.buildOcc(horizon);
    eng.ctx = savedCtx;
    eng.ctx.pb = &eng.pb; eng.ctx.s = &eng.sol; eng.ctx.oc = &eng.oc;
    eng.ctx.horizon = horizon;
    return out;
}

} // namespace kernel_decomp

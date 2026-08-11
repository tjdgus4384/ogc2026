// kernel_pattern_master.hpp -- exact set-partitioning master over audited bay plans.
#pragma once

#include "alns.hpp"
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <functional>
#include <limits>
#include <numeric>
#include <random>
#include <string>
#include <utility>
#include <vector>

namespace kernel_pattern_master {

struct Result {
    long populationBoards = 0;
    long generatedColumns = 0;
    long pricedColumns = 0;
    long dominatedColumns = 0;
    long retainedColumns = 0;
    uint64_t masterNodes = 0;
    uint64_t masterPruned = 0;
    long ownershipConflicts = 0;
    long completeCandidates = 0;
    long exactAudits = 0;
    double initialObjective = std::numeric_limits<double>::infinity();
    double bestObjective = std::numeric_limits<double>::infinity();
    double acceptedGain = 0.0;
    bool improved = false;
    bool deadlineHit = false;
    bool nodeLimitHit = false;
    bool columnLimitHit = false;
};

namespace detail {

struct Pattern {
    int bay = -1;
    int source = -1;
    std::vector<int> blocks;
    // Contributions which are additive across patterns.  z2 is deliberately
    // evaluated from the reconstructed whole board at every master leaf.
    double z1 = 0.0, z3 = 0.0, load = 0.0, localCost = 0.0, lowerCost = 0.0;
    double reducedCost = 0.0;
};

inline bool completeAndAudited(const core::Problem& pb, core::Sol& s, int horizon) {
    if ((int)s.placed.size() != pb.n) return false;
    for (int i = 0; i < pb.n; ++i) {
        if (!s.placed[i] || s.bay[i] < 0 || s.bay[i] >= pb.m ||
            s.ori[i] < 0 || s.ori[i] >= (int)pb.geo[i].orients.size() ||
            s.e[i] < pb.R[i] || s.f[i] != s.e[i] + pb.P[i] || s.f[i] > horizon) return false;
    }
    s.rebuildIdx(pb.m);
    return alns::audit(pb, s, nullptr, true) == 0;
}

inline void buildOcc(const core::Problem& pb, core::Sol& s, int horizon, occ::OccIndex& oc) {
    s.rebuildIdx(pb.m);
    oc.build(pb, horizon);
    for (int i = 0; i < pb.n; ++i) if (s.placed[i]) oc.stamp(pb, s, i, true);
}

inline repair::Ctx context(const core::Problem& pb, core::Sol& s, occ::OccIndex& oc,
                           int horizon, std::chrono::steady_clock::time_point deadline,
                           uint64_t seed) {
    repair::Ctx c{};
    c.pb = &pb; c.s = &s; c.oc = &oc; c.horizon = horizon;
    c.rng.seed(static_cast<uint32_t>(seed ^ (seed >> 32)));
    c.exactEvents = true; c.hasDeadline = true; c.deadline = deadline;
    return c;
}

inline bool sameOwnership(const Pattern& a, const Pattern& b) {
    return a.blocks == b.blocks;
}

inline Pattern extract(const core::Problem& pb, const core::Sol& s, int bay, int source) {
    Pattern p; p.bay = bay; p.source = source;
    for (int i : s.byBay[bay]) if (s.placed[i]) {
        p.blocks.push_back(i);
        p.z1 += std::max(0, s.f[i] - pb.D[i]);
        p.z3 += pb.Smax[i] - pb.pref[i][bay];
        p.load += pb.workload[i];
    }
    std::sort(p.blocks.begin(), p.blocks.end());
    for (int i : p.blocks)
        p.lowerCost += pb.w1 * std::max(0, pb.R[i] + pb.P[i] - pb.D[i]) +
                       pb.w3 * (pb.Smax[i] - pb.pref[i][bay]);
    p.localCost = pb.w1 * p.z1 + pb.w3 * p.z3;
    return p;
}

// Exact-geometry mutation: remove a deliberately stressed coalition and replay it
// with one of the production inserters.  Different destroy sizes and replay
// policies produce source boards whose bay ownership is meaningfully different.
enum class ReplayPolicy { Greedy, Scored, Contact };

inline bool mutate(const core::Problem& pb, const core::Sol& parent, int horizon,
                   const std::vector<double>& ownershipPrice, std::mt19937_64& rng,
                   std::chrono::steady_clock::time_point deadline, uint64_t salt,
                   ReplayPolicy policy, int destroySize, core::Sol& child) {
    child = parent;
    occ::OccIndex oc;
    buildOcc(pb, child, horizon, oc);
    repair::Ctx c = context(pb, child, oc, horizon, deadline, salt);
    std::vector<int> order(pb.n);
    std::iota(order.begin(), order.end(), 0);
    std::shuffle(order.begin(), order.end(), rng);
    std::sort(order.begin(), order.end(), [&](int a, int b) {
        const double pa = ownershipPrice.empty() ? 0.0 : ownershipPrice[a];
        const double pbv = ownershipPrice.empty() ? 0.0 : ownershipPrice[b];
        const double va = pa + 0.05 * std::max(0, child.f[a] - pb.D[a]);
        const double vb = pbv + 0.05 * std::max(0, child.f[b] - pb.D[b]);
        return va > vb;
    });
    const int q = std::min(pb.n, std::max(1, destroySize));
    order.resize(q);
    for (int i : order) repair::removeBlock(c, i);
    std::sort(order.begin(), order.end(), [&](int a, int b) {
        const int sa = pb.D[a] - pb.R[a] - pb.P[a];
        const int sb = pb.D[b] - pb.R[b] - pb.P[b];
        return sa != sb ? sa < sb : a < b;
    });
    for (int i : order) {
        bool inserted = false;
        if (policy == ReplayPolicy::Greedy) {
            inserted = repair::insertGreedy(c, i);
        } else if (policy == ReplayPolicy::Scored) {
            repair_scored::Config cfg;
            cfg.topK = 10; cfg.maxCandidates = 192; cfg.maxTimes = 18; cfg.contactAxisCap = 32;
            inserted = repair_scored::insertBest(c, i, cfg, nullptr);
        } else {
            nfp_contact::Config cfg;
            cfg.maxEntryTimes = 18; cfg.maxCandidates = 192; cfg.maxContactsPerResident = 32;
            cfg.includeGeneral = true;
            inserted = nfp_contact::insert(c, i, cfg, nullptr);
        }
        if (repair::deadlineExpired(c) || !inserted) return false;
    }
    return !repair::deadlineExpired(c) && completeAndAudited(pb, child, horizon);
}
inline bool replayPartition(const core::Problem& pb, const std::vector<int>& targetBay,
                            int horizon, std::chrono::steady_clock::time_point deadline,
                            uint64_t seed, ReplayPolicy policy, core::Sol& candidate) {
    candidate.init(pb.n, pb.m);
    occ::OccIndex oc;
    buildOcc(pb, candidate, horizon, oc);
    repair::Ctx c = context(pb, candidate, oc, horizon, deadline, seed);
    std::vector<int> order(pb.n);
    std::iota(order.begin(), order.end(), 0);
    std::sort(order.begin(), order.end(), [&](int a, int b) {
        const int sa = pb.D[a] - pb.R[a] - pb.P[a];
        const int sb = pb.D[b] - pb.R[b] - pb.P[b];
        return sa != sb ? sa < sb : (pb.P[a] != pb.P[b] ? pb.P[a] > pb.P[b] : a < b);
    });
    c.forcedBay = targetBay.data();
    for (int i : order) {
        bool inserted = false;
        if (policy == ReplayPolicy::Greedy) {
            inserted = repair::insertGreedy(c, i);
        } else if (policy == ReplayPolicy::Scored) {
            repair_scored::Config cfg;
            cfg.topK = 12; cfg.maxCandidates = 256; cfg.maxTimes = 24; cfg.contactAxisCap = 40;
            inserted = repair_scored::insertBest(c, i, cfg, nullptr);
        } else {
            nfp_contact::Config cfg;
            cfg.maxEntryTimes = 24; cfg.maxCandidates = 256; cfg.maxContactsPerResident = 40;
            cfg.includeGeneral = true;
            inserted = nfp_contact::insert(c, i, cfg, nullptr);
        }
        if (!inserted || repair::deadlineExpired(c)) return false;
    }
    return completeAndAudited(pb, candidate, horizon);
}
inline bool alnsDiversify(const core::Problem& pb, const core::Sol& parent, int horizon,
                          std::chrono::steady_clock::time_point deadline, uint64_t seed,
                          int repairMode, uint32_t opMask, core::Sol& child) {
    child = parent;
    occ::OccIndex oc;
    buildOcc(pb, child, horizon, oc);
    repair::Ctx c = context(pb, child, oc, horizon, deadline, seed);
    alns::RunParams rp;
    rp.qloF = 0.03; rp.qhiF = 0.20; rp.opMask = opMask;
    rp.repairMode = repairMode; rp.maxIters = 2;
    const double budget = std::chrono::duration<double>(
        deadline - std::chrono::steady_clock::now()).count();
    if (budget <= 0.0) return false;
    alns::run(c, budget, rp, seed, 0);
    return !repair::deadlineExpired(c) && completeAndAudited(pb, child, horizon);
}
 

inline bool patternLess(const Pattern& a, const Pattern& b) {
    if (a.reducedCost != b.reducedCost) return a.reducedCost < b.reducedCost;
    if (a.localCost != b.localCost) return a.localCost < b.localCost;
    return a.blocks < b.blocks;
}

} // namespace detail

template<class EngineT>
Result run(EngineT& engine, double seconds, uint64_t seed) {
    using Clock = std::chrono::steady_clock;
    Result out;
    const core::Problem& pb = engine.pb;
    const core::Sol saved = engine.sol;
    int horizon = engine.horizon_;
    for (int i = 0; i < pb.n; ++i) horizon = std::max(horizon, pb.D[i] + pb.P[i] + 1);
    horizon = std::max(1, horizon);
    core::Sol initial = saved;
    out.initialObjective = out.bestObjective = core::evaluate(pb, saved).total;
    if (seconds <= 0.0 || pb.n == 0 || pb.m == 0 || !detail::completeAndAudited(pb, initial, horizon))
        return out;

    const auto deadline = Clock::now() + std::chrono::duration_cast<Clock::duration>(
        std::chrono::duration<double>(seconds));
    std::mt19937_64 rng(seed ^ 0xd1b54a32d192ed03ULL);
    std::vector<core::Sol> population{initial};
    std::vector<double> ownershipPrice(pb.n, 0.0);
    core::Sol best = initial, candidate;
    auto auditCompleteBoard = [&](core::Sol& board) {
        ++out.exactAudits;
        if (!detail::completeAndAudited(pb, board, horizon)) return;
        ++out.completeCandidates;
        const double value = core::evaluate(pb, board).total;
        if (value + 1e-12 < out.bestObjective) {
            out.bestObjective = value;
            best = board;
        }
    };
    // Coherent source boards are complete candidates immediately; reserve the
    // master budget for mixed-bay exchanges instead of rediscovering them.
    auditCompleteBoard(population.front());

    // Reserve time for pricing and the master even when a difficult repair
    // repeatedly fails.  Attempts, rather than successful boards, are bounded.
    const auto start = Clock::now();
    const auto sourceDeadline = start + std::chrono::duration_cast<Clock::duration>(
        std::chrono::duration<double>(seconds * 0.40));
    const auto priceDeadline = start + std::chrono::duration_cast<Clock::duration>(
        std::chrono::duration<double>(seconds * 0.75));
    const int populationCap = std::max(4, std::min(16, 3 + pb.m));
    const int sourceAttempts = std::max(6, std::min(48, 4 + 3 * pb.m));
    for (int round = 0; round < sourceAttempts && Clock::now() < sourceDeadline; ++round) {
        const core::Sol& parent = population[static_cast<size_t>(rng() % population.size())];
        core::Sol child;
        const int span = std::max(1, std::min(pb.n, 1 + (round % 4 + 1) * (pb.n / 16 + 1)));
        const auto policy = static_cast<detail::ReplayPolicy>(round % 3);
        const auto now = Clock::now();
        const auto attemptDeadline = now + (sourceDeadline - now) /
            std::max(1, sourceAttempts - round);
        const bool useAlns = (round & 1) == 0 && pb.n > 1;
        const bool ok = useAlns
            ? detail::alnsDiversify(pb, parent, horizon, attemptDeadline,
                                    seed + static_cast<uint64_t>(round), round % 4,
                                    (round % 3 == 0) ? 0x155u : (round % 3 == 1) ? 0x2AAu : 0x3FFu,
                                    child)
            : detail::mutate(pb, parent, horizon, ownershipPrice, rng, attemptDeadline,
                             seed + static_cast<uint64_t>(round), policy, span, child);
        if (ok) {
            auditCompleteBoard(child);
            if ((int)population.size() < populationCap) population.push_back(std::move(child));
            else population[1 + static_cast<size_t>(rng() % (population.size() - 1))] = std::move(child);
            ++out.populationBoards;
        }
    }
    out.populationBoards += 1; // initial audited board

    std::vector<std::vector<detail::Pattern>> patterns(pb.m);
    std::vector<detail::Pattern> incumbentPatterns;
    incumbentPatterns.reserve(pb.m);
    for (int j = 0; j < pb.m; ++j) incumbentPatterns.push_back(detail::extract(pb, initial, j, 0));
    auto addBoard = [&](const core::Sol& s, int source, bool priced) {
        for (int j = 0; j < pb.m; ++j) {
            detail::Pattern p = detail::extract(pb, s, j, source);
            bool handled = false;
            for (auto& old : patterns[j]) if (detail::sameOwnership(old, p)) {
                // Equal ownership fixes this bay's workload, so z2 is identical;
                // localCost is therefore an exact objective dominance test.
                if (p.localCost + 1e-12 < old.localCost) old = std::move(p);
                handled = true;
                ++out.dominatedColumns;
                break;
            }
            if (!handled) { patterns[j].push_back(std::move(p)); ++out.generatedColumns; if (priced) ++out.pricedColumns; }
        }
    };
    for (size_t k = 0; k < population.size(); ++k) addBoard(population[k], static_cast<int>(k), false);

    // Column prices are a Lagrangian-style ownership signal.  Re-mutating the
    // best population members with it produces columns that resolve scarce
    // blocks rather than another unpriced crossover population.
    for (int j = 0; j < pb.m; ++j)
        for (const auto& p : patterns[j]) for (int i : p.blocks) ownershipPrice[i] += 1.0;
    for (double& v : ownershipPrice) v = std::max(0.0, v - (double)patterns.size());
    const int priceAttempts = std::max(4, std::min(32, 2 * pb.m + 2));
    const int priceDestroyCap = std::max(2, std::min(pb.n, std::min(12, pb.n / 10 + 3)));
    for (int round = 0; round < priceAttempts && Clock::now() < priceDeadline; ++round) {
        core::Sol child;
        // Price selective replacements from a coherent parent rather than
        // repeatedly rebuilding large, incompatible covers.
        const core::Sol& parent = population[static_cast<size_t>(round) % population.size()];
        const int span = std::min(priceDestroyCap, 2 + round % priceDestroyCap);
        const auto policy = static_cast<detail::ReplayPolicy>((round + 1) % 3);
        const auto now = Clock::now();
        const auto attemptDeadline = now + (priceDeadline - now) /
            std::max(1, priceAttempts - round);
        if (detail::mutate(pb, parent, horizon, ownershipPrice, rng, attemptDeadline,
                           seed ^ (0x9e3779b97f4a7c15ULL + (uint64_t)round),
                           policy, span, child)) {
            auditCompleteBoard(child);
            const int source = static_cast<int>(population.size());
            population.push_back(std::move(child)); // columns retain an immutable source board
            addBoard(population.back(), source, true);
        }
    }

    for (int j = 0; j < pb.m; ++j) {
        for (auto& p : patterns[j]) {
            double dual = 0.0;
            for (int i : p.blocks) dual += ownershipPrice[i];
            p.reducedCost = p.localCost - dual;
        }
        std::sort(patterns[j].begin(), patterns[j].end(), detail::patternLess);
        constexpr size_t kColumnsPerBay = 16;
        if (patterns[j].size() > kColumnsPerBay) {
            // Keep low-cost columns, but avoid a restricted master made of
            // near-clones from one board. Novel coverage and source diversity
            // are quality tie-breakers, never feasibility assumptions.
            std::vector<detail::Pattern> kept;
            std::vector<uint8_t> covered(pb.n, 0), sourceSeen(population.size(), 0);
            const double base = patterns[j].front().localCost;
            // Never price away the incumbent ownership partition: it is the
            // one known complete cover and provides a feasible master spine.
            for (const auto& p : patterns[j]) {
                if (!detail::sameOwnership(p, incumbentPatterns[j])) continue;
                for (int i : p.blocks) covered[i] = 1;
                if (p.source >= 0 && p.source < (int)sourceSeen.size()) sourceSeen[p.source] = 1;
                kept.push_back(p);
                break;
            }
            while (kept.size() < kColumnsPerBay) {
                size_t pick = patterns[j].size();
                double pickScore = std::numeric_limits<double>::infinity();
                for (size_t k = 0; k < patterns[j].size(); ++k) {
                    const auto& p = patterns[j][k];
                    bool already = false;
                    for (const auto& q : kept)
                        if (q.blocks == p.blocks && q.source == p.source) { already = true; break; }
                    if (already) continue;
                    int novel = 0;
                    for (int i : p.blocks) novel += !covered[i];
                    const double coverage = p.blocks.empty() ? 0.0 : (double)novel / p.blocks.size();
                    const bool freshSource = p.source >= 0 && p.source < (int)sourceSeen.size() &&
                                             !sourceSeen[p.source];
                    const double quality = (p.localCost - base) / (1.0 + std::fabs(base));
                    const double score = quality - 0.30 * coverage - (freshSource ? 0.10 : 0.0);
                    if (score < pickScore) { pickScore = score; pick = k; }
                }
                if (pick == patterns[j].size()) break;
                const auto& p = patterns[j][pick];
                for (int i : p.blocks) covered[i] = 1;
                if (p.source >= 0 && p.source < (int)sourceSeen.size()) sourceSeen[p.source] = 1;
                kept.push_back(p);
            }
            patterns[j] = std::move(kept);
            out.columnLimitHit = true;
        }
        out.retainedColumns += static_cast<long>(patterns[j].size());
    }

    std::vector<int> bayOrder(pb.m);
    std::iota(bayOrder.begin(), bayOrder.end(), 0);
    std::sort(bayOrder.begin(), bayOrder.end(), [&](int a, int b) {
        return patterns[a].size() < patterns[b].size();
    });
    for (int j : bayOrder) if (patterns[j].empty()) { out.deadlineHit = Clock::now() >= deadline; return out; }

    // Coherent source boards were audited as soon as they were produced.
    std::vector<int> selected(pb.m, -1), owner(pb.n, -1);
    std::vector<double> minCost(pb.m, 0.0);
    for (int j = 0; j < pb.m; ++j) {
        minCost[j] = std::numeric_limits<double>::infinity();
        for (const auto& p : patterns[j]) minCost[j] = std::min(minCost[j], p.lowerCost);
    }
    const bool objectiveBoundValid = pb.w1 >= 0.0 && pb.w2 >= 0.0 && pb.w3 >= 0.0;

    constexpr uint64_t kMasterNodeCap = 50000;
    std::function<void(int, double)> dfs = [&](int depth, double partial) {
        if (Clock::now() >= deadline) { out.deadlineHit = true; return; }
        if (out.masterNodes >= kMasterNodeCap) { out.nodeLimitHit = true; return; }
        ++out.masterNodes;
        double lb = partial;
        for (int q = depth; q < pb.m; ++q) lb += minCost[bayOrder[q]];
        // Forced bays fix preference cost; release plus duration lower-bounds
        // tardiness, and z2 is non-negative. Repacking cannot beat this bound.
        if (objectiveBoundValid && lb >= out.bestObjective - 1e-12) { ++out.masterPruned; return; }
        if (depth == pb.m) {
            if (std::find(owner.begin(), owner.end(), -1) != owner.end()) return;
            ++out.exactAudits;
            const auto policy = static_cast<detail::ReplayPolicy>(out.masterNodes % 3);
            if (!detail::replayPartition(pb, owner, horizon, deadline,
                                         seed + out.masterNodes, policy, candidate)) return;
            if (alns::audit(pb, candidate, nullptr, true) != 0) return;
            ++out.completeCandidates;
            const double value = core::evaluate(pb, candidate).total; // exact z1/z2/z3
            if (value + 1e-12 < out.bestObjective) { out.bestObjective = value; best = candidate; }
            return;
        }
        const int j = bayOrder[depth];
        for (size_t k = 0; k < patterns[j].size(); ++k) {
            const detail::Pattern& p = patterns[j][k];
            bool conflict = false;
            for (int i : p.blocks) if (owner[i] >= 0) { conflict = true; ++out.ownershipConflicts; break; }
            if (conflict) continue;
            for (int i : p.blocks) owner[i] = j;
            selected[j] = static_cast<int>(k);
            dfs(depth + 1, partial + p.lowerCost);
            selected[j] = -1;
            for (int i : p.blocks) owner[i] = -1;
            if (out.deadlineHit) return;
            if (out.nodeLimitHit) return;
        }
    };
    dfs(0, 0.0);

    out.improved = out.bestObjective + 1e-12 < out.initialObjective;
    out.acceptedGain = out.initialObjective - out.bestObjective;
    engine.sol = out.improved ? best : saved; // strict transactional commit
    engine.buildOcc(horizon);
    return out;
}

} // namespace kernel_pattern_master

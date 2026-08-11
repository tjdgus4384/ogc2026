// kernel_assignment_cycles.hpp -- exact-audited small bay-assignment exchanges.
#pragma once

#include "alns.hpp"
#include "repair_scored.hpp"
#include "repair_nfp_contact.hpp"
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <limits>
#include <numeric>
#include <vector>
#include <utility>

namespace kernel_assignment_cycles {

struct Result {
    long pairProposals = 0;
    long cycleProposals = 0;
    long completeReconstructions = 0;
    long exactAudits = 0;
    long acceptedGains = 0;
    double initialObjective = std::numeric_limits<double>::infinity();
    double bestObjective = std::numeric_limits<double>::infinity();
    bool improved = false;
    bool deadlineHit = false;
};

namespace detail {

struct Proposal {
    std::vector<int> blocks;
    std::vector<int> targets;
    double estimate = 0.0;
};

inline bool completeAndAudited(const core::Problem& pb, core::Sol& s, int horizon) {
    if ((int)s.placed.size() != pb.n) return false;
    for (int i = 0; i < pb.n; ++i) {
        if (!s.placed[i] || s.bay[i] < 0 || s.bay[i] >= pb.m ||
            s.ori[i] < 0 || s.ori[i] >= (int)pb.geo[i].orients.size() ||
            s.e[i] < pb.R[i] || s.f[i] != s.e[i] + pb.P[i] || s.f[i] > horizon)
            return false;
    }
    s.rebuildIdx(pb.m);
    return core::stateExactEvents(pb, s) && alns::audit(pb, s, nullptr, true) == 0;
}

inline void rebuildOcc(const core::Problem& pb, core::Sol& s, int horizon, occ::OccIndex& oc) {
    s.rebuildIdx(pb.m);
    oc.build(pb, horizon);
    for (int i = 0; i < pb.n; ++i) if (s.placed[i]) oc.stamp(pb, s, i, true);
}

inline double assignmentEstimate(const core::Problem& pb, const core::Sol& s,
                                 const std::vector<int>& blocks,
                                 const std::vector<int>& targets) {
    std::vector<double> load(pb.m, 0.0);
    double z3 = 0.0;
    for (int i = 0; i < pb.n; ++i) {
        load[s.bay[i]] += pb.workload[i];
        z3 += pb.Smax[i] - pb.pref[i][s.bay[i]];
    }
    for (size_t q = 0; q < blocks.size(); ++q) {
        const int i = blocks[q], to = targets[q];
        load[s.bay[i]] -= pb.workload[i];
        load[to] += pb.workload[i];
        z3 += pb.pref[i][s.bay[i]] - pb.pref[i][to];
    }
    double z2 = 0.0;
    for (int a = 0; a < pb.m; ++a)
        for (int b = a + 1; b < pb.m; ++b)
            z2 = std::max(z2, std::fabs(pb.u[a] * load[a] - pb.u[b] * load[b]));
    return pb.w2 * std::floor(z2) + pb.w3 * z3;
}

inline void orderBlocks(const core::Problem& pb, const core::Sol& base,
                        std::vector<int>& blocks, int policy) {
    std::sort(blocks.begin(), blocks.end(), [&](int a, int b) {
        if (policy == 0) {
            if (pb.D[a] != pb.D[b]) return pb.D[a] < pb.D[b];
            if (pb.P[a] != pb.P[b]) return pb.P[a] > pb.P[b];
        } else if (policy == 1) {
            const double ra = pb.Smax[a] - pb.pref[a][base.bay[a]];
            const double rb = pb.Smax[b] - pb.pref[b][base.bay[b]];
            if (ra != rb) return ra > rb;
        } else if (pb.P[a] != pb.P[b]) {
            return pb.P[a] > pb.P[b];
        }
        return a < b;
    });
}

inline bool reconstruct(const core::Problem& pb, const core::Sol& base, const Proposal& proposal,
                        int policy, int horizon, bool requireZeroTardy,
                        std::chrono::steady_clock::time_point deadline, uint64_t seed,
                        core::Sol& out, Result& result) {
    out = base; // All removal and repair is private to this transactional copy.
    occ::OccIndex oc;
    rebuildOcc(pb, out, horizon, oc);
    repair::Ctx c{};
    c.pb = &pb; c.s = &out; c.oc = &oc; c.horizon = horizon;
    c.exactEvents = true; c.hasDeadline = true; c.deadline = deadline;
    c.rng.seed((uint32_t)(seed ^ (seed >> 32)));
    std::vector<int> forced(pb.n, -1);
    for (size_t q = 0; q < proposal.blocks.size(); ++q) {
        const int i = proposal.blocks[q];
        if (forced[i] >= 0 || proposal.targets[q] < 0 || proposal.targets[q] >= pb.m) return false;
        forced[i] = proposal.targets[q];
        repair::removeBlock(c, i);
    }
    c.forcedBay = forced.data();
    c.releaseForcedBay = false;
    std::vector<int> order = proposal.blocks;
    orderBlocks(pb, base, order, policy);
    for (int i : order) {
        if (repair::deadlineExpired(c)) return false;
        bool inserted = false;
        if (policy == 1) {
            nfp_contact::Config cfg;
            cfg.maxEntryTimes = 32; cfg.maxCandidates = 320;
            cfg.maxContactsPerResident = 48; cfg.includeGeneral = true;
            inserted = nfp_contact::insert(c, i, cfg, nullptr);
        } else {
            repair_scored::Config cfg;
            cfg.topK = policy == 0 ? 20 : 28;
            cfg.maxCandidates = policy == 0 ? 320 : 448;
            cfg.maxTimes = 40; cfg.contactAxisCap = 56;
            inserted = repair_scored::insertBest(c, i, cfg, nullptr);
        }
        if (!inserted || repair::deadlineExpired(c)) return false;
    }
    if (requireZeroTardy) {
        for (int i : proposal.blocks) if (out.f[i] > pb.D[i]) return false;
    }
    ++result.exactAudits;
    if (!completeAndAudited(pb, out, horizon)) return false;
    ++result.completeReconstructions;
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

    const core::Objective initialValue = core::evaluate(pb, initial);
    result.initialObjective = result.bestObjective = initialValue.total;
    const bool requireZeroTardy = initialValue.z1 == 0.0;
    const auto deadline = Clock::now() + std::chrono::duration_cast<Clock::duration>(
        std::chrono::duration<double>(seconds));
    core::Sol incumbent = initial;

    while (Clock::now() < deadline) {
        std::vector<int> ranked(pb.n);
        std::iota(ranked.begin(), ranked.end(), 0);
        std::sort(ranked.begin(), ranked.end(), [&](int a, int b) {
            const double ra = pb.Smax[a] - pb.pref[a][incumbent.bay[a]];
            const double rb = pb.Smax[b] - pb.pref[b][incumbent.bay[b]];
            return ra != rb ? ra > rb : a < b;
        });
        std::vector<int> focus;
        std::vector<int> perBay(pb.m, 0);
        for (int i : ranked) {
            const int bay = incumbent.bay[i];
            if (perBay[bay] >= 6) continue;
            focus.push_back(i);
            ++perBay[bay];
        }
        std::vector<detail::Proposal> proposals;
        const double currentAssignment = detail::assignmentEstimate(pb, incumbent, {}, {});
        for (size_t a = 0; a < focus.size(); ++a) for (size_t b = a + 1; b < focus.size(); ++b) {
            const int i = focus[a], k = focus[b];
            if (incumbent.bay[i] == incumbent.bay[k]) continue;
            detail::Proposal p{{i, k}, {incumbent.bay[k], incumbent.bay[i]}};
            p.estimate = detail::assignmentEstimate(pb, incumbent, p.blocks, p.targets) - currentAssignment;
            proposals.push_back(std::move(p));
            ++result.pairProposals;
        }
        for (size_t a = 0; a < focus.size(); ++a) for (size_t b = 0; b < focus.size(); ++b)
            for (size_t c = 0; c < focus.size(); ++c) {
                if (a == b || b == c || a == c) continue;
                const int i = focus[a], k = focus[b], l = focus[c];
                if (incumbent.bay[i] == incumbent.bay[k] || incumbent.bay[k] == incumbent.bay[l] ||
                    incumbent.bay[i] == incumbent.bay[l]) continue;
                detail::Proposal p{{i, k, l}, {incumbent.bay[k], incumbent.bay[l], incumbent.bay[i]}};
                p.estimate = detail::assignmentEstimate(pb, incumbent, p.blocks, p.targets) - currentAssignment;
                proposals.push_back(std::move(p));
                ++result.cycleProposals;
            }
        std::sort(proposals.begin(), proposals.end(), [](const detail::Proposal& a, const detail::Proposal& b) {
            return a.estimate != b.estimate ? a.estimate < b.estimate : a.blocks < b.blocks;
        });
        if (proposals.empty()) break;
        const size_t limit = std::min<size_t>(192, proposals.size());
        bool accepted = false;
        for (size_t q = 0; q < limit && Clock::now() < deadline; ++q) {
            // The estimate merely orders exact proposals; it is never a feasibility or acceptance claim.
            for (int policy = 0; policy != 3 && Clock::now() < deadline; ++policy) {
                core::Sol trial;
                const auto localDeadline = std::min(
                    deadline, Clock::now() + std::chrono::milliseconds(250));
                if (!detail::reconstruct(pb, incumbent, proposals[q], policy, horizon, requireZeroTardy,
                                         localDeadline, seed + q + ((uint64_t)policy << 32), trial, result))
                    continue;
                const core::Objective value = core::evaluate(pb, trial);
                if ((requireZeroTardy && value.z1 != 0.0) ||
                    value.total + 1e-9 >= result.bestObjective) continue;
                result.bestObjective = value.total;
                incumbent = std::move(trial);
                result.improved = true;
                ++result.acceptedGains;
                accepted = true;
                break;
            }
            if (accepted) break;
        }
        if (!accepted) break;
    }
    result.deadlineHit = Clock::now() >= deadline;
    if (result.improved) {
        // This final guard makes the live update atomic: rejected/timeout trials never touched engine.sol.
        ++result.exactAudits;
        if (detail::completeAndAudited(pb, incumbent, horizon) &&
            (!requireZeroTardy || core::evaluate(pb, incumbent).z1 == 0.0) &&
            core::evaluate(pb, incumbent).total + 1e-9 < result.initialObjective) {
            engine.sol = std::move(incumbent);
            engine.buildOcc(horizon);
        } else {
            result.improved = false;
            result.bestObjective = result.initialObjective;
            result.acceptedGains = 0;
        }
    }
    return result;
}

} // namespace kernel_assignment_cycles

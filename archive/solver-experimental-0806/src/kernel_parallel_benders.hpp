// kernel_parallel_benders.hpp -- bounded logic-based Benders neighbourhood search.
#pragma once

#include "alns.hpp"
#include "repair_exact_coalition.hpp"
#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <numeric>
#include <random>
#include <vector>

namespace kernel_parallel_benders {

// A cut is immutable once published.  It is a necessary geometric condition:
// block may never be assigned to bay.  No bounded subproblem failure creates a
// cut, because a truncated search is not an infeasibility certificate.
struct StructuralCut {
    int block = -1;
    int bay = -1;
    unsigned born = 0;
    unsigned lastUsed = 0;
};

struct LaneStats {
    long masterProposals = 0;
    long masterPrunedByCertificate = 0;
    long fastRepairAttempts = 0;
    long fastRepairFeasible = 0;
    long exactCoalitionChecks = 0;
    long exactChecks = 0;
    long exactFeasible = 0;
    long exactUnknown = 0;
    long exactTimeouts = 0;
    long replayFailures = 0;
    long acceptedGains = 0;
    long structuralCutsUsed = 0;
    int coalition = 4;
    int trustRadius = 2;
};

struct Result {
    long rounds = 0;
    long joins = 0;
    long structuralCutsGenerated = 0;
    long cutsAgedOut = 0;
    long objectiveCertificates = 0;
    long fastRepairAttempts = 0;
    long fastRepairFeasible = 0;
    long exactCoalitionChecks = 0;
    long exactChecks = 0;
    long unknowns = 0;
    long timeouts = 0;
    long acceptedGains = 0;
    double initialObjective = std::numeric_limits<double>::infinity();
    double bestObjective = std::numeric_limits<double>::infinity();
    bool improved = false;
    bool deadlineHit = false;
    std::array<LaneStats, 4> lane{};
};

namespace detail {

struct Choice { int block = -1, bay = -1, entry = -1; };
struct ScoredChoice {
    Choice choice;
    double score = std::numeric_limits<double>::infinity();
};

struct PlanGuard {
    std::vector<Choice> plan; // Exact master assignment, not an infeasibility claim.
    unsigned born = 0;
    unsigned lastUsed = 0;
};

inline bool bayCanContain(const core::Problem& pb, int block, int bay) {
    if (bay < 0 || bay >= pb.m || block < 0 || block >= pb.n) return false;
    for (const auto& g : pb.geo[block].orients) {
        const double w = g.bb.x1 - g.bb.x0;
        const double h = g.bb.y1 - g.bb.y0;
        if (w <= pb.bayW[bay] && h <= pb.bayH[bay]) return true;
    }
    return false;
}

inline bool completeExactReplay(const core::Problem& pb, core::Sol& s, int horizon) {
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

inline bool hasCut(std::vector<StructuralCut>& cuts, int block, int bay,
                   unsigned proposal, long& used) {
    for (StructuralCut& cut : cuts) {
        if (cut.block == block && cut.bay == bay) {
            cut.lastUsed = proposal; // Metadata only; the cut predicate never changes.
            ++used;
            return true;
        }
    }
    return false;
}

inline double certificateLowerBound(const core::Problem& pb, const core::Sol& base,
                                    const std::vector<Choice>& plan) {
    // z2 is non-negative.  This is therefore a sound lower bound when the
    // official objective weights are non-negative, unlike a pretend LP dual.
    std::vector<int> bay = base.bay, entry = base.e;
    for (const Choice& q : plan) { bay[q.block] = q.bay; entry[q.block] = q.entry; }
    double bound = 0.0;
    for (int i = 0; i < pb.n; ++i) {
        bound += pb.w1 * std::max(0, entry[i] + pb.P[i] - pb.D[i]);
        bound += pb.w3 * (pb.Smax[i] - pb.pref[i][bay[i]]);
    }
    return bound;
}
inline double plannedObjective(const core::Problem& pb, const core::Sol& base,
                               const std::vector<Choice>& plan) {
    core::Sol candidate = base;
    for (const Choice& q : plan) {
        candidate.bay[q.block] = q.bay;
        candidate.e[q.block] = q.entry;
        candidate.f[q.block] = q.entry + pb.P[q.block];
    }
    return core::evaluate(pb, candidate).total;
}

inline bool samePlan(const std::vector<Choice>& a, const std::vector<Choice>& b) {
    if (a.size() != b.size()) return false;
    for (size_t i = 0; i < a.size(); ++i)
        if (a[i].block != b[i].block || a[i].bay != b[i].bay || a[i].entry != b[i].entry)
            return false;
    return true;
}

inline bool hasPlanGuard(std::vector<PlanGuard>& guards, const std::vector<Choice>& plan,
                         unsigned proposal) {
    for (PlanGuard& guard : guards) {
        if (samePlan(guard.plan, plan)) {
            guard.lastUsed = proposal;
            return true;
        }
    }
    return false;
}

inline void eventTimes(const core::Problem& pb, const core::Sol& s, int block,
                       int radius, int horizon, std::vector<int>& times) {
    times.clear();
    const int lo = std::max(pb.R[block], s.e[block] - radius);
    const int hi = std::min(horizon - pb.P[block], s.e[block] + radius);
    if (lo > hi) return;
    times.push_back(lo);
    times.push_back(hi);
    const int punctual = pb.D[block] - pb.P[block];
    if (punctual >= lo && punctual <= hi) times.push_back(punctual);
    for (int i = 0; i < pb.n; ++i) if (s.placed[i]) {
        if (s.e[i] >= lo && s.e[i] <= hi) times.push_back(s.e[i]);
        if (s.f[i] >= lo && s.f[i] <= hi) times.push_back(s.f[i]);
    }
    std::sort(times.begin(), times.end());
    times.erase(std::unique(times.begin(), times.end()), times.end());
}

} // namespace detail

// EngineT is the normal ALNS engine surface: pb, sol, ctx, horizon_, buildOcc.
// Four deterministic lanes have private RNG/trust state; they exchange only the
// audited, immutable structural-cut pool and the strictly better incumbent.
template<class EngineT>
Result run(EngineT& eng, double seconds, uint64_t seed) {
    using Clock = std::chrono::steady_clock;
    Result out;
    const core::Problem& pb = eng.pb;
    const core::Sol saved = eng.sol;
    int horizon = eng.horizon_;
    for (int i = 0; i < pb.n; ++i) horizon = std::max(horizon, pb.D[i] + pb.P[i] + 1);
    horizon = std::max(1, horizon);
    out.initialObjective = out.bestObjective = core::evaluate(pb, saved).total;
    if (seconds <= 0.0 || pb.n < 4 || pb.m == 0) return out;

    core::Sol initial = saved;
    if (!detail::completeExactReplay(pb, initial, horizon)) return out;
    const auto deadline = Clock::now() + std::chrono::duration_cast<Clock::duration>(
        std::chrono::duration<double>(seconds));
    const repair::Ctx savedCtx = eng.ctx;
    core::Sol best = initial;
    std::vector<StructuralCut> cuts;
    constexpr size_t kCutCap = 512;
    constexpr unsigned kCutMaxIdleProposals = 96;
    std::vector<detail::PlanGuard> retryGuards;
    constexpr size_t kRetryGuardCap = 256;
    constexpr unsigned kRetryGuardMaxIdleProposals = 48;
    std::array<std::mt19937_64, 4> rng{{
        std::mt19937_64(seed ^ 0x9e3779b97f4a7c15ULL),
        std::mt19937_64(seed ^ 0xbf58476d1ce4e5b9ULL),
        std::mt19937_64(seed ^ 0x94d049bb133111ebULL),
        std::mt19937_64(seed ^ 0xd2b74407b1ce6e93ULL)}};

    // These are exact necessary geometry facts, independently audited before
    // any lane sees them.  They are the only cuts that may cross lane joins.
    for (int i = 0; i < pb.n; ++i) for (int j = 0; j < pb.m; ++j)
        if (!detail::bayCanContain(pb, i, j)) {
            cuts.push_back({i, j, 0, 0});
            ++out.structuralCutsGenerated;
        }

    unsigned proposalId = 0;
    while (Clock::now() < deadline) {
        ++out.rounds;
        for (int lane = 0; lane < 4 && Clock::now() < deadline; ++lane) {
            LaneStats& st = out.lane[lane];
            const core::Sol before = best;
            const int coalition = std::min(pb.n, std::max(4, st.coalition));
            std::vector<int> order(pb.n);
            std::iota(order.begin(), order.end(), 0);
            std::shuffle(order.begin(), order.end(), rng[lane]);
            std::sort(order.begin(), order.end(), [&](int a, int b) {
                const double ka = lane == 0 ? std::max(0, before.f[a] - pb.D[a]) :
                    lane == 1 ? pb.Smax[a] - pb.pref[a][before.bay[a]] :
                    lane == 2 ? pb.P[a] : std::abs(before.e[a] - pb.R[a]);
                const double kb = lane == 0 ? std::max(0, before.f[b] - pb.D[b]) :
                    lane == 1 ? pb.Smax[b] - pb.pref[b][before.bay[b]] :
                    lane == 2 ? pb.P[b] : std::abs(before.e[b] - pb.R[b]);
                return ka != kb ? ka > kb : a < b;
            });
            order.resize(coalition);

            std::vector<detail::Choice> plan;
            std::vector<int> times;
            bool masterOK = true;
            const int choiceRank = static_cast<int>((proposalId / 4 + lane) % 3);
            for (int block : order) {
                std::vector<detail::ScoredChoice> choices;
                detail::eventTimes(pb, before, block, st.trustRadius, horizon, times);
                for (int bay = 0; bay < pb.m; ++bay) {
                    if (detail::hasCut(cuts, block, bay, proposalId, st.structuralCutsUsed)) continue;
                    for (int entry : times) {
                        const int finish = entry + pb.P[block];
                        const double z1 = pb.w1 * std::max(0, finish - pb.D[block]);
                        const double z3 = pb.w3 * (pb.Smax[block] - pb.pref[block][bay]);
                        const double disruption = (bay != before.bay[block]) +
                            0.1 * std::abs(entry - before.e[block]);
                        const double laneBias = lane == 2
                            ? 0.25 * (bay == before.bay[block] ? 1.0 : 0.0)
                            : lane == 3 ? 0.05 * std::abs(entry - std::max(pb.R[block],
                                pb.D[block] - pb.P[block])) : 0.0;
                        const double score = z1 + z3 + laneBias + (lane == 0 ? 2.0 * z1 :
                            lane == 1 ? 2.0 * z3 : lane == 2 ? 0.25 * pb.P[block] : disruption);
                        choices.push_back({{block, bay, entry}, score});
                    }
                }
                std::sort(choices.begin(), choices.end(), [](const detail::ScoredChoice& a,
                                                            const detail::ScoredChoice& b) {
                    return a.score != b.score ? a.score < b.score :
                        a.choice.bay != b.choice.bay ? a.choice.bay < b.choice.bay :
                        a.choice.entry < b.choice.entry;
                });
                if (choices.empty()) { masterOK = false; break; }
                plan.push_back(choices[std::min(choiceRank, static_cast<int>(choices.size()) - 1)].choice);
            }
            std::sort(plan.begin(), plan.end(), [](const detail::Choice& a, const detail::Choice& b) {
                return a.block < b.block;
            });
            ++proposalId;
            ++st.masterProposals;
            if (!masterOK || Clock::now() >= deadline) continue;
            if (detail::hasPlanGuard(retryGuards, plan, proposalId)) continue;

            const double plannedValue = detail::plannedObjective(pb, before, plan);
            const bool plannedGain = plannedValue + 1e-12 < out.bestObjective;
            if (plannedGain) ++out.objectiveCertificates;
            if (pb.w1 >= 0.0 && pb.w2 >= 0.0 && pb.w3 >= 0.0 &&
                detail::certificateLowerBound(pb, before, plan) >= out.bestObjective - 1e-12) {
                ++st.masterPrunedByCertificate;
                ++out.objectiveCertificates;
                continue;
            }

            // Stage 1: cheap exact geometry/event checks over several deterministic
            // repair orders.  This makes routine local moves productive; the bounded
            // joint enumerator is a fallback, not the primary subproblem.
            std::vector<int> forced(pb.n, -1), caps(pb.n, -1);
            for (const detail::Choice& q : plan) { forced[q.block] = q.bay; caps[q.block] = q.entry; }
            const int* oldForced = eng.ctx.forcedBay;
            const int* oldCaps = eng.ctx.entryCap;
            const bool oldReleaseForcedBay = eng.ctx.releaseForcedBay;
            bool realized = false;
            bool fastDeadline = false;
            for (int pass = 0; pass < 3 && !realized && Clock::now() < deadline; ++pass) {
                std::vector<int> sequence = order;
                if (pass == 0) {
                    std::sort(sequence.begin(), sequence.end(), [&](int a, int b) {
                        return pb.D[a] != pb.D[b] ? pb.D[a] < pb.D[b] : a < b;
                    });
                } else if (pass == 1) {
                    std::reverse(sequence.begin(), sequence.end());
                } else {
                    std::sort(sequence.begin(), sequence.end(), [&](int a, int b) {
                        const double ra = pb.Smax[a] - pb.pref[a][before.bay[a]];
                        const double rb = pb.Smax[b] - pb.pref[b][before.bay[b]];
                        return ra != rb ? ra > rb : a < b;
                    });
                }
                eng.sol = before;
                eng.buildOcc(horizon);
                eng.ctx.hasDeadline = true;
                eng.ctx.deadline = deadline;
                eng.ctx.releaseForcedBay = false;
                eng.ctx.exactEvents = true;
                eng.ctx.forcedBay = forced.data();
                eng.ctx.entryCap = caps.data();
                for (int block : sequence) repair::removeBlock(eng.ctx, block);
                bool passOK = true;
                for (int block : sequence) {
                    ++st.fastRepairAttempts;
                    ++out.fastRepairAttempts;
                    if ((pass & 1) == 0) {
                        repair_scored::Config cfg;
                        cfg.topK = 16; cfg.maxCandidates = 320; cfg.maxTimes = 32; cfg.contactAxisCap = 64;
                        repair_scored::Stats stats;
                        passOK = repair_scored::insertBest(eng.ctx, block, cfg, &stats);
                        fastDeadline = fastDeadline || stats.deadlineExpired;
                    } else {
                        nfp_contact::Config cfg;
                        cfg.maxEntryTimes = 32; cfg.maxCandidates = 320;
                        cfg.maxContactsPerResident = 64; cfg.includeGeneral = true;
                        nfp_contact::Stats stats;
                        passOK = nfp_contact::insert(eng.ctx, block, cfg, &stats);
                        fastDeadline = fastDeadline || stats.deadlineStops != 0;
                    }
                    if (!passOK || Clock::now() >= deadline) break;
                }
                if (passOK && detail::completeExactReplay(pb, eng.sol, horizon)) {
                    realized = true;
                    ++st.fastRepairFeasible;
                    ++out.fastRepairFeasible;
                }
            }

            // Stage 2: bounded joint exact enumeration, attempted only after the
            // productive single-block ladder failed.  Failure remains UNKNOWN.
            repair::coalition::Stats exactStats;
            if (!realized && Clock::now() < deadline) {
                eng.sol = before;
                eng.buildOcc(horizon);
                eng.ctx.hasDeadline = true;
                eng.ctx.deadline = deadline;
                eng.ctx.releaseForcedBay = false;
                eng.ctx.exactEvents = true;
                eng.ctx.forcedBay = forced.data();
                eng.ctx.entryCap = caps.data();
                repair::coalition::Options opt;
                opt.nodeLimit = static_cast<size_t>(400 + 100 * coalition);
                opt.beamWidth = 8;
                opt.candidateLimit = 12;
                realized = repair::coalition::repairExactCoalition(eng.ctx, order, &exactStats, opt);
                ++st.exactCoalitionChecks;
                ++out.exactCoalitionChecks;
            }
            eng.ctx.forcedBay = oldForced;
            eng.ctx.entryCap = oldCaps;
            eng.ctx.releaseForcedBay = oldReleaseForcedBay;
            ++st.exactChecks;
            ++out.exactChecks;

            const bool replayed = realized && Clock::now() < deadline &&
                detail::completeExactReplay(pb, eng.sol, horizon);
            if (!replayed) {
                if (realized) ++st.replayFailures;
                else ++st.exactUnknown; // bounded failure is never an infeasibility proof.
                if (fastDeadline || exactStats.deadline || Clock::now() >= deadline) {
                    ++st.exactTimeouts;
                    ++out.timeouts;
                }
                // A bounded failure is UNKNOWN, but retrying this exact bounded
                // neighbourhood cannot reveal new information.  Guard only this
                // full assignment; it is not published as an infeasibility cut.
                retryGuards.push_back({plan, proposalId, proposalId});
                if (retryGuards.size() > kRetryGuardCap) retryGuards.erase(retryGuards.begin());
                ++out.unknowns;
                st.coalition = std::max(4, coalition - 1);
                st.trustRadius = std::min(8, st.trustRadius + 1);
            } else {
                ++st.exactFeasible;
                const double value = core::evaluate(pb, eng.sol).total;
                if (value + 1e-12 < out.bestObjective) {
                    best = eng.sol;
                    out.bestObjective = value;
                    ++st.acceptedGains;
                    ++out.acceptedGains;
                }
                st.coalition = std::min(pb.n, coalition + 1);
                st.trustRadius = std::min(8, st.trustRadius + 1);
            }
            // Transactional join: no unaudited lane state survives.
            eng.sol = best;
            eng.buildOcc(horizon);
            ++out.joins;
        }

        // Age metadata is not part of a cut's meaning.  Removing an unused cut
        // can only broaden a later master, and caps memory deterministically.
        cuts.erase(std::remove_if(cuts.begin(), cuts.end(), [&](const StructuralCut& cut) {
            const bool stale = proposalId > cut.lastUsed + kCutMaxIdleProposals;
            if (stale) ++out.cutsAgedOut;
            return stale;
        }), cuts.end());
        // Retry guards are intentionally local, bounded no-goods: UNKNOWN is not
        // an infeasibility certificate, so expiry restores the complete master.
        retryGuards.erase(std::remove_if(retryGuards.begin(), retryGuards.end(),
            [&](const detail::PlanGuard& guard) {
                return proposalId > guard.lastUsed + kRetryGuardMaxIdleProposals;
            }), retryGuards.end());
        if (cuts.size() > kCutCap) {
            const size_t excess = cuts.size() - kCutCap;
            cuts.erase(cuts.begin(), cuts.begin() + static_cast<std::ptrdiff_t>(excess));
            out.cutsAgedOut += static_cast<long>(excess);
        }
    }
    out.deadlineHit = Clock::now() >= deadline;
    out.improved = out.bestObjective + 1e-12 < out.initialObjective;
    eng.sol = out.improved ? best : saved;
    eng.buildOcc(horizon);
    eng.ctx = savedCtx;
    eng.ctx.pb = &eng.pb;
    eng.ctx.s = &eng.sol;
    eng.ctx.oc = &eng.oc;
    eng.ctx.horizon = horizon;
    return out;
}

} // namespace kernel_parallel_benders

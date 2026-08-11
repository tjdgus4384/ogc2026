// kernel_backbone_repack.hpp -- transactional backbone-preserving bay repacker.
#pragma once

#include "alns.hpp"
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <limits>
#include <numeric>
#include <random>
#include <string>
#include <unordered_set>
#include <vector>

namespace kernel_backbone_repack {

struct Result {
    long masterSolves = 0, masterCandidates = 0, distinctAssignments = 0;
    long pairAttempts = 0, allBayAttempts = 0, proposedMoves = 0, peakMutableBlocks = 0;
    long blockersReleased = 0, backboneReuse = 0;
    long geometryNodes = 0, geometryCandidates = 0, geometryRejects = 0, geometryBacktracks = 0;
    long completeReconstructions = 0, exactAudits = 0, strictImprovements = 0;
    long stageFailures = 0, caps = 0;
    double initialObjective = std::numeric_limits<double>::infinity();
    double bestObjective = std::numeric_limits<double>::infinity(), strictGain = 0.0;
    bool improved = false, deadlineHit = false;
};

namespace detail {
struct Action { int bay, ori, x, y, e; };

inline bool complete(const core::Problem& pb, core::Sol& s, int horizon) {
    if ((int)s.placed.size() != pb.n) return false;
    for (int i = 0; i < pb.n; ++i)
        if (!s.placed[i] || s.bay[i] < 0 || s.bay[i] >= pb.m || s.ori[i] < 0 ||
            s.ori[i] >= (int)pb.geo[i].orients.size() || s.e[i] < pb.R[i] ||
            s.f[i] != s.e[i] + pb.P[i] || s.f[i] > horizon) return false;
    s.rebuildIdx(pb.m);
    return core::stateExactEvents(pb, s) && alns::audit(pb, s, nullptr, true) == 0;
}

inline void rebuild(const core::Problem& pb, core::Sol& s, int horizon, occ::OccIndex& oc) {
    s.rebuildIdx(pb.m); oc.build(pb, horizon);
    for (int i = 0; i < pb.n; ++i) if (s.placed[i]) oc.stamp(pb, s, i, true);
}

inline std::string assignmentKey(const std::vector<int>& target) {
    std::string key; key.reserve(target.size() * 3);
    for (int b : target) { key.append(std::to_string(b)); key.push_back(','); }
    return key;
}

inline std::vector<int> eventTimes(const core::Problem& pb, const core::Sol& s, int i, int horizon) {
    std::vector<int> out{pb.R[i], pb.D[i] - pb.P[i], s.e[i]};
    for (int k = 0; k < pb.n; ++k) if (s.placed[k]) {
        out.push_back(s.e[k]); out.push_back(s.f[k]);
        out.push_back(s.e[k] - pb.P[i]); out.push_back(s.f[k] - pb.P[i]);
    }
    out.erase(std::remove_if(out.begin(), out.end(), [&](int e) { return e < pb.R[i] || e + pb.P[i] > horizon; }), out.end());
    std::sort(out.begin(), out.end()); out.erase(std::unique(out.begin(), out.end()), out.end());
    const auto incumbent = std::find(out.begin(), out.end(), s.e[i]);
    if (incumbent != out.end()) std::rotate(out.begin(), incumbent, incumbent + 1);
    return out;
}

inline std::vector<Action> actions(repair::Ctx& c, const core::Sol& base, int i, int target,
                                   int horizon, bool zeroTardy, Result& r, size_t cap) {
    const core::Problem& pb = *c.pb;
    std::vector<Action> out;
    if (target == base.bay[i]) {
        ++r.geometryCandidates;
        if (repair::tryPlacement(c, i, target, base.ori[i], base.x[i], base.y[i], base.e[i])) {
            out.push_back({target, base.ori[i], base.x[i], base.y[i], base.e[i]});
            repair::removeBlock(c, i);
        } else {
            ++r.geometryRejects;
        }
    }
    for (int e : eventTimes(pb, *c.s, i, horizon)) {
        if (zeroTardy && e + pb.P[i] > pb.D[i]) continue;
        for (int oi = 0; oi < (int)pb.geo[i].orients.size(); ++oi) {
            std::vector<int> xs, ys;
            repair::cornerSets(c, i, oi, target, e, e + pb.P[i], xs, ys);
            repair::shapeContactSets(c, i, oi, target, e, e + pb.P[i], xs, ys, 64);
            if (target == base.bay[i] && oi == base.ori[i] && e == base.e[i]) {
                xs.insert(xs.begin(), base.x[i]); ys.insert(ys.begin(), base.y[i]);
            }
            std::sort(xs.begin(), xs.end()); xs.erase(std::unique(xs.begin(), xs.end()), xs.end());
            std::sort(ys.begin(), ys.end()); ys.erase(std::unique(ys.begin(), ys.end()), ys.end());
            for (int x : xs) for (int y : ys) {
                if (repair::deadlineExpired(c) || out.size() >= cap) return out;
                ++r.geometryCandidates;
                if (!repair::tryPlacement(c, i, target, oi, x, y, e)) { ++r.geometryRejects; continue; }
                out.push_back({target, oi, x, y, e});
                repair::removeBlock(c, i);
            }
        }
    }
    return out;
}

inline std::string stateKey(const core::Sol& s, const std::vector<uint8_t>& mutableSet) {
    std::string key;
    for (int i = 0; i < (int)mutableSet.size(); ++i) if (mutableSet[i] && s.placed[i]) {
        key.append(std::to_string(i)); key.push_back(':'); key.append(std::to_string(s.bay[i]));
        key.push_back(':'); key.append(std::to_string(s.ori[i])); key.push_back(':');
        key.append(std::to_string(s.x[i])); key.push_back(':'); key.append(std::to_string(s.y[i]));
        key.push_back(':'); key.append(std::to_string(s.e[i])); key.push_back(';');
    }
    return key;
}

struct Search {
    const core::Problem& pb; const core::Sol& base; core::Sol& s; occ::OccIndex& oc;
    std::vector<int>& target; std::vector<uint8_t>& mutableSet; int horizon; bool zeroTardy;
    std::chrono::steady_clock::time_point deadline; Result& r; uint64_t nodes = 0;
    bool capped = false; std::unordered_set<std::string> seen;

    bool expired() const { return std::chrono::steady_clock::now() >= deadline; }
    bool releaseBlockers(repair::Ctx& c, int i) {
        int released = 0;
        const std::vector<int> residents = s.byBay[target[i]];
        for (int k : residents) if (!mutableSet[k] && s.placed[k] &&
            s.e[k] < base.e[i] + pb.P[i] && base.e[i] < s.f[k]) {
            mutableSet[k] = 1; target[k] = base.bay[k]; repair::removeBlock(c, k);
            ++released; ++r.blockersReleased;
            if (released == 16) break;
        }
        if (!released) return false;
        long count = 0; for (uint8_t q : mutableSet) count += q;
        r.peakMutableBlocks = std::max(r.peakMutableBlocks, count);
        return true;
    }
    bool dfs(repair::Ctx& c) {
        if (expired()) return false;
        if (++nodes > 25000) { capped = true; return false; }
        ++r.geometryNodes;
        int chosen = -1; std::vector<Action> chosenActions;
        for (int i = 0; i < pb.n; ++i) if (mutableSet[i] && !s.placed[i]) {
            std::vector<Action> a = actions(c, base, i, target[i], horizon, zeroTardy, r, 96);
            if (a.empty()) { if (releaseBlockers(c, i)) return dfs(c); return false; }
            if (chosen < 0 || a.size() < chosenActions.size()) { chosen = i; chosenActions = std::move(a); }
        }
        if (chosen < 0) return true;
        const std::string key = stateKey(s, mutableSet);
        if (!seen.insert(key).second) return false;
        for (const Action& a : chosenActions) {
            if (expired() || capped) return false;
            if (!repair::tryPlacement(c, chosen, a.bay, a.ori, a.x, a.y, a.e)) { ++r.geometryRejects; continue; }
            if (dfs(c)) return true;
            repair::removeBlock(c, chosen); ++r.geometryBacktracks;
        }
        return false;
    }
};

inline bool reconstruct(const core::Problem& pb, const core::Sol& base, std::vector<int> target,
                        int horizon, bool zeroTardy, std::chrono::steady_clock::time_point deadline,
                        uint64_t seed, Result& r, core::Sol& out) {
    out = base; occ::OccIndex oc; rebuild(pb, out, horizon, oc);
    repair::Ctx c{&pb, &out, &oc, std::mt19937((uint32_t)seed), horizon};
    c.exactEvents = true; c.hasDeadline = true; c.deadline = deadline;
    std::vector<uint8_t> mutableSet(pb.n, 0);
    for (int i = 0; i < pb.n; ++i) if (target[i] != base.bay[i]) { mutableSet[i] = 1; repair::removeBlock(c, i); }
    long count = 0; for (uint8_t q : mutableSet) count += q;
    r.peakMutableBlocks = std::max(r.peakMutableBlocks, count);
    Search search{pb, base, out, oc, target, mutableSet, horizon, zeroTardy, deadline, r};
    const bool ok = search.dfs(c);
    if (search.capped) ++r.caps;
    if (!ok || search.expired() || search.capped) return false;
    if (zeroTardy) for (int i = 0; i < pb.n; ++i) if (out.f[i] > pb.D[i]) return false;
    ++r.exactAudits;
    if (!complete(pb, out, horizon)) return false;
    ++r.completeReconstructions;
    return true;
}
} // namespace detail

template<class EngineT>
Result run(EngineT& engine, double seconds, uint64_t seed, int mode) {
    using Clock = std::chrono::steady_clock;
    Result r; const core::Problem& pb = engine.pb; const core::Sol saved = engine.sol;
    r.initialObjective = r.bestObjective = core::evaluate(pb, saved).total;
    if ((mode != 0 && mode != 1) || seconds <= 0 || pb.m < 2 || pb.n < 12) return r;
    int horizon = engine.horizon_; for (int i = 0; i < pb.n; ++i) horizon = std::max(horizon, pb.D[i] + pb.P[i] + 1);
    core::Sol initial = saved;
    if (!detail::complete(pb, initial, horizon)) { ++r.stageFailures; return r; }
    const bool zeroTardy = core::evaluate(pb, initial).z1 == 0.0;
    const auto deadline = Clock::now() + std::chrono::duration_cast<Clock::duration>(std::chrono::duration<double>(seconds));
    std::unordered_set<std::string> assignments; core::Sol best = initial;
    std::vector<std::pair<int, int>> pairs;
    for (int a = 0; a < pb.m; ++a) for (int b = a + 1; b < pb.m; ++b) pairs.push_back({a, b});
    for (int attempt = 0; attempt < 24 && Clock::now() < deadline; ++attempt) {
        ++r.masterSolves;
        std::vector<int> bays;
        int pairDirection = 2;
        if (mode == 0) {
            const auto pair = pairs[(attempt / 3) % pairs.size()];
            bays = {pair.first, pair.second};
            pairDirection = attempt % 3;
            ++r.pairAttempts;
        } else {
            bays.resize(pb.m);
            std::iota(bays.begin(), bays.end(), 0);
            ++r.allBayAttempts;
        }

        std::vector<int> target = best.bay;
        std::vector<double> load(pb.m, 0.0);
        for (int i = 0; i < pb.n; ++i) load[best.bay[i]] += pb.workload[i];
        auto balance = [&]() {
            double z2 = 0.0;
            for (int a = 0; a < pb.m; ++a) for (int b = a + 1; b < pb.m; ++b)
                z2 = std::max(z2, std::fabs(pb.u[a] * load[a] - pb.u[b] * load[b]));
            return std::floor(z2);
        };
        double currentBalance = balance();
        const int cardinality = 12 + ((attempt * 11 + (int)(seed % 37)) % 37);
        const double trust = (double)((attempt + (seed & 3)) % 5);
        std::vector<uint8_t> used(pb.n, 0);
        for (int picked = 0; picked < cardinality; ++picked) {
            int chosen = -1, chosenBay = -1;
            double chosenScore = std::numeric_limits<double>::infinity();
            for (int i = 0; i < pb.n; ++i) if (!used[i]) for (int to : bays) {
                const int from = target[i];
                if (to == from) continue;
                if (mode == 0 && ((pairDirection == 0 && !(from == bays[0] && to == bays[1])) ||
                    (pairDirection == 1 && !(from == bays[1] && to == bays[0])) ||
                    (pairDirection == 2 && !((from == bays[0] && to == bays[1]) ||
                                             (from == bays[1] && to == bays[0]))))) continue;
                load[from] -= pb.workload[i]; load[to] += pb.workload[i];
                const double nextBalance = balance();
                load[to] -= pb.workload[i]; load[from] += pb.workload[i];
                const double preferenceDelta = pb.w3 * (pb.pref[i][from] - pb.pref[i][to]);
                const double exactDelta = pb.w2 * (nextBalance - currentBalance) + preferenceDelta;
                const double trustPenalty = std::max(0.0, pb.pref[i][from] - pb.pref[i][to] - trust);
                const uint64_t mix = seed ^ ((uint64_t)(attempt + 1) * 0x9e3779b97f4a7c15ULL) ^
                    ((uint64_t)(i + 1) * 0xbf58476d1ce4e5b9ULL) ^ (uint64_t)(to + 17);
                const double score = exactDelta + trustPenalty * pb.w3 + (double)(mix % 1009) * 1e-7;
                if (score < chosenScore || (score == chosenScore && (chosen < 0 || i < chosen ||
                    (i == chosen && to < chosenBay)))) { chosen = i; chosenBay = to; chosenScore = score; }
            }
            if (chosen < 0) break;
            const int from = target[chosen];
            load[from] -= pb.workload[chosen]; load[chosenBay] += pb.workload[chosen];
            currentBalance = balance();
            target[chosen] = chosenBay; used[chosen] = 1;
        }
        long moves = 0;
        for (int i = 0; i < pb.n; ++i) if (target[i] != best.bay[i]) ++moves;
        if (moves < 12) { ++r.stageFailures; continue; }
        ++r.masterCandidates;
        const std::string key = detail::assignmentKey(target);
        if (!assignments.insert(key).second) continue;
        ++r.distinctAssignments;
        r.proposedMoves += moves;
        const auto now = Clock::now(); const auto localDeadline = std::min(deadline, now + (deadline - now) / std::max(1, 24 - attempt));
        core::Sol candidate;
        if (!detail::reconstruct(pb, best, target, horizon, zeroTardy, localDeadline, seed + attempt, r, candidate)) { ++r.stageFailures; continue; }
        const core::Objective value = core::evaluate(pb, candidate);
        if ((!zeroTardy || value.z1 == 0.0) && value.total + 1e-9 < r.bestObjective) {
            r.strictGain += r.bestObjective - value.total; r.bestObjective = value.total; best = std::move(candidate); r.improved = true; ++r.strictImprovements;
        }
    }
    r.deadlineHit = Clock::now() >= deadline;
    if (r.improved) {
        ++r.exactAudits;
        if (detail::complete(pb, best, horizon) && (!zeroTardy || core::evaluate(pb, best).z1 == 0.0) && core::evaluate(pb, best).total + 1e-9 < r.initialObjective) {
            for (int i = 0; i < pb.n; ++i) if (best.bay[i] == initial.bay[i] && best.ori[i] == initial.ori[i] && best.x[i] == initial.x[i] && best.y[i] == initial.y[i] && best.e[i] == initial.e[i]) ++r.backboneReuse;
            engine.sol = std::move(best); engine.buildOcc(horizon);
        } else { r.improved = false; r.bestObjective = r.initialObjective; r.strictImprovements = 0; r.strictGain = 0.0; }
    }
    return r;
}
} // namespace kernel_backbone_repack

// kernel_mcts_coalition.hpp -- bounded progressive-widening MCTS for exact coalition repair.
#pragma once

#include "alns.hpp"
#include "repair.hpp"
#include "repair_nfp_contact.hpp"
#include "repair_scored.hpp"
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <limits>
#include <random>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

namespace kernel_mcts_coalition {

struct Limits {
    uint64_t nodeCap = 512;
    size_t memoryCapBytes = 96u * 1024u * 1024u;
    size_t actionCap = 80;
    size_t rolloutActionCap = 32;
    int rolloutCount = 3;
    int coalitionMin = 3;
    int coalitionMax = 18;
    double exploration = 1.25;
    double uncertainty = 0.35;
};

struct Result {
    uint64_t expandedNodes = 0;
    uint64_t expandedActions = 0;
    uint64_t transpositionHits = 0;
    uint64_t completeRollouts = 0;
    uint64_t feasibleCandidates = 0;
    uint64_t acceptedGains = 0;
    uint64_t exactReplays = 0;
    uint64_t deadEnds = 0;
    uint64_t memoryCapHits = 0;
    double initialObjective = std::numeric_limits<double>::infinity();
    double bestObjective = std::numeric_limits<double>::infinity();
    double acceptedGain = 0.0;
    bool improved = false;
    bool deadlineHit = false;
    bool nodeCapHit = false;
};

namespace detail {

using Clock = std::chrono::steady_clock;

struct Action {
    int block = -1, bay = -1, ori = -1, x = 0, y = 0, entry = 0;
};

struct Node {
    core::Sol sol;
    std::vector<int> remaining;
    std::vector<Action> actions;
    std::vector<int> children;
    uint64_t visits = 0;
    double rewardSum = 0.0;
    double rewardSqSum = 0.0;
    size_t nextAction = 0;
    bool generated = false;
    bool exhausted = false;
};

inline uint64_t mix(uint64_t h, uint64_t v) {
    v += 0x9e3779b97f4a7c15ULL;
    v = (v ^ (v >> 30)) * 0xbf58476d1ce4e5b9ULL;
    v = (v ^ (v >> 27)) * 0x94d049bb133111ebULL;
    return (h ^ (v ^ (v >> 31))) * 0x9e3779b97f4a7c15ULL;
}

inline uint64_t actionKey(const Action& a) {
    uint64_t h = mix(0, static_cast<uint32_t>(a.block));
    h = mix(h, static_cast<uint32_t>(a.bay)); h = mix(h, static_cast<uint32_t>(a.ori));
    h = mix(h, static_cast<uint32_t>(a.x)); h = mix(h, static_cast<uint32_t>(a.y));
    return mix(h, static_cast<uint32_t>(a.entry));
}

// This is a content key rather than a lossy hash: transposition pruning can never
// merge different assignments, orientations, coordinates, or event times.
inline std::string stateKey(const core::Problem& pb, const core::Sol& s) {
    std::string key;
    key.reserve(static_cast<size_t>(pb.n) * 30);
    for (int i = 0; i < pb.n; ++i) {
        key += std::to_string(i); key += ':'; key += s.placed[i] ? '1' : '0';
        if (s.placed[i]) {
            key += ',' + std::to_string(s.bay[i]); key += ',' + std::to_string(s.ori[i]);
            key += ',' + std::to_string(s.x[i]); key += ',' + std::to_string(s.y[i]);
            key += ',' + std::to_string(s.e[i]); key += ',' + std::to_string(s.f[i]);
        }
        key += ';';
    }
    return key;
}

inline void rebuild(const core::Problem& pb, core::Sol& s, int horizon, occ::OccIndex& oc) {
    s.rebuildIdx(pb.m);
    oc.build(pb, horizon);
    for (int i = 0; i < pb.n; ++i) if (s.placed[i]) oc.stamp(pb, s, i, true);
}

inline bool replayComplete(const core::Problem& pb, core::Sol& s, int horizon) {
    if ((int)s.placed.size() != pb.n) return false;
    for (int i = 0; i < pb.n; ++i) {
        if (!s.placed[i] || s.bay[i] < 0 || s.bay[i] >= pb.m || s.ori[i] < 0 ||
            s.ori[i] >= (int)pb.geo[i].orients.size() || s.e[i] < pb.R[i] ||
            s.f[i] != s.e[i] + pb.P[i] || s.f[i] > horizon) return false;
    }
    s.rebuildIdx(pb.m);
    return core::stateExactEvents(pb, s) && alns::audit(pb, s, nullptr, true) == 0;
}

inline int chooseBlock(const core::Problem& pb, const core::Sol& s, const std::vector<int>& ids) {
    int chosen = ids.front();
    double best = -std::numeric_limits<double>::infinity();
    for (int i : ids) {
        int conflicts = 0;
        for (int k = 0; k < pb.n; ++k) if (s.placed[k] && pb.R[i] < s.f[k] && s.e[k] < pb.D[i]) ++conflicts;
        const int slack = pb.D[i] - pb.R[i] - pb.P[i];
        const double score = 1000.0 * std::max(0, -slack) + 20.0 * conflicts - 0.1 * slack - i * 1e-7;
        if (score > best) { best = score; chosen = i; }
    }
    return chosen;
}

inline std::vector<int> times(const core::Problem& pb, const core::Sol& s, int i, int horizon) {
    std::vector<int> out{pb.R[i], std::max(pb.R[i], pb.D[i] - pb.P[i]), horizon - pb.P[i]};
    for (int k = 0; k < pb.n; ++k) if (s.placed[k]) { out.push_back(s.e[k]); out.push_back(s.f[k]); }
    std::sort(out.begin(), out.end()); out.erase(std::unique(out.begin(), out.end()), out.end());
    out.erase(std::remove_if(out.begin(), out.end(), [&](int e) { return e < pb.R[i] || e + pb.P[i] > horizon; }), out.end());
    return out;
}

inline bool apply(const core::Problem& pb, const core::Sol& parent, const Action& a, int horizon,
                  Clock::time_point deadline, core::Sol& child) {
    child = parent;
    occ::OccIndex oc;
    rebuild(pb, child, horizon, oc);
    repair::Ctx ctx{&pb, &child, &oc, std::mt19937(0), horizon};
    ctx.exactEvents = true; ctx.hasDeadline = true; ctx.deadline = deadline;
    return repair::tryPlacement(ctx, a.block, a.bay, a.ori, a.x, a.y, a.entry);
}

// The incumbent replay is always considered first.  It is an exact witness for
// every coalition prefix, so it prevents a large candidate enumeration from
// starving the root before MCTS can expand at all.
// Enumerate a small deterministic contact basis before attempting placements.
// Dense-board axes must be bounded before feasibility probes, not afterward.
inline std::vector<Action> exactActions(const core::Problem& pb, const core::Sol& s, int block,
                                        const core::Sol& guide, int horizon,
                                        Clock::time_point deadline, size_t cap) {
    struct Ranked { Action action; double objective; bool replay; };
    std::vector<Ranked> ranked;
    const auto started = Clock::now();
    const auto localDeadline = std::min(deadline, started + std::chrono::milliseconds(20));
    auto appendIfExact = [&](const Action& a, bool replay) {
        if (ranked.size() >= cap || Clock::now() >= localDeadline) return;
        core::Sol trial;
        if (apply(pb, s, a, horizon, localDeadline, trial))
            ranked.push_back({a, core::evaluate(pb, trial).total, replay});
    };
    if (guide.placed[block])
        appendIfExact({block, guide.bay[block], guide.ori[block], guide.x[block],
                       guide.y[block], guide.e[block]}, true);

    const std::vector<int> eventTimes = times(pb, s, block, horizon);
    const size_t probeCap = std::max<size_t>(16, cap * 3);
    const size_t phaseCap = std::max<size_t>(16, cap * 2);
    const size_t timeLimit = std::min<size_t>(8, eventTimes.size());
    const size_t bayLimit = std::min<size_t>(8, pb.m);
    size_t probes = 0, phases = 0;
    for (size_t timePhase = 0; timePhase < timeLimit && Clock::now() < localDeadline; ++timePhase) {
        const int entry = eventTimes[(timePhase * 7) % eventTimes.size()];
        for (size_t bayPhase = 0; bayPhase < bayLimit && Clock::now() < localDeadline; ++bayPhase) {
            const int bay = (int)((bayPhase * 5 + block) % pb.m);
            const int orientations = (int)pb.geo[block].orients.size();
            for (size_t oriPhase = 0; oriPhase < std::min<size_t>(4, orientations); ++oriPhase) {
                if (phases++ >= phaseCap) goto done;
                const int ori = (int)((oriPhase * 3 + block) % orientations);
                const auto& g = pb.geo[block].orients[ori];
                const int x0 = (int)std::ceil(-g.bb.x0), x1 = (int)std::floor(pb.bayW[bay] - g.bb.x1);
                const int y0 = (int)std::ceil(-g.bb.y0), y1 = (int)std::floor(pb.bayH[bay] - g.bb.y1);
                if (x1 < x0 || y1 < y0) continue;
                std::vector<int> xs{x0, x1}, ys{y0, y1};
                size_t residents = 0;
                for (int k : s.byBay[bay]) {
                    if (residents++ >= 8) break;
                    if (!s.placed[k] || !(s.e[k] < entry + pb.P[block] && entry < s.f[k])) continue;
                    const auto& other = pb.geo[k].orients[s.ori[k]];
                    const int right = (int)std::ceil(other.bb.x1 + s.x[k] - g.bb.x0);
                    const int left = (int)std::floor(other.bb.x0 + s.x[k] - (g.bb.x1 - g.bb.x0) - g.bb.x0);
                    const int above = (int)std::ceil(other.bb.y1 + s.y[k] - g.bb.y0);
                    const int below = (int)std::floor(other.bb.y0 + s.y[k] - (g.bb.y1 - g.bb.y0) - g.bb.y0);
                    if (right >= x0 && right <= x1) xs.push_back(right);
                    if (left >= x0 && left <= x1) xs.push_back(left);
                    if (above >= y0 && above <= y1) ys.push_back(above);
                    if (below >= y0 && below <= y1) ys.push_back(below);
                }
                std::sort(xs.begin(), xs.end()); xs.erase(std::unique(xs.begin(), xs.end()), xs.end());
                std::sort(ys.begin(), ys.end()); ys.erase(std::unique(ys.begin(), ys.end()), ys.end());
                for (int y : ys) for (int x : xs) {
                    if (probes++ >= probeCap || ranked.size() >= cap || Clock::now() >= localDeadline) goto done;
                    appendIfExact({block, bay, ori, x, y, entry}, false);
                }
            }
        }
    }
done:
    std::sort(ranked.begin(), ranked.end(), [](const Ranked& a, const Ranked& b) {
        if (a.replay != b.replay) return a.replay;
        if (a.objective != b.objective) return a.objective < b.objective;
        return actionKey(a.action) < actionKey(b.action);
    });
    std::vector<Action> out;
    out.reserve(ranked.size());
    for (const Ranked& candidate : ranked) {
        const uint64_t key = actionKey(candidate.action);
        if (std::none_of(out.begin(), out.end(), [&](const Action& action) { return actionKey(action) == key; }))
            out.push_back(candidate.action);
    }
    return out;
}

inline std::vector<int> coalition(const core::Problem& pb, const core::Sol& s, int width, uint64_t rotation) {
    std::vector<std::pair<double, int>> ranked;
    for (int i = 0; i < pb.n; ++i) {
        int overlap = 0;
        for (int k = 0; k < pb.n; ++k) if (k != i && s.bay[k] == s.bay[i] && s.e[k] < s.f[i] && s.e[i] < s.f[k]) ++overlap;
        ranked.push_back({-1000.0 * std::max(0, s.f[i] - pb.D[i]) - 15.0 * overlap, i});
    }
    std::sort(ranked.begin(), ranked.end());
    if (!ranked.empty()) std::rotate(ranked.begin(), ranked.begin() + rotation % ranked.size(), ranked.end());
    std::vector<int> out;
    for (int q = 0; q < width && q < (int)ranked.size(); ++q) out.push_back(ranked[q].second);
    std::sort(out.begin(), out.end());
    return out;
}

inline bool rollout(const core::Problem& pb, const Node& from, const core::Sol& guide, int horizon,
                    Clock::time_point deadline, int policy, uint64_t seed, size_t candidateCap,
                    core::Sol& candidate) {
    candidate = from.sol;
    occ::OccIndex oc; rebuild(pb, candidate, horizon, oc);
    repair::Ctx ctx{&pb, &candidate, &oc, std::mt19937(static_cast<uint32_t>(seed)), horizon};
    ctx.exactEvents = true; ctx.hasDeadline = true; ctx.deadline = deadline;
    std::vector<int> left = from.remaining;
    while (!left.empty() && Clock::now() < deadline) {
        const int block = chooseBlock(pb, candidate, left);
        bool inserted = guide.placed[block] &&
            repair::tryPlacement(ctx, block, guide.bay[block], guide.ori[block],
                                 guide.x[block], guide.y[block], guide.e[block]);
        if (!inserted && policy == 0) inserted = repair::insertGreedy(ctx, block);
        else if (!inserted && policy == 1) {
            repair_scored::Config c; c.topK = 10; c.maxCandidates = std::max(1, (int)candidateCap); c.maxTimes = 24; c.contactAxisCap = 48;
            inserted = repair_scored::insertBest(ctx, block, c, nullptr);
        } else if (!inserted) {
            nfp_contact::Config c; c.maxEntryTimes = 20; c.maxCandidates = std::max<size_t>(1, candidateCap); c.maxContactsPerResident = 40; c.includeGeneral = true;
            inserted = nfp_contact::insert(ctx, block, c, nullptr);
        }
        if (!inserted) return false;
        left.erase(std::remove(left.begin(), left.end(), block), left.end());
    }
    return left.empty();
}

inline size_t estimatedBytes(const std::vector<Node>& nodes, const std::unordered_map<std::string, double>& seen) {
    size_t bytes = nodes.capacity() * sizeof(Node);
    for (const Node& n : nodes) bytes += n.remaining.capacity() * sizeof(int) + n.actions.capacity() * sizeof(Action) + n.children.capacity() * sizeof(int);
    for (const auto& kv : seen) bytes += kv.first.capacity() + sizeof(kv);
    return bytes;
}

} // namespace detail

template<class EngineT>
Result run(EngineT& engine, double seconds, uint64_t seed, Limits limits = {}) {
    using Clock = std::chrono::steady_clock;
    Result out;
    const core::Problem& pb = engine.pb;
    const int horizon = engine.horizon_;
    const core::Sol saved = engine.sol;
    core::Sol incumbent = saved;
    if (seconds <= 0.0 || pb.n < 2 || pb.m == 0 || horizon <= 0 || !detail::replayComplete(pb, incumbent, horizon)) return out;
    out.initialObjective = out.bestObjective = core::evaluate(pb, incumbent).total;
    const auto deadline = Clock::now() + std::chrono::duration_cast<Clock::duration>(std::chrono::duration<double>(seconds));
    std::mt19937_64 rng(seed);
    const int maximumCoalition = std::min(pb.n - 1, std::max(limits.coalitionMin, limits.coalitionMax));
    int width = std::min(maximumCoalition, std::max(1, limits.coalitionMin));
    uint64_t rotation = 0;

    while (Clock::now() < deadline && width > 0) {
        core::Sol root = incumbent;
        const core::Sol guide = incumbent;
        std::vector<int> removed = detail::coalition(pb, root, width, rotation++);
        for (int i : removed) root.placed[i] = 0;
        root.rebuildIdx(pb.m);
        std::vector<detail::Node> nodes;
        nodes.push_back({std::move(root), std::move(removed)});
        std::unordered_map<std::string, double> transpositions;
        transpositions.emplace(detail::stateKey(pb, nodes.front().sol), out.bestObjective);
        bool completedThisCoalition = false;

        while (Clock::now() < deadline && nodes.size() < limits.nodeCap) {
            if (detail::estimatedBytes(nodes, transpositions) >= limits.memoryCapBytes) { ++out.memoryCapHits; break; }
            if (nodes.front().exhausted) break;
            int current = 0;
            std::vector<int> path{0};
            while (nodes[current].generated) {
                detail::Node& parent = nodes[current];
                const size_t widening = 1u + (size_t)std::sqrt((double)parent.visits + 1.0);
                if (parent.nextAction < parent.actions.size() && parent.children.size() < widening) break;
                int chosen = -1; double chosenScore = -std::numeric_limits<double>::infinity();
                const double parentLog = std::log(static_cast<double>(parent.visits) + 2.0);
                for (int childIndex : parent.children) {
                    const detail::Node& child = nodes[childIndex];
                    if (child.exhausted) continue;
                    const double visits = static_cast<double>(child.visits);
                    const double mean = visits > 0.0 ? child.rewardSum / visits : 0.0;
                    const double variance = visits > 1.0 ? std::max(0.0, child.rewardSqSum / visits - mean * mean) : 1.0;
                    const double score = mean + limits.exploration * std::sqrt(parentLog / (visits + 1.0)) +
                                         limits.uncertainty * std::sqrt(variance / (visits + 1.0));
                    if (chosen < 0 || score > chosenScore || (score == chosenScore && childIndex < chosen)) {
                        chosen = childIndex; chosenScore = score;
                    }
                }
                if (chosen < 0) {
                    if (parent.nextAction >= parent.actions.size()) parent.exhausted = true;
                    break;
                }
                current = chosen; path.push_back(current);
            }
            detail::Node& leaf = nodes[current];
            if (leaf.exhausted || leaf.remaining.empty()) {
                leaf.exhausted = true;
                ++out.deadEnds;
                for (int p : path) ++nodes[p].visits;
                continue;
            }
            if (!leaf.generated) {
                leaf.actions = detail::exactActions(pb, leaf.sol,
                    detail::chooseBlock(pb, leaf.sol, leaf.remaining), guide,
                    horizon, deadline, std::max<size_t>(1, limits.actionCap));
                leaf.generated = true;
            }
            const size_t widening = 1u + (size_t)std::sqrt((double)leaf.visits + 1.0);
            if (leaf.nextAction >= leaf.actions.size() || leaf.children.size() >= widening) {
                if (leaf.nextAction >= leaf.actions.size()) leaf.exhausted = true;
                ++out.deadEnds;
                ++leaf.visits;
                continue;
            }
            const detail::Action action = leaf.actions[leaf.nextAction++];
            core::Sol childSol;
            if (!detail::apply(pb, leaf.sol, action, horizon, deadline, childSol)) continue;
            std::vector<int> remaining = leaf.remaining;
            remaining.erase(std::remove(remaining.begin(), remaining.end(), action.block), remaining.end());
            const std::string key = detail::stateKey(pb, childSol);
            if (!transpositions.emplace(key, out.bestObjective).second) { ++out.transpositionHits; continue; }
            nodes.push_back({std::move(childSol), std::move(remaining)});
            const int child = (int)nodes.size() - 1;
            nodes[current].children.push_back(child);
            ++out.expandedNodes; ++out.expandedActions;

            double reward = 0.0;
            for (int r = 0; r < std::max(1, limits.rolloutCount) && Clock::now() < deadline; ++r) {
                core::Sol candidate;
                const int policy = r % 3;
                if (!detail::rollout(pb, nodes[child], guide, horizon, deadline, policy, rng(), limits.rolloutActionCap, candidate)) continue;
                ++out.completeRollouts; ++out.exactReplays;
                if (!detail::replayComplete(pb, candidate, horizon)) continue;
                ++out.feasibleCandidates; completedThisCoalition = true;
                const double value = core::evaluate(pb, candidate).total;
                const double gain = std::max(0.0, out.bestObjective - value);
                reward += gain;
                if (value + 1e-9 < out.bestObjective) {
                    incumbent = std::move(candidate); out.bestObjective = value; out.acceptedGain += gain;
                    ++out.acceptedGains; out.improved = true;
                }
            }
            for (int p : path) { ++nodes[p].visits; nodes[p].rewardSum += reward; nodes[p].rewardSqSum += reward * reward; }
            ++nodes[child].visits; nodes[child].rewardSum += reward; nodes[child].rewardSqSum += reward * reward;
        }
        if (nodes.size() >= limits.nodeCap) out.nodeCapHit = true;
        if (!completedThisCoalition || width >= maximumCoalition) break;
        width = std::min(maximumCoalition, width < 4 ? width + 1 : width + std::max(1, width / 2));
    }
    out.deadlineHit = Clock::now() >= deadline;
    if (out.improved) { engine.sol = incumbent; engine.buildOcc(horizon); }
    return out;
}

} // namespace kernel_mcts_coalition

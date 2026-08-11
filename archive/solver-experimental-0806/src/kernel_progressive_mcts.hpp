// kernel_progressive_mcts.hpp -- incumbent-window MCTS with exact placement actions.
#pragma once

#include "alns.hpp"
#include "repair.hpp"
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <limits>
#include <random>
#include <unordered_map>
#include <utility>
#include <vector>

namespace kernel_progressive_mcts {

struct Result {
    uint64_t treeNodes = 0;
    uint64_t expandedActions = 0;
    uint64_t rollouts = 0;
    uint64_t completedBoards = 0;
    uint64_t lowerBoundPrunes = 0;
    double initialObjective = std::numeric_limits<double>::infinity();
    double bestObjective = std::numeric_limits<double>::infinity();
    bool improved = false;
    bool deadlineHit = false;
};

namespace detail {

struct Action {
    int block = -1, bay = -1, ori = -1, x = 0, y = 0, entry = 0;
    uint64_t key = 0;
};

struct Learned {
    double reward = 0.0;
    uint32_t uses = 0;
};

struct Node {
    core::Sol sol;
    std::vector<int> remaining;
    std::vector<Action> actions;
    std::vector<int> children;
    uint64_t hash = 0;
    double lowerBound = 0.0;
    double totalReward = 0.0;
    uint32_t visits = 0;
    bool generated = false;
};

inline uint64_t mix(uint64_t h, uint64_t x) {
    x += 0x9e3779b97f4a7c15ULL;
    x = (x ^ (x >> 30)) * 0xbf58476d1ce4e5b9ULL;
    x = (x ^ (x >> 27)) * 0x94d049bb133111ebULL;
    return (h ^ (x ^ (x >> 31))) * 0x9e3779b97f4a7c15ULL;
}

inline uint64_t stateHash(const core::Problem& pb, const core::Sol& s) {
    uint64_t h = 0x6a09e667f3bcc909ULL;
    for (int i = 0; i < pb.n; ++i) {
        h = mix(h, (uint64_t)i * 2 + (s.placed[i] ? 1 : 0));
        if (s.placed[i]) {
            h = mix(h, (uint32_t)s.bay[i]); h = mix(h, (uint32_t)s.ori[i]);
            h = mix(h, (uint32_t)s.x[i]); h = mix(h, (uint32_t)s.y[i]);
            h = mix(h, (uint32_t)s.e[i]); h = mix(h, (uint32_t)s.f[i]);
        }
    }
    return h;
}

inline uint64_t actionKey(const Action& a) {
    uint64_t h = 0;
    h = mix(h, (uint32_t)a.block); h = mix(h, (uint32_t)a.bay);
    h = mix(h, (uint32_t)a.ori); h = mix(h, (uint32_t)a.entry);
    return h;
}

inline void rebuildOcc(const core::Problem& pb, core::Sol& s, int horizon, occ::OccIndex& oc) {
    s.rebuildIdx(pb.m);
    oc.build(pb, horizon);
    for (int i = 0; i < pb.n; ++i) if (s.placed[i]) oc.stamp(pb, s, i, true);
}

inline bool completeAndAudited(const core::Problem& pb, core::Sol& s, int horizon) {
    if ((int)s.placed.size() != pb.n) return false;
    for (int i = 0; i < pb.n; ++i)
        if (!s.placed[i] || s.bay[i] < 0 || s.bay[i] >= pb.m ||
            s.ori[i] < 0 || s.ori[i] >= (int)pb.geo[i].orients.size() ||
            s.e[i] < pb.R[i] || s.f[i] != s.e[i] + pb.P[i] || s.f[i] > horizon)
            return false;
    s.rebuildIdx(pb.m);
    return core::stateExactEvents(pb, s) && alns::audit(pb, s, nullptr, true) == 0;
}

inline double minPreferenceLoss(const core::Problem& pb, int i) {
    double v = std::numeric_limits<double>::infinity();
    for (int j = 0; j < pb.m; ++j) v = std::min(v, pb.Smax[i] - pb.pref[i][j]);
    return std::isfinite(v) ? v : 0.0;
}

// This is admissible: it charges frozen/placed tardiness and preference loss,
// and relaxes all temporal and geometric constraints of unplaced blocks.
inline double lowerBound(const core::Problem& pb, const core::Sol& s) {
    double z1 = 0.0, z3 = 0.0;
    for (int i = 0; i < pb.n; ++i) {
        if (s.placed[i]) {
            z1 += std::max(0, s.f[i] - pb.D[i]);
            z3 += pb.Smax[i] - pb.pref[i][s.bay[i]];
        } else {
            z1 += std::max(0, pb.R[i] + pb.P[i] - pb.D[i]);
            z3 += minPreferenceLoss(pb, i);
        }
    }
    return pb.w1 * z1 + pb.w3 * z3;
}

inline int chooseBlock(const core::Problem& pb, const core::Sol& s,
                       const std::vector<int>& remaining) {
    int pick = -1;
    double best = -std::numeric_limits<double>::infinity();
    for (int i : remaining) {
        int blockers = 0;
        for (int k = 0; k < pb.n; ++k) if (s.placed[k] &&
            s.e[k] < pb.D[i] + pb.P[i] && pb.R[i] < s.f[k]) ++blockers;
        const int slack = pb.D[i] - pb.R[i] - pb.P[i];
        const int incumbentTardy = s.placed[i] ? std::max(0, s.f[i] - pb.D[i]) : 0;
        const double score = 1000.0 * std::max(0, -slack) + 160.0 * incumbentTardy +
                             12.0 * blockers - 0.1 * slack - i * 1e-6;
        if (score > best) { best = score; pick = i; }
    }
    return pick;
}

inline std::vector<int> eventTimes(const core::Problem& pb, const core::Sol& s,
                                   int i, int horizon) {
    std::vector<int> out{pb.R[i], std::max(pb.R[i], pb.D[i] - pb.P[i])};
    for (int k = 0; k < pb.n; ++k) if (s.placed[k]) {
        out.push_back(s.e[k]); out.push_back(s.f[k]);
    }
    const int last = horizon - pb.P[i];
    if (last >= pb.R[i]) out.push_back(last);
    std::sort(out.begin(), out.end());
    out.erase(std::unique(out.begin(), out.end()), out.end());
    out.erase(std::remove_if(out.begin(), out.end(), [&](int e) {
        return e < pb.R[i] || e + pb.P[i] > horizon;
    }), out.end());
    return out;
}

// Every emitted action is checker-valid against the exact partial state.  The
// candidate set combines wall/resident corners with polygon vertex contacts.
inline std::vector<Action> exactActions(const core::Problem& pb, const core::Sol& state,
                                        int block, int horizon,
                                        std::chrono::steady_clock::time_point deadline,
                                        size_t cap) {
    std::vector<Action> out;
    const std::vector<int> times = eventTimes(pb, state, block, horizon);
    for (int e : times) {
        if (std::chrono::steady_clock::now() >= deadline) break;
        for (int j = 0; j < pb.m; ++j) {
            for (int oi = 0; oi < (int)pb.geo[block].orients.size(); ++oi) {
                std::vector<int> xs, ys;
                core::Sol basis = state;
                occ::OccIndex oc;
                rebuildOcc(pb, basis, horizon, oc);
                repair::Ctx ctx{&pb, &basis, &oc, std::mt19937(0), horizon};
                ctx.exactEvents = true; ctx.hasDeadline = true; ctx.deadline = deadline;
                repair::cornerSets(ctx, block, oi, j, e, e + pb.P[block], xs, ys);
                repair::shapeContactSets(ctx, block, oi, j, e, e + pb.P[block], xs, ys, 72);
                for (int y : ys) for (int x : xs) {
                    if (std::chrono::steady_clock::now() >= deadline) return out;
                    core::Sol trial = state;
                    occ::OccIndex toc;
                    rebuildOcc(pb, trial, horizon, toc);
                    repair::Ctx tctx{&pb, &trial, &toc, std::mt19937(0), horizon};
                    tctx.exactEvents = true; tctx.hasDeadline = true; tctx.deadline = deadline;
                    if (!repair::tryPlacement(tctx, block, j, oi, x, y, e)) continue;
                    Action a{block, j, oi, x, y, e, 0};
                    a.key = actionKey(a);
                    out.push_back(a);
                    if (out.size() >= cap) return out;
                }
            }
        }
    }
    return out;
}

inline bool applyAction(const core::Problem& pb, const core::Sol& parent, const Action& a,
                        int horizon, std::chrono::steady_clock::time_point deadline,
                        core::Sol& child) {
    child = parent;
    occ::OccIndex oc;
    rebuildOcc(pb, child, horizon, oc);
    repair::Ctx ctx{&pb, &child, &oc, std::mt19937(0), horizon};
    ctx.exactEvents = true; ctx.hasDeadline = true; ctx.deadline = deadline;
    return repair::tryPlacement(ctx, a.block, a.bay, a.ori, a.x, a.y, a.entry);
}

inline std::vector<int> coalition(const core::Problem& pb, const core::Sol& s,
                                  size_t width, size_t rotation) {
    std::vector<std::pair<double, int>> score;
    score.reserve(pb.n);
    for (int i = 0; i < pb.n; ++i) {
        int blockers = 0;
        for (int k = 0; k < pb.n; ++k) if (k != i && s.bay[i] == s.bay[k] &&
            s.e[i] < s.f[k] && s.e[k] < s.f[i]) ++blockers;
        score.push_back({-1000.0 * std::max(0, s.f[i] - pb.D[i]) - 14.0 * blockers, i});
    }
    std::sort(score.begin(), score.end());
    if (!score.empty()) std::rotate(score.begin(), score.begin() + rotation % score.size(), score.end());
    std::vector<int> out;
    for (size_t q = 0; q < width && q < score.size(); ++q) out.push_back(score[q].second);
    std::sort(out.begin(), out.end());
    return out;
}

} // namespace detail

template<class EngineT>
Result run(EngineT& engine, double seconds, uint64_t seed) {
    using Clock = std::chrono::steady_clock;
    Result result;
    const core::Problem& pb = engine.pb;
    const int horizon = engine.horizon_;
    core::Sol incumbent = engine.sol;
    if (seconds <= 0.0 || pb.n < 2 || horizon <= 0 ||
        !detail::completeAndAudited(pb, incumbent, horizon)) return result;
    result.initialObjective = core::evaluate(pb, incumbent).total;
    result.bestObjective = result.initialObjective;
    const auto deadline = Clock::now() + std::chrono::duration_cast<Clock::duration>(
        std::chrono::duration<double>(seconds));
    auto expired = [&] { return Clock::now() >= deadline; };
    std::mt19937_64 rng(seed);
    std::unordered_map<uint64_t, detail::Learned> learned;
    std::unordered_map<uint64_t, double> transposition;
    const size_t maxWindow = (size_t)pb.n - 1;
    size_t width = std::min(maxWindow, std::max<size_t>(3, (size_t)pb.n / 45 + 2));
    size_t rotation = 0;

    // A wider exterior is never thawed until this width has yielded at least one
    // independently audited completion.  Thus every root has a realizable frozen exterior.
    while (!expired() && width > 0) {
        const std::vector<int> removed = detail::coalition(pb, incumbent, width, rotation++);
        core::Sol root = incumbent;
        for (int i : removed) root.placed[i] = 0;
        root.rebuildIdx(pb.m);
        std::vector<detail::Node> nodes;
        nodes.push_back({std::move(root), removed, {}, {}, 0, 0.0, 0.0, 0, false});
        nodes[0].hash = detail::stateHash(pb, nodes[0].sol);
        nodes[0].lowerBound = detail::lowerBound(pb, nodes[0].sol);
        transposition[nodes[0].hash] = nodes[0].lowerBound;
        ++result.treeNodes;
        bool auditedCompletion = false;

        while (!expired() && nodes.size() < 384) {
            int nodeIndex = 0;
            std::vector<int> path{0};
            // UCT selection; no virtual loss is applied because rollouts are batched
            // deterministically and execute serially against immutable node snapshots.
            for (;;) {
                detail::Node& node = nodes[nodeIndex];
                if (node.lowerBound >= result.bestObjective) {
                    ++result.lowerBoundPrunes;
                    nodeIndex = -1;
                    break;
                }
                if (node.remaining.empty() || !node.generated) break;
                const size_t widen = 1 + (size_t)std::sqrt((double)node.visits + 1.0);
                if (node.children.size() < std::min(widen, node.actions.size())) break;
                if (node.children.empty()) break;
                int chosen = node.children.front();
                double chosenScore = -std::numeric_limits<double>::infinity();
                for (int c : node.children) {
                    const detail::Node& child = nodes[c];
                    const double mean = child.visits ? child.totalReward / child.visits : 0.0;
                    const double uct = mean + 1.35 * std::sqrt(std::log((double)node.visits + 2.0) /
                                                                 ((double)child.visits + 1.0));
                    if (uct > chosenScore || (uct == chosenScore && c < chosen)) {
                        chosenScore = uct; chosen = c;
                    }
                }
                nodeIndex = chosen; path.push_back(nodeIndex);
            }
            if (nodeIndex < 0) { ++nodes[0].visits; continue; }
            detail::Node& leaf = nodes[nodeIndex];
            if (leaf.remaining.empty()) {
                core::Sol candidate = leaf.sol;
                if (detail::completeAndAudited(pb, candidate, horizon)) {
                    ++result.completedBoards; auditedCompletion = true;
                    const double value = core::evaluate(pb, candidate).total;
                    if (value < result.bestObjective) {
                        incumbent = std::move(candidate); result.bestObjective = value; result.improved = true;
                    }
                }
                for (int p : path) { ++nodes[p].visits; nodes[p].totalReward += 0.0; }
                continue;
            }
            if (!leaf.generated) {
                const int block = detail::chooseBlock(pb, leaf.sol, leaf.remaining);
                leaf.actions = detail::exactActions(pb, leaf.sol, block, horizon, deadline, 96);
                std::sort(leaf.actions.begin(), leaf.actions.end(), [&](const detail::Action& a, const detail::Action& b) {
                    const double la = learned[a.key].uses ? learned[a.key].reward / learned[a.key].uses : 0.0;
                    const double lb = learned[b.key].uses ? learned[b.key].reward / learned[b.key].uses : 0.0;
                    if (la != lb) return la > lb;
                    return a.key < b.key;
                });
                leaf.generated = true;
            }
            const size_t widen = 1 + (size_t)std::sqrt((double)leaf.visits + 1.0);
            if (leaf.actions.empty() || leaf.children.size() >= std::min(widen, leaf.actions.size())) {
                for (int p : path) ++nodes[p].visits;
                continue;
            }
            const detail::Action action = leaf.actions[leaf.children.size()];
            core::Sol childSol;
            if (!detail::applyAction(pb, leaf.sol, action, horizon, deadline, childSol)) continue;
            std::vector<int> remain = leaf.remaining;
            remain.erase(std::remove(remain.begin(), remain.end(), action.block), remain.end());
            const uint64_t h = detail::stateHash(pb, childSol);
            const double bound = detail::lowerBound(pb, childSol);
            const auto known = transposition.find(h);
            if (known != transposition.end() && known->second <= bound) continue;
            transposition[h] = bound;
            nodes.push_back({std::move(childSol), std::move(remain), {}, {}, h, bound, 0.0, 0, false});
            const int childIndex = (int)nodes.size() - 1;
            nodes[nodeIndex].children.push_back(childIndex);
            ++result.treeNodes; ++result.expandedActions;

            // Deterministic batch of four independent rollouts.  A rollout is a
            // limited-discrepancy continuation: learned action rewards rank exact
            // actions, with a hash-derived offset retaining alternative branches.
            double batchReward = 0.0;
            for (int batch = 0; batch < 4 && !expired(); ++batch) {
                core::Sol trial = nodes[childIndex].sol;
                std::vector<int> left = nodes[childIndex].remaining;
                std::vector<uint64_t> rolloutKeys;
                bool complete = true;
                while (!left.empty() && !expired()) {
                    const int b = detail::chooseBlock(pb, trial, left);
                    std::vector<detail::Action> acts = detail::exactActions(pb, trial, b, horizon, deadline, 40);
                    if (acts.empty()) { complete = false; break; }
                    std::sort(acts.begin(), acts.end(), [&](const detail::Action& a, const detail::Action& z) {
                        const detail::Learned& la = learned[a.key]; const detail::Learned& lz = learned[z.key];
                        const double va = la.uses ? la.reward / la.uses : 0.0;
                        const double vz = lz.uses ? lz.reward / lz.uses : 0.0;
                        return va != vz ? va > vz : a.key < z.key;
                    });
                    const size_t pick = (size_t)(detail::mix(nodes[childIndex].hash, (uint64_t)batch + left.size()) %
                                                 std::min<size_t>(acts.size(), 1 + batch));
                    core::Sol next;
                    if (!detail::applyAction(pb, trial, acts[pick], horizon, deadline, next)) { complete = false; break; }
                    rolloutKeys.push_back(acts[pick].key);
                    trial = std::move(next);
                    left.erase(std::remove(left.begin(), left.end(), b), left.end());
                }
                ++result.rollouts;
                if (!complete || !detail::completeAndAudited(pb, trial, horizon)) continue;
                ++result.completedBoards; auditedCompletion = true;
                const double value = core::evaluate(pb, trial).total;
                const double reward = std::max(0.0, result.bestObjective - value);
                batchReward += reward;
                for (uint64_t key : rolloutKeys) {
                    detail::Learned& stat = learned[key];
                    stat.reward += reward;
                    ++stat.uses;
                }
                if (value < result.bestObjective) {
                    incumbent = std::move(trial); result.bestObjective = value; result.improved = true;
                }
            }
            for (int p : path) { ++nodes[p].visits; nodes[p].totalReward += batchReward; }
            ++nodes[childIndex].visits; nodes[childIndex].totalReward += batchReward;
        }
        if (!auditedCompletion) break;
        if (width >= maxWindow) break;
        width = std::min(maxWindow, width + std::max<size_t>(1, width / 2));
    }
    result.deadlineHit = expired();
    if (result.improved) engine.sol = incumbent; // strict, audited complete improvement only
    return result;
}

} // namespace kernel_progressive_mcts

#pragma once

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <functional>
#include <numeric>
#include <tuple>
#include <utility>
#include <vector>

namespace structural::m2 {

struct Placement {
    int block = -1;
    int x = 0;
    int y = 0;
    int orientation = 0;
    int entry = 0;
    int exit = 0;

    auto key() const { return std::tie(x, y, orientation, entry, exit, block); }
    bool operator==(const Placement& other) const {
        return block == other.block && x == other.x && y == other.y &&
               orientation == other.orientation && entry == other.entry &&
               exit == other.exit;
    }
};

struct BayState {
    std::vector<Placement> placements;
    std::vector<int> certifiedBackbone;
};

struct Request {
    std::uint32_t abiVersion = 1;
    std::uint64_t seed = 0;
    std::size_t nodeCap = 1;
    int mutableStartPercent = 20;
    int mutableMaxPercent = 60;
};

enum class Outcome { COMPLETE, CAPPED, INVALID, NO_IMPROVEMENT, IMPROVED };

struct Counters {
    std::size_t recursiveNodes = 0;
    std::size_t completeHalves = 0;
    std::size_t completeMerges = 0;
    std::size_t dynamicCoordinates = 0;
    std::size_t mutableBlocks = 0;
    std::size_t caps = 0;
    std::size_t rollbacks = 0;
};

struct Result {
    Outcome outcome = Outcome::INVALID;
    long long initialObjective = 0;
    long long bestObjective = 0;
    BayState state;
    Counters counters;
};

using CandidateProvider = std::function<std::vector<Placement>(
    int, const std::vector<Placement>&, const std::vector<Placement>&)>;
using ExactFeasible = std::function<bool(const std::vector<Placement>&)>;
using Objective = std::function<long long(const std::vector<Placement>&)>;

inline bool contains(const std::vector<int>& ids, int id) {
    return std::find(ids.begin(), ids.end(), id) != ids.end();
}

inline bool validate(const BayState& state, const Request& request) {
    if (request.abiVersion != 1 || request.nodeCap == 0 ||
        request.mutableStartPercent != 20 || request.mutableMaxPercent != 60 ||
        state.placements.size() < 50 || state.placements.size() > 83)
        return false;
    std::vector<int> ids;
    for (const Placement& p : state.placements) {
        if (p.block < 0 || p.exit < p.entry) return false;
        ids.push_back(p.block);
    }
    std::sort(ids.begin(), ids.end());
    if (std::adjacent_find(ids.begin(), ids.end()) != ids.end()) return false;
    for (int id : state.certifiedBackbone)
        if (!std::binary_search(ids.begin(), ids.end(), id)) return false;
    return true;
}

inline std::vector<int> pressureOrder(const BayState& state) {
    std::vector<int> mutableIds;
    for (const Placement& p : state.placements)
        if (!contains(state.certifiedBackbone, p.block)) mutableIds.push_back(p.block);
    std::sort(mutableIds.begin(), mutableIds.end(), [&](int a, int b) {
        const Placement* pa = nullptr;
        const Placement* pb = nullptr;
        for (const Placement& p : state.placements) {
            if (p.block == a) pa = &p;
            if (p.block == b) pb = &p;
        }
        const int spanA = pa->exit - pa->entry;
        const int spanB = pb->exit - pb->entry;
        return std::tie(spanA, pa->y, pa->x, a) >
               std::tie(spanB, pb->y, pb->x, b);
    });
    return mutableIds;
}

inline Result run(const BayState& incumbent, const Request& request,
                  const CandidateProvider& dynamicCandidates,
                  const ExactFeasible& exactFeasible, const Objective& objective) {
    Result out;
    if (!dynamicCandidates || !exactFeasible || !objective ||
        !validate(incumbent, request))
        return out;
    out.state = incumbent;
    out.initialObjective = objective(incumbent.placements);
    out.bestObjective = out.initialObjective;
    out.outcome = Outcome::COMPLETE;

    std::vector<int> ordered = pressureOrder(incumbent);
    const std::size_t start = std::max<std::size_t>(1,
        incumbent.placements.size() * (std::size_t)request.mutableStartPercent / 100);
    const std::size_t hardMax = incumbent.placements.size() *
        (std::size_t)request.mutableMaxPercent / 100;
    if (ordered.size() < start || start > hardMax) {
        out.outcome = Outcome::NO_IMPROVEMENT;
        ++out.counters.rollbacks;
        return out;
    }
    ordered.resize(start);
    out.counters.mutableBlocks = ordered.size();

    std::vector<Placement> fixed;
    for (const Placement& p : incumbent.placements)
        if (std::find(ordered.begin(), ordered.end(), p.block) == ordered.end())
            fixed.push_back(p);

    const std::size_t split = ordered.size() / 2;
    std::vector<Placement> left, right;
    auto formHalf = [&](std::size_t lo, std::size_t hi,
                        std::vector<Placement>& half) -> bool {
        for (std::size_t q = lo; q < hi; ++q) {
            if (out.counters.recursiveNodes >= request.nodeCap) return false;
            ++out.counters.recursiveNodes;
            std::vector<Placement> formed = fixed;
            formed.insert(formed.end(), half.begin(), half.end());
            std::vector<Placement> other = (&half == &left) ? right : left;
            std::vector<Placement> choices = dynamicCandidates(ordered[q], formed, other);
            out.counters.dynamicCoordinates += choices.size();
            std::sort(choices.begin(), choices.end(),
                      [](const Placement& a, const Placement& b) { return a.key() < b.key(); });
            if (choices.empty()) return false;
            const std::size_t at = (request.seed + q) % choices.size();
            half.push_back(choices[at]);
        }
        ++out.counters.completeHalves;
        return true;
    };

    if (!formHalf(0, split, left) || !formHalf(split, ordered.size(), right)) {
        out.outcome = out.counters.recursiveNodes >= request.nodeCap ?
            Outcome::CAPPED : Outcome::NO_IMPROVEMENT;
        out.counters.caps += out.outcome == Outcome::CAPPED;
        ++out.counters.rollbacks;
        return out;
    }

    std::vector<Placement> merged = fixed;
    merged.insert(merged.end(), left.begin(), left.end());
    merged.insert(merged.end(), right.begin(), right.end());
    if (!exactFeasible(merged)) {
        out.outcome = Outcome::NO_IMPROVEMENT;
        ++out.counters.rollbacks;
        return out;
    }
    ++out.counters.completeMerges;
    const long long value = objective(merged);
    if (value < out.initialObjective) {
        out.outcome = Outcome::IMPROVED;
        out.bestObjective = value;
        out.state.placements = std::move(merged);
    } else {
        out.outcome = Outcome::NO_IMPROVEMENT;
        ++out.counters.rollbacks;
    }
    return out;
}

}  // namespace structural::m2

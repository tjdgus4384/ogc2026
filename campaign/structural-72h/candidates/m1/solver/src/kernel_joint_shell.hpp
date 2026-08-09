#pragma once

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <functional>
#include <limits>
#include <stdexcept>
#include <tuple>
#include <utility>
#include <vector>

namespace structural::m1 {

struct Placement {
    int block = -1;
    int bay = -1;
    int orientation = 0;
    int x = 0;
    int y = 0;
    int entry = 0;
    int exit = 0;
    int operationOrder = 0;

    auto key() const {
        return std::tie(bay, orientation, x, y, entry, exit, operationOrder, block);
    }
    bool operator==(const Placement& other) const {
        return block == other.block && bay == other.bay &&
               orientation == other.orientation && x == other.x && y == other.y &&
               entry == other.entry && exit == other.exit &&
               operationOrder == other.operationOrder;
    }
};

struct BlockDomain {
    int block = -1;
    Placement incumbent;
    std::vector<Placement> liveBoundaryCandidates;
};

struct Request {
    std::uint32_t abiVersion = 1;
    std::uint64_t seed = 0;
    std::size_t minCohort = 6;
    std::size_t maxCohort = 20;
    std::size_t nodeCap = 1;
};

enum class Outcome { COMPLETE, CAPPED, INVALID, NO_IMPROVEMENT, IMPROVED };

struct Counters {
    std::size_t shellNodes = 0;
    std::size_t completeBoards = 0;
    std::size_t gains = 0;
    std::size_t caps = 0;
    std::size_t rollbacks = 0;
    std::size_t coupledDimensions = 0;
};

struct Result {
    Outcome outcome = Outcome::INVALID;
    long long initialObjective = 0;
    long long bestObjective = 0;
    std::vector<Placement> placements;
    Counters counters;
};

using PartialFeasible = std::function<bool(const std::vector<Placement>&)>;
using ExactBoard = std::function<bool(const std::vector<Placement>&)>;
using Objective = std::function<long long(const std::vector<Placement>&)>;

inline std::size_t changedDimensions(const std::vector<BlockDomain>& domains,
                                     const std::vector<Placement>& candidate) {
    bool assignment = false, orientation = false, coordinate = false;
    bool timing = false, operationOrder = false;
    for (std::size_t i = 0; i < domains.size(); ++i) {
        const Placement& a = domains[i].incumbent;
        const Placement& b = candidate[i];
        assignment |= a.bay != b.bay;
        orientation |= a.orientation != b.orientation;
        coordinate |= a.x != b.x || a.y != b.y;
        timing |= a.entry != b.entry || a.exit != b.exit;
        operationOrder |= a.operationOrder != b.operationOrder;
    }
    return assignment + orientation + coordinate + timing + operationOrder;
}

inline bool validDomains(const std::vector<BlockDomain>& domains,
                         const Request& request) {
    if (request.abiVersion != 1 || request.minCohort < 6 ||
        request.maxCohort > 20 || request.minCohort > request.maxCohort ||
        domains.size() < request.minCohort || domains.size() > request.maxCohort ||
        request.nodeCap == 0)
        return false;
    std::vector<int> ids;
    for (const BlockDomain& domain : domains) {
        if (domain.block < 0 || domain.incumbent.block != domain.block ||
            domain.liveBoundaryCandidates.empty())
            return false;
        ids.push_back(domain.block);
        for (const Placement& p : domain.liveBoundaryCandidates)
            if (p.block != domain.block || p.exit < p.entry) return false;
    }
    std::sort(ids.begin(), ids.end());
    return std::adjacent_find(ids.begin(), ids.end()) == ids.end();
}

inline Result run(const std::vector<BlockDomain>& domains, const Request& request,
                  const PartialFeasible& partialFeasible,
                  const ExactBoard& exactBoard, const Objective& objective) {
    Result out;
    if (!partialFeasible || !exactBoard || !objective ||
        !validDomains(domains, request))
        return out;

    out.placements.reserve(domains.size());
    for (const BlockDomain& domain : domains) out.placements.push_back(domain.incumbent);
    out.initialObjective = objective(out.placements);
    out.bestObjective = out.initialObjective;
    out.outcome = Outcome::COMPLETE;

    std::vector<Placement> current, best = out.placements;
    current.reserve(domains.size());
    bool capped = false;
    std::function<void(std::size_t)> visit = [&](std::size_t depth) {
        if (out.counters.shellNodes >= request.nodeCap) {
            capped = true;
            return;
        }
        ++out.counters.shellNodes;
        if (depth == domains.size()) {
            if (!exactBoard(current)) return;
            ++out.counters.completeBoards;
            const long long value = objective(current);
            if (value < out.bestObjective && changedDimensions(domains, current) >= 2) {
                out.bestObjective = value;
                best = current;
                ++out.counters.gains;
            }
            return;
        }
        std::vector<Placement> choices = domains[depth].liveBoundaryCandidates;
        std::sort(choices.begin(), choices.end(),
                  [](const Placement& a, const Placement& b) { return a.key() < b.key(); });
        const std::size_t offset = choices.empty() ? 0 :
            (std::size_t)((request.seed + depth) % choices.size());
        std::rotate(choices.begin(), choices.begin() + offset, choices.end());
        for (const Placement& choice : choices) {
            current.push_back(choice);
            if (partialFeasible(current)) visit(depth + 1);
            current.pop_back();
            if (capped) return;
        }
    };
    visit(0);

    if (capped) {
        out.outcome = Outcome::CAPPED;
        ++out.counters.caps;
        ++out.counters.rollbacks;
        out.bestObjective = out.initialObjective;
        return out;
    }
    if (out.bestObjective < out.initialObjective) {
        out.outcome = Outcome::IMPROVED;
        out.placements = std::move(best);
        out.counters.coupledDimensions = changedDimensions(domains, out.placements);
    } else {
        out.outcome = Outcome::NO_IMPROVEMENT;
        ++out.counters.rollbacks;
    }
    return out;
}

}  // namespace structural::m1

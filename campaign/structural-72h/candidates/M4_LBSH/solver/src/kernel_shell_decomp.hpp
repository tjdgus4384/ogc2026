#pragma once

#include <algorithm>
#include <cstdint>
#include <functional>
#include <numeric>
#include <set>
#include <utility>
#include <vector>

namespace lbsh {

enum class Outcome { IMPROVED, NO_IMPROVEMENT, UNKNOWN, CAPPED, INVALID };
enum class SubproblemStatus {
    FEASIBLE,
    PROVEN_CONTAINMENT,
    PROVEN_PRECEDENCE,
    PROVEN_EXHAUSTIVE_SHELL,
    UNKNOWN
};
enum class CutKind { CONTAINMENT, PRECEDENCE, EXACT_MASTER_SHELL };

struct Box {
    int width = 0;
    int height = 0;
};

struct Master {
    std::vector<int> order;
    std::vector<std::pair<int, int>> x_before;
    std::vector<std::pair<int, int>> y_before;
    std::int64_t exact_objective = 0;
};

struct Request {
    int bay_width = 0;
    int bay_height = 0;
    std::vector<Box> boxes;
    std::vector<Master> masters;
    std::int64_t initial_objective = 0;
    std::uint64_t expansion_cap = 0;
};

struct Cut {
    CutKind kind = CutKind::EXACT_MASTER_SHELL;
    std::size_t master_index = 0;
};

struct Result {
    Outcome outcome = Outcome::INVALID;
    std::vector<int> x;
    std::vector<int> y;
    std::vector<int> order;
    std::vector<Cut> cuts;
    std::int64_t objective = 0;
    std::uint64_t expansions = 0;
};

struct SubproblemResult {
    SubproblemStatus status = SubproblemStatus::UNKNOWN;
    std::vector<int> x;
    std::vector<int> y;
    std::int64_t objective = 0;
    std::uint64_t expansions = 0;
};

inline bool validMaster(const Master& master, std::size_t count) {
    if (master.order.size() != count) return false;
    std::vector<int> sorted = master.order;
    std::sort(sorted.begin(), sorted.end());
    for (std::size_t i = 0; i < count; ++i)
        if (sorted[i] != static_cast<int>(i)) return false;
    auto valid_edges = [count](const auto& edges) {
        for (const auto& edge : edges)
            if (edge.first < 0 || edge.second < 0 || edge.first == edge.second ||
                edge.first >= static_cast<int>(count) ||
                edge.second >= static_cast<int>(count)) return false;
        return true;
    };
    return valid_edges(master.x_before) && valid_edges(master.y_before);
}

inline bool acyclic(std::size_t count,
                    const std::vector<std::pair<int, int>>& edges) {
    std::vector<int> indegree(count, 0);
    std::vector<std::vector<int>> next(count);
    for (const auto& edge : edges) {
        next[edge.first].push_back(edge.second);
        ++indegree[edge.second];
    }
    std::vector<int> ready;
    for (std::size_t i = 0; i < count; ++i)
        if (indegree[i] == 0) ready.push_back(static_cast<int>(i));
    std::size_t visited = 0;
    while (!ready.empty()) {
        const int node = ready.back();
        ready.pop_back();
        ++visited;
        for (int target : next[node])
            if (--indegree[target] == 0) ready.push_back(target);
    }
    return visited == count;
}

inline SubproblemResult solveFreeCoordinates(const Request& request,
                                             const Master& master,
                                             std::uint64_t remaining_cap) {
    SubproblemResult result;
    const std::size_t count = request.boxes.size();
    if (!acyclic(count, master.x_before) || !acyclic(count, master.y_before)) {
        result.status = SubproblemStatus::PROVEN_PRECEDENCE;
        return result;
    }
    for (const Box& box : request.boxes) {
        if (box.width <= 0 || box.height <= 0 || box.width > request.bay_width ||
            box.height > request.bay_height) {
            result.status = SubproblemStatus::PROVEN_CONTAINMENT;
            return result;
        }
    }
    result.x.assign(count, 0);
    result.y.assign(count, 0);
    auto before = [](const auto& edges, int a, int b) {
        return std::find(edges.begin(), edges.end(), std::make_pair(a, b)) != edges.end();
    };
    std::function<bool(std::size_t)> search = [&](std::size_t depth) {
        if (depth == count) return true;
        const int block = master.order[depth];
        const Box& box = request.boxes[block];
        for (int y = 0; y + box.height <= request.bay_height; ++y) {
            for (int x = 0; x + box.width <= request.bay_width; ++x) {
                if (++result.expansions > remaining_cap) return false;
                bool valid = true;
                for (std::size_t prior_depth = 0; prior_depth < depth; ++prior_depth) {
                    const int prior = master.order[prior_depth];
                    const Box& other = request.boxes[prior];
                    if (before(master.x_before, prior, block) &&
                        result.x[prior] + other.width > x) valid = false;
                    if (before(master.x_before, block, prior) &&
                        x + box.width > result.x[prior]) valid = false;
                    if (before(master.y_before, prior, block) &&
                        result.y[prior] + other.height > y) valid = false;
                    if (before(master.y_before, block, prior) &&
                        y + box.height > result.y[prior]) valid = false;
                    const bool overlap = x < result.x[prior] + other.width &&
                        result.x[prior] < x + box.width &&
                        y < result.y[prior] + other.height &&
                        result.y[prior] < y + box.height;
                    if (overlap) valid = false;
                    if (!valid) break;
                }
                if (!valid) continue;
                result.x[block] = x;
                result.y[block] = y;
                if (search(depth + 1)) return true;
                if (result.expansions > remaining_cap) return false;
            }
        }
        return false;
    };
    if (search(0)) {
        result.status = SubproblemStatus::FEASIBLE;
        result.objective = master.exact_objective;
    } else if (result.expansions > remaining_cap) {
        result.status = SubproblemStatus::UNKNOWN;
    } else {
        result.status = SubproblemStatus::PROVEN_EXHAUSTIVE_SHELL;
    }
    return result;
}

inline Result run(const Request& request) {
    Result result;
    result.objective = request.initial_objective;
    if (request.bay_width <= 0 || request.bay_height <= 0 ||
        request.boxes.empty() || request.masters.empty() || request.expansion_cap == 0)
        return result;
    result.outcome = Outcome::NO_IMPROVEMENT;
    bool saw_unknown = false;
    for (std::size_t index = 0; index < request.masters.size(); ++index) {
        const Master& master = request.masters[index];
        if (!validMaster(master, request.boxes.size())) {
            result.outcome = Outcome::INVALID;
            return result;
        }
        if (result.expansions >= request.expansion_cap) {
            result.outcome = Outcome::CAPPED;
            return result;
        }
        SubproblemResult sub = solveFreeCoordinates(
            request, master, request.expansion_cap - result.expansions);
        result.expansions += sub.expansions;
        switch (sub.status) {
            case SubproblemStatus::PROVEN_CONTAINMENT:
                result.cuts.push_back({CutKind::CONTAINMENT, index});
                break;
            case SubproblemStatus::PROVEN_PRECEDENCE:
                result.cuts.push_back({CutKind::PRECEDENCE, index});
                break;
            case SubproblemStatus::PROVEN_EXHAUSTIVE_SHELL:
                result.cuts.push_back({CutKind::EXACT_MASTER_SHELL, index});
                break;
            case SubproblemStatus::UNKNOWN:
                saw_unknown = true;
                break;
            case SubproblemStatus::FEASIBLE:
                if (sub.objective < result.objective) {
                    result.outcome = Outcome::IMPROVED;
                    result.objective = sub.objective;
                    result.x = std::move(sub.x);
                    result.y = std::move(sub.y);
                    result.order = master.order;
                    return result;
                }
                break;
        }
    }
    if (saw_unknown) result.outcome = Outcome::UNKNOWN;
    return result;
}

struct Descriptor {
    static constexpr const char* id = "M4_LBSH";
    static constexpr std::uint32_t abi_version = 1;
};

}  // namespace lbsh

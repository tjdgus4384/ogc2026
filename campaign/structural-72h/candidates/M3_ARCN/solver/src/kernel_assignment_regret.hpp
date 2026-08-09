#pragma once

#include <algorithm>
#include <cstdint>
#include <functional>
#include <limits>
#include <numeric>
#include <set>
#include <utility>
#include <vector>

namespace arcn {

enum class OracleStatus { COMPLETE, INFEASIBLE, UNKNOWN };
enum class Outcome { IMPROVED, NO_IMPROVEMENT, CAPPED, UNKNOWN, INVALID };

struct Reconstruction {
    OracleStatus status = OracleStatus::UNKNOWN;
    std::int64_t z1 = 0;
    std::int64_t objective = 0;
    std::uint64_t state_hash = 0;
};

struct Request {
    std::vector<int> assignment;
    std::vector<std::vector<std::int64_t>> assignment_cost;
    std::int64_t initial_objective = 0;
    std::uint64_t expansion_cap = 0;
    int min_coalition = 3;
    int max_coalition = 12;
};

struct Result {
    Outcome outcome = Outcome::INVALID;
    std::vector<int> assignment;
    std::vector<int> coalition;
    std::vector<int> affected_bays;
    std::int64_t objective = 0;
    std::uint64_t state_hash = 0;
    std::uint64_t expansions = 0;
    std::uint64_t proper_subsets = 0;
};

using Reconstruct = std::function<Reconstruction(
    const std::vector<int>& assignment, const std::vector<int>& affected_bays)>;

inline bool validRequest(const Request& request) {
    if (request.assignment.empty() || request.expansion_cap == 0 ||
        request.min_coalition < 3 || request.max_coalition < request.min_coalition ||
        request.max_coalition > 12 ||
        request.assignment_cost.size() != request.assignment.size()) return false;
    for (std::size_t block = 0; block < request.assignment.size(); ++block) {
        const auto& row = request.assignment_cost[block];
        if (row.size() < 2 || request.assignment[block] < 0 ||
            request.assignment[block] >= static_cast<int>(row.size())) return false;
    }
    return true;
}

inline std::vector<int> regretOrder(const Request& request) {
    std::vector<int> blocks(request.assignment.size());
    std::iota(blocks.begin(), blocks.end(), 0);
    std::stable_sort(blocks.begin(), blocks.end(), [&](int lhs, int rhs) {
        auto regret = [&](int block) {
            std::int64_t best = std::numeric_limits<std::int64_t>::max();
            std::int64_t second = best;
            for (std::int64_t cost : request.assignment_cost[block]) {
                if (cost < best) { second = best; best = cost; }
                else if (cost < second) second = cost;
            }
            return second == std::numeric_limits<std::int64_t>::max() ? 0 : second - best;
        };
        const auto a = regret(lhs), b = regret(rhs);
        return a != b ? a > b : lhs < rhs;
    });
    if (blocks.size() > 12) blocks.resize(12);
    return blocks;
}

inline std::vector<int> affectedBays(const std::vector<int>& source,
                                     const std::vector<int>& candidate,
                                     const std::vector<int>& coalition) {
    std::set<int> bays;
    for (int block : coalition) {
        bays.insert(source[block]);
        bays.insert(candidate[block]);
    }
    return {bays.begin(), bays.end()};
}

inline bool properSubsetBarrier(const Request& request,
                                const std::vector<int>& candidate,
                                const std::vector<int>& coalition,
                                const Reconstruct& reconstruct,
                                Result& result) {
    const std::uint64_t full = (std::uint64_t{1} << coalition.size()) - 1;
    for (std::uint64_t mask = 1; mask < full; ++mask) {
        if (++result.expansions > request.expansion_cap) return false;
        std::vector<int> partial = request.assignment;
        std::vector<int> changed;
        for (std::size_t bit = 0; bit < coalition.size(); ++bit) {
            if (!(mask & (std::uint64_t{1} << bit))) continue;
            const int block = coalition[bit];
            partial[block] = candidate[block];
            changed.push_back(block);
        }
        const auto bays = affectedBays(request.assignment, partial, changed);
        const Reconstruction check = reconstruct(partial, bays);
        ++result.proper_subsets;
        if (check.status == OracleStatus::UNKNOWN) {
            result.outcome = Outcome::UNKNOWN;
            return false;
        }
        if (check.status == OracleStatus::COMPLETE &&
            check.objective < request.initial_objective) return false;
    }
    return true;
}

inline Result run(const Request& request, const Reconstruct& reconstruct) {
    Result result;
    result.assignment = request.assignment;
    result.objective = request.initial_objective;
    if (!validRequest(request) || !reconstruct) return result;
    result.outcome = Outcome::NO_IMPROVEMENT;
    const std::vector<int> ranked = regretOrder(request);
    const int upper = std::min<int>(request.max_coalition, ranked.size());
    for (int size = request.min_coalition; size <= upper; ++size) {
        std::vector<int> coalition(ranked.begin(), ranked.begin() + size);
        std::vector<int> candidate = request.assignment;
        std::function<bool(int)> assign = [&](int depth) {
            if (depth == size) {
                if (++result.expansions > request.expansion_cap) {
                    result.outcome = Outcome::CAPPED;
                    return true;
                }
                const auto bays = affectedBays(request.assignment, candidate, coalition);
                if (bays.size() < 2) return false;
                Result trial = result;
                if (!properSubsetBarrier(request, candidate, coalition, reconstruct, trial)) {
                    result.expansions = trial.expansions;
                    result.proper_subsets = trial.proper_subsets;
                    if (trial.outcome == Outcome::UNKNOWN ||
                        trial.expansions > request.expansion_cap) {
                        result.outcome = trial.outcome == Outcome::UNKNOWN
                            ? Outcome::UNKNOWN : Outcome::CAPPED;
                        return true;
                    }
                    return false;
                }
                const Reconstruction rebuilt = reconstruct(candidate, bays);
                result.expansions = trial.expansions;
                result.proper_subsets = trial.proper_subsets;
                if (rebuilt.status == OracleStatus::UNKNOWN) {
                    result.outcome = Outcome::UNKNOWN;
                    return true;
                }
                if (rebuilt.status == OracleStatus::COMPLETE && rebuilt.z1 == 0 &&
                    rebuilt.objective < result.objective) {
                    result.outcome = Outcome::IMPROVED;
                    result.assignment = candidate;
                    result.coalition = coalition;
                    result.affected_bays = bays;
                    result.objective = rebuilt.objective;
                    result.state_hash = rebuilt.state_hash;
                    return true;
                }
                return false;
            }
            const int block = coalition[depth];
            const int source_bay = request.assignment[block];
            std::vector<int> alternatives(request.assignment_cost[block].size());
            std::iota(alternatives.begin(), alternatives.end(), 0);
            std::stable_sort(alternatives.begin(), alternatives.end(), [&](int a, int b) {
                const auto ca = request.assignment_cost[block][a];
                const auto cb = request.assignment_cost[block][b];
                return ca != cb ? ca < cb : a < b;
            });
            for (int bay : alternatives) {
                if (bay == source_bay) continue;
                candidate[block] = bay;
                if (assign(depth + 1)) return true;
            }
            candidate[block] = source_bay;
            return false;
        };
        if (assign(0)) return result;
    }
    return result;
}

struct Descriptor {
    static constexpr const char* id = "M3_ARCN";
    static constexpr std::uint32_t abi_version = 1;
};

}  // namespace arcn

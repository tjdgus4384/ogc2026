#include "kernel_assignment_regret.hpp"

#include <cassert>
#include <cstdint>
#include <vector>

using arcn::OracleStatus;
using arcn::Outcome;
using arcn::Reconstruction;
using arcn::Request;

static Request fixture(std::uint64_t cap = 100) {
    Request request;
    request.assignment = {0, 0, 1};
    request.assignment_cost = {{5, 0}, {6, 0}, {0, 4}};
    request.initial_objective = 30;
    request.expansion_cap = cap;
    request.min_coalition = 3;
    request.max_coalition = 3;
    return request;
}

static Reconstruction barrierOracle(const std::vector<int>& assignment,
                                    const std::vector<int>& bays) {
    assert(!bays.empty());
    if (assignment == std::vector<int>({0, 0, 1}))
        return {OracleStatus::COMPLETE, 0, 30, 0x301};
    if (assignment == std::vector<int>({1, 1, 0}))
        return {OracleStatus::COMPLETE, 0, 15, 0x150};
    return {OracleStatus::INFEASIBLE, 0, 0, 0};
}

int main() {
    {
        const auto result = arcn::run(fixture(), barrierOracle);
        assert(result.outcome == Outcome::IMPROVED);
        assert(result.assignment == std::vector<int>({1, 1, 0}));
        assert(result.coalition.size() == 3);
        assert(result.affected_bays == std::vector<int>({0, 1}));
        assert(result.objective == 15);
        assert(result.state_hash == 0x150);
        assert(result.proper_subsets == 6);
        assert(result.expansions == 7);
    }
    {
        const auto result = arcn::run(fixture(6), barrierOracle);
        assert(result.outcome == Outcome::CAPPED);
        assert(result.assignment == std::vector<int>({0, 0, 1}));
    }
    {
        auto no_barrier = [](const std::vector<int>& assignment,
                             const std::vector<int>& bays) {
            if (assignment == std::vector<int>({1, 0, 1}))
                return Reconstruction{OracleStatus::COMPLETE, 0, 20, 0x201};
            return barrierOracle(assignment, bays);
        };
        const auto result = arcn::run(fixture(), no_barrier);
        assert(result.outcome == Outcome::NO_IMPROVEMENT);
        assert(result.assignment == std::vector<int>({0, 0, 1}));
    }
    {
        auto unknown = [](const std::vector<int>& assignment,
                          const std::vector<int>& bays) {
            if (assignment == std::vector<int>({1, 0, 1}))
                return Reconstruction{OracleStatus::UNKNOWN, 0, 0, 0};
            return barrierOracle(assignment, bays);
        };
        const auto result = arcn::run(fixture(), unknown);
        assert(result.outcome == Outcome::UNKNOWN);
        assert(result.assignment == std::vector<int>({0, 0, 1}));
    }
    {
        Request invalid = fixture();
        invalid.min_coalition = 2;
        assert(arcn::run(invalid, barrierOracle).outcome == Outcome::INVALID);
    }
}

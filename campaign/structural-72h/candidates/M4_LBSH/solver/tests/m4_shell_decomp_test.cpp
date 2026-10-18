#include "kernel_shell_decomp.hpp"

#include <algorithm>
#include <cassert>
#include <vector>

using lbsh::Box;
using lbsh::CutKind;
using lbsh::Master;
using lbsh::Outcome;
using lbsh::Request;

static Request fixture(std::uint64_t cap = 1000) {
    Request request;
    request.bay_width = 7;
    request.bay_height = 1;
    request.boxes = {{3, 1}, {2, 1}};
    request.initial_objective = 10;
    request.expansion_cap = cap;
    Master master;
    master.order = {0, 1};
    master.x_before = {{0, 1}};
    master.exact_objective = 5;
    request.masters = {master};
    return request;
}

int main() {
    {
        const auto result = lbsh::run(fixture());
        assert(result.outcome == Outcome::IMPROVED);
        assert(result.objective == 5);
        assert(result.order == std::vector<int>({0, 1}));
        assert(result.x == std::vector<int>({0, 3}));
        assert(result.y == std::vector<int>({0, 0}));
        assert(std::find(result.x.begin(), result.x.end(), 3) != result.x.end());
        assert(result.cuts.empty());
    }
    {
        Request request = fixture();
        request.masters[0].x_before.push_back({1, 0});
        const auto result = lbsh::run(request);
        assert(result.outcome == Outcome::NO_IMPROVEMENT);
        assert(result.cuts.size() == 1);
        assert(result.cuts[0].kind == CutKind::PRECEDENCE);
    }
    {
        Request request = fixture();
        request.boxes[0].width = 8;
        const auto result = lbsh::run(request);
        assert(result.outcome == Outcome::NO_IMPROVEMENT);
        assert(result.cuts.size() == 1);
        assert(result.cuts[0].kind == CutKind::CONTAINMENT);
    }
    {
        const auto result = lbsh::run(fixture(1));
        assert(result.outcome == Outcome::UNKNOWN);
        assert(result.cuts.empty());
        assert(result.x.empty());
    }
    {
        Request request = fixture();
        request.bay_width = 4;
        const auto result = lbsh::run(request);
        assert(result.outcome == Outcome::NO_IMPROVEMENT);
        assert(result.cuts.size() == 1);
        assert(result.cuts[0].kind == CutKind::EXACT_MASTER_SHELL);
    }
    {
        Request invalid = fixture();
        invalid.masters[0].order = {0, 0};
        assert(lbsh::run(invalid).outcome == Outcome::INVALID);
    }
}

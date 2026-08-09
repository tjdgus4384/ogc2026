#include <cassert>
#include <vector>

#include "../src/kernel_joint_shell.hpp"

using namespace structural::m1;

static std::vector<BlockDomain> fixture() {
    std::vector<BlockDomain> domains;
    for (int i = 0; i < 6; ++i) {
        Placement incumbent{i, 0, 0, i, 0, 0, 5, i};
        Placement joint{i, 1, 1, 10 + i, 2, 0, 5, 5 - i};
        domains.push_back({i, incumbent, {incumbent, joint}});
    }
    return domains;
}

int main() {
    const auto domains = fixture();
    Request request;
    request.nodeCap = 1000;
    auto partial = [](const std::vector<Placement>& p) {
        int changed = 0;
        for (const Placement& x : p) changed += x.bay == 1;
        return changed == 0 || changed == (int)p.size();
    };
    auto exact = [](const std::vector<Placement>& p) {
        if (p.size() != 6) return false;
        int changed = 0;
        for (const Placement& x : p) changed += x.bay == 1;
        return changed == 0 || changed == 6;
    };
    auto objective = [](const std::vector<Placement>& p) {
        long long value = 600;
        for (const Placement& x : p) if (x.bay == 1) value -= 20;
        return value;
    };

    Result result = run(domains, request, partial, exact, objective);
    assert(result.outcome == Outcome::IMPROVED);
    assert(result.initialObjective == 600);
    assert(result.bestObjective == 480);
    assert(result.counters.completeBoards == 2);
    assert(result.counters.coupledDimensions >= 4);
    for (const Placement& x : result.placements) assert(x.bay == 1);

    Request capped = request;
    capped.nodeCap = 1;
    Result rollback = run(domains, capped, partial, exact, objective);
    assert(rollback.outcome == Outcome::CAPPED);
    assert(rollback.bestObjective == rollback.initialObjective);
    assert(rollback.placements == std::vector<Placement>() ||
           rollback.placements.size() == domains.size());
    assert(rollback.counters.rollbacks == 1);

    Request invalid = request;
    invalid.minCohort = 5;
    assert(run(domains, invalid, partial, exact, objective).outcome == Outcome::INVALID);
}

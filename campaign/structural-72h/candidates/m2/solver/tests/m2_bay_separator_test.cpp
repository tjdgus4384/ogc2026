#include <cassert>
#include <set>
#include <vector>

#include "../src/kernel_bay_separator.hpp"

using namespace structural::m2;

static BayState fixture() {
    BayState state;
    for (int i = 0; i < 50; ++i) {
        state.placements.push_back({i, 100 + i, 0, 0, 0, 10});
        if (i < 40) state.certifiedBackbone.push_back(i);
    }
    return state;
}

int main() {
    const BayState incumbent = fixture();
    Request request;
    request.nodeCap = 100;
    bool sawPostSeparatorVocabulary = false;
    auto provider = [&](int block, const std::vector<Placement>& formed,
                        const std::vector<Placement>& otherHalf) {
        if (!otherHalf.empty()) sawPostSeparatorVocabulary = true;
        const int dynamicBoundary = (int)(formed.size() + otherHalf.size());
        return std::vector<Placement>{{block, dynamicBoundary, block % 3, 1, 0, 10}};
    };
    auto exact = [](const std::vector<Placement>& placements) {
        if (placements.size() != 50) return false;
        std::set<int> ids;
        for (const Placement& p : placements) ids.insert(p.block);
        return ids.size() == 50;
    };
    auto objective = [](const std::vector<Placement>& placements) {
        long long total = 0;
        for (const Placement& p : placements) total += p.x;
        return total;
    };

    Result result = run(incumbent, request, provider, exact, objective);
    assert(result.outcome == Outcome::IMPROVED);
    assert(result.state.placements.size() == 50);
    assert(result.counters.mutableBlocks == 10);
    assert(result.counters.completeHalves == 2);
    assert(result.counters.completeMerges == 1);
    assert(result.counters.dynamicCoordinates == 10);
    assert(sawPostSeparatorVocabulary);
    assert(result.bestObjective < result.initialObjective);

    Request capped = request;
    capped.nodeCap = 3;
    Result rollback = run(incumbent, capped, provider, exact, objective);
    assert(rollback.outcome == Outcome::CAPPED);
    assert(rollback.state.placements == incumbent.placements);
    assert(rollback.counters.completeMerges == 0);
    assert(rollback.counters.rollbacks == 1);

    BayState tooSmall = incumbent;
    tooSmall.placements.pop_back();
    assert(run(tooSmall, request, provider, exact, objective).outcome == Outcome::INVALID);
}

#include "kernel_complete_leaf_constructor_g02.hpp"

#include <cassert>
#include <chrono>
#include <cstdint>
#include <limits>
#include <stdexcept>
#include <vector>

namespace {

int64_t deadlineAfterSeconds(int seconds) {
    return std::chrono::duration_cast<std::chrono::nanoseconds>(
               std::chrono::steady_clock::now().time_since_epoch()).count() +
           static_cast<int64_t>(seconds) * 1000000000LL;
}

target_constructor::Orientation square(double x0, double y0, double side) {
    return target_constructor::makeOrientation({{{x0, y0}, {x0 + side, y0},
                                                  {x0 + side, y0 + side}, {x0, y0 + side}}});
}

target_constructor::RawProblem problemWith(std::vector<target_constructor::Block> blocks,
                                           std::vector<double> widths = {2.0, 2.0}) {
    target_constructor::RawProblem problem;
    problem.bays = static_cast<int>(widths.size());
    problem.bayWidth = std::move(widths);
    problem.bayHeight.assign(problem.bays, 2.0);
    problem.blocks = std::move(blocks);
    problem.w1 = problem.w2 = problem.w3 = 1.0;
    return problem;
}

target_constructor::Block block(const target_constructor::Orientation& shape, int release,
                                int processing, std::vector<double> preference) {
    target_constructor::Block result;
    result.orientations = {shape};
    result.release = release;
    result.due = release;
    result.processing = processing;
    result.workload = 1.0;
    result.preference = std::move(preference);
    return result;
}

} // namespace

int main() {
    using namespace target_constructor;

    const Orientation unit = square(0.0, 0.0, 1.0);
    const RawProblem normal = problemWith({block(unit, 0, 2, {2.0, 1.0}),
                                           block(unit, 1, 3, {2.0, 1.0}),
                                           block(unit, 4, 1, {2.0, 1.0})});
    const Output complete = constructCompleteLeafG02(normal, deadlineAfterSeconds(2));
    assert(complete.assignments.size() == normal.blocks.size());
    assert(complete.events.size() == normal.blocks.size() * 2);
    assert(complete.telemetry.assignments == normal.blocks.size());
    assert(complete.telemetry.events == normal.blocks.size() * 2);
    for (size_t i = 0; i < complete.assignments.size(); ++i) {
        assert(complete.assignments[i].assigned);
        assert(complete.assignments[i].exit - complete.assignments[i].entry ==
               normal.blocks[i].processing);
    }
    assert(complete.assignments[0].entry == 0 && complete.assignments[0].exit == 2);
    assert(complete.assignments[1].entry == 2 && complete.assignments[1].exit == 5);
    assert(complete.assignments[2].entry == 5 && complete.assignments[2].exit == 6);

    const RawProblem translated = problemWith({block(square(-10.0, 0.0, 1.0), 0, 1, {1.0, 0.0})}, {1.0, 2.0});
    const Output accessSafe = constructCompleteLeafG02(translated, deadlineAfterSeconds(2));
    assert(accessSafe.assignments[0].x == 10);
    assert(accessSafe.assignments[0].y == 0);

    const RawProblem splitBays = problemWith({block(unit, 0, 4, {2.0, 1.0}),
                                              block(unit, 0, 4, {1.0, 2.0})});
    const Output mechanismOn = constructCompleteLeafG02(splitBays, deadlineAfterSeconds(2), true);
    const Output mechanismOff = constructCompleteLeafG02(splitBays, deadlineAfterSeconds(2), false);
    assert(mechanismOn.assignments[1].entry == mechanismOn.assignments[0].exit);
    assert(mechanismOff.assignments[0].entry == 0 && mechanismOff.assignments[1].entry == 0);
    assert(mechanismOff.assignments[0].bay != mechanismOff.assignments[1].bay);

    const RawProblem latestFinite = problemWith(
        {block(unit, std::numeric_limits<int>::max() - 2, 2, {1.0, 0.0})});
    const Output finite = constructCompleteLeafG02(latestFinite, deadlineAfterSeconds(2));
    assert(finite.assignments[0].exit == std::numeric_limits<int>::max());

    bool impossibleTiming = false;
    try {
        static_cast<void>(constructCompleteLeafG02(
            problemWith({block(unit, std::numeric_limits<int>::max(), 1, {1.0, 0.0})}),
            deadlineAfterSeconds(2)));
    } catch (const std::runtime_error&) { impossibleTiming = true; }
    assert(impossibleTiming);

    bool geometryRejected = false;
    try {
        static_cast<void>(constructCompleteLeafG02(
            problemWith({block(square(0.0, 0.0, 3.0), 0, 1, {1.0, 0.0})}), deadlineAfterSeconds(2)));
    } catch (const std::runtime_error&) { geometryRejected = true; }
    assert(geometryRejected);

    bool expired = false;
    try { static_cast<void>(constructCompleteLeafG02(normal, 1)); }
    catch (const std::runtime_error&) { expired = true; }
    assert(expired);
}

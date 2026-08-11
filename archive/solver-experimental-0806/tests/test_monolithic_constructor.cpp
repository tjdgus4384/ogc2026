#include "kernel_monolithic_constructor.hpp"

#include <cassert>
#include <chrono>
#include <cstdint>
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

target_constructor::RawProblem problemWith(const target_constructor::Orientation& orientation,
                                           int copies, double width = 2.0) {
    target_constructor::RawProblem problem;
    problem.bays = 1;
    problem.bayWidth = {width};
    problem.bayHeight = {1.0};
    problem.w1 = 1.0;
    problem.blocks.resize(copies);
    for (target_constructor::Block& block : problem.blocks) {
        block.orientations = {orientation};
        block.release = 0;
        block.due = 20;
        block.processing = 1;
        block.workload = 1.0;
        block.preference = {0.0};
    }
    return problem;
}

bool same(const target_constructor::Output& left, const target_constructor::Output& right) {
    if (left.assignments.size() != right.assignments.size() || left.events.size() != right.events.size()) return false;
    for (size_t i = 0; i < left.assignments.size(); ++i) {
        const auto& a = left.assignments[i];
        const auto& b = right.assignments[i];
        if (a.assigned != b.assigned || a.bay != b.bay || a.orientation != b.orientation ||
            a.x != b.x || a.y != b.y || a.entry != b.entry || a.exit != b.exit) return false;
    }
    for (size_t i = 0; i < left.events.size(); ++i)
        if (left.events[i].block != right.events[i].block || left.events[i].time != right.events[i].time ||
            left.events[i].entering != right.events[i].entering) return false;
    return left.objective.total == right.objective.total;
}

void expectFailure(const target_constructor::RawProblem& problem, int64_t deadline) {
    try {
        static_cast<void>(target_constructor::constructMonolithic(problem, deadline));
        assert(false);
    } catch (const std::runtime_error&) {
    }
}

} // namespace

int main() {
    using namespace target_constructor;
    const RawProblem normal = problemWith(square(0.0, 0.0, 1.0), 2);
    const Output complete = constructMonolithic(normal, deadlineAfterSeconds(2));
    assert(complete.assignments.size() == 2 && complete.events.size() == 4);
    for (const Assignment& assignment : complete.assignments) assert(assignment.assigned);

    const RawProblem translated = problemWith(square(-10.0, 0.0, 1.0), 1, 1.0);
    const Output fullCoordinates = constructMonolithic(translated, deadlineAfterSeconds(2));
    assert(fullCoordinates.assignments[0].x == 10);

    const Output on = constructMonolithic(normal, deadlineAfterSeconds(2), true);
    const Output off = constructMonolithic(normal, deadlineAfterSeconds(2), false);
    const Output again = constructMonolithic(normal, deadlineAfterSeconds(2), true);
    assert(same(on, off));
    assert(same(on, again));

    Constructor rollback(normal, deadlineAfterSeconds(2));
    const size_t token = rollback.snapshot();
    rollback.assign(0, {false, 0, 0, 0, 0, 0, 1});
    rollback.rollback(token);
    assert(rollback.unassignedCount() == 2 && rollback.complete() == false);

    expectFailure(problemWith(square(0.0, 0.0, 2.0), 1, 1.0), deadlineAfterSeconds(2));
    expectFailure(normal, deadlineAfterSeconds(-1));
}

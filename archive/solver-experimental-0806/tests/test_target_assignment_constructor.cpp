#include "kernel_target_assignment_constructor.hpp"
#include <cassert>
#include <chrono>
#include <stdexcept>

using namespace target_constructor;

static int64_t deadline() {
    return std::chrono::duration_cast<std::chrono::nanoseconds>(
        std::chrono::steady_clock::now().time_since_epoch()).count() + 1000000000LL;
}

static Orientation square(int side) {
    return makeOrientation({{{0, 0}, {static_cast<double>(side), 0},
                             {static_cast<double>(side), static_cast<double>(side)},
                             {0, static_cast<double>(side)}}});
}

static RawProblem problem(std::vector<Block> blocks, std::vector<double> widths = {2, 2}) {
    RawProblem result;
    result.bays = static_cast<int>(widths.size());
    result.bayWidth = widths;
    result.bayHeight.assign(widths.size(), 2);
    result.blocks = std::move(blocks);
    result.w1 = result.w2 = result.w3 = 1;
    return result;
}

int main() {
    const Output empty = constructTargetAssignment(problem({}), deadline());
    assert(empty.assignments.empty() && empty.events.empty());

    Block a{{square(1)}, 0, 1, 1, 1, {2, 1}};
    Block b{{square(2)}, 0, 1, 1, 1, {2, 1}};
    const Output complete = constructTargetAssignment(problem({a, b}, {2, 1}), deadline());
    assert(complete.assignments.size() == 2 && complete.events.size() == 4);
    assert(complete.assignments[0].bay == 1);
    assert(complete.telemetry.rollbacks > 0);

    bool timedOut = false;
    try { constructTargetAssignment(problem({a}), 1); }
    catch (const std::runtime_error&) { timedOut = true; }
    assert(timedOut);

    Block late{{square(1)}, 0, 0, 1, 1, {1, 1}};
    bool onRejected = false;
    try { constructTargetAssignment(problem({late}), deadline(), true); }
    catch (const std::runtime_error&) { onRejected = true; }
    const Output off = constructTargetAssignment(problem({late}), deadline(), false);
    assert(onRejected && off.objective.tardiness == 1);

    Constructor invalid(problem({a, a}), deadline());
    invalid.assign(0, {false, 0, 0, 0, 0, 0, 1});
    invalid.assign(1, {false, 0, 0, 0, 0, 0, 1});
    bool overlapRejected = false;
    try { invalid.emit(); }
    catch (const std::logic_error&) { overlapRejected = true; }
    assert(overlapRejected);

    const Output repeat = constructTargetAssignment(problem({a, b}, {2, 1}), deadline());
    assert(repeat.assignments[0].bay == complete.assignments[0].bay);
}

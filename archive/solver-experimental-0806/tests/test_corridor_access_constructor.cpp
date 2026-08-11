#include "kernel_corridor_access_constructor.hpp"
#include <cassert>
#include <chrono>
#include <stdexcept>

using namespace target_constructor;

static int64_t deadline() {
    return std::chrono::duration_cast<std::chrono::nanoseconds>(
        std::chrono::steady_clock::now().time_since_epoch()).count() + 1000000000LL;
}

static Orientation square() {
    return makeOrientation({{{0, 0}, {1, 0}, {1, 1}, {0, 1}}});
}

static RawProblem problem(std::vector<Block> blocks) {
    RawProblem result;
    result.bays = 2;
    result.bayWidth = {2, 2};
    result.bayHeight = {2, 2};
    result.blocks = std::move(blocks);
    return result;
}

int main() {
    const Output empty = constructCorridorAccess(problem({}), deadline());
    assert(empty.assignments.empty() && empty.events.empty());

    Block left{{square()}, 0, 4, 1, 1, {2, 1}};
    Block right{{square()}, 0, 4, 1, 1, {1, 2}};
    const Output reserved = constructCorridorAccess(problem({left, right}), deadline(), true);
    assert(reserved.assignments.size() == 2 && reserved.events.size() == 4);
    assert(reserved.assignments[0].exit <= reserved.assignments[1].entry);
    for (size_t i = 1; i < reserved.events.size(); ++i)
        assert(reserved.events[i - 1].time <= reserved.events[i].time);

    const Output unreserved = constructCorridorAccess(problem({left, right}), deadline(), false);
    assert(unreserved.assignments[0].entry == unreserved.assignments[1].entry);
    assert(reserved.assignments[1].entry != unreserved.assignments[1].entry);

    std::vector<Assignment> present{{true, 0, 0, 0, 0, 0, 1}};
    std::vector<Orientation> shapes{square()};
    assert(!craneEntry(problem({left}), 0, present, shapes, square(), 0, 0));

    bool deadlineRejected = false;
    try { constructCorridorAccess(problem({left}), 1); }
    catch (const std::runtime_error&) { deadlineRejected = true; }
    assert(deadlineRejected);

    const Output repeat = constructCorridorAccess(problem({left, right}), deadline(), true);
    assert(repeat.assignments[0].entry == reserved.assignments[0].entry);
    assert(repeat.assignments[1].entry == reserved.assignments[1].entry);
}

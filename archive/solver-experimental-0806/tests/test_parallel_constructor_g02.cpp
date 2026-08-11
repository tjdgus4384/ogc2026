#include "kernel_parallel_constructor_g02.hpp"

#include <cassert>
#include <chrono>
#include <stdexcept>
#include <vector>
#include <utility>

using namespace target_constructor;

static int64_t deadline() {
    return std::chrono::duration_cast<std::chrono::nanoseconds>(
        std::chrono::steady_clock::now().time_since_epoch()).count() + 1000000000LL;
}

static Orientation square() {
    return makeOrientation({{{0, 0}, {1, 0}, {1, 1}, {0, 1}}});
}

static Block block(int release = 0, int due = 4) {
    return {{square()}, release, due, 1, 1.0, {1.0}};
}

static RawProblem oneBay(double width, std::vector<Block> blocks) {
    RawProblem result;
    result.bays = 1;
    result.bayWidth = {width};
    result.bayHeight = {1.0};
    result.blocks = std::move(blocks);
    return result;
}

int main() {
    const RawProblem parallel = oneBay(2.0, {block(), block()});
    const Output concurrent = constructParallelG02(parallel, deadline(), true);
    assert(concurrent.assignments.size() == 2);
    assert(concurrent.assignments[0].entry == concurrent.assignments[1].entry);
    assert(concurrent.assignments[0].exit == concurrent.assignments[1].exit);
    assert(!geometryOverlap(square(), concurrent.assignments[0].x, concurrent.assignments[0].y,
                            square(), concurrent.assignments[1].x, concurrent.assignments[1].y));

    const RawProblem crowded = oneBay(1.0, {block(), block()});
    const Output fallback = constructParallelG02(crowded, deadline(), true);
    assert(fallback.assignments[0].exit <= fallback.assignments[1].entry);
    const std::vector<Assignment> present{{true, 0, 0, 0, 0, 0, 1}};
    const std::vector<Orientation> shapes{square()};
    assert(!craneEntry(crowded, 0, present, shapes, square(), 0, 0));

    assert(fallback.telemetry.assignments == fallback.assignments.size());
    assert(fallback.telemetry.events == fallback.events.size());
    assert(fallback.events.size() == fallback.assignments.size() * 2);
    for (const Assignment& assignment : fallback.assignments) assert(assignment.assigned);
    for (size_t i = 1; i < fallback.events.size(); ++i)
        assert(fallback.events[i - 1].time <= fallback.events[i].time);

    bool expiredRejected = false;
    try { constructParallelG02(oneBay(1.0, {block()}), 1, true); }
    catch (const std::runtime_error&) { expiredRejected = true; }
    assert(expiredRejected);
}

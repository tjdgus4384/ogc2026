#include "../src/target_constructor.hpp"
#include <cassert>
#include <chrono>
#include <stdexcept>

namespace tc = target_constructor;

static int64_t futureDeadline() {
    return std::chrono::duration_cast<std::chrono::nanoseconds>(
        std::chrono::steady_clock::now().time_since_epoch()).count() + 1000000000LL;
}

static tc::RawProblem rawProblem() {
    tc::RawProblem raw;
    raw.bays = 1;
    raw.bayWidth = {10.0};
    raw.bayHeight = {10.0};
    raw.w1 = 1.0;
    tc::Block block;
    block.release = 0;
    block.due = 5;
    block.workload = 2.0;
    block.processing = 2;
    block.preference = {4.0};
    geom::Poly square{{0, 0}, {2, 0}, {2, 2}, {0, 2}};
    block.orientations.push_back(tc::makeOrientation({square}));
    raw.blocks.push_back(block);
    raw.blocks.push_back(block);
    return raw;
}

int main() {
    tc::Constructor constructor(rawProblem(), futureDeadline());
    assert(constructor.unassignedCount() == 2 && !constructor.complete());

    bool rejectedEmptyEmission = false;
    try { constructor.emit(); }
    catch (const std::logic_error&) { rejectedEmptyEmission = true; }
    assert(rejectedEmptyEmission);

    const size_t token = constructor.snapshot();
    tc::Assignment first;
    first.bay = 0;
    first.orientation = 0;
    first.x = 1;
    first.y = 1;
    first.entry = 0;
    first.exit = 3;
    constructor.assign(0, first);
    assert(constructor.unassignedCount() == 1 && !constructor.complete());

    bool rejectedPartialEmission = false;
    try { constructor.emit(); }
    catch (const std::logic_error&) { rejectedPartialEmission = true; }
    assert(rejectedPartialEmission);

    tc::Assignment second = first;
    second.x = 4;
    constructor.assign(1, second);
    assert(constructor.complete());
    tc::Output complete = constructor.emit();
    assert(complete.assignments.size() == rawProblem().blocks.size());
    assert(complete.events.size() == 4);
    assert(complete.telemetry.assignments == 2 && complete.telemetry.events == 4);
    assert(complete.events[0].entering && complete.events[1].entering &&
           !complete.events[2].entering && !complete.events[3].entering &&
           complete.events[2].time == 3 && complete.events[3].time == 3);

    constructor.rollback(token);
    assert(constructor.unassignedCount() == 2 && !constructor.complete());
    bool rejectedRolledBackEmission = false;
    try { constructor.emit(); }
    catch (const std::logic_error&) { rejectedRolledBackEmission = true; }
    assert(rejectedRolledBackEmission);
    bool rejectedProcessing = false;
    tc::Constructor processingConstructor(rawProblem(), futureDeadline());
    tc::Assignment tooShort = first;
    tooShort.exit = 1;
    try { processingConstructor.assign(0, tooShort); }
    catch (const std::invalid_argument&) { rejectedProcessing = true; }
    assert(rejectedProcessing);

    tc::Constructor collisionConstructor(rawProblem(), futureDeadline());
    collisionConstructor.assign(0, first);
    collisionConstructor.assign(1, first);
    assert(!collisionConstructor.complete());
    bool rejectedCollision = false;
    try { collisionConstructor.emit(); }
    catch (const std::logic_error&) { rejectedCollision = true; }
    assert(rejectedCollision);

    bool rejectedForbiddenState = false;
    first.assigned = true;
    try { constructor.assign(0, first); }
    catch (const std::invalid_argument&) { rejectedForbiddenState = true; }
    assert(rejectedForbiddenState);

    bool expired = false;
    try { tc::Constructor expiredConstructor(rawProblem(), 1); expiredConstructor.emit(); }
    catch (const std::runtime_error&) { expired = true; }
    assert(expired);
}

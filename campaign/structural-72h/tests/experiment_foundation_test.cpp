#include <cassert>
#include <sstream>
#include "../foundation/experiment_foundation.hpp"

namespace {
crane::OrientGeom unitSquare(uint64_t id) {
    crane::OrientGeom geometry(1, id, 0);
    geometry.layers.push_back({{0, 0}, {1, 0}, {1, 1}, {0, 1}});
    geometry.bb = {0, 0, 1, 1};
    geometry.lbb.push_back(geometry.bb);
    geometry.ip.push_back({0.5, 0.5});
    geometry.parea = 1;
    return geometry;
}

void makeFixture(core::Problem& problem, core::Sol& solution,
                 occ::OccIndex& occupancy, repair::Ctx& context) {
    problem.n = 2; problem.m = 1;
    problem.R = {0, 0}; problem.D = {3, 3}; problem.P = {2, 2};
    problem.workload = {1, 1}; problem.pref = {{1}, {1}};
    problem.Smax = {1, 1}; problem.bayW = {4}; problem.bayH = {4};
    problem.u = {1}; problem.geo.resize(2);
    problem.geo[0].orients.push_back(unitSquare(0));
    problem.geo[1].orients.push_back(unitSquare(1));
    solution.init(2, 1);
    for (int i = 0; i < 2; ++i) {
        solution.placed[i] = 1; solution.bay[i] = 0; solution.ori[i] = 0;
        solution.x[i] = i * 2; solution.y[i] = 0;
        solution.e[i] = 0; solution.f[i] = 2; solution.addIdx(i);
    }
    occupancy.build(problem, 4);
    occupancy.stamp(problem, solution, 0, true);
    occupancy.stamp(problem, solution, 1, true);
    context.pb = &problem; context.s = &solution; context.oc = &occupancy;
    context.rng.seed(7); context.horizon = 4;
}
} // namespace

int main() {
    core::Problem problem; core::Sol solution; occ::OccIndex occupancy;
    repair::Ctx context{};
    makeFixture(problem, solution, occupancy, context);

    const auto audit = experiment::auditExact(problem, solution);
    assert(audit.status == experiment::AuditStatus::COMPLETE);
    assert(audit.events.size() == 4);
    assert(audit.events[0].entry && audit.events[1].entry);
    assert(!audit.events[2].entry && !audit.events[3].entry);

    core::Sol collision = solution;
    collision.x[1] = 0;
    const auto bad = experiment::auditExact(problem, collision);
    assert(bad.status == experiment::AuditStatus::INVALID);
    assert(bad.diagnosticCode == "COLLISION");

    std::vector<std::vector<int>> candidates{{1, 2, 3}};
    std::vector<experiment::OrderedEvent> events = audit.events;
    bool active = false;
    experiment::ExperimentContext experimentContext{
        problem, solution, occupancy, context, candidates, events, active};
    const experiment::ExperimentSnapshot snapshot(experimentContext);
    const std::string before = snapshot.stateHash();
    solution.x[0] = 3; occupancy.bay[0].bits[0] ^= 1; context.rng();
    candidates.clear(); events.clear(); active = true;
    snapshot.restore(experimentContext);
    const experiment::ExperimentSnapshot restored(experimentContext);
    assert(restored.stateHash() == before);
    assert(candidates.size() == 1 && events.size() == 4 && !active);
    assert(context.pb == &problem && context.s == &solution && context.oc == &occupancy);

    experiment::MechanismRequest request;
    request.id = "missing"; request.armEnabled = true;
    const auto unknown = experiment::runExperiment(experimentContext, request);
    assert(unknown.outcome == experiment::Outcome::INVALID);
    assert(unknown.diagnosticCode == "UNKNOWN_ID");
    assert(restored.stateHash() == experiment::ExperimentSnapshot(experimentContext).stateHash());
}

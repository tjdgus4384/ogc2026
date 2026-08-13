#include <cassert>
#include <algorithm>
#include <string>
#include <utility>
#include <vector>

#include "../src/alns.hpp"

namespace {

crane::OrientGeom make_orient(std::vector<geom::Poly> layers) {
    crane::OrientGeom out;
    out.layers = std::move(layers);
    out.bb = {1e18, 1e18, -1e18, -1e18};
    for (const auto& layer : out.layers) {
        const auto bb = geom::bbox(layer);
        out.lbb.push_back(bb);
        out.ip.push_back(geom::interiorPoint(layer));
        out.bb.x0 = std::min(out.bb.x0, bb.x0);
        out.bb.y0 = std::min(out.bb.y0, bb.y0);
        out.bb.x1 = std::max(out.bb.x1, bb.x1);
        out.bb.y1 = std::max(out.bb.y1, bb.y1);
    }
    return out;
}

geom::Poly cell(int x) {
    return {{(double)x, 0}, {(double)x + 1, 0},
            {(double)x + 1, 1}, {(double)x, 1}};
}

core::Problem make_problem(std::vector<std::vector<geom::Poly>> layers,
                           std::vector<int> processing, int width) {
    core::Problem pb;
    pb.n = (int)layers.size();
    pb.m = 1;
    pb.geo.resize(pb.n);
    for (int i = 0; i < pb.n; ++i)
        pb.geo[i].orients.push_back(make_orient(std::move(layers[i])));
    pb.R.assign(pb.n, 0);
    pb.D.assign(pb.n, 10);
    pb.P = std::move(processing);
    pb.bayW = {(double)width};
    pb.bayH = {1};
    pb.workload.assign(pb.n, 1.0);
    pb.pref.assign(pb.n, std::vector<double>(1, 0.0));
    pb.Smax.assign(pb.n, 0.0);
    pb.u = {1.0};
    pb.w1 = pb.w2 = pb.w3 = 1.0;
    return pb;
}

core::Problem problem() {
    using geom::Poly;
    const Poly left{{0, 0}, {1, 0}, {1, 1}, {0, 1}};
    const Poly right{{2, 0}, {3, 0}, {3, 1}, {2, 1}};

    // Block 0 has one low layer at left.  Block 1 rests at right on layer 0
    // but reaches over block 0 on layer 1.  Thus 1 obstructs moving 0, while
    // 0 does not obstruct moving 1 and their resting layers do not collide.
    return make_problem({{left}, {right, left}}, {2, 3}, 3);
}

core::Problem chain_problem() {
    const auto p0 = cell(0), p1 = cell(2), p2 = cell(4);
    // 1 blocks 0 and 2 blocks 1.  There are no other directed obstructions.
    return make_problem({{p0}, {p1, p0}, {p2, p1}}, {2, 3, 4}, 5);
}

core::Problem cycle_problem() {
    const auto p0 = cell(0), p1 = cell(2), p2 = cell(4);
    // Same-level bases are disjoint, while upper layers induce the pure cycle
    // 1 blocks 0, 2 blocks 1, 0 blocks 2.
    return make_problem({{p0, p2}, {p1, p0}, {p2, p1}}, {1, 1, 1}, 5);
}

core::Sol schedule(std::vector<int> entries, std::vector<int> exits) {
    assert(entries.size() == exits.size());
    core::Sol s;
    s.init((int)entries.size(), 1);
    for (int i = 0; i < (int)entries.size(); ++i) {
        s.placed[i] = 1;
        s.bay[i] = 0;
        s.ori[i] = 0;
        s.x[i] = 0;
        s.y[i] = 0;
        s.e[i] = entries[i];
        s.f[i] = exits[i];
    }
    s.rebuildIdx(1);
    return s;
}

core::Sol schedule(int e0, int f0, int e1, int f1) {
    return schedule({e0, e1}, {f0, f1});
}

// Mirrors Engine::pairRelations' public code contract using the same core
// predicates.  The standalone header test intentionally does not link the
// pybind translation unit.
int pair_relation_code(const core::Problem& pb, const core::Sol& s,
                       int a, int b) {
    crane::ScopedMemoBypass memoBypass(true);
    if (core::restOverlap(pb, s, a, b)) return 1;
    const bool bBlocksA = core::entryBlocked(pb, s, b, a);
    const bool aBlocksB = core::entryBlocked(pb, s, a, b);
    if (bBlocksA && aBlocksB) return 1;
    if (bBlocksA) return 2;
    if (aBlocksB) return 3;
    return 0;
}

void test_pair_relation_contract() {
    const core::Problem directedPb = problem();
    const core::Sol directed = schedule(0, 2, 1, 4);
    assert(pair_relation_code(directedPb, directed, 0, 1) == 2);
    assert(pair_relation_code(directedPb, directed, 1, 0) == 3);

    // Relation classification is geometry-only: even a time-disjoint pair
    // has the same directed code for the flexible CP to condition later.
    const core::Sol timeDisjoint = schedule(0, 1, 100, 103);
    assert(pair_relation_code(directedPb, timeDisjoint, 0, 1) == 2);

    const auto left = cell(0), right = cell(2);
    const core::Problem restPb =
        make_problem({{left}, {left}}, {1, 1}, 3);
    assert(pair_relation_code(restPb, schedule(0, 1, 2, 3), 0, 1) == 1);

    // Bases and upper layers are pairwise disjoint at rest, while each upper
    // layer blocks the other block's base during crane travel.
    const core::Problem mutualPb =
        make_problem({{left, right}, {right, left}}, {1, 1}, 3);
    const core::Sol mutual = schedule(0, 1, 2, 3);
    assert(!core::restOverlap(mutualPb, mutual, 0, 1));
    assert(core::entryBlocked(mutualPb, mutual, 0, 1));
    assert(core::entryBlocked(mutualPb, mutual, 1, 0));
    assert(pair_relation_code(mutualPb, mutual, 0, 1) == 1);

    const core::Problem clearPb =
        make_problem({{left}, {right}}, {1, 1}, 3);
    assert(pair_relation_code(clearPb, schedule(0, 1, 2, 3), 0, 1) == 0);
}

void test_event_corner_candidate_range() {
    const core::Problem pb = problem();
    // The vocabulary engine starts from a complete incumbent.  Projecting out
    // only target 0 leaves incumbent peer 1 available to contribute corners.
    core::Sol source = schedule(0, 2, 1, 4);
    core::Sol peers = source;
    peers.placed[0] = 0;
    peers.rebuildIdx(pb.m);

    repair::Ctx ctx{};
    ctx.pb = &pb;
    ctx.s = &peers;
    std::vector<repair::RB> rbs;
    repair::rbCollect(ctx, 0, 0, 2, rbs);
    assert(rbs.size() == 1);  // resident 1 overlaps candidate window [0,2)

    std::vector<int> xs, ys;
    repair::cornerSetsRB(ctx, 0, 0, 0, rbs, xs, ys);
    assert(xs == std::vector<int>({0, 2}));
    assert(ys == std::vector<int>({0}));

    core::Sol pair;
    pair.init(pb.n, pb.m);
    pair.bay[0] = pair.bay[1] = 0;
    pair.ori[0] = pair.ori[1] = 0;
    pair.x[1] = pair.y[0] = pair.y[1] = 0;
    crane::ScopedMemoBypass memoBypass(true);
    pair.x[0] = 0;
    assert(!core::restOverlap(pb, pair, 0, 1));
    // This raw corner is obstructed only by resident 1's upper layer.  The
    // event-corner vocabulary must retain it for the nesting CP.
    assert(core::entryBlocked(pb, pair, 1, 0));
    pair.x[0] = 2;
    assert(core::restOverlap(pb, pair, 0, 1));

    // Endpoint touch is time-disjoint, so the same resident contributes no
    // bbox corner to a minimum-duration [0,2) candidate window.
    peers.e[1] = 2;
    peers.f[1] = 4;
    repair::rbCollect(ctx, 0, 0, 2, rbs);
    assert(rbs.empty());
}

void stamp_all(const core::Problem& pb, const core::Sol& s,
               occ::OccIndex& oc) {
    for (int i = 0; i < pb.n; ++i)
        if (s.placed[i]) oc.stamp(pb, s, i, true);
}

void assert_occ_matches_rebuild(const core::Problem& pb, const core::Sol& s,
                                int horizon, const occ::OccIndex& actual) {
    occ::OccIndex expected;
    expected.build(pb, horizon);
    stamp_all(pb, s, expected);
    assert(actual.bay.size() == expected.bay.size());
    for (size_t j = 0; j < actual.bay.size(); ++j)
        assert(actual.bay[j].bits == expected.bay[j].bits);
    assert(actual.areaUsed == expected.areaUsed);
}

void test_apply_occ(const core::Problem& pb, int candidate_id) {
    constexpr int horizon = 4;
    core::Sol complete = schedule(0, 2, 1, 4);
    const auto candidate = repair::currentPlacement(complete, candidate_id);

    core::Sol live = complete;
    live.placed[candidate_id] = 0;
    live.rebuildIdx(1);
    occ::OccIndex oc;
    oc.build(pb, horizon);
    stamp_all(pb, live, oc);

    core::Sol certified = live;
    repair::loadPlacement(certified, candidate_id, candidate);
    certified.addIdx(candidate_id);
    std::vector<repair::Ctx::ExitExtension> extensions;
    bool cycle = false, hard = false;
    assert(repair::minimumExitClosure(
        pb, certified, 0, horizon, extensions, cycle, hard));
    assert(!cycle && !hard && extensions.size() == 1);
    // candidate 0 is itself extended; candidate 1 extends resident 0's tail.
    assert((extensions[0].block == candidate_id) == (candidate_id == 0));

    repair::Ctx::ExitClosureWitness witness;
    witness.valid = true;
    witness.block = candidate_id;
    witness.candidate = candidate;
    witness.prefixSignature =
        repair::exitClosurePrefixSignature(live, candidate_id);
    witness.extensions = extensions;

    repair::Ctx ctx{};
    ctx.pb = &pb;
    ctx.s = &live;
    ctx.oc = &oc;
    ctx.horizon = horizon;
    assert(repair::applyExitClosure(ctx, candidate_id, witness));
    assert(live.placed[candidate_id]);
    assert(live.f[0] == 4 && live.f[1] == 4);
    assert_occ_matches_rebuild(pb, live, horizon, oc);
}

core::Problem run_problem() {
    // With integer placement coordinates in a unit-width bay, both blocks have
    // exactly one position.  Block 1's upper layer obstructs block 0, while
    // their bases are disjoint and block 0 does not obstruct block 1.
    const geom::Poly left{{0, 0}, {0.4, 0}, {0.4, 1}, {0, 1}};
    const geom::Poly right{{0.6, 0}, {1, 0}, {1, 1}, {0.6, 1}};
    core::Problem pb = make_problem({{left}, {right, left}}, {2, 3}, 1);
    pb.R = {0, 1};
    pb.D = {4, 4};
    // Keep the heuristic area screen out of this semantic fixture.  Geometry,
    // crane feasibility, and the official archive audit remain exact.
    pb.fillRatio = 10.0;
    return pb;
}

bool same_solution(const core::Sol& a, const core::Sol& b) {
    return a.placed == b.placed && a.bay == b.bay && a.ori == b.ori &&
           a.x == b.x && a.y == b.y && a.e == b.e && a.f == b.f &&
           a.byBay == b.byBay;
}

std::vector<int> flat_solution(const core::Sol& s) {
    std::vector<int> out;
    out.reserve(s.placed.size() * 7);
    for (size_t i = 0; i < s.placed.size(); i++) {
        out.push_back(s.bay[i]);
        out.push_back(s.ori[i]);
        out.push_back(s.x[i]);
        out.push_back(s.y[i]);
        out.push_back(s.e[i]);
        out.push_back(s.f[i]);
        out.push_back((int)s.placed[i]);
    }
    return out;
}

bool same_live_occ(const occ::OccIndex& a, const occ::OccIndex& b) {
    if (a.bay.size() != b.bay.size() || a.areaUsed != b.areaUsed)
        return false;
    for (size_t j = 0; j < a.bay.size(); j++)
        if (a.bay[j].bits != b.bay[j].bits) return false;
    return true;
}

struct RunCapture {
    core::Sol live, controlBest, checkpoint, archiveCheckpoint,
              archiveReturned;
    occ::OccIndex oc;
    alns::RunStats stats;
    double controlObjective = 0.0;
    int checkpointOfficial = 0, archiveCheckpointOfficial = 0;
    std::string finalRngState;
};

RunCapture run_arm(const core::Problem& pb, const core::Sol& start,
                   uint64_t seed, bool archive) {
    constexpr int horizon = 10;
    RunCapture out;
    out.live = start;
    out.oc.build(pb, horizon);
    stamp_all(pb, out.live, out.oc);

    repair::Ctx ctx{};
    ctx.pb = &pb;
    ctx.s = &out.live;
    ctx.oc = &out.oc;
    ctx.horizon = horizon;

    alns::RunParams rp;
    rp.opMask = 1u << 0;       // every completed neighbourhood is random-removal
    rp.bigQProb = 0.0;
    rp.hcFrac = 1.0;           // exercise the ordinary shared SA draw on ties
    rp.maxIters = 8;
    rp.exitClosureArchive = archive;
    rp.checkpointIter = rp.maxIters;
    rp.checkpointOut = &out.checkpoint;
    rp.checkpointOfficialOut = &out.checkpointOfficial;
    if (archive) {
        rp.archiveCheckpointOut = &out.archiveCheckpoint;
        rp.archiveCheckpointOfficialOut =
            &out.archiveCheckpointOfficial;
    }
    rp.controlBestOut = &out.controlBest;
    rp.controlBestObjectiveOut = &out.controlObjective;
    rp.finalRngStateOut = &out.finalRngState;
    rp.archiveReturnedOut = &out.archiveReturned;
    out.stats = alns::run(ctx, 60.0, rp, seed, 1);
    return out;
}

void assert_common_stats(const alns::RunStats& control,
                         const alns::RunStats& archive) {
    assert(control.iters == archive.iters);
    assert(control.accepted == archive.accepted);
    assert(control.improved == archive.improved);
    assert(control.failed == archive.failed);
    assert(control.best == archive.best);
    assert(control.tSamples == archive.tSamples);
    assert(control.badIter == archive.badIter);
    assert(control.badKind == archive.badKind);
    assert(control.badPhase == archive.badPhase);
    assert(control.exitTwinTests == archive.exitTwinTests);
    assert(control.exitTwinPass == archive.exitTwinPass);
    assert(control.exitTwinCompared == archive.exitTwinCompared);
    assert(control.exitTwinDominant == archive.exitTwinDominant);
    assert(control.exitTwinRescued == archive.exitTwinRescued);
    assert(control.phase2Calls == archive.phase2Calls);
    assert(control.phase2Success == archive.phase2Success);
    assert(control.stayNeighbourhoods == archive.stayNeighbourhoods);
    assert(control.stayTried == archive.stayTried);
    assert(control.stayBuilt == archive.stayBuilt);
    assert(control.staySelected == archive.staySelected);
    assert(control.stayRescued == archive.stayRescued);
    assert(control.stayGain == archive.stayGain);
    for (int k = 0; k < 10; k++) {
        assert(control.opUsed[k] == archive.opUsed[k]);
        assert(control.opImp[k] == archive.opImp[k]);
    }
    // These are live-closure decision counters, not observer telemetry.  The
    // archive arm must leave all of them exactly at the OFF value.
    assert(control.closureSelected == archive.closureSelected);
    assert(control.closureAccepted == archive.closureAccepted);
    assert(control.closureNewBest == archive.closureNewBest);
    assert(control.closureRescued == archive.closureRescued);
    assert(control.closureExtendedBlocks == archive.closureExtendedBlocks);
    assert(control.closureExtendedDays == archive.closureExtendedDays);
    assert(control.closurePaidDays == archive.closurePaidDays);
    assert(control.closureGain == archive.closureGain);
}

void test_archive_run_integration() {
    const core::Problem pb = run_problem();
    const core::Sol start = schedule(4, 6, 1, 4);
    core::Sol auditedStart = start;
    assert(alns::audit(pb, auditedStart) == 0);
    assert(core::evaluate(pb, start).total == 2.0);

    // Shift/swap are fixed production operators that can consume an iteration.
    // Search a tiny deterministic seed range instead of adding a production
    // test hook; every actual trial still traverses alns::run end-to-end.
    RunCapture archive;
    uint64_t chosenSeed = 0;
    bool found = false;
    for (uint64_t seed = 0; seed < 32 && !found; seed++) {
        RunCapture trial = run_arm(pb, start, seed, true);
        if (trial.stats.archiveReturned == 1) {
            archive = std::move(trial);
            chosenSeed = seed;
            found = true;
        }
    }
    assert(found);
    RunCapture control = run_arm(pb, start, chosenSeed, false);

    assert_common_stats(control.stats, archive.stats);
    assert(flat_solution(control.controlBest) ==
           flat_solution(archive.controlBest));
    assert(flat_solution(control.checkpoint) ==
           flat_solution(archive.checkpoint));
    assert(same_solution(control.live, archive.live));
    assert(same_solution(control.live, control.controlBest));
    assert(same_solution(archive.live, archive.controlBest));
    assert(same_live_occ(control.oc, archive.oc));
    assert_occ_matches_rebuild(pb, control.live, 10, control.oc);
    assert_occ_matches_rebuild(pb, archive.live, 10, archive.oc);
    assert(control.controlObjective == archive.controlObjective);
    assert(control.controlObjective == 2.0);
    assert(!control.finalRngState.empty());
    assert(control.finalRngState == archive.finalRngState);
    assert(control.checkpointOfficial == 0);
    assert(archive.checkpointOfficial == 0);
    assert(control.stats.finalOfficial == 0);
    assert(control.archiveCheckpoint.placed.empty());

    assert(control.stats.archiveBoundPruned == 0);
    assert(control.stats.archiveUpdates == 0);
    assert(control.stats.archiveReturned == 0);
    assert(control.stats.archiveGain == 0.0);
    assert(control.stats.archiveObjective == 0.0);
    assert(control.stats.archiveAuditRejects == 0);
    assert(control.archiveReturned.placed.empty());

    assert(archive.stats.archiveUpdates >= 1);
    assert(archive.stats.archiveReturned == 1);
    assert(archive.stats.archiveGain == 2.0);
    assert(archive.stats.archiveObjective == 0.0);
    assert(archive.stats.archiveAuditRejects == 0);
    assert(archive.stats.finalOfficial == 1);
    assert(archive.stats.closureSuffixBuilt >= archive.stats.archiveUpdates);
    assert(!archive.archiveReturned.placed.empty());
    assert(!same_solution(archive.live, archive.archiveReturned));
    assert(archive.archiveReturned.e[0] == 0);
    assert(archive.archiveReturned.f[0] == 4);
    assert(alns::auditClosureArchive(pb, archive.archiveReturned, 10) == 0);
    assert(core::evaluate(pb, archive.archiveReturned).total == 0.0);
    assert(alns::hasExtendedStay(pb, archive.archiveReturned));
    assert(archive.archiveCheckpointOfficial == 1);
    assert(same_solution(archive.archiveCheckpoint,
                         archive.archiveReturned));
}

}  // namespace

int main() {
    const core::Problem pb = problem();

    test_pair_relation_contract();
    test_event_corner_candidate_range();

    // Memo-neutral speculation must use the exact geometry fallback, restore
    // nested state, and return the same predicate as the normal cached path.
    assert(crane::memoEnabled());
    const bool cachedBlock = core::entryBlocked(
        pb, schedule(0, 2, 1, 4), 1, 0);
    {
        crane::ScopedMemoBypass outer(true);
        assert(!crane::memoEnabled());
        assert(core::entryBlocked(pb, schedule(0, 2, 1, 4), 1, 0) ==
               cachedBlock);
        {
            crane::ScopedMemoBypass inner(true);
            assert(!crane::memoEnabled());
        }
        assert(!crane::memoEnabled());
    }
    assert(crane::memoEnabled());

    assert(core::entryBlocked(pb, schedule(0, 2, 1, 4), 1, 0));
    assert(!core::entryBlocked(pb, schedule(0, 2, 1, 4), 0, 1));
    assert(!core::restOverlap(pb, schedule(0, 2, 1, 4), 0, 1));

    core::Sol s = schedule(0, 2, 1, 4);
    std::vector<repair::Ctx::ExitExtension> extensions;
    bool cycle = false, hard = false;
    assert(repair::minimumExitClosure(
        pb, s, 0, 4, extensions, cycle, hard));
    assert(!cycle && !hard);
    assert(s.f[0] == 4 && s.f[1] == 4);
    assert(extensions.size() == 1);
    assert(extensions[0].block == 0);
    assert(extensions[0].from == 2 && extensions[0].to == 4);
    assert(core::vpFlexibleExits(pb, s, 0));
    assert(core::vpFlexibleExits(pb, s, 1));
    std::vector<int> order;
    assert(core::exitOrderAt(pb, s, 0, 4, order));
    assert(order.size() == 2 && order[0] == 1 && order[1] == 0);

    // The commit path must stamp an extended candidate's whole interval and an
    // existing resident's new [oldF,newF) tail exactly like a full rebuild.
    test_apply_occ(pb, 0);
    test_apply_occ(pb, 1);

    // Endpoint touch is time-disjoint under [e,f), so it needs no closure.
    core::Sol touching = schedule(0, 2, 2, 5);
    extensions.clear();
    cycle = hard = false;
    assert(!repair::minimumExitClosure(
        pb, touching, 0, 5, extensions, cycle, hard));
    assert(!cycle && !hard && extensions.empty());
    assert(touching.f[0] == 2 && touching.f[1] == 5);

    // The endpoint equal to the horizon is legal above; one beyond it is not.
    core::Sol beyond = schedule(0, 2, 1, 4);
    extensions.clear();
    cycle = hard = false;
    assert(!repair::minimumExitClosure(
        pb, beyond, 0, 3, extensions, cycle, hard));
    assert(!cycle && hard);

    // A second obstruction is activated after the first pass: 0 grows to 4,
    // then 1 grows to 6, so the least fixed point must revisit 0 and grow to 6.
    const core::Problem chainPb = chain_problem();
    core::Sol chain = schedule({0, 1, 2}, {2, 4, 6});
    assert(core::entryBlocked(chainPb, chain, 1, 0));
    assert(core::entryBlocked(chainPb, chain, 2, 1));
    assert(!core::entryBlocked(chainPb, chain, 0, 1));
    assert(!core::entryBlocked(chainPb, chain, 1, 2));
    extensions.clear();
    cycle = hard = false;
    assert(repair::minimumExitClosure(
        chainPb, chain, 0, 6, extensions, cycle, hard));
    assert(!cycle && !hard);
    assert(chain.f == std::vector<int>({6, 6, 6}));
    assert(extensions.size() == 2);
    assert(extensions[0].block == 0 && extensions[0].from == 2 &&
           extensions[0].to == 6);
    assert(extensions[1].block == 1 && extensions[1].from == 4 &&
           extensions[1].to == 6);

    // A same-entry obstruction cycle has no operation order.  It is a cycle
    // classification boundary, not a generic geometry/horizon hard reject.
    const core::Problem cyclePb = cycle_problem();
    core::Sol cyclic = schedule({0, 0, 0}, {1, 2, 3});
    assert(core::entryBlocked(cyclePb, cyclic, 1, 0));
    assert(core::entryBlocked(cyclePb, cyclic, 2, 1));
    assert(core::entryBlocked(cyclePb, cyclic, 0, 2));
    extensions.clear();
    cycle = hard = false;
    assert(!repair::minimumExitClosure(
        cyclePb, cyclic, 0, 3, extensions, cycle, hard));
    assert(cycle && !hard);

    // If the obstructed wrapper enters after its obstructer, exit extension
    // alone cannot repair the entry-order inversion.
    core::Sol wrong = schedule(1, 3, 0, 4);
    extensions.clear();
    cycle = hard = false;
    assert(!repair::minimumExitClosure(
        pb, wrong, 0, 10, extensions, cycle, hard));
    assert(hard);

    // The archive selector is a pure, strict true-objective minimum.  It must
    // recompute both values instead of trusting a stale scalar, preserve both
    // inputs, return an overlong official sidecar when it wins, and give every
    // tie to control.
    core::Problem choicePb = problem();
    choicePb.D = {4, 10};
    const core::Sol control = schedule(4, 6, 1, 4);  // two tardy days
    core::Sol archived = schedule(0, 4, 1, 4); // overlong, no tardiness
    assert(alns::auditClosureArchive(choicePb, archived, 10) == 0);
    const auto chosen = alns::chooseClosureArchive(
        choicePb, control, &archived,
        std::numeric_limits<double>::infinity());
    assert(chosen.returnedArchive && chosen.official);
    assert(chosen.controlObjective == 2.0);
    assert(chosen.archiveObjective == 0.0);
    assert(chosen.solution.e == archived.e && chosen.solution.f == archived.f);
    assert(control.e == std::vector<int>({4, 1}));
    assert(control.f == std::vector<int>({6, 4}));

    const auto tied = alns::chooseClosureArchive(
        choicePb, archived, &archived, -1e300); // malicious stale hint
    assert(!tied.returnedArchive && !tied.official);
    assert(tied.controlObjective == tied.archiveObjective);

    core::Sol incomplete = archived;
    incomplete.placed[1] = 0;
    incomplete.rebuildIdx(1);
    assert(alns::auditClosureArchive(choicePb, incomplete, 10) != 0);

    test_archive_run_integration();
}

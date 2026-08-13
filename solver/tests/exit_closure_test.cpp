#include <cassert>
#include <algorithm>
#include <vector>

#include "../src/repair.hpp"

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

}  // namespace

int main() {
    const core::Problem pb = problem();
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
}

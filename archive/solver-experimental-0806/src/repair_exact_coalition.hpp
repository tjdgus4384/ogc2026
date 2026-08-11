// repair_exact_coalition.hpp -- bounded joint reinsertion for small destroy coalitions.
#pragma once
#include "repair.hpp"
#include <unordered_set>
#include <limits>
#include <tuple>

namespace repair {
namespace coalition {

struct Stats {
    size_t nodes = 0;
    size_t feasible = 0;
    size_t branches = 0;
    size_t candidateChecks = 0;
    size_t candidateRejected = 0;
    size_t dominated = 0;
    size_t prunedBound = 0;
    size_t peakBeam = 0;
    bool deadline = false;
};

struct Options {
    size_t nodeLimit = 0;       // zero selects a size-adaptive bounded budget
    int beamWidth = 0;          // zero selects a size-adaptive bounded beam
    int candidateLimit = 0;     // per selected block, zero selects adaptively
};

struct Placement {
    int block, bay, ori, x, y, e;
};

struct Node {
    Sol sol;
    occ::OccIndex oc;
    std::vector<uint8_t> left;
    double lower = 0.0;
    double score = 0.0;
};

inline bool expired(const Ctx& c, Stats& st) {
    if (!deadlineExpired(c)) return false;
    st.deadline = true;
    return true;
}

// This deliberately omits z2: adding a remaining block can reduce imbalance,
// so the partial z2 is not a lower bound.  z1/z3 minima are independently
// attainable lower bounds and remain valid even when the beam is truncated.
inline double lowerBound(const Problem& pb, const Node& n) {
    double z1 = 0.0, z3 = 0.0;
    for (int i = 0; i < pb.n; ++i) {
        if (n.sol.placed[i]) {
            z1 += std::max(0, n.sol.f[i] - pb.D[i]);
            z3 += pb.Smax[i] - pb.pref[i][n.sol.bay[i]];
        } else if (n.left[i]) {
            z1 += std::max(0, pb.R[i] + pb.P[i] - pb.D[i]);
            double best = pb.pref[i].empty() ? 0.0
                                              : *std::max_element(pb.pref[i].begin(), pb.pref[i].end());
            z3 += pb.Smax[i] - best;
        }
    }
    return pb.w1 * z1 + pb.w3 * z3;
}

inline uint64_t stateKey(const Node& n, const std::vector<int>& ids) {
    uint64_t h = 1469598103934665603ULL;
    auto mix = [&h](uint64_t v) { h ^= v; h *= 1099511628211ULL; };
    for (int i : ids) {
        mix((uint64_t)n.left[i]);
        if (!n.sol.placed[i]) continue;
        mix((uint32_t)n.sol.bay[i]); mix((uint32_t)n.sol.ori[i]);
        mix((uint32_t)n.sol.x[i]); mix((uint32_t)n.sol.y[i]);
        mix((uint32_t)n.sol.e[i]);
    }
    return h;
}

inline void eventTimes(const Problem& pb, const Sol& s, int A,
                       std::vector<int>& out) {
    out.clear();
    out.push_back(pb.R[A]);
    const int punctual = pb.D[A] - pb.P[A];
    if (punctual >= pb.R[A]) out.push_back(punctual);
    for (int j = 0; j < pb.m; ++j) for (int k : s.byBay[j]) {
        if (!s.placed[k]) continue;
        if (s.e[k] >= pb.R[A]) out.push_back(s.e[k]);
        if (s.f[k] >= pb.R[A]) out.push_back(s.f[k]);
    }
    std::sort(out.begin(), out.end());
    out.erase(std::unique(out.begin(), out.end()), out.end());
}

inline void feasiblePlacements(Ctx& c, Node& n, int A, int limit,
                               std::vector<Placement>& out, Stats& st) {
    const Problem& pb = *c.pb;
    out.clear();
    std::vector<int> times, bays, xs, ys;
    eventTimes(pb, n.sol, A, times);
    bays.resize(pb.m);
    for (int j = 0; j < pb.m; ++j) bays[j] = j;
    if (c.forcedBay && c.forcedBay[A] >= 0) bays.assign(1, c.forcedBay[A]);
    else std::sort(bays.begin(), bays.end(), [&](int a, int b) {
        return pb.pref[A][a] > pb.pref[A][b];
    });
    const int cap = (c.entryCap && c.entryCap[A] >= 0) ? c.entryCap[A] : -1;
    for (int e : times) {
        if (expired(c, st) || (int)out.size() >= limit) return;
        if (e < pb.R[A] || e + pb.P[A] >= c.horizon || (cap >= 0 && e > cap)) continue;
        const int f = e + pb.P[A];
        for (int j : bays) {
            if (expired(c, st) || (int)out.size() >= limit) return;
            std::vector<RB> residents;
            rbCollect(c, j, e, f, residents);
            for (int oi = 0; oi < (int)pb.geo[A].orients.size(); ++oi) {
                if (expired(c, st) || (int)out.size() >= limit) return;
                cornerSetsRB(c, A, oi, j, residents, xs, ys);
                // Vertex contacts add exact integer geometry missed by bbox corners.
                shapeContactSets(c, A, oi, j, e, f, xs, ys, 48);
                for (int y : ys) for (int x : xs) {
                    if (expired(c, st) || (int)out.size() >= limit) return;
                    ++st.candidateChecks;
                    if (n.oc.hits(A, oi, j, x, y, e, f)) {
                        ++st.candidateRejected;
                        continue;
                    }
                    if (!tryPlacement(c, A, j, oi, x, y, e)) {
                        ++st.candidateRejected;
                        continue;
                    }
                    ++st.feasible;
                    out.push_back({A, j, oi, x, y, e});
                    removeBlock(c, A);
                }
            }
        }
    }
}

inline bool repairExactCoalition(Ctx& c, const std::vector<int>& coalition,
                                 Stats* stats = nullptr, Options opt = {}) {
    Stats local;
    Stats& st = stats ? *stats : local;
    st = Stats{};
    const Problem& pb = *c.pb;
    if (coalition.size() < 4 || coalition.size() > 24 || expired(c, st)) return false;
    std::vector<int> ids = coalition;
    std::sort(ids.begin(), ids.end());
    if (std::adjacent_find(ids.begin(), ids.end()) != ids.end()) return false;
    for (int i : ids) if (i < 0 || i >= pb.n || !c.s->placed[i]) return false;

    const Sol originalSol = *c.s;
    const occ::OccIndex originalOcc = *c.oc;
    const core::Objective originalObj = core::evaluate(pb, originalSol);
    const int q = (int)ids.size();
    const int beam = opt.beamWidth > 0 ? opt.beamWidth : (q <= 6 ? 16 : q <= 12 ? 10 : 6);
    const size_t nodeLimit = opt.nodeLimit ? opt.nodeLimit
                                           : (size_t)(q <= 6 ? 1800 : q <= 12 ? 1200 : 700);
    const int candidateLimit = opt.candidateLimit > 0 ? opt.candidateLimit
                                                        : (q <= 6 ? 24 : q <= 12 ? 16 : 10);

    Node root{originalSol, originalOcc, std::vector<uint8_t>(pb.n, 0)};
    Ctx work = c;
    work.s = &root.sol; work.oc = &root.oc;
    for (int i : ids) { root.left[i] = 1; removeBlock(work, i); }
    root.lower = lowerBound(pb, root);
    root.score = root.lower;
    std::vector<Node> frontier;
    frontier.push_back(std::move(root));
    std::unordered_set<uint64_t> seen;
    seen.insert(stateKey(frontier.front(), ids));
    Sol bestSol;
    occ::OccIndex bestOcc;
    double best = originalObj.total;
    bool improved = false;

    while (!frontier.empty() && st.nodes < nodeLimit && !expired(c, st)) {
        std::vector<Node> next;
        for (Node& node : frontier) {
            if (st.nodes >= nodeLimit || expired(c, st)) break;
            ++st.nodes;
            if (node.lower >= best - 1e-9) { ++st.prunedBound; continue; }
            int chosen = -1, chosenCount = std::numeric_limits<int>::max();
            std::vector<Placement> chosenPlaces, places;
            Ctx stateCtx = c;
            stateCtx.s = &node.sol; stateCtx.oc = &node.oc;
            // Minimum remaining feasible placements is conflict-directed: hard
            // blocks are inserted first, and ties favour the earlier deadline.
            for (int A : ids) if (node.left[A]) {
                feasiblePlacements(stateCtx, node, A, candidateLimit, places, st);
                if (expired(c, st)) break;
                const int count = (int)places.size();
                if (count < chosenCount || (count == chosenCount &&
                    (chosen < 0 || pb.D[A] < pb.D[chosen] ||
                     (pb.D[A] == pb.D[chosen] && pb.P[A] > pb.P[chosen])))) {
                    chosen = A; chosenCount = count; chosenPlaces.swap(places);
                }
                if (chosenCount == 0) break;
            }
            if (expired(c, st) || chosen < 0 || chosenPlaces.empty()) continue;
            for (const Placement& p : chosenPlaces) {
                if (st.nodes + st.branches >= nodeLimit || expired(c, st)) break;
                Node child = node;
                Ctx childCtx = c;
                childCtx.s = &child.sol; childCtx.oc = &child.oc;
                if (!tryPlacement(childCtx, p.block, p.bay, p.ori, p.x, p.y, p.e)) continue;
                ++st.branches;
                child.left[p.block] = 0;
                child.lower = lowerBound(pb, child);
                const uint64_t key = stateKey(child, ids);
                if (!seen.insert(key).second) { ++st.dominated; continue; }
                bool complete = true;
                for (int i : ids) if (child.left[i]) { complete = false; break; }
                if (complete) {
                    const core::Objective value = core::evaluate(pb, child.sol);
                    if (value.total < best - 1e-9 && core::stateExactEvents(pb, child.sol)) {
                        best = value.total; bestSol = std::move(child.sol);
                        bestOcc = std::move(child.oc); improved = true;
                    }
                } else if (child.lower < best - 1e-9) {
                    child.score = child.lower;
                    next.push_back(std::move(child));
                } else ++st.prunedBound;
            }
        }
        if (next.empty() || expired(c, st)) break;
        std::sort(next.begin(), next.end(), [](const Node& a, const Node& b) {
            return a.score != b.score ? a.score < b.score : a.lower < b.lower;
        });
        if ((int)next.size() > beam) next.resize(beam);
        st.peakBeam = std::max(st.peakBeam, next.size());
        frontier.swap(next);
    }
    if (!improved || st.deadline) {
        // The live context was never mutated; explicit restoration also protects
        // callers against future changes that share root state differently.
        *c.s = originalSol;
        *c.oc = originalOcc;
        return false;
    }
    *c.s = std::move(bestSol);
    *c.oc = std::move(bestOcc);
    return true;
}

} // namespace coalition
} // namespace repair

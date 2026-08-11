// repair_scored.hpp -- bounded best-insertion repair, selected by full score.
#pragma once

#include "repair.hpp"
#include <algorithm>
#include <cmath>
#include <limits>
#include <vector>

namespace repair_scored {

struct Config {
    // Bounded enumeration controls.  Zero/negative values use these defaults.
    int topK = 24;
    int maxCandidates = 640;
    int maxTimes = 96;
    size_t contactAxisCap = 96;

    // Higher score is better.  Every non-objective term is normalized to a
    // dimensionless quantity; objectiveDelta is the exact core objective delta.
    double objectiveWeight = 1.0;
    double residualWeight = 0.20;
    double futureBlockingWeight = 0.35;
    double compactnessWeight = 0.15;
    double preferenceRegretWeight = 0.20;
};

struct Stats {
    int eventTimes = 0;
    int geometryPoints = 0;
    int snapshotRejected = 0;
    int checkerRejected = 0;
    int feasible = 0;
    int retained = 0;
    bool candidateLimitReached = false;
    bool deadlineExpired = false;
    double selectedScore = -std::numeric_limits<double>::infinity();
    bool committed = false;
};

namespace detail {

struct Candidate {
    int bay, entry, ori, x, y;
    double score;
};

inline int positiveOr(int value, int fallback) { return value > 0 ? value : fallback; }

inline void eventTimes(const repair::Ctx& c, int block, int last, int cap,
                       std::vector<int>& out) {
    const core::Problem& pb = *c.pb;
    const core::Sol& s = *c.s;
    out.clear();
    out.push_back(pb.R[block]);
    out.push_back(last);
    // A feasibility state changes only at an existing resident's entry or exit.
    for (int j = 0; j < pb.m; ++j) for (int k : s.byBay[j]) {
        if (!s.placed[k]) continue;
        if (s.e[k] >= pb.R[block] && s.e[k] <= last) out.push_back(s.e[k]);
        if (s.f[k] >= pb.R[block] && s.f[k] <= last) out.push_back(s.f[k]);
    }
    std::sort(out.begin(), out.end());
    out.erase(std::unique(out.begin(), out.end()), out.end());
    if ((int)out.size() <= cap) return;

    // Keep both endpoints and sample all event phases rather than truncating the
    // early prefix (which would recreate earliest-fit behaviour under pressure).
    std::vector<int> sampled;
    sampled.reserve(cap);
    for (int q = 0; q < cap; ++q) {
        size_t pos = (size_t)q * (out.size() - 1) / (size_t)(cap - 1);
        sampled.push_back(out[pos]);
    }
    out.swap(sampled);
}

inline double occupancyArea(const core::Problem& pb, int block, int ori) {
    const auto& g = pb.geo[block].orients[ori];
    return knobs::polyArea() && g.parea > 0.0
         ? g.parea
         : (g.bb.x1 - g.bb.x0) * (g.bb.y1 - g.bb.y0);
}

inline double residualCapacity(const repair::Ctx& c, int bay, int entry,
                               int finish, double area) {
    const core::Problem& pb = *c.pb;
    const double capacity = std::max(1.0, pb.bayW[bay] * pb.bayH[bay]);
    double residual = 0.0;
    int days = 0;
    for (int t = std::max(0, entry); t < finish && t < c.oc->T; ++t) {
        residual += std::max(0.0, capacity - c.oc->areaUsed[bay][t] - area) / capacity;
        ++days;
    }
    return days ? residual / days : 0.0;
}

inline void placementQuality(const repair::Ctx& c, int block, int bay, int ori,
                             int x, int y, int entry, int finish,
                             double& futureBlocking, double& compactness) {
    const core::Problem& pb = *c.pb;
    const core::Sol& s = *c.s;
    const auto& bb = pb.geo[block].orients[ori].bb;
    const double ax0 = bb.x0 + x, ax1 = bb.x1 + x;
    const double ay0 = bb.y0 + y, ay1 = bb.y1 + y;
    const double scale = std::max(1.0, std::hypot(pb.bayW[bay], pb.bayH[bay]));
    const double wall = std::min(std::min(ax0, pb.bayW[bay] - ax1),
                                 std::min(ay0, pb.bayH[bay] - ay1));
    compactness = 1.0 - std::min(1.0, std::max(0.0, wall) / scale);
    futureBlocking = 0.0;
    for (int k : s.byBay[bay]) {
        if (!s.placed[k]) continue;
        const auto& kb = pb.geo[k].orients[s.ori[k]].bb;
        const double kx0 = kb.x0 + s.x[k], kx1 = kb.x1 + s.x[k];
        const double ky0 = kb.y0 + s.y[k], ky1 = kb.y1 + s.y[k];
        const double dx = std::max({kx0 - ax1, ax0 - kx1, 0.0});
        const double dy = std::max({ky0 - ay1, ay0 - ky1, 0.0});
        const double gap = std::hypot(dx, dy);
        compactness = std::max(compactness, 1.0 - std::min(1.0, gap / scale));
        // Entries/exits during the new stay are the future crane constraints;
        // close shapes at those events are more likely to fragment access.
        if (entry <= s.e[k] && s.e[k] < finish) futureBlocking += 1.0 / (1.0 + gap);
        if (entry < s.f[k] && s.f[k] <= finish) futureBlocking += 1.0 / (1.0 + gap);
    }
    futureBlocking /= std::max(1.0, (double)s.byBay[bay].size());
}

inline bool exactCandidate(const repair::Ctx& c, int block, int bay, int ori,
                           int x, int y, int entry, core::Sol& trial) {
    trial = *c.s;
    trial.bay[block] = bay; trial.ori[block] = ori;
    trial.x[block] = x; trial.y[block] = y;
    trial.e[block] = entry; trial.f[block] = entry + c.pb->P[block];
    trial.placed[block] = 1;
    trial.addIdx(block);
    return core::vpExactEvents(*c.pb, trial, block);
}

inline void retain(std::vector<Candidate>& best, Candidate candidate, int topK) {
    best.push_back(candidate);
    std::sort(best.begin(), best.end(), [](const Candidate& a, const Candidate& b) {
        if (a.score != b.score) return a.score > b.score;
        if (a.entry != b.entry) return a.entry < b.entry;
        if (a.bay != b.bay) return a.bay < b.bay;
        if (a.ori != b.ori) return a.ori < b.ori;
        return a.y != b.y ? a.y < b.y : a.x < b.x;
    });
    if ((int)best.size() > topK) best.pop_back();
}

} // namespace detail

// Insert an unplaced block after evaluating a bounded event/contact candidate set.
// The live solution and occupancy index are untouched until the selected candidate
// passes a final exact checker audit and is committed.
inline bool insertBest(repair::Ctx& c, int block, const Config& cfg, Stats* stats = nullptr) {
    Stats local;
    Stats& st = stats ? *stats : local;
    st = Stats{};
    if (repair::deadlineExpired(c)) { st.deadlineExpired = true; return false; }
    if (block < 0 || block >= c.pb->n || c.s->placed[block]) return false;

    const core::Problem& pb = *c.pb;
    const int maxTimes = detail::positiveOr(cfg.maxTimes, 96);
    const int maxCandidates = detail::positiveOr(cfg.maxCandidates, 640);
    const int topK = detail::positiveOr(cfg.topK, 24);
    int last = c.horizon - pb.P[block] - 1;
    if (c.entryCap && c.entryCap[block] >= 0) last = std::min(last, c.entryCap[block]);
    if (last < pb.R[block]) return false;

    std::vector<int> times;
    detail::eventTimes(c, block, last, maxTimes, times);
    st.eventTimes = (int)times.size();
    std::vector<int> bays;
    if (c.forcedBay && c.forcedBay[block] >= 0) bays.push_back(c.forcedBay[block]);
    else for (int j = 0; j < pb.m; ++j) bays.push_back(j);

    const core::Objective before = core::evaluate(pb, *c.s);
    std::vector<detail::Candidate> best;
    core::Sol trial;
    std::vector<int> xs, ys;
    occ::OccIndex::Snap snap;
    int examined = 0;
    for (int entry : times) {
        const int finish = entry + pb.P[block];
        for (int bay : bays) {
            if (repair::deadlineExpired(c)) { st.deadlineExpired = true; return false; }
            c.oc->buildSnap(bay, entry, finish, snap);
            for (int ori = 0; ori < (int)pb.geo[block].orients.size(); ++ori) {
                repair::cornerSets(c, block, ori, bay, entry, finish, xs, ys);
                // Adds polygon vertex-contact translations beyond bbox corners.
                repair::shapeContactSets(c, block, ori, bay, entry, finish, xs, ys,
                                         cfg.contactAxisCap ? cfg.contactAxisCap : 96);
                for (int y : ys) for (int x : xs) {
                    if (repair::deadlineExpired(c)) { st.deadlineExpired = true; return false; }
                    if (examined++ >= maxCandidates) { st.candidateLimitReached = true; goto scored_done; }
                    ++st.geometryPoints;
                    if (c.oc->hitsSnap(snap, block, ori, x, y)) { ++st.snapshotRejected; continue; }
                    if (!detail::exactCandidate(c, block, bay, ori, x, y, entry, trial)) {
                        ++st.checkerRejected;
                        continue;
                    }
                    ++st.feasible;
                    const double objectiveDelta = core::evaluate(pb, trial).total - before.total;
                    const double residual = detail::residualCapacity(
                        c, bay, entry, finish, detail::occupancyArea(pb, block, ori));
                    double future, compact;
                    detail::placementQuality(c, block, bay, ori, x, y, entry, finish,
                                             future, compact);
                    const double regret = (pb.Smax[block] - pb.pref[block][bay]) /
                                          std::max(1.0, std::fabs(pb.Smax[block]));
                    const double score = -cfg.objectiveWeight * objectiveDelta
                                       + cfg.residualWeight * residual
                                       - cfg.futureBlockingWeight * future
                                       + cfg.compactnessWeight * compact
                                       - cfg.preferenceRegretWeight * regret;
                    detail::retain(best, {bay, entry, ori, x, y, score}, topK);
                }
            }
        }
    }
scored_done:
    st.retained = (int)best.size();
    if (best.empty() || repair::deadlineExpired(c)) {
        st.deadlineExpired = repair::deadlineExpired(c);
        return false;
    }

    // Exact authority is repeated immediately before the only mutation.  This
    // protects against a changed live state and leaves no partially placed block.
    const detail::Candidate selected = best.front();
    if (!detail::exactCandidate(c, block, selected.bay, selected.ori, selected.x,
                                selected.y, selected.entry, trial) ||
        repair::deadlineExpired(c)) {
        st.deadlineExpired = repair::deadlineExpired(c);
        return false;
    }
    const int oldBay = c.s->bay[block], oldOri = c.s->ori[block];
    const int oldX = c.s->x[block], oldY = c.s->y[block];
    const int oldE = c.s->e[block], oldF = c.s->f[block];
    const uint8_t oldPlaced = c.s->placed[block];
    c.s->bay[block] = selected.bay; c.s->ori[block] = selected.ori;
    c.s->x[block] = selected.x; c.s->y[block] = selected.y;
    c.s->e[block] = selected.entry; c.s->f[block] = selected.entry + pb.P[block];
    c.s->placed[block] = 1;
    c.s->addIdx(block);
    if (!core::vpExactEvents(pb, *c.s, block)) {
        c.s->delIdx(block);
        c.s->bay[block] = oldBay; c.s->ori[block] = oldOri;
        c.s->x[block] = oldX; c.s->y[block] = oldY;
        c.s->e[block] = oldE; c.s->f[block] = oldF;
        c.s->placed[block] = oldPlaced;
        return false;
    }
    c.oc->stamp(pb, *c.s, block, true);
    st.selectedScore = selected.score;
    st.committed = true;
    return true;
}

} // namespace repair_scored

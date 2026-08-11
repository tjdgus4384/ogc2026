// repair_learned.hpp -- bounded online contextual repair using exact candidates.
#pragma once

#include "repair.hpp"
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <limits>
#include <vector>

namespace repair_learned {

enum Feature : int {
    ObjectiveDelta, ResidualArea, WallContact, Fragmentation, EventPressure,
    DueSlack, Preference, LoadBalance, FeatureCount
};

struct Config {
    int maxCandidates = 768;
    int maxTimes = 96;
    int retain = 32;
    size_t contactAxisCap = 96;
    double exploration = 0.50;
    // Candidate probes are round-robin sampled over time, bay, orientation,
    // and corner/contact classes. This multiplier bounds empty-stratum skips.
    int strataAttemptsFactor = 8;
};

struct Feedback {
    std::array<double, FeatureCount + 1> context{};
    double scale = 1.0;
    bool valid = false;
};

// Fixed-size ridge/RLS model.  A Model is intentionally caller-owned: sharing it
// between runs is opt-in, while a fresh model has no information from past runs.
class Model {
public:
    Model() { reset(); }
    void reset(double ridge = 2.0) {
        const double diagonal = 1.0 / std::max(1e-6, ridge);
        weight_.fill(0.0);
        for (int i = 0; i <= FeatureCount; ++i)
            for (int j = 0; j <= FeatureCount; ++j)
                inverse_[i][j] = i == j ? diagonal : 0.0;
        updates_ = decisions_ = 0;
    }
    double predict(const std::array<double, FeatureCount + 1>& x) const {
        double value = 0.0;
        for (int i = 0; i <= FeatureCount; ++i) value += weight_[i] * x[i];
        return value;
    }
    double uncertainty(const std::array<double, FeatureCount + 1>& x) const {
        double q = 0.0;
        for (int i = 0; i <= FeatureCount; ++i)
            for (int j = 0; j <= FeatureCount; ++j) q += x[i] * inverse_[i][j] * x[j];
        return std::sqrt(std::max(0.0, q));
    }
    // exactDelta is post-placement total minus pre-placement total.
    void observe(const Feedback& feedback, double exactDelta) {
        if (!feedback.valid) return;
        const auto& x = feedback.context;
        std::array<double, FeatureCount + 1> ix{};
        for (int i = 0; i <= FeatureCount; ++i)
            for (int j = 0; j <= FeatureCount; ++j) ix[i] += inverse_[i][j] * x[j];
        double denominator = 1.0;
        for (int i = 0; i <= FeatureCount; ++i) denominator += x[i] * ix[i];
        if (!(denominator > 1e-12) || !std::isfinite(denominator)) return;
        const double reward = std::max(-2.0, std::min(2.0, -exactDelta / feedback.scale));
        const double error = reward - predict(x);
        for (int i = 0; i <= FeatureCount; ++i) weight_[i] += ix[i] * error / denominator;
        for (int i = 0; i <= FeatureCount; ++i)
            for (int j = 0; j <= FeatureCount; ++j) inverse_[i][j] -= ix[i] * ix[j] / denominator;
        ++updates_;
    }
    void noteDecision() { ++decisions_; }
    uint64_t updates() const { return updates_; }
    uint64_t decisions() const { return decisions_; }
private:
    std::array<double, FeatureCount + 1> weight_{};
    std::array<std::array<double, FeatureCount + 1>, FeatureCount + 1> inverse_{};
    uint64_t updates_ = 0, decisions_ = 0;
};

struct Result {
    int candidates = 0;
    int feasibleCandidates = 0;
    int completedRepairs = 0;
    int acceptedImprovements = 0;
    int learnedDecisions = 0;
    int explorationDecisions = 0;
    int checkerRejected = 0;
    bool candidateLimitReached = false;
    bool timedOut = false;
    bool completed = false;
    double objectiveDelta = 0.0;
    int strataVisited = 0;
    int cornerCandidates = 0;
    int contactCandidates = 0;
    int cohortOrderingDecisions = 0;
};
struct CohortConfig {
    int minBlocks = 3;
    int maxBlocks = 8;
    // Higher preference regret and lower temporal slack are repaired first.
    double preferenceRegretWeight = 1.0;
    double urgencyWeight = 1.0;
};

namespace detail {
struct Candidate {
    int bay = -1, entry = -1, ori = -1, x = 0, y = 0;
    std::array<double, FeatureCount + 1> context{};
    double delta = 0.0, learned = 0.0, exploration = 0.0, rank = 0.0;
};
inline double unit(double value) { return std::max(-1.0, std::min(1.0, value)); }
inline double area(const core::Problem& pb, int block, int ori) {
    const auto& g = pb.geo[block].orients[ori];
    return knobs::polyArea() && g.parea > 0.0 ? g.parea :
        (g.bb.x1 - g.bb.x0) * (g.bb.y1 - g.bb.y0);
}
inline void eventTimes(const repair::Ctx& c, int block, int last, int cap, std::vector<int>& out) {
    out.clear(); out.push_back(c.pb->R[block]); out.push_back(last);
    for (int j = 0; j < c.pb->m; ++j) for (int k : c.s->byBay[j]) if (c.s->placed[k]) {
        if (c.s->e[k] >= c.pb->R[block] && c.s->e[k] <= last) out.push_back(c.s->e[k]);
        if (c.s->f[k] >= c.pb->R[block] && c.s->f[k] <= last) out.push_back(c.s->f[k]);
    }
    std::sort(out.begin(), out.end()); out.erase(std::unique(out.begin(), out.end()), out.end());
    if ((int)out.size() <= cap) return;
    std::vector<int> sample; sample.reserve(cap);
    for (int i = 0; i < cap; ++i) sample.push_back(out[(size_t)i * (out.size() - 1) / (cap - 1)]);
    out.swap(sample);
}
inline bool exactCandidate(const repair::Ctx& c, int block, int bay, int ori, int x, int y,
                           int entry, core::Sol& trial) {
    trial = *c.s;
    trial.bay[block] = bay; trial.ori[block] = ori; trial.x[block] = x; trial.y[block] = y;
    trial.e[block] = entry; trial.f[block] = entry + c.pb->P[block]; trial.placed[block] = 1;
    trial.addIdx(block);
    return core::vpExactEvents(*c.pb, trial, block);
}
inline void retain(std::vector<Candidate>& pool, Candidate candidate, int cap) {
    pool.push_back(candidate);
    std::sort(pool.begin(), pool.end(), [](const Candidate& a, const Candidate& b) {
        if (a.rank != b.rank) return a.rank > b.rank;
        if (a.bay != b.bay) return a.bay < b.bay;
        if (a.entry != b.entry) return a.entry < b.entry;
        if (a.ori != b.ori) return a.ori < b.ori;
        return a.y != b.y ? a.y < b.y : a.x < b.x;
    });
    if ((int)pool.size() > cap) pool.pop_back();
}
inline void rebuild(const core::Problem& pb, repair::Ctx& c) {
    c.s->rebuildIdx(pb.m); c.oc->clearBits();
    for (int i = 0; i < pb.n; ++i) if (c.s->placed[i]) c.oc->stamp(pb, *c.s, i, true);
}
inline uint64_t mix(uint64_t value) {
    value ^= value >> 30; value *= 0xbf58476d1ce4e5b9ULL;
    value ^= value >> 27; value *= 0x94d049bb133111ebULL;
    return value ^ (value >> 31);
}
inline bool contains(const std::vector<int>& values, int value) {
    return std::binary_search(values.begin(), values.end(), value);
}
} // namespace detail

// Repair every listed currently-unplaced block.  This is transactional: a missed
// block, deadline, or final checker failure restores both Sol and OccIndex.
inline bool repairBlocks(repair::Ctx& c, const std::vector<int>& blocks, Model& model,
                         const Config& cfg = Config(), Result* result = nullptr) {
    Result local; Result& out = result ? *result : local; out = Result{};
    if (!c.pb || !c.s || !c.oc || repair::deadlineExpired(c)) { out.timedOut = repair::deadlineExpired(c); return false; }
    const core::Problem& pb = *c.pb;
    const core::Sol original = *c.s;
    const double originalObjective = core::evaluate(pb, original).total;
    const int candidateCap = std::max(1, cfg.maxCandidates);
    const int timeCap = std::max(2, cfg.maxTimes);
    const int retainCap = std::max(1, cfg.retain);

    for (int block : blocks) {
        if (repair::deadlineExpired(c)) { out.timedOut = true; *c.s = original; detail::rebuild(pb, c); return false; }
        if (block < 0 || block >= pb.n || c.s->placed[block]) { *c.s = original; detail::rebuild(pb, c); return false; }
        int last = c.horizon - pb.P[block] - 1;
        if (c.entryCap && c.entryCap[block] >= 0) last = std::min(last, c.entryCap[block]);
        if (last < pb.R[block]) { *c.s = original; detail::rebuild(pb, c); return false; }
        std::vector<int> times, bays, baseXs, baseYs, xs, ys;
        detail::eventTimes(c, block, last, timeCap, times);
        if (c.forcedBay && c.forcedBay[block] >= 0) bays.push_back(c.forcedBay[block]);
        else for (int j = 0; j < pb.m; ++j) bays.push_back(j);
        const double before = core::evaluate(pb, *c.s).total;
        const double scale = std::max(1.0, std::fabs(before));
        std::vector<detail::Candidate> pool, explorationPool; occ::OccIndex::Snap snap; core::Sol trial;
        int examined = 0;
        auto consider = [&](int bay, int entry, int ori, int x, int y, int contactClass) {
            ++examined; ++out.candidates;
            if (contactClass) ++out.contactCandidates; else ++out.cornerCandidates;
            const int finish = entry + pb.P[block];
            c.oc->buildSnap(bay, entry, finish, snap);
            if (c.oc->hitsSnap(snap, block, ori, x, y)) return;
            if (!detail::exactCandidate(c, block, bay, ori, x, y, entry, trial)) {
                ++out.checkerRejected; return;
            }
            ++out.feasibleCandidates;
            detail::Candidate candidate;
            candidate.bay = bay; candidate.entry = entry; candidate.ori = ori;
            candidate.x = x; candidate.y = y; candidate.context[0] = 1.0;
            candidate.delta = core::evaluate(pb, trial).total - before;
            candidate.context[ObjectiveDelta + 1] = detail::unit(-candidate.delta / scale);
            const auto& bb = pb.geo[block].orients[ori].bb;
            const double capacity = std::max(1.0, pb.bayW[bay] * pb.bayH[bay]);
            double residual = 0.0; int days = 0;
            for (int t = entry; t < finish && t < c.oc->T; ++t, ++days)
                residual += std::max(0.0, capacity - c.oc->areaUsed[bay][t] -
                                     detail::area(pb, block, ori)) / capacity;
            candidate.context[ResidualArea + 1] = days ? residual / days : 0.0;
            const double wall = std::min(std::min(bb.x0 + x, pb.bayW[bay] - bb.x1 - x),
                                         std::min(bb.y0 + y, pb.bayH[bay] - bb.y1 - y));
            candidate.context[WallContact + 1] = std::exp(-std::max(0.0, wall) /
                std::max(1.0, std::hypot(pb.bayW[bay], pb.bayH[bay])));
            const double strip = std::max({std::max(0.0, bb.x0 + x) * pb.bayH[bay],
                std::max(0.0, pb.bayW[bay] - bb.x1 - x) * pb.bayH[bay],
                std::max(0.0, bb.y0 + y) * pb.bayW[bay],
                std::max(0.0, pb.bayH[bay] - bb.y1 - y) * pb.bayW[bay]});
            candidate.context[Fragmentation + 1] = 1.0 - std::min(1.0, strip / capacity);
            int events = 0;
            for (int k : c.s->byBay[bay]) if (c.s->placed[k] &&
                ((entry <= c.s->e[k] && c.s->e[k] < finish) ||
                 (entry < c.s->f[k] && c.s->f[k] <= finish))) ++events;
            candidate.context[EventPressure + 1] =
                detail::unit((double)events / std::max(1, (int)c.s->byBay[bay].size()));
            candidate.context[DueSlack + 1] =
                detail::unit((double)(pb.D[block] - finish) / std::max(1, c.horizon));
            candidate.context[Preference + 1] = detail::unit(
                pb.pref[block][bay] / std::max(1.0, std::fabs(pb.Smax[block])));
            double totalLoad = pb.workload[block], maximum = 0.0;
            for (int q = 0; q < pb.m; ++q) {
                double load = q == bay ? pb.workload[block] : 0.0;
                for (int k : c.s->byBay[q]) if (c.s->placed[k]) load += pb.workload[k];
                totalLoad += load; maximum = std::max(maximum, load);
            }
            candidate.context[LoadBalance + 1] =
                1.0 - std::min(1.0, maximum / std::max(1.0, totalLoad));
            const double objectiveScore = detail::unit(-candidate.delta / scale);
            const double assignmentScore = candidate.context[Preference + 1];
            const double timeScore = candidate.context[DueSlack + 1];
            const double loadScore = candidate.context[LoadBalance + 1];
            const double layoutScore = candidate.context[ResidualArea + 1] +
                candidate.context[Fragmentation + 1] + candidate.context[WallContact + 1];
            const double pressureScore = candidate.context[EventPressure + 1];
            // Exact objective change remains dominant.  The deterministic terms
            // make tied or near-tied objective candidates sensitive to assignment,
            // time, load, and future packing consequences from the live state.
            candidate.learned = 4.0 * objectiveScore + 0.16 * assignmentScore +
                0.12 * timeScore + 0.12 * loadScore + 0.04 * layoutScore -
                0.08 * pressureScore + 0.10 * detail::unit(model.predict(candidate.context));
            candidate.exploration = candidate.learned + std::max(0.0, std::min(1.0,
                cfg.exploration)) * model.uncertainty(candidate.context);
            candidate.rank = candidate.learned;
            detail::retain(pool, candidate, retainCap);
            candidate.rank = candidate.exploration;
            detail::retain(explorationPool, candidate, retainCap);
        };
        const int attempts = candidateCap * std::max(1, cfg.strataAttemptsFactor);
        const int orientations = (int)pb.geo[block].orients.size();
        for (int attempt = 0; attempt < attempts && examined < candidateCap; ++attempt) {
            if (repair::deadlineExpired(c)) {
                out.timedOut = true; *c.s = original; detail::rebuild(pb, c); return false;
            }
            // Independent deterministic permutations give every marginal stratum
            // (time, bay, orientation, contact class) equal early representation.
            const int ti = (int)(detail::mix((uint64_t)attempt + 0x9e37ULL) % times.size());
            const int bi = (int)(detail::mix((uint64_t)attempt + 0x85ebULL) % bays.size());
            const int oi = (int)(detail::mix((uint64_t)attempt + 0xc2b2ULL) % orientations);
            const int contactClass = attempt & 1;
            const int entry = times[ti], bay = bays[bi], finish = entry + pb.P[block];
            repair::cornerSets(c, block, oi, bay, entry, finish, baseXs, baseYs);
            xs = baseXs; ys = baseYs;
            repair::shapeContactSets(c, block, oi, bay, entry, finish, xs, ys,
                                     cfg.contactAxisCap ? cfg.contactAxisCap : 96);
            const size_t pointCount = xs.size() * ys.size();
            if (!pointCount) continue;
            ++out.strataVisited;
            const size_t start = (size_t)(detail::mix((uint64_t)attempt + 0x27d4ULL) % pointCount);
            // A bounded local probe finds the requested contact class without
            // materializing an unbounded Cartesian product.
            for (size_t probe = 0; probe < std::min<size_t>(8, pointCount); ++probe) {
                const size_t point = (start + probe) % pointCount;
                const int x = xs[point % xs.size()], y = ys[point / xs.size()];
                const bool isContact = !detail::contains(baseXs, x) || !detail::contains(baseYs, y);
                if ((int)isContact != contactClass) continue;
                consider(bay, entry, oi, x, y, contactClass);
                break;
            }
        }
        if (examined >= candidateCap) out.candidateLimitReached = true;
        if (pool.empty() || repair::deadlineExpired(c)) { out.timedOut = repair::deadlineExpired(c); *c.s = original; detail::rebuild(pb, c); return false; }
        const double explorationRate = std::max(0.0, std::min(1.0, cfg.exploration));
        const bool explore = explorationRate > 0.0 &&
            detail::mix(model.decisions() + (uint64_t)(block + 1) * 0x9e3779b9ULL) % 10000ULL <
            (uint64_t)std::lround(explorationRate * 10000.0);
        const detail::Candidate selected = explore ? explorationPool.front() : pool.front();
        if (!detail::exactCandidate(c, block, selected.bay, selected.ori, selected.x, selected.y, selected.entry, trial) || repair::deadlineExpired(c)) { out.timedOut = repair::deadlineExpired(c); *c.s = original; detail::rebuild(pb, c); return false; }
        *c.s = trial; c.oc->stamp(pb, *c.s, block, true);
        Feedback feedback; feedback.context = selected.context; feedback.scale = scale; feedback.valid = true;
        model.observe(feedback, selected.delta); model.noteDecision();
        if (explore) ++out.explorationDecisions; else ++out.learnedDecisions;
    }
    if (repair::deadlineExpired(c)) { out.timedOut = true; *c.s = original; detail::rebuild(pb, c); return false; }
    const bool exact = core::stateExactEvents(pb, *c.s);
    const double finalObjective = core::evaluate(pb, *c.s).total;
    out.objectiveDelta = finalObjective - originalObjective;
    if (!exact || !(out.objectiveDelta < -1e-9)) {
        if (!exact) ++out.checkerRejected;
        *c.s = original; detail::rebuild(pb, c);
        return false;
    }
    out.completed = true; out.completedRepairs = 1;
    ++out.acceptedImprovements;
    return true;
}

inline bool insertBest(repair::Ctx& c, int block, Model& model, const Config& cfg = Config(), Result* result = nullptr) {
    return repairBlocks(c, std::vector<int>{block}, model, cfg, result);
}
// Orders a 3-8 block removal cohort by the cost of losing its best bay and by
// temporal urgency, then delegates to the same transactional exact repair.
inline bool repairCohort(repair::Ctx& c, std::vector<int> blocks, Model& model,
                         const Config& cfg = Config(), const CohortConfig& cohort = CohortConfig(),
                         Result* result = nullptr) {
    const int minimum = std::max(3, cohort.minBlocks);
    const int maximum = std::min(8, std::max(minimum, cohort.maxBlocks));
    if (!c.pb || (int)blocks.size() < minimum || (int)blocks.size() > maximum) return false;
    const core::Problem& pb = *c.pb;
    std::vector<char> seen(pb.n, 0);
    std::vector<std::pair<double, int>> ranked;
    ranked.reserve(blocks.size());
    for (int block : blocks) {
        if (block < 0 || block >= pb.n || seen[block] || c.s->placed[block]) return false;
        seen[block] = 1;
        double best = -std::numeric_limits<double>::infinity();
        double second = -std::numeric_limits<double>::infinity();
        for (int bay = 0; bay < pb.m; ++bay) {
            const double preference = pb.pref[block][bay];
            if (preference > best) { second = best; best = preference; }
            else if (preference > second) second = preference;
        }
        if (!std::isfinite(second)) second = best;
        const double preferenceRegret = (best - second) /
            std::max(1.0, std::fabs(pb.Smax[block]));
        const double slack = (double)(pb.D[block] - pb.R[block] - pb.P[block]) /
            std::max(1, c.horizon);
        ranked.push_back({cohort.preferenceRegretWeight * preferenceRegret -
                          cohort.urgencyWeight * slack, block});
    }
    std::sort(ranked.begin(), ranked.end(), [](const std::pair<double, int>& a,
                                                const std::pair<double, int>& b) {
        return a.first != b.first ? a.first > b.first : a.second < b.second;
    });
    for (size_t i = 0; i < ranked.size(); ++i) blocks[i] = ranked[i].second;
    const bool completed = repairBlocks(c, blocks, model, cfg, result);
    if (result) result->cohortOrderingDecisions += (int)blocks.size();
    return completed;
}

} // namespace repair_learned

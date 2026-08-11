// kernel_event_order.hpp -- exact-audited local search over bay event orders.
#pragma once

#include "alns.hpp"
#include <algorithm>
#include <chrono>
#include <cstdint>
#include <limits>
#include <numeric>
#include <random>
#include <vector>

namespace kernel_event_order {

struct Result {
    long windows = 0;
    long adjacentSwaps = 0;
    long tokenMoves = 0;
    long feasibleSchedules = 0;
    long exactAudits = 0;
    long acceptedImprovements = 0;
    double initialObjective = std::numeric_limits<double>::infinity();
    double bestObjective = std::numeric_limits<double>::infinity();
    double objectiveGain = 0.0;
    bool improved = false;
    bool deadlineHit = false;
};

namespace detail {

inline bool completeExact(const core::Problem& pb, core::Sol& s) {
    if ((int)s.placed.size() != pb.n || (int)s.byBay.size() != pb.m) return false;
    for (int i = 0; i < pb.n; ++i) {
        if (!s.placed[i] || s.bay[i] < 0 || s.bay[i] >= pb.m ||
            s.ori[i] < 0 || s.ori[i] >= (int)pb.geo[i].orients.size() ||
            s.e[i] < pb.R[i] || s.f[i] != s.e[i] + pb.P[i]) return false;
    }
    s.rebuildIdx(pb.m);
    return core::stateExactEvents(pb, s) && alns::audit(pb, s, nullptr, true) == 0;
}

struct Event {
    int block = -1;
    bool entry = false;
};

inline void eventOrder(const core::Sol& s, int bay, std::vector<Event>& order) {
    order.clear();
    order.reserve(2 * s.byBay[bay].size());
    for (int i : s.byBay[bay]) {
        order.push_back({i, true});
        order.push_back({i, false});
    }
    std::sort(order.begin(), order.end(), [&](const Event& a, const Event& b) {
        const int ta = a.entry ? s.e[a.block] : s.f[a.block];
        const int tb = b.entry ? s.e[b.block] : s.f[b.block];
        if (ta != tb) return ta < tb;
        if (a.entry != b.entry) return !a.entry; // EXIT before ENTRY at a tie.
        return a.block < b.block;
    });
}

inline bool replayBay(const core::Problem& pb, core::Sol& s,
                      const std::vector<Event>& order, int horizon) {
    std::vector<uint8_t> entered(pb.n, 0);
    int clock = 0;
    for (const Event& event : order) {
        const int i = event.block;
        if (event.entry) {
            if (entered[i]) return false;
            s.e[i] = std::max(clock, pb.R[i]);
            clock = s.e[i];
            entered[i] = 1;
        } else {
            if (!entered[i]) return false;
            s.f[i] = std::max(clock, s.e[i] + pb.P[i]);
            if (s.f[i] > horizon) return false;
            clock = s.f[i];
        }
    }
    return true;
}

} // namespace detail

template<class EngineT>
Result run(EngineT& engine, double seconds, uint64_t seed) {
    using Clock = std::chrono::steady_clock;
    Result out;
    const core::Problem& pb = engine.pb;
    const core::Sol saved = engine.sol;
    out.initialObjective = out.bestObjective = core::evaluate(pb, saved).total;
    if (seconds <= 0.0 || pb.n == 0 || pb.m == 0) return out;

    core::Sol best = saved;
    if (!detail::completeExact(pb, best)) return out;
    const auto deadline = Clock::now() + std::chrono::duration_cast<Clock::duration>(
        std::chrono::duration<double>(seconds));
    std::mt19937_64 rng(seed ^ 0xd1b54a32d192ed03ULL);
    std::vector<int> bays(pb.m);
    std::iota(bays.begin(), bays.end(), 0);

    bool changed = true;
    while (changed && Clock::now() < deadline) {
        changed = false;
        std::shuffle(bays.begin(), bays.end(), rng);
        for (int bay : bays) {
            if (Clock::now() >= deadline) break;
            std::vector<detail::Event> order;
            detail::eventOrder(best, bay, order);
            auto tryOrder = [&](const std::vector<detail::Event>& events) {
                core::Sol candidate = best;
                if (!detail::replayBay(pb, candidate, events, engine.horizon_)) return false;
                candidate.rebuildIdx(pb.m);
                ++out.exactAudits;
                if (!core::stateExactEvents(pb, candidate) ||
                    alns::audit(pb, candidate, nullptr, true) != 0) return false;
                ++out.feasibleSchedules;
                // Assignment and geometry stay fixed, so z2/z3 are invariant
                // and strict total improvement is a tardiness improvement.
                const double objective = core::evaluate(pb, candidate).total;
                if (objective + 1e-12 >= out.bestObjective) return false;
                out.objectiveGain += out.bestObjective - objective;
                out.bestObjective = objective;
                out.improved = true;
                ++out.acceptedImprovements;
                best = std::move(candidate);
                return true;
            };
            for (size_t p = 0; p + 1 < order.size() && Clock::now() < deadline; ++p) {
                ++out.windows;
                ++out.adjacentSwaps;
                ++out.tokenMoves;
                std::vector<detail::Event> swapped = order;
                std::swap(swapped[p], swapped[p + 1]);
                if (tryOrder(swapped)) { changed = true; break; }

                // Move one token across a bounded event window; replay rejects
                // any sequence that exits a block before its own entry.
                for (size_t q = p + 2; q < order.size() && q <= p + 3 &&
                                    Clock::now() < deadline; ++q) {
                    ++out.windows;
                    ++out.tokenMoves;
                    std::vector<detail::Event> moved = order;
                    const detail::Event event = moved[p];
                    moved.erase(moved.begin() + (long)p);
                    moved.insert(moved.begin() + (long)q, event);
                    if (tryOrder(moved)) { changed = true; break; }
                }
                if (changed) break;
            }
            if (changed) break;
        }
    }
    out.deadlineHit = Clock::now() >= deadline;
    // The engine remains byte-for-byte untouched until a strictly better,
    // exact-audited incumbent is available.
    if (out.improved) {
        engine.sol = std::move(best);
        engine.buildOcc(engine.horizon_);
    }
    return out;
}

} // namespace kernel_event_order

// kernel_island_memetic_v2.hpp -- exact-audited four-island memetic search.
#pragma once

#include "alns.hpp"
#include <algorithm>
#include <array>
#include <chrono>
#include <cstdint>
#include <cmath>
#include <limits>
#include <iterator>
#include <random>
#include <unordered_map>
#include <utility>
#include <vector>

namespace kernel_island_memetic_v2 {

struct Result {
    long populationSize = 0;
    double diversity = 0.0;
    double peakDiversity = 0.0;
    long offspring = 0;
    long crossovers = 0;
    long migrations = 0;
    long exactAudits = 0;
    long feasibleCandidates = 0;
    long acceptedGains = 0;
    long archiveEntries = 0;
    long deadlineChecks = 0;
    double initialObjective = 0.0;
    double bestObjective = 0.0;
    double elapsedSeconds = 0.0;
    bool improved = false;
    bool deadlineHit = false;
};

namespace detail {
constexpr size_t Islands = 4;
constexpr size_t IslandPopulation = 4;
constexpr size_t ArchiveLimit = 32;

inline uint64_t mix(uint64_t x) {
    x += 0x9e3779b97f4a7c15ull;
    x = (x ^ (x >> 30)) * 0xbf58476d1ce4e5b9ull;
    x = (x ^ (x >> 27)) * 0x94d049bb133111ebull;
    return x ^ (x >> 31);
}

inline double objective(const core::Problem& pb, const core::Sol& s) {
    return core::evaluate(pb, s).total;
}

inline double distance(const core::Problem& pb, const core::Sol& a, const core::Sol& b) {
    if (pb.n == 0) return 0.0;
    int changed = 0;
    for (int i = 0; i < pb.n; ++i)
        changed += a.bay[i] != b.bay[i] || a.ori[i] != b.ori[i] ||
                   a.e[i] != b.e[i] || a.x[i] != b.x[i] || a.y[i] != b.y[i];
    return (double)changed / (double)pb.n;
}

inline double diversity(const core::Problem& pb, const std::vector<core::Sol>& pop) {
    if (pop.size() < 2) return 0.0;
    double total = 0.0;
    long pairs = 0;
    for (size_t i = 0; i < pop.size(); ++i)
        for (size_t j = 0; j < i; ++j) { total += distance(pb, pop[i], pop[j]); ++pairs; }
    return total / (double)pairs;
}

inline void rebuild(const core::Problem& pb, core::Sol& s, int horizon, occ::OccIndex& oc) {
    s.rebuildIdx(pb.m);
    oc.build(pb, horizon);
    for (int i = 0; i < pb.n; ++i) if (s.placed[i]) oc.stamp(pb, s, i, true);
}

inline repair::Ctx context(const core::Problem& pb, core::Sol& s, occ::OccIndex& oc,
                           int horizon, std::chrono::steady_clock::time_point deadline,
                           uint64_t seed) {
    repair::Ctx c{};
    c.pb = &pb; c.s = &s; c.oc = &oc;
    c.horizon = horizon;
    c.exactEvents = true;
    c.hasDeadline = true;
    c.deadline = deadline;
    c.rng.seed((uint32_t)(seed ^ (seed >> 32)));
    return c;
}

inline bool audited(const core::Problem& pb, core::Sol& s, int horizon, long& audits) {
    ++audits;
    if ((int)s.placed.size() != pb.n || (int)s.byBay.size() != pb.m) return false;
    for (int i = 0; i < pb.n; ++i)
        if (!s.placed[i] || s.bay[i] < 0 || s.bay[i] >= pb.m ||
            s.ori[i] < 0 || s.ori[i] >= (int)pb.geo[i].orients.size() ||
            s.e[i] < pb.R[i] || s.f[i] != s.e[i] + pb.P[i] || s.f[i] > horizon)
            return false;
    s.rebuildIdx(pb.m);
    return alns::audit(pb, s, nullptr, true) == 0 && core::stateExactEvents(pb, s);
}

inline uint64_t descriptor(const core::Problem& pb, const core::Sol& s) {
    uint64_t key = mix((uint64_t)pb.n ^ ((uint64_t)pb.m << 32));
    for (int i = 0; i < pb.n; ++i) {
        uint64_t assignment = (uint64_t)(uint32_t)s.bay[i] | ((uint64_t)(uint32_t)s.ori[i] << 16);
        uint64_t event = (uint64_t)(uint32_t)s.e[i] | ((uint64_t)(uint32_t)s.f[i] << 32);
        uint64_t geometry = (uint64_t)(uint32_t)s.x[i] | ((uint64_t)(uint32_t)s.y[i] << 32);
        key ^= mix(assignment ^ mix(event) ^ mix(geometry) ^ (uint64_t)i);
        key = (key << 9) | (key >> 55);
    }
    return key;
}

inline bool constructPerturbation(const core::Problem& pb, const core::Sol& base, int horizon,
                                  int flavor, std::chrono::steady_clock::time_point deadline,
                                  uint64_t seed, core::Sol& out, long& audits) {
    out = base;
    occ::OccIndex oc;
    rebuild(pb, out, horizon, oc);
    repair::Ctx c = context(pb, out, oc, horizon, deadline, seed);
    std::vector<int> ids(pb.n);
    for (int i = 0; i < pb.n; ++i) ids[i] = i;
    std::sort(ids.begin(), ids.end(), [&](int a, int b) {
        const int sa = pb.D[a] - pb.R[a] - pb.P[a];
        const int sb = pb.D[b] - pb.R[b] - pb.P[b];
        if (flavor == 0) return sa != sb ? sa < sb : a < b;
        if (flavor == 1) return pb.P[a] != pb.P[b] ? pb.P[a] > pb.P[b] : sa < sb;
        if (flavor == 2) return base.bay[a] != base.bay[b] ? base.bay[a] < base.bay[b] : sa < sb;
        return mix(seed ^ (uint64_t)a) < mix(seed ^ (uint64_t)b);
    });
    const int q = std::max(2, std::min(pb.n - 1, std::max(2, pb.n / 5)));
    std::vector<int> removed;
    for (int k = 0; k < q; ++k) removed.push_back(ids[(k + flavor * q / 2) % pb.n]);
    for (int i : removed) repair::removeBlock(c, i);
    if (flavor & 1) std::reverse(removed.begin(), removed.end());
    for (int i : removed) {
        if (repair::deadlineExpired(c) || !alns::insertByMode(c, i, -1, flavor % 4)) {
            out = base; return false;
        }
    }
    return audited(pb, out, horizon, audits);
}

// Select whole bays from each parent.  Events that are selected by both bays are
// resolved before placement by slack and deterministic seed, never by overwriting.
inline bool baySetCrossover(const core::Problem& pb, const core::Sol& a, const core::Sol& b,
                            int horizon, std::chrono::steady_clock::time_point deadline,
                            uint64_t seed, core::Sol& out, long& audits) {
    std::vector<uint8_t> takeA(pb.m, 0), ownerA(pb.n, 0), seenA(pb.n, 0), seenB(pb.n, 0);
    for (int bay = 0; bay < pb.m; ++bay) takeA[bay] = (mix(seed ^ (uint64_t)bay) & 1ull) != 0;
    for (int bay = 0; bay < pb.m; ++bay) {
        for (int i : a.byBay[bay]) if (takeA[bay]) seenA[i] = 1;
        for (int i : b.byBay[bay]) if (!takeA[bay]) seenB[i] = 1;
    }
    for (int i = 0; i < pb.n; ++i) {
        if (seenA[i] && !seenB[i]) ownerA[i] = 1;
        else if (!seenA[i] && seenB[i]) ownerA[i] = 0;
        else if (seenA[i] && seenB[i]) {
            const int slackA = pb.D[i] - a.e[i] - pb.P[i];
            const int slackB = pb.D[i] - b.e[i] - pb.P[i];
            ownerA[i] = slackA != slackB ? slackA < slackB : ((mix(seed ^ (uint64_t)i) & 1ull) != 0);
        } else ownerA[i] = (mix(seed + (uint64_t)i) & 1ull) != 0;
    }
    out.init(pb.n, pb.m);
    occ::OccIndex oc;
    oc.build(pb, horizon);
    repair::Ctx c = context(pb, out, oc, horizon, deadline, seed);
    std::vector<int> order(pb.n);
    for (int i = 0; i < pb.n; ++i) order[i] = i;
    std::sort(order.begin(), order.end(), [&](int x, int y) {
        const core::Sol& sx = ownerA[x] ? a : b;
        const core::Sol& sy = ownerA[y] ? a : b;
        const int lx = pb.D[x] - sx.e[x] - pb.P[x], ly = pb.D[y] - sy.e[y] - pb.P[y];
        return lx != ly ? lx < ly : x < y;
    });
    std::vector<int> missing;
    for (int i : order) {
        if (repair::deadlineExpired(c)) return false;
        const core::Sol& src = ownerA[i] ? a : b;
        out.bay[i] = src.bay[i]; out.ori[i] = src.ori[i]; out.x[i] = src.x[i]; out.y[i] = src.y[i];
        out.e[i] = src.e[i]; out.f[i] = src.f[i]; out.placed[i] = 1; out.addIdx(i);
        if (repair::placementFeasible(c, i)) oc.stamp(pb, out, i, true);
        else { out.delIdx(i); out.placed[i] = 0; missing.push_back(i); }
    }
    for (int i : missing)
        if (repair::deadlineExpired(c) || !alns::insertByMode(c, i, -1, 3)) return false;
    return audited(pb, out, horizon, audits);
}

inline bool exactPolish(const core::Problem& pb, core::Sol& s, int horizon,
                        std::chrono::steady_clock::time_point deadline, uint64_t seed,
                        long& audits) {
    const core::Sol backup = s;
    occ::OccIndex oc;
    rebuild(pb, s, horizon, oc);
    repair::Ctx c = context(pb, s, oc, horizon, deadline, seed);
    if (repair::deadlineExpired(c)) return false;
    alns::RunParams rp;
    rp.qloF = 0.04; rp.qhiF = 0.14; rp.bigQProb = 0.08;
    rp.opMask = 0x3FF; rp.repairMode = 3; rp.maxIters = 9;
    const double remaining = std::chrono::duration<double>(deadline - std::chrono::steady_clock::now()).count();
    alns::run(c, std::min(0.035, std::max(0.0, remaining)), rp, seed, 1);
    if (repair::deadlineExpired(c) || !audited(pb, s, horizon, audits)) { s = backup; return false; }
    return true;
}

struct Member { core::Sol sol; double value = std::numeric_limits<double>::infinity(); };
struct Island { std::vector<Member> pop; };
struct ArchiveEntry { core::Sol sol; double value; };

inline bool admit(const core::Problem& pb, Island& island, Member child) {
    if (island.pop.empty()) { island.pop.push_back(std::move(child)); return true; }
    double nearest = 1.0;
    size_t nearestIndex = 0;
    for (size_t i = 0; i < island.pop.size(); ++i) {
        const double d = distance(pb, child.sol, island.pop[i].sol);
        if (d < nearest) { nearest = d; nearestIndex = i; }
    }
    if (nearest < 0.025 && child.value >= island.pop[nearestIndex].value - 1e-9) return false;
    if (island.pop.size() < IslandPopulation) { island.pop.push_back(std::move(child)); return true; }
    size_t replace = 0;
    double worst = -std::numeric_limits<double>::infinity();
    for (size_t i = 0; i < island.pop.size(); ++i) {
        double local = 1.0;
        for (size_t j = 0; j < island.pop.size(); ++j) if (i != j)
            local = std::min(local, distance(pb, island.pop[i].sol, island.pop[j].sol));
        const double pressure = island.pop[i].value + 0.05 * (1.0 - local);
        if (pressure > worst) { worst = pressure; replace = i; }
    }
    const bool improves = child.value < island.pop[replace].value - 1e-9;
    const bool diversifies = nearest >= 0.08;
    if (!improves && !diversifies) return false;
    island.pop[replace] = std::move(child);
    return true;
}
} // namespace detail

template<class EngineT>
Result run(EngineT& engine, double seconds, uint64_t seed) {
    using Clock = std::chrono::steady_clock;
    Result out;
    const core::Problem& pb = engine.pb;
    const int horizon = engine.horizon_;
    const core::Sol entry = engine.sol;
    core::Sol checked = entry;
    if (seconds <= 0.0 || pb.n <= 1 || pb.m <= 0 || horizon <= 0 ||
        !detail::audited(pb, checked, horizon, out.exactAudits)) return out;
    const auto started = Clock::now();
    const auto deadline = started + std::chrono::duration_cast<Clock::duration>(std::chrono::duration<double>(seconds));
    out.initialObjective = detail::objective(pb, entry);
    out.bestObjective = out.initialObjective;
    core::Sol best = entry;
    std::array<detail::Island, detail::Islands> islands;
    std::unordered_map<uint64_t, detail::ArchiveEntry> archive;
    auto archiveInsert = [&](const detail::Member& member) {
        const uint64_t key = detail::descriptor(pb, member.sol);
        auto found = archive.find(key);
        if (found == archive.end() || member.value < found->second.value - 1e-9)
            archive[key] = {member.sol, member.value};
        if (archive.size() > detail::ArchiveLimit) {
            auto worst = archive.begin();
            for (auto it = archive.begin(); it != archive.end(); ++it)
                if (it->second.value > worst->second.value) worst = it;
            archive.erase(worst);
        }
    };
    std::array<double, 3> credit{{1.0, 1.0, 1.0}};
    std::array<long, 3> pulls{{0, 0, 0}};
    auto recordDiversity = [&] {
        std::vector<core::Sol> members;
        for (const detail::Island& island : islands)
            for (const detail::Member& member : island.pop) members.push_back(member.sol);
        out.peakDiversity = std::max(out.peakDiversity, detail::diversity(pb, members));
    };
    for (size_t island = 0; island < detail::Islands; ++island) {
        core::Sol candidate;
        if (island == 0 || !detail::constructPerturbation(pb, entry, horizon, (int)island, deadline,
                                                            detail::mix(seed + island), candidate, out.exactAudits))
            candidate = entry;
        detail::Member member{candidate, detail::objective(pb, candidate)};
        islands[island].pop.push_back(member);
        archiveInsert(member);
    }
    recordDiversity();
    long generation = 0;
    while (Clock::now() < deadline) {
        ++out.deadlineChecks;
        const size_t islandId = (size_t)(generation % (long)detail::Islands);
        detail::Island& island = islands[islandId];
        if (island.pop.empty()) break;
        size_t op = 0;
        double bestBandit = -std::numeric_limits<double>::infinity();
        for (size_t k = 0; k < credit.size(); ++k) {
            const double bandit = credit[k] / (double)std::max(1L, pulls[k]) +
                0.25 * std::sqrt(std::log(2.0 + generation) / (double)std::max(1L, pulls[k]));
            if (bandit > bestBandit) { bestBandit = bandit; op = k; }
        }
        ++pulls[op];
        size_t a = (size_t)(detail::mix(seed ^ (uint64_t)generation) % island.pop.size());
        size_t b = a;
        double farthest = -1.0;
        for (size_t i = 0; i < island.pop.size(); ++i) if (i != a) {
            const double d = detail::distance(pb, island.pop[a].sol, island.pop[i].sol);
            if (d > farthest) { farthest = d; b = i; }
        }
        const detail::Member* pa = &island.pop[a];
        const core::Sol* pbSol = &island.pop[b].sol;
        double pbValue = island.pop[b].value;
        if (op == 1 && !archive.empty()) {
            auto it = archive.begin(); std::advance(it, (long)(detail::mix(seed + generation) % archive.size()));
            pbSol = &it->second.sol;
            pbValue = it->second.value;
        } else if (op == 2) {
            const detail::Island& foreign = islands[(islandId + 1) % detail::Islands];
            if (!foreign.pop.empty()) {
                const auto elite = std::min_element(foreign.pop.begin(), foreign.pop.end(),
                    [](const detail::Member& x, const detail::Member& y) { return x.value < y.value; });
                pbSol = &elite->sol;
                pbValue = elite->value;
            }
        }
        core::Sol child;
        ++out.crossovers;
        if (!detail::baySetCrossover(pb, pa->sol, *pbSol, horizon, deadline,
                                     detail::mix(seed ^ (uint64_t)generation), child, out.exactAudits)) break;
        ++out.offspring;
        if (!detail::exactPolish(pb, child, horizon, deadline, detail::mix(seed + generation + 17), out.exactAudits)) break;
        ++out.feasibleCandidates;
        const double value = detail::objective(pb, child);
        const double parentValue = std::min(pa->value, pbValue);
        const double gain = std::max(0.0, parentValue - value);
        credit[op] = 0.85 * credit[op] + 0.15 * gain / std::max(1.0, std::fabs(parentValue));
        detail::Member member{child, value};
        archiveInsert(member);
        if (detail::admit(pb, island, std::move(member))) {
            if (value < out.bestObjective - 1e-9) {
                best = child; out.bestObjective = value; ++out.acceptedGains;
            }
        }
        recordDiversity();
        if ((generation + 1) % 6 == 0 && Clock::now() < deadline) {
            const size_t to = (islandId + 1) % detail::Islands;
            const auto elite = std::min_element(island.pop.begin(), island.pop.end(),
                [](const detail::Member& x, const detail::Member& y) { return x.value < y.value; });
            if (elite != island.pop.end()) {
                core::Sol migrant = elite->sol;
                if (detail::audited(pb, migrant, horizon, out.exactAudits) &&
                    detail::admit(pb, islands[to], {migrant, elite->value})) ++out.migrations;
            }
        }
        ++generation;
    }
    std::vector<core::Sol> all;
    for (const detail::Island& island : islands)
        for (const detail::Member& member : island.pop) all.push_back(member.sol);
    out.populationSize = (long)all.size();
    out.diversity = detail::diversity(pb, all);
    out.peakDiversity = std::max(out.peakDiversity, out.diversity);
    out.archiveEntries = (long)archive.size();
    out.elapsedSeconds = std::chrono::duration<double>(Clock::now() - started).count();
    out.deadlineHit = Clock::now() >= deadline;
    out.improved = out.bestObjective < out.initialObjective - 1e-9;
    if (out.improved) {
        core::Sol commit = best;
        if (detail::audited(pb, commit, horizon, out.exactAudits) &&
            detail::objective(pb, commit) < out.initialObjective - 1e-9) {
            engine.sol = commit;
            detail::rebuild(pb, engine.sol, horizon, engine.oc);
        } else { out.bestObjective = out.initialObjective; out.improved = false; }
    }
    return out;
}

} // namespace kernel_island_memetic_v2

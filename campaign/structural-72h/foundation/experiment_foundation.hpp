// experiment_foundation.hpp -- sealed campaign-only exact experiment ABI.
#pragma once
#include "repair.hpp"
#include <array>
#include <chrono>
#include <cstdint>
#include <functional>
#include <iomanip>
#include <set>
#include <sstream>
#include <string>
#include <unordered_set>
#include <utility>
#include <vector>

namespace experiment {
constexpr uint32_t ABI_VERSION = 1;

enum class Outcome : uint8_t {
    COMPLETE, CAPPED, UNKNOWN, INVALID, NO_IMPROVEMENT, IMPROVED, TIMEOUT,
    DEADLINE_SKIP
};

struct OrderedEvent {
    int time = 0, bay = 0, block = 0;
    bool entry = false;
};

struct MechanismRequest {
    uint32_t abiVersion = ABI_VERSION;
    std::string id;
    int64_t secondsNs = 0;
    uint32_t seed = 0;
    std::array<int64_t, 8> limits{};
    bool armEnabled = false;
};

struct MechanismResult {
    uint32_t abiVersion = ABI_VERSION;
    std::string id;
    Outcome outcome = Outcome::INVALID;
    int64_t initialObjective = 0, bestObjective = 0;
    std::array<int64_t, 24> counters{};
    std::vector<OrderedEvent> orderedEvents;
    std::string stateHash, traceHash, diagnosticCode;
};

struct ExperimentContext {
    const core::Problem& problem;
    core::Sol& solution;
    occ::OccIndex& occupancy;
    repair::Ctx& repairContext;
    std::vector<std::vector<int>>& candidateBuffers;
    std::vector<OrderedEvent>& eventTrace;
    bool& experimentActive;
};

using RunFunction = MechanismResult (*)(ExperimentContext&,
                                        const MechanismRequest&);
struct MechanismDescriptor {
    const char* id;
    uint32_t abiVersion;
    const char* sourceSha;
    RunFunction runFunction;
};

#ifndef EXPERIMENT_REGISTRY_HEADER
inline const std::vector<MechanismDescriptor>& generatedRegistry() {
    static const std::vector<MechanismDescriptor> empty;
    return empty;
}
#else
#include EXPERIMENT_REGISTRY_HEADER
#endif

namespace detail {
inline const MechanismDescriptor* findDescriptor(const std::string& id,
                                                  std::string& error) {
    const auto& registry = generatedRegistry();
    std::string previous;
    for (const auto& descriptor : registry) {
        if (!descriptor.id || !descriptor.sourceSha || !descriptor.runFunction ||
            descriptor.abiVersion != ABI_VERSION) {
            error = "REGISTRY_INVALID";
            return nullptr;
        }
        const std::string current(descriptor.id);
        if (!previous.empty() && current <= previous) {
            error = current == previous ? "DUPLICATE_DESCRIPTOR"
                                        : "REGISTRY_NOT_SORTED";
            return nullptr;
        }
        previous = current;
        if (current == id) return &descriptor;
    }
    error = "UNKNOWN_ID";
    return nullptr;
}

inline std::string hashBytes(const std::string& bytes) {
    uint64_t value = 1469598103934665603ULL;
    for (unsigned char byte : bytes) {
        value ^= byte;
        value *= 1099511628211ULL;
    }
    std::ostringstream out;
    out << std::hex << std::setfill('0') << std::setw(16) << value;
    return out.str();
}

template <class T>
inline void appendVector(std::ostringstream& out, const std::vector<T>& values) {
    out << values.size() << ':';
    for (const auto& value : values) out << value << ',';
    out << ';';
}

inline std::string serializeSolution(const core::Sol& solution) {
    std::ostringstream out;
    appendVector(out, solution.placed); appendVector(out, solution.bay);
    appendVector(out, solution.ori); appendVector(out, solution.x);
    appendVector(out, solution.y); appendVector(out, solution.e);
    appendVector(out, solution.f);
    out << solution.byBay.size() << ':';
    for (const auto& bay : solution.byBay) appendVector(out, bay);
    return out.str();
}

inline std::string serializeOccupancy(const occ::OccIndex& occupancy) {
    std::ostringstream out;
    out << occupancy.Lmax << ',' << occupancy.T << ';';
    for (const auto& grid : occupancy.bay) {
        out << grid.W << ',' << grid.H << ',' << grid.T << ',' << grid.L << ','
            << grid.wpr << ';';
        appendVector(out, grid.bits);
    }
    for (const auto& area : occupancy.areaUsed) appendVector(out, area);
    for (const auto& masks : occupancy.masks)
        for (const auto& mask : masks) {
            out << mask.w << ',' << mask.h << ',' << mask.ox << ',' << mask.oy
                << ',' << mask.wpr << ',' << mask.any << ';';
            appendVector(out, mask.rows);
        }
    appendVector(out, occupancy.nOri); appendVector(out, occupancy.nLay);
    return out.str();
}

inline std::string serializeEvents(const std::vector<OrderedEvent>& events) {
    std::ostringstream out;
    for (const auto& event : events)
        out << event.time << ',' << event.bay << ',' << event.entry << ','
            << event.block << ';';
    return out.str();
}
} // namespace detail

struct ExperimentSnapshot {
    core::Sol solution;
    occ::OccIndex occupancy;
    repair::Ctx context;
    std::vector<std::vector<int>> candidateBuffers;
    std::vector<OrderedEvent> eventTrace;
    bool experimentActive;

    explicit ExperimentSnapshot(const ExperimentContext& source)
        : solution(source.solution), occupancy(source.occupancy),
          context(source.repairContext), candidateBuffers(source.candidateBuffers),
          eventTrace(source.eventTrace), experimentActive(source.experimentActive) {}

    void restore(ExperimentContext& target) const {
        target.solution = solution;
        target.occupancy = occupancy;
        target.repairContext = context;
        target.candidateBuffers = candidateBuffers;
        target.eventTrace = eventTrace;
        target.experimentActive = experimentActive;
        target.repairContext.pb = &target.problem;
        target.repairContext.s = &target.solution;
        target.repairContext.oc = &target.occupancy;
    }

    std::string stateHash() const {
        std::ostringstream rng;
        rng << context.rng;
        return detail::hashBytes(detail::serializeSolution(solution) +
                                 detail::serializeOccupancy(occupancy) +
                                 rng.str() + detail::serializeEvents(eventTrace));
    }
};

enum class AuditStatus { COMPLETE, UNKNOWN, INVALID };
struct AuditResult {
    AuditStatus status = AuditStatus::INVALID;
    std::vector<OrderedEvent> events;
    std::string diagnosticCode;
};

namespace detail {
inline bool deadlineReached(
    const std::chrono::steady_clock::time_point* deadline) {
    return deadline && std::chrono::steady_clock::now() >= *deadline;
}

inline bool exactEntries(const core::Problem& problem, const core::Sol& solution,
                         int bay, const std::vector<int>& base,
                         const std::vector<int>& entries,
                         std::vector<int>& order,
                         const std::chrono::steady_clock::time_point* deadline,
                         bool& timedOut) {
    std::unordered_set<std::string> failed;
    std::function<bool(std::vector<uint8_t>&)> visit = [&](std::vector<uint8_t>& used) {
        if (detail::deadlineReached(deadline)) { timedOut = true; return false; }
        std::string key(used.begin(), used.end());
        if (failed.count(key)) return false;
        std::vector<int> present = base;
        size_t count = 0;
        for (size_t i = 0; i < entries.size(); ++i)
            if (used[i]) { present.push_back(entries[i]); ++count; }
        if (count == entries.size()) return true;
        std::vector<crane::Placement> placements;
        placements.reserve(present.size());
        for (int id : present)
            placements.push_back({&problem.geo[id].orients[solution.ori[id]],
                                  (double)solution.x[id], (double)solution.y[id]});
        for (size_t i = 0; i < entries.size(); ++i) {
            if (used[i]) continue;
            const int id = entries[i];
            const auto& geometry = problem.geo[id].orients[solution.ori[id]];
            if (!crane::entryOK(problem.bayW[bay], problem.bayH[bay], placements,
                                geometry, solution.x[id], solution.y[id])) continue;
            used[i] = 1; order.push_back(id);
            if (visit(used)) return true;
            order.pop_back(); used[i] = 0;
            if (timedOut) return false;
        }
        failed.insert(std::move(key));
        return false;
    };
    std::vector<uint8_t> used(entries.size(), 0);
    return visit(used);
}
} // namespace detail

inline AuditResult auditExact(
    const core::Problem& problem, const core::Sol& solution,
    const std::chrono::steady_clock::time_point* deadline = nullptr) {
    AuditResult result;
    if ((int)solution.placed.size() != problem.n ||
        (int)solution.byBay.size() != problem.m) {
        result.diagnosticCode = "STATE_SIZE"; return result;
    }
    std::set<int> times;
    for (int i = 0; i < problem.n; ++i) if (solution.placed[i]) {
        if (solution.bay[i] < 0 || solution.bay[i] >= problem.m ||
            solution.ori[i] < 0 ||
            solution.ori[i] >= (int)problem.geo[i].orients.size() ||
            solution.e[i] < problem.R[i] || solution.f[i] - solution.e[i] < problem.P[i]) {
            result.diagnosticCode = "BLOCK_DOMAIN"; return result;
        }
        const auto& geometry = problem.geo[i].orients[solution.ori[i]];
        if (solution.x[i] + geometry.bb.x0 < 0 ||
            solution.y[i] + geometry.bb.y0 < 0 ||
            solution.x[i] + geometry.bb.x1 > problem.bayW[solution.bay[i]] ||
            solution.y[i] + geometry.bb.y1 > problem.bayH[solution.bay[i]]) {
            result.diagnosticCode = "OUT_OF_BOUNDS"; return result;
        }
        times.insert(solution.e[i]); times.insert(solution.f[i]);
    }
    for (int i = 0; i < problem.n; ++i) if (solution.placed[i])
        for (int j = i + 1; j < problem.n; ++j)
            if (solution.placed[j] && solution.bay[i] == solution.bay[j] &&
                solution.e[i] < solution.f[j] && solution.e[j] < solution.f[i] &&
                core::restOverlap(problem, solution, i, j)) {
                result.diagnosticCode = "COLLISION"; return result;
            }
    for (int time : times) {
        std::vector<OrderedEvent> dayExits, dayEntries;
        for (int bay = 0; bay < problem.m; ++bay) {
            std::vector<int> present, exits, entries;
            for (int id : solution.byBay[bay]) if (solution.placed[id]) {
                if (solution.e[id] < time && time <= solution.f[id]) present.push_back(id);
                if (solution.f[id] == time) exits.push_back(id);
                if (solution.e[id] == time) entries.push_back(id);
            }
            std::sort(exits.begin(), exits.end()); std::sort(entries.begin(), entries.end());
            while (!exits.empty()) {
                if (detail::deadlineReached(deadline)) {
                    result.status = AuditStatus::UNKNOWN;
                    result.diagnosticCode = "DEADLINE"; return result;
                }
                bool progressed = false;
                for (size_t index = 0; index < exits.size(); ++index) {
                    const int id = exits[index];
                    auto at = std::find(present.begin(), present.end(), id);
                    if (at == present.end()) {
                        result.diagnosticCode = "EXIT_ABSENT"; return result;
                    }
                    std::vector<crane::Placement> placements;
                    for (int other : present)
                        placements.push_back({&problem.geo[other].orients[solution.ori[other]],
                                              (double)solution.x[other], (double)solution.y[other]});
                    if (!crane::exitOK(placements, (int)(at - present.begin()))) continue;
                    present.erase(at); dayExits.push_back({time, bay, id, false});
                    exits.erase(exits.begin() + index); progressed = true; break;
                }
                if (!progressed) {
                    result.diagnosticCode = "EXIT_BLOCKED"; return result;
                }
            }
            std::vector<int> entryOrder; bool timedOut = false;
            if (!detail::exactEntries(problem, solution, bay, present, entries,
                                      entryOrder, deadline, timedOut)) {
                result.status = timedOut ? AuditStatus::UNKNOWN : AuditStatus::INVALID;
                result.diagnosticCode = timedOut ? "DEADLINE" : "ENTRY_BLOCKED";
                return result;
            }
            for (int id : entryOrder) dayEntries.push_back({time, bay, id, true});
        }
        result.events.insert(result.events.end(), dayExits.begin(), dayExits.end());
        result.events.insert(result.events.end(), dayEntries.begin(), dayEntries.end());
    }
    result.status = AuditStatus::COMPLETE;
    result.diagnosticCode = "OK";
    return result;
}

inline MechanismResult runExperiment(ExperimentContext& context,
                                     const MechanismRequest& request) {
    MechanismResult result;
    result.id = request.id;
    if (request.abiVersion != ABI_VERSION || !request.armEnabled ||
        request.secondsNs < 0 || context.experimentActive) {
        result.diagnosticCode = context.experimentActive ? "NESTED_EXPERIMENT"
                                                        : "REQUEST_INVALID";
        return result;
    }
    std::string registryError;
    const auto* descriptor = detail::findDescriptor(request.id, registryError);
    if (!descriptor) { result.diagnosticCode = registryError; return result; }
    ExperimentSnapshot snapshot(context);
    context.experimentActive = true;
    try { result = descriptor->runFunction(context, request); }
    catch (...) {
        snapshot.restore(context);
        result.id = request.id; result.outcome = Outcome::INVALID;
        result.diagnosticCode = "EXCEPTION"; return result;
    }
    const auto audit = auditExact(context.problem, context.solution);
    const bool improved = result.outcome == Outcome::IMPROVED &&
                          result.bestObjective < result.initialObjective &&
                          audit.status == AuditStatus::COMPLETE;
    if (!improved) snapshot.restore(context);
    else { context.eventTrace = audit.events; context.experimentActive = false; }
    result.orderedEvents = improved ? audit.events : snapshot.eventTrace;
    result.stateHash = detail::hashBytes(detail::serializeSolution(context.solution) +
                                         detail::serializeOccupancy(context.occupancy));
    result.traceHash = detail::hashBytes(detail::serializeEvents(result.orderedEvents));
    if (audit.status != AuditStatus::COMPLETE && result.diagnosticCode.empty())
        result.diagnosticCode = audit.diagnosticCode;
    return result;
}
} // namespace experiment

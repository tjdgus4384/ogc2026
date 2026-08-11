#pragma once

#include "target_constructor.hpp"
#include <algorithm>
#include <chrono>
#include <cmath>
#include <functional>
#include <stdexcept>
#include <vector>

namespace target_constructor {
namespace corridor_access_detail {

struct Reservation {
    int bay;
    int entry;
    int exit;
};

struct CorridorProof {
    size_t constructorSnapshot;
    std::vector<int> bayFree;
    int globalFree;
    size_t reservationCount;
};

inline bool expired(int64_t deadlineNs) {
    return std::chrono::duration_cast<std::chrono::nanoseconds>(
               std::chrono::steady_clock::now().time_since_epoch()).count() >= deadlineNs;
}

inline Output solve(const RawProblem& problem, int64_t deadlineNs, bool reserveCorridor) {
    Constructor constructor(problem, deadlineNs);
    const size_t count = problem.blocks.size();
    std::vector<int> order(count), bayFree(problem.bays, 0);
    std::vector<Reservation> reservations;
    int globalFree = 0;
    for (size_t i = 0; i < count; ++i) order[i] = static_cast<int>(i);
    std::sort(order.begin(), order.end(), [&](int a, int b) {
        const Block& left = problem.blocks[a];
        const Block& right = problem.blocks[b];
        if (left.release != right.release) return left.release < right.release;
        if (left.due != right.due) return left.due < right.due;
        return a < b;
    });

    std::function<bool(size_t)> extend = [&](size_t depth) {
        if (expired(deadlineNs)) return false;
        if (depth == count) return constructor.complete();
        const int block = order[depth];
        const Block& source = problem.blocks[block];
        std::vector<int> bays(problem.bays);
        for (int bay = 0; bay < problem.bays; ++bay) bays[bay] = bay;
        std::stable_sort(bays.begin(), bays.end(), [&](int a, int b) {
            if (source.preference[a] != source.preference[b])
                return source.preference[a] > source.preference[b];
            return a < b;
        });
        for (int bay : bays) for (int orientation = 0;
             orientation < static_cast<int>(source.orientations.size()); ++orientation) {
            const Orientation& shape = source.orientations[orientation];
            const int minX = static_cast<int>(std::ceil(-shape.bounds.x0));
            const int minY = static_cast<int>(std::ceil(-shape.bounds.y0));
            const int maxX = static_cast<int>(std::floor(problem.bayWidth[bay] - shape.bounds.x1));
            const int maxY = static_cast<int>(std::floor(problem.bayHeight[bay] - shape.bounds.y1));
            if (minX > maxX || minY > maxY) continue;
            const int entry = std::max(source.release, reserveCorridor ? globalFree : bayFree[bay]);
            const int exit = entry + source.processing;
            const int xs[] = {minX, maxX};
            const int ys[] = {minY, maxY};
            for (int x : xs) for (int y : ys) {
                if (expired(deadlineNs)) return false;
                if (!craneEntry(problem, bay, {}, {}, shape, x, y)) continue;
                CorridorProof proof{constructor.snapshot(), bayFree, globalFree, reservations.size()};
                try {
                    constructor.assign(block, {false, bay, orientation, x, y, entry, exit});
                    bayFree[bay] = exit;
                    if (reserveCorridor) globalFree = exit;
                    reservations.push_back({bay, entry, exit});
                    if (extend(depth + 1)) return true;
                    constructor.rollback(proof.constructorSnapshot);
                } catch (const std::runtime_error&) {
                    return false;
                }
                bayFree = std::move(proof.bayFree);
                globalFree = proof.globalFree;
                reservations.resize(proof.reservationCount);
            }
        }
        return false;
    };
    if (!extend(0) || expired(deadlineNs))
        throw std::runtime_error("corridor access construction has no complete leaf");
    return constructor.emit();
}

} // namespace corridor_access_detail

inline Output constructCorridorAccess(const RawProblem& problem, int64_t deadlineNs,
                                      bool corridorReservationInvariant = true) {
    return corridor_access_detail::solve(problem, deadlineNs, corridorReservationInvariant);
}

} // namespace target_constructor

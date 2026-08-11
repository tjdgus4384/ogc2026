#pragma once

#include "target_constructor.hpp"
#include <algorithm>
#include <chrono>
#include <cmath>
#include <functional>
#include <limits>
#include <stdexcept>
#include <vector>

namespace target_constructor {
namespace target_assignment_detail {

inline bool expired(int64_t deadlineNs) {
    return std::chrono::duration_cast<std::chrono::nanoseconds>(
               std::chrono::steady_clock::now().time_since_epoch()).count() >= deadlineNs;
}
inline double projectedImbalance(const RawProblem& problem, const std::vector<double>& load,
                                 int changedBay, double workload) {
    if (problem.bays == 1) return 0.0;
    double meanArea = 0.0;
    for (int bay = 0; bay < problem.bays; ++bay)
        meanArea += problem.bayWidth[bay] * problem.bayHeight[bay];
    meanArea /= problem.bays;
    double low = std::numeric_limits<double>::infinity();
    double high = -low;
    for (int bay = 0; bay < problem.bays; ++bay) {
        const double assigned = load[bay] + (bay == changedBay ? workload : 0.0);
        const double density = meanArea * assigned /
                               (problem.bayWidth[bay] * problem.bayHeight[bay]);
        low = std::min(low, density);
        high = std::max(high, density);
    }
    return std::floor(high - low);
}



inline Output solve(const RawProblem& problem, int64_t deadlineNs, bool requireOnTime) {
    Constructor constructor(problem, deadlineNs);
    const size_t count = problem.blocks.size();
    std::vector<int> order(count), bayFree(problem.bays, 0);
    std::vector<double> load(problem.bays, 0.0);
    for (size_t i = 0; i < count; ++i) order[i] = static_cast<int>(i);
    std::sort(order.begin(), order.end(), [&](int a, int b) {
        const Block& left = problem.blocks[a];
        const Block& right = problem.blocks[b];
        if (left.due != right.due) return left.due < right.due;
        if (left.release != right.release) return left.release < right.release;
        return a < b;
    });

    std::function<bool(size_t, double)> extend = [&](size_t depth, double preferenceLoss) {
        if (expired(deadlineNs)) return false;
        if (depth == count) return constructor.complete();
        const int block = order[depth];
        const Block& source = problem.blocks[block];
        std::vector<int> bays(problem.bays);
        for (int bay = 0; bay < problem.bays; ++bay) bays[bay] = bay;
        std::stable_sort(bays.begin(), bays.end(), [&](int a, int b) {
            const double best = *std::max_element(source.preference.begin(), source.preference.end());
            const double scoreA = problem.w2 * projectedImbalance(problem, load, a, source.workload) +
                                  problem.w3 * (preferenceLoss + best - source.preference[a]);
            const double scoreB = problem.w2 * projectedImbalance(problem, load, b, source.workload) +
                                  problem.w3 * (preferenceLoss + best - source.preference[b]);
            if (scoreA != scoreB) return scoreA < scoreB;
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
            const int entry = std::max(source.release, bayFree[bay]);
            const int exit = entry + source.processing;
            if (requireOnTime && exit > source.due) continue;
            const int xs[] = {minX, maxX};
            const int ys[] = {minY, maxY};
            for (int x : xs) for (int y : ys) {
                if (expired(deadlineNs)) return false;
                if (!craneEntry(problem, bay, {}, {}, shape, x, y)) continue;
                const size_t proof = constructor.snapshot();
                const int previousFree = bayFree[bay];
                const double previousLoad = load[bay];
                try {
                    constructor.assign(block, {false, bay, orientation, x, y, entry, exit});
                    bayFree[bay] = exit;
                    load[bay] += source.workload;
                    const double nextLoss = preferenceLoss +
                        (*std::max_element(source.preference.begin(), source.preference.end()) - source.preference[bay]);
                    if (extend(depth + 1, nextLoss)) return true;
                    constructor.rollback(proof);
                } catch (const std::runtime_error&) {
                    return false;
                }
                bayFree[bay] = previousFree;
                load[bay] = previousLoad;
            }
        }
        return false;
    };
    if (!extend(0, 0.0) || expired(deadlineNs))
        throw std::runtime_error("target assignment construction has no complete leaf");
    return constructor.emit();
}

} // namespace target_assignment_detail

inline Output constructTargetAssignment(const RawProblem& problem, int64_t deadlineNs,
                                        bool targetBudgetInvariant = true) {
    return target_assignment_detail::solve(problem, deadlineNs, targetBudgetInvariant);
}

} // namespace target_constructor

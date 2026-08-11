// kernel_parallel_constructor_g02.hpp -- bounded, concurrent from-scratch constructor.
#pragma once

#include "target_constructor.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <limits>
#include <stdexcept>
#include <utility>
#include <vector>

namespace target_constructor {
namespace parallel_constructor_g02_detail {

inline bool expired(int64_t deadlineNs) {
    return std::chrono::duration_cast<std::chrono::nanoseconds>(
        std::chrono::steady_clock::now().time_since_epoch()).count() >= deadlineNs;
}

inline bool addInt(int left, int right, int& result) {
    const int64_t sum = static_cast<int64_t>(left) + right;
    if (sum < std::numeric_limits<int>::min() || sum > std::numeric_limits<int>::max()) return false;
    result = static_cast<int>(sum);
    return true;
}

inline bool coordinateRange(const Orientation& shape, double width, double height,
                            int& minX, int& maxX, int& minY, int& maxY) {
    const double lowX = std::ceil(-shape.bounds.x0 - geom::EPS);
    const double highX = std::floor(width - shape.bounds.x1 + geom::EPS);
    const double lowY = std::ceil(-shape.bounds.y0 - geom::EPS);
    const double highY = std::floor(height - shape.bounds.y1 + geom::EPS);
    if (!std::isfinite(lowX) || !std::isfinite(highX) || !std::isfinite(lowY) || !std::isfinite(highY) ||
        lowX < std::numeric_limits<int>::min() || highX > std::numeric_limits<int>::max() ||
        lowY < std::numeric_limits<int>::min() || highY > std::numeric_limits<int>::max() ||
        lowX > highX || lowY > highY) return false;
    minX = static_cast<int>(lowX); maxX = static_cast<int>(highX);
    minY = static_cast<int>(lowY); maxY = static_cast<int>(highY);
    return true;
}

inline void appendUnique(std::vector<int>& values, int value) {
    if (std::find(values.begin(), values.end(), value) == values.end()) values.push_back(value);
}

class Search {
public:
    Search(const RawProblem& problem, int64_t deadlineNs, bool allowConcurrent)
        : problem_(problem), constructor_(problem, deadlineNs), deadlineNs_(deadlineNs),
          allowConcurrent_(allowConcurrent), active_(problem.blocks.size()) {
        int64_t horizon = std::numeric_limits<int>::min();
        for (const Block& block : problem.blocks)
            horizon = std::max<int64_t>(horizon, std::max(block.release, block.due));
        for (const Block& block : problem.blocks) {
            if (horizon > std::numeric_limits<int>::max() - block.processing) {
                horizon_ = std::numeric_limits<int>::max();
                return;
            }
            horizon += block.processing;
        }
        horizon_ = problem.blocks.empty() ? 0 : static_cast<int>(horizon);
    }

    Output run() {
        if (!extend(0)) throw std::runtime_error("parallel g02 constructor has no complete feasible leaf");
        return output_;
    }

private:
    const RawProblem& problem_;
    Constructor constructor_;
    int64_t deadlineNs_;
    bool allowConcurrent_;
    int horizon_ = 0;
    std::vector<Assignment> active_;
    Output output_;

    bool extend(size_t block) {
        if (expired(deadlineNs_)) return false;
        if (block == problem_.blocks.size()) {
            try {
                output_ = constructor_.emit();
                return true;
            } catch (const std::logic_error&) {
                return false;
            } catch (const std::runtime_error&) {
                return false;
            }
        }
        const Block& source = problem_.blocks[block];
        std::vector<int> bays(problem_.bays);
        for (int bay = 0; bay < problem_.bays; ++bay) bays[bay] = bay;
        std::stable_sort(bays.begin(), bays.end(), [&](int left, int right) {
            if (source.preference[left] != source.preference[right])
                return source.preference[left] > source.preference[right];
            return left < right;
        });
        for (int bay : bays) for (int orientation = 0;
             orientation < static_cast<int>(source.orientations.size()); ++orientation) {
            const Orientation& shape = source.orientations[orientation];
            int minX, maxX, minY, maxY;
            if (!coordinateRange(shape, problem_.bayWidth[bay], problem_.bayHeight[bay], minX, maxX, minY, maxY)) continue;
            const std::vector<int> starts = entryCandidates(block, bay);
            const std::vector<std::pair<int, int>> positions = positionCandidates(bay, shape, minX, maxX, minY, maxY);
            for (int entry : starts) for (const std::pair<int, int>& position : positions) {
                if (expired(deadlineNs_)) return false;
                int exit;
                if (!addInt(entry, source.processing, exit) || exit > horizon_) continue;
                Assignment candidate{false, bay, orientation, position.first, position.second, entry, exit};
                if (!compatible(block, candidate)) continue;
                size_t token = 0;
                try {
                    token = constructor_.snapshot();
                    constructor_.assign(static_cast<int>(block), candidate);
                } catch (const std::runtime_error&) {
                    return false;
                } catch (const std::invalid_argument&) {
                    try {
                        constructor_.rollback(token);
                    } catch (const std::runtime_error&) {
                        return false;
                    }
                    continue;
                }
                active_[block] = candidate;
                if (extend(block + 1)) return true;
                try {
                    constructor_.rollback(token);
                } catch (const std::runtime_error&) {
                    return false;
                }
                active_[block] = Assignment{};
            }
        }
        return false;
    }

    std::vector<int> entryCandidates(size_t block, int bay) const {
        const Block& source = problem_.blocks[block];
        std::vector<int> candidates;
        appendUnique(candidates, source.release);
        int latestOnTime;
        if (addInt(source.due, -source.processing, latestOnTime) && latestOnTime >= source.release)
            appendUnique(candidates, latestOnTime);
        for (const Assignment& prior : active_) {
            if (!prior.assigned || prior.bay != bay) continue;
            if (allowConcurrent_ && prior.entry >= source.release && prior.entry <= horizon_) appendUnique(candidates, prior.entry);
            if (prior.exit >= source.release && prior.exit <= horizon_) appendUnique(candidates, prior.exit);
        }
        candidates.erase(std::remove_if(candidates.begin(), candidates.end(), [&](int entry) {
            int exit;
            return entry < source.release || !addInt(entry, source.processing, exit) || exit > horizon_;
        }), candidates.end());
        std::sort(candidates.begin(), candidates.end());
        return candidates;
    }

    std::vector<std::pair<int, int>> positionCandidates(int bay, const Orientation& shape,
                                                         int minX, int maxX, int minY, int maxY) const {
        std::vector<int> xs{minX, maxX}, ys{minY, maxY};
        for (size_t i = 0; i < active_.size(); ++i) {
            const Assignment& prior = active_[i];
            if (!prior.assigned || prior.bay != bay) continue;
            const Orientation& priorShape = problem_.blocks[i].orientations[prior.orientation];
            const double right = prior.x + priorShape.bounds.x1 - shape.bounds.x0;
            const double left = prior.x + priorShape.bounds.x0 - shape.bounds.x1;
            const double top = prior.y + priorShape.bounds.y1 - shape.bounds.y0;
            const double bottom = prior.y + priorShape.bounds.y0 - shape.bounds.y1;
            if (right >= std::numeric_limits<int>::min() && right <= std::numeric_limits<int>::max()) appendUnique(xs, static_cast<int>(std::ceil(right - geom::EPS)));
            if (left >= std::numeric_limits<int>::min() && left <= std::numeric_limits<int>::max()) appendUnique(xs, static_cast<int>(std::floor(left + geom::EPS)));
            if (top >= std::numeric_limits<int>::min() && top <= std::numeric_limits<int>::max()) appendUnique(ys, static_cast<int>(std::ceil(top - geom::EPS)));
            if (bottom >= std::numeric_limits<int>::min() && bottom <= std::numeric_limits<int>::max()) appendUnique(ys, static_cast<int>(std::floor(bottom + geom::EPS)));
        }
        xs.erase(std::remove_if(xs.begin(), xs.end(), [&](int x) { return x < minX || x > maxX; }), xs.end());
        ys.erase(std::remove_if(ys.begin(), ys.end(), [&](int y) { return y < minY || y > maxY; }), ys.end());
        std::sort(xs.begin(), xs.end()); std::sort(ys.begin(), ys.end());
        std::vector<std::pair<int, int>> result;
        for (int x : xs) for (int y : ys) result.push_back({x, y});
        return result;
    }

    bool compatible(size_t block, const Assignment& candidate) const {
        const Orientation& entering = problem_.blocks[block].orientations[candidate.orientation];
        std::vector<Assignment> present;
        std::vector<Orientation> shapes;
        for (size_t other = 0; other < active_.size(); ++other) {
            const Assignment& prior = active_[other];
            if (!prior.assigned || prior.bay != candidate.bay) continue;
            const Orientation& priorShape = problem_.blocks[other].orientations[prior.orientation];
            if (prior.entry < candidate.exit && candidate.entry < prior.exit) {
                if (!allowConcurrent_ ||
                    geometryOverlap(priorShape, prior.x, prior.y, entering, candidate.x, candidate.y)) return false;
            }
            if ((prior.entry < candidate.entry ||
                 (prior.entry == candidate.entry && other < block)) &&
                candidate.entry < prior.exit) {
                present.push_back(prior);
                shapes.push_back(priorShape);
            }
        }
        return craneEntry(problem_, candidate.bay, present, shapes, entering, candidate.x, candidate.y);
    }
};

} // namespace parallel_constructor_g02_detail

inline Output constructParallelG02(const RawProblem& problem, int64_t deadlineNs,
                                   bool allowConcurrent = true) {
    return parallel_constructor_g02_detail::Search(problem, deadlineNs, allowConcurrent).run();
}

} // namespace target_constructor

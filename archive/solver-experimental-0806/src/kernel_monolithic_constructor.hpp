// kernel_monolithic_constructor.hpp -- isolated exhaustive joint constructor.
#pragma once

#include "target_constructor.hpp"

#include <cmath>
#include <cstdint>
#include <limits>
#include <stdexcept>
#include <utility>
#include <vector>

namespace target_constructor {
namespace monolithic_detail {

class Search {
public:
    Search(const RawProblem& problem, int64_t deadlineNs, bool integrated)
        : problem_(problem), engine_(problem, deadlineNs), integrated_(integrated),
          used_(problem.blocks.size(), false), active_(problem.blocks.size()) {}

    Output run() {
        if (!visit(std::numeric_limits<int>::min())) throw std::runtime_error("monolithic constructor found no complete state");
        return std::move(output_);
    }

private:
    const RawProblem& problem_;
    Constructor engine_;
    bool integrated_;
    std::vector<bool> used_;
    std::vector<Assignment> active_;
    Output output_;

    bool visit(int cursor) {
        if (engine_.deadlineExpired()) return false;
        int remaining = -1;
        for (int block = 0; block < static_cast<int>(used_.size()); ++block) {
            if (!used_[block]) {
                remaining = block;
                break;
            }
        }
        if (remaining < 0) {
            try {
                output_ = engine_.emit();
                return true;
            } catch (const std::logic_error&) {
                return false;
            }
        }

        // Selecting every unassigned block here exhausts nondecreasing entry-event orders.
        for (int block = 0; block < static_cast<int>(used_.size()); ++block) {
            if (used_[block]) continue;
            const Block& source = problem_.blocks[block];
            const int earliest = source.release > cursor ? source.release : cursor;
            const int latest = std::numeric_limits<int>::max() - source.processing;
            bool hasCoordinate = false;
            for (int bay = 0; bay < problem_.bays && !hasCoordinate; ++bay)
                for (const Orientation& shape : source.orientations) {
                    int firstX, lastX, firstY, lastY;
                    if (coordinateRange(shape.bounds.x0, shape.bounds.x1, problem_.bayWidth[bay], firstX, lastX) &&
                        coordinateRange(shape.bounds.y0, shape.bounds.y1, problem_.bayHeight[bay], firstY, lastY)) {
                        hasCoordinate = true;
                        break;
                    }
                }
            if (!hasCoordinate) continue;
            for (int entry = earliest; entry <= latest; ++entry) {
                if (engine_.deadlineExpired()) return false;
                for (int exit = entry + source.processing;; ++exit) {
                    for (int bay = 0; bay < problem_.bays; ++bay) {
                        for (int orientation = 0;
                             orientation < static_cast<int>(source.orientations.size()); ++orientation) {
                            const Orientation& shape = source.orientations[orientation];
                            int firstX, lastX, firstY, lastY;
                            if (!coordinateRange(shape.bounds.x0, shape.bounds.x1, problem_.bayWidth[bay], firstX, lastX) ||
                                !coordinateRange(shape.bounds.y0, shape.bounds.y1, problem_.bayHeight[bay], firstY, lastY)) continue;
                            for (int x = firstX;; ++x) {
                                for (int y = firstY;; ++y) {
                                    Assignment candidate;
                                    candidate.bay = bay;
                                    candidate.orientation = orientation;
                                    candidate.x = x;
                                    candidate.y = y;
                                    candidate.entry = entry;
                                    candidate.exit = exit;
                                    if (integrated_ && !propagated(block, candidate)) {
                                        if (y == lastY) break;
                                        continue;
                                    }
                                    const size_t token = engine_.snapshot();
                                    used_[block] = true;
                                    active_[block] = candidate;
                                    try {
                                        engine_.assign(block, candidate);
                                    } catch (const std::invalid_argument&) {
                                        used_[block] = false;
                                        active_[block] = Assignment{};
                                        engine_.rollback(token);
                                        if (y == lastY) break;
                                        continue;
                                    }
                                    if (visit(entry)) return true;
                                    engine_.rollback(token);
                                    used_[block] = false;
                                    active_[block] = Assignment{};
                                    if (y == lastY) break;
                                }
                                if (x == lastX) break;
                            }
                        }
                    }
                    if (exit == std::numeric_limits<int>::max()) break;
                    if (engine_.deadlineExpired()) return false;
                }
            }
        }
        return false;
    }

    static bool coordinateRange(double lower, double upper, double limit, int& first, int& last) {
        const double low = std::ceil(-lower - geom::EPS);
        const double high = std::floor(limit - upper + geom::EPS);
        if (!std::isfinite(low) || !std::isfinite(high) ||
            low < std::numeric_limits<int>::min() || high > std::numeric_limits<int>::max() || low > high) return false;
        first = static_cast<int>(low);
        last = static_cast<int>(high);
        return true;
    }

    bool propagated(int block, const Assignment& candidate) const {
        const Orientation& entering = problem_.blocks[block].orientations[candidate.orientation];
        std::vector<Assignment> present;
        std::vector<Orientation> shapes;
        for (int other = 0; other < static_cast<int>(active_.size()); ++other) {
            if (!used_[other] || active_[other].bay != candidate.bay) continue;
            const Assignment& prior = active_[other];
            const Orientation& priorShape = problem_.blocks[other].orientations[prior.orientation];
            if (prior.entry < candidate.exit && candidate.entry < prior.exit &&
                geometryOverlap(priorShape, prior.x, prior.y, entering, candidate.x, candidate.y)) return false;
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

} // namespace monolithic_detail

inline Output constructMonolithic(const RawProblem& problem, int64_t deadlineNs,
                                  bool integratedCrossDomainPropagation = true) {
    return monolithic_detail::Search(problem, deadlineNs, integratedCrossDomainPropagation).run();
}

} // namespace target_constructor

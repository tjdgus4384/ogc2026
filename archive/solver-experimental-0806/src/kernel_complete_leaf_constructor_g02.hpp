// kernel_complete_leaf_constructor_g02.hpp -- bounded, from-scratch complete-leaf constructor.
#pragma once

#include "target_constructor.hpp"

#include <algorithm>
#include <cmath>
#include <chrono>
#include <cstdint>
#include <limits>
#include <stdexcept>
#include <vector>

namespace target_constructor {
namespace complete_leaf_g02_detail {

inline bool expired(int64_t deadlineNs) {
    return std::chrono::duration_cast<std::chrono::nanoseconds>(
               std::chrono::steady_clock::now().time_since_epoch()).count() >= deadlineNs;
}

inline bool coordinateRange(double lower, double upper, double limit, int& first, int& last) {
    const double low = std::ceil(-lower - geom::EPS);
    const double high = std::floor(limit - upper + geom::EPS);
    if (!std::isfinite(low) || !std::isfinite(high) ||
        low < std::numeric_limits<int>::min() || high > std::numeric_limits<int>::max() ||
        low > high) return false;
    first = static_cast<int>(low);
    last = static_cast<int>(high);
    return true;
}

inline bool intervalAfter(int release, int available, int processing, int& entry, int& exit) {
    entry = std::max(release, available);
    if (entry > std::numeric_limits<int>::max() - processing) return false;
    exit = entry + processing;
    return true;
}

class CompleteLeaf {
public:
    CompleteLeaf(const RawProblem& problem, int64_t deadlineNs, bool reserveMechanism)
        : problem_(problem), constructor_(problem, deadlineNs), deadlineNs_(deadlineNs),
          reserveMechanism_(reserveMechanism), assigned_(problem.blocks.size(), 0),
          bayFree_(problem.bays, std::numeric_limits<int>::min()) {}

    Output run() {
        if (expired(deadlineNs_) || !extend(0))
            throw std::runtime_error("complete leaf construction has no complete state");
        return constructor_.emit();
    }

private:
    const RawProblem& problem_;
    Constructor constructor_;
    int64_t deadlineNs_;
    bool reserveMechanism_;
    std::vector<unsigned char> assigned_;
    std::vector<int> bayFree_;
    int mechanismFree_ = std::numeric_limits<int>::min();

    bool extend(size_t depth) {
        if (expired(deadlineNs_)) return false;
        if (depth == problem_.blocks.size()) return constructor_.complete();

        const int block = nextBlock();
        if (block < 0) return false;
        const Block& source = problem_.blocks[block];
        std::vector<int> bays(problem_.bays);
        for (int bay = 0; bay < problem_.bays; ++bay) bays[bay] = bay;
        std::stable_sort(bays.begin(), bays.end(), [&](int left, int right) {
            if (source.preference[left] != source.preference[right])
                return source.preference[left] > source.preference[right];
            return left < right;
        });

        for (int bay : bays) {
            const int available = reserveMechanism_ ? mechanismFree_ : bayFree_[bay];
            int entry = 0, exit = 0;
            if (!intervalAfter(source.release, available, source.processing, entry, exit)) continue;
            for (int orientation = 0;
                 orientation < static_cast<int>(source.orientations.size()); ++orientation) {
                if (expired(deadlineNs_)) return false;
                const Orientation& shape = source.orientations[orientation];
                int firstX = 0, lastX = 0, firstY = 0, lastY = 0;
                if (!coordinateRange(shape.bounds.x0, shape.bounds.x1, problem_.bayWidth[bay], firstX, lastX) ||
                    !coordinateRange(shape.bounds.y0, shape.bounds.y1, problem_.bayHeight[bay], firstY, lastY) ||
                    !craneEntry(problem_, bay, {}, {}, shape, firstX, firstY)) continue;

                const size_t token = constructor_.snapshot();
                const int oldBayFree = bayFree_[bay];
                const int oldMechanismFree = mechanismFree_;
                assigned_[block] = 1;
                try {
                    constructor_.assign(block, {false, bay, orientation, firstX, firstY, entry, exit});
                    bayFree_[bay] = exit;
                    if (reserveMechanism_) mechanismFree_ = exit;
                    if (extend(depth + 1)) return true;
                    constructor_.rollback(token);
                } catch (const std::runtime_error&) {
                    return false;
                } catch (const std::invalid_argument&) {
                    constructor_.rollback(token);
                }
                assigned_[block] = 0;
                bayFree_[bay] = oldBayFree;
                mechanismFree_ = oldMechanismFree;
            }
        }
        return false;
    }

    int nextBlock() const {
        int result = -1;
        for (int block = 0; block < static_cast<int>(assigned_.size()); ++block) {
            if (assigned_[block]) continue;
            if (result < 0 || problem_.blocks[block].release < problem_.blocks[result].release ||
                (problem_.blocks[block].release == problem_.blocks[result].release &&
                 (problem_.blocks[block].due < problem_.blocks[result].due ||
                  (problem_.blocks[block].due == problem_.blocks[result].due && block < result))))
                result = block;
        }
        return result;
    }
};

} // namespace complete_leaf_g02_detail

inline Output constructCompleteLeafG02(const RawProblem& problem, int64_t deadlineNs,
                                       bool reserveMechanism = true) {
    return complete_leaf_g02_detail::CompleteLeaf(problem, deadlineNs, reserveMechanism).run();
}

} // namespace target_constructor

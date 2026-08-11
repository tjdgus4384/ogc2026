// target_constructor.hpp -- isolated, from-scratch construction ABI.
#pragma once
#include "crane.hpp"
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace target_constructor {

struct Orientation {
    std::vector<geom::Poly> layers;
    geom::BBox bounds;
    std::vector<geom::BBox> layerBounds;
    std::vector<geom::Pt> interior;
};

struct Block {
    std::vector<Orientation> orientations;
    int release = 0;
    int due = 0;
    int processing = 1;
    double workload = 0.0;
    std::vector<double> preference;
};

struct RawProblem {
    int bays = 0;
    std::vector<double> bayWidth;
    std::vector<double> bayHeight;
    std::vector<Block> blocks;
    double w1 = 0.0;
    double w2 = 0.0;
    double w3 = 0.0;
};

struct Assignment {
    bool assigned = false;
    int bay = -1;
    int orientation = -1;
    int x = 0;
    int y = 0;
    int entry = 0;
    int exit = 0;
};

struct Event {
    int block = -1;
    int time = 0;
    bool entering = false;
};

struct Objective {
    double tardiness = 0.0;
    double imbalance = 0.0;
    double preferenceLoss = 0.0;
    double total = 0.0;
};

struct Telemetry {
    uint64_t assignments = 0;
    uint64_t events = 0;
    uint64_t snapshots = 0;
    uint64_t rollbacks = 0;
    bool deadlineExpired = false;
};

struct Output {
    std::vector<Assignment> assignments;
    std::vector<Event> events;
    Objective objective;
    Telemetry telemetry;
};

inline Orientation makeOrientation(std::vector<geom::Poly> layers) {
    if (layers.empty()) throw std::invalid_argument("orientation needs a layer");
    Orientation result;
    result.layers = std::move(layers);
    result.bounds = {1e18, 1e18, -1e18, -1e18};
    for (const geom::Poly& layer : result.layers) {
        for (const geom::Pt& point : layer)
            if (!std::isfinite(point.x) || !std::isfinite(point.y))
                throw std::invalid_argument("polygon coordinates must be finite");
        if (layer.size() < 3 || std::fabs(geom::signedArea2(layer)) <= geom::EPS)
            throw std::invalid_argument("layer must be a non-degenerate polygon");
        const geom::BBox box = geom::bbox(layer);
        result.bounds.x0 = std::min(result.bounds.x0, box.x0);
        result.bounds.y0 = std::min(result.bounds.y0, box.y0);
        result.bounds.x1 = std::max(result.bounds.x1, box.x1);
        result.bounds.y1 = std::max(result.bounds.y1, box.y1);
        result.layerBounds.push_back(box);
        result.interior.push_back(geom::interiorPoint(layer));
    }
    return result;
}

// Stateless exact adapters: construction state never enters these predicates.
inline bool geometryOverlap(const Orientation& a, int ax, int ay,
                            const Orientation& b, int bx, int by) {
    const int layers = std::min(a.layers.size(), b.layers.size());
    for (int layer = 0; layer < layers; ++layer)
        if (geom::overlapsAreaOffset(a.layers[layer], a.interior[layer],
                                     b.layers[layer], b.interior[layer],
                                     bx - ax, by - ay)) return true;
    return false;
}

inline crane::OrientGeom craneGeometry(const Orientation& source) {
    crane::OrientGeom result;
    result.layers = source.layers;
    result.bb = source.bounds;
    result.lbb = source.layerBounds;
    result.ip = source.interior;
    return result;
}

inline bool craneEntry(const RawProblem& problem, int bay,
                       const std::vector<Assignment>& present,
                       const std::vector<Orientation>& shapes,
                       const Orientation& entering, int x, int y) {
    if (bay < 0 || bay >= problem.bays || present.size() != shapes.size())
        throw std::invalid_argument("invalid crane entry arguments");
    std::vector<crane::Placement> placements;
    std::vector<crane::OrientGeom> converted;
    converted.reserve(shapes.size());
    placements.reserve(shapes.size());
    for (size_t i = 0; i < shapes.size(); ++i) converted.push_back(craneGeometry(shapes[i]));
    for (size_t i = 0; i < present.size(); ++i)
        placements.push_back({&converted[i], (double)present[i].x, (double)present[i].y});
    const crane::OrientGeom target = craneGeometry(entering);
    return crane::entryOK(problem.bayWidth[bay], problem.bayHeight[bay],
                          placements, target, x, y);
}

class Constructor {
public:
    Constructor(RawProblem problem, int64_t deadlineNs)
        : problem_(std::move(problem)), deadlineNs_(deadlineNs) {
        validateProblem(problem_);
        if (deadlineNs_ <= 0) throw std::invalid_argument("deadline_ns must be positive");
        assignments_.resize(problem_.blocks.size());
    }

    size_t unassignedCount() const {
        return static_cast<size_t>(std::count_if(assignments_.begin(), assignments_.end(),
            [](const Assignment& assignment) { return !assignment.assigned; }));
    }

    bool complete() const {
        return unassignedCount() == 0 && telemetry_.assignments == assignments_.size() &&
               telemetry_.events == events_.size() && events_.size() == assignments_.size() * 2 &&
               outputStateValid();
    }

    bool deadlineExpired() {
        telemetry_.deadlineExpired = nowNs() >= deadlineNs_;
        return telemetry_.deadlineExpired;
    }

    void requireLiveDeadline() {
        if (deadlineExpired()) throw std::runtime_error("hard deadline expired");
    }

    size_t snapshot() {
        requireLiveDeadline();
        snapshots_.push_back({assignments_, events_, telemetry_.assignments, telemetry_.events});
        ++telemetry_.snapshots;
        return snapshots_.size() - 1;
    }

    void rollback(size_t token) {
        requireLiveDeadline();
        if (token >= snapshots_.size()) throw std::invalid_argument("invalid snapshot token");
        assignments_ = snapshots_[token].assignments;
        events_ = snapshots_[token].events;
        telemetry_.assignments = snapshots_[token].assignmentsCount;
        telemetry_.events = snapshots_[token].eventsCount;
        snapshots_.resize(token);
        ++telemetry_.rollbacks;
    }

    void assign(int block, Assignment value) {
        requireLiveDeadline();
        validateAssignment(block, value);
        if (assignments_[block].assigned) throw std::logic_error("block already assigned");
        value.assigned = true;
        assignments_[block] = value;
        events_.push_back({block, value.entry, true});
        events_.push_back({block, value.exit, false});
        ++telemetry_.assignments;
        telemetry_.events += 2;
        std::sort(events_.begin(), events_.end(), [](const Event& a, const Event& b) {
            if (a.time != b.time) return a.time < b.time;
            if (a.entering != b.entering) return !a.entering;
            return a.block < b.block;
        });
    }

    Output emit() {
        requireLiveDeadline();
        if (!complete()) throw std::logic_error("cannot emit incomplete constructor state");
        Output output{assignments_, events_, evaluate(), telemetry_};
        return output;
    }

private:
    struct Snapshot {
        std::vector<Assignment> assignments;
        std::vector<Event> events;
        uint64_t assignmentsCount;
        uint64_t eventsCount;
    };
    RawProblem problem_;
    int64_t deadlineNs_;
    std::vector<Assignment> assignments_;
    std::vector<Event> events_;
    std::vector<Snapshot> snapshots_;
    Telemetry telemetry_;

    static int64_t nowNs() {
        return std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::steady_clock::now().time_since_epoch()).count();
    }

    static void validateProblem(const RawProblem& problem) {
        if (problem.bays <= 0 || (int)problem.bayWidth.size() != problem.bays ||
            (int)problem.bayHeight.size() != problem.bays)
            throw std::invalid_argument("invalid bay dimensions");
        for (int bay = 0; bay < problem.bays; ++bay)
            if (!(problem.bayWidth[bay] > 0.0) || !(problem.bayHeight[bay] > 0.0))
                throw std::invalid_argument("bay dimensions must be positive");
        if (!std::isfinite(problem.w1) || !std::isfinite(problem.w2) ||
            !std::isfinite(problem.w3))
            throw std::invalid_argument("weights must be finite");
        for (const Block& block : problem.blocks) {
            if (!std::isfinite(block.workload))
                throw std::invalid_argument("workload must be finite");
            for (double preference : block.preference)
                if (!std::isfinite(preference))
                    throw std::invalid_argument("preference must be finite");
        }
        for (const Block& block : problem.blocks) {
            if (block.release > block.due || block.processing <= 0 ||
                (int)block.preference.size() != problem.bays ||
                block.orientations.empty()) throw std::invalid_argument("invalid block");
        }
    }

    void validateAssignment(int block, const Assignment& value) const {
        if (block < 0 || block >= (int)problem_.blocks.size() || value.assigned ||
            value.bay < 0 || value.bay >= problem_.bays ||
            value.orientation < 0 || value.orientation >= (int)problem_.blocks[block].orientations.size() ||
            value.entry < problem_.blocks[block].release ||
            value.exit - value.entry < problem_.blocks[block].processing)
            throw std::invalid_argument("invalid assignment");
        const geom::BBox& box = problem_.blocks[block].orientations[value.orientation].bounds;
        if (box.x0 + value.x < -geom::EPS || box.y0 + value.y < -geom::EPS ||
            box.x1 + value.x > problem_.bayWidth[value.bay] + geom::EPS ||
            box.y1 + value.y > problem_.bayHeight[value.bay] + geom::EPS)
            throw std::invalid_argument("assignment outside bay");
    }

    bool outputStateValid() const {
        std::vector<unsigned char> entries(assignments_.size(), 0), exits(assignments_.size(), 0);
        for (size_t block = 0; block < assignments_.size(); ++block) {
            if (!assignments_[block].assigned) return false;
            Assignment check = assignments_[block]; check.assigned = false;
            try { validateAssignment((int)block, check); }
            catch (const std::invalid_argument&) { return false; }
        }
        std::vector<Event> ordered = events_;
        std::sort(ordered.begin(), ordered.end(), [](const Event& a, const Event& b) {
            if (a.time != b.time) return a.time < b.time;
            if (a.entering != b.entering) return !a.entering;
            return a.block < b.block;
        });
        if (!std::equal(ordered.begin(), ordered.end(), events_.begin(),
                        [](const Event& a, const Event& b) {
                            return a.block == b.block && a.time == b.time && a.entering == b.entering;
                        })) return false;
        for (const Event& event : events_) {
            if (event.block < 0 || event.block >= (int)assignments_.size()) return false;
            const Assignment& assignment = assignments_[event.block];
            if (event.entering) { if (event.time != assignment.entry || entries[event.block]++) return false; }
            else { if (event.time != assignment.exit || exits[event.block]++) return false; }
        }
        if (!std::all_of(entries.begin(), entries.end(), [](unsigned char n) { return n == 1; }) ||
            !std::all_of(exits.begin(), exits.end(), [](unsigned char n) { return n == 1; })) return false;
        for (size_t a = 0; a < assignments_.size(); ++a) for (size_t b = a + 1; b < assignments_.size(); ++b)
            if (assignments_[a].bay == assignments_[b].bay &&
                assignments_[a].entry < assignments_[b].exit && assignments_[b].entry < assignments_[a].exit &&
                geometryOverlap(problem_.blocks[a].orientations[assignments_[a].orientation], assignments_[a].x, assignments_[a].y,
                                problem_.blocks[b].orientations[assignments_[b].orientation], assignments_[b].x, assignments_[b].y))
                return false;
        std::vector<crane::OrientGeom> shapes;
        std::vector<size_t> offsets(problem_.blocks.size());
        for (size_t block = 0; block < problem_.blocks.size(); ++block) {
            offsets[block] = shapes.size();
            for (const Orientation& orientation : problem_.blocks[block].orientations)
                shapes.push_back(craneGeometry(orientation));
        }
        auto shape = [&](int block) -> const crane::OrientGeom& {
            return shapes[offsets[block] + assignments_[block].orientation];
        };
        std::vector<std::vector<int>> present(problem_.bays);
        for (const Event& event : events_) {
            const Assignment& assignment = assignments_[event.block];
            std::vector<int>& residents = present[assignment.bay];
            std::vector<crane::Placement> placements;
            for (int block : residents)
                placements.push_back({&shape(block), (double)assignments_[block].x, (double)assignments_[block].y});
            if (event.entering) {
                if (!crane::entryOK(problem_.bayWidth[assignment.bay], problem_.bayHeight[assignment.bay],
                                    placements, shape(event.block), assignment.x, assignment.y)) return false;
                residents.push_back(event.block);
            } else {
                auto target = std::find(residents.begin(), residents.end(), event.block);
                if (target == residents.end() || !crane::exitOK(placements, (int)(target - residents.begin()))) return false;
                residents.erase(target);
            }
        }
        return std::all_of(present.begin(), present.end(),
                           [](const std::vector<int>& residents) { return residents.empty(); });
    }
    Objective evaluate() const {
        Objective result;
        std::vector<double> load(problem_.bays, 0.0);
        for (size_t i = 0; i < assignments_.size(); ++i) {
            const Assignment& assignment = assignments_[i];
            if (!assignment.assigned) continue;
            const Block& block = problem_.blocks[i];
            result.tardiness += std::max(0, assignment.exit - block.due);
            const double best = *std::max_element(block.preference.begin(), block.preference.end());
            result.preferenceLoss += best - block.preference[assignment.bay];
            load[assignment.bay] += block.workload;
        }
        const double meanArea = [&] { double sum = 0.0; for (int b = 0; b < problem_.bays; ++b) sum += problem_.bayWidth[b] * problem_.bayHeight[b]; return sum / problem_.bays; }();
        for (int a = 0; a < problem_.bays; ++a) for (int b = a + 1; b < problem_.bays; ++b)
            result.imbalance = std::max(result.imbalance, std::fabs(meanArea * load[a] / (problem_.bayWidth[a] * problem_.bayHeight[a]) - meanArea * load[b] / (problem_.bayWidth[b] * problem_.bayHeight[b])));
        result.imbalance = std::floor(result.imbalance);
        result.total = problem_.w1 * result.tardiness + problem_.w2 * result.imbalance + problem_.w3 * result.preferenceLoss;
        return result;
    }
};

} // namespace target_constructor

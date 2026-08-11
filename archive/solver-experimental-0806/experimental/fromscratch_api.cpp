// Isolated pybind surface for the from-scratch constructor.  This file must not
// include or invoke the shipped engine, solution state, or search kernels.
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include "../src/target_constructor.hpp"
#if defined(FROMSCRATCH_LANE_D)
#include "../src/kernel_corridor_access_constructor.hpp"
#include "../src/kernel_target_assignment_constructor.hpp"
#elif defined(FROMSCRATCH_LANE_M)
#include "../src/kernel_monolithic_constructor.hpp"
#endif
#include <cstdint>
#include <stdexcept>
#include <string>
#include <cmath>
#include <initializer_list>
#include <limits>
#include <algorithm>
#include <vector>

namespace py = pybind11;
namespace tc = target_constructor;
using namespace pybind11::literals;

namespace {

bool emptyArtifact(const py::handle& value) {
    if (value.is_none()) return true;
    if (py::isinstance<py::str>(value)) return py::cast<std::string>(value).empty();
    if (py::isinstance<py::sequence>(value) || py::isinstance<py::dict>(value))
        return py::len(value) == 0;
    return false;
}

void rejectArtifacts(const py::handle& value) {
    if (py::isinstance<py::dict>(value)) {
        const py::dict object = py::cast<py::dict>(value);
        for (const auto& item : object) {
            const std::string key = py::cast<std::string>(item.first);
            if ((key == "seed" || key == "initial_solution" || key == "incumbent" ||
                 key == "solution" || key == "prefix") && !emptyArtifact(item.second))
                throw std::invalid_argument("forbidden inherited artifact: " + key);
            rejectArtifacts(item.second);
        }
    } else if (py::isinstance<py::sequence>(value) && !py::isinstance<py::str>(value)) {
        for (const py::handle& child : py::cast<py::sequence>(value)) rejectArtifacts(child);
    }
}

double number(const py::handle& value, const char* field) {
    if (py::isinstance<py::bool_>(value) ||
        (!py::isinstance<py::float_>(value) && !py::isinstance<py::int_>(value)))
        throw std::invalid_argument(std::string(field) + " must be numeric");
    const double parsed = py::cast<double>(value);
    if (!std::isfinite(parsed)) throw std::invalid_argument(std::string(field) + " must be finite");
    return parsed;
}

int integer(const py::handle& value, const char* field) {
    if (!py::isinstance<py::int_>(value)) throw std::invalid_argument(std::string(field) + " must be an integer");
    if (py::isinstance<py::bool_>(value))
        throw std::invalid_argument(std::string(field) + " must be an integer");
    return py::cast<int>(value);
}

void exactKeys(const py::dict& object, std::initializer_list<const char*> keys, const char* name) {
    if (object.size() != (py::ssize_t)keys.size()) throw std::invalid_argument(std::string(name) + " has unknown or missing fields");
    for (const char* key : keys)
        if (!object.contains(key)) throw std::invalid_argument(std::string(name) + " missing field: " + key);
}

const py::handle required(const py::dict& object, const char* key) {
    if (!object.contains(key)) throw std::invalid_argument(std::string("missing field: ") + key);
    return object[key];
}

int timeValue(const py::handle& value, const char* field, bool ceilValue) {
    const double parsed = number(value, field);
    if (!std::isfinite(parsed) || parsed < 0.0 ||
        parsed > (double)std::numeric_limits<int>::max())
        throw std::invalid_argument(std::string(field) + " must be a bounded nonnegative time");
    return ceilValue ? (int)std::ceil(parsed - 1e-9) : (int)std::llround(parsed);
}

tc::Orientation parseOrientation(const py::handle& value) {
    py::dict source = py::cast<py::dict>(value);
    exactKeys(source, {"orientation", "layers"}, "orientation");
    integer(required(source, "orientation"), "orientation");
    std::vector<geom::Poly> layers;
    for (const py::handle& rawLayer : py::cast<py::sequence>(required(source, "layers"))) {
        geom::Poly layer;
        for (const py::handle& rawPoint : py::cast<py::sequence>(rawLayer)) {
            py::sequence point = py::cast<py::sequence>(rawPoint);
            if (py::len(point) != 2) throw std::invalid_argument("point must have two coordinates");
            layer.push_back({number(point[0], "x"), number(point[1], "y")});
        }
        layers.push_back(std::move(layer));
    }
    if (layers.empty() || layers[0].empty()) throw std::invalid_argument("shape needs a first-layer reference vertex");
    const geom::Pt reference = layers[0][0];
    for (geom::Poly& layer : layers)
        for (geom::Pt& point : layer) {
            point.x -= reference.x;
            point.y -= reference.y;
        }
    return tc::makeOrientation(std::move(layers));
}

tc::RawProblem parseRaw(const py::dict& raw) {
    rejectArtifacts(raw);
    exactKeys(raw, {"name", "bays", "weights", "blocks"}, "raw problem");
    if (!py::isinstance<py::str>(required(raw, "name")) ||
        py::cast<std::string>(required(raw, "name")).empty())
        throw std::invalid_argument("name must be a nonempty string");
    tc::RawProblem result;
    for (const py::handle& rawBay : py::cast<py::sequence>(required(raw, "bays"))) {
        py::dict bay = py::cast<py::dict>(rawBay);
        exactKeys(bay, {"width", "height"}, "bay");
        result.bayWidth.push_back(number(required(bay, "width"), "width"));
        result.bayHeight.push_back(number(required(bay, "height"), "height"));
    }
    result.bays = (int)result.bayWidth.size();
    py::dict weights = py::cast<py::dict>(required(raw, "weights"));
    exactKeys(weights, {"w1", "w2", "w3"}, "weights");
    result.w1 = number(required(weights, "w1"), "w1");
    result.w2 = number(required(weights, "w2"), "w2");
    result.w3 = number(required(weights, "w3"), "w3");
    for (const py::handle& rawBlock : py::cast<py::sequence>(required(raw, "blocks"))) {
        py::dict source = py::cast<py::dict>(rawBlock);
        exactKeys(source, {"release_time", "due_date", "processing_time", "workload", "bay_preferences", "shape"}, "block");
        tc::Block block;
        block.release = timeValue(required(source, "release_time"), "release_time", true);
        block.due = timeValue(required(source, "due_date"), "due_date", false);
        block.processing = std::max(1, timeValue(required(source, "processing_time"), "processing_time", true));
        block.workload = number(required(source, "workload"), "workload");
        for (const py::handle& preference : py::cast<py::sequence>(required(source, "bay_preferences")))
            block.preference.push_back(number(preference, "bay_preferences"));
        for (const py::handle& orientation : py::cast<py::sequence>(required(source, "shape")))
            block.orientations.push_back(parseOrientation(orientation));
        result.blocks.push_back(std::move(block));
    }
    return result;
}

py::dict validateRaw(const py::dict& raw, int64_t deadlineNs) {
    tc::Constructor constructor(parseRaw(raw), deadlineNs);
    constructor.requireLiveDeadline();
    return py::dict("blocks"_a=constructor.unassignedCount(),
                    "unassigned"_a=constructor.unassignedCount(),
                    "complete"_a=false,
                    "telemetry"_a=py::dict("assignments"_a=0, "events"_a=0,
                                             "snapshots"_a=0, "rollbacks"_a=0,
                                             "deadline_expired"_a=false));
}

#if defined(FROMSCRATCH_LANE_D) || defined(FROMSCRATCH_LANE_M)
py::dict serializeOutput(tc::Output output) {
    std::sort(output.events.begin(), output.events.end(), [](const tc::Event& left, const tc::Event& right) {
        if (left.time != right.time) return left.time < right.time;
        if (left.entering != right.entering) return !left.entering;
        return left.block < right.block;
    });
    py::dict operations;
    for (const tc::Event& event : output.events) {
        const std::string time = std::to_string(event.time);
        if (!operations.contains(time.c_str())) operations[time.c_str()] = py::list();
        py::list atTime = py::cast<py::list>(operations[time.c_str()]);
        const tc::Assignment& assignment = output.assignments.at(event.block);
        if (event.entering) {
            atTime.append(py::dict("type"_a="ENTRY", "block_id"_a=event.block,
                                   "bay_id"_a=assignment.bay, "x"_a=assignment.x,
                                   "y"_a=assignment.y, "orient_idx"_a=assignment.orientation));
        } else {
            atTime.append(py::dict("type"_a="EXIT", "block_id"_a=event.block,
                                   "bay_id"_a=assignment.bay));
        }
    }
    return py::dict(
        "operations"_a=operations,
        "objective"_a=py::dict("obj1"_a=output.objective.tardiness,
                               "obj2"_a=output.objective.imbalance,
                               "obj3"_a=output.objective.preferenceLoss,
                               "total"_a=output.objective.total),
        "telemetry"_a=py::dict("assignments"_a=output.telemetry.assignments,
                               "events"_a=output.telemetry.events,
                               "snapshots"_a=output.telemetry.snapshots,
                               "rollbacks"_a=output.telemetry.rollbacks,
                               "deadline_expired"_a=output.telemetry.deadlineExpired));
}

py::dict construct(const py::dict& raw, int64_t deadlineNs, bool mechanismEnabled) {
    tc::RawProblem problem = parseRaw(raw);
#if defined(FROMSCRATCH_LANE_D)
    const std::string name = py::cast<std::string>(required(raw, "name"));
    tc::Output output = name == "prob_3"
        ? tc::constructTargetAssignment(problem, deadlineNs, mechanismEnabled)
        : (name == "prob_4" || name == "prob_5" || name == "prob_6"
            ? tc::constructCorridorAccess(problem, deadlineNs, mechanismEnabled)
            : throw std::invalid_argument("D supports only official P3-P6"));
#else
    tc::Output output = tc::constructMonolithic(problem, deadlineNs, mechanismEnabled);
#endif
    return serializeOutput(std::move(output));
}
#endif

} // namespace

PYBIND11_MODULE(fromscratch_api, module) {
    module.doc() = "Isolated from-scratch construction ABI";
    module.def("validate_raw", &validateRaw, py::arg("raw"), py::arg("deadline_ns"));
#if defined(FROMSCRATCH_LANE_D) || defined(FROMSCRATCH_LANE_M)
    module.def("construct", &construct, py::arg("raw"), py::arg("deadline_ns"),
               py::arg("mechanism_enabled") = true);
#endif
}

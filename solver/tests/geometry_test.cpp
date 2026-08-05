#include <cassert>

#include "../src/geometry.hpp"

int main() {
    using geom::Poly;

    const Poly base{{0, 0}, {2, 0}, {2, 1}, {0, 1}};
    const Poly collinear_overlap{{4.0 / 3, 0}, {10.0 / 3, 0},
                                 {10.0 / 3, 1}, {4.0 / 3, 1}};
    const Poly partial_overlap{{1, 0}, {3, 0}, {3, 1}, {1, 1}};
    const Poly edge_touch{{0, 1}, {2, 1}, {2, 2}, {0, 2}};
    const Poly point_touch{{2, 1}, {3, 1}, {3, 2}, {2, 2}};
    const Poly unit{{0, 0}, {1, 0}, {1, 1}, {0, 1}};

    assert(geom::overlapsAreaIP(base, {.5, .5},
                                collinear_overlap, {2, .5}));
    assert(geom::overlapsAreaIP(base, {.5, .5},
                                partial_overlap, {2, .5}));
    assert(!geom::overlapsAreaIP(base, {.5, .5},
                                 edge_touch, {.5, 1.5}));
    assert(!geom::overlapsAreaIP(base, {.5, .5},
                                 point_touch, {2.5, 1.5}));
    assert(geom::overlapsAreaOffset(base, {.5, .5},
                                    unit, {.5, .5}, 1.5, 0));
    assert(!geom::overlapsAreaOffset(base, {.5, .5},
                                     unit, {.5, .5}, 2, 0));
}

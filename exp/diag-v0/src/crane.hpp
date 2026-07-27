// crane.hpp -- exact reproduction of utils.check_entry / check_exit semantics.
// Entry: (1) whole-footprint bbox within bay; (2)&(3) for each present block,
// any positive-area overlap between new-layer k and exist-layer j with j>=k
// blocks the crane. Exit: same j>=k rule against surrounding blocks (no bay
// boundary re-check). All layer polygons live in LOCAL coords; placement is
// an (x,y) offset applied on the fly.
#pragma once
#include "geometry.hpp"
#include <vector>

namespace crane {
using geom::Pt; using geom::Poly; using geom::BBox;

struct OrientGeom {
    std::vector<Poly> layers;   // local coords (ref vertex of layer0 at origin)
    BBox bb;                    // union bbox over all layers (local)
    // sec 166: per-layer caches, filled once at load. lbb gates each layer
    // pair before any vertex copy; ip feeds overlapsAreaIP so the O(V^2)
    // interiorPoint search never runs in the hot path.
    std::vector<BBox> lbb;
    std::vector<Pt>  ip;
};

struct BlockGeom {
    std::vector<OrientGeom> orients;
};

inline bool bboxOverlapRaw(const BBox& a, double ax, double ay,
                           const BBox& b, double bx, double by) {
    return a.x0 + ax < b.x1 + bx - geom::EPS && b.x0 + bx < a.x1 + ax - geom::EPS &&
           a.y0 + ay < b.y1 + by - geom::EPS && b.y0 + by < a.y1 + ay - geom::EPS;
}

// sec 166: layer-indexed overlap using the per-layer caches. The lbb gate is
// the same EPS predicate overlapsArea used internally, applied BEFORE the
// vertex copies; interior samples are cached+translated, not recomputed.
inline bool overlapsLay(const OrientGeom& ga, int la, double ax, double ay,
                        const OrientGeom& gb, int lb, double bx, double by) {
    if (!bboxOverlapRaw(ga.lbb[la], ax, ay, gb.lbb[lb], bx, by)) return false;
    const Poly& A = ga.layers[la]; const Poly& B = gb.layers[lb];
    thread_local Poly ta, tb;
    ta.resize(A.size()); tb.resize(B.size());
    for (size_t i = 0; i < A.size(); i++) ta[i] = {A[i].x + ax, A[i].y + ay};
    for (size_t i = 0; i < B.size(); i++) tb[i] = {B[i].x + bx, B[i].y + by};
    geom::Pt ia{ga.ip[la].x + ax, ga.ip[la].y + ay};
    geom::Pt ib{gb.ip[lb].x + bx, gb.ip[lb].y + by};
    return geom::overlapsAreaIP(ta, ia, tb, ib);
}

struct Placement {           // one block resting in a bay
    const OrientGeom* g;
    double x, y;
};

inline bool bboxOverlapOff(const BBox& a, double ax, double ay,
                           const BBox& b, double bx, double by) {
    return a.x0 + ax < b.x1 + bx - geom::EPS && b.x0 + bx < a.x1 + ax - geom::EPS &&
           a.y0 + ay < b.y1 + by - geom::EPS && b.y0 + by < a.y1 + ay - geom::EPS;
}

// crane can lower `nu` at (x,y)? present = blocks resting in the bay then.
inline bool entryOK(double bayW, double bayH,
                    const std::vector<Placement>& present,
                    const OrientGeom& nu, double x, double y) {
    // (1) bay boundary: union bbox within [0,W]x[0,H]
    if (nu.bb.x0 + x < -geom::EPS || nu.bb.y0 + y < -geom::EPS ||
        nu.bb.x1 + x > bayW + geom::EPS || nu.bb.y1 + y > bayH + geom::EPS)
        return false;
    int nNew = (int)nu.layers.size();
    for (const auto& ex : present) {
        if (!bboxOverlapOff(nu.bb, x, y, ex.g->bb, ex.x, ex.y)) continue;
        int nEx = (int)ex.g->layers.size();
        for (int k = 0; k < nNew; k++) {
            for (int j = k; j < nEx; j++) {
                if (overlapsLay(nu, k, x, y, *ex.g, j, ex.x, ex.y))
                    return false;
            }
        }
    }
    return true;
}

// crane can lift `tg` (index ti in present)? j>=k rule vs surrounding blocks.
inline bool exitOK(const std::vector<Placement>& present, int ti) {
    const Placement& tg = present[ti];
    int nT = (int)tg.g->layers.size();
    for (int e = 0; e < (int)present.size(); e++) {
        if (e == ti) continue;
        const Placement& ex = present[e];
        if (!bboxOverlapOff(tg.g->bb, tg.x, tg.y, ex.g->bb, ex.x, ex.y)) continue;
        int nEx = (int)ex.g->layers.size();
        for (int k = 0; k < nT; k++) {
            for (int j = k; j < nEx; j++) {
                if (overlapsLay(*tg.g, k, tg.x, tg.y, *ex.g, j, ex.x, ex.y))
                    return false;
            }
        }
    }
    return true;
}

} // namespace crane

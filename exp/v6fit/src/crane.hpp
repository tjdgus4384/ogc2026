// crane.hpp -- exact reproduction of the checker's crane rules
// (utils.check_entry / check_exit).
//
// Entry of block `nu` at (x,y): (1) whole-footprint bbox inside the bay;
// (2) against every present block, any positive-area overlap between
// nu-layer k and resident-layer j with j >= k blocks the crane (the hook
// sweeps down through every level above the final one).
// Exit: the same j >= k rule against the surrounding blocks; no bay-boundary
// re-check. All layer polygons are in block-local coords; placement is an
// (x,y) translation applied on the fly.
#pragma once
#include "geometry.hpp"
#include <vector>

namespace crane {
using geom::Pt; using geom::Poly; using geom::BBox;

struct OrientGeom {
    std::vector<Poly> layers;   // local coords (ref vertex of layer0 at origin)
    BBox bb;                    // union bbox over all layers (local)
    std::vector<BBox> lbb;      // per-layer bbox: gates each pair before any work
    std::vector<Pt>  ip;        // per-layer strictly-interior sample (cached)
};

struct BlockGeom {
    std::vector<OrientGeom> orients;
};

// bbox overlap of two placed shapes (offsets applied), open-interval EPS.
inline bool bboxOverlapOff(const BBox& a, double ax, double ay,
                           const BBox& b, double bx, double by) {
    return a.x0 + ax < b.x1 + bx - geom::EPS && b.x0 + bx < a.x1 + ax - geom::EPS &&
           a.y0 + ay < b.y1 + by - geom::EPS && b.y0 + by < a.y1 + ay - geom::EPS;
}

// positive-area overlap of layer la of ga (at ax,ay) vs layer lb of gb (at bx,by)
inline bool overlapsLay(const OrientGeom& ga, int la, double ax, double ay,
                        const OrientGeom& gb, int lb, double bx, double by) {
    if (!bboxOverlapOff(ga.lbb[la], ax, ay, gb.lbb[lb], bx, by)) return false;
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

// can the crane lower `nu` at (x,y)? present = blocks resting in the bay then.
inline bool entryOK(double bayW, double bayH,
                    const std::vector<Placement>& present,
                    const OrientGeom& nu, double x, double y) {
    if (nu.bb.x0 + x < -geom::EPS || nu.bb.y0 + y < -geom::EPS ||
        nu.bb.x1 + x > bayW + geom::EPS || nu.bb.y1 + y > bayH + geom::EPS)
        return false;
    int nNew = (int)nu.layers.size();
    for (const auto& ex : present) {
        if (!bboxOverlapOff(nu.bb, x, y, ex.g->bb, ex.x, ex.y)) continue;
        int nEx = (int)ex.g->layers.size();
        for (int k = 0; k < nNew; k++)
            for (int j = k; j < nEx; j++)
                if (overlapsLay(nu, k, x, y, *ex.g, j, ex.x, ex.y))
                    return false;
    }
    return true;
}

// can the crane lift present[ti]? same j >= k rule vs the surrounding blocks.
inline bool exitOK(const std::vector<Placement>& present, int ti) {
    const Placement& tg = present[ti];
    int nT = (int)tg.g->layers.size();
    for (int e = 0; e < (int)present.size(); e++) {
        if (e == ti) continue;
        const Placement& ex = present[e];
        if (!bboxOverlapOff(tg.g->bb, tg.x, tg.y, ex.g->bb, ex.x, ex.y)) continue;
        int nEx = (int)ex.g->layers.size();
        for (int k = 0; k < nT; k++)
            for (int j = k; j < nEx; j++)
                if (overlapsLay(*tg.g, k, tg.x, tg.y, *ex.g, j, ex.x, ex.y))
                    return false;
    }
    return true;
}

} // namespace crane

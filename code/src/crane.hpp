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
#include <cstdint>
#include <vector>

namespace crane {
using geom::Pt; using geom::Poly; using geom::BBox;

struct OrientGeom {
    std::vector<Poly> layers;   // local coords (ref vertex of layer0 at origin)
    BBox bb;                    // union bbox over all layers (local)
    double parea = 0.0;         // true polygon footprint (max over layers):
                                // the area screener used the BBOX area, which
                                // is 1.6x larger on average, so it rejected
                                // bays that still had real room -- and the
                                // boards sit exactly at that bbox limit.
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

// QUALIFYING-WORK MEMO. Repair is ~96% of solver time and its cost sits
// in these exact polygon tests, not in rejects (the derived-floor
// analysis: visit reductions saved nothing because the reject path is
// ~free). The test is a PURE function of (shapeA, layer, shapeB, layer,
// relative offset): geometry is immutable and positions are integers,
// so results never invalidate and the anneal's swap/shift/compact moves
// revisit the same relative placements constantly. A thread-local
// open-address table turns a repeat test into one probe. The earlier
// memo refutation targeted the reject path; this caches the expensive
// path only (after the bbox prefilter).
struct OvSlot { uint64_t key; uint8_t val; };
inline bool overlapsLay(const OrientGeom& ga, int la, double ax, double ay,
                        const OrientGeom& gb, int lb, double bx, double by) {
    if (!bboxOverlapOff(ga.lbb[la], ax, ay, gb.lbb[lb], bx, by)) return false;
    static thread_local std::vector<OvSlot> memo(1u << 21);   // 2M slots
    const long dx = std::lround(bx - ax), dy = std::lround(by - ay);
    bool exact = (std::abs((bx - ax) - (double)dx) < 1e-9 &&
                  std::abs((by - ay) - (double)dy) < 1e-9);
    uint64_t h = 0;
    if (exact) {
        h = (uint64_t)(uintptr_t)&ga * 0x9E3779B97F4A7C15ull;
        h ^= (uint64_t)(uintptr_t)&gb * 0xC2B2AE3D27D4EB4Full;
        h ^= ((uint64_t)(uint32_t)la << 48) ^ ((uint64_t)(uint32_t)lb << 40);
        h ^= (uint64_t)(uint32_t)(int32_t)dx * 0x165667B19E3779F9ull;
        h ^= (uint64_t)(uint32_t)(int32_t)dy * 0x27D4EB2F165667C5ull;
        h |= 1ull;                                    // 0 = empty slot
        OvSlot& s = memo[h & (memo.size() - 1)];
        if (s.key == h) return s.val != 0;
    }
    const Poly& A = ga.layers[la]; const Poly& B = gb.layers[lb];
    thread_local Poly ta, tb;
    ta.resize(A.size()); tb.resize(B.size());
    for (size_t i = 0; i < A.size(); i++) ta[i] = {A[i].x + ax, A[i].y + ay};
    for (size_t i = 0; i < B.size(); i++) tb[i] = {B[i].x + bx, B[i].y + by};
    geom::Pt ia{ga.ip[la].x + ax, ga.ip[la].y + ay};
    geom::Pt ib{gb.ip[lb].x + bx, gb.ip[lb].y + by};
    bool r = geom::overlapsAreaIP(ta, ia, tb, ib);
    if (exact) {
        OvSlot& s = memo[h & (memo.size() - 1)];
        s.key = h; s.val = r ? 1 : 0;
    }
    return r;
}

// PAIR-LEVEL MEMO: one probe replaces a whole layer sweep. Three pure
// predicates per (shapeA, shapeB, integer relative offset):
//   mode 0  rest   same-layer overlap, l vs l
//   mode 1  sweep  descent of A vs B resting (A's layer k vs B's j >= k)
// Purity: geometry is immutable, offsets integral -> never invalidated.
inline bool pairMemo(const OrientGeom& a, double ax, double ay,
                     const OrientGeom& b, double bx, double by,
                     int mode, bool& out) {
    const long dx = std::lround(bx - ax), dy = std::lround(by - ay);
    if (std::abs((bx - ax) - (double)dx) > 1e-9 ||
        std::abs((by - ay) - (double)dy) > 1e-9) return false;
    static thread_local std::vector<OvSlot> tb(1u << 21);
    uint64_t h = (uint64_t)(uintptr_t)&a * 0x9E3779B97F4A7C15ull
               ^ (uint64_t)(uintptr_t)&b * 0xC2B2AE3D27D4EB4Full
               ^ ((uint64_t)(uint32_t)mode << 56)
               ^ (uint64_t)(uint32_t)(int32_t)dx * 0x165667B19E3779F9ull
               ^ (uint64_t)(uint32_t)(int32_t)dy * 0x27D4EB2F165667C5ull;
    h |= 2ull;
    OvSlot& s = tb[h & (tb.size() - 1)];
    if (s.key == h) { out = (s.val & 1) != 0; return true; }
    // compute, store
    bool r = false;
    if (mode == 0) {
        int L = (int)std::min(a.layers.size(), b.layers.size());
        for (int l = 0; l < L && !r; l++)
            r = overlapsLay(a, l, ax, ay, b, l, bx, by);
    } else {
        int nA = (int)a.layers.size(), nB = (int)b.layers.size();
        for (int k = 0; k < nA && !r; k++)
            for (int j = k; j < nB && !r; j++)
                r = overlapsLay(a, k, ax, ay, b, j, bx, by);
    }
    s.key = h; s.val = r ? 1 : 0;
    out = r;
    return true;
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
        bool hit;
        if (pairMemo(nu, x, y, *ex.g, ex.x, ex.y, 1, hit)) {
            if (hit) return false;
            continue;
        }
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
        bool hit;
        if (pairMemo(*tg.g, tg.x, tg.y, *ex.g, ex.x, ex.y, 1, hit)) {
            if (hit) return false;
            continue;
        }
        int nEx = (int)ex.g->layers.size();
        for (int k = 0; k < nT; k++)
            for (int j = k; j < nEx; j++)
                if (overlapsLay(*tg.g, k, tg.x, tg.y, *ex.g, j, ex.x, ex.y))
                    return false;
    }
    return true;
}

} // namespace crane

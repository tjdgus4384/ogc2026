// geometry.hpp -- positive-area overlap test for simple polygons (possibly
// concave, CCW or CW, closed implicit). Semantics must match Shapely:
//   overlaps == (A ∩ B).area > 0
// Edge-touching / vertex-touching (measure-zero intersection) => false.
#pragma once
#include <vector>
#include <cmath>
#include <algorithm>

namespace geom {

struct Pt { double x, y; };
using Poly = std::vector<Pt>;

constexpr double EPS = 1e-9;

inline double cross(const Pt& o, const Pt& a, const Pt& b) {
    return (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x);
}

struct BBox { double x0, y0, x1, y1; };

inline BBox bbox(const Poly& p) {
    BBox b{1e18, 1e18, -1e18, -1e18};
    for (auto& v : p) {
        b.x0 = std::min(b.x0, v.x); b.y0 = std::min(b.y0, v.y);
        b.x1 = std::max(b.x1, v.x); b.y1 = std::max(b.y1, v.y);
    }
    return b;
}

inline bool bboxOverlap(const BBox& a, const BBox& b) {
    return a.x0 < b.x1 - EPS && b.x0 < a.x1 - EPS &&
           a.y0 < b.y1 - EPS && b.y0 < a.y1 - EPS;
}

// Proper (interior) segment crossing: the open segments intersect at a single
// interior point. Collinear overlap and endpoint touching are NOT proper.
inline bool properCross(const Pt& p1, const Pt& p2, const Pt& q1, const Pt& q2) {
    double d1 = cross(q1, q2, p1);
    double d2 = cross(q1, q2, p2);
    double d3 = cross(p1, p2, q1);
    double d4 = cross(p1, p2, q2);
    return ((d1 > EPS && d2 < -EPS) || (d1 < -EPS && d2 > EPS)) &&
           ((d3 > EPS && d4 < -EPS) || (d3 < -EPS && d4 > EPS));
}

// Point strictly inside polygon (boundary => false). Ray casting with careful
// handling: count crossings of upward edges; boundary detected via distance.
inline bool onSegment(const Pt& a, const Pt& b, const Pt& p) {
    if (std::fabs(cross(a, b, p)) > EPS * (std::fabs(b.x-a.x)+std::fabs(b.y-a.y)+1)) return false;
    return p.x > std::min(a.x,b.x)-EPS && p.x < std::max(a.x,b.x)+EPS &&
           p.y > std::min(a.y,b.y)-EPS && p.y < std::max(a.y,b.y)+EPS;
}

inline bool strictlyInside(const Poly& poly, const Pt& p) {
    int n = (int)poly.size();
    // boundary check first
    for (int i = 0; i < n; i++) {
        if (onSegment(poly[i], poly[(i+1)%n], p)) return false;
    }
    bool in = false;
    for (int i = 0, j = n - 1; i < n; j = i++) {
        const Pt& a = poly[i]; const Pt& b = poly[j];
        if (((a.y > p.y) != (b.y > p.y))) {
            double xint = (b.x - a.x) * (p.y - a.y) / (b.y - a.y) + a.x;
            if (p.x < xint) in = !in;
        }
    }
    return in;
}

// Representative strictly-interior point of a simple polygon (ear centroid).
inline Pt interiorPoint(const Poly& poly) {
    int n = (int)poly.size();
    // signed area to know orientation
    double A = 0;
    for (int i = 0, j = n - 1; i < n; j = i++)
        A += (poly[j].x * poly[i].y - poly[i].x * poly[j].y);
    double orient = (A > 0) ? 1.0 : -1.0;
    // find a convex ear vertex and take centroid of the ear triangle shrunk
    for (int i = 0; i < n; i++) {
        const Pt& prev = poly[(i + n - 1) % n];
        const Pt& cur  = poly[i];
        const Pt& nxt  = poly[(i + 1) % n];
        if (orient * cross(prev, cur, nxt) > EPS) {
            Pt c{(prev.x + cur.x + nxt.x) / 3.0, (prev.y + cur.y + nxt.y) / 3.0};
            // ensure no other vertex inside the ear (true ear); if not, still
            // try candidate: midpoint pulled toward cur
            Pt cand = c;
            for (int t = 0; t < 6; t++) {
                if (strictlyInside(poly, cand)) return cand;
                cand.x = (cand.x + cur.x) / 2.0;   // pull toward the convex vertex
                cand.y = (cand.y + cur.y) / 2.0;
            }
        }
    }
    // fallback: average of vertices (fine for convex)
    Pt c{0, 0};
    for (auto& v : poly) { c.x += v.x; c.y += v.y; }
    c.x /= n; c.y /= n;
    return c;
}

// Positive-area overlap of two simple polygons.
inline bool overlapsArea(const Poly& A, const Poly& B) {
    if (A.size() < 3 || B.size() < 3) return false;
    if (!bboxOverlap(bbox(A), bbox(B))) return false;
    int na = (int)A.size(), nb = (int)B.size();
    // 1) any proper edge crossing => interiors intersect => positive area
    for (int i = 0; i < na; i++) {
        const Pt& a1 = A[i]; const Pt& a2 = A[(i + 1) % na];
        for (int j = 0; j < nb; j++) {
            if (properCross(a1, a2, B[j], B[(j + 1) % nb])) return true;
        }
    }
    // 2) no proper crossing: either disjoint, touching, or one contains other.
    //    test a strictly-interior representative point of each in the other.
    Pt ia = interiorPoint(A);
    if (strictlyInside(B, ia)) return true;
    Pt ib = interiorPoint(B);
    if (strictlyInside(A, ib)) return true;
    // 3) tricky case: equal or collinear-overlapping boundaries with same
    //    interior (A == B shifted along shared edges). Handled by (2) in
    //    practice since interior points coincide; also test vertices strictly
    //    inside as extra safety.
    for (auto& v : A) if (strictlyInside(B, v)) return true;
    for (auto& v : B) if (strictlyInside(A, v)) return true;
    return false;
}


// Same verdict as overlapsArea, but the caller supplies a strictly-interior
// sample of each polygon (precomputed once per rigid shape and translated by
// the caller) and has already applied a bbox gate -- so the O(V^2)-ish
// interiorPoint search and the bbox() recompute are skipped. Predicates are
// otherwise byte-for-byte the same (sec 166).
inline bool overlapsAreaIP(const Poly& A, const Pt& ipA,
                           const Poly& B, const Pt& ipB) {
    if (A.size() < 3 || B.size() < 3) return false;
    int na = (int)A.size(), nb = (int)B.size();
    for (int i = 0; i < na; i++) {
        const Pt& a1 = A[i]; const Pt& a2 = A[(i + 1) % na];
        for (int j = 0; j < nb; j++) {
            if (properCross(a1, a2, B[j], B[(j + 1) % nb])) return true;
        }
    }
    if (strictlyInside(B, ipA)) return true;
    if (strictlyInside(A, ipB)) return true;
    for (auto& v : A) if (strictlyInside(B, v)) return true;
    for (auto& v : B) if (strictlyInside(A, v)) return true;
    return false;
}
} // namespace geom

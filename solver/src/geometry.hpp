// geometry.hpp -- positive-area overlap test for simple polygons (possibly
// concave, CCW or CW, closed implicit). Semantics must match Shapely's
//   overlaps == (A ∩ B).area > 0
// as used by the official checker: edge- or vertex-touching (measure-zero
// intersection) is NOT an overlap.
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
inline double signedArea2(const Poly& p) {
    double a = 0.0;
    for (int i = 0, j = (int)p.size() - 1; i < (int)p.size(); j = i++)
        a += p[j].x * p[i].y - p[i].x * p[j].y;
    return a;
}

inline bool collinearInteriorOverlap(const Pt& a1, const Pt& a2,
                                     double orientA,
                                     const Pt& b1, const Pt& b2,
                                     double orientB) {
    const double adx = a2.x - a1.x, ady = a2.y - a1.y;
    const double bdx = b2.x - b1.x, bdy = b2.y - b1.y;
    const double scale = std::hypot(adx, ady) + std::hypot(bdx, bdy) + 1.0;
    if (std::fabs(adx * bdy - ady * bdx) > EPS * scale ||
        std::fabs(cross(a1, a2, b1)) > EPS * scale ||
        std::fabs(cross(a1, a2, b2)) > EPS * scale)
        return false;
    const bool useX = std::fabs(adx) >= std::fabs(ady);
    const double alo = std::min(useX ? a1.x : a1.y, useX ? a2.x : a2.y);
    const double ahi = std::max(useX ? a1.x : a1.y, useX ? a2.x : a2.y);
    const double blo = std::min(useX ? b1.x : b1.y, useX ? b2.x : b2.y);
    const double bhi = std::max(useX ? b1.x : b1.y, useX ? b2.x : b2.y);
    if (std::min(ahi, bhi) - std::max(alo, blo) <= EPS)
        return false;
    // For a consistently oriented polygon, orient*leftNormal(edge) points
    // inward. Equal inward half-planes along a shared segment imply a
    // positive-area intersection; opposite half-planes are boundary touch.
    return orientA * orientB * (adx * bdx + ady * bdy) > EPS;
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

inline bool onSegment(const Pt& a, const Pt& b, const Pt& p) {
    if (std::fabs(cross(a, b, p)) > EPS * (std::fabs(b.x-a.x)+std::fabs(b.y-a.y)+1)) return false;
    return p.x > std::min(a.x,b.x)-EPS && p.x < std::max(a.x,b.x)+EPS &&
           p.y > std::min(a.y,b.y)-EPS && p.y < std::max(a.y,b.y)+EPS;
}

// Point strictly inside polygon (boundary => false). Ray casting.
inline bool strictlyInside(const Poly& poly, const Pt& p) {
    int n = (int)poly.size();
    for (int i = 0; i < n; i++)
        if (onSegment(poly[i], poly[(i+1)%n], p)) return false;
    bool in = false;
    for (int i = 0, j = n - 1; i < n; j = i++) {
        const Pt& a = poly[i]; const Pt& b = poly[j];
        if ((a.y > p.y) != (b.y > p.y)) {
            double xint = (b.x - a.x) * (p.y - a.y) / (b.y - a.y) + a.x;
            if (p.x < xint) in = !in;
        }
    }
    return in;
}

// Representative strictly-interior point of a simple polygon (ear centroid,
// pulled toward the convex vertex until strictly inside). Cached per rigid
// shape at load; translated by the caller at test time.
inline Pt interiorPoint(const Poly& poly) {
    int n = (int)poly.size();
    double A = 0;
    for (int i = 0, j = n - 1; i < n; j = i++)
        A += (poly[j].x * poly[i].y - poly[i].x * poly[j].y);
    double orient = (A > 0) ? 1.0 : -1.0;
    for (int i = 0; i < n; i++) {
        const Pt& prev = poly[(i + n - 1) % n];
        const Pt& cur  = poly[i];
        const Pt& nxt  = poly[(i + 1) % n];
        if (orient * cross(prev, cur, nxt) > EPS) {
            Pt cand{(prev.x + cur.x + nxt.x) / 3.0, (prev.y + cur.y + nxt.y) / 3.0};
            for (int t = 0; t < 6; t++) {
                if (strictlyInside(poly, cand)) return cand;
                cand.x = (cand.x + cur.x) / 2.0;
                cand.y = (cand.y + cur.y) / 2.0;
            }
        }
    }
    Pt c{0, 0};
    for (auto& v : poly) { c.x += v.x; c.y += v.y; }
    c.x /= n; c.y /= n;
    return c;
}

// Positive-area overlap with B translated by (bx,by) relative to A.
// This avoids materializing translated polygons in the exact hot path.
inline bool overlapsAreaOffset(const Poly& A, const Pt& ipA,
                               const Poly& B, const Pt& ipB,
                               double bx, double by) {
    if (A.size() < 3 || B.size() < 3) return false;
    const int na = (int)A.size(), nb = (int)B.size();
    const double orientA = signedArea2(A) >= 0.0 ? 1.0 : -1.0;
    const double orientB = signedArea2(B) >= 0.0 ? 1.0 : -1.0;
    for (int i = 0; i < na; i++) {
        const Pt& a1 = A[i]; const Pt& a2 = A[(i + 1) % na];
        for (int j = 0; j < nb; j++) {
            const Pt b1{B[j].x + bx, B[j].y + by};
            const Pt b2{B[(j + 1) % nb].x + bx,
                        B[(j + 1) % nb].y + by};
            if (properCross(a1, a2, b1, b2) ||
                collinearInteriorOverlap(a1, a2, orientA,
                                         b1, b2, orientB))
                return true;
        }
    }
    if (strictlyInside(B, {ipA.x - bx, ipA.y - by})) return true;
    if (strictlyInside(A, {ipB.x + bx, ipB.y + by})) return true;
    for (const auto& v : A)
        if (strictlyInside(B, {v.x - bx, v.y - by})) return true;
    for (const auto& v : B)
        if (strictlyInside(A, {v.x + bx, v.y + by})) return true;
    return false;
}

inline bool overlapsAreaIP(const Poly& A, const Pt& ipA,
                           const Poly& B, const Pt& ipB) {
    return overlapsAreaOffset(A, ipA, B, ipB, 0.0, 0.0);
}

} // namespace geom

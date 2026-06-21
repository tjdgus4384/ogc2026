"""nfp.py -- No-Fit-Polygon machinery for fast TRUE interlock.

NFP(A,B) = set of translations d such that A and (B+d) have overlapping interiors = A (+) (-B)
(Minkowski sum). For our placement: blocks i (at pos_i) and k (at pos_k) with offset d = pos_k - pos_i,
- COLLISION (same level l): d in interior(NFP(layer_l_i, layer_l_k)) ; Phi_{ik} = union over l of those.
- CRANE (i obstructed by k, layers a<=b): d in interior(NFP(layer_a_i, layer_b_k)) ; Psi_{i<-k} = union.
Then a placement is fast: just test the offset point against precomputed Phi/Psi polygons (point-in-poly).

Minkowski of non-convex polygons via triangulation into convex pieces + pairwise convex Minkowski + union.
Triangles are kept whenever they intersect the polygon and used WHOLE (not clipped) -> the resulting NFP is
a conservative SUPERSET of the true region (never smaller) -> never reports a colliding offset as free
(no false-feasible) -> safe; at worst slightly over-forbids (a little less interlock density). Final
solutions are still validated by utils.check_feasibility.
"""
from shapely.geometry import Polygon, MultiPoint
from shapely.ops import triangulate, unary_union


def _poly(verts):
    p = Polygon(verts)
    return p if p.is_valid else p.buffer(0)


def _convex_pieces(poly):
    if poly.is_empty:
        return []
    tris = []
    for t in triangulate(poly):
        inter = poly.intersection(t)
        if not inter.is_empty and inter.area > 1e-9:
            tris.append(t)          # WHOLE triangle (superset) -> conservative NFP
    return tris or [poly.convex_hull]


def _mink_convex(A, B):
    pts = [(ax + bx, ay + by) for (ax, ay) in A.exterior.coords[:-1] for (bx, by) in B.exterior.coords[:-1]]
    return MultiPoint(pts).convex_hull


def minkowski(A, B):
    pa = _convex_pieces(A); pb = _convex_pieces(B)
    parts = [_mink_convex(a, b) for a in pa for b in pb]
    return unary_union(parts)


def nfp(A, B):
    """Offsets d where interior(A) ∩ interior(B+d) != empty = A (+) (-B)."""
    Bref = Polygon([(-x, -y) for (x, y) in B.exterior.coords[:-1]])
    return minkowski(A, Bref)


def build_phi(layers_i, layers_k):
    """Collision region: union over same level l of NFP(layer_l_i, layer_l_k)."""
    parts = []
    for l in range(min(len(layers_i), len(layers_k))):
        parts.append(nfp(layers_i[l], layers_k[l]))
    return unary_union(parts) if parts else Polygon()


def build_psi(layers_i, layers_k):
    """Crane region (i obstructed by k): union over a<=b of NFP(layer_a_i, layer_b_k)."""
    parts = []
    for a in range(len(layers_i)):
        for b in range(a, len(layers_k)):
            parts.append(nfp(layers_i[a], layers_k[b]))
    return unary_union(parts) if parts else Polygon()


if __name__ == "__main__":
    # self-test: two unit squares. NFP should be a 2x2 square centered at origin (offsets where they overlap).
    from shapely.geometry import Point
    A = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
    B = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
    n = nfp(A, B)
    print("NFP(unit,unit) bounds:", [round(v, 2) for v in n.bounds], "(expect -1,-1,1,1)")
    print("offset (0,0) inside (overlap)?", n.contains(Point(0, 0)), "(expect True)")
    print("offset (1.5,0) inside?", n.contains(Point(1.5, 0)), "(expect False)")
    print("offset (0.5,0) inside (overlap)?", n.contains(Point(0.5, 0)), "(expect True)")

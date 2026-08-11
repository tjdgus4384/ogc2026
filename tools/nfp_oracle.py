"""Conservative layer-aware polygon oracle for bay-plan pricing.

Coordinates accepted by this module are official reference-point coordinates: the
reference point is the first vertex of the first layer of the selected orientation.
A placement is rejected only when its offset is in the *interior* of a conservative
forbidden region.  This matches the official checker's positive-area collision
semantics: edge, vertex, and collinear boundary contact remain available to
pricing.  A boundary point of a conservative superset cannot be interior to the
true forbidden set, because an open neighbourhood inside the true set would also
be an open neighbourhood inside its superset.

The NFP construction decomposes non-convex polygons into covered triangles.  If
Shapely cannot prove that decomposition covers an input polygon, the explicitly
named ``conservative_convex_hull`` fallback is used instead.  That fallback can
over-forbid concave pockets but cannot admit a collision.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from itertools import product
from math import ceil, floor
from typing import Iterable, Sequence

from shapely.affinity import translate
from shapely.geometry import GeometryCollection, MultiPoint, Point, Polygon
from shapely.ops import triangulate, unary_union

Coordinate = tuple[float, float]


def _polygon(vertices: Sequence[Sequence[float]]) -> Polygon:
    """Build a valid, non-empty area polygon or reject the orientation.

    Rejecting malformed input is fail-closed: a pricing caller cannot accidentally
    treat unknown geometry as an empty footprint.
    """
    if len(vertices) < 3:
        raise ValueError("a layer requires at least three vertices")
    polygon = Polygon(vertices)
    if not polygon.is_valid or polygon.is_empty or polygon.area <= 0 or polygon.geom_type != "Polygon":
        raise ValueError("layer is not a valid simple positive-area polygon")
    return polygon


def _polygons(geometry) -> list[Polygon]:
    if geometry.is_empty:
        return []
    if geometry.geom_type == "Polygon":
        return [geometry]
    if geometry.geom_type in ("MultiPolygon", "GeometryCollection"):
        return [part for part in geometry.geoms if part.geom_type == "Polygon" and part.area > 0]
    return []


def conservative_convex_hull(polygon: Polygon) -> Polygon:
    """Over-forbidding decomposition fallback when triangulation is not covering."""
    return polygon.convex_hull


def _convex_pieces_conservative(polygon: Polygon) -> list[Polygon]:
    """Return a covering convex decomposition, with a conservative hull fallback."""
    pieces: list[Polygon] = []
    for triangle in triangulate(polygon):
        # A Delaunay triangle crossing a concave boundary is not a valid piece.
        if polygon.covers(triangle) and triangle.area > 0:
            pieces.append(triangle)
    if not pieces:
        return [conservative_convex_hull(polygon)]
    covered = unary_union(pieces)
    # ``covers`` is important: falling back on numerical uncertainty only expands.
    if not covered.covers(polygon):
        return [conservative_convex_hull(polygon)]
    return pieces


def _convex_minkowski(left: Polygon, right: Polygon) -> Polygon:
    """Minkowski sum of two convex polygons, computed from pairwise vertices."""
    points = [(ax + bx, ay + by)
              for ax, ay in left.exterior.coords[:-1]
              for bx, by in right.exterior.coords[:-1]]
    return MultiPoint(points).convex_hull


def conservative_minkowski(left: Polygon, right: Polygon):
    """A conservative Minkowski sum for possibly non-convex simple polygons."""
    parts = [_convex_minkowski(a, b)
             for a, b in product(_convex_pieces_conservative(left),
                                  _convex_pieces_conservative(right))]
    return unary_union(parts) if parts else GeometryCollection()


def conservative_nfp(fixed: Polygon, moving: Polygon):
    """Conservative closed translation region for touching or overlapping polygons.

    Its interior contains every offset producing positive-area overlap.  Consumers
    must use strict interior membership, not closed coverage: NFP boundaries
    represent checker-feasible zero-area contacts.
    """
    reflected = Polygon([(-x, -y) for x, y in moving.exterior.coords[:-1]])
    return conservative_minkowski(fixed, reflected)


@dataclass(frozen=True)
class OrientedBlock:
    """One official orientation expressed relative to its official reference point."""
    layers: tuple[tuple[Coordinate, ...], ...]

    @classmethod
    def from_orientation(cls, orientation: dict) -> "OrientedBlock":
        """Parse an instance ``shape[orientation]`` entry without modifying it."""
        raw_layers = orientation.get("layers")
        if not isinstance(raw_layers, list) or not raw_layers or not raw_layers[0]:
            raise ValueError("orientation must provide a non-empty first layer")
        ref_x, ref_y = raw_layers[0][0]
        layers = []
        for raw_layer in raw_layers:
            if not raw_layer:
                raise ValueError("orientation contains an empty layer")
            local = tuple((float(x) - float(ref_x), float(y) - float(ref_y))
                          for x, y in raw_layer)
            _polygon(local)  # validate now, rather than silently skipping later
            layers.append(local)
        return cls(tuple(layers))

    @cached_property
    def polygons(self) -> tuple[Polygon, ...]:
        return tuple(_polygon(layer) for layer in self.layers)

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        return unary_union(self.polygons).bounds


@dataclass(frozen=True)
class PlacedBlock:
    """An oriented block at an official integer reference-point position."""
    block: OrientedBlock
    x: int
    y: int

    @property
    def position(self) -> tuple[int, int]:
        return self.x, self.y


def _translated_forbidden(moving: OrientedBlock, fixed: PlacedBlock,
                           pairs: Iterable[tuple[int, int]]):
    regions = [translate(conservative_nfp(fixed.block.polygons[fixed_layer],
                                          moving.polygons[moving_layer]),
                         xoff=fixed.x, yoff=fixed.y)
               for moving_layer, fixed_layer in pairs]
    return unary_union(regions) if regions else GeometryCollection()


def forbidden_same_layer(moving: OrientedBlock, fixed: PlacedBlock):
    """Conservative forbidden moving-reference positions for resting collisions."""
    return _translated_forbidden(moving, fixed,
                                 ((layer, layer)
                                  for layer in range(min(len(moving.layers), len(fixed.block.layers)))))


def forbidden_entry_sweep(moving: OrientedBlock, fixed: PlacedBlock):
    """Conservative forbidden positions for the checker's descent ``j >= k`` rule."""
    return _translated_forbidden(moving, fixed,
                                 ((moving_layer, fixed_layer)
                                  for moving_layer in range(len(moving.layers))
                                  for fixed_layer in range(moving_layer, len(fixed.block.layers))))


def forbidden_exit_sweep(moving: OrientedBlock, fixed: PlacedBlock):
    """Conservative forbidden positions for the checker's ascent ``j >= k`` rule."""
    # The current official checker uses the same vertical-pair rule for exit.
    return forbidden_entry_sweep(moving, fixed)


def contains_reference(forbidden, position: tuple[float, float]) -> bool:
    """Whether ``position`` is strictly inside a conservative forbidden region.

    The strict test preserves NFP boundary contacts.  Since the true forbidden
    region is a subset of this conservative region, a boundary point here cannot
    lie in the interior of the true forbidden region.
    """
    return forbidden.contains(Point(position))


def within_bay(block: OrientedBlock, position: tuple[int, int], width: int, height: int) -> bool:
    """Check every layer against the rectangular bay, with no tolerance."""
    x, y = position
    return all(min_x + x >= 0 and min_y + y >= 0 and max_x + x <= width and max_y + y <= height
               for min_x, min_y, max_x, max_y in (polygon.bounds for polygon in block.polygons))


def _integer_neighbors(value: float) -> tuple[int, ...]:
    """Integer floor/ceil neighbours, retaining integral boundary contact exactly."""
    return tuple(sorted({floor(value), ceil(value)}))


def _boundary_coordinates(geometry) -> tuple[tuple[int, ...], tuple[int, ...]]:
    xs: set[int] = set()
    ys: set[int] = set()
    for polygon in _polygons(geometry):
        for x, y in polygon.exterior.coords:
            xs.update(_integer_neighbors(x))
            ys.update(_integer_neighbors(y))
        for ring in polygon.interiors:
            for x, y in ring.coords:
                xs.update(_integer_neighbors(x))
                ys.update(_integer_neighbors(y))
    return tuple(sorted(xs)), tuple(sorted(ys))


def wall_contact_candidates(moving: OrientedBlock, fixed: Iterable[PlacedBlock],
                            width: int, height: int, *, sweep: str = "entry",
                            max_candidates: int = 100_000) -> tuple[tuple[int, int], ...]:
    """Generate deterministic integer wall/contact candidates without an incumbent.

    Contacts come from conservative NFP boundary vertices and include floor/ceil
    neighbours.  Wall contacts use every layer's bounds.  ``sweep`` is ``entry``
    or ``exit`` and determines which directional crane NFP is used.  The finite
    cap raises rather than truncating, so omitted candidates are never disguised
    as a complete candidate set.
    """
    if width < 0 or height < 0:
        raise ValueError("bay dimensions must be non-negative")
    if sweep not in {"entry", "exit"}:
        raise ValueError("sweep must be 'entry' or 'exit'")
    xs: set[int] = {0, width}
    ys: set[int] = {0, height}
    for polygon in moving.polygons:
        min_x, min_y, max_x, max_y = polygon.bounds
        for value in (-min_x, width - max_x):
            xs.update(_integer_neighbors(value))
        for value in (-min_y, height - max_y):
            ys.update(_integer_neighbors(value))
    for blocker in fixed:
        region = forbidden_entry_sweep(moving, blocker) if sweep == "entry" else forbidden_exit_sweep(moving, blocker)
        boundary_xs, boundary_ys = _boundary_coordinates(region)
        xs.update(boundary_xs)
        ys.update(boundary_ys)
    candidates = tuple((x, y) for x in sorted(xs) for y in sorted(ys)
                       if within_bay(moving, (x, y), width, height))
    if len(candidates) > max_candidates:
        raise ValueError("candidate set exceeds max_candidates; refusing to truncate")
    return candidates


def feasible_for_entry(moving: OrientedBlock, position: tuple[int, int], fixed: Iterable[PlacedBlock],
                       width: int, height: int) -> bool:
    """Fail-closed oracle acceptance for a proposed entry placement."""
    if not within_bay(moving, position, width, height):
        return False
    return not any(contains_reference(forbidden_entry_sweep(moving, blocker), position) for blocker in fixed)

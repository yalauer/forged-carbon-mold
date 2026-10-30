from __future__ import annotations

from dataclasses import dataclass, field
import math
from pathlib import Path
from typing import Any

import cadquery as cq
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.BRepBndLib import BRepBndLib
from OCP.Bnd import Bnd_Box
from shapely import union_all
from shapely.geometry import Polygon

CONTOUR_TOLERANCE_MM = 0.01
CONTOUR_INSET_MM = 0.05


class CadError(ValueError):
    """A STEP model or CAD operation could not be completed safely."""


@dataclass(frozen=True)
class ModelInfo:
    solid_count: int
    bounds_mm: dict[str, float]
    bounds_min_mm: dict[str, float]
    bounds_max_mm: dict[str, float]
    volume_mm3: float
    valid: bool


@dataclass(frozen=True)
class MoldResult:
    upper: cq.Shape
    lower: cq.Shape
    blank: cq.Shape
    part: cq.Shape
    split_z: float
    punch_depth_mm: float
    info: ModelInfo
    features: dict[str, Any] = field(default_factory=dict)


def import_step(path: Path) -> cq.Shape:
    if path.suffix.lower() not in {".step", ".stp"}:
        raise CadError("Nur STEP-Dateien mit der Endung .step oder .stp werden unterstützt.")
    if not path.is_file() or path.stat().st_size == 0:
        raise CadError("Die STEP-Datei fehlt oder ist leer.")
    try:
        imported = cq.importers.importStep(str(path))
        solids = imported.solids().vals()
    except Exception as exc:
        raise CadError(f"STEP-Import fehlgeschlagen: {exc}") from exc
    if not solids:
        raise CadError("Die STEP-Datei enthält keine geschlossenen Volumenkörper.")
    if any(not solid.isValid() or solid.Volume() <= 0 for solid in solids):
        raise CadError("Die STEP-Datei enthält einen ungültigen oder leeren Volumenkörper.")
    return cq.Compound.makeCompound(solids) if len(solids) > 1 else solids[0]


def _exact_bounds(shape: cq.Shape) -> cq.BoundBox:
    bounds = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape.wrapped, bounds, False, False)
    return cq.BoundBox(bounds)


def model_info(shape: cq.Shape) -> ModelInfo:
    box = _exact_bounds(shape)
    return ModelInfo(
        solid_count=len(shape.Solids()),
        bounds_mm={"x": box.xlen, "y": box.ylen, "z": box.zlen},
        bounds_min_mm={"x": box.xmin, "y": box.ymin, "z": box.zmin},
        bounds_max_mm={"x": box.xmax, "y": box.ymax, "z": box.zmax},
        volume_mm3=shape.Volume(),
        valid=shape.isValid(),
    )


def _z_projection(part: cq.Shape) -> list[cq.Face]:
    """Toleranced Z footprint for the guide walls; forming faces stay B-Rep."""
    # Explicit absolute deflection, independent of model size and viewer mesh.
    part = part.copy()
    BRepMesh_IncrementalMesh(part.wrapped, CONTOUR_TOLERANCE_MM, False, 0.1, True)
    projected_faces = []
    for face in part.Faces():
        vertices, triangles = face.tessellate(CONTOUR_TOLERANCE_MM)
        polygons = []
        for triangle in triangles:
            a, b, c = (vertices[index] for index in triangle)
            signed_area = (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x)
            if abs(signed_area) > 1e-10:
                polygons.append(Polygon([(a.x, a.y), (b.x, b.y), (c.x, c.y)]))
        if polygons:
            projected = union_all(polygons, grid_size=1e-5)
            projected_faces.append(projected)
    if not projected_faces:
        raise CadError("Die Z-Projektion enthält keine geschlossene Bauteilkontur.")
    projection = union_all(projected_faces, grid_size=1e-5)
    regions = list(projection.geoms) if projection.geom_type == "MultiPolygon" else [projection]
    # Sub-grid pinholes from rounded triangulation seams are not CAD holes.
    projection = union_all([
        Polygon(region.exterior, [
            ring for ring in region.interiors
            if Polygon(ring).area > CONTOUR_TOLERANCE_MM ** 2
        ])
        for region in regions
    ])
    footprint = projection.buffer(-CONTOUR_INSET_MM).simplify(
        CONTOUR_TOLERANCE_MM / 2, preserve_topology=True,
    )
    if footprint.is_empty:
        raise CadError("Die Bauteilkontur ist zu schmal für den Innenversatz von 0,05 mm.")
    regions = list(footprint.geoms) if footprint.geom_type == "MultiPolygon" else [footprint]
    profiles = []
    for region in regions:
        outer = cq.Wire.makePolygon([cq.Vector(x, y, 0) for x, y in region.exterior.coords], close=True)
        holes = [
            cq.Wire.makePolygon([cq.Vector(x, y, 0) for x, y in ring.coords], close=True)
            for ring in region.interiors
        ]
        profile = cq.Face.makeFromWires(outer, holes)
        if profile.normalAt().z < 0:
            profile = cq.Shape.cast(profile.wrapped.Reversed())
        profiles.append(profile)
    return profiles


def build_two_piece_mold(
    part: cq.Shape,
    *,
    wall_mm: float = 20.0,
    punch_depth_mm: float = 10.0,
) -> MoldResult:
    if not math.isfinite(wall_mm) or wall_mm <= 0:
        raise CadError("Die Wandstärke muss größer als 0 mm sein.")
    if not math.isfinite(punch_depth_mm) or not 0 < punch_depth_mm <= 500:
        raise CadError("Die Stempeltiefe muss größer als 0 und höchstens 500 mm sein.")
    box = _exact_bounds(part)
    if min(box.xlen, box.ylen, box.zlen) <= 0:
        raise CadError("Das Bauteil besitzt eine leere Begrenzungsbox.")
    split = box.zmax + punch_depth_mm
    center_x, center_y = (box.xmin + box.xmax) / 2, (box.ymin + box.ymax) / 2

    def block(bottom: float, top: float) -> cq.Shape:
        return cq.Workplane("XY").box(
            box.xlen + 2 * wall_mm, box.ylen + 2 * wall_mm, top - bottom,
        ).translate((center_x, center_y, (bottom + top) / 2)).val()

    lower_stock = block(box.zmin - wall_mm, split)
    upper_head = block(split, split + wall_mm)
    blank = block(box.zmin - wall_mm, split + wall_mm)
    try:
        profiles = _z_projection(part)
        columns = [
            cq.Solid.extrudeLinear(
                profile.translate((0, 0, box.zmin)),
                (0, 0, split + wall_mm - box.zmin),
            )
            for profile in profiles
        ]
        column = columns[0].fuse(*columns[1:]).clean() if len(columns) > 1 else columns[0]
        # Subtract the complete part, then keep only free-space solids that
        # connect to the head. This gives the punch its complementary lower
        # surface rather than overlapping the positive part geometry.
        free_space = column.cut(part)
        free_solids = free_space.Solids()
        tips = [solid for solid in free_solids if solid.BoundingBox().zmax > split + wall_mm / 2]
        if not tips:
            raise CadError("Aus der Bauteilkontur konnte kein verbundener Stempel erzeugt werden.")
        upper = upper_head.fuse(*tips).clean()
        lower = lower_stock.cut(part, upper)
        # Do not unify the lower's curved forming faces: on imported spline
        # models this can take minutes without changing the solid geometry.
    except Exception as exc:
        raise CadError(f"Formherstellung oder Stempelkonstruktion fehlgeschlagen: {exc}") from exc
    if any(not shape.isValid() or shape.Volume() <= 0 for shape in (lower, upper)):
        raise CadError("Die Berechnung erzeugt ein leeres oder ungültiges Formsegment.")
    if any(len(shape.Solids()) != 1 for shape in (lower, upper)):
        raise CadError("Diese Geometrie erzeugt lose Forminseln und benötigt zusätzliche Formsegmente.")
    # Trimmed imported spline faces accumulate kernel/integration tolerances.
    # A 0.1 ppm stock-volume allowance still catches missing/overlapping parts.
    tolerance = max(1e-5, blank.Volume() * 1e-7)
    # The Boolean construction partitions the stock into disjoint regions.
    # Check conservation here rather than repeating full tangential Boolean
    # intersections across every shared forming face.
    volume_error = abs(blank.Volume() - lower.Volume() - upper.Volume() - part.Volume())
    if volume_error > tolerance:
        # Adaptive integration avoids false failures on trimmed STEP splines.
        volume_error = abs(
            blank.Volume(tol=1e-9) - lower.Volume(tol=1e-9)
            - upper.Volume(tol=1e-9) - part.Volume(tol=1e-9)
        )
    if volume_error > tolerance:
        raise CadError(f"Der geschlossene Formhohlraum entspricht nicht dem vollständigen Bauteil (Volumenabweichung {volume_error:.6f} mm³).")
    return MoldResult(
        upper=upper,
        lower=lower,
        blank=blank,
        part=part,
        split_z=split,
        punch_depth_mm=punch_depth_mm,
        info=model_info(part),
    )


def export_step(shape: cq.Shape, path: Path) -> None:
    if not shape.isValid() or not shape.Solids():
        raise CadError("Ungültige CAD-Geometrie kann nicht exportiert werden.")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        cq.exporters.export(shape, str(path), exportType="STEP")
    except Exception as exc:
        raise CadError(f"STEP-Export fehlgeschlagen: {exc}") from exc
    roundtrip = import_step(path)
    tolerance = max(1e-5, shape.Volume() * 1e-8)
    if len(roundtrip.Solids()) != len(shape.Solids()):
        raise CadError("STEP-Rundlaufprüfung fehlgeschlagen: Körperzahl stimmt nicht überein.")
    if abs(roundtrip.Volume() - shape.Volume()) > tolerance:
        precise_original = shape.Volume(tol=1e-9)
        precise_reopened = roundtrip.Volume(tol=1e-9)
        # Allow 1 ppm for reparameterization of trimmed STEP spline faces;
        # body count and exact CAD bounds are checked independently below.
        if abs(precise_original - precise_reopened) > max(1e-5, precise_original * 1e-6):
            raise CadError(f"STEP-Rundlaufprüfung fehlgeschlagen: Volumenabweichung {abs(precise_original - precise_reopened):.6f} mm³.")
    original_bounds, reopened_bounds = _exact_bounds(shape), _exact_bounds(roundtrip)
    bound_tolerance = 1e-5
    for axis in ("xmin", "ymin", "zmin", "xmax", "ymax", "zmax"):
        if abs(getattr(original_bounds, axis) - getattr(reopened_bounds, axis)) > bound_tolerance:
            raise CadError("STEP-Rundlaufprüfung fehlgeschlagen: Lage oder Abmessungen weichen zu stark ab.")


def mesh(shape: cq.Shape, tolerance: float = 0.8) -> dict[str, Any]:
    vertices, triangles = shape.tessellate(tolerance)
    return {
        "vertices": [[vertex.x, vertex.y, vertex.z] for vertex in vertices],
        "triangles": [list(triangle) for triangle in triangles],
    }

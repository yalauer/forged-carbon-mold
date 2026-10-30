from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Any

import cadquery as cq


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
    info: ModelInfo


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


def model_info(shape: cq.Shape) -> ModelInfo:
    box = shape.BoundingBox()
    return ModelInfo(
        solid_count=len(shape.Solids()),
        bounds_mm={"x": box.xlen, "y": box.ylen, "z": box.zlen},
        bounds_min_mm={"x": box.xmin, "y": box.ymin, "z": box.zmin},
        bounds_max_mm={"x": box.xmax, "y": box.ymax, "z": box.zmax},
        volume_mm3=shape.Volume(),
        valid=shape.isValid(),
    )


def build_two_piece_mold(
    part: cq.Shape,
    *,
    wall_mm: float = 20.0,
    split_z: float | None = None,
) -> MoldResult:
    if not math.isfinite(wall_mm) or wall_mm <= 0:
        raise CadError("Die Wandstärke muss größer als 0 mm sein.")
    box = part.BoundingBox()
    if min(box.xlen, box.ylen, box.zlen) <= 0:
        raise CadError("Das Bauteil besitzt eine leere Begrenzungsbox.")
    split = (box.zmin + box.zmax) / 2 if split_z is None else split_z
    if not math.isfinite(split) or not box.zmin < split < box.zmax:
        raise CadError("Die Trennebene muss innerhalb des Bauteils liegen, damit Unterform und Stempel wirksam sind.")
    blank = cq.Workplane("XY").box(
        box.xlen + 2 * wall_mm,
        box.ylen + 2 * wall_mm,
        box.zlen + 2 * wall_mm,
        centered=(True, True, True),
    ).translate(
        (
            (box.xmin + box.xmax) / 2,
            (box.ymin + box.ymax) / 2,
            (box.zmin + box.zmax) / 2,
        )
    ).val()
    try:
        cavity_blank = blank.cut(part)
        lower_limit = cq.Workplane("XY").box(
            box.xlen + 2 * wall_mm + 2,
            box.ylen + 2 * wall_mm + 2,
            split - (box.zmin - wall_mm),
        ).translate(
            (
                (box.xmin + box.xmax) / 2,
                (box.ymin + box.ymax) / 2,
                (split + box.zmin - wall_mm) / 2,
            )
        ).val()
        upper_limit = cq.Workplane("XY").box(
            box.xlen + 2 * wall_mm + 2,
            box.ylen + 2 * wall_mm + 2,
            box.zmax + wall_mm - split,
        ).translate(
            (
                (box.xmin + box.xmax) / 2,
                (box.ymin + box.ymax) / 2,
                (split + box.zmax + wall_mm) / 2,
            )
        ).val()
        lower = cavity_blank.intersect(lower_limit)
        upper_tip = part.intersect(upper_limit)
        punch_embed = min(wall_mm, (box.zmax - split) * 0.5)
        head_bottom = box.zmax - punch_embed
        upper_head = cq.Workplane("XY").box(
            box.xlen + 2 * wall_mm,
            box.ylen + 2 * wall_mm,
            box.zmax + wall_mm - head_bottom,
        ).translate(
            (
                (box.xmin + box.xmax) / 2,
                (box.ymin + box.ymax) / 2,
                (box.zmax + wall_mm + head_bottom) / 2,
            )
        ).val()
        upper = upper_tip.fuse(upper_head).clean()
    except Exception as exc:
        raise CadError(f"Formherstellung oder Stempelkonstruktion fehlgeschlagen: {exc}") from exc
    if any(not shape.isValid() or shape.Volume() <= 0 for shape in (lower, upper)):
        raise CadError("Die Teilung erzeugt ein leeres oder ungültiges Formsegment.")
    return MoldResult(
        upper=upper,
        lower=lower,
        blank=blank,
        part=part,
        split_z=split,
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
        raise CadError("STEP-Rundlaufprüfung fehlgeschlagen: Volumen weicht zu stark ab.")
    original_bounds, reopened_bounds = shape.BoundingBox(), roundtrip.BoundingBox()
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

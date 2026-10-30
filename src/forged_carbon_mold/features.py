"""Optional bores and opening-wedge pockets in the outer tooling rim."""
from __future__ import annotations

from dataclasses import replace
import math
from typing import Literal

import cadquery as cq
from pydantic import BaseModel, Field

from forged_carbon_mold.cad import CadError, MoldResult, _exact_bounds


class ConnectionParameters(BaseModel):
    model_config = {"extra": "forbid", "allow_inf_nan": False}
    mode: Literal["none", "bolts", "pins", "both"] = "none"
    bolt_diameter_mm: float = Field(default=6, gt=0, le=100)
    pin_diameter_mm: float = Field(default=4, gt=0, le=100)
    pin_depth_mm: float = Field(default=8, gt=0, le=100)
    edge_offset_mm: float = Field(default=8, gt=0, le=250)


class WedgeParameters(BaseModel):
    model_config = {"extra": "forbid", "allow_inf_nan": False}
    enabled: bool = False
    count: Literal[2, 4] = 2
    width_mm: float = Field(default=12, gt=0, le=200)
    depth_mm: float = Field(default=5, gt=0, le=100)
    height_mm: float = Field(default=4, gt=0, le=100)


def apply_mold_features(
    base: MoldResult, connections: ConnectionParameters, wedges: WedgeParameters,
) -> MoldResult:
    bounds, part = _exact_bounds(base.blank), _exact_bounds(base.part)
    cx, cy = (bounds.xmin + bounds.xmax) / 2, (bounds.ymin + bounds.ymax) / 2
    wall = (bounds.xlen - part.xlen) / 2
    web = 1.0
    holes: list[dict] = []
    cuts: list[cq.Shape] = []
    offset = connections.edge_offset_mm

    def add_hole(x: float, y: float, diameter: float, kind: str) -> None:
        radius = diameter / 2
        if not radius + web <= offset <= wall - radius - web:
            raise CadError("Bohrungen benötigen mindestens 1 mm Rand und Abstand zur Bauteilkontur. Wandstärke, Randabstand oder Durchmesser anpassen.")
        for other in holes:
            if math.hypot(x - other["x"], y - other["y"]) < radius + other["radius"] + web:
                raise CadError("Schraub- und Passstiftbohrungen liegen zu nah beieinander.")
        if kind == "bolt":
            bottom, height = bounds.zmin - 1, bounds.zlen + 2
        else:
            if connections.pin_depth_mm + web > wall:
                raise CadError("Die Passstifttiefe muss mindestens 1 mm Restboden in der Trägerplatte lassen.")
            bottom, height = base.split_z - connections.pin_depth_mm, 2 * connections.pin_depth_mm
        cuts.append(cq.Solid.makeCylinder(radius, height, cq.Vector(x, y, bottom)))
        holes.append({"x": x, "y": y, "radius": radius, "kind": kind})

    if connections.mode in {"bolts", "both"}:
        for x in (bounds.xmin + offset, bounds.xmax - offset):
            for y in (bounds.ymin + offset, bounds.ymax - offset):
                add_hole(x, y, connections.bolt_diameter_mm, "bolt")
    if connections.mode in {"pins", "both"}:
        for x, y in ((cx - bounds.xlen / 6, bounds.ymin + offset), (cx + bounds.xlen / 6, bounds.ymax - offset)):
            add_hole(x, y, connections.pin_diameter_mm, "pin")

    if wedges.enabled:
        if wedges.depth_mm + web > wall:
            raise CadError("Die Keiltiefe muss mindestens 1 mm Wand zur Bauteilkontur lassen.")
        if wedges.height_mm / 2 + web > wall:
            raise CadError("Die Keilöffnung ist für die Trägerplatte zu hoch.")
        sides = [("x", -1), ("x", 1)]
        if wedges.count == 4:
            sides += [("y", -1), ("y", 1)]
        for axis, sign in sides:
            tangent_length = bounds.ylen if axis == "x" else bounds.xlen
            reserve = 2 * (wedges.depth_mm + web) if wedges.count == 4 else 2 * web
            if wedges.width_mm + reserve >= tangent_length:
                raise CadError("Die Keilaufnahme ist zu breit für diese Werkzeugseite.")
            edge = (bounds.xmin if sign < 0 else bounds.xmax) if axis == "x" else (bounds.ymin if sign < 0 else bounds.ymax)
            inner, outer = edge - sign * wedges.depth_mm, edge + sign * 0.5
            low, high = sorted((inner, edge))
            if axis == "x":
                xmin, xmax, ymin, ymax = low, high, cy - wedges.width_mm / 2, cy + wedges.width_mm / 2
            else:
                xmin, xmax, ymin, ymax = cx - wedges.width_mm / 2, cx + wedges.width_mm / 2, low, high
            for hole in holes:
                dx = max(xmin - hole["x"], 0, hole["x"] - xmax)
                dy = max(ymin - hole["y"], 0, hole["y"] - ymax)
                if math.hypot(dx, dy) < hole["radius"] + web:
                    raise CadError("Eine Keilaufnahme liegt zu nah an einer Bohrung. Keilbreite oder Randabstand anpassen.")
            z = base.split_z
            half = wedges.height_mm / 2
            section = [(outer, z - half), (edge, z - half), (inner, z), (edge, z + half), (outer, z + half)]
            points = [
                cq.Vector(a, cy - wedges.width_mm / 2, b) if axis == "x"
                else cq.Vector(cx - wedges.width_mm / 2, a, b)
                for a, b in section
            ]
            wire = cq.Wire.makePolygon(points, close=True)
            direction = (0, wedges.width_mm, 0) if axis == "x" else (wedges.width_mm, 0, 0)
            cuts.append(cq.Solid.extrudeLinear(wire, [], direction))

    try:
        lower = base.lower.cut(*cuts) if cuts else base.lower
        upper = base.upper.cut(*cuts) if cuts else base.upper
    except Exception as exc:
        raise CadError(f"Bohrungen oder Keilaufnahmen konnten nicht erzeugt werden: {exc}") from exc
    if any(not shape.isValid() or len(shape.Solids()) != 1 or shape.Volume() <= 0 for shape in (lower, upper)):
        raise CadError("Bohrungen oder Keilaufnahmen würden ein ungültiges oder geteiltes Werkzeug erzeugen.")
    return replace(base, lower=lower, upper=upper, features={
        "bolt_holes": sum(hole["kind"] == "bolt" for hole in holes),
        "pin_pairs": sum(hole["kind"] == "pin" for hole in holes),
        "wedge_slots": wedges.count if wedges.enabled else 0,
        "holes": holes,
    })

import math
from pathlib import Path

import cadquery as cq
import pytest

from forged_carbon_mold.cad import CadError, build_two_piece_mold, export_step
from forged_carbon_mold.features import ConnectionParameters, WedgeParameters, apply_mold_features


@pytest.fixture(scope="module")
def base():
    return build_two_piece_mold(cq.Workplane("XY").box(80, 48, 24).val())


def test_through_bolts_and_blind_pin_bores_match_expected_geometry(base, tmp_path: Path):
    featured = apply_mold_features(base, ConnectionParameters(mode="both"), WedgeParameters())
    bolt_area = 4 * math.pi * 3 ** 2
    pin_volume = 2 * math.pi * 2 ** 2 * 8
    assert base.lower.Volume() - featured.lower.Volume() == pytest.approx(bolt_area * 54 + pin_volume)
    assert base.upper.Volume() - featured.upper.Volume() == pytest.approx(bolt_area * 20 + pin_volume)
    for hole in featured.features["holes"]:
        x, y = hole["x"], hole["y"]
        assert not featured.lower.isInside(cq.Vector(x, y, base.split_z - 1))
        assert not featured.upper.isInside(cq.Vector(x, y, base.split_z + 1))
        if hole["kind"] == "pin":
            assert featured.lower.isInside(cq.Vector(x, y, base.split_z - 9))
            assert featured.upper.isInside(cq.Vector(x, y, base.split_z + 9))
    assert featured.lower.intersect(base.part).Volume() == pytest.approx(0, abs=1e-6)
    export_step(featured.lower, tmp_path / "lower.step")
    export_step(featured.upper, tmp_path / "upper.step")


@pytest.mark.parametrize("count", [2, 4])
def test_opening_wedge_recesses_are_real_matching_cuts(base, count: int):
    params = WedgeParameters(enabled=True, count=count)
    featured = apply_mold_features(base, ConnectionParameters(), params)
    per_half = count * params.width_mm * params.depth_mm * params.height_mm / 4
    assert base.lower.Volume() - featured.lower.Volume() == pytest.approx(per_half)
    assert base.upper.Volume() - featured.upper.Volume() == pytest.approx(per_half)
    assert featured.features["wedge_slots"] == count
    assert not featured.lower.isInside(cq.Vector(59, 0, base.split_z - 0.5))
    assert not featured.upper.isInside(cq.Vector(59, 0, base.split_z + 0.5))
    assert featured.lower.isInside(cq.Vector(54, 0, base.split_z - 0.5))


@pytest.mark.parametrize("connections,wedges", [
    (ConnectionParameters(mode="bolts", edge_offset_mm=3), WedgeParameters()),
    (ConnectionParameters(mode="bolts", edge_offset_mm=19), WedgeParameters()),
    (ConnectionParameters(mode="pins", pin_depth_mm=20), WedgeParameters()),
    (ConnectionParameters(), WedgeParameters(enabled=True, depth_mm=20)),
    (ConnectionParameters(), WedgeParameters(enabled=True, height_mm=40)),
    (ConnectionParameters(), WedgeParameters(enabled=True, width_mm=100)),
])
def test_features_reject_insufficient_wall_or_floor(base, connections, wedges):
    with pytest.raises(CadError):
        apply_mold_features(base, connections, wedges)


def test_pin_wedge_collision_is_rejected():
    base = build_two_piece_mold(cq.Workplane("XY").box(12, 8, 4).val())
    with pytest.raises(CadError, match="Bohrung"):
        apply_mold_features(base, ConnectionParameters(mode="pins"), WedgeParameters(enabled=True, count=4, width_mm=20, depth_mm=8))


def test_disabled_features_preserve_base_without_mutation(base):
    lower_volume, upper_volume = base.lower.Volume(), base.upper.Volume()
    apply_mold_features(base, ConnectionParameters(mode="both"), WedgeParameters(enabled=True))
    plain = apply_mold_features(base, ConnectionParameters(), WedgeParameters())
    assert plain.lower.Volume() == pytest.approx(lower_volume)
    assert plain.upper.Volume() == pytest.approx(upper_volume)
    assert base.features == {}

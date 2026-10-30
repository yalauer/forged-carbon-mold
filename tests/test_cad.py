from pathlib import Path

import cadquery as cq
import pytest

from forged_carbon_mold.cad import CadError, build_two_piece_mold, export_step, import_step, model_info


def make_box(path: Path) -> cq.Shape:
    shape = cq.Workplane("XY").box(80, 48, 24).val()
    cq.exporters.export(shape, str(path), exportType="STEP")
    return shape


def test_step_import_mold_split_and_step_roundtrip(tmp_path: Path) -> None:
    source_path = tmp_path / "part.step"
    source = make_box(source_path)
    part = import_step(source_path)
    result = build_two_piece_mold(part, wall_mm=20, punch_depth_mm=10)

    assert model_info(part).solid_count == 1
    assert part.isValid()
    assert result.lower.isValid() and result.upper.isValid()
    assert result.lower.Volume() > 0 and result.upper.Volume() > 0
    assert result.lower.BoundingBox().zmax == pytest.approx(result.split_z)
    assert result.upper.BoundingBox().zmax > part.BoundingBox().zmax
    assert len(result.lower.Solids()) == 1
    assert len(result.upper.Solids()) == 1
    assert result.upper.Volume() > source.Volume() / 2

    output = tmp_path / "segment.step"
    export_step(result.lower, output)
    reopened = import_step(output)
    assert len(reopened.Solids()) == 1
    assert reopened.Volume() == pytest.approx(result.lower.Volume(), rel=1e-8)


@pytest.mark.parametrize("depth", [0, -1, 501, float("nan"), float("inf")])
def test_rejects_invalid_punch_depth(tmp_path: Path, depth: float) -> None:
    make_box(tmp_path / "part.step")
    part = import_step(tmp_path / "part.step")
    with pytest.raises(CadError, match="Stempeltiefe"):
        build_two_piece_mold(part, punch_depth_mm=depth)


def test_rejects_empty_or_non_step_input(tmp_path: Path) -> None:
    wrong_file = tmp_path / "part.stl"
    wrong_file.write_text("not a STEP file")
    with pytest.raises(CadError, match="STEP"):
        import_step(wrong_file)


@pytest.mark.parametrize("depth", [2.0, 10.0, 30.0])
def test_full_part_in_lower_and_punch_follows_z_contour(tmp_path: Path, depth: float) -> None:
    part = cq.Workplane("XY").cylinder(20, 10).val().translate((13, -7, 30))
    result = build_two_piece_mold(part, wall_mm=5, punch_depth_mm=depth)
    assert len(result.upper.Solids()) == 1
    assert result.split_z == pytest.approx(part.BoundingBox().zmax + depth)
    assert result.upper.BoundingBox().zmin == pytest.approx(part.BoundingBox().zmax)
    assert result.lower.BoundingBox().zmax == pytest.approx(result.split_z)
    assert result.upper.distance(result.lower) == pytest.approx(0, abs=1e-7)
    assert result.upper.intersect(result.lower).Volume() == pytest.approx(0, abs=1e-7)
    assert result.lower.intersect(part).Volume() == pytest.approx(0, abs=1e-7)
    assert result.upper.intersect(part).Volume() == pytest.approx(0, abs=1e-7)
    probe_z = result.split_z - 0.1
    assert not result.upper.isInside(cq.Vector(27, -7, probe_z))  # no rectangular rim below head
    assert result.upper.isInside(cq.Vector(13, -7, probe_z))  # cylinder contour only
    assert not result.upper.isInside(cq.Vector(22, 2, probe_z))  # outside round silhouette
    assert result.lower.isInside(cq.Vector(22, 2, probe_z))
    assert result.blank.Volume() - result.lower.Volume() - result.upper.Volume() == pytest.approx(part.Volume())
    export_step(result.upper, tmp_path / "upper.step")


@pytest.mark.parametrize("kind", ["sphere", "inclined", "ring", "concave"])
def test_complete_cavity_and_complementary_punch_surfaces(tmp_path: Path, kind: str) -> None:
    if kind == "sphere":
        part = cq.Workplane("XY").sphere(10).val()
    elif kind == "inclined":
        part = cq.Workplane("XY").box(20, 12, 6).val().rotate((0, 0, 0), (0, 1, 0), 20)
    elif kind == "ring":
        part = cq.Workplane("XY").circle(10).circle(5).extrude(8).val()
    else:
        part = cq.Workplane("XY").polyline([(0, 0), (20, 0), (20, 5), (5, 5), (5, 20), (0, 20)]).close().extrude(6).val()
    part = part.translate((23, -11, 40))
    result = build_two_piece_mold(part, wall_mm=5, punch_depth_mm=12)
    cavity = result.blank.cut(result.lower, result.upper)
    assert cavity.isValid()
    assert cavity.cut(part).Volume() == pytest.approx(0, abs=1e-5)
    assert part.cut(cavity).Volume() == pytest.approx(0, abs=1e-5)
    assert result.upper.intersect(part).Volume() == pytest.approx(0, abs=1e-5)
    if kind == "sphere":
        assert not result.upper.isInside(cq.Vector(23, -11, 49))
        assert result.upper.isInside(cq.Vector(31, -11, 47))
        assert result.lower.isInside(cq.Vector(31, -11, 33))
    export_step(result.upper, tmp_path / "upper.step")
    export_step(result.lower, tmp_path / "lower.step")

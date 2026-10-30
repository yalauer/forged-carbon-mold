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
    result = build_two_piece_mold(part, wall_mm=20, split_z=0)

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


def test_rejects_split_outside_part(tmp_path: Path) -> None:
    make_box(tmp_path / "part.step")
    part = import_step(tmp_path / "part.step")
    assert build_two_piece_mold(part, split_z=11).upper.Volume() > 0
    with pytest.raises(CadError, match="innerhalb"):
        build_two_piece_mold(part, split_z=13)


def test_rejects_empty_or_non_step_input(tmp_path: Path) -> None:
    wrong_file = tmp_path / "part.stl"
    wrong_file.write_text("not a STEP file")
    with pytest.raises(CadError, match="STEP"):
        import_step(wrong_file)

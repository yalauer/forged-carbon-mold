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
    assert result.lower.Volume() + result.upper.Volume() == pytest.approx(
        result.blank.Volume() - source.Volume(), rel=1e-6
    )
    assert result.lower.intersect(result.upper).Volume() == pytest.approx(0, abs=1e-6)
    assert result.lower.fuse(result.upper).Volume() == pytest.approx(
        result.blank.Volume() - source.Volume(), rel=1e-6
    )

    output = tmp_path / "segment.step"
    export_step(result.lower, output)
    reopened = import_step(output)
    assert len(reopened.Solids()) == 1
    assert reopened.Volume() == pytest.approx(result.lower.Volume(), rel=1e-8)


def test_rejects_split_outside_part(tmp_path: Path) -> None:
    make_box(tmp_path / "part.step")
    part = import_step(tmp_path / "part.step")
    assert build_two_piece_mold(part, split_z=13).upper.Volume() > 0
    with pytest.raises(CadError, match="innerhalb"):
        build_two_piece_mold(part, split_z=33)


def test_rejects_empty_or_non_step_input(tmp_path: Path) -> None:
    wrong_file = tmp_path / "part.stl"
    wrong_file.write_text("not a STEP file")
    with pytest.raises(CadError, match="STEP"):
        import_step(wrong_file)

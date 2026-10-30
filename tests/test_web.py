import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient
import cadquery as cq

from forged_carbon_mold.web import app


def test_local_mold_project_roundtrip_and_exports() -> None:
    client = TestClient(app)
    home = client.get("/")
    assert home.status_code == 200
    assert "Forged Carbon" in home.text

    demo = client.post("/api/demo")
    assert demo.status_code == 200
    assert demo.json()["model"]["valid"] is True

    part_mesh = client.get("/api/mesh/part")
    assert part_mesh.status_code == 200
    assert part_mesh.json()["triangles"]

    built = client.post("/api/build", json={"wall_mm": 20, "punch_depth_mm": 10})
    assert built.status_code == 200
    assert {segment["id"] for segment in built.json()["segments"]} == {"lower", "upper"}
    assert client.get("/api/status").json()["segments"] == built.json()["segments"]
    assert client.get("/api/mesh/lower").json()["triangles"]
    assert client.get("/api/mesh/upper").json()["triangles"]

    for segment in ("lower", "upper", "assembly"):
        exported = client.get(f"/api/export/{segment}")
        assert exported.status_code == 200
        assert exported.content.startswith(b"ISO-10303-21")

    saved = client.get("/api/project")
    assert saved.status_code == 200
    reopened = client.post(
        "/api/project",
        files={"file": ("sample.fcm", saved.content, "application/vnd.forged-carbon-mold")},
    )
    assert reopened.status_code == 200
    assert reopened.json()["model"]["valid"] is True
    assert client.get("/api/export/lower").status_code == 409


@pytest.mark.parametrize("change", [
    {"punch_depth_mm": float("nan")}, {"punch_depth_mm": float("inf")},
    {"punch_depth_mm": -1}, {"wall_mm": float("inf")},
    {"transform": {"x": float("nan"), "y": 0, "z": 0, "rx": 0, "ry": 0, "rz": 0}},
    [], None,
])
def test_bad_project_is_rejected_without_changing_current_model(change) -> None:
    client = TestClient(app)
    client.post("/api/demo")
    before = client.get("/api/status").json()
    with zipfile.ZipFile(io.BytesIO(client.get("/api/project").content)) as archive:
        project = json.loads(archive.read("project.json"))
    invalid = dict(project, **change) if isinstance(change, dict) else change
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("project.json", json.dumps(invalid))
    response = client.post("/api/project", files={"file": ("bad.fcm", buffer.getvalue())})
    assert response.status_code == 422
    assert client.get("/api/status").json() == before


def test_translation_preserves_depth_and_demo_clears_mold() -> None:
    client = TestClient(app)
    client.post("/api/demo")
    client.post("/api/build", json={"wall_mm": 20, "punch_depth_mm": 25})
    moved = client.post("/api/transform", json={"x": 0, "y": 0, "z": 100, "rx": 0, "ry": 0, "rz": 0})
    assert moved.status_code == 200
    current = client.get("/api/status").json()
    assert current["parameters"]["punch_depth_mm"] == 25
    assert current["split_z"] == 137
    assert current["segments"] == []
    assert client.post("/api/build", json=current["parameters"]).status_code == 200
    client.post("/api/demo")
    assert client.get("/api/status").json()["segments"] == []
    assert client.get("/api/export/lower").status_code == 409


def test_depth_changes_geometry_and_is_saved_in_project() -> None:
    client = TestClient(app)
    client.post("/api/demo")
    shallow = client.post("/api/build", json={"wall_mm": 20, "punch_depth_mm": 5}).json()
    deep = client.post("/api/build", json={"wall_mm": 20, "punch_depth_mm": 25}).json()
    assert deep["split_z"] - shallow["split_z"] == 20
    for first, second in zip(shallow["segments"], deep["segments"]):
        assert second["volume_mm3"] > first["volume_mm3"]
    saved = client.get("/api/project").content
    with zipfile.ZipFile(io.BytesIO(saved)) as archive:
        project = json.loads(archive.read("project.json"))
    assert project["version"] == 3
    assert project["punch_depth_mm"] == 25
    reopened = client.post("/api/project", files={"file": ("depth.fcm", saved)})
    assert reopened.status_code == 200
    assert reopened.json()["parameters"]["punch_depth_mm"] == 25


def test_legacy_project_migrates_and_old_build_parameter_is_rejected() -> None:
    client = TestClient(app)
    client.post("/api/demo")
    with zipfile.ZipFile(io.BytesIO(client.get("/api/project").content)) as archive:
        project = json.loads(archive.read("project.json"))
    project.update(version=1, split_z=0)
    project.pop("punch_depth_mm")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("project.json", json.dumps(project))
    opened = client.post("/api/project", files={"file": ("legacy.fcm", buffer.getvalue())})
    assert opened.status_code == 200
    assert opened.json()["migrated"] is True
    assert opened.json()["parameters"]["punch_depth_mm"] == 10
    assert client.post("/api/build", json={"wall_mm": 20, "split_z": 0}).status_code == 422


def test_transform_is_absolute_and_invalidates_old_mold() -> None:
    client = TestClient(app)
    client.post("/api/demo")
    moved = client.post(
        "/api/transform",
        json={"x": 10, "y": 2, "z": 1, "rx": 0, "ry": 0, "rz": 0},
    )
    assert moved.status_code == 200
    first_min = moved.json()["model"]["bounds_min_mm"]["x"]
    assert first_min == -30
    repeated = client.post(
        "/api/transform",
        json={"x": 10, "y": 2, "z": 1, "rx": 0, "ry": 0, "rz": 0},
    )
    assert repeated.status_code == 200
    assert repeated.json()["model"]["bounds_min_mm"]["x"] == first_min
    assert client.get("/api/export/lower").status_code == 409


def test_step_upload_rejects_wrong_extension_and_accepts_real_step(tmp_path) -> None:
    client = TestClient(app)
    wrong = client.post("/api/import", files={"file": ("model.stl", b"not step")})
    assert wrong.status_code == 400

    path = tmp_path / "model.step"
    cq.exporters.export(cq.Workplane("XY").box(12, 8, 4).val(), str(path), exportType="STEP")
    uploaded = client.post(
        "/api/import",
        files={"file": ("model.step", path.read_bytes(), "application/step")},
    )
    assert uploaded.status_code == 200
    assert uploaded.json()["model"]["valid"] is True
    assert uploaded.json()["model"]["solid_count"] == 1


def test_feature_options_project_roundtrip_and_base_cache(monkeypatch) -> None:
    import forged_carbon_mold.web as web
    client = TestClient(app)
    client.post("/api/demo")
    params = {"wall_mm": 20, "punch_depth_mm": 10}
    assert client.post("/api/build", json=params).status_code == 200

    def should_not_rebuild(*args, **kwargs):
        raise AssertionError("Feature changes must reuse the base mold")

    monkeypatch.setattr(web, "build_two_piece_mold", should_not_rebuild)
    params.update(connections={"mode": "both"}, wedges={"enabled": True})
    built = client.post("/api/build", json=params)
    assert built.status_code == 200
    assert built.json()["features"]["bolt_holes"] == 4
    assert built.json()["features"]["pin_pairs"] == 2
    assert built.json()["features"]["wedge_slots"] == 2
    for kind in ["lower", "upper", "assembly"]:
        assert client.get(f"/api/export/{kind}").status_code == 200
    current = client.get("/api/status").json()
    assert client.put("/api/parameters", json=current["parameters"]).json()["mold_ready"] is True
    changed = dict(current["parameters"], wedges={"enabled": False})
    assert client.put("/api/parameters", json=changed).json()["mold_ready"] is False
    assert client.get("/api/export/upper").status_code == 409
    saved = client.get("/api/project").content
    reopened = client.post("/api/project", files={"file": ("features.fcm", saved)})
    assert reopened.status_code == 200
    assert reopened.json()["parameters"]["connections"]["mode"] == "both"
    assert reopened.json()["parameters"]["wedges"]["enabled"] is False


def test_invalid_feature_request_does_not_replace_previous_mold():
    client = TestClient(app)
    client.post("/api/demo")
    params = {"wall_mm": 20, "punch_depth_mm": 10}
    client.post("/api/build", json=params)
    before = client.get("/api/status").json()
    invalid = dict(params, connections={"mode": "bolts", "bolt_diameter_mm": 40})
    assert client.post("/api/build", json=invalid).status_code == 422
    assert client.get("/api/status").json() == before

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

    built = client.post("/api/build", json={"wall_mm": 20, "split_z": 0})
    assert built.status_code == 200
    assert {segment["id"] for segment in built.json()["segments"]} == {"lower", "upper"}

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

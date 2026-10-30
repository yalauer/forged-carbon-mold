from __future__ import annotations

import base64
import io
import json
import threading
import tempfile
import zipfile
from pathlib import Path
from typing import Literal

import cadquery as cq
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from forged_carbon_mold.cad import (
    CadError,
    MoldResult,
    build_two_piece_mold,
    export_step,
    import_step,
    mesh,
    model_info,
)

STATIC_DIR = Path(__file__).parent / "static"
MAX_STEP_BYTES = 100 * 1024 * 1024
app = FastAPI(title="Forged Carbon Mold", version="0.1.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
_lock = threading.RLock()
_part: cq.Shape | None = None
_base_part: cq.Shape | None = None
_source_step: bytes | None = None
_source_name = "Bauteil.step"
_mold: MoldResult | None = None
_revision = 0
_wall_mm = 20.0
_split_z: float | None = None
_transform = {"x": 0.0, "y": 0.0, "z": 0.0, "rx": 0.0, "ry": 0.0, "rz": 0.0}


class Parameters(BaseModel):
    wall_mm: float = Field(gt=0, le=500)
    split_z: float


class Transform(BaseModel):
    x: float = Field(ge=-100_000, le=100_000)
    y: float = Field(ge=-100_000, le=100_000)
    z: float = Field(ge=-100_000, le=100_000)
    rx: float = Field(ge=-3600, le=3600)
    ry: float = Field(ge=-3600, le=3600)
    rz: float = Field(ge=-3600, le=3600)


def _require_part() -> cq.Shape:
    if _part is None:
        raise HTTPException(status_code=409, detail="Bitte zuerst eine STEP-Datei importieren.")
    return _part


def _bytes_to_shape(data: bytes, suffix: str) -> cq.Shape:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / f"source{suffix}"
        path.write_bytes(data)
        return import_step(path)


def _apply_transform(shape: cq.Shape, values: dict[str, float]) -> cq.Shape:
    transformed = shape
    for axis, key in (((1, 0, 0), "rx"), ((0, 1, 0), "ry"), ((0, 0, 1), "rz")):
        if values[key]:
            transformed = transformed.rotate((0, 0, 0), axis, values[key])
    return transformed.translate((values["x"], values["y"], values["z"]))


@app.get("/", response_class=HTMLResponse)
async def home() -> str:
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/api/status")
async def status() -> dict:
    with _lock:
        info = model_info(_part) if _part is not None else None
        return {
            "revision": _revision,
            "part": info.__dict__ if info else None,
            "mold_ready": _mold is not None,
            "source_name": _source_name if _part is not None else None,
            "parameters": {"wall_mm": _wall_mm, "split_z": _split_z},
            "transform": dict(_transform),
        }


@app.post("/api/import")
async def upload_step(file: UploadFile = File(...)) -> dict:
    global _part, _base_part, _source_step, _source_name, _mold, _revision, _split_z
    name = Path(file.filename or "").name
    if Path(name).suffix.lower() not in {".step", ".stp"}:
        raise HTTPException(status_code=400, detail="Bitte eine .step- oder .stp-Datei auswählen.")
    data = await file.read(MAX_STEP_BYTES + 1)
    if len(data) > MAX_STEP_BYTES:
        raise HTTPException(status_code=413, detail="STEP-Dateien dürfen höchstens 100 MB groß sein.")
    try:
        shape = await run_in_threadpool(_bytes_to_shape, data, Path(name).suffix.lower())
    except CadError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    with _lock:
        _part = _base_part = shape
        _source_step = data
        _source_name = name
        _mold = None
        _revision += 1
        _split_z = (shape.BoundingBox().zmin + shape.BoundingBox().zmax) / 2
        for key in _transform:
            _transform[key] = 0.0
        return {"revision": _revision, "model": model_info(shape).__dict__}


@app.post("/api/demo")
async def create_demo() -> dict:
    global _part, _base_part, _source_step, _source_name, _mold, _revision, _split_z
    def make_fixture() -> tuple[cq.Shape, bytes]:
        demo = cq.Workplane("XY").box(80, 48, 24).val()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reference.step"
            cq.exporters.export(demo, str(path), exportType="STEP")
            data = path.read_bytes()
        return _bytes_to_shape(data, ".step"), data

    shape, data = await run_in_threadpool(make_fixture)
    with _lock:
        _part, _base_part, _source_step, _source_name, _mold = shape, shape, data, "Referenzkörper.step", None
        _revision += 1
        box = shape.BoundingBox()
        _split_z = (box.zmin + box.zmax) / 2
        for key in _transform:
            _transform[key] = 0.0
        return {"revision": _revision, "model": model_info(shape).__dict__}


@app.post("/api/transform")
async def set_transform(values: Transform) -> dict:
    global _part, _mold, _revision, _transform, _split_z
    _require_part()
    with _lock:
        original = _base_part
    if original is None:
        raise HTTPException(status_code=409, detail="Bitte zuerst eine STEP-Datei importieren.")
    updated = await run_in_threadpool(_apply_transform, original, values.model_dump())
    with _lock:
        if original is not _base_part:
            raise HTTPException(status_code=409, detail="Bauteil wurde während der Transformation geändert. Bitte erneut versuchen.")
        _part = updated
        _transform = values.model_dump()
        _mold = None
        _revision += 1
        bounds = updated.BoundingBox()
        if _split_z is None or not bounds.zmin < _split_z < bounds.zmax:
            _split_z = (bounds.zmin + bounds.zmax) / 2
        return {"revision": _revision, "model": model_info(updated).__dict__}


@app.post("/api/build")
async def build(parameters: Parameters) -> dict:
    global _mold, _wall_mm, _split_z
    part = _require_part()
    try:
        result = await run_in_threadpool(
            build_two_piece_mold,
            part,
            wall_mm=parameters.wall_mm,
            split_z=parameters.split_z,
        )
    except CadError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    with _lock:
        if part is not _part:
            raise HTTPException(status_code=409, detail="Bauteil wurde während der Berechnung geändert. Bitte erneut berechnen.")
        _mold, _wall_mm, _split_z = result, parameters.wall_mm, parameters.split_z
        return {
            "revision": _revision,
            "segments": [
                {"id": "lower", "name": "Unterform", "solid_count": len(result.lower.Solids()), "volume_mm3": result.lower.Volume()},
                {"id": "upper", "name": "Oberstempel", "solid_count": len(result.upper.Solids()), "volume_mm3": result.upper.Volume()},
            ],
            "split_z": result.split_z,
        }


@app.get("/api/mesh/{kind}")
async def get_mesh(kind: Literal["part", "blank", "lower", "upper"]) -> dict:
    with _lock:
        part = _require_part()
        current_mold = _mold
        if kind in {"lower", "upper", "blank"} and current_mold is None:
            raise HTTPException(status_code=409, detail="Form noch nicht berechnet; es wird kein veraltetes Ergebnis angezeigt.")
        shape = part if kind == "part" else getattr(current_mold, kind)
    return await run_in_threadpool(mesh, shape)


@app.get("/api/export/{kind}")
async def download_step(kind: Literal["lower", "upper", "assembly"]) -> Response:
    with _lock:
        current = _mold
        if current is None:
            raise HTTPException(status_code=409, detail="Form noch nicht berechnet; es wird kein veralteter Export angeboten.")
        shape = (
            current.lower if kind == "lower" else
            current.upper if kind == "upper" else
            cq.Compound.makeCompound([current.lower, current.upper])
        )

    def export_bytes() -> bytes:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / f"{kind}.step"
            export_step(shape, path)
            return path.read_bytes()

    try:
        data = await run_in_threadpool(export_bytes)
    except CadError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return Response(
        content=data,
        media_type="application/step",
        headers={"Content-Disposition": f'attachment; filename="forged-carbon-{kind}.step"'},
    )


@app.get("/api/project")
async def save_project() -> Response:
    with _lock:
        _require_part()
        if _source_step is None:
            raise HTTPException(status_code=409, detail="Die ursprüngliche STEP-Datei ist nicht verfügbar.")
        project = {
            "format": "forged-carbon-mold",
            "version": 1,
            "source_name": _source_name,
            "wall_mm": _wall_mm,
            "split_z": _split_z,
            "transform": _transform,
            "source_step_base64": base64.b64encode(_source_step).decode("ascii"),
        }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("project.json", json.dumps(project, ensure_ascii=False))
    return Response(
        content=buffer.getvalue(),
        media_type="application/vnd.forged-carbon-mold",
        headers={"Content-Disposition": 'attachment; filename="forged-carbon-project.fcm"'},
    )


@app.post("/api/project")
async def open_project(file: UploadFile = File(...)) -> dict:
    global _part, _base_part, _source_step, _source_name, _mold, _revision, _wall_mm, _split_z, _transform
    data = await file.read(MAX_STEP_BYTES + 1024 * 1024)
    if len(data) > MAX_STEP_BYTES + 1024 * 1024:
        raise HTTPException(status_code=413, detail="Projektdatei ist zu groß.")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if archive.namelist() != ["project.json"]:
                raise ValueError("Unerwartete Projektdatei-Inhalte.")
            project = json.loads(archive.read("project.json"))
        if project.get("format") != "forged-carbon-mold" or project.get("version") != 1:
            raise ValueError("Unbekanntes Projektformat oder nicht unterstützte Version.")
        source = base64.b64decode(project["source_step_base64"], validate=True)
        name = Path(project["source_name"]).name
        if Path(name).suffix.lower() not in {".step", ".stp"} or len(source) > MAX_STEP_BYTES:
            raise ValueError("Ungültige Quelldatei im Projekt.")
        base_shape = await run_in_threadpool(_bytes_to_shape, source, Path(name).suffix.lower())
        transform = Transform(**project["transform"]).model_dump()
        shape = await run_in_threadpool(_apply_transform, base_shape, transform)
        wall = float(project["wall_mm"])
        split = float(project["split_z"])
        if not 0 < wall <= 500:
            raise ValueError("Ungültige Wandstärke im Projekt.")
    except (OSError, KeyError, TypeError, ValueError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail=f"Projektdatei kann nicht geöffnet werden: {exc}") from exc
    with _lock:
        _part, _base_part, _source_step, _source_name, _mold = shape, base_shape, source, name, None
        _wall_mm, _split_z, _transform = wall, split, transform
        _revision += 1
        return {"revision": _revision, "model": model_info(shape).__dict__, "parameters": {"wall_mm": wall, "split_z": split}}

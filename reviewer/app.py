"""Loopback-only API and locally served reviewer frontend."""

from contextlib import asynccontextmanager
import os
from pathlib import Path
import re
import zipfile

from fastapi import FastAPI, Request, UploadFile, File, Body
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .service import Service, uid
from . import comparison as cmp
from .assets import VanillaAssets, resource


def create_app(directory=None, worker=True, testing=False):
    @asynccontextmanager
    async def lifespan(app):
        app.state.service = Service(directory, start_worker=worker)
        app.state.vanilla = VanillaAssets(app.state.service.directory / "vanilla")
        yield
        app.state.service.close()

    app = FastAPI(title="Minecraft Dataset Reviewer", lifespan=lifespan, docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def local_requests(request, call_next):
        allowed_hosts = {"127.0.0.1", "localhost", "::1"}
        if testing:
            allowed_hosts.add("testserver")
        if request.url.hostname not in allowed_hosts:
            return JSONResponse({"detail": "This reviewer accepts localhost requests only"}, status_code=403)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin and origin not in {"http://127.0.0.1:8765", "http://localhost:8765"}:
                return JSONResponse({"detail": "Write request origin is not this local reviewer"}, status_code=403)
        return await call_next(request)

    @app.exception_handler(ValueError)
    async def bad_request(request, error):
        return JSONResponse({"detail": str(error)}, status_code=400)

    @app.exception_handler(KeyError)
    async def missing(request, error):
        return JSONResponse({"detail": str(error.args[0])}, status_code=404)

    def service():
        return app.state.service

    async def uploaded(file):
        content = bytearray()
        while part := await file.read(1024 * 1024):
            content.extend(part)
            if len(content) > cmp.MAX_FILE_BYTES:
                raise ValueError("File exceeds the 64 MiB import limit")
        return bytes(content)

    @app.get("/api/health")
    def health():
        return {"status": "ok", "renderer": "diagnostic", "schema_version": 1}

    @app.get("/api/vanilla")
    def vanilla_status():
        return app.state.vanilla.status()

    @app.post("/api/vanilla/palette")
    def vanilla_palette(states: list[dict] = Body(...)):
        if len(states) > 4096:
            raise ValueError("Palette exceeds 4096 states")
        if any(not isinstance(s.get("name"), str) or not isinstance(s.get("properties", {}), dict) for s in states):
            raise ValueError("Palette entries require names and property objects")
        return [app.state.vanilla.appearance(state) for state in states]

    @app.get("/api/vanilla/{asset_hash}/textures/{name:path}")
    def vanilla_texture(asset_hash: str, name: str):
        assets = app.state.vanilla
        if not assets.manifest or asset_hash != assets.manifest["hash"] or not name.endswith(".png"):
            raise KeyError("Vanilla texture not found")
        resource(name[:-4])
        if not name.startswith(("block/", "entity/", "item/", "misc/", "environment/")):
            raise KeyError("Texture category is not available to the block viewer")
        path = assets.path("textures", name[:-4], ".png")
        if not path.is_file():
            raise KeyError("Vanilla texture not found")
        return FileResponse(path, media_type="image/png", headers={"Cache-Control": "private, max-age=31536000, immutable"})

    @app.get("/api/vanilla/coverage")
    def vanilla_coverage():
        from .coverage import audit
        return audit(app.state.vanilla)

    @app.post("/api/imports")
    async def imports(files: list[UploadFile] = File(...)):
        results = []
        for file in files:
            try:
                results.append(service().import_bytes(file.filename or "unnamed", await uploaded(file)))
            except ValueError as error:
                results.append({"filename": file.filename, "error": str(error)})
        return results

    @app.post("/api/imports/existing")
    def existing():
        results = []
        for path in sorted((cmp.ROOT / "data/raw_litematics").glob("*.litematic")):
            if path.stat().st_size > cmp.MAX_FILE_BYTES:
                results.append({"filename": path.name, "error": "File exceeds import limit"})
                continue
            results.append(service().import_bytes(path.name, path.read_bytes(), "Existing project source"))
        return results

    @app.post("/api/imports/demo")
    def demo():
        import completion_data
        fixture = service().directory / "project-generated-demo.litematic"
        if not fixture.exists():
            completion_data.synthetic_house(fixture)
        imported = service().import_bytes(fixture.name, fixture.read_bytes(), "Local project generator")
        service().update_build(imported["build_id"], {"provenance": "project_generated",
            "evidence": "Generated by tools/completion_data.py synthetic_house", "attribution": "Minecraft building AI project"})
        return imported

    @app.get("/api/jobs")
    def jobs():
        return service().jobs()

    @app.post("/api/jobs/{job_id}/retry")
    def retry(job_id: str):
        return service().retry(job_id)

    @app.get("/api/builds")
    def builds(query: str = "", review: str = "", building_type: str = "", redstone: str = "",
               provenance: str = "", style: str = "", function: str = "", archived: str = "active",
               min_dimension: int = 0, max_dimension: int = 0):
        return service().builds(query, review, building_type, redstone, provenance, style, function, archived, min_dimension, max_dimension)

    @app.get("/api/labels")
    def labels():
        return service().label_options()

    @app.post("/api/labels")
    def add_label(payload: dict = Body(...)):
        return service().add_label(payload.get("category"), payload.get("label"))

    @app.get("/api/builds/{build_id}")
    def build(build_id: str):
        return service().get_build(build_id)

    @app.patch("/api/builds/{build_id}")
    def update_build(build_id: str, changes: dict = Body(...)):
        return service().update_build(build_id, changes)

    @app.post("/api/builds/{build_id}/review")
    def review(build_id: str, payload: dict = Body(...)):
        notes = payload.get("notes", "")
        if not isinstance(notes, str) or len(notes) > 10000:
            raise ValueError("Review notes must be text of at most 10000 characters")
        return service().review(build_id, payload.get("decision"), notes)

    @app.post("/api/builds/{build_id}/reprocess")
    def reprocess(build_id: str):
        return service().reprocess(build_id)

    @app.get("/api/builds/{build_id}/comparison")
    def comparison(build_id: str):
        return service().scenes(build_id)

    @app.post("/api/builds/{build_id}/conversion")
    async def replacement(build_id: str, file: UploadFile = File(...)):
        return service().replacement(build_id, file.filename or "unnamed", await uploaded(file))

    @app.get("/api/builds/{build_id}/files/{side}")
    def file(build_id: str, side: str):
        record = service().get_build(build_id)
        if side == "original":
            path = service().directory / "sources" / f"{record['source_hash']}.litematic"
        elif side == "converted" and record["conversion"]:
            path = service().directory / "conversions" / f"{record['conversion']['id']}.litematic"
        else:
            raise KeyError("Managed file not found")
        return FileResponse(path, filename=f"{side}-{record['filename']}", media_type="application/octet-stream")

    @app.get("/api/datasets")
    def datasets():
        return service().datasets()

    @app.post("/api/datasets")
    def create_dataset(payload: dict = Body(...)):
        return service().update_dataset(payload)

    @app.patch("/api/datasets/{dataset_id}")
    def update_dataset(dataset_id: str, payload: dict = Body(...)):
        return service().update_dataset(payload, dataset_id)

    @app.post("/api/datasets/{dataset_id}/membership")
    def membership(dataset_id: str, payload: dict = Body(...)):
        ids, include = payload.get("build_ids"), payload.get("include")
        if not isinstance(ids, list) or len(ids) > 1000 or any(not isinstance(i, str) for i in ids) or type(include) is not bool:
            raise ValueError("Provide up to 1000 build IDs and a boolean include value")
        return service().membership(dataset_id, ids, include)

    @app.post("/api/datasets/{dataset_id}/splits")
    def split(dataset_id: str, payload: dict = Body(...)):
        if not isinstance(payload.get("family"), str) or not isinstance(payload.get("split"), str):
            raise ValueError("Provide family and split strings")
        return service().assign_split(dataset_id, payload["family"], payload["split"])

    @app.get("/api/datasets/{dataset_id}/preview")
    def preview(dataset_id: str):
        return service().preview(dataset_id)

    @app.post("/api/datasets/{dataset_id}/versions")
    def export(dataset_id: str):
        return service().export(dataset_id)

    @app.get("/api/datasets/{dataset_id}/versions/{version_id}")
    def version(dataset_id: str, version_id: str):
        return service().version(dataset_id, version_id)

    @app.get("/api/datasets/{dataset_id}/versions/{version_id}/download")
    def download(dataset_id: str, version_id: str):
        service().version(dataset_id, version_id)
        if not re.fullmatch(r"[a-f0-9]{32}", version_id):
            raise KeyError("Version not found")
        packages = service().directory / 'packages'
        packages.mkdir(exist_ok=True)
        package = packages / f'{version_id}.zip'
        with service().lock:
            if not package.exists():
                temporary = packages / f'.staging-{uid()}.zip'
                with zipfile.ZipFile(temporary, "x", compression=zipfile.ZIP_DEFLATED) as archive:
                    for path in (service().directory / "snapshots" / version_id).iterdir():
                        if path.is_file():
                            archive.write(path, path.name)
                temporary.rename(package)
        return FileResponse(package, filename=f'dataset-{version_id}.zip', media_type='application/zip')

    dist = cmp.ROOT / "reviewer/frontend/dist"
    if dist.exists():
        app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")
    else:
        @app.get("/")
        def setup_required():
            return JSONResponse({"detail": "Frontend has not been built. Run setup-reviewer.ps1 first."}, status_code=503)
    return app


app = create_app(os.environ.get("REVIEWER_DATA_DIR"))

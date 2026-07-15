import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import csv_export, database, jobs, ocr, taxonomy

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("warehouse_scan")

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
UPLOADS_DIR = BASE_DIR / "storage" / "uploads"

MAX_FILES_PER_BATCH = 500
MAX_FILE_SIZE_BYTES = 25 * 1024 * 1024  # 25 MB
ALLOWED_CONTENT_PREFIXES = ("image/",)


@asynccontextmanager
async def lifespan(app: FastAPI):
    database.init_db()
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    if not ocr.tesseract_available():
        logger.warning(
            "Tesseract OCR binary not found (checked PATH and default install "
            "location). OCR will fail until it is installed."
        )

    stuck = database.get_incomplete_images()
    if stuck:
        logger.info("Resuming %d image(s) left mid-batch by a previous run.", len(stuck))
        by_job: dict[str, list] = {}
        for row in stuck:
            path = Path(row["stored_path"]) if row["stored_path"] else None
            if path is None:
                path = UPLOADS_DIR / row["job_id"] / f"{row['id']}{Path(row['original_filename']).suffix}"
            by_job.setdefault(row["job_id"], []).append((row["id"], path, row["original_filename"]))
        for job_id, items in by_job.items():
            jobs.submit_job_images(job_id, items)

    yield


app = FastAPI(title="Warehouse Parcel Scan", lifespan=lifespan)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"error": "Something went wrong processing your request. Please try again."},
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@app.post("/api/jobs", status_code=202)
async def create_job(files: list[UploadFile] = File(default=[])):
    if not files:
        raise HTTPException(status_code=400, detail="Please select at least one image to upload.")
    if len(files) > MAX_FILES_PER_BATCH:
        raise HTTPException(
            status_code=400,
            detail=f"Too many files in one batch ({len(files)}). Max is {MAX_FILES_PER_BATCH}; "
            f"please split into smaller batches.",
        )

    job_id = database.new_id()
    job_dir = UPLOADS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    database.create_job(job_id, total=len(files), created_at=_now_iso())

    items = []
    for seq, upload in enumerate(files):
        original_name = upload.filename or f"file_{seq}"
        content = await upload.read()

        image_id = database.new_id()
        database.create_image_row(image_id, job_id, seq, original_name)

        if len(content) == 0:
            database.save_image_result(
                image_id, job_id,
                {"status_code": 901, "status_key": "CORRUPT_FILE",
                 "notes": "Uploaded file is empty (0 bytes)."},
            )
            continue
        if len(content) > MAX_FILE_SIZE_BYTES:
            database.save_image_result(
                image_id, job_id,
                {"status_code": 902, "status_key": "UNSUPPORTED_FILE",
                 "notes": f"File exceeds the {MAX_FILE_SIZE_BYTES // (1024 * 1024)} MB size limit."},
            )
            continue

        ext = Path(original_name).suffix.lower() or ".bin"
        stored_path = job_dir / f"{image_id}{ext}"
        stored_path.write_bytes(content)
        items.append((image_id, stored_path, original_name))

    if items:
        jobs.submit_job_images(job_id, items)
    else:
        database.set_job_state(job_id, "done")

    return {"job_id": job_id, "total": len(files)}


@app.get("/api/jobs")
def list_jobs():
    rows = database.list_jobs()
    return [dict(r) for r in rows]


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    job = database.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    counts = database.get_status_counts(job_id)
    return {
        **dict(job),
        "status_counts": counts,
    }


@app.get("/api/jobs/{job_id}/results")
def get_job_results(job_id: str):
    job = database.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    rows = database.get_images_for_job(job_id)
    return [dict(r) for r in rows]


@app.get("/api/jobs/{job_id}/csv")
def download_csv(job_id: str):
    job = database.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    csv_text = csv_export.build_csv(job_id)
    return PlainTextResponse(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="parcel_scan_{job_id}.csv"'},
    )


@app.get("/api/jobs/{job_id}/image/{image_id}")
def get_image(job_id: str, image_id: str):
    job = database.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    rows = database.get_images_for_job(job_id)
    row = next((r for r in rows if r["id"] == image_id), None)
    if row is None or not row["stored_path"]:
        raise HTTPException(status_code=404, detail="Image not found.")
    path = Path(row["stored_path"])
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Image file is no longer available.")
    return FileResponse(path)


@app.get("/api/taxonomy")
def get_taxonomy():
    return [
        {
            "code": s.code,
            "key": s.key,
            "label": s.label,
            "description": s.description,
            "severity": s.severity,
        }
        for s in taxonomy.STATUSES
    ]


app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")

"""Background processing so a batch upload never blocks the request thread.

Each image is submitted as its own task to a shared thread pool, so images
from different jobs interleave and a large batch keeps making progress even
while the user has navigated away -- they can reload the job URL later and
poll the same job id for results.
"""

import logging
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import database, pipeline

logger = logging.getLogger("warehouse_scan.jobs")

MAX_WORKERS = int(os.environ.get("SCAN_WORKERS", "4"))
_executor = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="scan-worker")


def _process_one(image_id: str, job_id: str, path: Path, original_filename: str) -> None:
    try:
        database.mark_image_processing(image_id, str(path))
        result = pipeline.process_image(path, original_filename)
        database.save_image_result(image_id, job_id, result)
    except Exception:  # noqa: BLE001 - must never take down the worker pool
        logger.exception("Unhandled error processing image %s (job %s)", image_id, job_id)
        database.save_image_result(
            image_id,
            job_id,
            {
                "status_code": 903,
                "status_key": "PROCESSING_ERROR",
                "notes": "Unexpected internal error while processing this image.",
            },
        )
    finally:
        _maybe_finish_job(job_id)


def _maybe_finish_job(job_id: str) -> None:
    job = database.get_job(job_id)
    if job and job["processed"] >= job["total"]:
        database.set_job_state(job_id, "done")


def submit_job_images(job_id: str, items: list[tuple[str, Path, str]]) -> None:
    """items: list of (image_id, stored_path, original_filename)."""
    database.set_job_state(job_id, "processing")
    for image_id, path, original_filename in items:
        _executor.submit(_process_one, image_id, job_id, path, original_filename)

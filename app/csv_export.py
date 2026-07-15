import csv
import io

from . import database, taxonomy

COLUMNS = [
    "filename",
    "status_code",
    "status_key",
    "status_label",
    "tracking_number",
    "weight_kg",
    "length_cm",
    "width_cm",
    "height_cm",
    "dimensions_raw",
    "location",
    "timestamp_raw",
    "notes",
]


def build_csv(job_id: str) -> str:
    rows = database.get_images_for_job(job_id)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(COLUMNS)
    for r in rows:
        status_label = ""
        if r["status_key"] and r["status_key"] in taxonomy.BY_KEY:
            status_label = taxonomy.BY_KEY[r["status_key"]].label
        writer.writerow(
            [
                r["original_filename"],
                r["status_code"] if r["status_code"] is not None else "",
                r["status_key"] or "",
                status_label,
                r["tracking_number"] or "",
                r["weight_kg"] if r["weight_kg"] is not None else "",
                r["length_cm"] if r["length_cm"] is not None else "",
                r["width_cm"] if r["width_cm"] is not None else "",
                r["height_cm"] if r["height_cm"] is not None else "",
                r["dims_raw"] or "",
                r["location"] or "",
                r["timestamp_raw"] or "",
                r["notes"] or "",
            ]
        )
    return buf.getvalue()

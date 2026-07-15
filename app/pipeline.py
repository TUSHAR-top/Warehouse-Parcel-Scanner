"""Per-image processing pipeline: file validation -> OCR -> scene heuristics
-> a single taxonomy status + extracted fields.
"""

import traceback
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, UnidentifiedImageError

from . import ocr, taxonomy, vision

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png"}
SIGNIFICANT_SECOND_PARCEL_RATIO = 0.02


def _load_image_bgr(path: Path) -> np.ndarray:
    with Image.open(path) as im:
        im.verify()
    with Image.open(path) as im:
        im = im.convert("RGB")
        arr = np.array(im)
    return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)


def process_image(path: Path, original_filename: str) -> dict:
    ext = Path(original_filename).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        st = taxonomy.status("UNSUPPORTED_FILE")
        return {
            "status_code": st.code,
            "status_key": st.key,
            "notes": f"File extension '{ext or '(none)'}' is not JPG/PNG.",
        }

    try:
        img_bgr = _load_image_bgr(path)
    except (UnidentifiedImageError, OSError, ValueError):
        st = taxonomy.status("CORRUPT_FILE")
        return {
            "status_code": st.code,
            "status_key": st.key,
            "notes": "The file could not be decoded as a valid image.",
        }

    try:
        return _analyze(img_bgr)
    except Exception as exc:  # noqa: BLE001 - one bad image must not kill the batch
        st = taxonomy.status("PROCESSING_ERROR")
        return {
            "status_code": st.code,
            "status_key": st.key,
            "notes": f"Unexpected error: {exc}",
            "error": traceback.format_exc(limit=5),
        }


def _analyze(img_bgr: np.ndarray) -> dict:
    ocr_result = ocr.run_ocr(img_bgr)
    fields = ocr.parse_fields(ocr_result)
    scene = vision.analyze_scene(img_bgr, fields.overlay_bbox)

    base = {
        "tracking_number": fields.tracking_number,
        "weight_kg": fields.weight_kg,
        "weight_raw": fields.weight_raw,
        "length_cm": fields.length_cm,
        "width_cm": fields.width_cm,
        "height_cm": fields.height_cm,
        "dims_raw": fields.dims_raw,
        "location": fields.location,
        "timestamp_raw": fields.timestamp_raw,
    }

    significant = [
        c for c in scene.candidates if c.area_ratio >= SIGNIFICANT_SECOND_PARCEL_RATIO
    ]

    if len(significant) == 0:
        st = taxonomy.status("NO_PARCEL")
        return {**base, "status_code": st.code, "status_key": st.key,
                "notes": "No parcel-sized object detected on the belt."}

    if len(significant) >= 2:
        st = taxonomy.status("MULTIPLE_PARCELS")
        return {**base, "status_code": st.code, "status_key": st.key,
                "notes": f"Detected {len(significant)} candidate parcels in frame."}

    candidate = significant[0]

    if candidate.touches_border:
        st = taxonomy.status("PARCEL_PARTIAL")
        return {**base, "status_code": st.code, "status_key": st.key,
                "notes": "Parcel bounding area touches the edge of the frame; "
                         "likely only partially captured."}

    if candidate.solidity < vision.SOLIDITY_OBSTRUCTED_THRESHOLD:
        st = taxonomy.status("LABEL_OBSTRUCTED")
        return {**base, "status_code": st.code, "status_key": st.key,
                "notes": f"Parcel outline is irregular (solidity="
                         f"{candidate.solidity:.2f}), consistent with a hand "
                         f"or object partly covering it."}

    has_tracking = bool(fields.tracking_number)
    has_weight = fields.weight_kg is not None
    has_dims = (
        fields.length_cm is not None
        or fields.width_cm is not None
        or fields.height_cm is not None
        or fields.dims_raw is not None
    )

    if not has_tracking and not has_weight and not has_dims:
        st = taxonomy.status("LABEL_UNREADABLE")
        return {**base, "status_code": st.code, "status_key": st.key,
                "notes": "Parcel is visible and unobstructed but no label/"
                         "overlay fields could be read."}

    if has_tracking and (has_weight or has_dims):
        st = taxonomy.status("CLEAN_READ")
        return {**base, "status_code": st.code, "status_key": st.key, "notes": None}

    missing = []
    if not has_tracking:
        missing.append("tracking number")
    if not has_weight:
        missing.append("weight")
    if not has_dims:
        missing.append("dimensions")
    st = taxonomy.status("PARTIAL_DATA")
    return {**base, "status_code": st.code, "status_key": st.key,
            "notes": "Missing: " + ", ".join(missing) + "."}

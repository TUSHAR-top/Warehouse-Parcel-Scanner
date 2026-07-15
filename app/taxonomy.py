"""Status flag taxonomy for parcel scan results.

Every processed image ends up with exactly one status code from this table.
Codes are grouped by numeric band so the UI/CSV consumer can reason about
severity without a lookup: 1xx = usable data, 2xx = scene problem (nothing
usable extracted), 9xx = the file itself was the problem.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Status:
    code: int
    key: str
    label: str
    description: str
    severity: str  # "ok" | "warning" | "error"


STATUSES = [
    Status(
        100, "CLEAN_READ", "Clean read",
        "Single parcel, fully visible, unobstructed. Tracking number plus "
        "weight and/or dimensions extracted with good confidence.",
        "ok",
    ),
    Status(
        110, "PARTIAL_DATA", "Partial data",
        "A single, unobstructed parcel was read but one or more fields "
        "(tracking number, weight, dimensions) were not present or not "
        "confidently extracted from the label/overlay.",
        "warning",
    ),
    Status(
        200, "NO_PARCEL", "No parcel in frame",
        "No parcel could be detected on the belt/scanning area.",
        "error",
    ),
    Status(
        201, "MULTIPLE_PARCELS", "Multiple parcels detected",
        "More than one parcel is visible in the frame, so a single clean "
        "read is not possible.",
        "error",
    ),
    Status(
        202, "PARCEL_PARTIAL", "Parcel partially visible",
        "The parcel is cut off by the edge of the frame.",
        "error",
    ),
    Status(
        203, "LABEL_OBSTRUCTED", "Label likely obstructed",
        "A hand or object appears to be covering part of the parcel/label.",
        "error",
    ),
    Status(
        204, "LABEL_UNREADABLE", "Label unreadable",
        "A parcel is present and unobstructed but no usable text could be "
        "read from the label/overlay (blur, glare, low contrast, etc.).",
        "error",
    ),
    Status(
        901, "CORRUPT_FILE", "Corrupt or unreadable file",
        "The uploaded file could not be decoded as an image.",
        "error",
    ),
    Status(
        902, "UNSUPPORTED_FILE", "Unsupported file type",
        "The uploaded file is not a JPG or PNG image.",
        "error",
    ),
    Status(
        903, "PROCESSING_ERROR", "Processing error",
        "An unexpected error occurred while processing this image.",
        "error",
    ),
]

BY_KEY = {s.key: s for s in STATUSES}
BY_CODE = {s.code: s for s in STATUSES}


def status(key: str) -> Status:
    return BY_KEY[key]

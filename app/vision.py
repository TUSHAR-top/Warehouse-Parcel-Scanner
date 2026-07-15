"""Classical-CV scene heuristics: parcel presence / count / crop / obstruction.

No ML model is used here -- these are contour-based heuristics tuned against
the provided sample images. They are deliberately conservative: an ambiguous
scene should fall through to an honest flag rather than a guessed one.
"""

from dataclasses import dataclass

import cv2
import numpy as np

MIN_PARCEL_AREA_RATIO = 0.015
MAX_PARCEL_AREA_RATIO = 0.85
BORDER_MARGIN_PX = 3
SOLIDITY_OBSTRUCTED_THRESHOLD = 0.80
# A big, low-solidity blob is far more likely to be the empty belt/machine
# background (irregular, spans much of the frame) than a parcel (boxy,
# convex). Reject candidates that look like background before classifying.
BACKGROUND_AREA_RATIO = 0.25
BACKGROUND_SOLIDITY_MAX = 0.50


@dataclass
class ParcelCandidate:
    bbox: tuple[int, int, int, int]
    area_ratio: float
    solidity: float
    touches_border: bool


@dataclass
class SceneResult:
    candidates: list[ParcelCandidate]


def _mask_overlay(gray: np.ndarray, overlay_bbox: tuple[int, int, int, int] | None) -> np.ndarray:
    if overlay_bbox is None:
        return gray
    out = gray.copy()
    h, w = out.shape[:2]
    x0, y0, x1, y1 = overlay_bbox
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(w, x1), min(h, y1)
    if x1 <= x0 or y1 <= y0:
        return out
    # fill with the median of a nearby strip so it doesn't register as an edge
    fill = int(np.median(out))
    out[y0:y1, x0:x1] = fill
    return out


def analyze_scene(
    img_bgr: np.ndarray, overlay_bbox: tuple[int, int, int, int] | None
) -> SceneResult:
    h, w = img_bgr.shape[:2]
    total_area = float(h * w)

    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    gray = _mask_overlay(gray, overlay_bbox)
    blurred = cv2.GaussianBlur(gray, (7, 7), 0)

    _, binary = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    # The belt/background is darker than parcel surfaces in these captures;
    # if Otsu decided the *majority* is foreground, polarity is flipped.
    if float((binary == 255).mean()) > 0.5:
        binary = 255 - binary

    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (25, 25))
    closed = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel, iterations=3)
    closed = cv2.dilate(closed, kernel, iterations=1)

    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    candidates = []
    for c in contours:
        area = cv2.contourArea(c)
        area_ratio = area / total_area
        if area_ratio < MIN_PARCEL_AREA_RATIO or area_ratio > MAX_PARCEL_AREA_RATIO:
            continue
        x, y, bw, bh = cv2.boundingRect(c)
        hull = cv2.convexHull(c)
        hull_area = cv2.contourArea(hull)
        solidity = (area / hull_area) if hull_area > 0 else 1.0
        if area_ratio > BACKGROUND_AREA_RATIO and solidity < BACKGROUND_SOLIDITY_MAX:
            continue  # looks like empty belt / machine surface, not a parcel
        touches_border = (
            x <= BORDER_MARGIN_PX
            or y <= BORDER_MARGIN_PX
            or x + bw >= w - BORDER_MARGIN_PX
            or y + bh >= h - BORDER_MARGIN_PX
        )
        candidates.append(
            ParcelCandidate(
                bbox=(x, y, x + bw, y + bh),
                area_ratio=area_ratio,
                solidity=solidity,
                touches_border=touches_border,
            )
        )

    candidates.sort(key=lambda c: c.area_ratio, reverse=True)
    return SceneResult(candidates=candidates)

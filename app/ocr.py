"""OCR of the sorter-camera on-screen data overlay + regex field parsing.

The images in scope are not photos of a printed label held up to a camera;
they are warehouse sorter captures with a burned-in HUD-style text overlay
(bright text on a dark panel, e.g. "AWB: ...", "Weight: 0.76 Kgs") in a
corner of the frame, with the physical parcel visible on the belt. Several
overlay layouts/vocabularies are in use across sites, so the parser is
keyword/regex driven (line by line) rather than tied to one fixed layout.
"""

import re
import shutil
from dataclasses import dataclass, field

import cv2
import numpy as np
import pytesseract
from pytesseract import Output

TESSERACT_CMD = shutil.which("tesseract") or r"C:\Program Files\Tesseract-OCR\tesseract.exe"
pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD


def tesseract_available() -> bool:
    import os

    return os.path.isfile(TESSERACT_CMD) or shutil.which("tesseract") is not None


@dataclass
class OcrLine:
    text: str
    conf: float
    bbox: tuple[int, int, int, int]  # x0, y0, x1, y1 in the *processed* image


@dataclass
class OcrResult:
    lines: list[OcrLine]
    scale: float  # processed_size = original_size * scale

    def bbox_in_original(self, bbox: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
        x0, y0, x1, y1 = bbox
        s = self.scale
        return (int(x0 / s), int(y0 / s), int(x1 / s), int(y1 / s))


TARGET_LONG_SIDE = 1400
MIN_LONG_SIDE = 1000


def _resize_for_ocr(gray: np.ndarray) -> tuple[np.ndarray, float]:
    h, w = gray.shape[:2]
    long_side = max(h, w)
    if long_side > TARGET_LONG_SIDE:
        scale = TARGET_LONG_SIDE / long_side
    elif long_side < MIN_LONG_SIDE:
        scale = min(2.0, MIN_LONG_SIDE / long_side)
    else:
        scale = 1.0
    if scale != 1.0:
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    return gray, scale


def _fix_polarity(gray: np.ndarray) -> np.ndarray:
    """Overlay text is consistently bright-on-dark; tesseract expects the
    opposite, so invert when the likely overlay region (top-left) is dark."""
    h, w = gray.shape[:2]
    region = gray[: int(h * 0.45), : int(w * 0.75)]
    if region.size and float(region.mean()) < 128.0:
        return 255 - gray
    return gray


def run_ocr(img_bgr: np.ndarray) -> OcrResult:
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    gray, scale = _resize_for_ocr(gray)
    gray = _fix_polarity(gray)

    data = pytesseract.image_to_data(
        gray, config="--psm 6", output_type=Output.DICT
    )

    lines_map: dict[tuple[int, int, int], dict] = {}
    n = len(data["text"])
    for i in range(n):
        word = data["text"][i].strip()
        if not word:
            continue
        conf_raw = data["conf"][i]
        try:
            conf = float(conf_raw)
        except (TypeError, ValueError):
            conf = -1.0
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
        entry = lines_map.setdefault(
            key, {"words": [], "confs": [], "x0": x, "y0": y, "x1": x + w, "y1": y + h}
        )
        entry["words"].append(word)
        if conf >= 0:
            entry["confs"].append(conf)
        entry["x0"] = min(entry["x0"], x)
        entry["y0"] = min(entry["y0"], y)
        entry["x1"] = max(entry["x1"], x + w)
        entry["y1"] = max(entry["y1"], y + h)

    lines = []
    for entry in lines_map.values():
        text = " ".join(entry["words"])
        conf = sum(entry["confs"]) / len(entry["confs"]) if entry["confs"] else -1.0
        lines.append(
            OcrLine(
                text=text,
                conf=conf,
                bbox=(entry["x0"], entry["y0"], entry["x1"], entry["y1"]),
            )
        )
    return OcrResult(lines=lines, scale=scale)


# ---------------------------------------------------------------------------
# Field parsing
# ---------------------------------------------------------------------------

_TRACKING_KEYS = [
    re.compile(r"^\s*AWB[\s_]*No\.?\s*[:\-]?\s*(.+)$", re.I),
    re.compile(r"^\s*AWB\s*[:\-]?\s*(.+)$", re.I),
    re.compile(r"^\s*Barcode\s*1\s*D\s*[:\-]?\s*(.+)$", re.I),
    re.compile(r"^\s*Barcode\s*[:\-]?\s*(.+)$", re.I),
    re.compile(r"^\s*Tracking\s*(?:No\.?)?\s*[:\-]?\s*(.+)$", re.I),
]
_BAD_TRACKING_VALUES = {"noread", "n/a", "na", "none", "-", ""}

_NUM = r"\d+(?:\.\d+)?"

_DIM_COMBINED_RE = re.compile(
    r"Dimension[s]?\s*(?:\([^)]*\))?\s*[:\-]?\s*"
    rf"({_NUM})\s*[xX×]\s*({_NUM})\s*[xX×]\s*({_NUM})\s*(cm|mm)?",
    re.I,
)
_DIM_LETTERED_RE = re.compile(r"([LBWH])\D{0,2}0*(\d+(?:\.\d+)?)", re.I)
_LENGTH_RE = re.compile(rf"^\s*Length\s*[:\-]?\s*({_NUM})\s*(cm|mm)?", re.I)
_WIDTH_RE = re.compile(rf"^\s*(?:Width|Breadth)\s*[:\-]?\s*({_NUM})\s*(cm|mm)?", re.I)
_HEIGHT_RE = re.compile(rf"^\s*Height\s*[:\-]?\s*({_NUM})\s*(cm|mm)?", re.I)

_WEIGHT_RE = re.compile(
    rf"^\s*Weight\s*[:\-]?\s*({_NUM})\s*(kgs?|kg|g|grams?)?", re.I
)
_LOCATION_RE = re.compile(r"^\s*Location\s*[:\-]?\s*(.+)$", re.I)
_TIME_RE = re.compile(r"^\s*(?:Timestamp|Time)\s*[:\-]?\s*(.+)$", re.I)

_OVERLAY_KEYWORDS = re.compile(
    r"(AWB|Barcode|Dimension|Length|Width|Breadth|Height|Weight|Volume|"
    r"Location|Time|Sorter|Machine)",
    re.I,
)


MIN_PLAUSIBLE_DIM_CM = 1.0
MAX_PLAUSIBLE_DIM_CM = 200.0
MIN_PLAUSIBLE_WEIGHT_KG = 0.005
MAX_PLAUSIBLE_WEIGHT_KG = 100.0


def _to_cm(value: float, unit: str | None) -> float | None:
    cm = round(value / 10.0, 2) if unit and unit.lower() == "mm" else round(value, 2)
    if not (MIN_PLAUSIBLE_DIM_CM <= cm <= MAX_PLAUSIBLE_DIM_CM):
        return None
    return cm


def _infer_unit_from_padding(raw_values: list[str]) -> str | None:
    """Several overlay formats write mm dimensions as zero-padded integers
    with no unit token (e.g. "0208 X 0148 X 0163"). Leading zeros with no
    decimal point is a strong, consistently-observed signal for mm."""
    if all(re.fullmatch(r"0\d{2,}", v) for v in raw_values):
        return "mm"
    return None


def _clean_tracking_value(raw: str) -> str:
    """OCR sometimes appends stray short tokens after the real value on the
    same line; prefer the first token that actually looks like an ID."""
    candidate = raw.strip(" .")
    tokens = candidate.split()
    if not tokens:
        return candidate
    for tok in tokens:
        if sum(ch.isalnum() for ch in tok) >= 6:
            return tok
    return candidate


@dataclass
class ParsedFields:
    tracking_number: str | None = None
    length_cm: float | None = None
    width_cm: float | None = None
    height_cm: float | None = None
    dims_raw: str | None = None
    weight_kg: float | None = None
    weight_raw: str | None = None
    location: str | None = None
    timestamp_raw: str | None = None
    overlay_bbox: tuple[int, int, int, int] | None = None
    matched_line_confs: list[float] = field(default_factory=list)


def parse_fields(ocr: OcrResult) -> ParsedFields:
    result = ParsedFields()
    bbox_acc = None

    def absorb_bbox(b):
        nonlocal bbox_acc
        x0, y0, x1, y1 = ocr.bbox_in_original(b)
        if bbox_acc is None:
            bbox_acc = [x0, y0, x1, y1]
        else:
            bbox_acc[0] = min(bbox_acc[0], x0)
            bbox_acc[1] = min(bbox_acc[1], y0)
            bbox_acc[2] = max(bbox_acc[2], x1)
            bbox_acc[3] = max(bbox_acc[3], y1)

    for line in ocr.lines:
        text = line.text.strip()
        if not text or not _OVERLAY_KEYWORDS.search(text):
            continue

        # Every line identified as part of the overlay (matched a known
        # keyword) is masked out of the scene image later, even if we don't
        # parse a specific field from it (e.g. Sorter_ID, MachineID) -- this
        # keeps stray overlay text from being mistaken for a second parcel.
        absorb_bbox(line.bbox)
        if line.conf >= 0:
            result.matched_line_confs.append(line.conf)

        matched_this_line = False

        if result.tracking_number is None:
            for pat in _TRACKING_KEYS:
                m = pat.match(text)
                if m:
                    candidate = _clean_tracking_value(m.group(1))
                    if candidate and candidate.lower() not in _BAD_TRACKING_VALUES:
                        result.tracking_number = candidate
                        matched_this_line = True
                    break

        if result.length_cm is None and result.width_cm is None and result.height_cm is None:
            m = _DIM_COMBINED_RE.search(text)
            if m:
                a, b, c, unit = m.groups()
                result.dims_raw = m.group(0)
                if unit is None:
                    unit = _infer_unit_from_padding([a, b, c])
                result.length_cm = _to_cm(float(a), unit)
                result.width_cm = _to_cm(float(b), unit)
                result.height_cm = _to_cm(float(c), unit)
                matched_this_line = True
            else:
                letters = _DIM_LETTERED_RE.findall(text)
                if len(letters) >= 2 and re.search(r"Dimension", text, re.I):
                    result.dims_raw = text
                    letter_map = {l.upper(): v for l, v in letters}
                    unit = _infer_unit_from_padding(list(letter_map.values()))
                    if unit:
                        # L/H present in all observed lettered formats; B and
                        # W are used interchangeably for the same axis.
                        if "L" in letter_map:
                            result.length_cm = _to_cm(float(letter_map["L"]), unit)
                        if "W" in letter_map:
                            result.width_cm = _to_cm(float(letter_map["W"]), unit)
                        elif "B" in letter_map:
                            result.width_cm = _to_cm(float(letter_map["B"]), unit)
                        if "H" in letter_map:
                            result.height_cm = _to_cm(float(letter_map["H"]), unit)
                    matched_this_line = True

        if result.length_cm is None:
            m = _LENGTH_RE.match(text)
            if m:
                result.length_cm = _to_cm(float(m.group(1)), m.group(2))
                matched_this_line = True
        if result.width_cm is None:
            m = _WIDTH_RE.match(text)
            if m:
                result.width_cm = _to_cm(float(m.group(1)), m.group(2))
                matched_this_line = True
        if result.height_cm is None:
            m = _HEIGHT_RE.match(text)
            if m:
                result.height_cm = _to_cm(float(m.group(1)), m.group(2))
                matched_this_line = True

        if result.weight_kg is None and result.weight_raw is None:
            m = _WEIGHT_RE.match(text)
            if m:
                raw_val, unit = m.groups()
                result.weight_raw = text
                weight_kg = None
                if unit:
                    unit = unit.lower()
                    val = float(raw_val)
                    weight_kg = round(val / 1000.0, 3) if unit.startswith("g") else round(val, 3)
                elif "." in raw_val:
                    # Unitless decimal values consistently mean kg in the
                    # observed overlay formats.
                    weight_kg = round(float(raw_val), 3)
                if weight_kg is not None and MIN_PLAUSIBLE_WEIGHT_KG <= weight_kg <= MAX_PLAUSIBLE_WEIGHT_KG:
                    result.weight_kg = weight_kg
                matched_this_line = True

        if result.location is None:
            m = _LOCATION_RE.match(text)
            if m:
                result.location = m.group(1).strip()
                matched_this_line = True

        if result.timestamp_raw is None:
            m = _TIME_RE.match(text)
            if m:
                result.timestamp_raw = m.group(1).strip()
                matched_this_line = True

    if bbox_acc:
        # pad a little so we don't clip anti-aliased overlay edges
        pad = 6
        result.overlay_bbox = (
            max(0, bbox_acc[0] - pad),
            max(0, bbox_acc[1] - pad),
            bbox_acc[2] + pad,
            bbox_acc[3] + pad,
        )

    return result

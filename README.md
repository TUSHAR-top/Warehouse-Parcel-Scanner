# Warehouse Parcel Scanner

Turns a bulk upload of warehouse parcel photos into structured shipment records (tracking number, weight, dimensions) or an honest flag when a clean read isn't possible — plus a downloadable CSV of the whole batch.

## Table of Contents

- [The Problem (What & Why)](#the-problem-what--why)
- [Solution at a Glance](#solution-at-a-glance)
- [Architecture (How)](#architecture-how)
- [Tech Stack](#tech-stack)
- [Getting Started (run locally in under 15 minutes)](#getting-started-run-locally-in-under-15-minutes)
- [How to Use (for non-technical users)](#how-to-use-for-non-technical-users)
- [Output Format](#output-format)
- [Status Flags & Edge Cases](#status-flags--edge-cases)
- [Assumptions & Design Trade-offs](#assumptions--design-trade-offs)
- [Known Limitations](#known-limitations)
- [What I'd Improve With More Time](#what-id-improve-with-more-time)
- [Testing](#testing)
- [Project Structure](#project-structure)
- [Demo](#demo)

## The Problem (What & Why)

At a warehouse scanning station, every parcel moving down the line is photographed, and every photo shows a courier shipping label with a tracking number, a weight, and dimensions on it. Today, a person looks at each photo and types those details into a sheet by hand. That's slow, and with hundreds of parcels an hour, mistakes creep in.

This system replaces the typing, not the judgment. A warehouse user uploads a batch of photos through a web page. Each photo is read automatically and turned into a row of data: tracking number, weight, and dimensions, whichever of those are actually visible. The whole batch can then be downloaded as a single CSV file.

Photos from a real scanning line are not all clean. Some frames catch an empty belt, some catch two parcels at once, some catch a parcel only half in frame, and some have a hand or object over the label. For these, the system does not guess — it says so. A wrong tracking number or a made-up weight is worse than no data at all, because a warehouse team will trust the sheet and act on it; an honest flag tells them to look at that photo again instead of silently corrupting the record.

## Solution at a Glance

Photos go in through the browser, each one is analyzed independently by an OCR + computer-vision pipeline, and the whole batch comes out as a CSV — one row per photo, either a clean extraction or an explicit flag explaining why it isn't one.

| Brief requirement | How this repo meets it |
|---|---|
| Bulk upload of parcel photos | Drag-and-drop / multi-select upload, up to 500 JPG/PNG files per batch ([static/index.html](static/index.html), [app/main.py](app/main.py)) |
| Structured record per image | Tracking/AWB number, weight (kg), length/width/height (cm) extracted per photo ([app/ocr.py](app/ocr.py)) |
| Honest flag over wrong guess | 10-status taxonomy covering scene problems and file problems, checked before any extracted text is trusted ([app/taxonomy.py](app/taxonomy.py), [app/pipeline.py](app/pipeline.py)) |
| Batch downloadable as CSV | One-click CSV export of every row in a batch, any time after upload ([app/csv_export.py](app/csv_export.py)) |
| Large batch doesn't freeze the UI | Upload returns immediately; each image processes on a background thread pool while the browser polls for progress ([app/jobs.py](app/jobs.py)) |
| Runs without paid API keys | No paid service is called anywhere in the pipeline — OCR and vision both run locally (see [Free path](#free-path)) |

## Architecture (How)

<!-- ============================================= -->
<!-- ARCHITECTURE DIAGRAM — paste image below      -->
<!-- Replace the line under this comment with:     -->
<!-- ![Architecture Diagram](docs/images/architecture.png) -->
<!-- ============================================= -->

*[Architecture diagram will be placed here]*
<img width="1500" height="980" alt="image" src="https://github.com/user-attachments/assets/f078a98f-f36c-4c6b-a891-cfcd35ad7ff5" />


**Pipeline walkthrough, in the order code actually runs:**

1. **Upload** — The browser posts a multipart batch to `POST /api/jobs` ([app/main.py](app/main.py)). The request rejects an empty selection (400) and batches over 500 files (400) before anything is written to disk.
2. **Per-file intake checks** — For each file, `main.py` flags a 0-byte upload as `CORRUPT_FILE` and anything over 25 MB as `UNSUPPORTED_FILE`, without touching the image decoder. Everything else is written to `storage/uploads/<job_id>/<image_id><ext>` and queued.
3. **Background dispatch** — `jobs.submit_job_images()` marks the job `processing` and submits each image to a shared `ThreadPoolExecutor` ([app/jobs.py](app/jobs.py)), so the HTTP response to the browser doesn't wait for any OCR/CV work.
4. **File validation** — In the worker thread, `pipeline.process_image()` ([app/pipeline.py](app/pipeline.py)) rejects unsupported extensions (`UNSUPPORTED_FILE`) and files that fail to decode as an image via Pillow (`CORRUPT_FILE`).
5. **OCR** — `ocr.run_ocr()` ([app/ocr.py](app/ocr.py)) converts to grayscale, resizes so the long side is 1000–1400 px, corrects for the overlay's bright-text-on-dark-panel polarity, then runs Tesseract (`--psm 6`) to get text lines with bounding boxes and confidence.
6. **Field parsing** — `ocr.parse_fields()` line-matches each OCR line against a keyword/regex vocabulary (AWB/Barcode/Tracking, Dimensions in combined or lettered `L/B/W/H` form, Weight, Location, Timestamp), normalizes units (mm→cm, g→kg), and rejects implausible values (e.g. a 0 cm dimension). It also accumulates the bounding box of every matched overlay line.
7. **Scene analysis** — `vision.analyze_scene()` ([app/vision.py](app/vision.py)) masks out the overlay region found in step 6, thresholds and closes the image, and finds contours. Each contour is scored by area ratio, solidity (how convex/regular its outline is), and whether it touches the frame border; background-like blobs (large area, low solidity) are discarded.
8. **Flag decision** — Back in `pipeline._analyze()`, a strict decision tree runs *geometry first, text second*: zero parcel candidates → `NO_PARCEL`; two or more → `MULTIPLE_PARCELS`; the one candidate touches the border → `PARCEL_PARTIAL`; its outline is irregular (solidity below threshold) → `LABEL_OBSTRUCTED`. Only if the frame passes all of those does the code look at what OCR found: no fields at all → `LABEL_UNREADABLE`; tracking number **and** at least one of weight/dimensions → `CLEAN_READ`; anything less → `PARTIAL_DATA`, with the specific missing fields named in the notes.
9. **Persistence** — `database.save_image_result()` ([app/database.py](app/database.py)) writes the status and every extracted field to SQLite and increments the job's processed count; when `processed == total`, the job flips to `done`. `main.py`'s startup routine re-queues any image left `queued`/`processing` after an unclean restart, so a batch survives a server restart.
10. **Results & progress** — The frontend ([static/app.js](static/app.js)) polls `GET /api/jobs/{id}` every 1.5s for counts and `GET /api/jobs/{id}/results` for the row-level table; the job id is written into the URL so reloading or returning later resumes watching the same batch.
11. **CSV export** — `GET /api/jobs/{id}/csv` calls `csv_export.build_csv()` ([app/csv_export.py](app/csv_export.py)), which reads every row for the job straight from SQLite and streams it back as `parcel_scan_<job_id>.csv`. Nothing is cached to a CSV file on disk.

### Design decisions & why

- **Tesseract + OpenCV instead of a paid vision/LLM API.** The images in scope aren't photos of a printed label held up to a camera — they're sorter-camera captures with a burned-in HUD-style text overlay (`AWB: ...`, `Weight: 0.76 Kgs`) in a corner of the frame. That's a good match for classic OCR on a masked, high-contrast region, needs no training data, has no per-call cost, and satisfies the requirement to run without a paid key.
- **Deterministic code vs. model-driven steps.** Only text *recognition* (Tesseract itself) is a trained model. Field parsing (regex), parcel/obstruction detection (contour area + solidity + border checks), and the entire flag decision tree are plain, deterministic Python. This was deliberate: when a rule misfires, it traces back to one named threshold or regex, which is something a warehouse ops reviewer — or a developer under a deadline — can actually debug. A black-box classifier's wrong flag is much harder to explain.
- **Geometry gates text.** The decision tree in `pipeline._analyze()` checks scene problems (no parcel / multiple parcels / cropped / obstructed) *before* it looks at anything OCR read. A geometry problem invalidates any text on the frame, however clean the OCR looked — so a `LABEL_OBSTRUCTED` or `MULTIPLE_PARCELS` row can still carry extracted-but-unreliable text in its columns (this is verified against `test_images_data`, not hypothetical — see [Known Limitations](#known-limitations)). `CLEAN_READ` requires tracking number **and** at least one of weight/dimensions, not just any single field, because a tracking number alone is still commercially incomplete for a warehouse sheet.
- **What was considered and rejected.** A paid cloud OCR/vision API was rejected outright — the brief requires the grading run to work with no paid keys, and Tesseract already handles this overlay format well. A trained object detector for parcel counting/obstruction was also considered and rejected for this timeline: the sample images share fairly consistent belt/camera geometry, and a contour-area-and-solidity heuristic handles it adequately without labeled training data. That heuristic is the part most likely to need rework on truly different camera setups — see [What I'd Improve With More Time](#what-id-improve-with-more-time).

## Tech Stack

| Layer | Technology | Why chosen |
|---|---|---|
| Backend framework | FastAPI 0.139.0 | Async-capable REST API with automatic request validation, minimal boilerplate |
| ASGI server | Uvicorn 0.51.0 | Standard production-ready server for FastAPI |
| File uploads | python-multipart 0.0.32 | Required by FastAPI to parse multipart batch uploads |
| Image decoding/validation | Pillow 12.3.0 | Verifies a file actually decodes as an image before any CV/OCR work runs on it |
| Computer vision | OpenCV (opencv-python-headless 5.0.0.93) | Contour/morphology-based parcel detection — no GPU or training required |
| Array/numeric ops | NumPy 2.5.1 | Backs the pixel-array operations used by OpenCV and the OCR preprocessing |
| OCR engine | Tesseract OCR via pytesseract 0.3.13 | Free, open-source, runs fully offline — no API key, no per-call cost |
| Persistence | SQLite (Python stdlib `sqlite3`) | Zero-setup embedded database; survives server restarts; plenty for hundreds of images |
| Background processing | `concurrent.futures.ThreadPoolExecutor` (stdlib) | Lightweight bounded worker pool; no extra infrastructure (Redis/Celery) needed at this scale |
| Frontend | Vanilla HTML/CSS/JS ([static/](static/)) | No build step — served directly by FastAPI's `StaticFiles`, keeps "clone and run" simple |
| Containerization | Docker (`python:3.12-slim` + `apt tesseract-ocr`) | Reproducible deploy target, e.g. Render (see [render-build.sh](render-build.sh)) |

## Getting Started (run locally in under 15 minutes)

### Prerequisites

- Python 3.10 or newer
- Tesseract OCR binary installed and on your `PATH` (or at the default install location on Windows)
- ~5 minutes of internet access to install Python packages

### Free path

This project has no paid path to opt out of — there isn't one. Every extraction step (OCR via Tesseract, parcel/obstruction detection via OpenCV) runs entirely on your machine. No API key, no signup, no usage limit tied to a billing account. Nothing in the codebase calls an external paid service.

### Windows — one-click

1. Install Tesseract if it isn't already on your machine (≈2 min):
   ```powershell
   winget install --id Tesseract-OCR.TesseractOCR --accept-source-agreements --accept-package-agreements
   ```
   (or download the installer from https://github.com/UB-Mannheim/tesseract/wiki)
2. Double-click [start_app.bat](start_app.bat) in the project folder. It creates a `.venv`, installs `requirements.txt`, checks for Tesseract, and starts the server at `http://127.0.0.1:8000/`.

### Manual setup (Windows, macOS, or Linux)

```bash
# 1. Install Tesseract (skip if already installed)
#    macOS:   brew install tesseract
#    Debian/Ubuntu: sudo apt-get install -y tesseract-ocr
#    Windows: winget install --id Tesseract-OCR.TesseractOCR

# 2. Create a virtual environment and install dependencies (~2-3 min)
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

pip install -r requirements.txt

# 3. Start the server
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Then open **http://127.0.0.1:8000/** in your browser.

### Environment variables

There are no secrets to configure — the app doesn't call any external API. The one optional setting is:

| Variable | Default | Meaning |
|---|---|---|
| `SCAN_WORKERS` | `4` | Number of images processed concurrently by the background thread pool ([app/jobs.py](app/jobs.py)) |

The repo does not currently include a `.env.example` file — see the note at the end of this README.

### Verify it works (~2 minutes)

1. With the server running, go to `http://127.0.0.1:8000/`.
2. Upload a few images from [test_images_data/](test_images_data/) — for example `90146983102_jpg.rf.QswrXnKMqlMdbJouH5Pq.jpg` (a clean read) and `No Shipment ccaptured_png.rf.7xveM0Q6aPacgoxworum.png` (no parcel in frame).
3. Watch the progress bar reach 100%, then confirm the results table shows one `Clean read` row with a tracking number, weight, and dimensions, and one `No parcel in frame` row with those fields empty.
4. Click **Download CSV** and confirm the file opens with both rows.

## How to Use (for non-technical users)

**1. Upload a batch of photos.** Open the app in a browser, click the upload area (or drag photos onto it), and select as many JPG/PNG parcel photos as you have — up to 500 at a time. Click **Upload & Start Scan**.

<!-- ============================================= -->
<!-- SCREENSHOT: Upload screen                     -->
<!-- Replace the line below with:                  -->
<!-- ![Upload screen](docs/images/upload-screen.png) -->
<!-- ============================================= -->

*[Screenshot: Upload screen — will be placed here]*

**2. Watch progress.** A progress bar shows how many of the batch have been processed so far, with a live count of how many fell into each status. You can close the tab at any point — the batch keeps processing on the server, and reopening the same link later picks up right where it left off.

<!-- ============================================= -->
<!-- SCREENSHOT: Processing/progress view          -->
<!-- Replace the line below with:                  -->
<!-- ![Processing progress view](docs/images/processing-progress-view.png) -->
<!-- ============================================= -->

*[Screenshot: Processing/progress view — will be placed here]*

**3. Review results.** Each photo becomes one row: a thumbnail, its filename, a colored status badge, and whatever was read — tracking number, weight, dimensions. Rows that couldn't be read cleanly are flagged (not guessed) with a plain-language note explaining why. You can filter the table down to a single status, and click "what do the flags mean?" for a full reference of every flag.

<!-- ============================================= -->
<!-- SCREENSHOT: Results table with flags          -->
<!-- Replace the line below with:                  -->
<!-- ![Results table with flags](docs/images/results-table-with-flags.png) -->
<!-- ============================================= -->

*[Screenshot: Results table with flags — will be placed here]*

**4. Download the CSV.** One click exports every row in the batch — clean reads and flagged rows alike — as a single CSV file you can open in Excel or hand off downstream.

<!-- ============================================= -->
<!-- SCREENSHOT: CSV download                      -->
<!-- Replace the line below with:                  -->
<!-- ![CSV download](docs/images/csv-download.png) -->
<!-- ============================================= -->

*[Screenshot: CSV download — will be placed here]*

## Output Format

### CSV schema

| Column | Meaning |
|---|---|
| `filename` | Original filename as uploaded |
| `status_code` | Numeric taxonomy code (see [Status Flags & Edge Cases](#status-flags--edge-cases)) |
| `status_key` | Machine-readable status, e.g. `CLEAN_READ` |
| `status_label` | Human-readable status, e.g. "Clean read" |
| `tracking_number` | AWB/tracking/barcode ID read from the overlay; empty if not read |
| `weight_kg` | Weight in kilograms, normalized from kg or g on the label; empty if absent/unreadable/implausible |
| `length_cm` / `width_cm` / `height_cm` | Individual dimensions in centimeters, normalized from cm or mm; empty per-axis if not read |
| `dimensions_raw` | The raw matched "L x W x H" text when dimensions came from one combined line; empty when length/width/height were read from separate lines instead |
| `location` | Sorter/site location string from a "Location:" line, verbatim (may include OCR noise) |
| `timestamp_raw` | Capture timestamp string from a "Timestamp:"/"Time:" line, verbatim (not parsed into a structured date) |
| `notes` | Why a flag was raised, or which fields are missing for `PARTIAL_DATA`; empty for a full `CLEAN_READ` |

### Sample rows

The rows below were produced by running the current pipeline (`app/pipeline.py`) against real files in [test_images_data/](test_images_data/) — not hand-written. No sample CSV file is committed to the repo; see [Testing](#testing) for how to reproduce this.

| filename | status_key | tracking_number | weight_kg | dims (L×W×H cm) | notes |
|---|---|---|---|---|---|
| `90146983102_jpg...` | CLEAN_READ | 90146983102 | 0.73 | 28.2 × 17.7 × 17.3 | *(empty)* |
| `ms2_jpeg...` | PARTIAL_DATA | 77882419215 | *(empty)* | *(empty)* | Missing: weight, dimensions. |
| `Multiple shipment packets in single sorter images...` | MULTIPLE_PARCELS | 31049520637826 | 0.72 | 59.1 × 38.0 × 16.8 | Detected 2 candidate parcels in frame. |
| `One more Ekart Format...` | LABEL_OBSTRUCTED | *(garbled OCR text)* | 70.0 | 24.6 × 48.6 × 12.5 | Parcel outline is irregular (solidity=0.73), consistent with a hand or object partly covering it. |
| `No Shipment ccaptured...` | NO_PARCEL | 19108610815135 | *(empty)* | *(empty)* | No parcel-sized object detected on the belt. |

The `LABEL_OBSTRUCTED` and `NO_PARCEL` rows above still carry OCR-extracted text even though the row is flagged — that's real, current behavior, not a display artifact. See [Known Limitations](#known-limitations).

### Where the file lands

There's no server-side CSV file. Clicking **Download CSV** (or `GET /api/jobs/{job_id}/csv`) generates `parcel_scan_<job_id>.csv` on demand from the SQLite results and streams it straight to the browser's normal download location.

## Status Flags & Edge Cases

| Scenario | Flag Emitted | System Behavior |
|---|---|---|
| Single parcel, fully visible, unobstructed, tracking + weight/dims all read | `CLEAN_READ` (100) | Row carries all extracted fields; `notes` empty |
| Single unobstructed parcel, but tracking, weight, or dimensions missing | `PARTIAL_DATA` (110) | Whatever *was* read is kept; `notes` names exactly which field(s) are missing |
| No parcel-sized contour found on the belt | `NO_PARCEL` (200) | Extraction fields are left as whatever OCR happened to find (may be non-empty — see limitation below); flag takes priority |
| Two or more parcel-sized contours in frame | `MULTIPLE_PARCELS` (201) | Same as above — no single-parcel record is trusted even if OCR read something |
| The one parcel candidate's bounding box touches the frame edge | `PARCEL_PARTIAL` (202) | Parcel judged only partially captured |
| Parcel outline is irregular (solidity below threshold) | `LABEL_OBSTRUCTED` (203) | Consistent with a hand/object partly covering the parcel |
| Parcel visible and unobstructed, but zero fields parsed from OCR | `LABEL_UNREADABLE` (204) | Typically blur, glare, extreme angle, or low contrast |
| Uploaded file is 0 bytes | `CORRUPT_FILE` (901) | Caught at upload time, before any decode attempt |
| File fails to decode as an image (corrupted bytes, non-image content) | `CORRUPT_FILE` (901) | Caught by Pillow's `Image.verify()` in `pipeline.py` |
| File extension isn't `.jpg`, `.jpeg`, or `.png` | `UNSUPPORTED_FILE` (902) | Checked by filename extension, not file content |
| File exceeds 25 MB | `UNSUPPORTED_FILE` (902) | Same status code as a wrong-extension file today — only `notes` distinguishes them |
| Unexpected exception during analysis | `PROCESSING_ERROR` (903) | Caught per-image so one bad file can't take down the batch; full traceback is stored (not exported to CSV) |
| Upload request has zero files selected | HTTP 400, no job created | `"Please select at least one image to upload."` — rejected before any row exists |
| Batch has more than 500 files | HTTP 400, no job created | `"Too many files in one batch (...). Max is 500..."` |

## Assumptions & Design Trade-offs

- **Capture format.** Images are assumed to be sorter-camera captures with a burned-in, bright-on-dark HUD-style text overlay in a corner of the frame — not photos of a printed label held up to the camera. The whole OCR/parsing strategy in `ocr.py` is built around that.
- **Language/vocabulary.** The overlay text is assumed to be in English with a known keyword vocabulary (`AWB`, `Barcode`, `Weight`, `Dimension`, `Length`/`Width`/`Breadth`/`Height`, `Location`, `Time`). A courier overlay using different field labels, or a non-Latin script, will under-extract.
- **One photo = one parcel = one CSV row.** The pipeline never merges two photos of the same parcel, and there's no mechanism for an operator to mark two consecutive images as the same shipment.
- **Plausibility ranges are enforced, not just parsed.** Weight is only accepted between 0.005–100 kg and each dimension between 1–200 cm (`ocr.py`); values outside that range are dropped as unreliable rather than reported, on the assumption that a wildly implausible OCR read is worse than an honest gap.
- **mm-vs-cm inference is heuristic.** Some overlay formats write millimeter dimensions as zero-padded integers with no unit token (e.g. `0208 X 0148 X 0163`); the parser infers `mm` from that padding pattern rather than an explicit unit label. This is tuned to the formats observed in `test_images_data/` and may not generalize to an unseen format.
- **Resolution handling.** Images are resized so their long side is 1000–1400 px before OCR, upscaling low-resolution photos up to 2x. Extremely low-resolution or extremely high-resolution originals weren't specifically tuned for.
- **Upload caps are a request-size guard, not a hard architectural limit.** 500 files and 25 MB/file were chosen to keep a single HTTP request finite; a real deployment might tune these differently.

## Known Limitations

- **No measured accuracy.** There is no labeled ground-truth set and no scoring script in this repo. Extraction correctness has only been spot-checked manually against `test_images_data/`, not measured as a number.
- **Detection heuristics are hand-tuned, not learned.** Parcel presence/count and obstruction detection (`vision.py`) are classical contour-area and solidity heuristics tuned by eye against the provided sample images — not a trained detector. A different courier's camera angle, belt color, or lighting could require retuning `MIN_PARCEL_AREA_RATIO`, `SOLIDITY_OBSTRUCTED_THRESHOLD`, or `BACKGROUND_AREA_RATIO`.
- **"Not present" and "unreadable" look the same today.** The system cannot currently distinguish a field that's genuinely absent from the label from one that's present but failed OCR — both produce an empty value and, if that's the only gap, a generic `PARTIAL_DATA` note like "Missing: weight, dimensions."
- **Flagged rows can still carry unreliable extracted text.** Verified against real output: a `LABEL_OBSTRUCTED` or `NO_PARCEL` row can still have a non-empty `tracking_number`/`weight_kg` in the CSV, because field extraction runs independently of the scene check and isn't cleared once a flag wins. `status_key`, not field presence, is the trustworthy signal — downstream consumers need to know this.
- **Two failure modes share one status code.** An oversized file and a wrong-extension file both map to `UNSUPPORTED_FILE` (902); only the free-text `notes` column tells them apart.
- **No automated tests.** Correctness is currently validated by manually running the pipeline against `test_images_data/`, not by a `pytest` suite.
- **Format support is narrow.** Only `.jpg`, `.jpeg`, `.png` are accepted; HEIC, WEBP, BMP, TIFF, and PDF are all rejected as `UNSUPPORTED_FILE`.
- **Regex vocabulary is fixed.** Field parsing depends on recognizing specific keywords (`AWB`, `Weight`, `Dimension`, etc.). An overlay using different labels will likely under-extract (safe) rather than mis-extract (unsafe), but it's still a real coverage gap.
- **SQLite is single-writer.** One shared connection with a global lock serializes all writes — fine at "hundreds of images per batch," not designed for a much larger multi-user deployment.
- **Upload itself is synchronous.** `POST /api/jobs` must finish receiving and writing every file in the batch before returning a job id. Background OCR/CV processing doesn't block the browser, but a very large batch over a slow connection can make the *upload* step itself take a while before the progress screen appears.
- **`storage/db.sqlite3` is committed to git** as an (empty) placeholder. Real batches will grow this file locally; it's worth moving to `.gitignore`-only going forward (see summary below).

## What I'd Improve With More Time

1. **Measure accuracy.** Hand-label a subset of `test_images_data/` with correct tracking/weight/dimensions and write a scoring script, so extraction quality is a tracked number instead of a claim.
2. **Human-in-the-loop review queue.** Let a warehouse operator open any non-`CLEAN_READ` row in the UI, correct or confirm the value, and have that flow into the CSV — right now flagged rows are visible but not actionable in the app.
3. **Separate "not present" from "could not read."** Use OCR line confidence and neighboring-field success as a signal: if the rest of the overlay was read at high confidence and a keyword simply never appeared, that's "not on label"; if confidence near the expected position is low, that's "unreadable."
4. **Per-courier overlay profiles.** Detect which layout/vocabulary a frame is using (multiple formats are already visible across `test_images_data/`, e.g. the "Ekart" variants) and apply a matching parser instead of one global regex vocabulary.
5. **Automated regression tests.** Turn `test_images_data/` into `pytest` fixtures asserting expected `status_key` per filename pattern, so a threshold tweak in `vision.py` can't silently break a previously-working case.
6. **Real-time progress instead of polling.** Replace the 1.5s poll in `app.js` with Server-Sent Events so large-batch progress feels immediate rather than laggy.

## Testing

There is no automated test suite in this repo today. [test_images_data/](test_images_data/) (262 JPG/PNG images) is the closest thing to a fixture set — filenames encode the edge case each one is meant to exercise, e.g. `No Shipment ccaptured...`, `Multiple shipment packets in single sorter images...`, `Image with worker hand on it...`, `LBH concetenated with dimensions...`, `Shipment showing LBH values as 00...`.

To exercise the pipeline manually:

- **Through the UI** — start the app (see [Getting Started](#getting-started-run-locally-in-under-15-minutes)) and upload some or all of `test_images_data/`.
- **Headlessly**, without the web server:
  ```python
  from pathlib import Path
  from app import pipeline

  for p in sorted(Path("test_images_data").glob("*")):
      result = pipeline.process_image(p, p.name)
      print(p.name, "->", result["status_key"])
  ```

No sample CSV output file is committed to the repo. The sample rows in [Output Format](#output-format) were generated by running the pipeline above against real files for this README; reproduce them the same way, or download a CSV from the running app after processing a batch.

## Project Structure

```
Warehouse-Parcel-Scanner/
├── app/
│   ├── main.py          # FastAPI app: routes, upload validation, static file serving
│   ├── pipeline.py      # Per-image decision tree: file checks -> OCR -> scene analysis -> status
│   ├── ocr.py            # Tesseract OCR wrapper + regex field parsing (tracking/weight/dims/etc.)
│   ├── vision.py         # OpenCV contour/solidity heuristics for parcel presence/count/obstruction
│   ├── taxonomy.py       # Single source of truth for every status flag (code/key/label/severity)
│   ├── jobs.py           # ThreadPoolExecutor background processing + resume-on-restart
│   ├── database.py       # SQLite persistence for jobs and per-image results
│   └── csv_export.py     # Builds the downloadable CSV from a job's stored results
├── static/
│   ├── index.html        # Single-page UI: upload, progress, results, taxonomy modal
│   ├── app.js             # Upload/poll/render logic, talks to the /api/* endpoints
│   └── style.css          # Styling
├── storage/
│   ├── db.sqlite3         # SQLite database (created/updated at runtime)
│   └── uploads/            # Saved copies of uploaded images, one subfolder per job (gitignored)
├── test_images_data/      # 262 sample parcel photos covering the documented edge cases
├── requirements.txt        # Python dependencies
├── start_app.bat            # Windows one-click launcher (venv + deps + Tesseract check + run)
├── Dockerfile                # Container build for cloud deploy
├── render-build.sh           # Render.com build script (apt tesseract-ocr + pip install)
└── README.md
```

## Demo

<!-- DEMO: paste video link or deployed URL below -->
📽️ **Demo video:** *[link will be added here]*

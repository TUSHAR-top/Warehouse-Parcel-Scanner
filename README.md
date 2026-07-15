# Warehouse Parcel Scanner

Warehouse Parcel Scanner is a local web application for uploading parcel images, processing them with OCR, and reviewing extracted parcel data. It is designed to run on a Windows machine with a simple one-click launcher for non-technical users.

## What the application does

The app allows a user to:
- upload one or more parcel images through the browser
- process the images with OCR and rule-based parsing
- view job status and extracted results
- download the final results as CSV

## Tech stack

The project uses:
- Python 3.10+ for the backend
- FastAPI for the web API
- Uvicorn as the ASGI server
- HTML, CSS, and JavaScript for the frontend UI
- OpenCV and NumPy for image processing
- Tesseract OCR for text extraction
- Pillow for image handling
- Pytesseract as the Python interface to Tesseract

## Prerequisites

Before running the app, make sure the following are available:
- Python 3.10 or newer
- Windows operating system
- Internet access for the first-time dependency installation

## One-click startup on Windows

Double-click the file named start_app.bat in the project folder to launch the app locally.

The launcher will:
- create a local virtual environment in .venv
- install the Python dependencies from requirements.txt
- check for Tesseract OCR and attempt to install it automatically with winget when needed
- start the FastAPI app on http://127.0.0.1:8000/

If Tesseract cannot be installed automatically, install it manually from the official website and run the batch file again.

## Manual startup (for developers)

If you want to run the app manually, use the following commands from the project root:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Then open the browser and visit:
- http://127.0.0.1:8000/

## Tesseract OCR setup

Tesseract is required for OCR processing. The launcher will try to install it automatically, but if it is not available you can install it manually:
- download and install Tesseract from https://github.com/UB-Mannheim/tesseract/wiki
- or install it using winget:

```powershell
winget install --id Tesseract-OCR.TesseractOCR --accept-source-agreements --accept-package-agreements
```

## Project structure

Key folders and files:
- app/ – backend application code
- static/ – frontend files
- storage/ – uploaded images and database storage
- requirements.txt – Python dependencies
- start_app.bat – Windows one-click startup script

## Notes

- The app stores uploaded files and job information locally in the storage folder.
- For first-time use, the launcher may take a few minutes to install dependencies.
- To stop the app, press Ctrl+C in the terminal window.

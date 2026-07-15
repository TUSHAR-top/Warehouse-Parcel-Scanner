@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "ROOT=%~dp0"
cd /d "%ROOT%"

set "VENV_DIR=%ROOT%.venv"
set "PYTHON_EXE="

if exist "%VENV_DIR%\Scripts\python.exe" (
    set "PYTHON_EXE=%VENV_DIR%\Scripts\python.exe"
) else (
    where py >nul 2>nul
    if not errorlevel 1 (
        py -3 -m venv "%VENV_DIR%" >nul 2>&1
        if not errorlevel 1 (
            set "PYTHON_EXE=%VENV_DIR%\Scripts\python.exe"
        )
    )
)

if not defined PYTHON_EXE (
    where python >nul 2>nul
    if not errorlevel 1 (
        python -m venv "%VENV_DIR%" >nul 2>&1
        if not errorlevel 1 (
            set "PYTHON_EXE=%VENV_DIR%\Scripts\python.exe"
        )
    )
)

if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo Python 3 was not found or could not create a virtual environment.
    echo Please install Python 3.10+ from https://www.python.org/downloads/windows/ and try again.
    pause
    exit /b 1
)

echo Creating or updating Python environment...
"%PYTHON_EXE%" -m pip install --upgrade pip >nul
"%PYTHON_EXE%" -m pip install -r "%ROOT%requirements.txt"
if errorlevel 1 (
    echo.
    echo Dependency installation failed.
    pause
    exit /b 1
)

call :ensure_tesseract
if errorlevel 1 (
    exit /b %errorlevel%
)

echo.
echo Starting Warehouse Parcel Scanner...
echo Open http://127.0.0.1:8000/ in your browser.
"%PYTHON_EXE%" -m uvicorn app.main:app --host 127.0.0.1 --port 8000

exit /b %errorlevel%

:ensure_tesseract
where tesseract >nul 2>nul
if not errorlevel 1 exit /b 0

if exist "C:\Program Files\Tesseract-OCR\tesseract.exe" exit /b 0
if exist "C:\Program Files (x86)\Tesseract-OCR\tesseract.exe" exit /b 0

echo.
echo Tesseract OCR was not found on this computer.
echo Trying to install it with winget...
where winget >nul 2>nul
if not errorlevel 1 (
    winget install --id Tesseract-OCR.TesseractOCR --accept-source-agreements --accept-package-agreements
    if not errorlevel 1 (
        echo Tesseract OCR installation completed.
        exit /b 0
    )
)

echo.
echo Tesseract OCR could not be installed automatically.
echo Please install it manually from: https://github.com/UB-Mannheim/tesseract/wiki
pause
exit /b 1

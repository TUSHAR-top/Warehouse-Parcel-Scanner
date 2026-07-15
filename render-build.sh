#!/usr/bin/env bash
set -o errexit

# Update package lists and install Tesseract OCR
apt-get update
apt-get install -y tesseract-ocr

# Install Python requirements
pip install -r requirements.txt
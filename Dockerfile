# Use an official lightweight Python image
FROM python:3.12-slim

# Install system dependencies
# tesseract-ocr: The actual OCR engine
# libtesseract-dev: Required development files for the engine
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    libtesseract-dev \
    && rm -rf /var/lib/apt/lists/*

# Set the working directory inside the container
WORKDIR /app

# Copy your requirements file first to take advantage of Docker caching
COPY requirements.txt .

# Install your Python packages
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of your application code
COPY . .

# Expose the port (Render will override this with the $PORT environment variable)
EXPOSE 8000

# Use a shell command to ensure the app uses the PORT assigned by Render
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port $PORT"]
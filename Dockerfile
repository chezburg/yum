FROM python:3.12-slim-bookworm

# System dependencies:
#  - ffmpeg: audio extraction & frame sampling
#  - tesseract-ocr: fallback OCR engine
#  - libgl1/libglib2.0-0: OpenCV runtime requirements
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    tesseract-ocr \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Run as a non-root user (principle of least privilege)
RUN useradd --create-home --uid 1000 appuser

WORKDIR /app

COPY requirements.txt requirements-local.txt ./

# Base dependencies are always installed.
# Set build arg INSTALL_LOCAL_MODELS=true to bake in faster-whisper/PaddleOCR.
ARG INSTALL_LOCAL_MODELS=false
RUN pip install --no-cache-dir -r requirements.txt && \
    if [ "$INSTALL_LOCAL_MODELS" = "true" ]; then \
        pip install --no-cache-dir -r requirements-local.txt; \
    fi

COPY src/ ./src/

# Writable dirs for data, config, and exports
RUN mkdir -p /data /config /export && \
    chown -R appuser:appuser /app /data /config /export

USER appuser

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM python:3.12-slim

# OpenCV runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 libgomp1 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY dashboard ./dashboard
COPY alembic.ini ./
COPY migrations ./migrations
COPY scripts ./scripts
COPY demo ./demo
COPY tests ./tests

# Models are NOT baked in (large binaries). Mount ./models with the YOLO
# PPE + phone weights and the face models, or run:
#   python scripts/download_phone_model.py
# Face models: see README (InsightFace buffalo_l or YuNet/SFace onnx).
RUN mkdir -p models evidence dataset

EXPOSE 8000

# Alembic migrations + camera seed sync run automatically at startup.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

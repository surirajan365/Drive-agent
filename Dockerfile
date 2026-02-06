# ── AI Drive Agent — Production Dockerfile ────────────────────────
FROM python:3.13-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc && rm -rf /var/lib/apt/lists/*

# Python deps
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code
COPY backend/ ./backend/
COPY frontend/ ./frontend/
COPY run.py ./run.py

# Port
ENV PORT=8000
EXPOSE 8000

CMD ["python", "run.py"]

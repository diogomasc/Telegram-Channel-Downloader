# ── Stage 1: builder ────────────────────────────────────────────────────────
FROM python:3.9-slim AS builder

WORKDIR /build

# Install build deps only in this stage
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Stage 2: runtime ─────────────────────────────────────────────────────────
FROM python:3.9-slim AS runtime

LABEL maintainer="tgdl" \
      description="Telegram Channel Downloader"

# Only runtime SSL lib needed
RUN apt-get update && apt-get install -y --no-install-recommends \
    libssl3 \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /install /usr/local

WORKDIR /app

# Copy the script
COPY telegram_downloader.py .

# Persist config & downloads in a named volume
VOLUME ["/app/config", "/app/download"]

# Set env so config/session files land in the mounted volume
ENV TGDL_CONFIG_DIR=/app/config

# Non-root user for security
RUN useradd -m -u 1000 tgdl && chown -R tgdl:tgdl /app
USER tgdl

ENTRYPOINT ["python", "telegram_downloader.py"]

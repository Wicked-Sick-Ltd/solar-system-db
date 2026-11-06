# Build host image: runs the nightly full build, the publish step and the crawler.
FROM python:3.15-slim
# zstandard has no cp315 wheel. gcc and libffi-dev compile it; they are
# purged after pip so the runtime image keeps the zstd CLI and ca-certificates.
RUN apt-get update && apt-get install -y --no-install-recommends \
        zstd \
        ca-certificates \
        gcc \
        libc6-dev \
        libffi-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml README.md ./
COPY solar_db ./solar_db
COPY scripts ./scripts
COPY schema ./schema
COPY seed ./seed
COPY tests/fixtures ./tests/fixtures
RUN pip install --no-cache-dir -e ".[publish]" \
    && apt-get purge -y --auto-remove gcc libc6-dev libffi-dev \
    && rm -rf /var/lib/apt/lists/*
ENV PYTHONUNBUFFERED=1 SSDB_BUILD_PATH=/build/solar_system.sqlite SSDB_NO_PUBLISH=1
VOLUME ["/build", "/data"]
# Nightly: build → verify → publish. The crawler runs as a separate long-lived container (see docker-compose, profile "build").
# After a verified publish, install the fresh catalogue on /data too so the
# always-on crawler re-syncs its queue against it (it watches the file mtime).
CMD ["bash", "-lc", "python scripts/build_full.py --fresh --online --enrichment-store /data/enrichment.sqlite && python scripts/verify.py && python scripts/publish_artifact.py --db /build/solar_system.sqlite --enrichment-store /data/enrichment.sqlite --bucket \"$S3_BUCKET\" --endpoint \"$S3_ENDPOINT\" --public-base \"$S3_PUBLIC_BASE\" --summary-out /data/last-publish.json && cp /build/solar_system.sqlite /data/.solar_system.sqlite.new && mv -f /data/.solar_system.sqlite.new /data/solar_system.sqlite"]

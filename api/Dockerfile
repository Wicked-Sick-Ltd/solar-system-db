FROM python:3.12-slim

WORKDIR /app

# Install root package (solar_db) + api package
COPY pyproject.toml README.md ./
COPY solar_db ./solar_db
COPY api ./api

RUN pip install --no-cache-dir -e . && \
    pip install --no-cache-dir -e ./api

RUN addgroup --system app && \
    adduser --system --ingroup app --no-create-home app && \
    mkdir -p /var/lib/publicuniverse && \
    chown app:app /var/lib/publicuniverse

ENV PYTHONUNBUFFERED=1 \
    SOLAR_DB_PATH=/data/solar_system.sqlite \
    API_PORT=8003 \
    PU_API_KEYS_PATH=/var/lib/publicuniverse/api_keys.sqlite

EXPOSE 8003

USER app

CMD ["python", "api/main.py"]

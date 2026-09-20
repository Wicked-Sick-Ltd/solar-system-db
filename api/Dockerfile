FROM python:3.12-slim

WORKDIR /app

# Install root package (solar_db) + api package
COPY pyproject.toml README.md ./
COPY solar_db ./solar_db
COPY api ./api

RUN pip install --no-cache-dir -e . && \
    pip install --no-cache-dir -e ./api

RUN addgroup --system app && \
    adduser --system --ingroup app --no-create-home app

ENV PYTHONUNBUFFERED=1 \
    SOLAR_DB_PATH=/data/solar_system.sqlite \
    API_PORT=8003

EXPOSE 8003

USER app

CMD ["python", "api/main.py"]

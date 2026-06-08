# FastAPI backend that powers the Angular UI module.
# Reads from the same Postgres data_mart strata extracts from,
# and proxies Superset's dashboard list for embedding.
FROM python:3.11-slim

WORKDIR /app

# Build deps for psycopg2 (binary wheel ships pre-compiled, but libpq-dev
# is occasionally needed for source fallback)
RUN apt-get update \
    && apt-get install --no-install-recommends -y libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY strata_api ./strata_api

EXPOSE 8000

CMD ["uvicorn", "strata_api.main:app", "--host", "0.0.0.0", "--port", "8000"]

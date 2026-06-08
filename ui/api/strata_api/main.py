"""FastAPI entrypoint."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .settings import settings
from .routers import payments, tables

app = FastAPI(
    title="strata UI backend",
    version="0.1.0",
    description=(
        "Backs the Angular UI module: lists data_mart tables, fetches row "
        "pages, and accepts new fact_pay_payment rows. The dashboards UX "
        "is an embedded Superset iframe served same-origin through nginx, "
        "so no dashboard API surface is needed here."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(tables.router, prefix="/api/tables", tags=["tables"])
app.include_router(payments.router, prefix="/api/payments", tags=["payments"])


@app.get("/api/health", tags=["meta"])
def health() -> dict[str, str]:
    """Liveness probe. Returns 200 if the process is up."""
    return {"status": "ok", "schema": settings.pg_schema}

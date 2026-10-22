from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from time import perf_counter

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from starlette.middleware.trustedhost import TrustedHostMiddleware

import models  # noqa: F401 - registers mapper metadata
from config import (
    CORS_ORIGINS,
    ENABLE_DOCS,
    ENV,
    FRONTEND_DIR,
    IS_PRODUCTION,
    TRUSTED_HOSTS,
)
from database import Base, engine
from migrate import run_migrations, seed_plans
from routes import admin, auth, files, plans

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("storix")


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    run_migrations()
    seed_plans()
    logger.info("database initialized (%s)", ENV)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


docs_enabled = ENABLE_DOCS
app = FastAPI(
    title="StoriX",
    version="2.0.0",
    lifespan=lifespan,
    docs_url="/docs" if docs_enabled else None,
    redoc_url="/redoc" if docs_enabled else None,
    openapi_url="/openapi.json" if docs_enabled else None,
)

if CORS_ORIGINS:
    allow_credentials = "*" not in CORS_ORIGINS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(CORS_ORIGINS),
        allow_credentials=allow_credentials,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )

if TRUSTED_HOSTS:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(TRUSTED_HOSTS))


CSP = (
    "default-src 'self'; base-uri 'self'; object-src 'none'; "
    "frame-ancestors 'none'; "
    "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.tailwindcss.com; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; "
    "img-src 'self' data: blob:; media-src 'self' blob:; "
    "frame-src 'self' blob:; connect-src 'self'"
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    request_id = request.headers.get("x-request-id", "")[:64] or uuid.uuid4().hex
    started = perf_counter()
    response = await call_next(request)

    defaults = {
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "X-Frame-Options": "DENY",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
        "Content-Security-Policy": CSP,
        "X-Request-ID": request_id,
    }
    if IS_PRODUCTION:
        defaults["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )
    for name, value in defaults.items():
        if name not in response.headers:
            response.headers[name] = value

    elapsed_ms = (perf_counter() - started) * 1000
    if elapsed_ms > 2000:
        logger.warning(
            "slow request %s %s -> %s in %.0fms (request_id=%s)",
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
            request_id,
        )
    return response


@app.exception_handler(Exception)
async def unhandled_exception(request: Request, exc: Exception):
    request_id = request.headers.get("x-request-id", "")
    logger.exception(
        "unhandled error on %s %s (request_id=%s): %s",
        request.method,
        request.url.path,
        request_id,
        exc,
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "request_id": request_id},
    )


@app.get("/health", tags=["ops"])
def health():
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    return {"status": "ok", "environment": ENV}


app.include_router(router=auth.router)
app.include_router(router=files.router)
app.include_router(router=plans.router)
app.include_router(router=admin.router)

app.mount(
    "/",
    StaticFiles(directory=str(FRONTEND_DIR), html=True),
    name="frontend",
)

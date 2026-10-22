"""Central configuration for StoriX.

All deployment-specific values come from the environment. Development defaults
are deliberately safe enough to run locally, but production refuses to start
without an explicit secret.
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR / "frontend"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc


ENV = os.getenv("STORIX_ENV", "development").strip().lower()
IS_PRODUCTION = ENV == "production"


def _dev_secret() -> str:
    """Keep one development secret across reloads instead of rotating it.

    The file is local, git-ignored, and only used when STORIX_SECRET_KEY is
    absent outside production. Production refuses to start in that case.
    """
    secret_file = BASE_DIR / ".storix-dev-secret"
    if secret_file.exists():
        value = secret_file.read_text(encoding="utf-8").strip()
        if value:
            return value
    value = secrets.token_urlsafe(64)
    secret_file.write_text(value + "\n", encoding="utf-8")
    try:
        secret_file.chmod(0o600)
    except OSError:
        pass
    return value


# --- Secrets -----------------------------------------------------------------
_secret = os.getenv("STORIX_SECRET_KEY", "").strip()
if not _secret:
    if IS_PRODUCTION:
        raise RuntimeError(
            "STORIX_SECRET_KEY is required in production. "
            "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(64))\""
        )
    _secret = _dev_secret()
    DEV_SECRET_GENERATED = True
else:
    DEV_SECRET_GENERATED = False

SECRET_KEY = _secret
# Share links use a derived key so a leaked auth token can never be replayed as
# a share token, and vice versa.
SHARE_SECRET_KEY = os.getenv("STORIX_SHARE_SECRET_KEY", "").strip() or f"{_secret}:share"
ALGORITHM = "HS256"
TOKEN_ISSUER = "storix"
ACCESS_TOKEN_AUDIENCE = "storix-api"
SHARE_TOKEN_AUDIENCE = "storix-share"

# --- Tokens ------------------------------------------------------------------
ACCESS_TOKEN_MINUTES = _env_int("STORIX_ACCESS_TOKEN_MINUTES", 30)
REFRESH_TOKEN_DAYS = _env_int("STORIX_REFRESH_TOKEN_DAYS", 14)
# A rotated refresh token may be reused briefly so parallel tabs do not log
# each other out during a race.
REFRESH_ROTATION_GRACE_SECONDS = _env_int("STORIX_REFRESH_ROTATION_GRACE_SECONDS", 60)

# --- Persistence -------------------------------------------------------------
DATABASE_URL = os.getenv(
    "STORIX_DATABASE_URL", f"sqlite:///{(BASE_DIR / 'hosting.db').as_posix()}"
)
STORAGE_ROOT = Path(
    os.getenv("STORIX_STORAGE_ROOT", str(BASE_DIR / "storage"))
).expanduser().resolve()
STORAGE_ROOT.mkdir(parents=True, exist_ok=True)

# --- HTTP --------------------------------------------------------------------
BASE_URL = os.getenv("STORIX_BASE_URL", "").strip().rstrip("/")
CORS_ORIGINS = tuple(
    origin.strip()
    for origin in os.getenv("STORIX_CORS_ORIGINS", "").split(",")
    if origin.strip()
)
TRUSTED_HOSTS = tuple(
    host.strip()
    for host in os.getenv("STORIX_TRUSTED_HOSTS", "").split(",")
    if host.strip()
)
# Public API docs are useful locally and noisy/risky in production.
ENABLE_DOCS = _env_bool("STORIX_ENABLE_DOCS", not IS_PRODUCTION)

# --- Product policy ----------------------------------------------------------
# Paid plan changes are admin-only in production unless explicitly enabled.
ALLOW_SELF_PLAN_CHANGE = _env_bool(
    "STORIX_ALLOW_SELF_PLAN_CHANGE", not IS_PRODUCTION
)

# --- Limits ------------------------------------------------------------------
MAX_FILENAME_LENGTH = _env_int("STORIX_MAX_FILENAME_LENGTH", 120)
MAX_UPLOAD_FILES = _env_int("STORIX_MAX_UPLOAD_FILES", 50)
SHARE_MAX_MINUTES = _env_int("STORIX_SHARE_MAX_MINUTES", 7 * 24 * 60)
ZIP_MAX_BYTES = _env_int("STORIX_ZIP_MAX_BYTES", 2 * 1024**3)
ZIP_MAX_FILES = _env_int("STORIX_ZIP_MAX_FILES", 1000)
LOGIN_RATE_LIMIT = _env_int("STORIX_LOGIN_RATE_LIMIT", 10)
LOGIN_RATE_WINDOW_SECONDS = _env_int("STORIX_LOGIN_RATE_WINDOW_SECONDS", 300)
REGISTER_RATE_LIMIT = _env_int("STORIX_REGISTER_RATE_LIMIT", 10)
REGISTER_RATE_WINDOW_SECONDS = _env_int("STORIX_REGISTER_RATE_WINDOW_SECONDS", 3600)
UPLOAD_RATE_LIMIT = _env_int("STORIX_UPLOAD_RATE_LIMIT", 240)
UPLOAD_RATE_WINDOW_SECONDS = _env_int("STORIX_UPLOAD_RATE_WINDOW_SECONDS", 60)
SHARE_RATE_LIMIT = _env_int("STORIX_SHARE_RATE_LIMIT", 60)
SHARE_RATE_WINDOW_SECONDS = _env_int("STORIX_SHARE_RATE_WINDOW_SECONDS", 60)

# --- Background housekeeping -------------------------------------------------
TEMP_DIR = STORAGE_ROOT / ".tmp"
TEMP_DIR.mkdir(parents=True, exist_ok=True)

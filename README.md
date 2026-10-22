# StoriX

A self-hosted file workspace built with **FastAPI**, **SQLAlchemy**, and a
vanilla JavaScript frontend. StoriX provides accounts, nested folders, quota
tracking, file versioning, trash/restore, expiring share links, and an admin
console.

This branch is the hardened version produced by a full security and
correctness audit. The findings, severity ratings, and verification matrix are
in [AUDIT.md](AUDIT.md).

## Features

- **Accounts and sessions** — bcrypt password hashing, short-lived access
  tokens, persisted refresh tokens rotated on use, and logout revocation.
- **Files and folders** — multi-file upload with progress, nested folders,
  rename, move, star, search, sorting, and grid/list views. Drag file rows or
  cards onto a folder to move them in, or onto “My Files” to move them out.
- **Quota enforcement** — an atomic database reservation prevents parallel
  uploads from overspending a plan. `used_storage` counts every byte on disk,
  including superseded versions and trashed files.
- **Versioning** — re-uploading the same filename into the same folder creates
  a new version under a stable `version_group`; older versions can be restored.
- **Trash** — group-level soft delete, restore, permanent delete, and empty
  trash. Permanently deleting a file frees its quota.
- **Sharing** — plan-gated public files and stateless, signed share links with
  expiry. Links are bound to the file's version group, not its mutable name or
  folder, and are re-validated on every download.
- **Safe previews** — images, video, audio, PDF, and plain text render inline;
  HTML, SVG, XML, scripts, and unknown binaries are forced to download with
  `X-Content-Type-Options: nosniff`.
- **Admin console** — user list, plan assignment, storage reconciliation, and
  account deletion. Paid plan changes are admin-only in production.
- **Operations** — environment-driven configuration, security headers, login
  and upload rate limits, `/health`, and a non-root Docker image.

## Tech stack

| Area | Technology |
|---|---|
| Backend | FastAPI, SQLAlchemy 2, Pydantic 2 |
| Frontend | Vanilla JS, Tailwind CDN, custom CSS design system |
| Database | SQLite (WAL mode) |
| Auth | OAuth2 password flow, JWT access/refresh tokens, bcrypt |
| Storage | Local filesystem; UUID disk names under `storage/users/<id>/` |
| Tests | pytest + FastAPI TestClient |

## Architecture

```text
Browser (HTML/CSS/JS served by FastAPI)
        │  JSON + multipart
        ▼
FastAPI routes ── auth / files / plans / admin
        │
        ├── SQLite metadata (users, folders, file rows, plans, refresh tokens)
        └── Local storage root (UUID-named blobs)
```

- Display filenames are sanitized and stored in the database. They never
  become filesystem paths.
- Disk paths are generated as `users/<user_id>/<uuid><extension>` and resolved
  defensively against the storage root on read and delete.
- A logical file is identified by `version_group`. Every version keeps its own
  row and blob; exactly one row per group is `is_latest`.
- Share links are JWTs signed with a share-specific key and audience. They are
  not bearer session tokens, and they cannot be used against the API.

## Quick start

Requirements: Python 3.12+.

```bash
git clone https://github.com/shin0biX/StoriX.git
cd StoriX

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

uvicorn main:app --reload
```

Open <http://127.0.0.1:8000>. On first start StoriX creates `hosting.db`,
`storage/`, the schema, and the Free/Pro/Premium plans. In development a local
secret is generated once in `.storix-dev-secret` (git-ignored); production
refuses to start without `STORIX_SECRET_KEY`.

### Create an admin

```bash
python make_admin.py <username>
```

Then sign in and open **Admin** in the sidebar.

### Run the tests

```bash
pip install -r requirements-dev.txt
pytest -q
```

The suite uses an isolated temporary database and storage root, so it never
touches `hosting.db` or `storage/`.

## Configuration

All settings are environment variables. `.env.example` documents the full set.
Docker Compose reads `.env` automatically. For a local shell:

```bash
set -a
source .env
set +a
uvicorn main:app
```

| Variable | Default | Notes |
|---|---|---|
| `STORIX_ENV` | `development` | Set to `production` to require a secret, disable docs, and make paid plans admin-only. |
| `STORIX_SECRET_KEY` | dev secret file | **Required in production.** Rotating it invalidates access tokens. |
| `STORIX_SHARE_SECRET_KEY` | derived | Optional dedicated share-link key. |
| `STORIX_DATABASE_URL` | `sqlite:///<project>/hosting.db` | SQLAlchemy URL. |
| `STORIX_STORAGE_ROOT` | `./storage` | Absolute or relative blob directory. |
| `STORIX_BASE_URL` | request origin | Public origin used for share URLs. |
| `STORIX_TRUSTED_HOSTS` | empty | Comma-separated host allowlist. |
| `STORIX_CORS_ORIGINS` | empty | Comma-separated origins; leave empty for same-origin deployments. |
| `STORIX_ALLOW_SELF_PLAN_CHANGE` | `true` in dev | `false` in production means admins assign paid plans. |
| `STORIX_ACCESS_TOKEN_MINUTES` | `30` | Access-token lifetime. |
| `STORIX_REFRESH_TOKEN_DAYS` | `14` | Refresh-token lifetime. |
| `STORIX_MAX_UPLOAD_FILES` | `50` | Files per multipart request. |
| `STORIX_SHARE_MAX_MINUTES` | `10080` | Maximum share-link lifetime (7 days). |
| `STORIX_ZIP_MAX_BYTES` / `STORIX_ZIP_MAX_FILES` | 2 GiB / 1000 | Bulk ZIP guardrails. |

Rate limits are also configurable; see `.env.example`.

## Docker

```bash
cp .env.example .env
# edit .env: set STORIX_SECRET_KEY and a public STORIX_BASE_URL
docker compose up --build
```

The image runs as a non-root user, exposes a healthcheck, and persists both the
SQLite database and blobs in the `storix-data` volume (`/data`). Use only one
application instance with SQLite; move to PostgreSQL and shared object storage
before scaling horizontally.

## API overview

| Area | Endpoint | Description |
|---|---|---|
| Auth | `POST /auth/` | Register a user (always starts on Free). |
| Auth | `POST /auth/token` | OAuth2 password login; returns access + refresh tokens. |
| Auth | `POST /auth/refresh` | Rotate a refresh token and issue a new session. |
| Auth | `POST /auth/logout` | Revoke a refresh token. |
| Auth | `GET /auth/me` | Current user, plan, and quota. |
| Files | `POST /files/upload/` | Upload one or more files (`files`, optional `folder_id`). |
| Files | `GET /files/get-files` | List active current versions. |
| Files | `PUT /files/{id}/rename` · `/move` · `/star` · `/visibility` | Group-wide file operations. |
| Files | `GET /files/versions/{id}` · `POST /files/versions/{id}/restore/{version_id}` | Version history and restore. |
| Files | `GET /files/download/{id}` · `/preview/{id}` | Authorized download / safe inline preview. |
| Files | `DELETE /files/file-delete/{id}` · `GET /files/trash` · `POST /files/restore/{id}` | Trash lifecycle. |
| Files | `DELETE /files/permanent-delete/{id}` · `POST /files/trash/empty` | Free quota permanently. |
| Files | `GET/POST /files/folders` · `PUT /files/folders/{id}/rename` · `/move` · `DELETE /files/folders/{id}` | Folder management. |
| Files | `GET /files/download-zip?ids=1,2,3` | ZIP selected files. |
| Files | `POST /files/share/{id}?expires_minutes=30` | Create an expiring share link. |
| Files | `GET /files/shared/{token}` | Redeem a share link (anonymous). |
| Plans | `GET /plans/` · `PUT /plans/change-plan` | List plans / change plan when allowed. |
| Admin | `GET /admin/users` · `PUT /admin/users/{id}/plan` · `DELETE /admin/users/{id}` | Admin console. |
| Admin | `POST /admin/reconcile-storage` | Recalculate all quota counters from metadata. |
| Ops | `GET /health` | Database liveness check. |

Interactive docs are available at `/docs` in development, or when
`STORIX_ENABLE_DOCS=true`. They are disabled in production by default.

## Storage and quota semantics

- Every upload reserves quota before metadata is written. If the reservation
  fails, the temporary blob is removed and the upload is rejected.
- Trashed files and superseded versions still occupy disk and remain counted
  until permanent deletion.
- Deleting a folder moves its active files (all versions) to trash and detaches
  every affected row from the folder, so restore always has a valid root
  destination.
- Renaming or moving a file applies to the whole version group, so restoring an
  older version cannot resurrect a stale name or folder.
- Permanent deletion of a version group removes every blob and frees the total
  size of all of its rows.

## Project structure

```text
StoriX/
├── main.py              # App, middleware, lifespan, static mount
├── config.py            # Environment configuration
├── database.py          # Engine, SQLite pragmas, session factory
├── models.py            # User, Plan, Folder, FileTable, RefreshToken
├── migrate.py           # Idempotent schema repair, backfills, plan seeds
├── storage.py           # Filename sanitization and safe path resolution
├── ratelimit.py         # Dependency-free sliding-window limiter
├── make_admin.py        # Promote an existing account to admin
├── routes/
│   ├── auth.py          # Registration, login, refresh, logout, dependencies
│   ├── files.py         # Upload, folders, versions, trash, sharing, ZIP
│   ├── plans.py         # Plan listing and self-service policy
│   └── admin.py         # Admin user management and reconciliation
├── frontend/
│   ├── index.html       # Landing page
│   ├── auth.html        # Sign in / register
│   ├── dashboard.html   # Workspace
│   ├── admin.html       # Admin console
│   ├── shared.html      # Share-link download page
│   ├── css/app.css      # Design system
│   └── js/              # api.js, ui.js, auth.js, dashboard.js, admin.js
├── tests/               # pytest suite (isolated DB + storage)
├── requirements.txt     # Runtime pins
├── requirements-dev.txt # Test pins
├── Dockerfile
├── docker-compose.yml
├── .env.example
├── AUDIT.md
└── LICENSE
```

## Operational notes

- **Single instance only with SQLite.** Rate limits are in-process and the
  default database is a local file. Use Redis for shared limits and PostgreSQL
  for multiple workers before scaling out.
- **Back up both** the SQLite database and `STORIX_STORAGE_ROOT`; metadata and
  blobs must stay consistent.
- **Rotate the old development secret** if you deployed an earlier version.
  Existing tokens become invalid, which is expected.
- **Serve behind HTTPS** in production. `STORIX_ENV=production` enables HSTS;
  configure your reverse proxy for TLS termination.
- **Tailwind and fonts are CDN-loaded.** The app needs outbound access to
  `cdn.tailwindcss.com` and Google Fonts. A fully offline deployment needs a
  local build or vendored assets.
- The frontend stores tokens in `localStorage`. This is a deliberate trade-off
  for a same-origin, framework-free app; if you add a strict CSP or third-party
  scripts, revisit that choice and consider an HttpOnly cookie session instead.

## License

MIT — see [LICENSE](LICENSE).

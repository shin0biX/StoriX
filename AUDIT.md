# StoriX — Full Project Audit

**Repo:** `shin0biX/StoriX` @ `83fe247` · **Scope:** backend, frontend, data model, deployment, security
**Method:** full source read + static analysis + isolated end-to-end run (FastAPI `TestClient`, temp copy, throwaway DB)

Line references are against `83fe247`.

---

## Executive summary

The architecture is sound for a portfolio project — clean separation of routes, a coherent data model, and a genuinely nice frontend — but the current build is **not safe to expose publicly**. Three issues are critical and independently sufficient to compromise the whole deployment:

1. **The JWT signing secret is committed to the public repo.** Anyone can mint a token for any user, including admins.
2. **Usernames are used as filesystem paths without validation.** A username like `../frontend` writes uploads into the served static root, giving stored XSS on the app origin.
3. **Registration accepts a client-supplied `plan_id`.** Signing up with `plan_id: 3` grants Premium for free. A separate endpoint also lets any user self-upgrade to any plan.

Beyond security, the **versioning/trash model is structurally broken**: versions are grouped by mutable `(filename, folder_id)` instead of a stable ID, and permanent-deleting the latest version orphans every older version. Storage accounting drifts, folder deletion nulls `folder_id` across all versions, and multi-file uploads bypass the storage quota.

Confirmed by execution, not inference: forged tokens authenticate; duplicate registration returns 500; `../frontend` uploads are served as `text/html`; two 16-byte files succeed against a 20-byte quota; deleted files remain downloadable; timestamps serialize without timezone.

**Verdict:** fix the P0 set before any public deployment; the P1 set before calling versioning/trash "done".

---

## P0 — Critical (compromise / data loss)

### P0-1. Hardcoded JWT secret in a public repository
`routes/auth.py:38` — `SECRET_KEY = 'df6f...e375f7'`

The same key signs access tokens (`routes/auth.py:50-54`), refresh tokens (`:149-159`), and share links (`routes/files.py:563-582`). Because the repo is public, the key is public and also present in git history. `get_current_user` trusts the `id` claim and looks the user up in the DB (`routes/auth.py:89-103`), so a forged token for `id=1` is a full account takeover; if that user is an admin, it is full admin takeover.

**Verified:** a token minted offline with the repo's key returned `200` from `/auth/me` and `/admin/users`.

**Fix:** move to `STORIX_SECRET_KEY` env (fail fast in production), rotate the key (old tokens/links invalid), separate share-link signing key/audience, add `iat`/`jti`/`type` claims. Set a new key in the deployed environment *before* shipping the code.

---

### P0-2. Username path traversal → arbitrary write into the static root
`routes/auth.py:43-48` accepts any `username` string; `routes/files.py:178` builds the storage directory as:

```python
user_folder = os.path.join("storage", user.username)
```

`../frontend` resolves to `frontend/`, so an uploaded file lands in the directory mounted at `/` (`main.py:65`). `sanitize_name` only cleans the *filename*, not the username (`routes/files.py:86-91`).

**Verified:** registered `../frontend`, uploaded `owned.html`, then `GET /owned.html` returned `200 text/html` with the payload. That is stored XSS on the API origin, where the access token lives in `localStorage` — token theft, account takeover, and admin actions in the victim's session.

**Fix:** validate usernames to `^[A-Za-z0-9][A-Za-z0-9_.-]{2,31}$` (reject `.`/`..`), never derive paths from usernames, store under `STORAGE_ROOT/users/{user.id}/`, and assert `Path(resolved).is_relative_to(STORAGE_ROOT)` before every write. Also reject `.`/`..` in `sanitize_name` and cap filename length (currently a 304-char name reaches `open()` and 500s).

---

### P0-3. Plan escalation at registration and via self-service
`routes/auth.py:48` exposes `plan_id` in `CreateUserRequest`; `:109-120` honors it directly. `frontend/js/auth.js:94` sends `plan_id: 1`, which is also fragile — plan IDs are autoincrement and not guaranteed to be Free.

**Verified:** `POST /auth/ {"plan_id": 3}` created a user on Premium ($20/mo) immediately.

Separately, `routes/plans.py:31-56` lets any authenticated user switch to any plan, including paid plans.

**Fix:** remove `plan_id` from registration and always assign Free server-side (look up by name, not id). Gate paid upgrades behind admin or a payment webhook; allow self-service only to Free, or behind an explicit demo flag. Reject downgrades below `used_storage`.

---

### P0-4. HTML/SVG previews are served inline on the app origin
`routes/files.py:457-477` sets `media_type` from `guess_type()` and `Content-Disposition: inline`. Uploading `x.html` or `x.svg` and opening `/files/preview/{id}` executes active content on the API origin. Combined with public files or a share link, this is stored XSS without needing P0-2.

**Fix:** allow inline only for safe types (`image/*` except SVG, `video/*`, `audio/*`, `application/pdf`, `text/plain`). Force `Content-Disposition: attachment` for HTML/SVG/XML/JS and unknown types, add `X-Content-Type-Options: nosniff`, and send `Content-Security-Policy: sandbox; default-src 'none'` on preview responses. A separate download origin is the long-term fix.

---

### P0-5. Multi-file upload bypasses the storage quota
`routes/files.py:195` checks `user.used_storage + file_size`, but `user` comes from the auth session and is not refreshed inside the loop; the increment at `:232-233` goes to a different session/row. Every file in one request sees the same stale total.

**Verified:** plan limit 20 bytes; two 16-byte files in one request both succeeded, leaving `used_storage = 32`.

**Fix:** re-read the user inside the loop, reserve quota atomically with a conditional `UPDATE users SET used_storage = used_storage + :size WHERE id = :id AND used_storage + :size <= :limit`, check `rowcount`, and roll back the reservation on write/commit failure. Also check the total request size before reading files, not per file after spooling.

---

### P0-6. Trashed files remain downloadable and shareable
`routes/files.py:435-454` (download) and `routes/files.py:457-477` (preview) do not filter `deleted_at`. `routes/files.py:585-608` (shared download) does not either.

**Verified:** after `DELETE /files/file-delete/{id}`, `GET /files/download/{id}` still returned the bytes.

**Fix:** require `deleted_at IS NULL` for download/preview/shared, and require the row to be the active latest (or explicitly allow version IDs in an owner-only endpoint). Re-check on every request; a share token must die with the file.

---

### P0-7. Docker deployment loses the database on every recreate
`docker-compose.yml:6-7` persists only `/app/storage`. `database.py:7` uses `sqlite:///./hosting.db` inside the container filesystem, so `docker compose down/up` or an image rebuild wipes users, metadata, and plans while the files survive. The secret is also not injected, so the app runs on the hardcoded key.

**Fix:** move DB to `/app/data/hosting.db`, add a `storix-db` volume, pass `STORIX_SECRET_KEY`/`STORAGE_ROOT`/`DATABASE_URL`, add `.dockerignore`, run as a non-root user, add a healthcheck.

---

## P1 — High (correctness / data integrity)

### P1-1. Versioning is grouped by mutable fields, not identity
`routes/files.py:200-214`, `:527-558`: versions are found by `(user_id, filename, folder_id, is_latest)`.
Consequences:
- Renaming the latest file (`:316-332`) renames only one row; older versions keep the old name and disappear from history.
- Moving the latest file (`:335-343`) moves only one row; history stays in the old folder.
- Deleting a folder (`:387-407`) nulls `folder_id` on **all** rows via SQLAlchemy's relationship default, silently merging version groups across folders. **Verified:** `folder_id` became `NULL` after folder deletion.
- Uploading after restoring an old version reuses a version number, producing duplicate `(version)` rows.
- `rename` and `move` don't promote/keep `is_latest` invariants for the group.

**Fix:** add a stable `version_group` (UUID or first-row id) to `FileTable`; group all version operations by it. Rename/move/delete operate on the whole group. Renumber `version = max(version)+1` within the group. Backfill existing rows by `(user_id, filename, folder_id)` in a one-time migration.

### P1-2. Permanent delete orphans all older versions
`routes/files.py:276-285` deletes one row and frees only that row's bytes. If it is the latest, the previous row keeps `is_latest=False`, so the logical file vanishes from `get_files` while its bytes stay on disk and in `used_storage` forever.

**Verified:** v2 deleted permanently; v1 remained with `is_latest=False`, invisible in the file list and un-freed.

**Fix:** soft-delete the whole group; permanent-delete the whole group (all rows + all disk files), promote the highest remaining version when deleting a single version, and recompute `used_storage` from `SUM(size)` of non-deleted rows as a repair job.

### P1-3. `used_storage` is not a reliable number
It is incremented per version, decremented per row, and never reconciled. Old versions are not deletable through the UI, failed commits can leave downloaded-but-uncommitted bytes, and folder/user deletions take different paths. The sidebar meter and quota checks both trust it.

**Fix:** make it derived (`SUM(size) WHERE deleted_at IS NULL`) or maintain it in one place with atomic updates and a periodic reconcile. Add a `POST /admin/reconcile-storage` for the existing drift.

### P1-4. SQLite foreign keys are off; schema has no useful indexes
`database.py:9` never enables `PRAGMA foreign_keys=ON`, so `folder_id`/`user_id` can dangle. No indexes on `filetable.user_id`, `folder_id`, `deleted_at`, `is_latest`, `version_group`, or `folders.user_id/parent_id`; every listing is a full scan.

**Fix:** enable FK pragma on connect, add `ondelete="SET NULL"`/`CASCADE` where appropriate, add composite indexes, switch SQLite to WAL with a busy timeout.

### P1-5. Migrations swallow every failure and run before table creation
`main.py:34-50` executes `ALTER TABLE ... ADD COLUMN` on one connection and ignores every exception. That makes the migration layer silently non-functional: a typo, a locked DB, or a missing table is indistinguishable from "column already exists", with no log and no exit code. `create_all()` runs *after* migrations (`:52-54`), so on a fresh DB all five ALTERs fail (table absent) and are discarded; it only works because `create_all()` then builds the full schema. There is no schema versioning, no backfill, and no rollback path. (SQLAlchemy/SQLite kept the connection usable after a failed ALTER in my test, but that is driver behavior, not a guarantee.)

**Fix:** run `create_all()` first, then apply idempotent per-column checks via `PRAGMA table_info` (or `ALTER` in its own transaction with a logged failure), and adopt Alembic (already in `requirements.txt` but unused). Backfill `version_group` and storage totals in the same migration.

### P1-6. Timestamps are UTC-naive and serialize without a timezone
`models.py:28,40,46` use `datetime.now(timezone.utc)` into `DateTime` (no timezone). SQLite drops the offset; FastAPI emits `"2026-10-22T09:36:50.851330"`.

**Verified:** the API returned exactly that. JS `new Date(...)` interprets it as local time, so "uploaded 2h ago" is wrong by the user's UTC offset.

**Fix:** custom `UTCDateTime` `TypeDecorator` (store naive UTC, return aware UTC) or store epoch integers. Add `Z`/`+00:00` in responses.

### P1-7. Duplicate registration returns 500
`routes/auth.py:123-124` commits without checking uniqueness or catching `IntegrityError`.

**Verified:** second registration with the same username returned `500 Internal Server Error`.

**Fix:** check case-insensitively (`func.lower`) for username/email, normalize email, catch `IntegrityError` → 409, and roll back. Add a DB-level `COLLATE NOCASE` unique index or normalized columns.

### P1-8. Registration validation is effectively absent
`CreateUserRequest` (`routes/auth.py:43-48`) accepts `"not-an-email"` and a one-character password; no username constraints, no max lengths, no bcrypt 72-byte cap.

**Verified:** both were accepted (`200`).

**Fix:** Pydantic `EmailStr`, password `min_length=8` + explicit 72-byte check, username regex, `full_name` length, strip whitespace. Return structured 422 errors the frontend can display.

### P1-9. "Silent refresh" cannot extend a session
`frontend/js/api.js:15-30` calls `/auth/refresh` with the *access* token; `routes/auth.py:149-159` requires a valid access token. After the 30-minute expiry, the refresh call is 401 and the client logs the user out. Every user is forcibly logged out every 30 minutes, contradicting the README.

**Fix:** issue a short-lived access token + long-lived refresh token (`type=refresh`, 14 days), store both, refresh in a single-flight promise, rotate on use, and retry the failed request including XHR uploads and blob downloads. Or, minimally, raise access-token lifetime to a sane duration and make `/auth/refresh` accept a token within a grace window with a separate `type`.

### P1-10. ZIP download is built entirely in memory
`routes/files.py:480-522` writes every selected file into a `BytesIO`. A Premium user can select 100 GB and OOM the process. It also blocks the event loop.

**Fix:** write the archive incrementally to a temp file (`zipfile` needs seekable output), stream it with `FileResponse`/`StreamingResponse`, cap selection bytes/count, and run in a thread (`run_in_threadpool`). Enforce ownership as today.

### P1-11. Folder deletion silently mutates file metadata
`models.py:31` + `routes/files.py:387-407`: `db.delete(folder)` triggers SQLAlchemy to null `folder_id` on related `FileTable` rows before deleting the folder, including non-deleted version rows. This is why P1-1's grouping breaks. It also means restore behavior depends on ORM internals.

**Fix:** explicitly `UPDATE filetable SET folder_id=NULL WHERE folder_id IN (...)` for rows you intend to detach (or set `passive_deletes=True` and `ON DELETE SET NULL`), then delete folders bottom-up. Do the same for every version row.

### P1-12. Public file IDs and trashed shares
`download`/`preview` authorize by `is_public` or owner, but never verify `is_latest`/`deleted_at`; share links are stateless JWTs with no revocation. Downgrading a plan does not invalidate existing links, and deleting a file does not either (P0-6). `expires_minutes` is unvalidated (`routes/files.py:564`), so negative or multi-year links are accepted.

**Fix:** validate `1 <= expires_minutes <= 10080`, require active file state on redemption, and add a `share_links` table (token hash, file/group id, creator, expiry, revoked_at) if revocation matters. At minimum, embed the file's `version_group` and a `jti`, and check state at redemption.

---

## P2 — Medium (functional, UX, ops)

### Frontend correctness
- **Row ID collision:** folders and files both use `data-row="${id}"` (`dashboard.js:275,292,324,333`). The click handler resolves folders first (`:859-871`), so clicking a file whose id equals a folder id opens the folder; its context menu becomes the folder menu. Use `data-kind="folder|file"` + `data-id`.
- **Preview breaks on any click:** the first `#modal-preview` click listener (`dashboard.js:896-898`) revokes the object URL for *every* click inside the modal, including play/pause and the download button. Remove it; keep the backdrop/close handler.
- **Upload queue targets the live folder:** `processQueue` reads `currentFolder` per iteration (`dashboard.js:688`); navigating mid-queue sends the rest of the files to the new folder. Snapshot the destination when queueing.
- **Hash routing ignores history:** `location.hash` is set on nav clicks (`:762`) but there is no `hashchange` listener; Back/Forward doesn't change the view.
- **Trash counts go stale:** `refreshAll` only reloads trash when already in the trash view (`:746-750`), so `nav-count-trash` is stale after deletes/restores elsewhere.
- **Shared view is misleading:** it filters `is_public` (`:100`), not share links; a file shared via link never appears. Rename to "Public" or track share links.
- **No refresh path for XHR/blob:** `uploadFileWithProgress` and `fetchBlob` (`api.js:76-115`) don't handle 401; large uploads/downloads fail after token expiry even if the JSON API refreshes.
- **Parallel 401s stampede:** every failed request calls `tryRefreshToken`; with rotation this invalidates siblings. Single-flight the refresh.
- **`apiFetch` returns `undefined` after redirect** (`api.js:61-63`); callers destructure it and crash before navigation. Throw a sentinel instead.
- **Enter doesn't submit auth forms** (`auth.html` has no `<form>`; `auth.js` only binds click). Add forms/submit handlers.
- **`confirmDialog` always says "This action cannot be undone"** (`ui.js:222`), including "Move to trash", which is reversible.
- **Admin plan list is hardcoded** (`admin.js:76`); if plans change, the console breaks. Fetch plans from an endpoint.
- **`openPreview` text branch** reads up to 1 MB then slices 200k chars; fine, but binary files under 1 MB render as mojibake. Gate on `text/*`/known text extensions.

### Backend/API
- **`FileResponses.path` leaks server paths** (`routes/files.py:36-49`); the frontend never uses it. **Verified** in the list response.
- **Folder rename has no duplicate check** (`:376-384`); move has no name-clash check (`:335-343`), so two active files can share a name/folder and versioning picks one arbitrarily.
- **Only the latest row can be trashed from the UI, but any row can be trashed via API.** Delete/restore/permanent-delete should operate on groups and reject non-latest version IDs.
- **`get_current_user` returns 404 for a missing user** (`:97-99`) instead of 401; leaks existence and confuses the client.
- **Duplicate `get_db` definitions** in `auth/files/plans/admin` create two sessions per request (auth's and the route's). The upload code even comments on the stale-session workaround (`files.py:230-233`). Centralize one dependency.
- **`download_zip` declares dependencies as `= None`** (`:480-485`); it works but is non-idiomatic and easy to break. Make them normal `Annotated` dependencies.
- **No pagination** on `get-files`, `trash`, `versions`, or `admin/users`; every call returns everything.
- **`seed_plans` only runs when the table is empty** (`main.py:22-32`); a partially seeded DB never gets missing plans. Seed by name idempotently.
- **Plan IDs are assumed** (frontend sends `1`); resolve by name.
- **CORS:** `allow_origins=["*"]` with `allow_credentials=True` (`main.py:14-20`) is an invalid/over-permissive combination. Configure explicit origins from env; defaults to same-origin.
- **No security headers:** add `X-Content-Type-Options`, `Referrer-Policy: no-referrer` (share tokens live in the query string, so they end up in access logs, browser history, and any Referer header), `X-Frame-Options`/`frame-ancestors`, and a CSP compatible with the Tailwind CDN.
- **Share URL uses `request.base_url`** (`files.py:578`); behind a proxy it produces `http://internal-host/...` unless forwarded headers are trusted. Add `STORIX_BASE_URL`.
- **`/docs` and `/openapi.json`** are public; disable or protect in production.
- **No rate limiting** on login/register/upload/share; no login lockout. Add a small per-IP/per-username limiter.
- **No password change/reset/verification**; expected feature gap, but note it for the auth story.
- **JWT lacks `iat`, `jti`, `aud`, `iss`**, and refresh tokens are indistinguishable from access tokens. Add claims and validate `type`.
- **Share tokens use the auth secret.** Derive a separate key/audience.
- **`User.username`/`email` are case-sensitive**; `Alice` and `alice` can coexist and, on case-insensitive filesystems, collide in storage (another reason to use user IDs).
- **`full_name` is nullable in the model but required at registration** (`models.py:12` vs `auth.py:46`); normalize.
- **No unique constraint on `(user_id, parent_id, name)` for folders**; concurrent creates can duplicate names.
- **No folder move endpoint**, no max depth, no cycle guard; `descendant_folder_ids` assumes a tree.
- **No file extension/MIME policy**; arbitrary HTML/SVG is the P0-4 vector. Consider a deny/allow list and an explicit "download only" mode for active types.
- **Admin delete user** removes rows and files but leaves the empty storage directory (`admin.py:71-91`); disk deletion is not transactional with the DB commit.
- **`make_admin.py`** works only from the repo root and doesn't confirm the new role; minor.

### Deployment / ops
- **`requirements.txt` is a full `pip freeze`**, including unused `alembic`, `psycopg2-binary`, `PyMySQL`, `sentry-sdk`, `aiofiles`, `email-validator` (until you use it), etc. Trim to direct deps + lock file.
- **`Dockerfile`** runs as root, has no `.dockerignore` (so `.venv`, `.git`, `hosting.db`, `storage/` get copied), no healthcheck, no `--proxy-headers`, no non-root user. `COPY . .` after `pip install` is good for caching.
- **`docker-compose.yml`** has no DB volume, no env, no healthcheck, no resource limits (P0-7).
- **SQLite is a single-writer bottleneck**; fine for a demo, but document the Postgres path (the code already has `psycopg2` in the freeze, unused).
- **No logging/metrics**, no request IDs, no error reporting; `print`-less but also blind.
- **No tests, no CI, no lint/type config.** README lists automated tests as "future".
- **No `LICENSE` file** despite the README's MIT link.
- **README inaccuracies:** "silent token refresh" (P1-9), "space is freed on permanent delete" (only one version's bytes), "Shared" view (public-only), and the structure tree omits `shared.html`/`admin.html` details. Add env/setup docs and a security section.
- **Tailwind CDN + Google Fonts** in production; pin versions/SRI or build Tailwind locally. Combined with the share token in the query string, set `Referrer-Policy: no-referrer` on `shared.html`.
- **`shared.html` path-token fallback** (`:46-49`) is dead code: the static mount returns 404 for `/token` paths. Remove or add a real route.
- **`shared.html` filename parsing** (`:72-74`) doesn't handle RFC 5987 `filename*=UTF-8''...`; use a small parser or the API's JSON metadata.

### UI/accessibility polish
- Add `:focus-visible` styles, `aria-label`s on icon-only buttons, `prefers-reduced-motion`, and real `<form>` semantics on auth.
- Sticky table header inside `#browser { overflow:hidden }` (`dashboard.html:128`, `app.css:229`) won't stick as intended; make the browser a scroll container or drop sticky.
- The grid card checkbox is absolutely positioned over the icon; verify it doesn't overlap the menu on narrow cards.
- `formatDate` will be correct only after P1-6; also show absolute time in a tooltip.

---

## What I verified end-to-end

Run in an isolated copy with a fresh DB; repo untouched.

| Probe | Result |
|---|---|
| Register with `plan_id: 3` | `200`, user on **Premium** |
| Forged token signed with repo secret | `200 /auth/me`, `200 /admin/users` |
| Register `../frontend`, upload `owned.html`, GET `/owned.html` | `200 text/html`, payload served |
| Duplicate username | `500 Internal Server Error` |
| `not-an-email` + 1-char password | `200`, account created |
| Two 16 B files vs 20 B quota | both `200`, `used_storage = 32` |
| Trash file, then download by id | `200`, bytes returned |
| `GET /files/get-files` | includes `path`, timestamp lacks timezone |
| Delete latest v2 permanently | v1 remains `is_latest=False`, file disappears |
| Delete folder containing a file | `folder_id` becomes `NULL` |
| `download-zip` with dependency signature | `200 application/zip` (works, but in-memory) |

---

## Proposed fix order

**Phase 1 — stop the bleeding (P0)**
1. Config module + env secret/database/storage root; rotate the key.
2. Username validation + ID-based storage paths + path containment checks.
3. Registration: server-assigned Free plan, validation, 409 handling.
4. Preview/download hardening + deleted-state checks + nosniff/CSP/referrer headers.
5. Atomic quota reservation + request-level size cap.
6. Docker DB volume/env/non-root/healthcheck.

**Phase 2 — data model integrity (P1)**
7. `version_group` + migration/backfill; rewrite upload/version/rename/move/delete/trash/restore around groups.
8. `used_storage` reconciliation; folder-delete detachment; FK pragma + indexes.
9. Refresh-token flow + single-flight client refresh + XHR/blob retry.
10. Streaming ZIP; pagination; folder clash checks.

**Phase 3 — product/UX (P2)**
11. Frontend row identity, preview listener, upload folder snapshot, hash routing, trash counts.
12. Admin plan endpoint, share-link tracking/revocation, rate limiting.
13. Tests (pytest + httpx), CI, trimmed requirements, README/env docs, LICENSE.

---

## Test matrix to add

- Auth: register validation, duplicate 409, login failure, token type confusion, expired/forged tokens, refresh rotation, rate limit.
- Isolation: user A cannot read/rename/move/delete user B's file/folder/version/share; folder IDs from another user rejected.
- Storage: username traversal, filename traversal, long names, unicode names, quota at exact boundary, multi-file quota, concurrent uploads, disk-full simulation.
- Versioning: upload v1/v2/v3, rename, move, folder delete, restore old, delete latest, permanent delete group, storage totals.
- Trash: soft delete hides from list/download/share, restore to root when folder gone, empty trash frees all versions.
- Sharing: Free plan 403, expiry bounds, expired token, deleted file, downgrade behavior, revoked link.
- Preview: HTML/SVG forced attachment, MIME correctness, nosniff/CSP headers.
- Admin: role enforcement, self-delete blocked, plan changes, user deletion cleans disk.
- Frontend smoke: auth forms, upload queue folder snapshot, row ID collision, preview controls, Back/Forward routing.

---

*This audit is based on source + runtime probes. No production data or live endpoints were touched.*

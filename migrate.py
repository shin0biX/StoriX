"""Idempotent schema migrations and data repairs for StoriX.

The project historically used ad-hoc ALTER statements in main.py. This module
keeps that lightweight approach but makes it explicit: columns are added only
when missing, errors are logged, version groups are backfilled from legacy
rows, and storage totals are recalculated. For a multi-instance deployment,
move this to Alembic/Postgres.
"""
from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from database import SessionLocal, engine
from models import Plan

logger = logging.getLogger("storix.migrate")

PLAN_SEEDS = (
    {
        "name": "Free",
        "storage_limit": 1024**3,
        "max_file_size": 100 * 1024**2,
        "can_share": False,
        "price": 0,
    },
    {
        "name": "Pro",
        "storage_limit": 10 * 1024**3,
        "max_file_size": 1000 * 1024**2,
        "can_share": True,
        "price": 10,
    },
    {
        "name": "Premium",
        "storage_limit": 100 * 1024**3,
        "max_file_size": 10_000 * 1024**2,
        "can_share": True,
        "price": 20,
    },
)

COLUMN_DDL = {
    "users": {
        "created_at": "DATETIME",
    },
    "filetable": {
        "version_group": "VARCHAR(32)",
        "deleted_at": "DATETIME",
        "is_starred": "BOOLEAN DEFAULT 0",
        "folder_id": "INTEGER",
        "version": "INTEGER DEFAULT 1",
        "is_latest": "BOOLEAN DEFAULT 1",
    },
}

INDEX_DDL = (
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_users_username_nocase "
    "ON users (username COLLATE NOCASE)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_users_email_nocase "
    "ON users (email COLLATE NOCASE)",
    "CREATE INDEX IF NOT EXISTS ix_filetable_user_active "
    "ON filetable (user_id, deleted_at, is_latest)",
    "CREATE INDEX IF NOT EXISTS ix_filetable_group ON filetable (version_group)",
    "CREATE INDEX IF NOT EXISTS ix_filetable_folder ON filetable (folder_id)",
    "CREATE INDEX IF NOT EXISTS ix_filetable_uploaded_at ON filetable (uploaded_at)",
    "CREATE INDEX IF NOT EXISTS ix_folders_user_parent "
    "ON folders (user_id, parent_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_folders_name_parent "
    "ON folders (user_id, COALESCE(parent_id, 0), name COLLATE NOCASE)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_file_active_name "
    "ON filetable (user_id, COALESCE(folder_id, 0), filename COLLATE NOCASE) "
    "WHERE is_latest = 1 AND deleted_at IS NULL",
    "CREATE INDEX IF NOT EXISTS ix_refresh_tokens_user "
    "ON refresh_tokens (user_id)",
)


def _columns(table: str) -> set[str]:
    try:
        return {col["name"] for col in inspect(engine).get_columns(table)}
    except Exception:
        return set()


def _add_missing_columns() -> None:
    for table, columns in COLUMN_DDL.items():
        existing = _columns(table)
        if not existing:
            logger.warning(
                "table %s does not exist yet; skipping column migration", table
            )
            continue
        for column, ddl in columns.items():
            if column in existing:
                continue
            try:
                with engine.begin() as conn:
                    conn.execute(
                        text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
                    )
                logger.info("added column %s.%s", table, column)
            except Exception as exc:  # pragma: no cover - defensive
                logger.error("could not add %s.%s: %s", table, column, exc)


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            dt = datetime.min
    else:
        dt = datetime.min
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _backfill_created_at() -> None:
    if "created_at" not in _columns("users"):
        return
    with engine.begin() as conn:
        result = conn.execute(
            text("UPDATE users SET created_at = :now WHERE created_at IS NULL"),
            {"now": datetime.now(timezone.utc).replace(tzinfo=None)},
        )
        if result.rowcount:
            logger.info("backfilled created_at for %d user(s)", result.rowcount)


def _apply_updates(updates: list[tuple[int, str, int, bool]]) -> None:
    if not updates:
        return
    with engine.begin() as conn:
        # Avoid transient unique(version_group, version) conflicts while
        # repairing duplicate numbering by moving touched rows out of the way.
        ids = [item[0] for item in updates]
        for chunk_start in range(0, len(ids), 400):
            chunk = ids[chunk_start : chunk_start + 400]
            placeholders = ",".join(f":id{i}" for i in range(len(chunk)))
            params = {f"id{i}": value for i, value in enumerate(chunk)}
            conn.execute(
                text(
                    f"UPDATE filetable SET version = -id "
                    f"WHERE id IN ({placeholders})"
                ),
                params,
            )
        for file_id, group, version, is_latest in updates:
            conn.execute(
                text(
                    "UPDATE filetable SET version_group = :group, "
                    "version = :version, is_latest = :is_latest WHERE id = :id"
                ),
                {
                    "group": group,
                    "version": version,
                    "is_latest": 1 if is_latest else 0,
                    "id": file_id,
                },
            )
    logger.info("repaired version metadata for %d file row(s)", len(updates))


def _backfill_version_groups() -> None:
    with engine.begin() as conn:
        rows = (
            conn.execute(
                text(
                    "SELECT id, user_id, filename, folder_id, version, is_latest, "
                    "deleted_at, uploaded_at, version_group FROM filetable"
                )
            )
            .mappings()
            .all()
        )

    if not rows:
        return

    grouped: dict[str, list[dict]] = defaultdict(list)
    legacy: dict[tuple, list[dict]] = defaultdict(list)

    for row in rows:
        group = row.get("version_group")
        if group:
            grouped[group].append(dict(row))
        else:
            key = (
                row["user_id"],
                (row["filename"] or "").lower(),
                row["folder_id"],
            )
            legacy[key].append(dict(row))

    updates: list[tuple[int, str, int, bool]] = []

    for group, group_rows in grouped.items():
        ordered = sorted(
            group_rows, key=lambda r: (_parse_dt(r["uploaded_at"]), r["id"])
        )
        latest_rows = [row for row in ordered if row.get("is_latest")]
        versions = [row.get("version") for row in ordered]
        needs_repair = (
            len(latest_rows) != 1
            or any(not isinstance(v, int) or v < 1 for v in versions)
            or len(set(versions)) != len(versions)
        )
        if not needs_repair:
            continue

        if len(latest_rows) == 1:
            latest_id = latest_rows[0]["id"]
        else:
            latest_id = ordered[-1]["id"]
        for index, row in enumerate(ordered, start=1):
            updates.append((row["id"], group, index, row["id"] == latest_id))

    for key, legacy_rows in legacy.items():
        ordered = sorted(
            legacy_rows, key=lambda r: (_parse_dt(r["uploaded_at"]), r["id"])
        )
        # Old data used one is_latest=True row as the anchor per logical file.
        # A re-upload after deletion created a second anchor. Keep those
        # anchors separate instead of merging unrelated history.
        anchors = [row for row in ordered if row.get("is_latest")]
        if not anchors:
            anchors = [ordered[-1]]

        buckets: dict[int, list[dict]] = defaultdict(list)
        anchor_ids = {anchor["id"] for anchor in anchors}
        for row in ordered:
            if row["id"] in anchor_ids:
                buckets[row["id"]].append(row)
                continue
            target = next(
                (
                    anchor
                    for anchor in anchors
                    if _parse_dt(anchor["uploaded_at"])
                    >= _parse_dt(row["uploaded_at"])
                ),
                anchors[-1],
            )
            buckets[target["id"]].append(row)

        for anchor in anchors:
            bucket = sorted(
                buckets[anchor["id"]],
                key=lambda r: (_parse_dt(r["uploaded_at"]), r["id"]),
            )
            group = anchor.get("version_group") or uuid.uuid4().hex
            for index, row in enumerate(bucket, start=1):
                updates.append(
                    (row["id"], group, index, index == len(bucket))
                )

    _apply_updates(updates)


def recalculate_used_storage() -> None:
    """Make used_storage match every metadata row on disk.

    Trashed files and superseded versions still consume disk, so they count
    until permanent deletion removes the bytes.
    """
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE users SET used_storage = COALESCE(("
                "SELECT SUM(filetable.size) FROM filetable "
                "WHERE filetable.user_id = users.id"
                "), 0)"
            )
        )


def _create_indexes() -> None:
    for statement in INDEX_DDL:
        try:
            with engine.begin() as conn:
                conn.execute(text(statement))
        except Exception as exc:
            logger.warning(
                "index creation skipped (%s): %s",
                statement.split(" ON ")[0],
                exc,
            )


def seed_plans() -> None:
    db = SessionLocal()
    try:
        existing = {
            (name or "").lower() for (name,) in db.query(Plan.name).all()
        }
        added = 0
        for seed in PLAN_SEEDS:
            if seed["name"].lower() in existing:
                continue
            db.add(Plan(**seed))
            added += 1
        if added:
            db.commit()
            logger.info("seeded %d plan(s)", added)
    finally:
        db.close()


def run_migrations(target_engine: Engine | None = None) -> None:
    # Keep the parameter for tests/future callers; the module-level engine is
    # what these ad-hoc statements use.
    if target_engine is not None and target_engine is not engine:
        raise RuntimeError("run_migrations uses the configured engine")
    _add_missing_columns()
    _backfill_created_at()
    _backfill_version_groups()
    _create_indexes()
    recalculate_used_storage()

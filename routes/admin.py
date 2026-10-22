from __future__ import annotations

import logging
import shutil
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func
from sqlalchemy.orm import Session

from database import get_db
from models import FileTable, Folder, Plan, User
from routes.auth import get_current_user
from storage import (
    STORAGE_ROOT,
    delete_stored_path,
    deletion_paths_for_user_dir,
)

logger = logging.getLogger("storix.admin")

router = APIRouter(prefix="/admin", tags=["admin"])

db_dependency = Annotated[Session, Depends(get_db)]
user_dependency = Annotated[User, Depends(get_current_user)]


def require_admin(user: user_dependency) -> User:
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return user


admin_dependency = Annotated[User, Depends(require_admin)]


class AdminPlanRequest(BaseModel):
    plan: str = Field(min_length=1, max_length=40)

    @field_validator("plan", mode="before")
    @classmethod
    def strip_plan(cls, value):  # noqa: ANN001
        return value.strip() if isinstance(value, str) else value


@router.get("/users")
def list_users(
    db: db_dependency,
    _admin: admin_dependency,
    limit: Annotated[int, Query(ge=1, le=1000)] = 500,
    offset: Annotated[int, Query(ge=0)] = 0,
    q: Annotated[str | None, Query(max_length=120)] = None,
):
    query = db.query(User)
    if q:
        pattern = f"%{q.strip().lower()}%"
        query = query.filter(
            func.lower(User.username).like(pattern)
            | func.lower(User.email).like(pattern)
            | func.lower(User.full_name).like(pattern)
        )
    users = query.order_by(User.id).limit(limit).offset(offset).all()

    counts = dict(
        db.query(FileTable.user_id, func.count(FileTable.id))
        .filter(
            FileTable.user_id.in_([user.id for user in users]),
            FileTable.deleted_at.is_(None),
            FileTable.is_latest.is_(True),
        )
        .group_by(FileTable.user_id)
        .all()
    ) if users else {}

    return [
        {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "full_name": user.full_name,
            "role": user.role,
            "used_storage": user.used_storage,
            "file_count": counts.get(user.id, 0),
            "plan": user.plan.name if user.plan else "—",
            "created_at": user.created_at,
        }
        for user in users
    ]


@router.put("/users/{user_id}/plan")
def change_user_plan(
    user_id: int,
    request: AdminPlanRequest,
    db: db_dependency,
    _admin: admin_dependency,
):
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    plan = (
        db.query(Plan)
        .filter(func.lower(Plan.name) == request.plan.strip().lower())
        .first()
    )
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan not found")
    if target.used_storage > plan.storage_limit:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User's stored data exceeds this plan's limit",
        )
    target.plan_id = plan.id
    if not plan.can_share:
        db.query(FileTable).filter(FileTable.user_id == target.id).update(
            {FileTable.is_public: False}, synchronize_session=False
        )
    db.commit()
    return {"message": f"{target.username} moved to {plan.name}"}


@router.delete("/users/{user_id}")
def delete_user(
    user_id: int,
    db: db_dependency,
    admin: admin_dependency,
):
    if user_id == admin.id:
        raise HTTPException(
            status_code=400, detail="You cannot delete your own account"
        )
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")

    username = target.username
    target_id = target.id
    stored_paths = [
        row[0]
        for row in db.query(FileTable.path)
        .filter(FileTable.user_id == target_id)
        .all()
    ]
    folder_rows = (
        db.query(Folder.id, Folder.parent_id)
        .filter(Folder.user_id == target.id)
        .all()
    )

    db.query(FileTable).filter(FileTable.user_id == target_id).delete(
        synchronize_session=False
    )
    # Delete deepest folders first so self-referencing foreign keys are safe.
    parent_by_id = {row.id: row.parent_id for row in folder_rows}
    depth_cache: dict[int, int] = {}

    def depth(folder_id: int) -> int:
        if folder_id in depth_cache:
            return depth_cache[folder_id]
        value = 0
        current = parent_by_id.get(folder_id)
        seen = {folder_id}
        while current is not None and current in parent_by_id and current not in seen:
            seen.add(current)
            value += 1
            current = parent_by_id.get(current)
        depth_cache[folder_id] = value
        return value

    for folder_id in sorted(parent_by_id, key=depth, reverse=True):
        db.query(Folder).filter(
            Folder.id == folder_id, Folder.user_id == target.id
        ).delete(synchronize_session=False)
    db.delete(target)
    db.commit()

    for stored in stored_paths:
        delete_stored_path(stored)
    user_dir = deletion_paths_for_user_dir(target_id)
    try:
        if user_dir.is_dir() and user_dir.resolve().is_relative_to(STORAGE_ROOT):
            shutil.rmtree(user_dir, ignore_errors=True)
    except OSError as exc:  # pragma: no cover - filesystem dependent
        logger.error("could not remove user storage directory: %s", exc)

    return {"message": f"User {username} deleted"}


@router.post("/reconcile-storage")
def reconcile_storage(db: db_dependency, _admin: admin_dependency):
    """Recalculate every user's used_storage from file rows.

    All versions and trashed rows count because they still occupy disk. The
    number is freed only by permanent deletion.
    """
    totals = dict(
        db.query(FileTable.user_id, func.coalesce(func.sum(FileTable.size), 0))
        .group_by(FileTable.user_id)
        .all()
    )
    users = db.query(User).all()
    changed = 0
    for user in users:
        expected = int(totals.get(user.id, 0))
        if user.used_storage != expected:
            user.used_storage = expected
            changed += 1
    db.commit()
    return {"message": f"Reconciled storage for {changed} user(s)", "changed": changed}

from __future__ import annotations

import logging
import os
import tempfile
import uuid
import zipfile
from datetime import datetime
from mimetypes import guess_type
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import case, func
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from config import (
    BASE_URL,
    MAX_UPLOAD_FILES,
    SHARE_MAX_MINUTES,
    SHARE_RATE_LIMIT,
    SHARE_RATE_WINDOW_SECONDS,
    TEMP_DIR,
    UPLOAD_RATE_LIMIT,
    UPLOAD_RATE_WINDOW_SECONDS,
    ZIP_MAX_BYTES,
    ZIP_MAX_FILES,
)
from database import get_db
from models import FileTable, Folder, User, utcnow
from ratelimit import SlidingWindowLimiter
from routes.auth import (
    create_share_token,
    decode_share_token,
    get_current_user,
    get_current_user_optional,
)
from storage import (
    delete_stored_path,
    new_disk_path,
    resolve_stored_path,
    safe_unlink,
    sanitize_filename,
)

logger = logging.getLogger("storix.files")

router = APIRouter(prefix="/files", tags=["files"])

db_dependency = Annotated[Session, Depends(get_db)]
user_dependency = Annotated[User, Depends(get_current_user)]
user_dependency_optional = Annotated[
    User | None, Depends(get_current_user_optional)
]

upload_limiter = SlidingWindowLimiter(
    UPLOAD_RATE_LIMIT, UPLOAD_RATE_WINDOW_SECONDS
)
share_limiter = SlidingWindowLimiter(
    SHARE_RATE_LIMIT, SHARE_RATE_WINDOW_SECONDS
)

CHUNK_SIZE = 1024 * 1024

SAFE_PREVIEW_TYPES = {
    "application/pdf",
    "text/plain",
}
SAFE_PREVIEW_PREFIXES = ("image/", "video/", "audio/")


class FileResponses(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    filename: str
    size: int
    is_public: bool
    is_starred: bool
    uploaded_at: datetime
    folder_id: int | None
    version: int
    is_latest: bool
    version_group: str
    deleted_at: datetime | None = None


class FolderResponses(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    parent_id: int | None
    created_at: datetime


class VisibilityRequest(BaseModel):
    choice: bool


class StarRequest(BaseModel):
    starred: bool


class RenameRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255)

    @field_validator("filename", mode="before")
    @classmethod
    def strip_name(cls, value):  # noqa: ANN001
        return value.strip() if isinstance(value, str) else value


class MoveRequest(BaseModel):
    folder_id: int | None = None


class FolderCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    parent_id: int | None = None

    @field_validator("name", mode="before")
    @classmethod
    def strip_name(cls, value):  # noqa: ANN001
        return value.strip() if isinstance(value, str) else value


class FolderRenameRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)

    @field_validator("name", mode="before")
    @classmethod
    def strip_name(cls, value):  # noqa: ANN001
        return value.strip() if isinstance(value, str) else value


class FolderMoveRequest(BaseModel):
    parent_id: int | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _owned_file_or_404(db: Session, user: User, file_id: int) -> FileTable:
    file = (
        db.query(FileTable)
        .filter(FileTable.id == file_id, FileTable.user_id == user.id)
        .first()
    )
    if file is None:
        raise HTTPException(status_code=404, detail="File not found")
    return file


def _active_latest_or_404(db: Session, user: User, file_id: int) -> FileTable:
    file = _owned_file_or_404(db, user, file_id)
    if file.deleted_at is not None:
        raise HTTPException(status_code=404, detail="File not found")
    if not file.is_latest:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This is not the current version of the file",
        )
    return file


def _trashed_latest_or_404(db: Session, user: User, file_id: int) -> FileTable:
    file = _owned_file_or_404(db, user, file_id)
    if file.deleted_at is None:
        raise HTTPException(status_code=409, detail="File is not in the trash")
    if not file.is_latest:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This is not the current version of the file",
        )
    return file


def _folder_or_404(db: Session, user: User, folder_id: int | None) -> Folder | None:
    if folder_id is None:
        return None
    folder = (
        db.query(Folder)
        .filter(Folder.id == folder_id, Folder.user_id == user.id)
        .first()
    )
    if folder is None:
        raise HTTPException(status_code=404, detail="Folder not found")
    return folder


def _folder_subtree(db: Session, user: User, root: Folder) -> list[tuple[Folder, int]]:
    """Return (folder, depth) for the subtree, root first, cycle-safe."""
    result: list[tuple[Folder, int]] = []
    seen: set[int] = set()
    stack: list[tuple[Folder, int]] = [(root, 0)]
    while stack:
        current, depth = stack.pop()
        if current.id in seen:
            continue
        seen.add(current.id)
        result.append((current, depth))
        children = (
            db.query(Folder)
            .filter(Folder.user_id == user.id, Folder.parent_id == current.id)
            .all()
        )
        for child in children:
            if child.id not in seen:
                stack.append((child, depth + 1))
    return result


def _clean_folder_name(raw: str) -> str:
    name = sanitize_filename(raw)
    if name in {".", ".."}:
        raise HTTPException(status_code=400, detail="Invalid folder name")
    return name


def _folder_name_taken(
    db: Session,
    user: User,
    name: str,
    parent_id: int | None,
    exclude_id: int | None = None,
) -> bool:
    query = db.query(Folder).filter(
        Folder.user_id == user.id,
        func.lower(Folder.name) == name.lower(),
    )
    if parent_id is None:
        query = query.filter(Folder.parent_id.is_(None))
    else:
        query = query.filter(Folder.parent_id == parent_id)
    if exclude_id is not None:
        query = query.filter(Folder.id != exclude_id)
    return query.first() is not None


def _active_name_taken(
    db: Session,
    user: User,
    filename: str,
    folder_id: int | None,
    exclude_group: str | None = None,
) -> bool:
    query = db.query(FileTable).filter(
        FileTable.user_id == user.id,
        FileTable.deleted_at.is_(None),
        FileTable.is_latest.is_(True),
        func.lower(FileTable.filename) == filename.lower(),
    )
    if folder_id is None:
        query = query.filter(FileTable.folder_id.is_(None))
    else:
        query = query.filter(FileTable.folder_id == folder_id)
    if exclude_group is not None:
        query = query.filter(FileTable.version_group != exclude_group)
    return query.first() is not None


def _disk_path_or_404(stored: str) -> Path:
    try:
        path = resolve_stored_path(stored)
    except ValueError:
        logger.error("blocked path outside storage root: %s", stored)
        raise HTTPException(status_code=404, detail="File missing on server")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="File missing on server")
    return path


def _downloadable_file(
    db: Session, user: User | None, file_id: int
) -> FileTable:
    file = db.query(FileTable).filter(FileTable.id == file_id).first()
    if file is None or file.deleted_at is not None or not file.is_latest:
        raise HTTPException(status_code=404, detail="File not found")
    if not file.is_public:
        if user is None:
            raise HTTPException(status_code=401, detail="Login required")
        if file.user_id != user.id:
            # Do not reveal that another user's file exists.
            raise HTTPException(status_code=404, detail="File not found")
    elif user is None or user.id != file.user_id:
        # Legacy rows (or a plan changed outside this app) may still be marked
        # public after the owner lost sharing rights. The owner always keeps
        # access; everyone else is checked against the owner's current plan.
        owner = db.get(User, file.user_id)
        if owner is None or owner.plan is None or not owner.plan.can_share:
            raise HTTPException(
                status_code=403,
                detail="Public sharing is no longer available for this file",
            )
    return file


def _freed_storage_update(freed: int) -> case:
    return case(
        (User.used_storage >= freed, User.used_storage - freed),
        else_=0,
    )


def _build_zip(entries: list[tuple[Path, str]], destination: Path) -> None:
    used: set[str] = set()
    with zipfile.ZipFile(
        destination, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True
    ) as archive:
        for path, filename in entries:
            # Legacy rows predate filename sanitization; never let a stored
            # name inject path separators into the archive.
            arcname = sanitize_filename(filename)
            stem, extension = os.path.splitext(filename)
            counter = 1
            while arcname.lower() in used:
                arcname = f"{stem}({counter}){extension}"
                counter += 1
            used.add(arcname.lower())
            archive.write(path, arcname)


# ---------------------------------------------------------------------------
# Files listing / upload
# ---------------------------------------------------------------------------


@router.get("/get-files", response_model=list[FileResponses])
def get_files(
    user: user_dependency,
    db: db_dependency,
    limit: Annotated[int, Query(ge=1, le=2000)] = 1000,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    return (
        db.query(FileTable)
        .filter(
            FileTable.user_id == user.id,
            FileTable.deleted_at.is_(None),
            FileTable.is_latest.is_(True),
        )
        .order_by(FileTable.uploaded_at.desc(), FileTable.id.desc())
        .limit(limit)
        .offset(offset)
        .all()
    )


@router.get("/trash", response_model=list[FileResponses])
def get_trash(
    user: user_dependency,
    db: db_dependency,
    limit: Annotated[int, Query(ge=1, le=2000)] = 1000,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    return (
        db.query(FileTable)
        .filter(
            FileTable.user_id == user.id,
            FileTable.deleted_at.is_not(None),
            FileTable.is_latest.is_(True),
        )
        .order_by(FileTable.deleted_at.desc(), FileTable.id.desc())
        .limit(limit)
        .offset(offset)
        .all()
    )


@router.post("/upload/")
async def upload_file(
    request: Request,
    db: db_dependency,
    user: user_dependency,
    files: Annotated[list[UploadFile], File(...)],
    folder_id: Annotated[int | None, Query()] = None,
):
    retry_after = upload_limiter.hit(str(user.id))
    if retry_after is not None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many uploads. Slow down and try again shortly.",
            headers={"Retry-After": str(retry_after)},
        )

    if not files:
        raise HTTPException(status_code=400, detail="No files were uploaded")
    if len(files) > MAX_UPLOAD_FILES:
        raise HTTPException(
            status_code=413,
            detail=f"At most {MAX_UPLOAD_FILES} files can be uploaded at once",
        )

    target_folder = _folder_or_404(db, user, folder_id)
    db_user = db.get(User, user.id)
    if db_user is None:
        raise HTTPException(status_code=401, detail="Could not validate credentials")
    plan = db_user.plan
    if plan is None:
        raise HTTPException(status_code=500, detail="Plan configuration is missing")

    content_length = request.headers.get("content-length")
    if content_length:
        try:
            # Multipart boundaries add overhead, so allow a small margin.
            if int(content_length) > plan.storage_limit + 1024 * 1024:
                raise HTTPException(
                    status_code=413,
                    detail="Upload is larger than your entire storage quota",
                )
        except ValueError:
            pass

    uploaded: list[dict] = []
    errors: list[dict] = []

    for upload in files:
        display_name = sanitize_filename(upload.filename or "file")
        temp_path = TEMP_DIR / f"{uuid.uuid4().hex}.part"
        final_path: Path | None = None
        size = 0

        try:
            if upload.size is not None and upload.size > plan.max_file_size:
                errors.append(
                    {
                        "filename": display_name,
                        "detail": "File exceeds your plan's max file size",
                    }
                )
                continue

            try:
                with open(temp_path, "wb") as buffer:
                    while True:
                        chunk = await upload.read(CHUNK_SIZE)
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > plan.max_file_size:
                            raise HTTPException(
                                status_code=413,
                                detail="File exceeds your plan's max file size",
                            )
                        buffer.write(chunk)
            except HTTPException as exc:
                safe_unlink(temp_path)
                errors.append(
                    {"filename": display_name, "detail": str(exc.detail)}
                )
                continue
            except OSError as exc:
                safe_unlink(temp_path)
                logger.error("upload write failed for %s: %s", display_name, exc)
                errors.append(
                    {
                        "filename": display_name,
                        "detail": "Could not store the file on the server",
                    }
                )
                continue

            # Reserve quota before creating any metadata. The conditional
            # UPDATE is atomic, so parallel/multi-file uploads cannot overspend.
            reservation = db.execute(
                User.__table__.update()
                .where(
                    User.id == db_user.id,
                    User.used_storage + size <= plan.storage_limit,
                )
                .values(used_storage=User.used_storage + size)
            )
            if reservation.rowcount != 1:
                db.rollback()
                safe_unlink(temp_path)
                errors.append(
                    {
                        "filename": display_name,
                        "detail": "Storage limit exceeded",
                    }
                )
                continue

            # The quota UPDATE holds the SQLite write lock. Resolve versioning
            # now so concurrent uploads cannot create two active rows.
            existing = (
                db.query(FileTable)
                .filter(
                    FileTable.user_id == user.id,
                    FileTable.deleted_at.is_(None),
                    FileTable.is_latest.is_(True),
                    func.lower(FileTable.filename) == display_name.lower(),
                    FileTable.folder_id.is_(None)
                    if target_folder is None
                    else FileTable.folder_id == target_folder.id,
                )
                .first()
            )

            if existing is not None:
                version_group = existing.version_group
                new_version = (
                    db.query(func.max(FileTable.version))
                    .filter(FileTable.version_group == version_group)
                    .scalar()
                    or 0
                ) + 1
                db.query(FileTable).filter(
                    FileTable.version_group == version_group
                ).update(
                    {
                        FileTable.is_latest: False,
                        FileTable.filename: display_name,
                    },
                    synchronize_session=False,
                )
                stored_folder_id = existing.folder_id
            else:
                version_group = uuid.uuid4().hex
                new_version = 1
                stored_folder_id = target_folder.id if target_folder else None

            final_path, relative_path = new_disk_path(user.id, display_name)
            try:
                os.replace(temp_path, final_path)
            except OSError as exc:
                db.rollback()
                safe_unlink(temp_path)
                logger.error("could not move upload into storage: %s", exc)
                errors.append(
                    {
                        "filename": display_name,
                        "detail": "Could not store the file on the server",
                    }
                )
                continue

            record = FileTable(
                filename=display_name,
                size=size,
                path=relative_path,
                user_id=user.id,
                folder_id=stored_folder_id,
                version=new_version,
                is_latest=True,
                version_group=version_group,
            )
            db.add(record)
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                safe_unlink(final_path)
                logger.warning("upload version race for %s", display_name)
                errors.append(
                    {
                        "filename": display_name,
                        "detail": "A conflicting upload happened. Please retry.",
                    }
                )
                continue
            except SQLAlchemyError as exc:
                db.rollback()
                safe_unlink(final_path)
                logger.exception("upload commit failed: %s", exc)
                errors.append(
                    {
                        "filename": display_name,
                        "detail": "Could not save the file",
                    }
                )
                continue

            db.refresh(record)
            uploaded.append(
                {
                    "id": record.id,
                    "filename": record.filename,
                    "size": record.size,
                    "version": record.version,
                    "folder_id": record.folder_id,
                    "uploaded_at": record.uploaded_at,
                }
            )
        finally:
            await upload.close()
            safe_unlink(temp_path)

    if not uploaded and errors:
        raise HTTPException(status_code=400, detail=errors[0]["detail"])

    return {"uploaded": uploaded, "errors": errors}


# ---------------------------------------------------------------------------
# Trash / delete
# ---------------------------------------------------------------------------


@router.delete("/file-delete/{file_id}")
def delete_file(user: user_dependency, db: db_dependency, file_id: int):
    file = _active_latest_or_404(db, user, file_id)
    now = utcnow()
    db.query(FileTable).filter(
        FileTable.user_id == user.id,
        FileTable.version_group == file.version_group,
    ).update({FileTable.deleted_at: now}, synchronize_session=False)
    db.commit()
    return {"message": "File moved to trash"}


@router.post("/restore/{file_id}")
def restore_file(user: user_dependency, db: db_dependency, file_id: int):
    file = _trashed_latest_or_404(db, user, file_id)

    folder = None
    if file.folder_id is not None:
        folder = (
            db.query(Folder)
            .filter(Folder.id == file.folder_id, Folder.user_id == user.id)
            .first()
        )
    target_folder_id = folder.id if folder is not None else None

    if _active_name_taken(
        db,
        user,
        file.filename,
        target_folder_id,
        exclude_group=file.version_group,
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "An active file with this name already exists in the restore "
                "location. Rename or delete it first."
            ),
        )

    db.query(FileTable).filter(
        FileTable.user_id == user.id,
        FileTable.version_group == file.version_group,
    ).update(
        {FileTable.deleted_at: None, FileTable.folder_id: target_folder_id},
        synchronize_session=False,
    )
    db.commit()
    return {"message": "File restored"}


@router.delete("/permanent-delete/{file_id}")
def permanent_delete(user: user_dependency, db: db_dependency, file_id: int):
    file = _trashed_latest_or_404(db, user, file_id)
    rows = (
        db.query(FileTable)
        .filter(
            FileTable.user_id == user.id,
            FileTable.version_group == file.version_group,
        )
        .all()
    )
    if not rows:
        raise HTTPException(status_code=404, detail="File not found")

    freed = sum(row.size for row in rows)
    paths = [row.path for row in rows]

    db.query(FileTable).filter(
        FileTable.user_id == user.id,
        FileTable.version_group == file.version_group,
    ).delete(synchronize_session=False)
    db.query(User).filter(User.id == user.id).update(
        {User.used_storage: _freed_storage_update(freed)},
        synchronize_session=False,
    )
    db.commit()

    for stored in paths:
        delete_stored_path(stored)
    return {"message": "File permanently deleted", "freed_bytes": freed}


@router.post("/trash/empty")
def empty_trash(user: user_dependency, db: db_dependency):
    rows = (
        db.query(FileTable)
        .filter(
            FileTable.user_id == user.id,
            FileTable.deleted_at.is_not(None),
        )
        .all()
    )
    if not rows:
        return {"message": "Trash is already empty", "files": 0, "freed_bytes": 0}

    logical_count = sum(1 for row in rows if row.is_latest)
    freed = sum(row.size for row in rows)
    paths = [row.path for row in rows]

    db.query(FileTable).filter(
        FileTable.user_id == user.id,
        FileTable.deleted_at.is_not(None),
    ).delete(synchronize_session=False)
    db.query(User).filter(User.id == user.id).update(
        {User.used_storage: _freed_storage_update(freed)},
        synchronize_session=False,
    )
    db.commit()

    for stored in paths:
        delete_stored_path(stored)
    return {
        "message": f"Trash emptied ({logical_count} files)",
        "files": logical_count,
        "freed_bytes": freed,
    }


# ---------------------------------------------------------------------------
# Star / rename / move / visibility
# ---------------------------------------------------------------------------


@router.put("/{file_id}/star")
def toggle_star(
    file_id: int,
    request: StarRequest,
    user: user_dependency,
    db: db_dependency,
):
    file = _active_latest_or_404(db, user, file_id)
    db.query(FileTable).filter(
        FileTable.user_id == user.id,
        FileTable.version_group == file.version_group,
    ).update(
        {FileTable.is_starred: request.starred}, synchronize_session=False
    )
    db.commit()
    return {"is_starred": request.starred}


@router.put("/{file_id}/rename")
def rename_file(
    file_id: int,
    request: RenameRequest,
    user: user_dependency,
    db: db_dependency,
):
    file = _active_latest_or_404(db, user, file_id)
    new_name = sanitize_filename(request.filename)
    if new_name.lower() == file.filename.lower():
        return {"filename": file.filename}

    if _active_name_taken(
        db,
        user,
        new_name,
        file.folder_id,
        exclude_group=file.version_group,
    ):
        raise HTTPException(
            status_code=409,
            detail="A file with that name already exists here",
        )

    db.query(FileTable).filter(
        FileTable.user_id == user.id,
        FileTable.version_group == file.version_group,
    ).update({FileTable.filename: new_name}, synchronize_session=False)
    db.commit()
    return {"filename": new_name}


@router.put("/{file_id}/move")
def move_file(
    file_id: int,
    request: MoveRequest,
    user: user_dependency,
    db: db_dependency,
):
    file = _active_latest_or_404(db, user, file_id)
    target = _folder_or_404(db, user, request.folder_id)
    target_id = target.id if target else None
    if target_id == file.folder_id:
        return {"message": "File is already in that folder", "folder_id": target_id}

    if _active_name_taken(
        db,
        user,
        file.filename,
        target_id,
        exclude_group=file.version_group,
    ):
        raise HTTPException(
            status_code=409,
            detail="A file with that name already exists in the destination",
        )

    db.query(FileTable).filter(
        FileTable.user_id == user.id,
        FileTable.version_group == file.version_group,
    ).update({FileTable.folder_id: target_id}, synchronize_session=False)
    db.commit()
    return {"message": "File moved", "folder_id": target_id}


@router.put("/{file_id}/visibility")
def change_visibility(
    file_id: int,
    request: VisibilityRequest,
    user: user_dependency,
    db: db_dependency,
):
    file = _active_latest_or_404(db, user, file_id)
    db_user = db.get(User, user.id)
    if db_user is None or db_user.plan is None:
        raise HTTPException(status_code=401, detail="Could not validate credentials")

    if request.choice and not db_user.plan.can_share:
        raise HTTPException(
            status_code=403, detail="Your plan does not allow public sharing"
        )

    db.query(FileTable).filter(
        FileTable.user_id == user.id,
        FileTable.version_group == file.version_group,
    ).update({FileTable.is_public: request.choice}, synchronize_session=False)
    db.commit()
    return {"is_public": request.choice}


# ---------------------------------------------------------------------------
# Folders
# ---------------------------------------------------------------------------


@router.get("/folders", response_model=list[FolderResponses])
def get_folders(user: user_dependency, db: db_dependency):
    return (
        db.query(Folder)
        .filter(Folder.user_id == user.id)
        .order_by(Folder.parent_id, Folder.name)
        .all()
    )


@router.post("/folders", status_code=status.HTTP_201_CREATED)
def create_folder(
    request: FolderCreateRequest,
    user: user_dependency,
    db: db_dependency,
):
    name = _clean_folder_name(request.name)
    parent = _folder_or_404(db, user, request.parent_id)
    parent_id = parent.id if parent else None

    if _folder_name_taken(db, user, name, parent_id):
        raise HTTPException(
            status_code=409, detail="A folder with that name already exists here"
        )

    folder = Folder(name=name, parent_id=parent_id, user_id=user.id)
    db.add(folder)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="A folder with that name already exists here"
        )
    db.refresh(folder)
    return {
        "id": folder.id,
        "name": folder.name,
        "parent_id": folder.parent_id,
        "created_at": folder.created_at,
    }


@router.put("/folders/{folder_id}/rename")
def rename_folder(
    folder_id: int,
    request: FolderRenameRequest,
    user: user_dependency,
    db: db_dependency,
):
    folder = _folder_or_404(db, user, folder_id)
    assert folder is not None
    name = _clean_folder_name(request.name)
    if name == folder.name:
        return {"id": folder.id, "name": folder.name}
    if _folder_name_taken(db, user, name, folder.parent_id, exclude_id=folder.id):
        raise HTTPException(
            status_code=409, detail="A folder with that name already exists here"
        )

    folder.name = name
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="A folder with that name already exists here"
        )
    return {"id": folder.id, "name": folder.name}


@router.put("/folders/{folder_id}/move")
def move_folder(
    folder_id: int,
    request: FolderMoveRequest,
    user: user_dependency,
    db: db_dependency,
):
    folder = _folder_or_404(db, user, folder_id)
    assert folder is not None
    target = _folder_or_404(db, user, request.parent_id)
    target_id = target.id if target else None
    if target_id == folder.parent_id:
        return {"message": "Folder is already there", "parent_id": target_id}
    if target_id == folder.id:
        raise HTTPException(status_code=400, detail="A folder cannot contain itself")

    if target is not None:
        subtree_ids = {item.id for item, _ in _folder_subtree(db, user, folder)}
        if target.id in subtree_ids:
            raise HTTPException(
                status_code=400, detail="A folder cannot be moved into itself"
            )
    if _folder_name_taken(db, user, folder.name, target_id, exclude_id=folder.id):
        raise HTTPException(
            status_code=409, detail="A folder with that name already exists there"
        )

    folder.parent_id = target_id
    db.commit()
    return {"message": "Folder moved", "parent_id": target_id}


@router.delete("/folders/{folder_id}")
def delete_folder(folder_id: int, user: user_dependency, db: db_dependency):
    folder = _folder_or_404(db, user, folder_id)
    assert folder is not None
    subtree = _folder_subtree(db, user, folder)
    folder_ids = [item.id for item, _ in subtree]

    groups = [
        row[0]
        for row in db.query(FileTable.version_group)
        .filter(
            FileTable.user_id == user.id,
            FileTable.folder_id.in_(folder_ids),
            FileTable.deleted_at.is_(None),
            FileTable.is_latest.is_(True),
        )
        .distinct()
        .all()
    ]
    now = utcnow()
    if groups:
        db.query(FileTable).filter(
            FileTable.user_id == user.id,
            FileTable.version_group.in_(groups),
        ).update({FileTable.deleted_at: now}, synchronize_session=False)

    # The folder is gone; keep all versions (including already-trashed ones)
    # at the root rather than letting foreign-key/SET NULL behavior merge them.
    db.query(FileTable).filter(
        FileTable.user_id == user.id,
        FileTable.folder_id.in_(folder_ids),
    ).update({FileTable.folder_id: None}, synchronize_session=False)

    # Delete children before parents so self-referencing FKs stay valid.
    for item, _depth in sorted(subtree, key=lambda pair: pair[1], reverse=True):
        db.query(Folder).filter(
            Folder.id == item.id, Folder.user_id == user.id
        ).delete(synchronize_session=False)
    db.commit()
    return {
        "message": "Folder deleted",
        "trashed_files": len(groups),
    }


# ---------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------


@router.get("/versions/{file_id}")
def get_versions(file_id: int, user: user_dependency, db: db_dependency):
    file = _active_latest_or_404(db, user, file_id)
    versions = (
        db.query(FileTable)
        .filter(
            FileTable.user_id == user.id,
            FileTable.version_group == file.version_group,
        )
        .order_by(FileTable.version.desc())
        .all()
    )
    return [
        {
            "id": version.id,
            "version": version.version,
            "size": version.size,
            "uploaded_at": version.uploaded_at,
            "is_latest": version.is_latest,
            "filename": version.filename,
        }
        for version in versions
    ]


@router.post("/versions/{file_id}/restore/{version_id}")
def restore_version(
    file_id: int,
    version_id: int,
    user: user_dependency,
    db: db_dependency,
):
    current = _active_latest_or_404(db, user, file_id)
    target = _owned_file_or_404(db, user, version_id)
    if (
        target.version_group != current.version_group
        or target.deleted_at is not None
    ):
        raise HTTPException(
            status_code=400, detail="Version does not belong to this file"
        )

    db.query(FileTable).filter(
        FileTable.user_id == user.id,
        FileTable.version_group == current.version_group,
    ).update({FileTable.is_latest: False}, synchronize_session=False)
    db.query(FileTable).filter(
        FileTable.id == target.id, FileTable.user_id == user.id
    ).update({FileTable.is_latest: True}, synchronize_session=False)
    db.commit()
    return {"message": f"Restored version {target.version}"}


# ---------------------------------------------------------------------------
# Download / preview / ZIP
# ---------------------------------------------------------------------------


@router.get("/download/{file_id}")
def download_file(
    file_id: int,
    user: user_dependency_optional,
    db: db_dependency,
):
    file = _downloadable_file(db, user, file_id)
    path = _disk_path_or_404(file.path)
    return FileResponse(
        path=path,
        filename=file.filename,
        media_type="application/octet-stream",
        headers={"Cache-Control": "private, no-store"},
    )


@router.get("/preview/{file_id}")
def preview_file(
    file_id: int,
    user: user_dependency_optional,
    db: db_dependency,
):
    file = _downloadable_file(db, user, file_id)
    path = _disk_path_or_404(file.path)

    media_type = guess_type(file.filename)[0] or "application/octet-stream"
    subtype = media_type.split("/", 1)[-1].lower()
    safe = media_type in SAFE_PREVIEW_TYPES or (
        media_type.startswith(SAFE_PREVIEW_PREFIXES)
        and not (media_type == "image/svg+xml" or subtype in {"svg", "svg+xml"})
    )

    if safe:
        headers = {
            "Content-Disposition": f"inline; filename*=UTF-8''{quote(file.filename)}",
            "Content-Security-Policy": (
                "sandbox; default-src 'none'; img-src 'self' data: blob:; "
                "media-src 'self' blob:; style-src 'unsafe-inline'"
            ),
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "SAMEORIGIN",
            "Cache-Control": "private, no-store",
        }
        return FileResponse(path=path, media_type=media_type, headers=headers)

    # HTML, SVG, XML, JS, unknown binaries: never render inline on this origin.
    return FileResponse(
        path=path,
        filename=file.filename,
        media_type="application/octet-stream",
        content_disposition_type="attachment",
        headers={
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


@router.get("/download-zip")
def download_zip(
    ids: Annotated[str, Query(min_length=1, max_length=20000)],
    user: user_dependency,
    db: db_dependency,
):
    id_list: list[int] = []
    seen: set[int] = set()
    for part in ids.split(","):
        part = part.strip()
        if not part.isdigit():
            continue
        value = int(part)
        if value not in seen:
            seen.add(value)
            id_list.append(value)
    if not id_list:
        raise HTTPException(status_code=400, detail="No valid file ids provided")
    if len(id_list) > ZIP_MAX_FILES:
        raise HTTPException(
            status_code=413,
            detail=f"At most {ZIP_MAX_FILES} files can be archived at once",
        )

    files = (
        db.query(FileTable)
        .filter(
            FileTable.id.in_(id_list),
            FileTable.user_id == user.id,
            FileTable.deleted_at.is_(None),
            FileTable.is_latest.is_(True),
        )
        .all()
    )
    if not files:
        raise HTTPException(status_code=404, detail="No files found")

    total = sum(file.size for file in files)
    if total > ZIP_MAX_BYTES:
        raise HTTPException(
            status_code=413, detail="Selection is too large to archive"
        )

    entries: list[tuple[Path, str]] = []
    for file in files:
        try:
            path = resolve_stored_path(file.path)
        except ValueError:
            logger.error("zip skipped unsafe path for file %s", file.id)
            continue
        if path.is_file():
            entries.append((path, file.filename))
    if not entries:
        raise HTTPException(status_code=404, detail="Files are missing on server")

    fd, archive_name = tempfile.mkstemp(dir=TEMP_DIR, suffix=".zip")
    os.close(fd)
    archive_path = Path(archive_name)
    try:
        # This endpoint is sync, so FastAPI already runs it in a worker thread.
        _build_zip(entries, archive_path)
    except Exception as exc:
        safe_unlink(archive_path)
        logger.exception("ZIP build failed: %s", exc)
        raise HTTPException(status_code=500, detail="Could not build the archive")

    return FileResponse(
        path=archive_path,
        filename="storix-files.zip",
        media_type="application/zip",
        background=BackgroundTask(safe_unlink, archive_path),
        headers={"Cache-Control": "private, no-store"},
    )


# ---------------------------------------------------------------------------
# Sharing
# ---------------------------------------------------------------------------


@router.post("/share/{file_id}")
def create_share_link(
    request: Request,
    user: user_dependency,
    db: db_dependency,
    file_id: int,
    expires_minutes: Annotated[
        int, Query(ge=1, le=SHARE_MAX_MINUTES)
    ] = 30,
):
    retry_after = share_limiter.hit(str(user.id))
    if retry_after is not None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many share links created. Try again shortly.",
            headers={"Retry-After": str(retry_after)},
        )

    file = _active_latest_or_404(db, user, file_id)
    db_user = db.get(User, user.id)
    if db_user is None or db_user.plan is None:
        raise HTTPException(status_code=401, detail="Could not validate credentials")
    if not db_user.plan.can_share:
        raise HTTPException(
            status_code=403, detail="Your plan does not allow sharing"
        )

    token, expires = create_share_token(
        version_group=file.version_group, expires_minutes=expires_minutes
    )
    base_url = BASE_URL or str(request.base_url).rstrip("/")
    return {
        "share_url": f"{base_url}/shared.html?token={token}",
        "expires_at": expires,
        "expires_minutes": expires_minutes,
    }


@router.get("/shared/{token}")
def download_shared_file(token: str, db: db_dependency):
    payload = decode_share_token(token)
    version_group = payload["g"]

    file = (
        db.query(FileTable)
        .filter(
            FileTable.version_group == version_group,
            FileTable.deleted_at.is_(None),
            FileTable.is_latest.is_(True),
        )
        .first()
    )
    if file is None:
        raise HTTPException(status_code=404, detail="File not found")

    owner = db.get(User, file.user_id)
    if owner is None or owner.plan is None or not owner.plan.can_share:
        raise HTTPException(
            status_code=403, detail="Sharing is no longer available for this file"
        )

    path = _disk_path_or_404(file.path)
    return FileResponse(
        path=path,
        filename=file.filename,
        media_type="application/octet-stream",
        headers={
            "Cache-Control": "private, no-store",
            "Referrer-Policy": "no-referrer",
        },
    )

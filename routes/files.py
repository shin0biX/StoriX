import os, re, io, zipfile, shutil
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Annotated
from mimetypes import guess_type

from fastapi import Depends, HTTPException, APIRouter, UploadFile, File, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func
from jose import jwt, JWTError

from .auth import get_current_user, get_current_user_optional, SECRET_KEY, ALGORITHM
from database import SessionLocal
from models import User, FileTable, Plan, Folder


router = APIRouter(
    prefix='/files',
    tags=['files']
)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

db_dependency = Annotated[Session, Depends(get_db)]
user_dependency = Annotated[User, Depends(get_current_user)]
user_dependency_optional = Annotated[Optional[User], Depends(get_current_user_optional)]


class FileResponses(BaseModel):
    id: int
    filename: str
    size: int
    path: str
    is_public: bool
    is_starred: bool
    uploaded_at: Optional[datetime]
    folder_id: Optional[int]
    version: int
    deleted_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class FolderResponses(BaseModel):
    id: int
    name: str
    parent_id: Optional[int]

    class Config:
        from_attributes = True


class VisibilityRequest(BaseModel):
    choice: bool


class StarRequest(BaseModel):
    starred: bool


class RenameRequest(BaseModel):
    filename: str


class MoveRequest(BaseModel):
    folder_id: Optional[int] = None


class FolderCreateRequest(BaseModel):
    name: str
    parent_id: Optional[int] = None


class FolderRenameRequest(BaseModel):
    name: str


def sanitize_name(original_name: str) -> str:
    """Clean a filename without making it unique on disk."""
    name = os.path.basename(original_name)
    name = name.replace(" ", "_")
    name = re.sub(r'[^a-zA-Z0-9._-]', '', name)
    return name or "file"


def get_safe_filename(original_name: str, folder: str) -> str:
    name = sanitize_name(original_name)
    base, ext = os.path.splitext(name)
    counter = 1
    final_name = name
    while os.path.exists(os.path.join(folder, final_name)):
        final_name = f"{base}({counter}){ext}"
        counter += 1
    return final_name


def get_versioned_disk_name(original_name: str, folder: str, version: int) -> str:
    """Disk name for a new version of an existing logical file."""
    base, ext = os.path.splitext(original_name)
    candidate = f"{base}(v{version}){ext}"
    counter = 1
    while os.path.exists(os.path.join(folder, candidate)):
        candidate = f"{base}(v{version})({counter}){ext}"
        counter += 1
    return candidate


def owned_file_or_404(db: Session, user: User, file_id: int) -> FileTable:
    file = db.query(FileTable).filter(FileTable.id == file_id).first()
    if not file:
        raise HTTPException(status_code=404, detail="File not found")
    if file.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized")
    return file


def owned_folder_or_404(db: Session, user: User, folder_id: Optional[int]) -> Optional[Folder]:
    if folder_id is None:
        return None
    folder = db.query(Folder).filter(Folder.id == folder_id).first()
    if not folder or folder.user_id != user.id:
        raise HTTPException(status_code=404, detail="Folder not found")
    return folder


def descendant_folder_ids(db: Session, folder: Folder) -> List[int]:
    """All ids in the subtree rooted at folder (excluding folder itself)."""
    ids = []
    stack = [folder.id]
    while stack:
        current = stack.pop()
        children = db.query(Folder).filter(Folder.parent_id == current).all()
        for child in children:
            ids.append(child.id)
            stack.append(child.id)
    return ids


# ---------------- Files listing ----------------

@router.get("/get-files", response_model=list[FileResponses])
async def get_files(user: user_dependency, db: db_dependency):
    files = db.query(FileTable).filter(
        FileTable.user_id == user.id,
        FileTable.deleted_at.is_(None),
        FileTable.is_latest.is_(True),
    ).order_by(FileTable.uploaded_at.desc()).all()
    return files


@router.get("/trash", response_model=list[FileResponses])
async def get_trash(user: user_dependency, db: db_dependency):
    files = db.query(FileTable).filter(
        FileTable.user_id == user.id,
        FileTable.deleted_at.is_not(None),
    ).order_by(FileTable.deleted_at.desc()).all()
    return files


# ---------------- Upload ----------------

@router.post("/upload/")
async def upload_file(
    db: db_dependency,
    user: user_dependency,
    files: List[UploadFile] = File(...),
    folder_id: Optional[int] = None,
):
    target_folder = owned_folder_or_404(db, user, folder_id)
    user_folder = os.path.join("storage", user.username)
    os.makedirs(user_folder, exist_ok=True)

    uploaded = []
    errors = []

    for file in files:
        clean_name = sanitize_name(file.filename or "file")

        # Determine size before touching disk so we can reject early
        file.file.seek(0, 2)
        file_size = file.file.tell()
        file.file.seek(0)

        if file_size > user.plan.max_file_size:
            errors.append({"filename": file.filename, "detail": "File exceeds your plan's max file size"})
            continue
        if user.used_storage + file_size > user.plan.storage_limit:
            errors.append({"filename": file.filename, "detail": "Storage limit exceeded"})
            continue

        # Versioning: same logical name in same folder -> supersede old row
        existing = db.query(FileTable).filter(
            FileTable.user_id == user.id,
            FileTable.filename == clean_name,
            FileTable.folder_id == (target_folder.id if target_folder else None),
            FileTable.deleted_at.is_(None),
            FileTable.is_latest.is_(True),
        ).first()

        if existing:
            new_version = existing.version + 1
            existing.is_latest = False
            disk_name = get_versioned_disk_name(clean_name, user_folder, new_version)
        else:
            new_version = 1
            disk_name = get_safe_filename(clean_name, user_folder)

        file_path = os.path.join(user_folder, disk_name)
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        file_size = os.path.getsize(file_path)

        new_file = FileTable(
            filename=clean_name,
            size=file_size,
            path=file_path,
            user_id=user.id,
            folder_id=target_folder.id if target_folder else None,
            version=new_version,
            is_latest=True,
        )
        # re-fetch in this session: `user` comes from auth's session, so
        # mutating it here would never be committed
        db_user = db.query(User).filter(User.id == user.id).first()
        db_user.used_storage += file_size
        db.add(new_file)
        db.commit()
        db.refresh(new_file)

        uploaded.append({
            "id": new_file.id,
            "filename": new_file.filename,
            "size": new_file.size,
            "version": new_file.version,
        })

    if not uploaded and errors:
        raise HTTPException(status_code=400, detail=errors[0]["detail"])

    return {"uploaded": uploaded, "errors": errors}


# ---------------- Trash / delete ----------------

@router.delete("/file-delete/{file_id}")
async def delete_files(user: user_dependency, db: db_dependency, file_id: int):
    file = owned_file_or_404(db, user, file_id)
    file.deleted_at = datetime.now(timezone.utc)
    db.commit()
    return {"message": "File moved to trash"}


@router.post("/restore/{file_id}")
async def restore_file(user: user_dependency, db: db_dependency, file_id: int):
    file = owned_file_or_404(db, user, file_id)
    if file.deleted_at is None:
        raise HTTPException(status_code=400, detail="File is not in trash")
    # If the parent folder was deleted meanwhile, restore to root
    if file.folder_id is not None:
        folder = db.query(Folder).filter(Folder.id == file.folder_id).first()
        if folder is None:
            file.folder_id = None
    file.deleted_at = None
    db.commit()
    return {"message": "File restored"}


@router.delete("/permanent-delete/{file_id}")
async def permanent_delete(user: user_dependency, db: db_dependency, file_id: int):
    file = owned_file_or_404(db, user, file_id)
    db_user = db.query(User).filter(User.id == user.id).first()
    db_user.used_storage = max(0, db_user.used_storage - file.size)
    db.delete(file)
    db.commit()
    if os.path.exists(file.path):
        os.remove(file.path)
    return {"message": "File permanently deleted"}


@router.post("/trash/empty")
async def empty_trash(user: user_dependency, db: db_dependency):
    trashed = db.query(FileTable).filter(
        FileTable.user_id == user.id,
        FileTable.deleted_at.is_not(None),
    ).all()
    freed = 0
    for file in trashed:
        freed += file.size
        if os.path.exists(file.path):
            os.remove(file.path)
        db.delete(file)
    db_user = db.query(User).filter(User.id == user.id).first()
    db_user.used_storage = max(0, db_user.used_storage - freed)
    db.commit()
    return {"message": f"Trash emptied ({len(trashed)} files)"}


# ---------------- Star / rename / move ----------------

@router.put("/{file_id}/star")
async def toggle_star(file_id: int, request: StarRequest, user: user_dependency, db: db_dependency):
    file = owned_file_or_404(db, user, file_id)
    file.is_starred = request.starred
    db.commit()
    return {"is_starred": file.is_starred}


@router.put("/{file_id}/rename")
async def rename_file(file_id: int, request: RenameRequest, user: user_dependency, db: db_dependency):
    file = owned_file_or_404(db, user, file_id)
    new_name = re.sub(r'[^a-zA-Z0-9._ ()-]', '', request.filename.strip().replace(" ", "_")) or file.filename
    clash = db.query(FileTable).filter(
        FileTable.user_id == user.id,
        FileTable.filename == new_name,
        FileTable.folder_id == file.folder_id,
        FileTable.deleted_at.is_(None),
        FileTable.is_latest.is_(True),
        FileTable.id != file.id,
    ).first()
    if clash:
        raise HTTPException(status_code=400, detail="A file with that name already exists here")
    file.filename = new_name
    db.commit()
    return {"filename": file.filename}


@router.put("/{file_id}/move")
async def move_file(file_id: int, request: MoveRequest, user: user_dependency, db: db_dependency):
    file = owned_file_or_404(db, user, file_id)
    target = owned_folder_or_404(db, user, request.folder_id)
    if target and target.id == file.folder_id:
        return {"message": "File is already in that folder"}
    file.folder_id = target.id if target else None
    db.commit()
    return {"message": "File moved", "folder_id": file.folder_id}


# ---------------- Folders ----------------

@router.get("/folders", response_model=list[FolderResponses])
async def get_folders(user: user_dependency, db: db_dependency):
    folders = db.query(Folder).filter(Folder.user_id == user.id).order_by(Folder.name).all()
    return folders


@router.post("/folders")
async def create_folder(request: FolderCreateRequest, user: user_dependency, db: db_dependency):
    name = request.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Folder name is required")
    parent = owned_folder_or_404(db, user, request.parent_id)

    clash = db.query(Folder).filter(
        Folder.user_id == user.id,
        Folder.name == name,
        Folder.parent_id == (parent.id if parent else None),
    ).first()
    if clash:
        raise HTTPException(status_code=400, detail="A folder with that name already exists here")

    folder = Folder(name=name, parent_id=parent.id if parent else None, user_id=user.id)
    db.add(folder)
    db.commit()
    db.refresh(folder)
    return {"id": folder.id, "name": folder.name, "parent_id": folder.parent_id}


@router.put("/folders/{folder_id}/rename")
async def rename_folder(folder_id: int, request: FolderRenameRequest, user: user_dependency, db: db_dependency):
    folder = owned_folder_or_404(db, user, folder_id)
    name = request.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Folder name is required")
    folder.name = name
    db.commit()
    return {"id": folder.id, "name": folder.name}


@router.delete("/folders/{folder_id}")
async def delete_folder(folder_id: int, user: user_dependency, db: db_dependency):
    folder = owned_folder_or_404(db, user, folder_id)
    now = datetime.now(timezone.utc)

    folder_ids = [folder.id] + descendant_folder_ids(db, folder)
    files = db.query(FileTable).filter(
        FileTable.user_id == user.id,
        FileTable.folder_id.in_(folder_ids),
        FileTable.deleted_at.is_(None),
    ).all()
    for file in files:
        file.deleted_at = now

    # hard-delete the (empty of active files) folder subtree
    for fid in folder_ids:
        f = db.query(Folder).filter(Folder.id == fid, Folder.user_id == user.id).first()
        if f:
            db.delete(f)
    db.commit()
    return {"message": "Folder deleted", "trashed_files": len(files)}


# ---------------- Visibility ----------------

@router.put("/{file_id}/visibility")
async def change_visibility(
    request: VisibilityRequest,
    user: user_dependency,
    db: db_dependency,
    file_id: int,
):
    file = owned_file_or_404(db, user, file_id)

    if request.choice and not user.plan.can_share:
        raise HTTPException(
            status_code=403,
            detail="Your plan does not allow public sharing"
        )

    file.is_public = request.choice
    db.commit()
    db.refresh(file)
    return {"is_public": file.is_public}


# ---------------- Download / preview ----------------

@router.get("/download/{file_id}")
async def download_file(file_id: int, user: user_dependency_optional, db: db_dependency):
    file = db.query(FileTable).filter(FileTable.id == file_id).first()
    if file is None:
        raise HTTPException(status_code=404, detail="File not found")

    if not file.is_public:
        if user is None:
            raise HTTPException(status_code=401, detail="Login required")
        if file.user_id != user.id:
            raise HTTPException(status_code=403, detail="Not authorized")

    if not os.path.exists(file.path):
        raise HTTPException(status_code=404, detail="File missing on server")

    return FileResponse(
        path=file.path,
        filename=file.filename,
        media_type="application/octet-stream"
    )


@router.get("/preview/{file_id}")
async def preview_file(file_id: int, user: user_dependency_optional, db: db_dependency):
    file = db.query(FileTable).filter(FileTable.id == file_id).first()
    if file is None:
        raise HTTPException(status_code=404, detail="File not found")

    if not file.is_public:
        if user is None:
            raise HTTPException(status_code=401, detail="Login required")
        if file.user_id != user.id:
            raise HTTPException(status_code=403, detail="Not authorized")

    if not os.path.exists(file.path):
        raise HTTPException(status_code=404, detail="File missing on server")

    media_type = guess_type(file.filename)[0] or "application/octet-stream"
    return FileResponse(
        path=file.path,
        media_type=media_type,
        headers={"Content-Disposition": f'inline; filename="{file.filename}"'},
    )


@router.get("/download-zip")
async def download_zip(
    ids: str = Query(..., description="Comma-separated file ids"),
    user: user_dependency = None,
    db: db_dependency = None,
):
    id_list = []
    for part in ids.split(","):
        part = part.strip()
        if part.isdigit():
            id_list.append(int(part))
    if not id_list:
        raise HTTPException(status_code=400, detail="No valid file ids provided")

    files = db.query(FileTable).filter(
        FileTable.id.in_(id_list),
        FileTable.user_id == user.id,
        FileTable.deleted_at.is_(None),
    ).all()
    if not files:
        raise HTTPException(status_code=404, detail="No files found")

    buf = io.BytesIO()
    used_names = set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in files:
            if not os.path.exists(file.path):
                continue
            arcname = file.filename
            counter = 1
            while arcname in used_names:
                base, ext = os.path.splitext(file.filename)
                arcname = f"{base}({counter}){ext}"
                counter += 1
            used_names.add(arcname)
            zf.write(file.path, arcname)
    buf.seek(0)

    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="storix-files.zip"'},
    )


# ---------------- Versions ----------------

@router.get("/versions/{file_id}")
async def get_versions(file_id: int, user: user_dependency, db: db_dependency):
    file = owned_file_or_404(db, user, file_id)
    versions = db.query(FileTable).filter(
        FileTable.user_id == user.id,
        FileTable.filename == file.filename,
        FileTable.folder_id == file.folder_id,
        FileTable.deleted_at.is_(None),
    ).order_by(FileTable.version.desc()).all()
    return [
        {
            "id": v.id,
            "version": v.version,
            "size": v.size,
            "uploaded_at": v.uploaded_at,
            "is_latest": v.is_latest,
        }
        for v in versions
    ]


@router.post("/versions/{file_id}/restore/{version_id}")
async def restore_version(file_id: int, version_id: int, user: user_dependency, db: db_dependency):
    current = owned_file_or_404(db, user, file_id)
    target = owned_file_or_404(db, user, version_id)
    if target.filename != current.filename or target.folder_id != current.folder_id:
        raise HTTPException(status_code=400, detail="Version does not belong to this file")

    current.is_latest = False
    target.is_latest = True
    db.commit()
    return {"message": f"Restored version {target.version}"}


# ---------------- Sharing ----------------

@router.post("/share/{file_id}")
async def create_share_link(request: Request, user: user_dependency, db: db_dependency, file_id: int, expires_minutes: int = 30):
    file = owned_file_or_404(db, user, file_id)

    if not user.plan.can_share:
        raise HTTPException(status_code=403, detail="Your plan does not allow sharing")

    expire = datetime.now(timezone.utc) + timedelta(minutes=expires_minutes)
    payload = {
        'file_id': file_id,
        'type': "share",
        'exp': expire
    }
    token = jwt.encode(payload, SECRET_KEY, ALGORITHM)

    base_url = str(request.base_url).rstrip("/")
    return {
        "share_url": f"{base_url}/shared.html?token={token}",
        "expires_at": expire
    }


@router.get("/shared/{token}")
async def download_shared_file(token: str, db: db_dependency):
    try:
        payload = jwt.decode(token, SECRET_KEY, ALGORITHM)
        if payload.get("type") != 'share':
            raise HTTPException(status_code=400, detail="Invalid token")
        file_id = payload.get('file_id')
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Link expired")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

    file = db.query(FileTable).filter(FileTable.id == file_id).first()
    if not file:
        raise HTTPException(status_code=404, detail="File not found")

    if not os.path.exists(file.path):
        raise HTTPException(status_code=404, detail="File missing")

    return FileResponse(
        path=file.path,
        filename=file.filename,
        media_type="application/octet-stream"
    )

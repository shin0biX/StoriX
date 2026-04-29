from fastapi import Depends,HTTPException,Path,APIRouter,Request,status,UploadFile,File
from models import User,FileTable,Plan
from database import SessionLocal
from typing import Annotated
from sqlalchemy.orm import Session
from starlette import status
from pydantic import BaseModel
from .auth import get_current_user , get_current_user_optional
from starlette.responses import RedirectResponse
import os,re
import shutil
from fastapi.responses import FileResponse
from datetime import datetime,timezone,timedelta
from jose import JWTError,jwt,ExpiredSignatureError



SECRET_KEY = 'df6f0d0a3add6756944ce0cc1de56d756d8fb17b309d63704a3224aeb0e375f7'
ALGORITHM = 'HS256'



router = APIRouter(
    prefix='/files',
    tags=['files']
)

def get_db():
    db= SessionLocal()
    try:
        yield db
    finally:
        db.close()

db_dependency = Annotated[Session,Depends(get_db)]
user_dependency = Annotated[User, Depends(get_current_user)]
user_dependency_optional = Annotated[User | None, Depends(get_current_user_optional)]

class FileResponses(BaseModel):
    id: int
    filename: str
    size: int
    path: str
    is_public : bool

    class Config:
        from_attributes = True


class VisibilityRequest(BaseModel):
    choice: bool


def get_safe_filename(original_name : str, folder:str) -> str:
    
    name = os.path.basename(original_name)
    
    name = name.replace(" " , "_")
    
    name = re.sub(r'[^a-zA-Z0-9._-]', '', name)
    
    if not name:
        name = "file"

    base,ext = os.path.splitext(name)
    
    counter =1
    
    final_name = name
    
    while(os.path.exists(os.path.join(folder, final_name))):
        final_name = f"{base}({counter}){ext}"
        counter +=1
        
    return final_name


def redirect_to_login():
    redirect_response = RedirectResponse(url="/auth/login-page", status_code=status.HTTP_302_FOUND)
    redirect_response.delete_cookie(key='access_token')
    return redirect_response

@router.get("/get-files", response_model=list[FileResponses])
async def get_files(user:user_dependency,db:db_dependency):
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")

    file_model = db.query(FileTable).filter(FileTable.user_id == user.id).all()
    
    return file_model


@router.post("/upload/")
async def upload_file(
    db: db_dependency,
    user: user_dependency,
    file: UploadFile = File(...)
):
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")

    # create user folder if not exists
    user_folder = os.path.join("storage", user.username)
    os.makedirs(user_folder, exist_ok=True)

    safe_name = get_safe_filename(file.filename,user_folder)
    
    
    file_path = os.path.join(user_folder, safe_name)

    # save file
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    # get file size
    file_size = os.path.getsize(file_path)
    
    user_model = db.query(User).filter(User.id == user.id).first()
    if user_model is None:
        raise HTTPException(status_code=404, detail="user not found")
    
    if user_model.used_storage + file_size > user_model.plan.storage_limit:
        user_model
        raise HTTPException(status_code=400, detail="Storage limit exceeded")
    
    
    # create DB entry
    new_file = FileTable(
        filename=safe_name,
        size=file_size,
        path=file_path,
        user_id=user.id
    )

    user_model.used_storage+=file_size
    
    db.add(new_file)
    db.commit()
    db.refresh(new_file)

    return {
        "filename": new_file.filename,
        "path": new_file.path,
        "size": new_file.size
    }
    
@router.delete("/file-delete/{file_id}")
async def delete_files(user:user_dependency,db:db_dependency, file_id:int):
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")
    
    file = db.query(FileTable).filter(FileTable.id == file_id).first()
    if not file:
        raise HTTPException(status_code=404, detail="File not found")
    
    if file.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    
        
    user_model = db.query(User).filter(User.id == user.id).first()
    if user_model is None:
        raise HTTPException(status_code=404, detail="user not found")
    user_model.used_storage -= file.size
    if user_model.used_storage<0:
        user_model.used_storage=0

    db.delete(file)
    db.commit()
    
    if os.path.exists(file.path):
        os.remove(file.path)

    return {"message": "File deleted successfully"}
    
    
@router.get("/download/{file_id}")
async def download_file(file_id:int, user:user_dependency_optional, db:db_dependency):
    
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
        path = file.path,
        filename= file.filename,
        media_type="application/octet-stream"
    )
    
@router.put("/{file_id}/visibility")
async def change_visibility(
    request: VisibilityRequest,
    user: user_dependency,
    db: db_dependency,
    file_id: int,
):
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")

    file_model = db.query(FileTable).filter(FileTable.id == file_id).first()

    if file_model is None:
        raise HTTPException(status_code=404, detail="File not found")

    if file_model.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized")

    if request.choice and not user.plan.can_share:
        raise HTTPException(
            status_code=403,
            detail="Your plan does not allow public sharing"
        )

    file_model.is_public = request.choice

    db.commit()
    db.refresh(file_model)
    return {
        "is_public": file_model.is_public
    }
    
    
    
@router.post("/share/{file_id}")
async def create_share_link(user:user_dependency,db:db_dependency,file_id:int , expires_minutes: int = 30):
    
    file = db.query(FileTable).filter(FileTable.id == file_id).first()

    if not file:
        raise HTTPException(status_code=404, detail="File not found")

    if file.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    expire = datetime.now(timezone.utc) + timedelta(minutes=expires_minutes)
    
    payload = {
        'file_id' : file_id,
        'type' : "share",
        'exp' : expire
    }
    
    token = jwt.encode(payload,SECRET_KEY,ALGORITHM)
    
    return {
        "share_url": f"http://127.0.0.1:8000/files/shared/{token}",
        "expires_at": expire
    }
    
    

@router.get("/shared/{token}")
async def download_shared_file(token:str , db:db_dependency):
    
    try:
        payload = jwt.decode(token,SECRET_KEY,ALGORITHM)
        
        if payload.get("type") != 'share':
            raise HTTPException(status_code=400, detail="Invalid token")
        
        file_id = payload.get('file_id')
        
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Link expired")

    except jwt.JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    file = db.query(FileTable).filter(FileTable.id == file_id).first()
    
    if not file:
        raise HTTPException(status_code=404, detail="File not found")
    
    if not os.path.exists(file.path):
        raise HTTPException(status_code=404, detail="File missing")
    
    return FileResponse(
        path = file.path,
        filename = file.filename,
        media_type="application/octet-stream"
        
    )
    
    
        
    
    
        
    
    
from fastapi import Depends,HTTPException,Path,APIRouter,Request,status,UploadFile,File
from models import User,FileTable
from database import SessionLocal
from typing import Annotated
from sqlalchemy.orm import Session
from starlette import status
from pydantic import BaseModel
from .auth import get_current_user
from starlette.responses import RedirectResponse
import os
import shutil


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

class FileResponse(BaseModel):
    id: int
    filename: str
    size: int
    path: str

    class Config:
        from_attributes = True




def redirect_to_login():
    redirect_response = RedirectResponse(url="/auth/login-page", status_code=status.HTTP_302_FOUND)
    redirect_response.delete_cookie(key='access_token')
    return redirect_response

@router.get("/get-files", response_model=list[FileResponse])
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

    file_path = os.path.join(user_folder, file.filename)

    # save file
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    # get file size
    file_size = os.path.getsize(file_path)
    
    user_model = db.query(User).filter(User.id == user.id).first()
    if user_model is None:
        raise HTTPException(status_code=404, detail="user not found")
    
    if user_model.used_storage + file_size > user_model.storage_limit:
        user_model
        raise HTTPException(status_code=400, detail="Storage limit exceeded")
    
    
    # create DB entry
    new_file = FileTable(
        filename=file.filename,
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
    
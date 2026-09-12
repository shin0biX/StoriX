from fastapi import APIRouter, Depends, HTTPException
from typing import Annotated
from sqlalchemy import func
from sqlalchemy.orm import Session
from pydantic import BaseModel

from database import SessionLocal
from models import User, Plan, FileTable
from .auth import get_current_user


router = APIRouter(
    prefix='/admin',
    tags=['admin']
)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

db_dependency = Annotated[Session, Depends(get_db)]
user_dependency = Annotated[User, Depends(get_current_user)]


def require_admin(user: User):
    if user is None or user.role != 'admin':
        raise HTTPException(status_code=403, detail="Admin access required")


class AdminPlanRequest(BaseModel):
    plan: str


@router.get("/users")
async def list_users(db: db_dependency, user: user_dependency):
    require_admin(user)
    users = db.query(User).all()
    return [
        {
            "id": u.id,
            "username": u.username,
            "email": u.email,
            "full_name": u.full_name,
            "role": u.role,
            "used_storage": u.used_storage,
            "file_count": db.query(FileTable).filter(FileTable.user_id == u.id).count(),
            "plan": u.plan.name,
        }
        for u in users
    ]


@router.put("/users/{user_id}/plan")
async def change_user_plan(user_id: int, request: AdminPlanRequest, db: db_dependency, user: user_dependency):
    require_admin(user)
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    plan = db.query(Plan).filter(func.lower(Plan.name) == request.plan.strip().lower()).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    target.plan_id = plan.id
    db.commit()
    return {"message": f"{target.username} moved to {plan.name}"}


@router.delete("/users/{user_id}")
async def delete_user(user_id: int, db: db_dependency, user: user_dependency):
    require_admin(user)
    if user_id == user.id:
        raise HTTPException(status_code=400, detail="You cannot delete your own account")
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    username = target.username
    import os
    files = db.query(FileTable).filter(FileTable.user_id == target.id).all()
    for f in files:
        if os.path.exists(f.path):
            os.remove(f.path)
    from models import Folder
    db.query(FileTable).filter(FileTable.user_id == target.id).delete(synchronize_session=False)
    db.query(Folder).filter(Folder.user_id == target.id).delete(synchronize_session=False)
    db.delete(target)
    db.commit()
    return {"message": f"User {username} deleted"}

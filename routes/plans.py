from fastapi import APIRouter,Depends,HTTPException
from database import SessionLocal
from typing import Annotated
from models import User,Plan
from sqlalchemy.orm import Session
from .auth import get_current_user
from sqlalchemy import func
from pydantic import BaseModel

router = APIRouter(
    prefix='/plans',
    tags=['plans']
)


def get_db():
    db= SessionLocal()
    try:
        yield db
    finally:
        db.close()

db_dependency = Annotated[Session,Depends(get_db)]
user_dependency = Annotated[User, Depends(get_current_user)]


class ChangePlanRequest(BaseModel):
    plan: str


@router.put("/change-plan")
def change_plan(request: ChangePlanRequest, db:db_dependency, user:user_dependency):
    
    if user is None:
       raise HTTPException(status_code=401, detail="Not authorized") 
    
    plan_model = db.query(Plan).filter(
        func.lower(Plan.name) == request.plan.strip().lower()
    ).first()
    if plan_model is None:
        raise HTTPException(status_code=404, detail="Plan Not Found")
    
    db_user = db.query(User).filter(User.id == user.id).first()
    if db_user is None:
        raise HTTPException(status_code=404, detail="User not found")
    
    if db_user.plan_id == plan_model.id:
        return {"message" : "Same plan selected"}
    
    db_user.plan_id = plan_model.id

    db.commit()
    db.refresh(db_user)
    return {
        "message" : "Plan changed successfully"
    }
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func
from sqlalchemy.orm import Session

from config import ALLOW_SELF_PLAN_CHANGE
from database import get_db
from models import FileTable, Plan, User
from routes.auth import get_current_user

router = APIRouter(prefix="/plans", tags=["plans"])

db_dependency = Annotated[Session, Depends(get_db)]
user_dependency = Annotated[User, Depends(get_current_user)]


class ChangePlanRequest(BaseModel):
    plan: str = Field(min_length=1, max_length=40)

    @field_validator("plan", mode="before")
    @classmethod
    def strip_plan(cls, value):  # noqa: ANN001
        return value.strip() if isinstance(value, str) else value


@router.get("/")
def list_plans(db: db_dependency):
    plans = db.query(Plan).order_by(Plan.price, Plan.id).all()
    return [
        {
            "id": plan.id,
            "name": plan.name,
            "storage_limit": plan.storage_limit,
            "max_file_size": plan.max_file_size,
            "can_share": plan.can_share,
            "price": plan.price,
        }
        for plan in plans
    ]


@router.put("/change-plan")
def change_plan(
    request: ChangePlanRequest,
    db: db_dependency,
    user: user_dependency,
):
    if not ALLOW_SELF_PLAN_CHANGE and user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Plan changes require an administrator",
        )

    plan_model = (
        db.query(Plan)
        .filter(func.lower(Plan.name) == request.plan.strip().lower())
        .first()
    )
    if plan_model is None:
        raise HTTPException(status_code=404, detail="Plan not found")

    db_user = db.get(User, user.id)
    if db_user is None:
        raise HTTPException(status_code=404, detail="User not found")

    if db_user.plan_id == plan_model.id:
        return {"message": "You are already on this plan", "plan": plan_model.name}

    if db_user.used_storage > plan_model.storage_limit:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Your stored data exceeds this plan's limit. "
                "Delete files before downgrading."
            ),
        )

    db_user.plan_id = plan_model.id
    if not plan_model.can_share:
        # A downgraded account must not retain publicly reachable files.
        db.query(FileTable).filter(FileTable.user_id == db_user.id).update(
            {FileTable.is_public: False}, synchronize_session=False
        )
    db.commit()
    return {
        "message": f"Plan changed to {plan_model.name}",
        "plan": plan_model.name,
    }

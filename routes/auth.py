from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import ExpiredSignatureError, JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from config import (
    ACCESS_TOKEN_AUDIENCE,
    ACCESS_TOKEN_MINUTES,
    ALGORITHM,
    ALLOW_SELF_PLAN_CHANGE,
    LOGIN_RATE_LIMIT,
    LOGIN_RATE_WINDOW_SECONDS,
    REFRESH_ROTATION_GRACE_SECONDS,
    REFRESH_TOKEN_DAYS,
    REGISTER_RATE_LIMIT,
    REGISTER_RATE_WINDOW_SECONDS,
    SECRET_KEY,
    SHARE_SECRET_KEY,
    SHARE_TOKEN_AUDIENCE,
    TOKEN_ISSUER,
)
from database import get_db
from models import Plan, RefreshToken, User, utcnow
from ratelimit import SlidingWindowLimiter, client_key

logger = logging.getLogger("storix.auth")

router = APIRouter(prefix="/auth", tags=["auth"])

db_dependency = Annotated[Session, Depends(get_db)]

bcrypt_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
# Verifying against this hash when the account does not exist keeps the login
# path's timing close to the real-password path.
DUMMY_PASSWORD_HASH = bcrypt_context.hash("storix-dummy-password")
oauth2_bearer = OAuth2PasswordBearer(tokenUrl="/auth/token")
oauth2_bearer_optional = OAuth2PasswordBearer(
    tokenUrl="/auth/token", auto_error=False
)

login_ip_limiter = SlidingWindowLimiter(LOGIN_RATE_LIMIT, LOGIN_RATE_WINDOW_SECONDS)
login_user_limiter = SlidingWindowLimiter(
    LOGIN_RATE_LIMIT, LOGIN_RATE_WINDOW_SECONDS
)
register_limiter = SlidingWindowLimiter(
    REGISTER_RATE_LIMIT, REGISTER_RATE_WINDOW_SECONDS
)

USERNAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,31}$"


class CreateUserRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32, pattern=USERNAME_PATTERN)
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("username", "full_name", mode="before")
    @classmethod
    def strip_text(cls, value):  # noqa: ANN001
        return value.strip() if isinstance(value, str) else value

    @field_validator("username")
    @classmethod
    def reject_path_dots(cls, value: str) -> str:
        if value in {".", ".."} or ".." in value:
            raise ValueError("Username cannot contain path traversal sequences")
        return value

    @field_validator("password")
    @classmethod
    def bcrypt_byte_limit(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 72:
            raise ValueError("Password must be at most 72 bytes")
        return value


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=20, max_length=4096)


def _credentials_error(detail: str = "Could not validate credentials") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _create_token(
    *,
    username: str,
    user_id: int,
    role: str,
    token_type: str,
    expires_delta: timedelta,
    jti: str | None = None,
) -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    token_id = jti or uuid4().hex
    payload = {
        "sub": username,
        "id": user_id,
        "role": role,
        "type": token_type,
        "iat": now,
        "nbf": now,
        "exp": now + expires_delta,
        "jti": token_id,
        "iss": TOKEN_ISSUER,
        "aud": ACCESS_TOKEN_AUDIENCE,
    }
    token = jwt.encode(payload, SECRET_KEY, ALGORITHM)
    return token, token_id


def create_access_token(
    *,
    username: str,
    user_id: int,
    role: str,
    expires_delta: timedelta | None = None,
) -> str:
    token, _ = _create_token(
        username=username,
        user_id=user_id,
        role=role,
        token_type="access",
        expires_delta=expires_delta or timedelta(minutes=ACCESS_TOKEN_MINUTES),
    )
    return token


def _create_refresh_token(
    *, username: str, user_id: int, role: str
) -> tuple[str, str, datetime]:
    expires = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_DAYS)
    token, jti = _create_token(
        username=username,
        user_id=user_id,
        role=role,
        token_type="refresh",
        expires_delta=timedelta(days=REFRESH_TOKEN_DAYS),
    )
    return token, jti, expires


def create_share_token(*, version_group: str, expires_minutes: int) -> tuple[str, datetime]:
    now = datetime.now(timezone.utc)
    expires = now + timedelta(minutes=expires_minutes)
    payload = {
        "g": version_group,
        "type": "share",
        "iat": now,
        "exp": expires,
        "jti": uuid4().hex,
        "iss": TOKEN_ISSUER,
        "aud": SHARE_TOKEN_AUDIENCE,
    }
    return jwt.encode(payload, SHARE_SECRET_KEY, ALGORITHM), expires


def decode_share_token(token: str) -> dict:
    try:
        payload = jwt.decode(
            token,
            SHARE_SECRET_KEY,
            algorithms=[ALGORITHM],
            audience=SHARE_TOKEN_AUDIENCE,
            issuer=TOKEN_ISSUER,
        )
    except ExpiredSignatureError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Link expired"
        ) from exc
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
        ) from exc

    if payload.get("type") != "share" or not payload.get("g"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
        )
    return payload


def _decode_user_token(token: str, expected_type: str) -> dict:
    try:
        payload = jwt.decode(
            token,
            SECRET_KEY,
            algorithms=[ALGORITHM],
            audience=ACCESS_TOKEN_AUDIENCE,
            issuer=TOKEN_ISSUER,
        )
    except ExpiredSignatureError as exc:
        raise _credentials_error("Token expired") from exc
    except JWTError as exc:
        raise _credentials_error() from exc

    if payload.get("type") != expected_type:
        raise _credentials_error()
    if not isinstance(payload.get("id"), int):
        raise _credentials_error()
    return payload


def authenticate_user(username: str, password: str, db: Session) -> User | None:
    user = (
        db.query(User)
        .filter(func.lower(User.username) == username.strip().lower())
        .first()
    )
    if not user:
        try:
            bcrypt_context.verify(password, DUMMY_PASSWORD_HASH)
        except (ValueError, TypeError):
            pass
        return None
    try:
        valid = bcrypt_context.verify(password, user.hashed_password)
    except (ValueError, TypeError):
        valid = False
    return user if valid else None


async def get_current_user_optional(
    token: Annotated[str | None, Depends(oauth2_bearer_optional)],
    db: db_dependency,
) -> User | None:
    if not token:
        return None
    try:
        payload = _decode_user_token(token, "access")
    except HTTPException:
        return None
    return db.get(User, payload["id"])


async def get_current_user(
    token: Annotated[str, Depends(oauth2_bearer)],
    db: db_dependency,
) -> User:
    payload = _decode_user_token(token, "access")
    user = db.get(User, payload["id"])
    if user is None:
        raise _credentials_error()
    return user


def _issue_session(db: Session, user: User) -> dict:
    """Create an access token and a persisted, rotating refresh token."""
    # Expired sessions can never be replayed. Removing them on login/refresh
    # keeps the table from growing without bound.
    db.query(RefreshToken).filter(
        RefreshToken.user_id == user.id,
        RefreshToken.expires_at <= utcnow(),
    ).delete(synchronize_session=False)
    refresh_token, jti, expires = _create_refresh_token(
        username=user.username, user_id=user.id, role=user.role
    )
    db.add(
        RefreshToken(
            jti=jti,
            user_id=user.id,
            expires_at=expires,
        )
    )
    db.commit()
    return {
        "access_token": create_access_token(
            username=user.username, user_id=user.id, role=user.role
        ),
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "expires_in": ACCESS_TOKEN_MINUTES * 60,
    }


@router.post("/", status_code=status.HTTP_201_CREATED)
def create_user(
    create_user_request: CreateUserRequest,
    request: Request,
    db: db_dependency,
):
    retry_after = register_limiter.hit(client_key(request))
    if retry_after is not None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many registration attempts. Try again later.",
            headers={"Retry-After": str(retry_after)},
        )

    username = create_user_request.username
    email = str(create_user_request.email).strip().lower()

    if (
        db.query(User)
        .filter(func.lower(User.username) == username.lower())
        .first()
    ):
        raise HTTPException(status_code=409, detail="Username is already taken")
    if db.query(User).filter(func.lower(User.email) == email).first():
        raise HTTPException(status_code=409, detail="Email is already registered")

    # The client never chooses a plan. New accounts always start on Free.
    plan_model = db.query(Plan).filter(func.lower(Plan.name) == "free").first()
    if plan_model is None:
        logger.error("Free plan is missing; cannot register users")
        raise HTTPException(status_code=500, detail="Default plan not configured")

    new_user = User(
        email=email,
        full_name=create_user_request.full_name.strip(),
        username=username,
        hashed_password=bcrypt_context.hash(create_user_request.password),
        plan_id=plan_model.id,
        role="user",
    )
    db.add(new_user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="Username or email is already registered"
        )
    db.refresh(new_user)
    return {"message": "User created successfully", "user_id": new_user.id}


@router.post("/token")
def login_for_access_token(
    request: Request,
    response: Response,
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: db_dependency,
):
    username = form_data.username.strip().lower()
    ip_key = client_key(request)
    user_key = f"{ip_key}:{username}"

    retry_after = login_ip_limiter.hit(ip_key)
    if retry_after is None:
        retry_after = login_user_limiter.hit(user_key)
    if retry_after is not None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Try again later.",
            headers={"Retry-After": str(retry_after)},
        )

    user = authenticate_user(form_data.username, form_data.password, db)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    login_user_limiter.reset(user_key)
    login_ip_limiter.reset(ip_key)
    response.headers["Cache-Control"] = "no-store"
    return _issue_session(db, user)


@router.post("/refresh")
def refresh_access_token(payload: RefreshRequest, response: Response, db: db_dependency):
    data = _decode_user_token(payload.refresh_token, "refresh")
    jti = data.get("jti")
    if not isinstance(jti, str):
        raise _credentials_error()

    token_row = db.query(RefreshToken).filter(RefreshToken.jti == jti).first()
    now = utcnow()
    if token_row is None or token_row.user_id != data["id"]:
        raise _credentials_error("Refresh token is no longer valid")
    if token_row.expires_at <= now:
        raise _credentials_error("Refresh token expired")

    if token_row.revoked_at is not None:
        grace = timedelta(seconds=REFRESH_ROTATION_GRACE_SECONDS)
        if now - token_row.revoked_at > grace:
            raise _credentials_error("Refresh token has already been used")
    else:
        token_row.revoked_at = now

    user = db.get(User, data["id"])
    if user is None:
        raise _credentials_error()

    response.headers["Cache-Control"] = "no-store"
    return _issue_session(db, user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    db: db_dependency,
    payload: RefreshRequest | None = None,
) -> Response:
    """Best-effort revocation. Clients may also simply discard their tokens."""
    if payload is not None:
        try:
            data = jwt.decode(
                payload.refresh_token,
                SECRET_KEY,
                algorithms=[ALGORITHM],
                audience=ACCESS_TOKEN_AUDIENCE,
                issuer=TOKEN_ISSUER,
                options={"verify_exp": False},
            )
            jti = data.get("jti")
            if isinstance(jti, str):
                # Delete rather than mark revoked: a revoked row inside the
                # rotation grace window would still be accepted, which is not
                # what an explicit logout means.
                db.query(RefreshToken).filter(
                    RefreshToken.jti == jti,
                ).delete(synchronize_session=False)
                db.commit()
        except JWTError:
            pass
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me")
def read_current_user(
    db: db_dependency,
    current_user: Annotated[User, Depends(get_current_user)],
):
    user = db.get(User, current_user.id)
    if user is None:
        raise _credentials_error()
    plan = user.plan
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role,
        "used_storage": user.used_storage,
        "can_change_plan": ALLOW_SELF_PLAN_CHANGE or user.role == "admin",
        "created_at": user.created_at,
        "plan": {
            "id": plan.id,
            "name": plan.name,
            "storage_limit": plan.storage_limit,
            "max_file_size": plan.max_file_size,
            "can_share": plan.can_share,
            "price": plan.price,
        },
    }

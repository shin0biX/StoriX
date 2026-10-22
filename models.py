from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from database import Base


class UTCDateTime(TypeDecorator):
    """Store UTC as naive datetimes (SQLite has no tz support) and always
    return timezone-aware UTC values to the application."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect):  # noqa: ANN001
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect):  # noqa: ANN001
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(320), unique=True, nullable=False)
    username = Column(String(32), unique=True, nullable=False, index=True)
    full_name = Column(String(120), nullable=False)
    hashed_password = Column(String(255), nullable=False)
    used_storage = Column(BigInteger, default=0, nullable=False)
    role = Column(String(20), default="user", nullable=False)
    plan_id = Column(
        Integer, ForeignKey("plans.id", ondelete="RESTRICT"), nullable=False
    )
    created_at = Column(UTCDateTime, default=utcnow, nullable=False)

    plan = relationship("Plan", back_populates="users")
    files = relationship(
        "FileTable",
        back_populates="owner",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    folders = relationship(
        "Folder",
        back_populates="owner",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    refresh_tokens = relationship(
        "RefreshToken",
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class Folder(Base):
    __tablename__ = "folders"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(120), nullable=False)
    parent_id = Column(
        Integer, ForeignKey("folders.id", ondelete="CASCADE"), nullable=True
    )
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_at = Column(UTCDateTime, default=utcnow, nullable=False)

    owner = relationship("User", back_populates="folders")
    files = relationship(
        "FileTable", back_populates="folder", passive_deletes=True
    )

    __table_args__ = (
        Index("ix_folders_user_parent", "user_id", "parent_id"),
    )


class FileTable(Base):
    __tablename__ = "filetable"

    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String(255), nullable=False)
    size = Column(BigInteger, nullable=False)
    # Path is always relative to STORAGE_ROOT for new uploads. Legacy rows are
    # resolved defensively in storage.resolve_stored_path().
    path = Column(String(512), nullable=False)
    uploaded_at = Column(UTCDateTime, default=utcnow, nullable=False, index=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    owner = relationship("User", back_populates="files")
    is_public = Column(Boolean, default=False, nullable=False)

    # trash / organization / versioning
    deleted_at = Column(UTCDateTime, nullable=True)
    is_starred = Column(Boolean, default=False, nullable=False)
    folder_id = Column(
        Integer, ForeignKey("folders.id", ondelete="SET NULL"), nullable=True
    )
    folder = relationship("Folder", back_populates="files")
    version = Column(Integer, default=1, nullable=False)
    is_latest = Column(Boolean, default=True, nullable=False)
    # Stable identity shared by every version of a logical file. Never derived
    # from the mutable (filename, folder) pair.
    version_group = Column(
        String(32), nullable=False, default=lambda: uuid.uuid4().hex
    )

    __table_args__ = (
        Index("ix_filetable_user_active", "user_id", "deleted_at", "is_latest"),
        Index("ix_filetable_group", "version_group"),
        Index("ix_filetable_folder", "folder_id"),
        UniqueConstraint("version_group", "version", name="uq_file_group_version"),
    )


class Plan(Base):
    __tablename__ = "plans"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(40), unique=True, nullable=False)
    storage_limit = Column(BigInteger, nullable=False)
    max_file_size = Column(BigInteger, nullable=False)
    can_share = Column(Boolean, default=False, nullable=False)
    price = Column(Integer, default=0, nullable=False)

    users = relationship("User", back_populates="plan")


class RefreshToken(Base):
    """One persisted refresh-token session.

    Access tokens stay stateless. Refresh tokens are rotated on use, and the
    row lets logout/rotation invalidate a token instead of trusting an
    unrevocable JWT for two weeks.
    """

    __tablename__ = "refresh_tokens"

    id = Column(Integer, primary_key=True)
    jti = Column(String(64), unique=True, nullable=False, index=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    expires_at = Column(UTCDateTime, nullable=False, index=True)
    created_at = Column(UTCDateTime, default=utcnow, nullable=False)
    revoked_at = Column(UTCDateTime, nullable=True)

    user = relationship("User", back_populates="refresh_tokens")

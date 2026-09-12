from database import Base
from sqlalchemy import Column,String,Integer,ForeignKey,DateTime,Boolean
from sqlalchemy.orm import relationship
from datetime import datetime,timezone


class User(Base):
    __tablename__ = "users"
    id = Column(Integer,primary_key=True, index=True)
    email = Column(String,unique=True,nullable=False)
    username = Column(String,unique=True,nullable=False)
    full_name = Column(String)
    hashed_password = Column(String,nullable=False)
    used_storage=Column(Integer,default=0)
    role = Column(String, default='user')
    plan_id = Column(Integer,ForeignKey("plans.id"),nullable=False)
    plan = relationship("Plan" , back_populates="users")
    files = relationship("FileTable", back_populates="owner")
    folders = relationship("Folder", back_populates="owner")


class Folder(Base):
    __tablename__ = "folders"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    parent_id = Column(Integer, ForeignKey("folders.id"), nullable=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    owner = relationship("User", back_populates="folders")
    files = relationship("FileTable", back_populates="folder")


class FileTable(Base):
    __tablename__ = "filetable"
    id = Column(Integer,primary_key=True, index=True)
    filename = Column(String, nullable=False)
    size = Column(Integer, nullable=False)
    path = Column(String,nullable=False)
    uploaded_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    user_id = Column(Integer, ForeignKey("users.id"),nullable=False)
    owner = relationship("User" , back_populates="files")
    is_public = Column(Boolean,default=False)

    # trash / organization / versioning
    deleted_at = Column(DateTime, nullable=True)          # set when file is in trash
    is_starred = Column(Boolean, default=False)
    folder_id = Column(Integer, ForeignKey("folders.id"), nullable=True)
    folder = relationship("Folder", back_populates="files")
    version = Column(Integer, default=1)                  # 1 for the first upload of a name
    is_latest = Column(Boolean, default=True)             # False for superseded versions


class Plan(Base):

    __tablename__ = "plans"
    id = Column(Integer, primary_key=True, index=True)

    # Plan identity
    name = Column(String, unique=True, nullable=False)  # e.g. Free, Pro, Premium

    # Limits
    storage_limit = Column(Integer, nullable=False)     # total storage (bytes)
    max_file_size = Column(Integer, nullable=False)     # per file limit (bytes)

    # Feature flags
    can_share = Column(Boolean, default=False)

    price = Column(Integer, default=0)

    # Relationship
    users = relationship("User", back_populates="plan")

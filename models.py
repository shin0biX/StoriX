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
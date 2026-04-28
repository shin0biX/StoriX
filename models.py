from database import Base
from sqlalchemy import Column,String,Integer,ForeignKey,DateTime
from sqlalchemy.orm import relationship
from datetime import datetime,timezone


class User(Base):
    __tablename__ = "users"
    id = Column(Integer,primary_key=True, index=True)
    email = Column(String,unique=True)
    username = Column(String,unique=True)
    full_name = Column(String)
    hashed_password = Column(String)
    role = Column(String, default='user')
    plan = Column(String, default='basic')
    storage_limit = Column(Integer, default=1024 * 1024 * 1024)
    used_storage = Column(Integer, default=0)
    files = relationship("FileTable", back_populates="owner")
    
class FileTable(Base):
    __tablename__ = "filetable"
    id = Column(Integer,primary_key=True, index=True)
    filename = Column(String, nullable=False)
    size = Column(Integer, nullable=False)
    path = Column(String,nullable=False)
    uploaded_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    user_id = Column(Integer, ForeignKey("users.id"))
    owner = relationship("User" , back_populates="files")
    
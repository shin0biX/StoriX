from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()

DATABASE_URL = "sqlite:///./hosting.db"

engine = create_engine(DATABASE_URL , connect_args={'check_same_thread': False})
SessionLocal = sessionmaker(autocommit=False , autoflush=False , bind=engine)
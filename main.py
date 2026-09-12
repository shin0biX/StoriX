from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import models
from database import engine, SessionLocal
from routes import auth, files, plans, admin
from models import Plan
from sqlalchemy import text

app = FastAPI(title="StoriX")



app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # for development
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def seed_plans():
    db = SessionLocal()
    try:
        if db.query(Plan).count() == 0:
            free_plan = Plan(name="Free", storage_limit=1024*1024*1024, max_file_size=100*1024*1024, can_share=False, price=0)
            pro_plan = Plan(name="Pro", storage_limit=10*1024*1024*1024, max_file_size=1000*1024*1024, can_share=True, price=10)
            premium_plan = Plan(name="Premium", storage_limit=100*1024*1024*1024, max_file_size=10000*1024*1024, can_share=True, price=20)
            db.add_all([free_plan, pro_plan, premium_plan])
            db.commit()
    finally:
        db.close()

def run_migrations():
    """Lightweight column migrations for existing SQLite databases.
    create_all() only creates missing tables, not missing columns."""
    stmts = [
        "ALTER TABLE filetable ADD COLUMN deleted_at DATETIME",
        "ALTER TABLE filetable ADD COLUMN is_starred BOOLEAN DEFAULT 0",
        "ALTER TABLE filetable ADD COLUMN folder_id INTEGER",
        "ALTER TABLE filetable ADD COLUMN version INTEGER DEFAULT 1",
        "ALTER TABLE filetable ADD COLUMN is_latest BOOLEAN DEFAULT 1",
    ]
    with engine.connect() as conn:
        for stmt in stmts:
            try:
                conn.execute(text(stmt))
                conn.commit()
            except Exception:
                pass  # column already exists

models.Base.metadata.create_all(bind=engine)
run_migrations()
seed_plans()

app.include_router(router=auth.router)
app.include_router(router=files.router)
app.include_router(router=plans.router)
app.include_router(router=admin.router)

import os
if not os.path.exists("frontend"):
    os.makedirs("frontend")

app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")

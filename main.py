from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import models
from database import engine, SessionLocal
from routes import auth, files, plans
from models import Plan

app = FastAPI()



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

models.Base.metadata.create_all(bind=engine)
seed_plans()

app.include_router(router=auth.router)
app.include_router(router=files.router)
app.include_router(router=plans.router)

import os
if not os.path.exists("frontend"):
    os.makedirs("frontend")

app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
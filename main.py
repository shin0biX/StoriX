from fastapi import FastAPI,Request,status,requests
import models
from database import engine
from routes import auth,files
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from fastapi import UploadFile,File


app = FastAPI()

models.Base.metadata.create_all(bind=engine)

app.include_router(router=auth.router)
app.include_router(router=files.router)
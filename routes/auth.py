from fastapi import APIRouter,Depends,HTTPException,Request
from pydantic import BaseModel
from models import User,FileTable
from passlib.context import CryptContext
from sqlalchemy.orm import Session
from typing import Annotated
from database import SessionLocal
from starlette import status
from datetime import timedelta,datetime,timezone
from fastapi.security import OAuth2PasswordBearer,OAuth2PasswordRequestForm
from jose import JWTError,jwt
from database import SessionLocal 


router = APIRouter(
    prefix='/auth',
    tags=['auth']
)

    
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

db_dependency = Annotated[Session,Depends(get_db)]

bcrypt_context = CryptContext(schemes=['bcrypt'], deprecated='auto')
oauth2_bearer = OAuth2PasswordBearer(tokenUrl="auth/token")

SECRET_KEY = 'df6f0d0a3add6756944ce0cc1de56d756d8fb17b309d63704a3224aeb0e375f7'
ALGORITHM = 'HS256'


class CreateUserRequest(BaseModel):
    username:str
    email : str
    full_name : str
    password:str
    
def create_access_token(usesrname:str,user_id:int,expires_delta:timedelta, role:str):
    encode = {'sub' : usesrname, 'id': user_id , 'role':role}
    expires = datetime.now(timezone.utc) + expires_delta
    encode.update({'exp':expires})
    return jwt.encode(encode,SECRET_KEY,ALGORITHM)

def authenticate_user(username:str , password:str ,db):
    user = db.query(User).filter(User.username==username).first()
    if not user:
        return False
    if not bcrypt_context.verify(password,user.hashed_password):
        return False
    return user

async def get_current_user(token: Annotated[str, Depends(oauth2_bearer)], db:db_dependency) -> User:
    try:
        payload= jwt.decode(token , SECRET_KEY, algorithms=[ALGORITHM])
        username : str = payload.get('sub')
        user_id : int = payload.get('id')
        
        if username is None or user_id is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='could not validate the user')
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        return user
    
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='could not validate the user')
    

@router.post("/")
async def create_user(create_user_request:CreateUserRequest,db:db_dependency):
    create_user_request = User(
        email = create_user_request.email,
        full_name = create_user_request.full_name,
        username = create_user_request.username,
        hashed_password = bcrypt_context.hash(create_user_request.password)
    )
    db.add(create_user_request)
    db.commit()
    
@router.post("/token")
async def login_for_access_token(form_data:Annotated[OAuth2PasswordRequestForm, Depends()],db:db_dependency):
    
    user = authenticate_user(
        form_data.username,form_data.password,db
    )
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Could not vaildate the user")
    token = create_access_token(
    usesrname=user.username,
    user_id=user.id,
    expires_delta=timedelta(minutes=20),
    role=user.role
)
    return {"access_token": token , "token_type": "bearer"}
import os
import hashlib
from datetime import datetime, timedelta
from typing import Optional
import jwt
from passlib.context import CryptContext
from fastapi import HTTPException

SECRET_KEY = os.getenv('SECRET_KEY', 'please-change-me')
ALGORITHM = 'HS256'
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv('ACCESS_TOKEN_EXPIRE_MINUTES', '60'))

# Use pbkdf2_sha256 to avoid bcrypt backend compatibility issues in local environments.
pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)

def create_access_token(subject: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = subject.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    encoded = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded

def decode_access_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid authentication credentials")

def create_password_reset_token(user_id: int, hashed_password: str) -> str:
    """Create a one-hour token that becomes invalid as soon as the password changes."""
    return create_access_token(
        {
            'user_id': user_id,
            'purpose': 'password_reset',
            'password_marker': hashlib.sha256(hashed_password.encode('utf-8')).hexdigest(),
        },
        expires_delta=timedelta(hours=1),
    )

def decode_password_reset_token(token: str, hashed_password: str) -> int:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        raise HTTPException(status_code=400, detail='This password reset link is invalid or has expired.')

    expected_marker = hashlib.sha256(hashed_password.encode('utf-8')).hexdigest()
    if payload.get('purpose') != 'password_reset' or payload.get('password_marker') != expected_marker:
        raise HTTPException(status_code=400, detail='This password reset link is invalid or has expired.')
    try:
        return int(payload['user_id'])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(status_code=400, detail='This password reset link is invalid or has expired.')

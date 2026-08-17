from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import RedirectResponse
import requests
from ..core.config import settings
from ..db import SessionLocal
from sqlalchemy.orm import Session
from ..models import User
from cryptography.fernet import Fernet
from fastapi import Depends
from ..routes.auth import get_current_user
import os

router = APIRouter()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@router.get('/login')
def google_login():
    client_id = settings['google']['client_id']
    redirect = settings['google']['redirect_uri']
    if not client_id or not redirect:
        raise HTTPException(status_code=500, detail='Google OAuth not configured')
    scope = 'https://www.googleapis.com/auth/calendar.events'
    auth_url = (
        'https://accounts.google.com/o/oauth2/v2/auth'
        f'?client_id={client_id}&response_type=code&scope={scope}&access_type=offline&prompt=consent&redirect_uri={redirect}'
    )
    return RedirectResponse(auth_url)

@router.get('/callback')
def google_callback(request: Request, code: str = None, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not code:
        raise HTTPException(status_code=400, detail='Missing code')
    token_url = 'https://oauth2.googleapis.com/token'
    data = {
        'code': code,
        'client_id': settings['google']['client_id'],
        'client_secret': settings['google']['client_secret'],
        'redirect_uri': settings['google']['redirect_uri'],
        'grant_type': 'authorization_code',
    }
    resp = requests.post(token_url, data=data, timeout=10)
    if not resp.ok:
        raise HTTPException(status_code=500, detail='Failed to exchange code')
    payload = resp.json()
    refresh_token = payload.get('refresh_token')
    user = current_user
    # Encrypt refresh token
    fkey = os.getenv('FERNET_KEY')
    if not fkey:
        raise HTTPException(status_code=500, detail='FERNET_KEY not configured')
    f = Fernet(fkey.encode())
    encrypted = f.encrypt((refresh_token or '').encode()).decode()
    user.google_refresh_token = encrypted
    db.add(user)
    db.commit()
    return {'ok': True}

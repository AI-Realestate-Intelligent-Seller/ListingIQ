import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session
from .. import db
from ..schemas import (UserCreate, UserRegister, UserOut, AuthResponse, RegistrationResponse,
                       RefreshRequest, ForgotPasswordRequest, PasswordResetRequest, MessageResponse)
from ..models import User
from ..auth import (hash_password, verify_password, create_access_token,
                    create_password_reset_token, decode_password_reset_token,
                    ACCESS_TOKEN_EXPIRE_MINUTES)
from ..core.email import (EmailDeliveryError, build_password_reset_url,
                          send_password_reset_email)
from ..logger import get_logger, set_request_context

# Login is a plain JSON endpoint, not an OAuth2 password-grant endpoint, so the
# access token is simply pasted into Swagger's Authorize dialog as a bearer token.
# auto_error=False keeps the existing 401 (rather than HTTPBearer's default 403)
# when the Authorization header is missing.
bearer_scheme = HTTPBearer(
    auto_error=False,
    description="Paste the access_token returned by POST /api/v1/auth/login.",
)

router = APIRouter()
logger = get_logger(__name__)
PASSWORD_RESET_REQUEST_MESSAGE = (
    'If an active account exists for that email, a password reset link has been sent.'
)

def get_db():
    db_session = db.SessionLocal()
    try:
        yield db_session
    finally:
        db_session.close()


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    session: Session = Depends(get_db),
) -> User:
    from ..auth import decode_access_token
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=401,
            detail='Not authenticated',
            headers={'WWW-Authenticate': 'Bearer'},
        )
    payload = decode_access_token(credentials.credentials)
    user_id = payload.get('user_id')
    if not user_id:
        raise HTTPException(status_code=401, detail='Invalid token')
    user = session.query(User).filter(User.id == int(user_id)).first()
    if not user:
        raise HTTPException(status_code=401, detail='User not found')
    # set logging context
    set_request_context(user_id=user.id)
    return user


def require_role(role: str):
    def _check(user: User = Depends(get_current_user)):
        if user.role != role:
            raise HTTPException(status_code=403, detail='Forbidden')
        return user
    return _check

@router.post('/register', response_model=RegistrationResponse)
def register(user_in: UserRegister, session: Session = Depends(get_db)):
    existing = session.query(User).filter(User.email == user_in.email).first()
    if existing:
        raise HTTPException(status_code=400, detail='Email already registered')

    role = 'hob' if user_in.is_head_or_owner else 'broker'
    full_name = f"{user_in.first_name.strip()} {user_in.last_name.strip()}"
    user = User(
        email=user_in.email,
        hashed_password=hash_password(user_in.password),
        full_name=full_name,
        first_name=user_in.first_name.strip(),
        last_name=user_in.last_name.strip(),
        brokerage_name=user_in.brokerage_name.strip(),
        brokerage_id=str(uuid.uuid4()),
        is_head_or_owner=user_in.is_head_or_owner,
        is_verified=False,
        is_active=True,
        role=role,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return {
        "message": "Account created successfully.",
        "email": user.email,
    }

@router.post('/login', response_model=AuthResponse)
def login(form: UserCreate, session: Session = Depends(get_db)):
    user = session.query(User).filter(User.email == form.email).first()
    if not user or not verify_password(form.password, user.hashed_password):
        raise HTTPException(status_code=401, detail='Invalid credentials')

    token = create_access_token({"user_id": user.id, "role": user.role})
    refresh_token = create_access_token(
        {"user_id": user.id, "role": user.role},
        expires_delta=timedelta(days=30),
    )
    expires_in = ACCESS_TOKEN_EXPIRE_MINUTES * 60
    return {
        "access_token": token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "expires_in": expires_in,
        "user": user,
    }


@router.post('/forgot-password', response_model=MessageResponse)
def forgot_password(payload: ForgotPasswordRequest, session: Session = Depends(get_db)):
    """Email a reset link while returning the same response for unknown accounts."""
    email = str(payload.email).strip().lower()
    user = session.query(User).filter(User.email == email, User.is_active.is_(True)).first()
    if user:
        token = create_password_reset_token(user.id, user.hashed_password)
        try:
            send_password_reset_email(user.email, build_password_reset_url(token))
        except EmailDeliveryError:
            # Do not reveal whether an address is registered through differing responses.
            logger.exception('password_reset_email_failed user_id=%s', user.id)
    return {'message': PASSWORD_RESET_REQUEST_MESSAGE}


@router.post('/reset-password', response_model=MessageResponse)
def reset_password(payload: PasswordResetRequest, session: Session = Depends(get_db)):
    """Replace the password once; its hash invalidates this and all older reset links."""
    from jwt import DecodeError

    # Read the untrusted user id only to locate the password hash. Full signature,
    # expiry, purpose and password-marker validation happens immediately after.
    try:
        import jwt
        unverified = jwt.decode(payload.token, options={'verify_signature': False})
        user_id = int(unverified.get('user_id'))
    except (DecodeError, TypeError, ValueError):
        raise HTTPException(status_code=400, detail='This password reset link is invalid or has expired.')

    user = session.query(User).filter(User.id == user_id, User.is_active.is_(True)).first()
    if not user:
        raise HTTPException(status_code=400, detail='This password reset link is invalid or has expired.')
    decode_password_reset_token(payload.token, user.hashed_password)
    user.hashed_password = hash_password(payload.password)
    session.commit()
    return {'message': 'Your password has been reset. You can now log in.'}


@router.post('/refresh', response_model=AuthResponse)
def refresh(payload: RefreshRequest, session: Session = Depends(get_db)):
    """Exchange a valid refresh token for a fresh access token.

    Access tokens are short-lived, so a session that stays active for hours needs
    this; the client keeps working while the user keeps working, and only real
    inactivity ends the session.
    """
    from ..auth import decode_access_token

    claims = decode_access_token(payload.refresh_token)
    user_id = claims.get('user_id')
    if not user_id:
        raise HTTPException(status_code=401, detail='Invalid refresh token')

    user = session.query(User).filter(User.id == int(user_id)).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail='Account is no longer active')

    return {
        'access_token': create_access_token({'user_id': user.id, 'role': user.role}),
        'refresh_token': create_access_token(
            {'user_id': user.id, 'role': user.role}, expires_delta=timedelta(days=30)),
        'token_type': 'bearer',
        'expires_in': ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        'user': user,
    }


@router.get('/me', response_model=UserOut)
def me(current: User = Depends(get_current_user)):
    return current

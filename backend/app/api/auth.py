import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.core.login_throttle import client_ip, login_throttle
from app.api.deps import get_current_user
from app.core.security import create_access_token, hash_password, verify_password
from app.database import get_db
from app.models.user import User
from app.schemas.auth import ChangePasswordRequest, LoginRequest, MeOut, TokenResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

TOO_MANY_ATTEMPTS = "Too many failed login attempts, try again later"


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    ip = client_ip(request)
    if login_throttle.is_blocked(ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=TOO_MANY_ATTEMPTS,
        )
    user = db.query(User).filter(User.username == payload.username).first()
    if user is None or not verify_password(payload.password, user.password_hash):
        login_throttle.register_failure(ip)
        logger.warning("login failed user=%s ip=%s", payload.username, ip)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    login_throttle.reset(ip)
    return TokenResponse(access_token=create_access_token(user.username))


@router.get("/me", response_model=MeOut)
def me(user: User = Depends(get_current_user)):
    return user


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Guessing the current password from a stolen session counts like a
    # failed login.
    ip = client_ip(request)
    if login_throttle.is_blocked(ip):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=TOO_MANY_ATTEMPTS)
    if not verify_password(payload.current_password, user.password_hash):
        login_throttle.register_failure(ip)
        logger.warning("change-password failed user=%s ip=%s", user.username, ip)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="La contraseña actual no es correcta")
    login_throttle.reset(ip)
    user.password_hash = hash_password(payload.new_password)
    db.commit()
    logger.info("password changed user=%s", user.username)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

import logging

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.api.deps import require_full
from app.core.security import hash_password
from app.database import get_db
from app.models.user import ROLE_FULL, User
from app.schemas.user import UserCreate, UserOut, UserUpdate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/users", tags=["users"], dependencies=[Depends(require_full)])

LAST_FULL_USER = "Tiene que quedar al menos un usuario con acceso completo"


def _get_user(db: Session, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuario no encontrado")
    return user


@router.get("", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db)):
    return db.query(User).order_by(User.username).all()


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(payload: UserCreate, db: Session = Depends(get_db), me: User = Depends(require_full)):
    if db.query(User).filter(User.username == payload.username).first() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Ya existe un usuario con ese nombre")
    user = User(username=payload.username, password_hash=hash_password(payload.password), role=payload.role)
    db.add(user)
    db.commit()
    db.refresh(user)
    logger.info("user created user=%s role=%s by=%s", user.username, user.role, me.username)
    return user


@router.put("/{user_id}", response_model=UserOut)
def update_user(
    user_id: int, payload: UserUpdate, db: Session = Depends(get_db), me: User = Depends(require_full)
):
    user = _get_user(db, user_id)
    if payload.role is not None and payload.role != user.role:
        if user.role == ROLE_FULL:
            # Row lock so two concurrent demotions can't both pass the check.
            fulls = db.query(User.id).filter(User.role == ROLE_FULL).with_for_update().all()
            if len(fulls) <= 1:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=LAST_FULL_USER)
        user.role = payload.role
    if payload.password is not None:
        user.password_hash = hash_password(payload.password)
    db.commit()
    db.refresh(user)
    logger.info("user updated user=%s role=%s password_reset=%s by=%s",
                user.username, user.role, payload.password is not None, me.username)
    return user


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(user_id: int, db: Session = Depends(get_db), me: User = Depends(require_full)):
    user = _get_user(db, user_id)
    if user.id == me.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No podés borrar tu propio usuario")
    # The caller is a full user and stays, so this never removes the last one.
    db.delete(user)
    db.commit()
    logger.info("user deleted user=%s by=%s", user.username, me.username)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

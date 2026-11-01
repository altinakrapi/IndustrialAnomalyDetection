import secrets

from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.dependencies import get_db, require_roles
from app.core.security import hash_password
from app.modules.users.application.auth_service import user_payload
from app.modules.users.domain.roles import USER_ROLES, UserRole
from app.shared.models import AuditLog, User

router = APIRouter()
ADMIN = require_roles("administrator")


class UserCreate(BaseModel):
    full_name: str = Field(min_length=1, max_length=150)
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=10, max_length=128)
    role: UserRole = "data_analyst"


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=150)
    email: str | None = Field(default=None, min_length=3, max_length=255)
    role: UserRole | None = None
    is_active: bool | None = None


@router.get("")
def list_users(_: User = Depends(ADMIN), db: Session = Depends(get_db)) -> list[dict]:
    return [user_payload(user) for user in db.scalars(select(User).order_by(User.full_name))]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_user(
    payload: UserCreate,
    actor: User = Depends(ADMIN),
    db: Session = Depends(get_db),
) -> dict:
    if payload.role not in USER_ROLES:
        raise HTTPException(status_code=422, detail="Unsupported user role")
    email = payload.email.strip().lower()
    if db.scalar(select(User.id).where(User.email == email)):
        raise HTTPException(status_code=409, detail="An account with this email already exists")
    user = User(
        full_name=payload.full_name.strip(),
        email=email,
        password_hash=hash_password(payload.password),
        role=payload.role,
    )
    db.add(user)
    db.flush()
    db.add(AuditLog(user_id=actor.id, action="create", entity_type="user", entity_id=user.id))
    db.commit()
    db.refresh(user)
    return user_payload(user)


@router.patch("/{user_id}")
def update_user(
    user_id: int,
    payload: UserUpdate,
    actor: User = Depends(ADMIN),
    db: Session = Depends(get_db),
) -> dict:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    updates = payload.model_dump(exclude_unset=True)
    if "role" in updates and updates["role"] not in USER_ROLES:
        raise HTTPException(status_code=422, detail="Unsupported user role")
    if user.id == actor.id and updates.get("is_active") is False:
        raise HTTPException(status_code=400, detail="You cannot deactivate your own account")
    if "full_name" in updates:
        updates["full_name"] = updates["full_name"].strip()
        if not updates["full_name"]:
            raise HTTPException(status_code=422, detail="Full name cannot be empty")
    if "email" in updates:
        updates["email"] = updates["email"].strip().lower()
        duplicate = db.scalar(
            select(User.id).where(
                func.lower(User.email) == updates["email"],
                User.id != user.id,
            )
        )
        if duplicate is not None:
            raise HTTPException(status_code=409, detail="An account with this email already exists")
    for key, value in updates.items():
        if value is not None or key == "is_active":
            setattr(user, key, value)
    db.add(AuditLog(user_id=actor.id, action="update", entity_type="user", entity_id=user.id, details=updates))
    db.commit()
    db.refresh(user)
    return user_payload(user)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    user_id: int,
    actor: User = Depends(ADMIN),
    db: Session = Depends(get_db),
) -> Response:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if user.id == actor.id:
        raise HTTPException(status_code=400, detail="You cannot delete your own account")
    if user.role == "administrator" and user.is_active:
        active_admin_count = db.scalar(
            select(func.count(User.id)).where(
                User.role == "administrator",
                User.is_active.is_(True),
            )
        )
        if active_admin_count == 1:
            raise HTTPException(
                status_code=400,
                detail="You cannot delete the last active administrator",
            )

    db.add(
        AuditLog(
            user_id=actor.id,
            action="delete",
            entity_type="user",
            entity_id=user.id,
            details={"email": user.email},
        )
    )
    db.delete(user)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{user_id}/reset-password")
def reset_user_password(
    user_id: int,
    actor: User = Depends(ADMIN),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    temporary_password = secrets.token_urlsafe(18)
    user.password_hash = hash_password(temporary_password)
    db.add(
        AuditLog(
            user_id=actor.id,
            action="reset_password",
            entity_type="user",
            entity_id=user.id,
        )
    )
    db.commit()
    return {"temporary_password": temporary_password}
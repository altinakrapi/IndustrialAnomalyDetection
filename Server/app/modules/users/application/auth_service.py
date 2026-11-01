import hashlib
import secrets
import smtplib
import ssl
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.security import (
    create_access_token,
    hash_password,
    verify_password,
)
from app.core.config import settings
from app.core.email import EmailConfigurationError, send_password_reset_email
from app.modules.users.domain.exceptions import (
    AdministratorNotConfiguredError,
    InactiveUserError,
    IncorrectPasswordError,
    InvalidCredentialsError,
    UserAlreadyExistsError,
)
from app.modules.users.infrastructure.user_repository import UserRepository
from app.modules.users.schemas.auth import (
    LoginRequest,
    PasswordRequest,
    ProfileUpdate,
    RegisterRequest,
    ResetPasswordRequest,
)
from app.shared.models import AuditLog, PasswordResetToken, User


def user_payload(user: User) -> dict:
    return {
        "id": user.id,
        "full_name": user.full_name,
        "email": user.email,
        "role": user.role,
        "is_active": user.is_active,
        "created_at": user.created_at,
    }


def _auth_payload(user: User) -> dict:
    return {
        "access_token": create_access_token(user.id),
        "token_type": "bearer",
        "user": user_payload(user),
    }


def registration_status(repository: UserRepository) -> dict[str, bool]:
    return {"signup_enabled": repository.has_active_administrator()}


def register_user(payload: RegisterRequest, repository: UserRepository) -> dict:
    email = payload.email.strip().lower()
    if not repository.has_active_administrator():
        raise AdministratorNotConfiguredError
    if repository.email_exists(email):
        raise UserAlreadyExistsError

    user = User(
        full_name=f"{payload.first_name.strip()} {payload.surname.strip()}",
        email=email,
        password_hash=hash_password(payload.password),
        role=payload.role,
    )
    return _auth_payload(repository.save_with_audit(user, "register"))


def login_user(payload: LoginRequest, repository: UserRepository) -> dict:
    user = repository.find_by_email(payload.email.strip())
    if user is None or not verify_password(payload.password, user.password_hash):
        raise InvalidCredentialsError
    if not user.is_active:
        raise InactiveUserError
    repository.save_with_audit(user, "login")
    return _auth_payload(user)


def update_user_profile(
    payload: ProfileUpdate,
    user: User,
    repository: UserRepository,
) -> dict:
    email = payload.email.strip().lower()
    if repository.email_exists(email, excluding_user_id=user.id):
        raise UserAlreadyExistsError
    user.full_name = payload.full_name.strip()
    user.email = email
    return user_payload(repository.save_with_audit(user, "update_profile"))


def change_user_password(
    payload: PasswordRequest,
    user: User,
    repository: UserRepository,
) -> dict[str, str]:
    if not verify_password(payload.current_password, user.password_hash):
        raise IncorrectPasswordError
    user.password_hash = hash_password(payload.new_password)
    repository.save_with_audit(user, "change_password")
    return {"message": "Password updated"}


def request_password_reset(email: str, db: Session) -> None:
    normalized_email = email.strip().lower()
    user = db.scalar(
        select(User).where(func.lower(User.email) == normalized_email)
    )
    if user is None or not user.is_active:
        return

    now = datetime.now(timezone.utc)
    recent_request = db.scalar(
        select(PasswordResetToken.id)
        .where(
            PasswordResetToken.user_id == user.id,
            PasswordResetToken.created_at >= now - timedelta(minutes=1),
        )
        .limit(1)
    )
    if recent_request is not None:
        return

    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    db.execute(
        update(PasswordResetToken)
        .where(
            PasswordResetToken.user_id == user.id,
            PasswordResetToken.used_at.is_(None),
        )
        .values(used_at=now)
    )
    db.add(
        PasswordResetToken(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=now + timedelta(minutes=30),
        )
    )
    db.commit()

    reset_url = f"{settings.PASSWORD_RESET_FRONTEND_URL.rstrip('/')}?reset_token={token}"
    try:
        send_password_reset_email(user.email, reset_url)
    except (
        EmailConfigurationError,
        smtplib.SMTPException,
        ssl.SSLError,
        OSError,
    ):
        db.execute(
            update(PasswordResetToken)
            .where(PasswordResetToken.token_hash == token_hash)
            .values(used_at=datetime.now(timezone.utc))
        )
        db.commit()
        raise


def reset_user_password(payload: ResetPasswordRequest, db: Session) -> None:
    token_hash = hashlib.sha256(payload.token.encode("utf-8")).hexdigest()
    now = datetime.now(timezone.utc)
    reset_token = db.scalar(
        select(PasswordResetToken)
        .where(
            PasswordResetToken.token_hash == token_hash,
            PasswordResetToken.used_at.is_(None),
        )
        .with_for_update()
    )
    if reset_token is None or reset_token.expires_at <= now:
        raise ValueError("This password reset link is invalid or expired")

    user = db.scalar(
        select(User).where(User.id == reset_token.user_id).with_for_update()
    )
    if user is None or not user.is_active:
        raise ValueError("This password reset link is invalid or expired")
    reset_token = db.scalar(
        select(PasswordResetToken)
        .where(
            PasswordResetToken.id == reset_token.id,
            PasswordResetToken.used_at.is_(None),
        )
        .with_for_update()
    )
    if reset_token is None or reset_token.expires_at <= now:
        raise ValueError("This password reset link is invalid or expired")

    user.password_hash = hash_password(payload.new_password)
    db.execute(
        update(PasswordResetToken)
        .where(
            PasswordResetToken.user_id == user.id,
            PasswordResetToken.used_at.is_(None),
        )
        .values(used_at=now)
    )
    db.add(
        AuditLog(
            user_id=user.id,
            action="password_reset",
            entity_type="user",
            entity_id=user.id,
        )
    )
    db.commit()

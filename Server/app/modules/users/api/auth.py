import logging
import smtplib

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user, get_db
from app.core.email import EmailConfigurationError, email_is_configured
from app.modules.users.application.auth_service import (
    change_user_password,
    login_user,
    register_user,
    registration_status,
    request_password_reset,
    reset_user_password,
    update_user_profile,
    user_payload,
)
from app.modules.users.domain.exceptions import (
    AdministratorNotConfiguredError,
    InactiveUserError,
    IncorrectPasswordError,
    InvalidCredentialsError,
    UserAlreadyExistsError,
)
from app.modules.users.infrastructure.user_repository import UserRepository
from app.modules.users.schemas.auth import (
    ForgotPasswordRequest,
    LoginRequest,
    PasswordRequest,
    ProfileUpdate,
    RegisterRequest,
    ResetPasswordRequest,
)
from app.shared.models import User

router = APIRouter()
logger = logging.getLogger(__name__)


def _raise_auth_error(error: Exception) -> None:
    if isinstance(error, AdministratorNotConfiguredError):
        raise HTTPException(
            status_code=503,
            detail="Public signup is unavailable until an Administrator is seeded",
        ) from error
    if isinstance(error, UserAlreadyExistsError):
        raise HTTPException(
            status_code=409, detail="An account with this email already exists"
        ) from error
    if isinstance(error, InvalidCredentialsError):
        raise HTTPException(
            status_code=401, detail="Email or password is incorrect"
        ) from error
    if isinstance(error, InactiveUserError):
        raise HTTPException(
            status_code=403, detail="This account is inactive"
        ) from error
    if isinstance(error, IncorrectPasswordError):
        raise HTTPException(
            status_code=400, detail="Current password is incorrect"
        ) from error
    raise error


@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, db: Session = Depends(get_db)) -> dict:
    try:
        return register_user(payload, UserRepository(db))
    except (AdministratorNotConfiguredError, UserAlreadyExistsError) as error:
        _raise_auth_error(error)


@router.get("/status")
def auth_status(db: Session = Depends(get_db)) -> dict[str, bool]:
    return registration_status(UserRepository(db))


@router.post("/login")
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> dict:
    try:
        return login_user(payload, UserRepository(db))
    except (InvalidCredentialsError, InactiveUserError) as error:
        _raise_auth_error(error)


@router.post("/forgot-password", status_code=status.HTTP_202_ACCEPTED)
def forgot_password(
    payload: ForgotPasswordRequest, db: Session = Depends(get_db)
) -> dict[str, str]:
    if not email_is_configured():
        raise HTTPException(
            status_code=503,
            detail="Password reset email is not configured. Contact the administrator.",
        )
    try:
        request_password_reset(payload.email, db)
    except (EmailConfigurationError, smtplib.SMTPException, OSError) as error:
        logger.exception("Unable to send password reset email")
        raise HTTPException(
            status_code=503,
            detail="Unable to send the password reset email. Try again later.",
        ) from error
    return {
        "message": "If an account exists for that email, a password reset link will be sent."
    }


@router.post("/reset-password")
def reset_password(
    payload: ResetPasswordRequest, db: Session = Depends(get_db)
) -> dict[str, str]:
    try:
        reset_user_password(payload, db)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {"message": "Password updated. You can now sign in."}


@router.get("/me")
def current_user(user: User = Depends(get_current_user)) -> dict:
    return user_payload(user)


@router.patch("/me")
def update_profile(
    payload: ProfileUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    try:
        return update_user_profile(payload, user, UserRepository(db))
    except UserAlreadyExistsError as error:
        _raise_auth_error(error)


@router.post("/change-password")
def change_password(
    payload: PasswordRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    try:
        return change_user_password(payload, user, UserRepository(db))
    except IncorrectPasswordError as error:
        _raise_auth_error(error)
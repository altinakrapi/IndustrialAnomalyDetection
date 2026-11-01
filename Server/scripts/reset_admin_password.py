import getpass
from collections.abc import Callable

from sqlalchemy import select

from app.core.security import hash_password
from app.shared.database.session import SessionLocal
from app.shared.models import AuditLog, User


def reset_administrator_password(
    db,
    email: str,
    password: str,
) -> User:
    normalized_email = email.strip().lower()
    if len(password) < 10:
        raise ValueError("New password must be at least 10 characters.")

    user = db.scalar(select(User).where(User.email.ilike(normalized_email)))
    if user is None:
        raise LookupError("Account not found.")
    if user.role != "administrator":
        raise PermissionError("Only Administrator accounts can be reset with this script.")

    user.password_hash = hash_password(password)
    user.is_active = True
    db.add(
        AuditLog(
            user_id=user.id,
            action="reset_password",
            entity_type="user",
            entity_id=user.id,
            details={"method": "interactive_administrator_recovery"},
        )
    )
    db.commit()
    db.refresh(user)
    return user


def main(prompt: Callable[[str], str] = input) -> None:
    email = prompt("Administrator email: ").strip().lower()
    password = getpass.getpass("New password (hidden): ")
    confirmation = getpass.getpass("Confirm new password (hidden): ")
    if password != confirmation:
        raise SystemExit("Passwords do not match.")

    try:
        with SessionLocal() as db:
            reset_administrator_password(db, email, password)
    except (LookupError, PermissionError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc

    print("Administrator password reset and account activated.")


if __name__ == "__main__":
    main()

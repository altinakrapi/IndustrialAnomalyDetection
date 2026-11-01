from sqlalchemy import select

from app.core.config import settings
from app.core.security import hash_password
from app.modules.users.infrastructure.user_repository import UserRepository
from app.shared.database.session import SessionLocal
from app.shared.models import User


def main() -> None:
    if not settings.ADMIN_EMAIL or not settings.ADMIN_PASSWORD:
        raise SystemExit(
            "Set ADMIN_EMAIL and ADMIN_PASSWORD in Server/.env before seeding."
        )
    if len(settings.ADMIN_PASSWORD) < 10:
        raise SystemExit("ADMIN_PASSWORD must be at least 10 characters.")

    email = settings.ADMIN_EMAIL.strip().lower()
    with SessionLocal() as db:
        repository = UserRepository(db)
        if repository.has_active_administrator():
            print("An active Administrator already exists; no changes made.")
            return

        existing_user = db.scalar(
            select(User).where(User.email.ilike(email))
        )
        if existing_user is not None:
            raise SystemExit(
                "ADMIN_EMAIL is already assigned to a non-active or non-administrator account."
            )

        user = User(
            full_name=settings.ADMIN_FULL_NAME.strip(),
            email=email,
            password_hash=hash_password(settings.ADMIN_PASSWORD),
            role="administrator",
        )
        repository.save_with_audit(user, "seed")

    print(f"Administrator account seeded for {email}.")


if __name__ == "__main__":
    main()

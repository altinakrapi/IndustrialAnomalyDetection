from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.shared.models import AuditLog, User


class UserRepository:
    def __init__(self, db: Session):
        self.db = db

    def has_active_administrator(self) -> bool:
        return self.db.scalar(
            select(User.id).where(
                User.role == "administrator",
                User.is_active.is_(True),
            ).limit(1)
        ) is not None

    def find_by_email(self, email: str) -> User | None:
        return self.db.scalar(
            select(User).where(func.lower(User.email) == email.lower())
        )

    def email_exists(self, email: str, excluding_user_id: int | None = None) -> bool:
        query = select(User.id).where(func.lower(User.email) == email.lower())
        if excluding_user_id is not None:
            query = query.where(User.id != excluding_user_id)
        return self.db.scalar(query) is not None

    def save_with_audit(
        self,
        user: User,
        action: str,
        *,
        actor_id: int | None = None,
        details: dict | None = None,
    ) -> User:
        self.db.add(user)
        self.db.flush()
        self.db.add(
            AuditLog(
                user_id=actor_id if actor_id is not None else user.id,
                action=action,
                entity_type="user",
                entity_id=user.id,
                details=details or {},
            )
        )
        self.db.commit()
        self.db.refresh(user)
        return user

from typing import Literal

UserRole = Literal["administrator", "data_analyst", "maintenance_technician"]
PublicSignupRole = Literal["data_analyst", "maintenance_technician"]

USER_ROLES: frozenset[str] = frozenset(
    {"administrator", "data_analyst", "maintenance_technician"}
)

from pydantic import BaseModel, EmailStr, Field

from app.modules.users.domain.roles import PublicSignupRole


class RegisterRequest(BaseModel):
    first_name: str = Field(min_length=1, max_length=75)
    surname: str = Field(min_length=1, max_length=75)
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=10, max_length=128)
    role: PublicSignupRole = "data_analyst"


class LoginRequest(BaseModel):
    email: str
    password: str


class PasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=10, max_length=128)


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=32, max_length=128)
    new_password: str = Field(min_length=10, max_length=128)


class ProfileUpdate(BaseModel):
    full_name: str = Field(min_length=1, max_length=150)
    email: str = Field(min_length=3, max_length=255)

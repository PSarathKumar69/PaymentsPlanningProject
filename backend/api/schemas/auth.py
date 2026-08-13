"""Request/response schemas for the auth router."""
from pydantic import BaseModel


class LoginRequest(BaseModel):
    username: str
    password: str
    remember: bool = False


class CurrentUserOut(BaseModel):
    username: str
    display_name: str
    role: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

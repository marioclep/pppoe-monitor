from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.security import MIN_PASSWORD_LENGTH

Role = Literal["full", "readonly"]


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    role: Role
    created_at: datetime


class UserCreate(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=MIN_PASSWORD_LENGTH)
    role: Role


class UserUpdate(BaseModel):
    """Change the role and/or reset the password; omitted fields stay."""

    role: Role | None = None
    password: str | None = Field(default=None, min_length=MIN_PASSWORD_LENGTH)

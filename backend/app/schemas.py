from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    account_name: str = Field(min_length=2)
    email: EmailStr
    password: str = Field(min_length=8)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserSummary(BaseModel):
    id: str
    account_id: str
    email: EmailStr
    role: str


class LotOut(BaseModel):
    id: str
    account_id: str
    sku: str | None
    auction_title: str
    professional_description: str
    specification_lines: list[str]
    brands: list[str]
    labels_and_tags: list[str]
    starting_price: float | None
    condition_text: str
    photo_count: int
    created_at: datetime


class LotUpdate(BaseModel):
    auction_title: str | None = None
    professional_description: str | None = None
    specification_lines: list[str] | None = None
    brands: list[str] | None = None
    labels_and_tags: list[str] | None = None
    starting_price: float | None = None
    condition_text: str | None = None


class CreateJobRequest(BaseModel):
    input_folder: str


class JobOut(BaseModel):
    id: str
    account_id: str
    status: str
    input_folder: str
    error_message: str | None
    created_at: datetime


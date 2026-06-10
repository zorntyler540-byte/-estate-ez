from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class JobStatus(str, enum.Enum):
    queued = "queued"
    processing = "processing"
    completed = "completed"
    failed = "failed"


class Sale(Base):
    """A client-facing sale / auction event; lots and jobs are scoped to one sale."""

    __tablename__ = "sales"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True, nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Account(Base):
    __tablename__ = "accounts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    plan_code: Mapped[str] = mapped_column(String(40), default="beta_free", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    users: Mapped[list["User"]] = relationship(back_populates="account")
    lots: Mapped[list["Lot"]] = relationship(back_populates="account")


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(30), default="owner")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    account: Mapped["Account"] = relationship(back_populates="users")
    creator_jobs: Mapped[list["ProcessingJob"]] = relationship(back_populates="creator")


class Lot(Base):
    __tablename__ = "lots"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True, nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    sale_id: Mapped[str] = mapped_column(ForeignKey("sales.id"), index=True, nullable=False)
    sku: Mapped[str | None] = mapped_column(String(40), index=True)
    auction_title: Mapped[str] = mapped_column(String(500), default="")
    professional_description: Mapped[str] = mapped_column(Text, default="")
    specification_lines_json: Mapped[str] = mapped_column(Text, default="[]")
    brands_json: Mapped[str] = mapped_column(Text, default="[]")
    labels_and_tags_json: Mapped[str] = mapped_column(Text, default="[]")
    ct_category: Mapped[str | None] = mapped_column(String(120))
    ct_subcategory: Mapped[str | None] = mapped_column(String(120))
    is_completed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    starting_price: Mapped[float | None] = mapped_column(Float)
    condition_text: Mapped[str] = mapped_column(String(255), default="")
    sku_needs_review: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sku_duplicate_in_sale: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sku_text_mismatch: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    photo_count: Mapped[int] = mapped_column(Integer, default=0)
    ai_regenerate_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ai_regenerate_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    account: Mapped["Account"] = relationship(back_populates="lots")
    owner: Mapped["User"] = relationship()
    photos: Mapped[list["Photo"]] = relationship(back_populates="lot")


class Photo(Base):
    __tablename__ = "photos"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True, nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    lot_id: Mapped[str | None] = mapped_column(ForeignKey("lots.id"), index=True)
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(600), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    lot: Mapped["Lot"] = relationship(back_populates="photos")
    owner: Mapped["User"] = relationship()


class ProcessingJob(Base):
    __tablename__ = "processing_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True, nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    sale_id: Mapped[str] = mapped_column(ForeignKey("sales.id"), index=True, nullable=False)
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus), default=JobStatus.queued, nullable=False)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    input_folder: Mapped[str] = mapped_column(String(600), nullable=False)
    inherit_sku_from_subfolder: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    single_lot_mode: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    single_lot_manual_sku: Mapped[str | None] = mapped_column(String(40), nullable=True)
    analyzed_photos: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_photos: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    est_input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    est_output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    creator: Mapped["User"] = relationship(back_populates="creator_jobs")


class AppraisalCheck(Base):
    """Job-site spot appraisal (separate from catalog lots/photos)."""

    __tablename__ = "appraisal_checks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True, nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    sale_id: Mapped[str] = mapped_column(ForeignKey("sales.id"), index=True, nullable=False)
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus), default=JobStatus.queued, nullable=False)
    scene_note: Mapped[str | None] = mapped_column(Text)
    photo_paths_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    original_names_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    result_json: Mapped[str | None] = mapped_column(Text)
    est_input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    est_output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SalePhotoZipExport(Base):
    """Background-built ZIP of all catalog photos for a sale (large downloads)."""

    __tablename__ = "sale_photo_zip_exports"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True, nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    sale_id: Mapped[str] = mapped_column(ForeignKey("sales.id"), index=True, nullable=False)
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus), default=JobStatus.queued, nullable=False)
    zip_path: Mapped[str | None] = mapped_column(String(700), nullable=True)
    total_photos_expected: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    photo_files_written: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


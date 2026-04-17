from __future__ import annotations

import csv
import io
import json

from fastapi import Depends, FastAPI, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.auth import create_access_token, get_current_user, hash_password, verify_password
from app.config import settings
from app.db import Base, engine, get_db
from app.models import Account, JobStatus, Lot, ProcessingJob, User
from app.schemas import (
    CreateJobRequest,
    JobOut,
    LoginRequest,
    LotOut,
    LotUpdate,
    RegisterRequest,
    TokenResponse,
    UserSummary,
)
from app.services.processing import process_job
from app.services.serialization import lot_to_schema

app = FastAPI(title=settings.app_name)


@app.on_event("startup")
def startup() -> None:
    Base.metadata.create_all(bind=engine)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/auth/register", response_model=TokenResponse)
def register(payload: RegisterRequest, db: Session = Depends(get_db)) -> TokenResponse:
    existing = db.query(User).filter(User.email == payload.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already exists")
    account = Account(name=payload.account_name)
    db.add(account)
    db.flush()
    user = User(
        account_id=account.id,
        email=payload.email.lower(),
        password_hash=hash_password(payload.password),
        role="owner",
    )
    db.add(user)
    db.commit()
    token = create_access_token(user.id, account.id, user.role)
    return TokenResponse(access_token=token)


@app.post("/auth/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    user = db.query(User).filter(User.email == payload.email.lower()).first()
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid login")
    token = create_access_token(user.id, user.account_id, user.role)
    return TokenResponse(access_token=token)


@app.get("/auth/me", response_model=UserSummary)
def me(current: User = Depends(get_current_user)) -> UserSummary:
    return UserSummary(id=current.id, account_id=current.account_id, email=current.email, role=current.role)


@app.get("/lots", response_model=list[LotOut])
def list_lots(current: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[LotOut]:
    rows = (
        db.query(Lot)
        .filter(Lot.account_id == current.account_id)
        .order_by(Lot.created_at.desc())
        .all()
    )
    return [lot_to_schema(r) for r in rows]


@app.get("/lots/{lot_id}", response_model=LotOut)
def get_lot(lot_id: str, current: User = Depends(get_current_user), db: Session = Depends(get_db)) -> LotOut:
    row = db.query(Lot).filter(Lot.id == lot_id, Lot.account_id == current.account_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Lot not found")
    return lot_to_schema(row)


@app.patch("/lots/{lot_id}", response_model=LotOut)
def update_lot(
    lot_id: str,
    patch: LotUpdate,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> LotOut:
    row = db.query(Lot).filter(Lot.id == lot_id, Lot.account_id == current.account_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Lot not found")

    if patch.auction_title is not None:
        row.auction_title = patch.auction_title
    if patch.professional_description is not None:
        row.professional_description = patch.professional_description
    if patch.specification_lines is not None:
        row.specification_lines_json = json.dumps(patch.specification_lines, ensure_ascii=True)
    if patch.brands is not None:
        row.brands_json = json.dumps(patch.brands, ensure_ascii=True)
    if patch.labels_and_tags is not None:
        row.labels_and_tags_json = json.dumps(patch.labels_and_tags, ensure_ascii=True)
    if patch.starting_price is not None:
        row.starting_price = patch.starting_price
    if patch.condition_text is not None:
        row.condition_text = patch.condition_text
    db.commit()
    db.refresh(row)
    return lot_to_schema(row)


@app.post("/jobs", response_model=JobOut)
def create_job(
    payload: CreateJobRequest,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> JobOut:
    job = ProcessingJob(account_id=current.account_id, status=JobStatus.queued, input_folder=payload.input_folder)
    db.add(job)
    db.commit()
    db.refresh(job)
    return JobOut(
        id=job.id,
        account_id=job.account_id,
        status=job.status.value,
        input_folder=job.input_folder,
        error_message=job.error_message,
        created_at=job.created_at,
    )


@app.get("/jobs", response_model=list[JobOut])
def list_jobs(current: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[JobOut]:
    rows = (
        db.query(ProcessingJob)
        .filter(ProcessingJob.account_id == current.account_id)
        .order_by(ProcessingJob.created_at.desc())
        .all()
    )
    return [
        JobOut(
            id=r.id,
            account_id=r.account_id,
            status=r.status.value,
            input_folder=r.input_folder,
            error_message=r.error_message,
            created_at=r.created_at,
        )
        for r in rows
    ]


@app.post("/jobs/{job_id}/run", response_model=JobOut)
def run_job(job_id: str, current: User = Depends(get_current_user), db: Session = Depends(get_db)) -> JobOut:
    job = (
        db.query(ProcessingJob)
        .filter(ProcessingJob.id == job_id, ProcessingJob.account_id == current.account_id)
        .first()
    )
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    process_job(db, job)
    db.refresh(job)
    return JobOut(
        id=job.id,
        account_id=job.account_id,
        status=job.status.value,
        input_folder=job.input_folder,
        error_message=job.error_message,
        created_at=job.created_at,
    )


def _markdown_for_lots(rows: list[Lot]) -> str:
    out = ["# Estate Inventory", "", "| SKU | Listing | Price | Condition | Photos |", "| :-- | :-- | :-- | :-- | :-- |"]
    for row in rows:
        specs = json.loads(row.specification_lines_json or "[]")
        listing = (
            f"**Auction Title:** {row.auction_title}<br><br>"
            f"**Professional Description:** {row.professional_description}<br><br>"
            f"**Item Specifications:**<br>"
            + "<br>".join(f"- {s}" for s in specs)
        ).replace("|", "/")
        out.append(
            f"| {row.sku or ''} | {listing} | ${row.starting_price or ''} | {row.condition_text} | {row.photo_count} |"
        )
    return "\n".join(out)


@app.get("/exports/markdown")
def export_markdown(current: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Response:
    rows = db.query(Lot).filter(Lot.account_id == current.account_id).order_by(Lot.created_at).all()
    return Response(_markdown_for_lots(rows), media_type="text/markdown")


@app.get("/exports/json")
def export_json(current: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[LotOut]:
    rows = db.query(Lot).filter(Lot.account_id == current.account_id).order_by(Lot.created_at).all()
    return [lot_to_schema(r) for r in rows]


@app.get("/exports/csv")
def export_csv(current: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Response:
    rows = db.query(Lot).filter(Lot.account_id == current.account_id).order_by(Lot.created_at).all()
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(
        [
            "sku",
            "auction_title",
            "professional_description",
            "specification_lines",
            "brands",
            "labels_and_tags",
            "starting_price",
            "condition",
            "photo_count",
            "created_at",
        ]
    )
    for r in rows:
        writer.writerow(
            [
                r.sku,
                r.auction_title,
                r.professional_description,
                r.specification_lines_json,
                r.brands_json,
                r.labels_and_tags_json,
                r.starting_price,
                r.condition_text,
                r.photo_count,
                r.created_at.isoformat() if r.created_at else "",
            ]
        )
    return Response(stream.getvalue(), media_type="text/csv")


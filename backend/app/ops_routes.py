"""Franchise operations: quotes, proposals, settlements, monthly workbook."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.auth import get_current_user
from app.db import get_db
from app.models import Account, OpsEmployee, OpsFactor, OpsJob, OpsMonthFigures, OpsSettlement, OpsSettings, Sale, User
from app.ops_schemas import (
    OpsEmployeeIn,
    OpsEmployeeOut,
    OpsFactorApplyIn,
    OpsFactorsIn,
    OpsJobOut,
    OpsJobWrite,
    OpsMonthFiguresIn,
    OpsMonthJobOut,
    OpsMonthOut,
    OpsRatesIn,
    OpsRatesOut,
    OpsSettingsOut,
    OpsSettlementIn,
    OpsSettlementOut,
)
from app.services.ops_quote import (
    DENSITIES,
    DEFAULT_FACTORS,
    SERVICE_KEYS,
    build_quote,
    client_total_from,
    clone_factors,
    primary_service,
    settle,
)
from app.services.roles import can_catalog

router = APIRouter(prefix="/ops", tags=["operations"])

OPS_STATUSES = frozenset({"lead", "quoted", "proposal", "signed", "in_progress", "settled"})


def _require_ops(user: User) -> None:
    if not can_catalog(user.role):
        raise HTTPException(status_code=403, detail="Operations is for franchise staff.")


def _account(db: Session, user: User) -> Account:
    account = db.query(Account).filter(Account.id == user.account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    return account


def _owner_name_default(user: User) -> str:
    email = (user.email or "").strip()
    local = email.split("@", 1)[0].replace(".", " ").replace("_", " ").strip()
    return local.title() if local else "Owner"


def _seed_factors(db: Session, account_id: str) -> None:
    existing = {
        (row.service, row.density)
        for row in db.query(OpsFactor).filter(OpsFactor.account_id == account_id).all()
    }
    for service in SERVICE_KEYS:
        for density in DENSITIES:
            if (service, density) in existing:
                continue
            db.add(
                OpsFactor(
                    account_id=account_id,
                    service=service,
                    density=density,
                    factor=float(DEFAULT_FACTORS[service][density]),
                )
            )


def _ensure_settings(db: Session, account: Account, user: User) -> OpsSettings:
    row = db.query(OpsSettings).filter(OpsSettings.account_id == account.id).first()
    if row is None:
        row = OpsSettings(
            account_id=account.id,
            location_name=account.name or "Caring Transitions",
            owner_name=_owner_name_default(user),
        )
        db.add(row)
        _seed_factors(db, account.id)
        db.flush()
    else:
        _seed_factors(db, account.id)
    return row


def _factors_for(db: Session, account_id: str) -> dict[str, dict[str, float]]:
    factors = clone_factors()
    rows = db.query(OpsFactor).filter(OpsFactor.account_id == account_id).all()
    for row in rows:
        if row.service in factors and row.density in factors[row.service]:
            factors[row.service][row.density] = float(row.factor)
    return factors


def _parse_quote(raw: str | None) -> dict | None:
    if not raw or not str(raw).strip():
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _quote_for(job: OpsJob, settings: OpsSettings, factors: dict[str, dict[str, float]]) -> dict:
    return build_quote(
        sqft=float(job.sqft or 0),
        density=job.density or "medium",
        liquidation=job.liquidation or "none",
        include_sorting=bool(job.include_sorting),
        include_cleanout=bool(job.include_cleanout),
        include_packing=bool(job.include_packing),
        include_resettling=bool(job.include_resettling),
        space_plan_hours=float(job.space_plan_hours or 0),
        projected_proceeds=float(job.projected_proceeds or 0),
        bill_rate=float(settings.bill_rate),
        labor_target=float(settings.labor_target),
        royalty_rate=float(settings.royalty_rate),
        deposit_rate=float(settings.deposit_rate),
        commission_rate=float(settings.commission_rate),
        minimum_fee=float(settings.minimum_fee),
        factors=factors,
    )


def _settlement_out(row: OpsSettlement) -> OpsSettlementOut:
    return OpsSettlementOut(
        job_id=row.job_id,
        actual_hours=row.actual_hours,
        in_person_proceeds=row.in_person_proceeds,
        online_proceeds=row.online_proceeds,
        buyer_premium=row.buyer_premium,
        shipping=row.shipping,
        misc_revenue=row.misc_revenue,
        direct_expenses=row.direct_expenses,
        deposit_received=row.deposit_received,
        balance_received=row.balance_received,
        hourly_labor_cost=row.hourly_labor_cost,
        commission_rate=row.commission_rate,
        minimum_fee=row.minimum_fee,
        royalty_rate=row.royalty_rate,
        settled_on=row.settled_on,
    )


def _settle_job(job: OpsJob, settlement: OpsSettlement | None, quote: dict | None) -> dict | None:
    if not quote or not settlement:
        return None
    return settle(
        quote=quote,
        liquidation=job.liquidation or "none",
        in_person_proceeds=float(settlement.in_person_proceeds or 0),
        online_proceeds=float(settlement.online_proceeds or 0),
        buyer_premium=float(settlement.buyer_premium or 0),
        shipping=float(settlement.shipping or 0),
        misc_revenue=float(settlement.misc_revenue or 0),
        direct_expenses=float(settlement.direct_expenses or 0),
        actual_hours=float(settlement.actual_hours or 0),
        hourly_labor_cost=float(settlement.hourly_labor_cost or 0),
        commission_rate=float(settlement.commission_rate or 0),
        minimum_fee=float(settlement.minimum_fee or 0),
        royalty_rate=float(settlement.royalty_rate or 0),
    )


def _sale_name(db: Session, account_id: str, sale_id: str | None) -> str | None:
    if not sale_id:
        return None
    name = (
        db.query(Sale.name)
        .filter(Sale.id == sale_id, Sale.account_id == account_id)
        .scalar()
    )
    return str(name) if name else None


def _suggested(job: OpsJob, settlement: OpsSettlement | None) -> tuple[int | None, str | None]:
    if not settlement or not (job.sqft and job.sqft > 0) or settlement.actual_hours < 0.5:
        return None, None
    service = primary_service(
        job.liquidation,
        bool(job.include_sorting),
        bool(job.include_cleanout),
        bool(job.include_packing),
        bool(job.include_resettling),
    )
    if not service:
        return None, None
    return round(job.sqft / settlement.actual_hours), service


def _job_out(
    db: Session,
    job: OpsJob,
    settlement: OpsSettlement | None = None,
    *,
    include_suggestion: bool = False,
) -> OpsJobOut:
    quote = _parse_quote(job.quote_json)
    settlement = settlement if settlement is not None else job.settlement
    result = _settle_job(job, settlement, quote)
    suggested_factor = None
    suggested_service = None
    if include_suggestion:
        suggested_factor, suggested_service = _suggested(job, settlement)
    return OpsJobOut(
        id=job.id,
        account_id=job.account_id,
        client_name=job.client_name,
        phone=job.phone or "",
        email=job.email or "",
        address=job.address or "",
        zip=job.zip or "",
        sqft=float(job.sqft or 0),
        density=job.density,
        liquidation=job.liquidation,
        include_sorting=bool(job.include_sorting),
        include_cleanout=bool(job.include_cleanout),
        include_packing=bool(job.include_packing),
        include_resettling=bool(job.include_resettling),
        space_plan_hours=float(job.space_plan_hours or 0),
        projected_proceeds=float(job.projected_proceeds or 0),
        sale_id=job.sale_id,
        sale_name=_sale_name(db, job.account_id, job.sale_id),
        status=job.status,
        notes=job.notes or "",
        quote=quote,
        settlement=_settlement_out(settlement) if settlement else None,
        settlement_result=result,
        client_total=client_total_from(quote, result),
        suggested_factor=suggested_factor,
        suggested_service=suggested_service,
        created_at=job.created_at,
        settled_on=settlement.settled_on if settlement else None,
    )


def _get_job(db: Session, user: User, job_id: str) -> OpsJob:
    job = (
        db.query(OpsJob)
        .filter(OpsJob.id == job_id, OpsJob.account_id == user.account_id)
        .first()
    )
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


def _resolve_sale_id(db: Session, user: User, sale_id: str | None) -> str | None:
    value = (sale_id or "").strip() or None
    if not value:
        return None
    sale = db.query(Sale).filter(Sale.id == value, Sale.account_id == user.account_id).first()
    if not sale:
        raise HTTPException(status_code=400, detail="Estate-EZ sale not found on this account.")
    return sale.id


def _apply_write(job: OpsJob, body: OpsJobWrite, sale_id: str | None, *, lock_quote: bool) -> None:
    job.client_name = body.client_name.strip()
    job.phone = (body.phone or "").strip()
    job.email = (body.email or "").strip()
    job.address = (body.address or "").strip()
    job.zip = (body.zip or "").strip()
    job.sale_id = sale_id
    job.notes = body.notes or ""
    if lock_quote:
        job.status = "settled"
        return
    if body.status == "settled":
        raise HTTPException(status_code=400, detail="Enter the settlement before marking the job settled.")
    status = body.status if body.status in OPS_STATUSES else job.status
    job.status = "quoted" if status == "lead" else status
    job.sqft = max(0.0, float(body.sqft or 0))
    job.density = body.density
    job.liquidation = body.liquidation
    job.include_sorting = bool(body.include_sorting)
    job.include_cleanout = bool(body.include_cleanout)
    job.include_packing = bool(body.include_packing)
    job.include_resettling = bool(body.include_resettling)
    job.space_plan_hours = max(0.0, float(body.space_plan_hours or 0))
    job.projected_proceeds = max(0.0, float(body.projected_proceeds or 0))


def _average_pay(db: Session, account_id: str) -> float | None:
    avg = (
        db.query(func.avg(OpsEmployee.pay_rate))
        .filter(OpsEmployee.account_id == account_id)
        .scalar()
    )
    if avg is None:
        return None
    value = float(avg)
    return value if value > 0 else None


def _today() -> str:
    now = datetime.now(timezone.utc).astimezone()
    return f"{now.year:04d}-{now.month:02d}-{now.day:02d}"


def _rates_out(row: OpsSettings) -> OpsRatesOut:
    return OpsRatesOut(
        account_id=row.account_id,
        location_name=row.location_name,
        owner_name=row.owner_name,
        bill_rate=row.bill_rate,
        labor_target=row.labor_target,
        burden_rate=row.burden_rate,
        royalty_rate=row.royalty_rate,
        deposit_rate=row.deposit_rate,
        card_fee_rate=row.card_fee_rate,
        commission_rate=row.commission_rate,
        minimum_fee=row.minimum_fee,
    )


@router.get("/settings", response_model=OpsSettingsOut)
def get_settings(
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OpsSettingsOut:
    _require_ops(current)
    account = _account(db, current)
    settings = _ensure_settings(db, account, current)
    employees = (
        db.query(OpsEmployee)
        .filter(OpsEmployee.account_id == account.id)
        .order_by(OpsEmployee.name)
        .all()
    )
    db.commit()
    return OpsSettingsOut(
        rates=_rates_out(settings),
        factors=_factors_for(db, account.id),
        employees=[OpsEmployeeOut(id=e.id, name=e.name, pay_rate=e.pay_rate) for e in employees],
    )


@router.put("/settings", response_model=OpsSettingsOut)
def save_settings(
    body: OpsRatesIn,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OpsSettingsOut:
    _require_ops(current)
    account = _account(db, current)
    settings = _ensure_settings(db, account, current)
    settings.location_name = body.location_name.strip()
    settings.owner_name = body.owner_name.strip()
    settings.bill_rate = float(body.bill_rate)
    settings.labor_target = float(body.labor_target)
    settings.burden_rate = float(body.burden_rate)
    settings.royalty_rate = float(body.royalty_rate)
    settings.deposit_rate = float(body.deposit_rate)
    settings.card_fee_rate = float(body.card_fee_rate)
    settings.commission_rate = float(body.commission_rate)
    settings.minimum_fee = float(body.minimum_fee)
    db.commit()
    return get_settings(current=current, db=db)


@router.put("/factors", response_model=OpsSettingsOut)
def save_factors(
    body: OpsFactorsIn,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OpsSettingsOut:
    _require_ops(current)
    account = _account(db, current)
    _ensure_settings(db, account, current)
    incoming = body.factors or {}
    for service in SERVICE_KEYS:
        dens = incoming.get(service) or {}
        for density in DENSITIES:
            raw = dens.get(density, DEFAULT_FACTORS[service][density])
            try:
                factor = max(0.0, float(raw))
            except (TypeError, ValueError):
                factor = float(DEFAULT_FACTORS[service][density])
            row = (
                db.query(OpsFactor)
                .filter(
                    OpsFactor.account_id == account.id,
                    OpsFactor.service == service,
                    OpsFactor.density == density,
                )
                .first()
            )
            if row:
                row.factor = factor
            else:
                db.add(OpsFactor(account_id=account.id, service=service, density=density, factor=factor))
    db.commit()
    return get_settings(current=current, db=db)


@router.post("/factors/apply", response_model=OpsSettingsOut)
def apply_factor(
    body: OpsFactorApplyIn,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OpsSettingsOut:
    _require_ops(current)
    account = _account(db, current)
    _ensure_settings(db, account, current)
    factor = max(0.0, round(float(body.factor)))
    row = (
        db.query(OpsFactor)
        .filter(
            OpsFactor.account_id == account.id,
            OpsFactor.service == body.service,
            OpsFactor.density == body.density,
        )
        .first()
    )
    if row:
        row.factor = factor
    else:
        db.add(
            OpsFactor(
                account_id=account.id,
                service=body.service,
                density=body.density,
                factor=factor,
            )
        )
    db.commit()
    return get_settings(current=current, db=db)


@router.post("/employees", response_model=OpsEmployeeOut, status_code=201)
def create_employee(
    body: OpsEmployeeIn,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OpsEmployeeOut:
    _require_ops(current)
    account = _account(db, current)
    _ensure_settings(db, account, current)
    row = OpsEmployee(account_id=account.id, name=body.name.strip(), pay_rate=float(body.pay_rate))
    db.add(row)
    db.commit()
    db.refresh(row)
    return OpsEmployeeOut(id=row.id, name=row.name, pay_rate=row.pay_rate)


@router.delete("/employees/{employee_id}", status_code=204)
def delete_employee(
    employee_id: str,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    _require_ops(current)
    row = (
        db.query(OpsEmployee)
        .filter(OpsEmployee.id == employee_id, OpsEmployee.account_id == current.account_id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Employee not found")
    db.delete(row)
    db.commit()


@router.get("/jobs", response_model=list[OpsJobOut])
def list_jobs(
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[OpsJobOut]:
    _require_ops(current)
    rows = (
        db.query(OpsJob)
        .options(joinedload(OpsJob.settlement))
        .filter(OpsJob.account_id == current.account_id)
        .order_by(OpsJob.created_at.desc())
        .all()
    )
    return [_job_out(db, job) for job in rows]


@router.post("/jobs", response_model=OpsJobOut, status_code=201)
def create_job(
    body: OpsJobWrite,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OpsJobOut:
    _require_ops(current)
    account = _account(db, current)
    settings = _ensure_settings(db, account, current)
    sale_id = _resolve_sale_id(db, current, body.sale_id)
    job = OpsJob(account_id=account.id, user_id=current.id, client_name=body.client_name.strip())
    db.add(job)
    db.flush()
    _apply_write(job, body, sale_id, lock_quote=False)
    job.quote_json = json.dumps(_quote_for(job, settings, _factors_for(db, account.id)))
    db.commit()
    db.refresh(job)
    return _job_out(db, job, include_suggestion=True)


@router.get("/jobs/{job_id}", response_model=OpsJobOut)
def get_job(
    job_id: str,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OpsJobOut:
    _require_ops(current)
    job = _get_job(db, current, job_id)
    return _job_out(db, job, include_suggestion=True)


@router.put("/jobs/{job_id}", response_model=OpsJobOut)
def update_job(
    job_id: str,
    body: OpsJobWrite,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OpsJobOut:
    _require_ops(current)
    account = _account(db, current)
    settings = _ensure_settings(db, account, current)
    job = _get_job(db, current, job_id)
    sale_id = _resolve_sale_id(db, current, body.sale_id)
    locked = job.settlement is not None
    _apply_write(job, body, sale_id, lock_quote=locked)
    if not locked:
        job.quote_json = json.dumps(_quote_for(job, settings, _factors_for(db, account.id)))
    db.commit()
    db.refresh(job)
    return _job_out(db, job, include_suggestion=True)


@router.delete("/jobs/{job_id}", status_code=204)
def delete_job(
    job_id: str,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    _require_ops(current)
    job = _get_job(db, current, job_id)
    if job.settlement is not None:
        db.delete(job.settlement)
    db.delete(job)
    db.commit()


@router.put("/jobs/{job_id}/settlement", response_model=OpsJobOut)
def save_settlement(
    job_id: str,
    body: OpsSettlementIn,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OpsJobOut:
    _require_ops(current)
    account = _account(db, current)
    settings = _ensure_settings(db, account, current)
    job = _get_job(db, current, job_id)
    quote = _parse_quote(job.quote_json)
    if not quote:
        raise HTTPException(status_code=400, detail="Save the quote before settling the job.")
    avg = _average_pay(db, account.id)
    hourly = avg * (1 + float(settings.burden_rate)) if avg else float(settings.bill_rate) * float(settings.labor_target)
    existing = job.settlement
    if existing is None:
        existing = OpsSettlement(job_id=job.id, settled_on=_today())
        db.add(existing)
        job.settlement = existing
    existing.actual_hours = max(0.0, float(body.actual_hours or 0))
    existing.in_person_proceeds = max(0.0, float(body.in_person_proceeds or 0))
    existing.online_proceeds = max(0.0, float(body.online_proceeds or 0))
    existing.buyer_premium = max(0.0, float(body.buyer_premium or 0))
    existing.shipping = max(0.0, float(body.shipping or 0))
    existing.misc_revenue = max(0.0, float(body.misc_revenue or 0))
    existing.direct_expenses = max(0.0, float(body.direct_expenses or 0))
    existing.deposit_received = max(0.0, float(body.deposit_received or 0))
    existing.balance_received = max(0.0, float(body.balance_received or 0))
    if not existing.hourly_labor_cost:
        existing.hourly_labor_cost = hourly
    existing.commission_rate = float(settings.commission_rate)
    existing.minimum_fee = float(settings.minimum_fee)
    existing.royalty_rate = float(settings.royalty_rate)
    if not existing.settled_on:
        existing.settled_on = _today()
    job.status = "settled"
    db.commit()
    db.refresh(job)
    return _job_out(db, job, include_suggestion=True)


@router.delete("/jobs/{job_id}/settlement", response_model=OpsJobOut)
def reopen_job(
    job_id: str,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OpsJobOut:
    _require_ops(current)
    job = _get_job(db, current, job_id)
    if job.settlement is not None:
        db.delete(job.settlement)
        job.settlement = None
    job.status = "in_progress"
    db.commit()
    db.refresh(job)
    return _job_out(db, job, include_suggestion=True)


@router.get("/month", response_model=OpsMonthOut)
def get_month(
    m: str | None = Query(default=None),
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OpsMonthOut:
    _require_ops(current)
    now = datetime.now(timezone.utc).astimezone()
    month = m if m and len(m) == 7 and m[4] == "-" else f"{now.year:04d}-{now.month:02d}"
    if not (len(month) == 7 and month[4] == "-" and month[:4].isdigit() and month[5:].isdigit()):
        raise HTTPException(status_code=400, detail="Month must be YYYY-MM.")
    figures = (
        db.query(OpsMonthFigures)
        .filter(OpsMonthFigures.account_id == current.account_id, OpsMonthFigures.month == month)
        .first()
    )
    rows = (
        db.query(OpsJob)
        .options(joinedload(OpsJob.settlement))
        .join(OpsSettlement, OpsSettlement.job_id == OpsJob.id)
        .filter(OpsJob.account_id == current.account_id, OpsSettlement.settled_on.like(f"{month}%"))
        .order_by(OpsSettlement.settled_on.desc())
        .all()
    )
    jobs: list[OpsMonthJobOut] = []
    for job in rows:
        if not job.settlement or not _parse_quote(job.quote_json):
            continue
        out = _job_out(db, job)
        if not out.settlement or not out.settlement_result:
            continue
        jobs.append(OpsMonthJobOut(job=out, settlement=out.settlement, result=out.settlement_result))
    return OpsMonthOut(
        month=month,
        consultations=figures.consultations if figures else 0,
        marketing_spend=figures.marketing_spend if figures else 0.0,
        jobs=jobs,
    )


@router.put("/month", response_model=OpsMonthOut)
def save_month(
    body: OpsMonthFiguresIn,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OpsMonthOut:
    _require_ops(current)
    row = (
        db.query(OpsMonthFigures)
        .filter(OpsMonthFigures.account_id == current.account_id, OpsMonthFigures.month == body.month)
        .first()
    )
    if row is None:
        row = OpsMonthFigures(account_id=current.account_id, month=body.month)
        db.add(row)
    row.consultations = int(body.consultations)
    row.marketing_spend = float(body.marketing_spend)
    db.commit()
    return get_month(m=body.month, current=current, db=db)

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

Density = Literal["low", "medium", "high", "hoarder"]
Liquidation = Literal["none", "estate", "auction", "flash"]
OpsJobStatus = Literal["lead", "quoted", "proposal", "signed", "in_progress", "settled"]
ServiceKey = Literal[
    "sorting", "cleanout", "packing", "resettling", "estate", "auction", "flash"
]


class OpsRatesIn(BaseModel):
    location_name: str = Field(min_length=1, max_length=255)
    owner_name: str = Field(min_length=1, max_length=255)
    bill_rate: float = Field(ge=0)
    labor_target: float = Field(ge=0)
    burden_rate: float = Field(ge=0)
    royalty_rate: float = Field(ge=0)
    deposit_rate: float = Field(ge=0)
    card_fee_rate: float = Field(ge=0)
    commission_rate: float = Field(ge=0)
    minimum_fee: float = Field(ge=0)


class OpsRatesOut(OpsRatesIn):
    account_id: str


class OpsFactorsIn(BaseModel):
    factors: dict[str, dict[str, float]]


class OpsEmployeeIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    pay_rate: float = Field(ge=0)


class OpsEmployeeOut(BaseModel):
    id: str
    name: str
    pay_rate: float


class OpsSettingsOut(BaseModel):
    rates: OpsRatesOut
    factors: dict[str, dict[str, float]]
    employees: list[OpsEmployeeOut]


class OpsJobWrite(BaseModel):
    client_name: str = Field(min_length=1, max_length=255)
    phone: str = ""
    email: str = ""
    address: str = ""
    zip: str = ""
    cuft: float = Field(default=0, ge=0)
    density: Density = "medium"
    liquidation: Liquidation = "none"
    include_sorting: bool = True
    include_cleanout: bool = False
    include_packing: bool = False
    include_resettling: bool = False
    space_plan_hours: float = Field(default=0, ge=0)
    projected_proceeds: float = Field(default=0, ge=0)
    sale_id: str | None = None
    status: OpsJobStatus = "quoted"
    notes: str = ""

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_sqft(cls, data: Any) -> Any:
        if isinstance(data, dict) and data.get("cuft") is None and data.get("sqft") is not None:
            data = {**data, "cuft": data["sqft"]}
        return data


class OpsSettlementIn(BaseModel):
    actual_hours: float = Field(default=0, ge=0)
    in_person_proceeds: float = Field(default=0, ge=0)
    online_proceeds: float = Field(default=0, ge=0)
    buyer_premium: float = Field(default=0, ge=0)
    shipping: float = Field(default=0, ge=0)
    misc_revenue: float = Field(default=0, ge=0)
    direct_expenses: float = Field(default=0, ge=0)
    deposit_received: float = Field(default=0, ge=0)
    balance_received: float = Field(default=0, ge=0)


class OpsSettlementOut(OpsSettlementIn):
    job_id: str
    hourly_labor_cost: float
    commission_rate: float
    minimum_fee: float
    royalty_rate: float
    settled_on: str


class OpsJobOut(BaseModel):
    id: str
    account_id: str
    client_name: str
    phone: str
    email: str
    address: str
    zip: str
    cuft: float
    density: str
    liquidation: str
    include_sorting: bool
    include_cleanout: bool
    include_packing: bool
    include_resettling: bool
    space_plan_hours: float
    projected_proceeds: float
    sale_id: str | None = None
    sale_name: str | None = None
    status: str
    notes: str
    quote: dict[str, Any] | None = None
    settlement: OpsSettlementOut | None = None
    settlement_result: dict[str, Any] | None = None
    client_total: float | None = None
    suggested_factor: int | None = None
    suggested_service: str | None = None
    created_at: datetime
    settled_on: str | None = None


class OpsMonthFiguresIn(BaseModel):
    month: str = Field(pattern=r"^\d{4}-\d{2}$")
    consultations: int = Field(ge=0)
    marketing_spend: float = Field(ge=0)


class OpsMonthJobOut(BaseModel):
    job: OpsJobOut
    settlement: OpsSettlementOut
    result: dict[str, Any]


class OpsMonthOut(BaseModel):
    month: str
    consultations: int
    marketing_spend: float
    jobs: list[OpsMonthJobOut]


class OpsFactorApplyIn(BaseModel):
    service: ServiceKey
    density: Density
    factor: float = Field(gt=0)

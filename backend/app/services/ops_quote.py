"""Quote / settlement math for franchise operations jobs.

Port of Enterprise System ``src/lib/quote.ts``. Keep behavior identical: hours are
cubic footage ÷ density factor, sale fee is max(prep, percent of proceeds, minimum),
and royalty base includes proceeds without stacking commission on top of them.
"""

from __future__ import annotations

from typing import Literal

DENSITIES = ("low", "medium", "high", "hoarder")
Density = Literal["low", "medium", "high", "hoarder"]

LIQUIDATIONS = ("none", "estate", "auction", "flash")
Liquidation = Literal["none", "estate", "auction", "flash"]

SERVICE_KEYS = (
    "sorting",
    "cleanout",
    "packing",
    "resettling",
    "estate",
    "auction",
    "flash",
)
ServiceKey = Literal[
    "sorting", "cleanout", "packing", "resettling", "estate", "auction", "flash"
]

QuoteBucket = Literal["setup", "cleanout", "relocation", "liquidation"]
FeeWinner = Literal["none", "prep", "percent", "minimum"]
Factors = dict[str, dict[str, float]]

DEFAULT_FACTORS: Factors = {
    "sorting": {"low": 50, "medium": 30, "high": 15, "hoarder": 10},
    "cleanout": {"low": 130, "medium": 90, "high": 45, "hoarder": 12},
    "packing": {"low": 80, "medium": 55, "high": 40, "hoarder": 20},
    "resettling": {"low": 70, "medium": 45, "high": 30, "hoarder": 15},
    "estate": {"low": 50, "medium": 30, "high": 16, "hoarder": 7},
    "auction": {"low": 50, "medium": 30, "high": 16, "hoarder": 7},
    "flash": {"low": 200, "medium": 130, "high": 80, "hoarder": 56},
}

SERVICE_LABELS: dict[str, str] = {
    "sorting": "Sorting and organizing",
    "cleanout": "Cleanout",
    "packing": "Packing",
    "resettling": "Resettling",
    "estate": "Estate sale prep",
    "auction": "Online auction prep",
    "flash": "Flash sale prep",
}

DENSITY_LABELS: dict[str, str] = {
    "low": "Low",
    "medium": "Medium",
    "high": "High",
    "hoarder": "Hoarder",
}

LIQUIDATION_LABELS: dict[str, str] = {
    "none": "No sale",
    "estate": "In-person estate sale",
    "auction": "Online auction",
    "flash": "Flash sale",
}


def clone_factors(source: Factors | None = None) -> Factors:
    base = source or DEFAULT_FACTORS
    return {service: dict(densities) for service, densities in base.items()}


def primary_service(
    liquidation: str,
    include_sorting: bool,
    include_cleanout: bool,
    include_packing: bool,
    include_resettling: bool,
) -> str | None:
    if liquidation != "none":
        return liquidation
    if include_sorting:
        return "sorting"
    if include_cleanout:
        return "cleanout"
    if include_packing:
        return "packing"
    if include_resettling:
        return "resettling"
    return None


def hours_for(cuft: float, factor: float) -> float:
    if not (cuft > 0) or not (factor > 0):
        return 0.0
    return cuft / factor


def _sum_bucket(lines: list[dict], bucket: str, billable_only: bool = False) -> float:
    total = 0.0
    for line in lines:
        if line["bucket"] != bucket:
            continue
        if billable_only and not line["billable"]:
            continue
        total += line["price"]
    return total


def fee_from(
    prep_price: float,
    proceeds: float,
    commission_rate: float,
    minimum_fee: float,
    has_sale: bool,
) -> dict:
    if not has_sale:
        return {"percent_fee": 0.0, "commission": 0.0, "fee_winner": "none"}
    percent_fee = max(0.0, proceeds) * max(0.0, commission_rate)
    minimum = max(0.0, minimum_fee)
    prep = max(0.0, prep_price)
    commission = max(prep, percent_fee, minimum)
    fee_winner: FeeWinner = "minimum"
    if commission == prep and prep >= percent_fee and prep >= minimum:
        fee_winner = "prep"
    elif commission == percent_fee and percent_fee >= minimum:
        fee_winner = "percent"
    return {"percent_fee": percent_fee, "commission": commission, "fee_winner": fee_winner}


def royalty_base_for(hourly_invoiced: float, proceeds: float, extras: float, commission: float) -> float:
    base = max(0.0, hourly_invoiced) + max(0.0, proceeds) + max(0.0, extras)
    if proceeds > 0:
        return base
    return base + max(0.0, commission)


def build_quote(
    *,
    cuft: float,
    density: str,
    liquidation: str,
    include_sorting: bool,
    include_cleanout: bool,
    include_packing: bool,
    include_resettling: bool,
    space_plan_hours: float,
    projected_proceeds: float,
    bill_rate: float,
    labor_target: float,
    royalty_rate: float,
    deposit_rate: float,
    commission_rate: float,
    minimum_fee: float,
    factors: Factors,
) -> dict:
    bill = max(0.0, bill_rate)
    lines: list[dict] = []

    def add(key: str, label: str, hours: float, bucket: str, billable: bool) -> None:
        if hours <= 0 and key != "space_plan":
            return
        lines.append(
            {
                "key": key,
                "label": label,
                "hours": hours,
                "price": hours * bill,
                "bucket": bucket,
                "billable": billable,
            }
        )

    if include_sorting:
        add(
            "sorting",
            SERVICE_LABELS["sorting"],
            hours_for(cuft, factors["sorting"][density]),
            "setup",
            True,
        )
    if space_plan_hours > 0:
        add("space_plan", "Space planning", space_plan_hours, "relocation", True)
    if include_packing:
        add(
            "packing",
            SERVICE_LABELS["packing"],
            hours_for(cuft, factors["packing"][density]),
            "relocation",
            True,
        )
    if include_resettling:
        add(
            "resettling",
            SERVICE_LABELS["resettling"],
            hours_for(cuft, factors["resettling"][density]),
            "relocation",
            True,
        )
    if include_cleanout:
        add(
            "cleanout",
            SERVICE_LABELS["cleanout"],
            hours_for(cuft, factors["cleanout"][density]),
            "cleanout",
            True,
        )
    if liquidation != "none":
        add(
            liquidation,
            SERVICE_LABELS[liquidation],
            hours_for(cuft, factors[liquidation][density]),
            "liquidation",
            False,
        )

    hours = sum(line["hours"] for line in lines)
    hourly_invoiced = sum(line["price"] for line in lines if line["billable"])
    prep_price = _sum_bucket(lines, "liquidation")
    proceeds = 0.0 if liquidation == "none" else max(0.0, projected_proceeds)
    priced = fee_from(prep_price, proceeds, commission_rate, minimum_fee, liquidation != "none")
    client_total = hourly_invoiced + priced["commission"]
    labor_cost = hours * bill * max(0.0, labor_target)
    royalty_base = royalty_base_for(hourly_invoiced, proceeds, 0.0, priced["commission"])
    royalty = royalty_base * max(0.0, royalty_rate)

    return {
        "lines": lines,
        "hours": hours,
        "laborCost": labor_cost,
        "hourlyInvoiced": hourly_invoiced,
        "prepPrice": prep_price,
        "proceeds": proceeds,
        "percentFee": priced["percent_fee"],
        "minimumFee": 0.0 if liquidation == "none" else max(0.0, minimum_fee),
        "commission": priced["commission"],
        "feeWinner": priced["fee_winner"],
        "clientTotal": client_total,
        "deposit": client_total * max(0.0, deposit_rate),
        "royaltyBase": royalty_base,
        "royalty": royalty,
        "laborPercent": (labor_cost / client_total) if client_total > 0 else 0.0,
        "setup": _sum_bucket(lines, "setup", True),
        "cleanout": _sum_bucket(lines, "cleanout", True),
        "relocation": _sum_bucket(lines, "relocation", True),
    }


def settle(
    *,
    quote: dict,
    liquidation: str,
    in_person_proceeds: float,
    online_proceeds: float,
    buyer_premium: float,
    shipping: float,
    misc_revenue: float,
    direct_expenses: float,
    actual_hours: float,
    hourly_labor_cost: float,
    commission_rate: float,
    minimum_fee: float,
    royalty_rate: float,
) -> dict:
    proceeds = max(0.0, in_person_proceeds) + max(0.0, online_proceeds)
    prep_price = float(quote.get("prepPrice") or 0)
    has_sale = liquidation != "none" or proceeds > 0 or prep_price > 0
    priced = fee_from(
        prep_price,
        proceeds,
        commission_rate,
        minimum_fee,
        has_sale and liquidation != "none",
    )
    extras = max(0.0, buyer_premium) + max(0.0, shipping) + max(0.0, misc_revenue)
    hourly_invoiced = float(quote.get("hourlyInvoiced") or 0)
    invoiced = hourly_invoiced + priced["commission"] + extras
    labor_cost = max(0.0, actual_hours) * max(0.0, hourly_labor_cost)
    royalty_base = royalty_base_for(hourly_invoiced, proceeds, extras, priced["commission"])
    royalty = royalty_base * max(0.0, royalty_rate)
    direct = max(0.0, direct_expenses)
    return {
        "proceeds": proceeds,
        "percentFee": priced["percent_fee"],
        "commission": priced["commission"],
        "feeWinner": priced["fee_winner"],
        "invoiced": invoiced,
        "laborCost": labor_cost,
        "laborPercent": (labor_cost / invoiced) if invoiced > 0 else 0.0,
        "royaltyBase": royalty_base,
        "royalty": royalty,
        "contribution": invoiced - labor_cost - direct - royalty,
        "setup": float(quote.get("setup") or 0),
        "cleanout": float(quote.get("cleanout") or 0),
        "relocation": float(quote.get("relocation") or 0),
        "balanceDue": invoiced,
    }


def client_total_from(quote: dict | None, settlement_result: dict | None) -> float | None:
    """Sidebar / list total: settled sale fee uses actual proceeds, not the quote."""
    if settlement_result and quote:
        return float(quote.get("hourlyInvoiced") or 0) + float(settlement_result.get("commission") or 0)
    if quote:
        return float(quote.get("clientTotal") or 0)
    return None

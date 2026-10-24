"""Mirror Enterprise System ``src/lib/quote.test.ts`` so Python math stays identical."""

from app.services.ops_quote import (
    DEFAULT_FACTORS,
    auction_prep_hours,
    build_quote,
    royalty_base_for,
    settle,
    space_plan_hours_from,
)

BASE = dict(
    cuft=1500,
    density="medium",
    liquidation="estate",
    include_sorting=True,
    include_cleanout=False,
    include_packing=False,
    include_resettling=False,
    space_plan_hours=0,
    projected_proceeds=10000,
    bill_rate=75,
    labor_target=0.33,
    royalty_rate=0.06,
    deposit_rate=0.2,
    commission_rate=0.35,
    minimum_fee=2500,
    factors=DEFAULT_FACTORS,
)


def test_sorting_hours_are_cubic_footage_divided_by_the_density_factor() -> None:
    quote = build_quote(**BASE)
    sorting = next(line for line in quote["lines"] if line["key"] == "sorting")
    assert sorting["hours"] == 1500 / 30
    assert sorting["price"] == 50 * 75
    assert sorting["billable"] is True


def test_estate_prep_uses_the_estate_factor_and_is_not_billed_twice() -> None:
    quote = build_quote(**BASE)
    prep = next(line for line in quote["lines"] if line["key"] == "estate")
    assert prep["hours"] == 1500 / 30
    assert prep["billable"] is False
    assert quote["prepPrice"] == 3750
    assert quote["percentFee"] == 3500
    assert quote["commission"] == 3750
    assert quote["feeWinner"] == "prep"
    assert quote["clientTotal"] == 3750 + 3750


def test_a_large_sale_uses_the_percent_when_it_beats_prep_and_the_minimum() -> None:
    quote = build_quote(**{**BASE, "projected_proceeds": 40000})
    assert quote["percentFee"] == 14000
    assert quote["commission"] == 14000
    assert quote["feeWinner"] == "percent"
    assert quote["clientTotal"] == 3750 + 14000


def test_a_thin_sale_uses_the_minimum() -> None:
    quote = build_quote(**{**BASE, "cuft": 300, "projected_proceeds": 1000, "include_sorting": False})
    assert quote["feeWinner"] == "minimum"
    assert quote["commission"] == 2500


def test_no_sale_means_no_commission() -> None:
    quote = build_quote(**{**BASE, "liquidation": "none", "projected_proceeds": 10000})
    assert quote["commission"] == 0
    assert quote["feeWinner"] == "none"
    assert quote["royaltyBase"] == 3750
    assert quote["clientTotal"] == 3750


def test_royalty_base_includes_proceeds_and_leaves_commission_out_when_proceeds_exist() -> None:
    quote = build_quote(**BASE)
    assert quote["royaltyBase"] == 3750 + 10000
    assert royalty_base_for(3750, 0, 0, 2500) == 3750 + 2500


def test_packing_and_cleanout_add_their_own_hours() -> None:
    quote = build_quote(
        **{
            **BASE,
            "liquidation": "none",
            "include_packing": True,
            "include_cleanout": True,
            "space_plan_hours": 4,
        }
    )
    packing = next(line for line in quote["lines"] if line["key"] == "packing")
    cleanout = next(line for line in quote["lines"] if line["key"] == "cleanout")
    assert packing["hours"] == 1500 / 55
    assert cleanout["hours"] == 1500 / 90
    assert quote["relocation"] == (1500 / 55) * 75 + 4 * 75
    assert any(line["key"] == "space_plan" for line in quote["lines"])


def test_zero_cubic_footage_does_not_divide_by_zero() -> None:
    quote = build_quote(**{**BASE, "cuft": 0, "liquidation": "none"})
    assert quote["hours"] == 0
    assert quote["clientTotal"] == 0


def test_auction_lot_hours_replace_cubic_footage_and_pickup_adds_prep() -> None:
    quote = build_quote(
        **{
            **BASE,
            "liquidation": "auction",
            "include_sorting": False,
            "projected_lots": 150,
            "auction_questions": 25,
            "pickup_days": 1,
            "pickup_staff": 3,
            "pickup_hours_per_day": 3,
        }
    )
    prep = next(line for line in quote["lines"] if line["key"] == "auction")
    pickup = next(line for line in quote["lines"] if line["key"] == "auction_pickup")
    assert prep["hours"] == auction_prep_hours(150) + 2.5
    assert prep["hours"] == 59.5
    assert pickup["hours"] == 9
    assert quote["hours"] == 68.5
    assert quote["prepPrice"] == 68.5 * 75
    assert quote["hourlyInvoiced"] == 0
    assert quote["feeWinner"] == "prep"


def test_auction_without_lots_uses_the_cubic_footage_factor() -> None:
    quote = build_quote(**{**BASE, "liquidation": "auction", "include_sorting": False})
    prep = next(line for line in quote["lines"] if line["key"] == "auction")
    assert prep["hours"] == 1500 / 30
    assert not any(line["key"] == "auction_pickup" for line in quote["lines"])


def test_estate_sale_day_staffing_raises_the_prep_floor() -> None:
    quote = build_quote(
        **{
            **BASE,
            "include_sorting": False,
            "sale_days": 2,
            "sale_staff": 3,
            "sale_hours_per_day": 4,
        }
    )
    day = next(line for line in quote["lines"] if line["key"] == "estate_day")
    assert day["hours"] == 24
    assert day["billable"] is False
    assert quote["prepPrice"] == (50 + 24) * 75
    assert quote["commission"] == quote["prepPrice"]


def test_space_plan_takeoff_uses_areas_and_items_with_a_four_hour_minimum() -> None:
    assert space_plan_hours_from(3, 6, 0) == 4
    quote = build_quote(
        **{
            **BASE,
            "liquidation": "none",
            "include_sorting": False,
            "space_plan_areas": 10,
            "space_plan_items": 20,
        }
    )
    plan = next(line for line in quote["lines"] if line["key"] == "space_plan")
    assert plan["hours"] == (10 * 8 + 20 * 10) / 60
    assert quote["relocation"] == plan["hours"] * 75


def test_settlement_reprices_the_fee_from_actual_proceeds_and_keeps_service_prices() -> None:
    quote = build_quote(**BASE)
    result = settle(
        quote=quote,
        liquidation="estate",
        in_person_proceeds=40000,
        online_proceeds=0,
        buyer_premium=500,
        shipping=100,
        misc_revenue=0,
        direct_expenses=200,
        actual_hours=40,
        hourly_labor_cost=22,
        commission_rate=0.35,
        minimum_fee=2500,
        royalty_rate=0.06,
    )
    assert result["commission"] == 14000
    assert result["feeWinner"] == "percent"
    assert result["invoiced"] == 3750 + 14000 + 600
    assert result["laborCost"] == 880
    assert result["royaltyBase"] == 3750 + 40000 + 600
    assert result["contribution"] == result["invoiced"] - 880 - 200 - result["royalty"]

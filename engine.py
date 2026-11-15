"""Transparent, illustrative underwriting commission engine.

Commission means a payout as a fraction of written premium. All costs and SLR
are assumed to share this same premium base. This is demo logic, not pricing advice.
"""

from __future__ import annotations

from statistics import median
from typing import Any, Iterable
import math

REQUIRED_ASSUMPTIONS = (
    "expense_ratio", "other_cost_ratio", "target_profit_margin",
    "min_policy_count", "max_commission_rate",
)
REQUIRED_COHORT_COLUMNS = (
    "Cohort_ID", "Product", "Segment", "Region", "Risk_Tier", "Channel",
    "Policy_Count", "Expected_SLR", "Written_Premium_INR",
)
RATE_COLUMNS = ("Competitor_1_Pct", "Competitor_2_Pct", "Competitor_3_Pct")


class ModelInputError(ValueError):
    pass


def _number(value: Any, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ModelInputError(f"{field}: expected a number, got {value!r}") from None
    if not math.isfinite(result):
        raise ModelInputError(f"{field}: value must be finite")
    return result


def _fraction(value: Any, field: str) -> float:
    val = _number(value, field)
    if not (0 <= val <= 1):
        raise ModelInputError(
            f"{field}: enter rates as Excel percentages, e.g. 65% (stored as 0.65), not 65."
        )
    return val


def parse_assumptions(rows: Iterable[dict[str, Any]]) -> dict[str, float]:
    settings = {}
    for row in rows:
        key = str(row.get("Key", "")).strip()
        if not key or key.lower() == "nan":
            continue

        value = row.get("Value")
        # Spreadsheet templates can include notes or section headings (e.g.
        # a NOTE row with a blank Value). Pandas represents blank Excel cells
        # as NaN, which is not a financial assumption. Skip these rows while
        # still rejecting blanks for all REQUIRED assumptions.
        is_blank = (value is None or
                    (isinstance(value, (int, float)) and not math.isfinite(value)) or
                    (isinstance(value, str) and not value.strip()))
        if is_blank and key not in REQUIRED_ASSUMPTIONS:
            continue
        if key in settings:
            raise ModelInputError(f"Duplicate assumption: {key}")
        settings[key] = _number(value, key)
    for key in REQUIRED_ASSUMPTIONS:
        if key not in settings:
            raise ModelInputError(f"Missing assumption in Assumptions sheet: {key}")
    for key in ("expense_ratio", "other_cost_ratio", "target_profit_margin", "max_commission_rate"):
        settings[key] = _fraction(settings[key], key)
    if settings["min_policy_count"] < 0:
        raise ModelInputError("min_policy_count cannot be negative")
    return settings


def build_recommendations(
    cohort_rows: Iterable[dict[str, Any]],
    market_rows: Iterable[dict[str, Any]],
    assumptions: dict[str, float],
    market_target: str = "median",
) -> list[dict[str, Any]]:
    """Returns cohort recommendations without learning a predictive model.

    If the profitability target is infeasible at 0% commission, recommended
    commission is 0% AND the result is explicitly flagged for review/repricing.
    """
    if market_target not in ("minimum", "median", "maximum"):
        raise ModelInputError("market_target must be minimum, median, or maximum")
    market = {}
    for row in market_rows:
        cid = str(row.get("Cohort_ID", "")).strip()
        if not cid:
            continue
        if cid in market:
            raise ModelInputError(f"Duplicate market cohort: {cid}")
        quotes = []
        for field in RATE_COLUMNS:
            value = row.get(field)
            if value is not None and str(value).strip() not in ("", "nan", "NaN"):
                quotes.append(_fraction(value, f"{cid}: {field}"))
        if not quotes:
            raise ModelInputError(f"Missing commission quotes for {cid}")
        market[cid] = {"low": min(quotes), "median": median(quotes), "high": max(quotes), "quotes": len(quotes)}

    results = []
    seen = set()
    expense = assumptions["expense_ratio"]
    other = assumptions["other_cost_ratio"]
    target = assumptions["target_profit_margin"]
    cap = assumptions["max_commission_rate"]
    min_policies = assumptions["min_policy_count"]

    for row in cohort_rows:
        for field in REQUIRED_COHORT_COLUMNS:
            if field not in row:
                raise ModelInputError(f"Cohorts sheet missing column {field}")
        cid = str(row["Cohort_ID"]).strip()
        if not cid:
            raise ModelInputError("Cohorts sheet contains a blank Cohort_ID")
        if cid in seen:
            raise ModelInputError(f"Duplicate cohort: {cid}")
        seen.add(cid)
        if cid not in market:
            raise ModelInputError(f"No market benchmark for {cid}")
        slr = _fraction(row["Expected_SLR"], f"{cid}: Expected_SLR")
        policies = _number(row["Policy_Count"], f"{cid}: Policy_Count")
        premium = _number(row["Written_Premium_INR"], f"{cid}: Written_Premium_INR")
        if policies < 0 or premium <= 0:
            raise ModelInputError(f"{cid}: Policy_Count must be >= 0 and premium > 0")
        quotes = market[cid]
        market_rate = quotes[{"minimum": "low", "median": "median", "maximum": "high"}[market_target]]
        # Margin >= target iff commission <= raw_headroom (same premium base).
        raw_headroom = 1.0 - slr - expense - other - target
        affordable = max(0.0, min(cap, raw_headroom))
        recommended = min(market_rate, affordable)
        actual_margin = 1.0 - slr - expense - other - recommended
        flags = []
        if raw_headroom < -1e-9:
            flags.append("Profit target infeasible even at 0% commission")
        if recommended < quotes["low"] - 1e-9:
            flags.append("Below lowest quoted market commission")
        if policies < min_policies:
            flags.append("Limited cohort experience: review SLR credibility")
        status = "Manual review" if flags else "Market aligned"
        results.append({
            "Cohort_ID": cid,
            "Product": str(row["Product"]),
            "Segment": str(row["Segment"]),
            "Region": str(row["Region"]),
            "Risk_Tier": str(row["Risk_Tier"]),
            "Channel": str(row["Channel"]),
            "Policy_Count": int(policies),
            "Written_Premium_INR": premium,
            "Expected_SLR": slr,
            "Market_Min_Pct": quotes["low"],
            "Market_Median_Pct": quotes["median"],
            "Market_Max_Pct": quotes["high"],
            "Market_Target_Pct": market_rate,
            "Raw_Affordable_Pct": raw_headroom,
            "Max_Affordable_Pct": affordable,
            "Recommended_Pct": recommended,
            "Expected_Profit_Margin_Pct": actual_margin,
            "Gap_To_Market_Points": 100 * (recommended - market_rate),
            "Status": status,
            "Why": "; ".join(flags) if flags else "Market target fits profitability constraint",
        })
    if not results:
        raise ModelInputError("Cohorts sheet contains no data")
    return results


def simulate_deal(
    cohort: dict[str, Any],
    deal_premium_inr: float,
    commission_rate: float,
    assumptions: dict[str, float],
    slr_adjustment_pp: float = 0.0,
    extra_cost_ratio: float = 0.0,
) -> dict[str, Any]:
    premium = _number(deal_premium_inr, "Deal premium")
    commission = _fraction(commission_rate, "Commission")
    if premium <= 0:
        raise ModelInputError("Deal premium must be positive")
    slr_adjusted = cohort["Expected_SLR"] + _number(slr_adjustment_pp, "SLR adjustment") / 100
    extra = _fraction(extra_cost_ratio, "Extra deal cost")
    if not (0 <= slr_adjusted <= 1):
        raise ModelInputError("Adjusted SLR must stay between 0% and 100%")
    margin = 1.0 - slr_adjusted - assumptions["expense_ratio"] - assumptions["other_cost_ratio"] - extra - commission
    return {
        "commission_amount_inr": premium * commission,
        "expected_loss_inr": premium * slr_adjusted,
        "adjusted_slr": slr_adjusted,
        "extra_cost_ratio": extra,
        "other_costs_inr": premium * (assumptions["expense_ratio"] + assumptions["other_cost_ratio"] + extra),
        "expected_contribution_inr": premium * margin,
        "expected_margin": margin,
        "target_margin": assumptions["target_profit_margin"],
        "meets_profit_target": margin >= assumptions["target_profit_margin"] - 1e-9,
        "within_commission_cap": commission <= assumptions["max_commission_rate"] + 1e-9,
    }


def rebalance_portfolio(
    cohorts: list[dict[str, Any]],
    selected_id: str,
    override_rate: float,
    assumptions: dict[str, float],
    preserve: str = "baseline",
    floor_policy: str = "market_floor",
) -> dict[str, Any]:
    """Allocate a manual commission override across remaining cohorts.

    The preserved quantity is expected aggregate profit in INR, not the
    unweighted average of cohort margins. Premium volumes are held constant.
    No demand response, SLR feedback, or risk correlation is modelled.
    """
    if preserve not in ("baseline", "target_margin"):
        raise ModelInputError("preserve must be baseline or target_margin")
    if floor_policy not in ("market_floor", "zero"):
        raise ModelInputError("floor_policy must be market_floor or zero")
    override = _fraction(override_rate, "Underwriter override")
    ids = [str(c["Cohort_ID"]) for c in cohorts]
    if len(ids) != len(set(ids)) or selected_id not in ids:
        raise ModelInputError("Select a unique cohort present in the portfolio")
    if not cohorts:
        raise ModelInputError("Portfolio cannot be empty")
    rows = []
    total_premium = 0.0
    baseline_profit = 0.0
    for c in cohorts:
        cid = str(c["Cohort_ID"])
        premium = _number(c["Written_Premium_INR"], f"{cid} premium")
        if premium <= 0:
            raise ModelInputError(f"{cid} premium must be positive")
        rate = _fraction(c["Recommended_Pct"], f"{cid} recommended rate")
        slr = _fraction(c["Expected_SLR"], f"{cid} SLR")
        market_low = _fraction(c["Market_Min_Pct"], f"{cid} market minimum")
        margin_before = 1 - slr - assumptions["expense_ratio"] - assumptions["other_cost_ratio"] - rate
        total_premium += premium
        baseline_profit += margin_before * premium
        rows.append({
            "Cohort_ID": cid,
            "Product": c.get("Product", ""),
            "Risk_Tier": c.get("Risk_Tier", ""),
            "Written_Premium_INR": premium,
            "Market_Min_Pct": market_low,
            "Market_Target_Pct": c["Market_Target_Pct"],
            "Baseline_Pct": rate,
            "Rebalanced_Pct": override if cid == selected_id else rate,
            "Expected_SLR": slr,
            "Margin_Before_Pct": margin_before,
            "Note": "Underwriter override" if cid == selected_id else "Unchanged",
        })
    selected = next(r for r in rows if r["Cohort_ID"] == selected_id)
    override_cost = (override - selected["Baseline_Pct"]) * selected["Written_Premium_INR"]
    current_profit = baseline_profit - override_cost
    target_profit = (baseline_profit if preserve == "baseline"
                     else assumptions["target_profit_margin"] * total_premium)
    gap = max(0.0, target_profit - current_profit)
    # Do not reduce other cohorts unless the selected override creates a gap.
    # Market floor mode never makes an already-below-market cohort worse.
    candidates = []
    for r in rows:
        if r["Cohort_ID"] == selected_id:
            continue
        floor = (min(r["Baseline_Pct"], r["Market_Min_Pct"])
                 if floor_policy == "market_floor" else 0.0)
        room = max(0.0, r["Baseline_Pct"] - floor)
        candidates.append((room, r["Written_Premium_INR"], r))
    # Prefer cohorts with more commission headroom; use premium volume as
    # tie-breaker. This is a transparent heuristic, not economic optimisation.
    candidates.sort(key=lambda x: (-x[0], -x[1], x[2]["Cohort_ID"]))
    total_capacity = sum(room * premium for room, premium, _ in candidates)
    unmet = gap
    for room, premium, r in candidates:
        take = min(unmet, room * premium)
        if take > 1e-7:
            r["Rebalanced_Pct"] -= take / premium
            r["Note"] = "Suggested offset"
            unmet -= take
        if unmet <= 1e-6:
            unmet = 0.0
            break
    after_profit = 0.0
    for r in rows:
        p = r["Written_Premium_INR"]
        r["Delta_Points"] = (r["Rebalanced_Pct"] - r["Baseline_Pct"]) * 100
        r["Commission_Change_INR"] = p * (r["Rebalanced_Pct"] - r["Baseline_Pct"])
        r["Margin_After_Pct"] = r["Margin_Before_Pct"] - (r["Rebalanced_Pct"] - r["Baseline_Pct"])
        r["Profit_Change_INR"] = -r["Commission_Change_INR"]
        after_profit += p * r["Margin_After_Pct"]
        if r["Rebalanced_Pct"] < r["Market_Min_Pct"] - 1e-8:
            r["Note"] += "; below market minimum"
        if r["Rebalanced_Pct"] > assumptions["max_commission_rate"] + 1e-8:
            r["Note"] += "; exceeds commission cap"
        if r["Margin_After_Pct"] < assumptions["target_profit_margin"] - 1e-8:
            r["Note"] += "; below cohort profit target"
    feasible = after_profit >= target_profit - 0.01
    return {
        "rows": rows,
        "baseline_profit_inr": baseline_profit,
        "post_override_profit_inr": current_profit,
        "after_profit_inr": after_profit,
        "target_profit_inr": target_profit,
        "total_premium_inr": total_premium,
        "baseline_margin": baseline_profit / total_premium,
        "after_margin": after_profit / total_premium,
        "override_cost_inr": override_cost,
        "needed_offset_inr": gap,
        "available_offset_inr": total_capacity,
        "unfunded_inr": max(0.0, target_profit - after_profit),
        "feasible": feasible,
        "adjusted_count": sum(r["Note"].startswith("Suggested offset") for r in rows),
    }

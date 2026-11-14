# FinLeadAI — Commission Underwriting Prototype

A **demo only** deterministic commission engine using synthetic insurance-like cohorts, illustrative expected SLRs and fictional market commission quotes.

## Start locally

```bash
python -m pip install -r requirements.txt
streamlit run app.py
```

The app automatically loads `dummy_commission_inputs.xlsx`. You can also upload a different `.xlsx` file using the same tab names and columns.

## Input workbook schema

- **Cohorts**: `Cohort_ID`, `Product`, `Segment`, `Region`, `Risk_Tier`, `Channel`, `Policy_Count`, `Expected_SLR`, `Written_Premium_INR`; optionally `Experience_Period`.
- **Market_Rates**: `Cohort_ID`, `Competitor_1_Pct`, `Competitor_2_Pct`, `Competitor_3_Pct`. You may leave one or two competitor quotes blank, but not all three. There must be a matching `Cohort_ID` for each cohort.
- **Assumptions**: `Key`, `Value`, `Meaning`. Required keys: `expense_ratio`, `other_cost_ratio`, `target_profit_margin`, `min_policy_count`, `max_commission_rate`.
- **Example_Deals** and **Results_Preview** are optional for the app; the latter contains workbook formulas for reference only.

Enter **percentages as percentages in Excel** (65% displays as `65%`, internally `0.65`). The app validates this. Do not type `65` as a plain numeric value.

The **Deal simulator** also accepts a deal-specific expected SLR adjustment and incremental contract/servicing costs. It recomputes the affordability cap and checks manual override proposals.

## Formula and interpretation

Assumptions for this demo:

- Commission is a **payout** to an intermediary, on written premium.
- `Expected_SLR` is an expected ultimate claims cost divided by the **same premium base** used for commission and other ratios.
- Operating expense, other cost, and target profit ratios share that premium denominator.
- The calculation does not model taxes, reinsurance, solvency capital, discounting, lags, credibility correction, or customer responses to commission changes.

```text
raw_capacity = 1 - Expected_SLR - expense_ratio - other_cost_ratio - target_profit_margin
max_affordable = max(0, min(raw_capacity, max_commission_rate))
recommended = min(target_market_quote, max_affordable)
expected_margin = 1 - Expected_SLR - expense_ratio - other_cost_ratio - recommended
```

`target_market_quote` defaults to median competitor quote and can be changed to minimum or maximum. Results with a commission below the minimum market quote, inadequate policy count, or an infeasible profit target are flagged for human review.

**This tool does not claim that a market rate will win the deal**, nor does it prove the loss ratios are unbiased or credible. It protects the *assumed* financial hurdle within this simplified framework.

## Deployment: Streamlit Community Cloud

1. Put `app.py`, `engine.py`, `requirements.txt`, and `dummy_commission_inputs.xlsx` in a GitHub repository. (You can include `test_engine.py` and this README too.)
2. In Streamlit Community Cloud, create an app from your GitHub repository and set `app.py` as the entry point.
3. Share the resulting demo URL with the interviewers.

Free hosting availability and limits are subject to Streamlit's published policies. Keep only non-sensitive synthetic data in this public demo.

If Vercel is mandatory, a Next.js rewrite is the more natural deployment architecture; this Python/Streamlit app is designed for a Python hosting provider instead.

## Test

```bash
python -m unittest test_engine.py -v
```

## First changes when real inputs arrive

1. Confirm if **commission is paid or charged** and what monetary denominator it uses.
2. Confirm what **SLR** means, how it is derived, and whether it is on the correct revenue/premium basis.
3. Replace fabricated cohort SLRs and competitor quotes.
4. Calibrate expenses and the target margin; adjust exception thresholds.
5. Validate results on manual underwriting examples before presenting claims of suitability.

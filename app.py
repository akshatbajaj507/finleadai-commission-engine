"""Streamlit demo: upload the sample Excel workbook or use the bundled sample."""
from pathlib import Path
from io import BytesIO

import pandas as pd
import streamlit as st

from engine import ModelInputError, build_recommendations, parse_assumptions, simulate_deal, rebalance_portfolio

st.set_page_config(page_title="Commission Underwriting | Demo", page_icon="🧮", layout="wide")

SAMPLE = Path(__file__).with_name("dummy_commission_inputs.xlsx")
REQUIRED_TABS = ("Cohorts", "Market_Rates", "Assumptions")


@st.cache_data(show_spinner=False)
def load_workbook(raw_bytes: bytes):
    return pd.read_excel(BytesIO(raw_bytes), sheet_name=None, engine="calamine")


def pct(v):
    return f"{100 * v:.1f}%"


def inr(v):
    return f"₹{v:,.0f}"


st.title("Commission Underwriting Engine")
st.caption("FinLeadAI interview prototype • synthetic data • rule-based, explainable recommendation")
st.info("**Scope assumption:** Commission is a payout to an intermediary, expressed as a % of premium. Expected SLR, costs and profit margin use the same premium base. Real business definitions must be confirmed.")

with st.sidebar:
    st.header("1. Load data")
    uploaded = st.file_uploader("Upload commission input workbook", type=["xlsx"], help="Use the format in the downloadable sample workbook")
    if SAMPLE.exists():
        st.download_button("Download sample Excel", data=SAMPLE.read_bytes(), file_name=SAMPLE.name,
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    st.header("2. Commission positioning")
    market_target = st.selectbox("Target market rate", ["median", "minimum", "maximum"], index=0)
    st.caption("Rules and ratios are read from the Assumptions sheet. No hidden model coefficients.")

if uploaded:
    blob = uploaded.getvalue()
elif SAMPLE.exists():
    blob = SAMPLE.read_bytes()
else:
    st.error("No sample workbook found. Upload an Excel file to continue.")
    st.stop()

try:
    sheets = load_workbook(blob)
    missing_tabs = [tab for tab in REQUIRED_TABS if tab not in sheets]
    if missing_tabs:
        raise ModelInputError(f"Workbook must contain: {', '.join(REQUIRED_TABS)}. Missing: {', '.join(missing_tabs)}")
    assumptions = parse_assumptions(sheets["Assumptions"].to_dict("records"))
    result_rows = build_recommendations(
        sheets["Cohorts"].to_dict("records"),
        sheets["Market_Rates"].to_dict("records"),
        assumptions, market_target,
    )
except (ModelInputError, ValueError, KeyError, ImportError) as exc:
    st.error(f"Input validation failed: {exc}")
    st.stop()

all_results = pd.DataFrame(result_rows)
product_options = sorted(all_results["Product"].unique())
selected_products = st.multiselect("Filter products", product_options, default=product_options)
filtered = all_results[all_results["Product"].isin(selected_products)].copy()
if filtered.empty:
    st.warning("Select at least one product to show results.")
    st.stop()

tab1, tab2, tab3, tab4, tab5 = st.tabs(["Portfolio overview", "Deal simulator", "Portfolio rebalancing", "Calculation methodology", "Input data"])

with tab1:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Cohorts", len(filtered))
    c2.metric("Market-aligned", int((filtered["Status"] == "Market aligned").sum()))
    c3.metric("Manual reviews", int((filtered["Status"] == "Manual review").sum()))
    avg_slr = (filtered["Expected_SLR"] * filtered["Written_Premium_INR"]).sum() / filtered["Written_Premium_INR"].sum()
    c4.metric("Premium-weighted SLR", pct(avg_slr))
    st.subheader("Cohort commission recommendations")
    cols = ["Cohort_ID", "Product", "Risk_Tier", "Policy_Count", "Expected_SLR", "Market_Min_Pct", "Market_Median_Pct", "Max_Affordable_Pct", "Recommended_Pct", "Expected_Profit_Margin_Pct", "Status", "Why"]
    st.dataframe(filtered[cols].style.format({k: "{:.1%}" for k in ("Expected_SLR", "Market_Min_Pct", "Market_Median_Pct", "Max_Affordable_Pct", "Recommended_Pct", "Expected_Profit_Margin_Pct")}), use_container_width=True, hide_index=True)
    st.subheader("Market vs recommended commission")
    comparison = filtered.set_index("Cohort_ID")[["Market_Target_Pct", "Recommended_Pct"]] * 100
    comparison.columns = ["Target market %", "Recommended %"]
    st.bar_chart(comparison)
    st.download_button("Export all results (CSV)", all_results.to_csv(index=False).encode("utf-8-sig"),
                       file_name="commission_recommendations.csv", mime="text/csv")

with tab2:
    st.subheader("Underwriter deal desk")
    selected_id = st.selectbox("Cohort", filtered["Cohort_ID"].tolist())
    row = filtered.loc[filtered["Cohort_ID"] == selected_id].iloc[0].to_dict()
    a, b = st.columns(2)
    with a:
        premium = st.number_input("Deal premium (₹)", min_value=1_000.0, value=1_000_000.0, step=100_000.0)
        slr_stress_pp = st.number_input("Deal-specific SLR adjustment (percentage points)", min_value=-30.0, max_value=30.0, value=0.0, step=0.5, help="Example: +3 raises a 68% SLR to 71%. Negative values assume a risk improvement.")
    with b:
        extra_cost_pct = st.number_input("Extra contractual/servicing cost (%)", min_value=0.0, max_value=30.0, value=0.0, step=0.5, help="Additional costs of special deal terms, as % of premium.")
    adjusted_slr = row["Expected_SLR"] + slr_stress_pp / 100.0
    if not 0 <= adjusted_slr <= 1:
        st.error("Adjusted SLR must remain within 0% to 100%.")
        st.stop()
    custom_headroom = 1 - adjusted_slr - assumptions["expense_ratio"] - assumptions["other_cost_ratio"] - extra_cost_pct / 100 - assumptions["target_profit_margin"]
    custom_cap = max(0.0, min(assumptions["max_commission_rate"], custom_headroom))
    custom_recommendation = min(row["Market_Target_Pct"], custom_cap)
    override = st.toggle("Manual commission override", value=False)
    if override:
        rate_input = st.number_input("Proposed commission (%)", min_value=0.0, max_value=40.0, value=float(round(custom_recommendation * 100, 2)), step=0.1) / 100
    else:
        rate_input = custom_recommendation
    simulation = simulate_deal(row, premium, rate_input, assumptions, slr_stress_pp, extra_cost_pct / 100)
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Recommended", pct(custom_recommendation))
    k2.metric("Applied", pct(rate_input))
    k3.metric("Commission payout", inr(simulation["commission_amount_inr"]))
    k4.metric("Expected contribution", inr(simulation["expected_contribution_inr"]))
    st.markdown(f"**Expected profit margin:** {pct(simulation['expected_margin'])} · **Target:** {pct(simulation['target_margin'])}")
    if not simulation["meets_profit_target"] or not simulation["within_commission_cap"]:
        st.error("Approval required: this proposal breaches the profit target and/or policy commission cap.")
    elif custom_headroom < -1e-9 or custom_recommendation < row["Market_Min_Pct"] - 1e-9 or row["Policy_Count"] < assumptions["min_policy_count"]:
        st.warning("Manual underwriting review required: target infeasibility, below-market recommendation and/or limited experience.")
    else:
        st.success("Meets illustrative profitability and commission constraints.")
    st.markdown("**Recommendation rationale**")
    st.write(f"Market target: {pct(row['Market_Target_Pct'])}; adjusted max affordable: {pct(custom_cap)}; deal recommendation: {pct(custom_recommendation)}.")
    st.write(f"Calculation: 100% − adjusted SLR {pct(adjusted_slr)} − expenses {pct(assumptions['expense_ratio'])} − other costs {pct(assumptions['other_cost_ratio'])} − extra deal costs {extra_cost_pct:.1f}% − commission {pct(rate_input)} = expected margin {pct(simulation['expected_margin'])}.")


with tab3:
    st.subheader("Underwriter discretion & portfolio rebalancing")
    st.write("Override the commission for one cohort. The engine proposes reductions in other cohorts to protect portfolio-wide expected profitability, weighted by written premium.")
    st.info("This simulation applies to the **entire uploaded portfolio**, regardless of the product filter above. It assumes unchanged premium volumes and SLRs.")
    choose = st.selectbox("Cohort to override", all_results["Cohort_ID"].tolist(), key="rebalance_cohort")
    chosen = all_results.set_index("Cohort_ID").loc[choose]
    first, second, third = st.columns(3)
    with first:
        st.metric("Current commission", pct(chosen["Recommended_Pct"]))
        st.metric("Market benchmark", pct(chosen["Market_Target_Pct"]))
    with second:
        discretionary_rate = st.number_input("Your proposed commission (%)", min_value=0.0, max_value=100.0,
                                             value=float(round(chosen["Recommended_Pct"] * 100, 2)), step=0.25,
                                             key="portfolio_override") / 100
        preserve = st.radio("Protect", ["Current portfolio profit (₹)", "Minimum target portfolio margin"],
                            index=0, help="Current profit preserves the baseline expected contribution in rupees. The minimum target option permits use of existing headroom.")
    with third:
        market_floor = st.checkbox("Respect market minimum for offset cohorts", value=True,
                                   help="Never reduce another cohort below its lowest market quote. Cohorts already below the minimum cannot be reduced further.")
        st.caption("Turning this off permits cuts down to 0%, which may make affected cohorts uncompetitive.")
    plan = rebalance_portfolio(
        all_results.to_dict("records"), choose, discretionary_rate, assumptions,
        preserve="baseline" if preserve.startswith("Current") else "target_margin",
        floor_policy="market_floor" if market_floor else "zero",
    )
    a,b,c,d = st.columns(4)
    a.metric("Original portfolio margin", pct(plan["baseline_margin"]))
    b.metric("Rebalanced margin", pct(plan["after_margin"]), delta=f"{(plan['after_margin']-plan['baseline_margin'])*100:+.2f} pp")
    c.metric("Override cost / savings", inr(plan["override_cost_inr"]))
    d.metric("Other cohorts adjusted", plan["adjusted_count"])
    if discretionary_rate > assumptions["max_commission_rate"] + 1e-9:
        st.warning("The selected cohort override exceeds the policy commission cap. Approval required even if portfolio margin is protected.")
    if plan["feasible"]:
        st.success(f"Portfolio profitability constraint satisfied. Expected portfolio profit: {inr(plan['after_profit_inr'])}; required: {inr(plan['target_profit_inr'])}.")
    else:
        st.error(f"No feasible rebalancing under these constraints. Unfunded expected profit shortfall: {inr(plan['unfunded_inr'])}. Consider reducing the override, relaxing market floors with approval, or repricing.")
    if not market_floor:
        st.warning("Market floors have been relaxed. Review commercial competitiveness of every suggested cut.")
    st.caption(f"Estimated offset needed: {inr(plan['needed_offset_inr'])} · Available offset capacity: {inr(plan['available_offset_inr'])} · Profit before override: {inr(plan['baseline_profit_inr'])} · Profit after override before offsets: {inr(plan['post_override_profit_inr'])}")
    plan_df = pd.DataFrame(plan["rows"])
    view = ["Cohort_ID", "Product", "Risk_Tier", "Written_Premium_INR", "Market_Min_Pct", "Baseline_Pct", "Rebalanced_Pct", "Delta_Points", "Commission_Change_INR", "Margin_Before_Pct", "Margin_After_Pct", "Note"]
    st.dataframe(plan_df[view].style.format({"Written_Premium_INR":"₹{:,.0f}", "Commission_Change_INR":"₹{:,.0f}",
        "Market_Min_Pct":"{:.1%}", "Baseline_Pct":"{:.2%}", "Rebalanced_Pct":"{:.2%}",
        "Margin_Before_Pct":"{:.2%}", "Margin_After_Pct":"{:.2%}", "Delta_Points":"{:+.2f}"}),
        use_container_width=True, hide_index=True)
    st.subheader("Commission changes by cohort (percentage points)")
    st.bar_chart(plan_df.set_index("Cohort_ID")["Delta_Points"])
    st.download_button("Download rebalancing proposal (CSV)", plan_df.to_csv(index=False).encode("utf-8-sig"),
                       file_name="underwriter_rebalancing_proposal.csv", mime="text/csv")
    st.caption("Demonstration heuristic: prioritise offsets in cohorts with the most room above market minimum, then larger premium volume. Overrides are proposals requiring underwriting approval. No new business response, capital cost or forecast uncertainty is included.")

with tab4:
    st.subheader("Transparent rule set")
    st.latex(r"C_{\max}=\max(0,\min(C_{\text{cap}},1-\mathrm{SLR}-E-O-P))")
    st.latex(r"C_{\text{recommended}}=\min(C_{\text{market target}},C_{\max})")
    st.write("If the profit target cannot be met even at 0% commission, the cohort is flagged for repricing or manual review. Low-exposure and below-market results are also flagged.")
    st.json({key: value for key, value in assumptions.items()})
    st.caption("This is a deterministic, illustrative margin-on-premium model. It does not adjust for inflation, reinsurance, capital cost, taxes, acquisition effects, credibility smoothing, or demand elasticity.")

with tab5:
    st.subheader("Uploaded workbook")
    chosen_sheet = st.selectbox("Sheet", list(sheets))
    st.dataframe(sheets[chosen_sheet], use_container_width=True, hide_index=True)
    st.caption("Blue values in the Excel sample are intended as editable inputs; formulas in its Results_Preview tab are for inspection only. The web app recalculates from the input tabs.")

"""Streamlit demo: upload the sample Excel workbook or use the bundled sample."""
from pathlib import Path
from io import BytesIO
from hashlib import sha256

import pandas as pd
import streamlit as st

from engine import ModelInputError, build_recommendations, parse_assumptions, simulate_deal, rebalance_portfolio, rebalance_deal_portfolio, rebalance_multiple_cohorts

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

# Invalidate a transferred proposal when the source data or market setting changes.
workbook_signature = sha256(blob + market_target.encode("utf-8")).hexdigest()
if st.session_state.get("deal_transfer") and st.session_state["deal_transfer"].get("signature") != workbook_signature:
    st.session_state.pop("deal_transfer", None)

all_results = pd.DataFrame(result_rows)
product_options = sorted(all_results["Product"].unique())
selected_products = st.multiselect("Filter products", product_options, default=product_options)
filtered = all_results[all_results["Product"].isin(selected_products)].copy()
if filtered.empty:
    st.warning("Select at least one product to show results.")
    st.stop()

# Use a stateful page selector rather than st.tabs so the Deal Simulator button
# can navigate directly to Portfolio Rebalancing.
PAGES = ["Portfolio overview", "Deal simulator", "Portfolio rebalancing", "Calculation methodology", "Input data"]
if "active_page" not in st.session_state or st.session_state["active_page"] not in PAGES:
    st.session_state["active_page"] = PAGES[0]
active_page = st.radio("Application section", PAGES, key="active_page", horizontal=True, label_visibility="collapsed")


if active_page == "Portfolio overview":
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

if active_page == "Deal simulator":
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
    st.divider()
    st.markdown("### Continue to portfolio rebalancing")
    st.caption("Transfer this *additional deal* with its actual deal premium, SLR adjustment, extra costs and proposed commission. Existing cohort volumes remain unchanged. The new deal is compared against its own recommended commission before offsets are suggested.")
    def send_deal_to_rebalancing():
        st.session_state["deal_transfer"] = {
            "signature": workbook_signature,
            "cohort_id": selected_id,
            "deal_premium": float(premium),
            "slr_adjustment_pp": float(slr_stress_pp),
            "adjusted_slr": float(adjusted_slr),
            "extra_cost_ratio": float(extra_cost_pct / 100),
            "recommended_rate": float(custom_recommendation),
            "proposed_rate": float(rate_input),
        }
        st.session_state["active_page"] = "Portfolio rebalancing"
    st.button("Send to Portfolio Rebalancing →", type="primary", on_click=send_deal_to_rebalancing)


if active_page == "Portfolio rebalancing":
    st.subheader("Multi-cohort underwriting & overall portfolio rebalancing")
    st.write("Edit as many cohort commissions as you want in the table. Your changes are **locked**, and the engine finds suggested offsets only in cohorts you have not edited.")
    st.info("The rebalancing calculation uses the **entire portfolio**, regardless of the product filter above. Premium volume and expected SLR are held constant for existing cohorts.")

    # Reset the editable table if the workbook or market benchmark changes.
    if st.session_state.get("multi_editor_signature") != workbook_signature:
        st.session_state["multi_editor_signature"] = workbook_signature
        st.session_state.pop("multi_commission_editor", None)

    editor_input = all_results[["Cohort_ID", "Product", "Risk_Tier", "Written_Premium_INR", "Expected_SLR",
                               "Market_Min_Pct", "Market_Target_Pct", "Recommended_Pct"]].copy()
    editor_input["Current commission %"] = (editor_input["Recommended_Pct"] * 100).round(4)
    editor_input["Underwriter commission %"] = editor_input["Current commission %"]
    editor_input["Market floor %"] = (editor_input["Market_Min_Pct"] * 100).round(3)
    editor_input["Market target %"] = (editor_input["Market_Target_Pct"] * 100).round(3)
    editor_input["SLR %"] = (editor_input["Expected_SLR"] * 100).round(3)
    editor_input = editor_input[["Cohort_ID", "Product", "Risk_Tier", "Written_Premium_INR",
                                 "SLR %", "Market floor %", "Market target %",
                                 "Current commission %", "Underwriter commission %"]]
    st.markdown("#### 1. Enter discretion across multiple cohorts")
    st.caption("Double-click cells in **Underwriter commission %** to change them. Values are percentages (e.g., enter 12.5 for 12.5%). Every changed row is protected from automatic offsets. Use the reset button to clear all discretion.")
    left, right = st.columns([4, 1])
    with right:
        if st.button("Reset all changes", use_container_width=True):
            st.session_state.pop("multi_commission_editor", None)
            st.session_state.pop("deal_transfer", None)
            st.rerun()
    edited = st.data_editor(
        editor_input, key="multi_commission_editor", hide_index=True, use_container_width=True,
        num_rows="fixed", disabled=[c for c in editor_input.columns if c != "Underwriter commission %"],
        column_config={
            "Cohort_ID": st.column_config.TextColumn("Cohort ID"),
            "Written_Premium_INR": st.column_config.NumberColumn("Premium (₹)", format="₹ %.0f"),
            "Underwriter commission %": st.column_config.NumberColumn("Underwriter commission %", min_value=0.0, max_value=100.0, step=0.25, format="%.2f"),
            "Current commission %": st.column_config.NumberColumn("Baseline commission %", format="%.2f"),
        },
    )
    overrides = {}
    invalid = []
    for _, r in edited.iterrows():
        proposed = r["Underwriter commission %"]
        if pd.isna(proposed) or not 0 <= float(proposed) <= 100:
            invalid.append(str(r["Cohort_ID"]))
        elif abs(float(proposed) - float(r["Current commission %"])) > 0.00001:
            overrides[str(r["Cohort_ID"])] = float(proposed) / 100
    if invalid:
        st.error(f"Enter valid commission percentages between 0 and 100 for: {', '.join(invalid)}")
        st.stop()

    transfer = st.session_state.get("deal_transfer")
    if transfer:
        st.success(f"Additional deal transferred from Deal Simulator: cohort {transfer['cohort_id']}, premium {inr(transfer['deal_premium'])}, SLR {pct(transfer['adjusted_slr'])}, proposed {pct(transfer['proposed_rate'])}.")
        st.caption("The transferred deal is incremental and locked. You can simultaneously change commission in existing cohorts in the table above.")
        if st.button("Remove transferred deal (keep cohort edits)"):
            st.session_state.pop("deal_transfer", None)
            st.rerun()
    st.write(f"**Discretion applied to {len(overrides)} existing cohort(s)**" + (" + 1 additional deal" if transfer else ""))

    st.markdown("#### 2. Choose portfolio protection rules")
    c1, c2 = st.columns(2)
    with c1:
        preserve = st.radio("Protect", ["Current portfolio profit (₹)", "Minimum target portfolio margin"], index=0,
                            help="The first preserves profit including the transferred deal at its recommended commission. The second protects the portfolio-level target margin.")
    with c2:
        market_floor = st.checkbox("Protect market floors for suggested offsets", value=True,
                                   help="Automatic cuts cannot go below the lowest observed market quote. Existing cohorts already below their market minimum get no additional automatic reductions.")
    options = {"preserve": "baseline" if preserve.startswith("Current") else "target_margin",
               "floor_policy": "market_floor" if market_floor else "zero"}
    try:
        plan = rebalance_multiple_cohorts(
            all_results.to_dict("records"), overrides, assumptions,
            deal=transfer, **options,
        )
    except (ModelInputError, ValueError) as exc:
        st.error(f"Cannot calculate rebalancing: {exc}")
        st.stop()

    st.markdown("#### 3. Overall portfolio profitability")
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Baseline profit", inr(plan["baseline_profit_inr"]))
    k2.metric("Profit after discretionary edits", inr(plan["post_override_profit_inr"]),
              delta=inr(plan["post_override_profit_inr"] - plan["baseline_profit_inr"]))
    k3.metric("Profit after rebalancing", inr(plan["after_profit_inr"]),
              delta=inr(plan["after_profit_inr"] - plan["baseline_profit_inr"]))
    k4.metric("Unfunded profit gap", inr(plan["unfunded_inr"]))
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Baseline portfolio margin", pct(plan["baseline_margin"]))
    m2.metric("Margin after overrides", pct(plan["post_override_margin"]))
    m3.metric("Final portfolio margin", pct(plan["after_margin"]),
              delta=f"{(plan['after_margin']-plan['baseline_margin'])*100:+.2f} pp")
    m4.metric("Premium considered", inr(plan["total_premium_inr"]))

    if plan["feasible"]:
        st.success("Portfolio profitability constraint is satisfied with the suggested offsets.")
    else:
        st.error(f"Insufficient available offsets: {inr(plan['unfunded_inr'])} expected profit shortfall remains. Reduce discretionary rates or revise constraints with approval.")
    policy_issues = [r for r in plan["rows"] if r["Locked"] and
        (r["Rebalanced_Pct"] > assumptions["max_commission_rate"] + 1e-9 or
         r["Rebalanced_Pct"] < r["Market_Min_Pct"] - 1e-9 or
         r["Margin_After_Pct"] < assumptions["target_profit_margin"] - 1e-9)]
    if policy_issues:
        st.warning(f"{len(policy_issues)} locked proposal(s) have cohort-level policy/market/profitability exceptions. Portfolio feasibility is not individual-cohort approval.")
    if not market_floor:
        st.warning("Market floor protection is OFF. The offsets may be commercially uncompetitive.")

    st.markdown("#### 4. Where the money moves")
    bridge = pd.DataFrame({
        "Stage": ["Baseline", "After discretionary changes", "After suggested offsets", "Required profit"],
        "Expected profit (₹)": [plan["baseline_profit_inr"], plan["post_override_profit_inr"],
                                plan["after_profit_inr"], plan["target_profit_inr"]],
    }).set_index("Stage")
    st.bar_chart(bridge)
    cost1, cost2, cost3, cost4 = st.columns(4)
    cost1.metric("Increased discretionary payouts", inr(plan["gross_extra_cost_inr"]))
    cost2.metric("Savings from discretionary cuts", inr(plan["gross_discretionary_savings_inr"]))
    cost3.metric("Automatic offset savings", inr(plan["suggested_savings_inr"]))
    cost4.metric("Automatic offset capacity", inr(plan["available_offset_inr"]))

    st.markdown("#### 5. Complete cohort-by-cohort impact")
    plan_df = pd.DataFrame(plan["rows"])
    if transfer and plan["deal_row_id"]:
        plan_df.loc[plan_df["Cohort_ID"] == plan["deal_row_id"], "Cohort_ID"] = f"New deal ({transfer['cohort_id']})"
    breakdown = plan_df[["Cohort_ID", "Row_Type", "Product", "Written_Premium_INR", "Baseline_Pct",
                         "Discretionary_Pct", "Rebalanced_Pct", "Delta_Points", "Commission_Change_INR",
                         "Profit_Change_INR", "Margin_Before_Pct", "Margin_After_Pct", "Note"]]
    st.dataframe(breakdown.style.format({
        "Written_Premium_INR": "₹{:,.0f}", "Commission_Change_INR": "₹{:,.0f}",
        "Profit_Change_INR": "₹{:,.0f}", "Baseline_Pct": "{:.2%}",
        "Discretionary_Pct": "{:.2%}", "Rebalanced_Pct": "{:.2%}",
        "Margin_Before_Pct": "{:.2%}", "Margin_After_Pct": "{:.2%}", "Delta_Points": "{:+.2f}",
    }), use_container_width=True, hide_index=True)
    st.subheader("Commission movement by cohort (percentage points)")
    st.bar_chart(plan_df.set_index("Cohort_ID")["Delta_Points"])
    st.download_button("Download complete portfolio rebalancing CSV", plan_df.to_csv(index=False).encode("utf-8-sig"),
                       file_name="finleadai_multi_cohort_rebalancing.csv", mime="text/csv")
    st.caption("This is a transparent greedy reallocation, not optimal economic pricing. Manually modified rates remain locked. It assumes unchanged premium volume and SLR, and does not model partner retention, demand response or future claims uncertainty.")


if active_page == "Calculation methodology":
    st.subheader("Transparent rule set")
    st.latex(r"C_{\max}=\max(0,\min(C_{\text{cap}},1-\mathrm{SLR}-E-O-P))")
    st.latex(r"C_{\text{recommended}}=\min(C_{\text{market target}},C_{\max})")
    st.write("If the profit target cannot be met even at 0% commission, the cohort is flagged for repricing or manual review. Low-exposure and below-market results are also flagged.")
    st.json({key: value for key, value in assumptions.items()})
    st.caption("This is a deterministic, illustrative margin-on-premium model. It does not adjust for inflation, reinsurance, capital cost, taxes, acquisition effects, credibility smoothing, or demand elasticity.")

if active_page == "Input data":
    st.subheader("Uploaded workbook")
    chosen_sheet = st.selectbox("Sheet", list(sheets))
    st.dataframe(sheets[chosen_sheet], use_container_width=True, hide_index=True)
    st.caption("Blue values in the Excel sample are intended as editable inputs; formulas in its Results_Preview tab are for inspection only. The web app recalculates from the input tabs.")

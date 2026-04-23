import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns


ARTIFACT_DIR = Path("/home/ubuntu/RLD/scripts/artifacts")
SUMMARY_PATH = ARTIFACT_DIR / "usdc_irm_sensitivity_summary_2026-04-23.json"
PROJECTION_PATH = ARTIFACT_DIR / "usdc_irm_sensitivity_projection_2026-04-23.csv"
BASE_PATH = ARTIFACT_DIR / "usdc_hf_sorted_envio_reconstruction_2026-04-23.csv"
LARGEST_ADDRESS = "0x0591926d5d3b9cc48ae6efb8db68025ddc3adfa5"

OUT_MD = ARTIFACT_DIR / "aave_forum_state_reply_2026-04-23.md"
OUT_HF_CHART = ARTIFACT_DIR / "chart_hf_distribution_material_2026-04-23.png"
OUT_BAR_CHART = ARTIFACT_DIR / "chart_material_debt_risk_7d_30d_2026-04-23.png"
OUT_CUM_CHART = ARTIFACT_DIR / "chart_cumulative_material_debt_90d_2026-04-23.png"
OUT_LARGEST_CHART = ARTIFACT_DIR / "chart_largest_address_hf_trajectory_2026-04-23.png"

SCENARIO_ORDER = [
    "Current observed APR",
    "Interim @ current util",
    "Target @ current util",
    "Interim @ 99.87% util",
    "Target @ 99.87% util",
]
TTL_COLUMN_BY_SCENARIO = {
    "Current observed APR": "days_to_liq_current_observed_apr",
    "Interim @ current util": "days_to_liq_interim_u_current",
    "Target @ current util": "days_to_liq_target_u_current",
    "Interim @ 99.87% util": "days_to_liq_interim_u_pinned",
    "Target @ 99.87% util": "days_to_liq_target_u_pinned",
}


def fmt_usd(value: float) -> str:
    return f"${value:,.0f}"


def scenario_apr_map(summary: dict) -> dict:
    state = summary["state"]
    return {
        "Current observed APR": state["usdc_borrow_apy_observed"],
        "Interim @ current util": state["model_rates"]["interim_at_u_current"],
        "Target @ current util": state["model_rates"]["target_at_u_current"],
        "Interim @ 99.87% util": state["model_rates"]["interim_at_u_pinned_99_87"],
        "Target @ 99.87% util": state["model_rates"]["target_at_u_pinned_99_87"],
    }


def account_hf_after_days(hf0: float, usdc_share: float, usdc_apr: float, other_apr: float, days: float) -> float:
    t = days / 365.0
    growth = usdc_share * np.exp(np.log(1 + usdc_apr) * t) + (1 - usdc_share) * np.exp(np.log(1 + other_apr) * t)
    return float(hf0 / growth)


def load_data():
    with open(SUMMARY_PATH, "r", encoding="utf-8") as handle:
        summary = json.load(handle)
    projection = pd.read_csv(PROJECTION_PATH)
    base = pd.read_csv(BASE_PATH)
    return summary, projection, base


def make_hf_distribution_chart(base_df: pd.DataFrame) -> None:
    sns.set_theme(style="darkgrid")
    fig, ax = plt.subplots(figsize=(11, 6))

    material = base_df[base_df["total_debt_usd"] >= 100000].copy()
    material = material[material["hf"] > 0]
    material["hf_capped"] = material["hf"].clip(upper=5.0)

    ax.hist(
        material["hf_capped"],
        bins=np.linspace(0.8, 5.0, 42),
        color="#3B82F6",
        alpha=0.85,
    )
    ax.axvline(1.0, color="#B91C1C", linestyle="--", linewidth=2, label="Liquidation boundary (HF=1)")
    ax.axvline(1.1, color="#EA580C", linestyle=":", linewidth=2, label="Early-warning boundary (HF=1.1)")

    ax.set_title("Material USDC-Debt Users: Health Factor Distribution", fontsize=14, fontweight="bold")
    ax.set_xlabel("Health Factor (capped at 5.0)")
    ax.set_ylabel("User count")
    ax.legend(loc="upper right")
    fig.tight_layout()
    fig.savefig(OUT_HF_CHART, dpi=240)
    plt.close(fig)


def make_material_risk_bar_chart(summary: dict) -> None:
    sns.set_theme(style="darkgrid")
    labels = []
    risk_7d = []
    risk_30d = []

    for scenario in SCENARIO_ORDER:
        payload = summary["scenarios"][scenario]["material_users_debt_ge_100k"]
        labels.append(scenario)
        risk_7d.append(payload["debt_liq_7d_usd"] / 1e6)
        risk_30d.append(payload["debt_liq_30d_usd"] / 1e6)

    x = np.arange(len(labels))
    width = 0.38
    fig, ax = plt.subplots(figsize=(12, 7))
    ax.bar(x - width / 2, risk_7d, width, label="7-day debt-at-risk ($M)", color="#991B1B")
    ax.bar(x + width / 2, risk_30d, width, label="30-day debt-at-risk ($M)", color="#F97316")

    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=15, ha="right")
    ax.set_ylabel("Debt-at-risk (USD millions)")
    ax.set_title("Material Debt-at-Risk by Scenario", fontsize=14, fontweight="bold")
    ax.legend(loc="upper left")
    fig.tight_layout()
    fig.savefig(OUT_BAR_CHART, dpi=240)
    plt.close(fig)


def make_cumulative_risk_chart(projection_df: pd.DataFrame) -> None:
    sns.set_theme(style="darkgrid")
    material = projection_df[projection_df["total_debt_usd"] >= 100000].copy()
    days = np.arange(0, 91)

    fig, ax = plt.subplots(figsize=(12, 7))
    for scenario in SCENARIO_ORDER:
        col = TTL_COLUMN_BY_SCENARIO[scenario]
        cumulative = []
        ttl = material[col].values
        debt = material["total_debt_usd"].values
        for day in days:
            cumulative.append(float(debt[(ttl <= day)].sum()) / 1e6)
        ax.plot(days, cumulative, linewidth=2.5, label=scenario)

    ax.axvline(7, color="#7F1D1D", linestyle="--", linewidth=1.5, alpha=0.8)
    ax.axvline(30, color="#B91C1C", linestyle="--", linewidth=1.5, alpha=0.8)
    ax.set_title("Cumulative Material Debt-at-Risk Over Time", fontsize=14, fontweight="bold")
    ax.set_xlabel("Days from snapshot")
    ax.set_ylabel("Cumulative debt-at-risk (USD millions)")
    ax.legend(loc="upper left", fontsize=9)
    fig.tight_layout()
    fig.savefig(OUT_CUM_CHART, dpi=240)
    plt.close(fig)


def make_largest_address_hf_chart(summary: dict, projection_df: pd.DataFrame) -> None:
    target = projection_df[projection_df["address"] == LARGEST_ADDRESS]
    if target.empty:
        return

    row = target.iloc[0]
    hf0 = float(row["hf"])
    usdc_share = float(row["usdc_share_for_model"])
    other_apr = float(summary["state"]["non_usdc_weighted_borrow_apy"])
    aprs = scenario_apr_map(summary)

    days = np.arange(0, 46)
    sns.set_theme(style="darkgrid")
    fig, ax = plt.subplots(figsize=(12, 7))

    for scenario in SCENARIO_ORDER:
        apr = aprs[scenario]
        series = [account_hf_after_days(hf0, usdc_share, apr, other_apr, day) for day in days]
        ax.plot(days, series, linewidth=2.5, label=scenario)

    ax.axhline(1.0, color="#B91C1C", linestyle="--", linewidth=2, label="HF = 1")
    ax.axvline(7, color="#7F1D1D", linestyle=":", linewidth=1.5, alpha=0.8)
    ax.axvline(30, color="#B91C1C", linestyle=":", linewidth=1.5, alpha=0.8)
    ax.set_title("Largest Address HF Trajectory Under Rate Scenarios", fontsize=14, fontweight="bold")
    ax.set_xlabel("Days from snapshot")
    ax.set_ylabel("Projected health factor")
    ax.set_ylim(0.95, max(1.03, hf0 + 0.01))
    ax.legend(loc="upper right", fontsize=9)
    fig.tight_layout()
    fig.savefig(OUT_LARGEST_CHART, dpi=240)
    plt.close(fig)


def render_markdown(summary: dict, projection_df: pd.DataFrame) -> None:
    state = summary["state"]
    pop = summary["population"]
    scenarios = summary["scenarios"]
    scenario_aprs = scenario_apr_map(summary)

    material_projection = projection_df[projection_df["total_debt_usd"] >= 100000].copy()
    material_projection = material_projection.sort_values("hf", ascending=True)

    top_interim = scenarios["Interim @ current util"]["top10_fastest_material"][:8]
    top_target = scenarios["Target @ current util"]["top10_fastest_material"][:8]
    interim_30d = scenarios["Interim @ current util"]["material_users_debt_ge_100k"]["debt_liq_30d_usd"]
    target_30d = scenarios["Target @ current util"]["material_users_debt_ge_100k"]["debt_liq_30d_usd"]
    if abs(interim_30d - target_30d) < 1:
        debt_range_sentence = f"**{fmt_usd(interim_30d)}**"
    else:
        debt_range_sentence = f"**{fmt_usd(interim_30d)}** to **{fmt_usd(target_30d)}**"

    largest = projection_df[projection_df["address"] == LARGEST_ADDRESS].iloc[0]
    largest_hf = float(largest["hf"])
    largest_debt = float(largest["total_debt_usd"])
    largest_collateral = float(largest["collateral_usd"])
    largest_usdc_debt = float(largest["usdc_debt_events_usd"])
    largest_usdc_share = float(largest["usdc_share_for_model"])
    largest_non_usdc_debt = largest_debt - largest_usdc_debt
    largest_hf_buffer_pct = (largest_hf - 1.0) * 100.0
    largest_hf_buffer_usd = largest_debt * (largest_hf - 1.0)
    other_apr = float(state["non_usdc_weighted_borrow_apy"])

    largest_ttl_rows = []
    for scenario in SCENARIO_ORDER:
        ttl_col = TTL_COLUMN_BY_SCENARIO[scenario]
        ttl = float(largest[ttl_col])
        apr = float(scenario_aprs[scenario])
        hf_7d = account_hf_after_days(largest_hf, largest_usdc_share, apr, other_apr, 7)
        hf_30d = account_hf_after_days(largest_hf, largest_usdc_share, apr, other_apr, 30)
        daily_usdc_carry = largest_usdc_debt * ((1 + apr) ** (1 / 365) - 1)
        monthly_usdc_carry = largest_usdc_debt * ((1 + apr) ** (30 / 365) - 1)
        scenario_30d_material = float(scenarios[scenario]["material_users_debt_ge_100k"]["debt_liq_30d_usd"])
        if ttl <= 30 and scenario_30d_material > 0:
            contribution = largest_debt / scenario_30d_material
        else:
            contribution = 0.0
        largest_ttl_rows.append(
            {
                "scenario": scenario,
                "ttl": ttl,
                "hf_7d": hf_7d,
                "hf_30d": hf_30d,
                "daily_carry": daily_usdc_carry,
                "monthly_carry": monthly_usdc_carry,
                "contribution": contribution,
            }
        )

    lines = []
    lines.append("# [Data] USDC on Ethereum Core: Current State and User Sensitivity")
    lines.append("")
    lines.append(
        "This post is a data-only state update based on Envio event reconstruction + live account risk reads. "
        "It does **not** include a new parameter recommendation (that will be posted separately)."
    )
    lines.append("")
    lines.append("## TL;DR")
    lines.append("")
    lines.append(
        f"- USDC remains highly utilized (**{state['usdc_utilization_true']*100:.2f}%**), but is no longer fully pinned; "
        f"available liquidity is about **{fmt_usd(state['usdc_available_liquidity_usd'])}**."
    )
    lines.append(
        f"- Observed variable borrow APR is **{state['usdc_borrow_apy_observed']*100:.2f}%** at snapshot time "
        f"({summary['snapshot_ts']} UTC)."
    )
    lines.append(
        "- Under proposal scenarios at current utilization, modeled 30-day material debt-at-risk is concentrated around "
        f"{debt_range_sentence}."
    )
    lines.append(
        "- Under pinned-utilization stress (99.87%), target-curve stress introduces one material <=7d account and raises "
        f"30-day material debt-at-risk to **{fmt_usd(scenarios['Target @ 99.87% util']['material_users_debt_ge_100k']['debt_liq_30d_usd'])}**."
    )
    lines.append("")
    lines.append("## Methodology")
    lines.append("")
    lines.append(
        "- **User set:** addresses with positive net USDC debt from event reconstruction "
        "(`Borrow - Repay - LiquidationCall` debt leg) from `aave_events`."
    )
    lines.append("- **Risk state:** live `getUserAccountData` for HF, debt, collateral.")
    lines.append(
        "- **Debt mix modeling:** reconstructed USDC share applied to user-level debt growth; non-USDC debt accrues at current "
        "debt-weighted baseline APR."
    )
    lines.append("- **Assumptions:** static prices, no top-ups/repay/migrations, deterministic accrual.")
    lines.append("")
    lines.append("## Current State Snapshot")
    lines.append("")
    lines.append(f"- Supply: **{fmt_usd(state['usdc_supply_usd'])}**")
    lines.append(f"- Borrow: **{fmt_usd(state['usdc_borrow_usd'])}**")
    lines.append(f"- Available liquidity: **{fmt_usd(state['usdc_available_liquidity_usd'])}**")
    lines.append(f"- Utilization (`borrow/supply`): **{state['usdc_utilization_true']*100:.2f}%**")
    lines.append(f"- Observed USDC variable borrow APR: **{state['usdc_borrow_apy_observed']*100:.2f}%**")
    lines.append(f"- Observed USDC supply APR: **{state['usdc_supply_apy_observed']*100:.2f}%**")
    lines.append(f"- Population covered: **{pop['users_with_positive_usdc_debt']:,}** users")
    lines.append(f"- Material users (`debt >= $100k`): **{pop['users_with_material_debt_ge_100k']:,}**")
    lines.append("")
    lines.append("## Chart 1: Material HF Distribution")
    lines.append("")
    lines.append("![Material HF distribution](<UPLOAD_CHART_1_URL>)")
    lines.append(f"_Local artifact: `{OUT_HF_CHART}`_")
    lines.append("")
    lines.append("## Chart 2: Material Debt-at-Risk by Scenario")
    lines.append("")
    lines.append("![Material debt-at-risk bar chart](<UPLOAD_CHART_2_URL>)")
    lines.append(f"_Local artifact: `{OUT_BAR_CHART}`_")
    lines.append("")
    lines.append("## Chart 3: Cumulative Material Debt-at-Risk (0-90 days)")
    lines.append("")
    lines.append("![Cumulative material debt-at-risk](<UPLOAD_CHART_3_URL>)")
    lines.append(f"_Local artifact: `{OUT_CUM_CHART}`_")
    lines.append("")
    lines.append("## Chart 4: Largest Address HF Path")
    lines.append("")
    lines.append("![Largest address HF trajectory](<UPLOAD_CHART_4_URL>)")
    lines.append(f"_Local artifact: `{OUT_LARGEST_CHART}`_")
    lines.append("")
    lines.append("## Scenario Table (Material Users)")
    lines.append("")
    lines.append("| Scenario | 7d users | 7d debt-at-risk | 30d users | 30d debt-at-risk |")
    lines.append("|---|---:|---:|---:|---:|")
    for scenario in SCENARIO_ORDER:
        m = scenarios[scenario]["material_users_debt_ge_100k"]
        lines.append(
            f"| {scenario} | {m['users_liq_7d']} | {fmt_usd(m['debt_liq_7d_usd'])} | "
            f"{m['users_liq_30d']} | {fmt_usd(m['debt_liq_30d_usd'])} |"
        )
    lines.append("")
    lines.append(f"## Largest Address Deep-Dive: `{LARGEST_ADDRESS}`")
    lines.append("")
    lines.append(
        "This address is the largest debt concentration in the sensitivity set and materially drives scenario-level debt-at-risk outcomes."
    )
    lines.append("")
    lines.append(f"- Total debt: **{fmt_usd(largest_debt)}**")
    lines.append(f"- Reconstructed USDC debt: **{fmt_usd(largest_usdc_debt)}** ({largest_usdc_share*100:.2f}% of total debt)")
    lines.append(f"- Non-USDC debt (modeled baseline accrual): **{fmt_usd(largest_non_usdc_debt)}**")
    lines.append(f"- Collateral: **{fmt_usd(largest_collateral)}**")
    lines.append(f"- Current HF: **{largest_hf:.4f}**")
    lines.append(
        f"- Debt headroom to HF=1 under static prices: **{fmt_usd(largest_hf_buffer_usd)}** "
        f"({largest_hf_buffer_pct:.2f}% debt growth buffer)"
    )
    lines.append("")
    lines.append("| Scenario | Days to liquidation | HF @ 7d | HF @ 30d | USDC carry/day | USDC carry/30d | Share of scenario 30d material debt-at-risk |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for row in largest_ttl_rows:
        lines.append(
            f"| {row['scenario']} | {row['ttl']:.1f} | {row['hf_7d']:.4f} | {row['hf_30d']:.4f} | "
            f"{fmt_usd(row['daily_carry'])} | {fmt_usd(row['monthly_carry'])} | {row['contribution']*100:.1f}% |"
        )
    lines.append("")
    lines.append(
        "At current-utilization interim/target scenarios, this single address contributes ~98% of 30-day material debt-at-risk, "
        "highlighting strong single-name concentration."
    )
    lines.append("")
    lines.append("## Most Sensitive Material Accounts")
    lines.append("")
    lines.append("### Interim @ current utilization")
    lines.append("")
    lines.append("| Address | HF now | Debt | USDC share | Days to liquidation |")
    lines.append("|---|---:|---:|---:|---:|")
    for row in top_interim:
        lines.append(
            f"| `{row['address']}` | {row['hf']:.4f} | {fmt_usd(row['total_debt_usd'])} | "
            f"{row['usdc_share_for_model']*100:.1f}% | {row['days_to_liq']:.1f} |"
        )
    lines.append("")
    lines.append("### Target @ current utilization")
    lines.append("")
    lines.append("| Address | HF now | Debt | USDC share | Days to liquidation |")
    lines.append("|---|---:|---:|---:|---:|")
    for row in top_target:
        lines.append(
            f"| `{row['address']}` | {row['hf']:.4f} | {fmt_usd(row['total_debt_usd'])} | "
            f"{row['usdc_share_for_model']*100:.1f}% | {row['days_to_liq']:.1f} |"
        )
    lines.append("")
    lines.append("## Interpretation")
    lines.append("")
    lines.append("- Count-based risk is dominated by dust accounts; debt-weighted risk remains concentrated in a small set of loop-heavy wallets.")
    lines.append("- At the current (not fully pinned) utilization, no material account is in <=7d liquidation under interim/target-at-current-util scenarios.")
    lines.append("- Under pinned stress, the target curve notably tightens clocks for the most levered material accounts.")
    lines.append("")
    lines.append("## Caveats")
    lines.append("")
    lines.append("- This is sensitivity analysis, not a forecast of realized liquidations.")
    lines.append("- It excludes collateral price shocks and behavioral responses.")
    lines.append("- A separate post will propose a balanced parameter set.")
    lines.append("")
    lines.append("## Reproducibility Artifacts")
    lines.append("")
    lines.append(f"- Base user snapshot: `{BASE_PATH}`")
    lines.append(f"- Projection dataset: `{PROJECTION_PATH}`")
    lines.append(f"- Summary dataset: `{SUMMARY_PATH}`")

    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def generate_report():
    summary, projection_df, base_df = load_data()
    make_hf_distribution_chart(base_df)
    make_material_risk_bar_chart(summary)
    make_cumulative_risk_chart(projection_df)
    make_largest_address_hf_chart(summary, projection_df)
    render_markdown(summary, projection_df)

    print(f"Wrote report: {OUT_MD}")
    print(f"Wrote chart: {OUT_HF_CHART}")
    print(f"Wrote chart: {OUT_BAR_CHART}")
    print(f"Wrote chart: {OUT_CUM_CHART}")
    print(f"Wrote chart: {OUT_LARGEST_CHART}")


if __name__ == "__main__":
    generate_report()

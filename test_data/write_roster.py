"""Generates test_data/vendor_roster.md — the plain-English roster of the
45 synthetic vendors, for Sarath to sanity-check independently of the
model's own output. Run standalone: python -m test_data.write_roster
"""
import os

from test_data.compute_predictions import outstanding_balance, required_amount
from test_data.dataset_spec import AS_OF_MONTH_ISO, MONTH_LABELS, PLANNING_MONTH, SHEET_START_MONTH_ISO, VENDORS

OUT_PATH = os.path.join(os.path.dirname(__file__), "vendor_roster.md")


def _aging_summary(v):
    if v.category_label == "Commitment":
        return f"flat opening balance Rs {v.op_balance:,.0f}, no monthly ledger activity"
    parts = []
    for label, (payable, payment) in zip(MONTH_LABELS, v.monthly):
        if payable == 0 and payment == 0:
            continue
        remaining = payable - payment
        tag = "paid in full" if remaining <= 0 else f"Rs {remaining:,.0f} unpaid"
        parts.append(f"{label} billed Rs {payable:,.0f} ({tag})")
    return "; ".join(parts) if parts else "no billing activity"


def render():
    lines = [
        "# Synthetic 45-vendor dataset — plain roster",
        "",
        "Built for the New Model 2 end-to-end validation exercise "
        "(2026-08-03) — predictions computed BEFORE the real model ever "
        "ran, see predictions_scenario_*.md.",
        "",
        f"- Sheet months: {MONTH_LABELS[0]} .. {MONTH_LABELS[-1]} "
        f"(sheet_start_month = {SHEET_START_MONTH_ISO})",
        f"- Planning month: {PLANNING_MONTH} -> as_of (aging '0-30' reference) = {AS_OF_MONTH_ISO}",
        "- 7 Must Pay (P0), 14 Commitment (P1), 19 Normal (7 P2 / 6 P3 / 6 P4), 5 Inactive (P5)",
        "- Every Must Pay/Normal/Inactive vendor is built from one of 6 fixed aging shapes, each "
        "engineered to land on a specific required_amount_v2() rule branch (docs/14):",
        "  - A = only_current, B = current_zero_oldest_only, C = oldest_and_current (adjacent), "
        "D = oldest_and_second (gap month), E = oldest_only, F = no_outstanding",
        "",
        "| ERP Code | Vendor | Category | Tag | Branch | Aging shape | Op.Bal / commitment_months | "
        "Required (Rs) | Outstanding (Rs) | Assigned Week |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for v in VENDORS:
        amount, rule = required_amount(v)
        outstanding = outstanding_balance(v)
        cm = f"{v.op_balance:,.0f} / {v.commitment_months}" if v.category_label == "Commitment" else "-"
        branch = v.branch or "-"
        lines.append(
            f"| {v.erp_code} | {v.vendor_name} | {v.category_label} | {v.priority_tag} | {branch} | "
            f"{_aging_summary(v)} | {cm} | {amount:,.2f} | {outstanding:,.2f} | W{v.assigned_week} |"
        )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(render())
    print(f"Wrote {OUT_PATH}")

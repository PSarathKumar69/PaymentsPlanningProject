"""Filters ZERO-status vendors and assembles the fact-pack per vendor —
strictly from fields Models 2/3's generate_plan() already computed. Never
imports the raw data loader / column mapping / ledger — CLAUDE.md rule 3.
"""
from backend.shared.constants import MONEY_EPSILON
from backend.shared.enums import AllocationStatus

# Model 2's rows carry required_amount/rule/oldest_bucket*; Model 3's carry
# bucket/score/aging_bucket instead — a given row only has whichever set its
# model produced. Anything not in this whitelist is never read.
_FACT_FIELDS = [
    "vendor_id",
    "erp_code",
    "vendor_name",
    "category",
    "commitment_months",
    "outstanding_balance",
    "allocated_amount",
    "status",
    "required_amount",
    "rule",
    "oldest_bucket",
    "oldest_bucket_age_days",
    "bucket",
    "score",
    "aging_bucket",
]


def zero_allocation_rows(vendor_allocations):
    """vendor_allocations: the same per-vendor plan output shape
    build_weekly_view() consumes (a generate_plan()'s "allocations" DataFrame
    or an equivalent list of dict records). Returns only ZERO-status rows."""
    if hasattr(vendor_allocations, "to_dict"):
        vendor_allocations = vendor_allocations.to_dict("records")
    return [row for row in vendor_allocations if row.get("status") == AllocationStatus.ZERO.value]


def build_fact_pack(row):
    """One vendor's already-computed fields, filtered to the known whitelist.
    Nothing here is computed — missing or NaN fields are simply omitted."""
    return {k: row[k] for k in _FACT_FIELDS if k in row and row[k] is not None and row[k] == row[k]}


def _filtered(row, fields):
    """Same whitelist-and-drop-missing/NaN convention as build_fact_pack above,
    factored out for the two fact packs below (build_fact_pack itself is left
    untouched — still whatever the zero-only talking script relies on)."""
    return {k: row[k] for k in fields if k in row and row[k] is not None and row[k] == row[k]}


_AGING_FIELDS = [
    "oldest_bucket",
    "oldest_bucket_months_back",
    "bucket_balances",
    "total_outstanding",
    "oldest_bucket_amount",
    "monthly_breakdown",
]

_MIN_FUNDS_BREAKDOWN_FIELDS = [
    "total",
    "rule",
    "category",
    "opening_balance",
    "live_balance",
    "commitment_months",
    "current_month",
    "current_amount",
    "oldest_month",
    "oldest_amount",
    "second_month",
    "second_amount",
]


def build_vendor_talking_points_fact_pack(
    vendor_id,
    erp_code,
    vendor_name,
    category,
    priority_tag,
    aging,
    min_funds_breakdown,
    allocated_amount,
    required_amount,
    status,
):
    """Generalizes zero_allocation_rows/build_fact_pack above to ANY vendor
    status, for the vendor talking points flow (any of Must Pay/Commitment/
    Normal/Inactive, any of full/partial/zero).
    `aging` is a GET /vendors/{id}/aging-shaped dict, `min_funds_breakdown` is
    a min_funds_breakdown_v2()-shaped dict — both already computed by the
    caller (the router), never recomputed here. `cut_from_full` is the one
    plain comparison this module makes (mirrors zero_allocation_rows' own
    status == ZERO.value filter above) — not an amount computation."""
    fact_pack = _filtered(
        {
            "vendor_id": vendor_id,
            "erp_code": erp_code,
            "vendor_name": vendor_name,
            "category": category,
            "priority_tag": priority_tag,
            "required_amount": required_amount,
            "allocated_amount": allocated_amount,
            "status": status,
        },
        ["vendor_id", "erp_code", "vendor_name", "category", "priority_tag", "required_amount", "allocated_amount", "status"],
    )
    fact_pack["aging"] = _filtered(aging or {}, _AGING_FIELDS)
    fact_pack["min_funds_breakdown"] = _filtered(min_funds_breakdown or {}, _MIN_FUNDS_BREAKDOWN_FIELDS)
    if allocated_amount is not None and required_amount is not None:
        fact_pack["cut_from_full"] = allocated_amount < required_amount - MONEY_EPSILON
    return fact_pack

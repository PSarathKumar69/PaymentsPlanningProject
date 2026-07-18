"""Filters ZERO-status vendors and assembles the fact-pack per vendor —
strictly from fields Models 2/3's generate_plan() already computed. Never
imports the raw data loader / column mapping / ledger — CLAUDE.md rule 3.
"""
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

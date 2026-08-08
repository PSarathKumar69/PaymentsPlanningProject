"""Independent, from-scratch reimplementation of docs/14-new-model-2.md's
Minimum Funds Required rule + the priority-bucket ceiling/cutting/leftover-
topup allocation logic, used ONLY to hand-verify this task's predictions
BEFORE the real model ever runs. Deliberately does not import anything from
backend.shared.min_funds_v2 or backend.models.new_model_2.allocator — if this
script and the real code agree, that's a real cross-check, not a tautology.

Run standalone: python -m test_data.compute_predictions
Writes test_data/predictions_scenario_{1,2,3}.md
"""
import os
from datetime import date

from test_data.dataset_spec import AS_OF_MONTH_ISO, MONTH_LABELS, VENDORS

AS_OF = date.fromisoformat(AS_OF_MONTH_ISO)
MONTH_DATES = [date(2026, 3 + i, 1) if 3 + i <= 12 else date(2027, 3 + i - 12, 1) for i in range(6)]
EPS = 1e-6

CEILING = {"P2": 0.95, "P3": 0.90, "P4": 0.85, "P5": 0.50}
FLOOR = {"P2": 0.25, "P3": 0.25, "P4": 0.25, "P5": 0.10}
BUCKET_ORDER = ["P2", "P3", "P4", "P5"]  # highest priority first
STEP = 0.05


def months_back(origin_month):
    return max(0, (AS_OF.year - origin_month.year) * 12 + (AS_OF.month - origin_month.month))


def build_tranches(monthly):
    """Faithful independent FIFO reimplementation (mirrors
    backend/shared/aging.py::build_vendor_tranches's contract, written from
    scratch): one tranche per month with positive payable, each month's own
    payment applied FIFO oldest-tranche-first across everything built so
    far."""
    tranches = []  # [month_date, remaining]
    for month, (payable, payment) in zip(MONTH_DATES, monthly):
        if payable > 0:
            tranches.append([month, float(payable)])
        remaining_payment = float(payment)
        for t in tranches:
            if remaining_payment <= 0:
                break
            if t[1] <= 0:
                continue
            applied = min(t[1], remaining_payment)
            t[1] -= applied
            remaining_payment -= applied
    return tranches


def required_amount_v2_independent(monthly):
    """docs/14 "Minimum Funds Required" rules 1-6, reimplemented from the doc
    text alone. Returns (amount, rule_label)."""
    tranches = build_tranches(monthly)
    outstanding = [(m, r) for m, r in tranches if r > EPS]
    if not outstanding:
        return 0.0, "no_outstanding"  # rule 6

    current = next((r for m, r in outstanding if m == AS_OF), 0.0)
    older = [(m, r) for m, r in outstanding if m < AS_OF]

    if not older:
        # only the current month's own bill (rule 1) — this dataset never
        # hits the "as_of predates all data" edge case, so `current` is
        # always real here.
        return current, "only_current"

    oldest_month, oldest_amt = older[0]  # already oldest -> newest by construction

    if current <= EPS:
        return oldest_amt, "current_zero_oldest_only"  # rule 2

    if oldest_amt <= 0.5 * current + EPS:
        after_oldest = sorted((m, r) for m, r in outstanding if m > oldest_month)
        second_month, second_amt = after_oldest[0]
        is_current = second_month == AS_OF
        return oldest_amt + second_amt, "oldest_and_current" if is_current else "oldest_and_second"  # rule 3

    return oldest_amt, "oldest_only"  # rule 4


def required_amount(v):
    if v.category_label == "Commitment":
        return v.op_balance / v.commitment_months, "commitment"
    return required_amount_v2_independent(v.monthly)


def outstanding_balance(v):
    if v.category_label == "Commitment":
        return v.op_balance
    total_payable = sum(p for p, _ in v.monthly)
    total_payment = sum(p for _, p in v.monthly)
    return max(v.op_balance + total_payable - total_payment, 0.0)


def compute_rows():
    rows = []
    for v in VENDORS:
        amount, rule = required_amount(v)
        rows.append({
            "erp_code": v.erp_code,
            "vendor_name": v.vendor_name,
            "category": v.category_label,
            "priority_tag": v.priority_tag,
            "required_amount": round(amount, 2),
            "outstanding_balance": round(outstanding_balance(v), 2),
            "rule": rule,
        })
    return rows


def allocate(available_funds, rows):
    """docs/14 "Allocation logic" + "Leftover funds top-up", reimplemented
    from the doc text. rows: compute_rows() output. Returns a dict per
    vendor with allocated_amount/status, plus aggregate figures.

    A zero-outstanding-balance vendor never enters generate_plan() at all
    (backend/shared/min_funds.py::_load_vendors_and_ledger()'s
    has_outstanding_balance() filter, docs/06 — "a vendor with nothing left
    to pay must never re-enter scoring/ranking/allocation/eligibility";
    docs/14 cross-references this too: "dropping out of planning once
    outstanding_balance reaches 0"). Filtered out here up front so this
    reimplementation's vendor set matches the real one exactly, not just
    its zero-allocation OUTCOME for those vendors."""
    rows = [r for r in rows if r["outstanding_balance"] > EPS]
    guaranteed = [r for r in rows if r["category"] in ("Must Pay", "Commitment")]
    normal = [r for r in rows if r["category"] in ("Normal", "Inactive")]

    guaranteed_total = sum(r["required_amount"] for r in guaranteed)
    remaining = available_funds - guaranteed_total
    escalation = remaining < 0
    funds_left = max(remaining, 0.0)

    by_bucket = {b: [r for r in normal if r["priority_tag"] == b] for b in BUCKET_ORDER}
    required_total = {b: sum(r["required_amount"] for r in rows_) for b, rows_ in by_bucket.items()}
    total_required_normal = sum(required_total.values())

    exceptional = False
    if total_required_normal <= funds_left + EPS:
        pct = {b: 1.0 for b in BUCKET_ORDER}
    else:
        pct = dict(CEILING)

        def cost():
            return sum(required_total[b] * pct[b] for b in BUCKET_ORDER)

        if cost() > funds_left + EPS:
            rotation = list(reversed(BUCKET_ORDER))  # P5, P4, P3, P2
            i = 0
            active = list(rotation)
            while cost() > funds_left + EPS:
                if not active:
                    exceptional = True
                    break
                b = active[i % len(active)]
                pct[b] = round(pct[b] - STEP, 10)
                if pct[b] <= FLOOR[b] + 1e-9:
                    pct[b] = FLOOR[b]
                    active.remove(b)
                    if active:
                        i = i % len(active)
                else:
                    i += 1

    allocations = {}
    for r in guaranteed:
        allocations[r["erp_code"]] = {"allocated_amount": r["required_amount"], "status": "guaranteed"}

    for b in BUCKET_ORDER:
        for r in by_bucket[b]:
            allocated = r["required_amount"] * pct[b]
            if allocated >= r["required_amount"] - EPS:
                status = "full"
            elif allocated <= EPS:
                status = "zero"
            else:
                status = "partial"
            allocations[r["erp_code"]] = {"allocated_amount": round(allocated, 2), "status": status}

    # Leftover top-up: forward sweep P2 -> P3 -> P4 -> P5, vendor_id
    # ascending within a bucket (dataset construction order == that order).
    leftover = funds_left - sum(allocations[r["erp_code"]]["allocated_amount"] for r in normal)
    spent = 0.0
    if leftover > EPS:
        for b in BUCKET_ORDER:
            rows_ = by_bucket[b]
            while True:
                paid_any = False
                for r in rows_:
                    cap = r["required_amount"]
                    cur = allocations[r["erp_code"]]["allocated_amount"]
                    if cur >= cap - EPS:
                        continue
                    step_amt = r["required_amount"] * STEP
                    if leftover < step_amt - EPS:
                        continue
                    new_amt = cur + step_amt
                    allocations[r["erp_code"]]["allocated_amount"] = round(new_amt, 2)
                    allocations[r["erp_code"]]["status"] = (
                        "full" if new_amt >= cap - EPS else ("zero" if new_amt <= EPS else "partial")
                    )
                    leftover -= step_amt
                    spent += step_amt
                    paid_any = True
                if not paid_any:
                    break

    return {
        "guaranteed_total": round(guaranteed_total, 2),
        "escalation": escalation,
        "escalation_shortfall": round(max(-remaining, 0.0), 2),
        "bucket_pct": pct,
        "exceptional_shortfall": exceptional,
        "leftover_topup_total": round(spent, 2),
        "leftover_remaining": round(leftover, 2),
        "allocations": allocations,
    }


def render_scenario_md(title, available_funds, rows, result):
    lines = [f"# Prediction — {title}", "", f"Available Funds: Rs {available_funds:,.2f}", ""]
    lines.append(f"- guaranteed_total (Must Pay + Commitment): Rs {result['guaranteed_total']:,.2f}")
    lines.append(f"- escalation: {result['escalation']} (escalation_shortfall: Rs {result['escalation_shortfall']:,.2f})")
    lines.append(f"- bucket_pct: {result['bucket_pct']}")
    lines.append(f"- exceptional_shortfall: {result['exceptional_shortfall']}")
    lines.append(f"- leftover_topup_total: Rs {result['leftover_topup_total']:,.2f}")
    lines.append(f"- leftover_remaining: Rs {result['leftover_remaining']:,.2f}")
    lines.append("")
    lines.append("| ERP Code | Vendor | Category | Tag | Rule | Required | Allocated | Status |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for r in rows:
        a = result["allocations"].get(r["erp_code"])
        if a is None:
            # Zero outstanding balance -> excluded from generate_plan()
            # entirely (docs/06/docs/14 "drops out of planning"), not just
            # zero-allocated. See allocate()'s own docstring.
            lines.append(
                f"| {r['erp_code']} | {r['vendor_name']} | {r['category']} | {r['priority_tag']} | {r['rule']} | "
                f"{r['required_amount']:,.2f} | (excluded — zero outstanding balance) | n/a |"
            )
            continue
        lines.append(
            f"| {r['erp_code']} | {r['vendor_name']} | {r['category']} | {r['priority_tag']} | {r['rule']} | "
            f"{r['required_amount']:,.2f} | {a['allocated_amount']:,.2f} | {a['status']} |"
        )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    rows = compute_rows()
    total_required = sum(r["required_amount"] for r in rows)
    guaranteed_total = sum(r["required_amount"] for r in rows if r["category"] in ("Must Pay", "Commitment"))
    print(f"Total required across all 45 vendors: {total_required:,.2f}")
    print(f"Guaranteed (Must Pay + Commitment) total: {guaranteed_total:,.2f}")

    scenarios = [
        ("1_fully_funded", "Scenario 1 — Fully funded", 9_000_000),
        ("2_moderate_shortfall", "Scenario 2 — Moderate shortfall", 7_500_000),
        ("3_severe_shortfall", "Scenario 3 — Severe shortfall", 4_000_000),
    ]
    for key, title, funds in scenarios:
        result = allocate(funds, rows)
        md = render_scenario_md(title, funds, rows, result)
        out_path = os.path.join(os.path.dirname(__file__), f"predictions_scenario_{key}.md")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(md)
        print(f"Wrote {out_path}")

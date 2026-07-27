"""Tests against the real ingested database (backend/db/app.db) — no synthetic
data, same convention as backend/models/new_model_1/test_allocator.py — plus
a few pure-function tests of the round-robin cutting math itself
(_find_bucket_pcts takes plain dicts, not a session, so synthetic numbers
there are the clearest way to pin down the exact cascade docs/14 describes).
"""
import pandas as pd
import pytest

from backend.db.models import Vendor
from backend.db.session import SessionLocal
from backend.models.new_model_2.allocator import (
    PCT_FLOOR,
    PCT_START,
    PCT_STEP,
    _apply_leftover_topup,
    _find_bucket_pcts,
    generate_plan,
)
from backend.shared.enums import AllocationStatus, VendorCategory, VendorPriorityTag
from backend.shared.min_funds_v2 import calculate_minimum_funds_required_v2

CEILING = {"P2": 0.95, "P3": 0.90, "P4": 0.85}  # pure _find_bucket_pcts unit tests only — P2-P4, no P5 (see FLOOR)
FLOOR = {"P2": PCT_FLOOR, "P3": PCT_FLOOR, "P4": PCT_FLOOR}  # matches CEILING's keys, same pure-unit-test scope

# Matches allocator.py's real _BUCKET_SEED (P2/P3/P4/P5) — for tests that go
# through the real generate_plan() (DB-backed bucket_pct now always has all
# 4 keys, and P5's own ceiling/floor (50%/10%, docs/14, confirmed with
# Finance 2026-07-24) differ from P2/P3/P4's 95%/90%/85% and shared 25%
# floor, so these can't reuse the plain CEILING/FLOOR/PCT_FLOOR above).
REAL_CEILING = {"P2": 0.95, "P3": 0.90, "P4": 0.85, "P5": 0.50}
REAL_FLOOR = {"P2": 0.25, "P3": 0.25, "P4": 0.25, "P5": 0.10}


def _row(vendor_id, bucket, required_amount, allocated_amount, outstanding_balance=None, erp_code=None):
    """Minimal Normal/Inactive-allocation dict — same shape
    _apply_leftover_topup expects out of generate_plan()'s own
    normal_allocations list. effective_bucket is what grouping actually
    keys off; for these plain P2/P3/P4 rows it's identical to
    priority_tag."""
    return {
        "vendor_id": vendor_id,
        "erp_code": erp_code or f"V{vendor_id}",
        "priority_tag": bucket,
        "effective_bucket": bucket,
        "required_amount": required_amount,
        "outstanding_balance": outstanding_balance if outstanding_balance is not None else required_amount,
        "allocated_amount": allocated_amount,
        "status": AllocationStatus.PARTIAL.value,
    }


def test_absolute_funds_gives_100_percent_not_ceiling():
    """docs/14 fix (2026-07-19): bucket ceilings only apply once funds are
    short of paying every Normal vendor their full 100% — absolute funds
    (this test's >=2x-required case) must suggest 100%, same as every other
    model, never capped at the bucket ceiling just because ceilings exist."""
    result = calculate_minimum_funds_required_v2()
    plan = generate_plan(available_funds=result["total"] * 2)
    assert plan["bucket_pct"] == pytest.approx({b: PCT_START for b in REAL_CEILING})
    assert not plan["exceptional_shortfall"]
    allocations = plan["allocations"]
    assert (allocations["allocated_amount"] <= allocations["outstanding_balance"] + 1e-6).all()


def test_ceiling_applies_once_funds_short_of_100_percent_for_everyone():
    """The bucket-ceiling mechanism itself only kicks in below absolute
    funds — pick a funds figure strictly between ceiling-total and
    100%-for-everyone (computed from the real data, not guessed) and
    confirm the model falls back to ceiling, not 100%."""
    session = SessionLocal()
    try:
        from backend.shared.min_funds import _load_vendors_and_ledger
        from backend.shared.min_funds_v2 import required_amount_v2

        vendors, ledger = _load_vendors_and_ledger(session)
        guaranteed_total = 0.0
        ceiling_total = 0.0
        normal_required_total = 0.0
        for v in vendors:
            amount, _ = required_amount_v2(v, ledger[v.id])
            if v.category == VendorCategory.NORMAL:
                normal_required_total += amount
                bucket = (v.priority_tag if v.priority_tag is not None else "P5")
                ceiling_total += amount * REAL_CEILING[bucket]
            else:
                guaranteed_total += amount

        # Midpoint between ceiling-total and 100%-for-everyone — guaranteed
        # to sit strictly inside the "short of 100%, but ceiling still fits"
        # window regardless of the real data's actual bucket distribution.
        available_funds = guaranteed_total + (ceiling_total + normal_required_total) / 2
        plan = generate_plan(available_funds=available_funds, session=session)
        assert plan["bucket_pct"] == pytest.approx(REAL_CEILING)
        assert not plan["exceptional_shortfall"]
    finally:
        session.rollback()
        session.close()


def test_must_pay_and_commitment_always_fully_funded_under_severe_shortfall():
    session = SessionLocal()
    try:
        vendors = session.query(Vendor).order_by(Vendor.id).limit(2).all()
        vendors[0].category = VendorCategory.MUST_PAY
        vendors[1].category = VendorCategory.COMMITMENT
        vendors[1].commitment_months = 2

        plan = generate_plan(available_funds=1, session=session)  # near-zero funds
        allocations = plan["allocations"]
        guaranteed_rows = allocations[allocations["status"] == "guaranteed"]
        assert {vendors[0].id, vendors[1].id} <= set(guaranteed_rows["vendor_id"])
        for _, row in guaranteed_rows.iterrows():
            assert row["allocated_amount"] == row["required_amount"]
        assert plan["escalation"] is True
    finally:
        session.rollback()
        session.close()


def test_exceptional_shortfall_flagged_not_ground_below_floor():
    """docs/14 rule 6: only once every bucket is at its own 25% floor and it
    still doesn't fit is this exceptional — not something to keep grinding
    past."""
    session = SessionLocal()
    try:
        result = calculate_minimum_funds_required_v2()
        plan = generate_plan(available_funds=result["total"] * 0.02, session=session)
        assert plan["exceptional_shortfall"] is True
        assert plan["bucket_pct"] == pytest.approx(REAL_FLOOR)  # P5's own floor (0.20) differs from P2/P3/P4's 0.25
    finally:
        session.rollback()
        session.close()


def test_eligible_vendor_ids_excludes_locked_vendor_from_pool():
    session = SessionLocal()
    try:
        all_ids = {v.id for v in session.query(Vendor).filter(Vendor.category == VendorCategory.NORMAL).all()}
        locked_id = next(iter(all_ids))
        eligible_ids = all_ids - {locked_id}

        plan = generate_plan(available_funds=1_000_000_000, session=session, eligible_vendor_ids=eligible_ids)
        assert locked_id not in plan["allocations"]["vendor_id"].values
    finally:
        session.rollback()
        session.close()


def test_no_allocation_exceeds_outstanding_balance():
    result = calculate_minimum_funds_required_v2()
    plan = generate_plan(available_funds=result["total"] * 2)
    allocations = plan["allocations"]
    assert (allocations["allocated_amount"] <= allocations["outstanding_balance"] + 1e-6).all()


def test_no_priority_tag_falls_back_to_last_bucket_defensively():
    """docs/14 rule 7: a Normal vendor with no priority tag at all
    (shouldn't happen once scripts/seed_priority_tags.py has run) is
    defensively treated as the lowest-priority bucket (bucket_order[-1] —
    P5 now that it exists, docs/11 Task 2), not silently excluded or
    crashed on."""
    session = SessionLocal()
    try:
        vendor = (
            session.query(Vendor)
            .filter(Vendor.category == VendorCategory.NORMAL, Vendor.opening_balance > 1)
            .first()
        )
        vendor.priority_tag = None
        session.flush()
        plan = generate_plan(available_funds=1_000_000_000, session=session)
        row = plan["allocations"][plan["allocations"]["vendor_id"] == vendor.id].iloc[0]
        assert row["priority_tag"] == VendorPriorityTag.P5.value
    finally:
        session.rollback()
        session.close()


# ---- pure round-robin cutting math (_find_bucket_pcts) -------------------
# One vendor per bucket, required_amount=100 each, so cost == pct sum * 100 —
# makes the exact cascade easy to hand-verify.


def test_find_bucket_pcts_returns_ceiling_when_it_fits():
    vendor_bucket = {1: "P4", 2: "P3", 3: "P2"}
    required = {1: 100.0, 2: 100.0, 3: 100.0}
    bucket_pct, exceptional = _find_bucket_pcts(vendor_bucket, required, CEILING, FLOOR, funds_left=1000.0)
    assert bucket_pct == CEILING
    assert not exceptional


def test_find_bucket_pcts_cuts_p4_first_on_a_small_shortfall():
    vendor_bucket = {1: "P4", 2: "P3", 3: "P2"}
    required = {1: 100.0, 2: 100.0, 3: 100.0}
    # Ceiling total = 85+90+95 = 270; one 5% cut off P4 alone (-5) reaches 265.
    bucket_pct, exceptional = _find_bucket_pcts(vendor_bucket, required, CEILING, FLOOR, funds_left=265.0)
    assert bucket_pct["P4"] == pytest.approx(0.80)
    assert bucket_pct["P3"] == pytest.approx(0.90)
    assert bucket_pct["P2"] == pytest.approx(0.95)
    assert not exceptional


def test_find_bucket_pcts_p4_drops_out_of_rotation_at_floor():
    """docs/14 rule 5's own worked example: P4 hits its 25% floor first
    (fewest steps from its ceiling to the floor) and drops out, after which
    the cycle continues P3 -> P2 only. Hand-derived: P4 needs exactly 12
    cuts to go from 85% to the 25% floor; by the time its 12th cut lands
    (round-robin position 0 of 3), P3 has had 11 cuts (0.90 -> 0.35) and P2
    11 cuts (0.95 -> 0.40) — total = 25+35+40 = 100."""
    vendor_bucket = {1: "P4", 2: "P3", 3: "P2"}
    required = {1: 100.0, 2: 100.0, 3: 100.0}
    bucket_pct, exceptional = _find_bucket_pcts(vendor_bucket, required, CEILING, FLOOR, funds_left=100.0)
    assert bucket_pct["P4"] == pytest.approx(PCT_FLOOR)
    assert bucket_pct["P3"] == pytest.approx(0.35)
    assert bucket_pct["P2"] == pytest.approx(0.40)
    assert not exceptional


def test_find_bucket_pcts_exceptional_when_every_bucket_at_floor_still_short():
    vendor_bucket = {1: "P4"}
    required = {1: 1000.0}  # floor (25%) alone needs 250 — funds far below that
    bucket_pct, exceptional = _find_bucket_pcts(vendor_bucket, required, CEILING, FLOOR, funds_left=100.0)
    assert bucket_pct["P4"] == pytest.approx(PCT_FLOOR)
    assert exceptional is True


def test_pct_step_is_the_documented_5_percent():
    assert PCT_STEP == pytest.approx(0.05)
    assert PCT_FLOOR == pytest.approx(0.25)


# ---- leftover funds top-up (_apply_leftover_topup) — pure-function unit tests ----
# Same synthetic-numbers convention as the _find_bucket_pcts tests above: rows
# are plain dicts, small hand-checkable amounts, no DB involved.


def test_topup_noop_when_leftover_is_zero():
    rows = [_row(1, "P2", 100.0, 80.0)]
    spent, remaining = _apply_leftover_topup(rows, leftover=0.0)
    assert spent == 0.0
    assert remaining == 0.0
    assert rows[0]["allocated_amount"] == 80.0


def test_topup_covers_exactly_one_p2_vendor_one_step():
    p2 = _row(1, "P2", 100.0, 80.0)
    p3 = _row(2, "P3", 100.0, 70.0)
    p4 = _row(3, "P4", 100.0, 60.0)
    rows = [p2, p3, p4]
    spent, remaining = _apply_leftover_topup(rows, leftover=5.0)
    assert spent == pytest.approx(5.0)
    assert remaining == pytest.approx(0.0)
    assert p2["allocated_amount"] == pytest.approx(85.0)
    assert p2["status"] == AllocationStatus.PARTIAL.value
    assert p3["allocated_amount"] == 70.0  # untouched
    assert p4["allocated_amount"] == 60.0  # untouched


def test_topup_pays_multiple_p2_vendors_in_the_same_pass():
    v1 = _row(1, "P2", 100.0, 80.0)
    v2 = _row(2, "P2", 100.0, 80.0)
    rows = [v1, v2]
    spent, remaining = _apply_leftover_topup(rows, leftover=10.0)
    assert spent == pytest.approx(10.0)
    assert remaining == pytest.approx(0.0)
    assert v1["allocated_amount"] == pytest.approx(85.0)
    assert v2["allocated_amount"] == pytest.approx(85.0)


def test_topup_skips_p2_lands_on_p3_when_p2_step_too_big():
    p2 = _row(1, "P2", 1000.0, 500.0)  # step = 50, too big for leftover
    p3 = _row(2, "P3", 100.0, 90.0)  # step = 5, fits
    rows = [p2, p3]
    spent, remaining = _apply_leftover_topup(rows, leftover=5.0)
    assert p2["allocated_amount"] == 500.0  # untouched
    assert p3["allocated_amount"] == pytest.approx(95.0)
    assert spent == pytest.approx(5.0)
    assert remaining == pytest.approx(0.0)


def test_topup_same_p4_vendor_bumped_across_multiple_passes():
    p2 = _row(1, "P2", 1000.0, 500.0)  # never fits, drops out immediately
    p3 = _row(2, "P3", 1000.0, 500.0)  # never fits, drops out immediately
    p4 = _row(3, "P4", 100.0, 0.0)  # step = 5, alone in its bucket
    rows = [p2, p3, p4]
    spent, remaining = _apply_leftover_topup(rows, leftover=17.0)
    assert p4["allocated_amount"] == pytest.approx(15.0)  # 3 successive +5 bumps
    assert spent == pytest.approx(15.0)
    assert remaining == pytest.approx(2.0)  # too small for a 4th step
    assert p2["allocated_amount"] == 500.0
    assert p3["allocated_amount"] == 500.0


def test_topup_stops_cleanly_when_remainder_smaller_than_any_step():
    p4 = _row(1, "P4", 100.0, 90.0)  # one more step (5) would exceed leftover
    spent, remaining = _apply_leftover_topup([p4], leftover=3.0)
    assert spent == 0.0
    assert remaining == pytest.approx(3.0)
    assert p4["allocated_amount"] == 90.0


def test_topup_never_bumps_vendor_already_at_100_percent():
    p2 = _row(1, "P2", 100.0, 100.0)  # already FULL
    p2["status"] = AllocationStatus.FULL.value
    spent, remaining = _apply_leftover_topup([p2], leftover=50.0)
    assert spent == 0.0
    assert remaining == pytest.approx(50.0)
    assert p2["allocated_amount"] == 100.0
    assert p2["status"] == AllocationStatus.FULL.value


def test_topup_respects_outstanding_balance_clamp():
    # required 100, but vendor only owes 92 — already clamped below 100% by
    # the same outstanding-balance check generate_plan()'s main loop applies.
    p2 = _row(1, "P2", 100.0, 90.0, outstanding_balance=92.0)
    spent, remaining = _apply_leftover_topup([p2], leftover=10.0)
    assert p2["allocated_amount"] == pytest.approx(92.0)  # not 95 — clamped
    assert spent == pytest.approx(2.0)  # only the 2 rupees it could actually take
    assert remaining == pytest.approx(8.0)
    assert p2["status"] == AllocationStatus.PARTIAL.value  # never reached required_amount


def test_topup_is_deterministic_lower_vendor_id_wins_the_single_slot():
    for _ in range(5):
        v_low = _row(3, "P2", 100.0, 90.0)
        v_high = _row(5, "P2", 100.0, 90.0)
        spent, remaining = _apply_leftover_topup([v_high, v_low], leftover=5.0)  # deliberately out of order
        assert v_low["allocated_amount"] == pytest.approx(95.0)
        assert v_high["allocated_amount"] == 90.0
        assert spent == pytest.approx(5.0)
        assert remaining == pytest.approx(0.0)


def test_topup_absolute_funds_scenario_is_a_noop():
    rows = [_row(1, "P2", 100.0, 100.0), _row(2, "P3", 50.0, 50.0), _row(3, "P4", 200.0, 200.0)]
    spent, remaining = _apply_leftover_topup(rows, leftover=1_000_000.0)
    assert spent == 0.0
    assert remaining == pytest.approx(1_000_000.0)
    for r in rows:
        assert r["allocated_amount"] == r["required_amount"]


def test_topup_guaranteed_rows_are_structurally_excluded():
    """Must Pay/Commitment rows are never passed into _apply_leftover_topup
    at all (generate_plan only ever passes normal_allocations) — assert
    explicitly rather than assuming: a guaranteed row has no effective_bucket,
    so if one were ever passed in this raises instead of silently mis-
    bucketing it."""
    guaranteed_shaped_row = {
        "vendor_id": 1,
        "erp_code": "V1",
        "required_amount": 100.0,
        "outstanding_balance": 100.0,
        "allocated_amount": 100.0,
        "status": AllocationStatus.GUARANTEED.value,
        # no "effective_bucket" key — guaranteed rows never carry one
    }
    with pytest.raises(KeyError):
        _apply_leftover_topup([guaranteed_shaped_row], leftover=50.0)


# ---- leftover funds top-up — integration tests through generate_plan() ----


def _reference_topup_simulation(rows, leftover):
    """Independent re-implementation of docs/14's top-up sweep, used as an
    oracle against generate_plan()'s real output in
    test_topup_raises_a_vendor_above_its_bucket_ceiling_in_shortfall below —
    kept deliberately separate from _apply_leftover_topup's own code."""
    by_bucket = {"P2": [], "P3": [], "P4": [], "P5": []}
    for r in rows:
        by_bucket[r["priority_tag"]].append(dict(r))
    for bucket_rows in by_bucket.values():
        bucket_rows.sort(key=lambda r: r["vendor_id"])
    spent = 0.0
    for bucket in ["P2", "P3", "P4", "P5"]:
        bucket_rows = by_bucket[bucket]
        progress = True
        while progress:
            progress = False
            for r in bucket_rows:
                cap = min(r["required_amount"], r["outstanding_balance"])
                step = r["required_amount"] * PCT_STEP
                if r["allocated_amount"] >= cap - 1.0:
                    continue
                if leftover < step - 1.0:
                    continue
                new_amount = min(r["allocated_amount"] + step, cap)
                actual = new_amount - r["allocated_amount"]
                r["allocated_amount"] = new_amount
                leftover -= actual
                spent += actual
                progress = True
    final_by_id = {r["vendor_id"]: r["allocated_amount"] for bucket_rows in by_bucket.values() for r in bucket_rows}
    return final_by_id, spent, leftover


def test_topup_raises_a_vendor_above_its_bucket_ceiling_in_shortfall():
    """Engineer a real-data shortfall where the ceiling fits with room to
    spare (funds_left strictly between ceiling-total and 100%-for-everyone —
    same window test_ceiling_applies_once_funds_short_of_100_percent_for_everyone
    uses), so the top-up step has real leftover to spend. Confirm at least
    one vendor's final allocated_amount exceeds their plain bucket-ceiling
    amount, and that the exact final numbers match an independently
    re-implemented reference simulation."""
    session = SessionLocal()
    try:
        from backend.shared.min_funds import _load_vendors_and_ledger
        from backend.shared.min_funds_v2 import required_amount_v2

        vendors, ledger = _load_vendors_and_ledger(session)
        guaranteed_total = 0.0
        normal_rows = []
        for v in vendors:
            amount, _ = required_amount_v2(v, ledger[v.id])
            if v.category == VendorCategory.NORMAL:
                bucket = v.priority_tag if v.priority_tag is not None else "P5"
                outstanding = max(float(v.opening_balance) - float(v.paid_so_far_this_month), 0.0)
                normal_rows.append(
                    {
                        "vendor_id": v.id,
                        "priority_tag": bucket,
                        "required_amount": amount,
                        "outstanding_balance": outstanding,
                    }
                )
            else:
                guaranteed_total += amount

        ceiling_total = sum(r["required_amount"] * REAL_CEILING[r["priority_tag"]] for r in normal_rows)
        normal_required_total = sum(r["required_amount"] for r in normal_rows)
        funds_left = (ceiling_total + normal_required_total) / 2
        available_funds = guaranteed_total + funds_left

        plan = generate_plan(available_funds=available_funds, session=session)
        assert plan["bucket_pct"] == pytest.approx(REAL_CEILING)  # confirms no cutting happened, ceiling is the baseline

        baseline_rows = []
        for r in normal_rows:
            baseline_allocated = min(r["required_amount"] * REAL_CEILING[r["priority_tag"]], r["outstanding_balance"])
            baseline_rows.append({**r, "allocated_amount": baseline_allocated})
        leftover = funds_left - sum(r["allocated_amount"] for r in baseline_rows)
        expected_final, expected_spent, expected_remaining = _reference_topup_simulation(baseline_rows, leftover)

        assert plan["leftover_topup_total"] == pytest.approx(expected_spent, abs=1.0)
        assert plan["leftover_remaining"] == pytest.approx(expected_remaining, abs=1.0)

        allocations = plan["allocations"]
        normal_alloc = allocations[allocations["category"] == VendorCategory.NORMAL.value]
        for _, row in normal_alloc.iterrows():
            assert row["allocated_amount"] == pytest.approx(expected_final[row["vendor_id"]], abs=1.0)

        ceiling_amounts = {r["vendor_id"]: r["required_amount"] * REAL_CEILING[r["priority_tag"]] for r in normal_rows}
        assert any(
            expected_final[vid] > ceiling_amounts[vid] + 1.0 for vid in expected_final
        ), "expected at least one vendor topped up above their plain bucket-ceiling amount"
    finally:
        session.rollback()
        session.close()


def test_topup_never_pushes_total_allocated_past_available_funds():
    result = calculate_minimum_funds_required_v2()
    available_funds = result["total"] * 0.6  # shortfall, guaranteed to invoke top-up somewhere
    plan = generate_plan(available_funds=available_funds)
    total_allocated = plan["allocations"]["allocated_amount"].sum()
    assert total_allocated <= available_funds + 1.0


def test_topup_full_generate_plan_is_deterministic():
    result = calculate_minimum_funds_required_v2()
    available_funds = result["total"] * 0.6
    plan_a = generate_plan(available_funds=available_funds)
    plan_b = generate_plan(available_funds=available_funds)
    assert plan_a["leftover_topup_total"] == pytest.approx(plan_b["leftover_topup_total"])
    assert plan_a["leftover_remaining"] == pytest.approx(plan_b["leftover_remaining"])
    cols = ["vendor_id", "allocated_amount", "status"]
    a = plan_a["allocations"][cols].sort_values("vendor_id").reset_index(drop=True)
    b = plan_b["allocations"][cols].sort_values("vendor_id").reset_index(drop=True)
    pd.testing.assert_frame_equal(a, b)


def test_topup_must_pay_and_commitment_never_touched_even_with_leftover():
    session = SessionLocal()
    try:
        vendors = session.query(Vendor).order_by(Vendor.id).limit(2).all()
        vendors[0].category = VendorCategory.MUST_PAY
        vendors[1].category = VendorCategory.COMMITMENT
        vendors[1].commitment_months = 2
        session.flush()

        result = calculate_minimum_funds_required_v2(session=session)
        plan = generate_plan(available_funds=result["total"] * 2, session=session)  # absolute funds, plenty leftover
        guaranteed_rows = plan["allocations"][plan["allocations"]["status"] == AllocationStatus.GUARANTEED.value]
        assert {vendors[0].id, vendors[1].id} <= set(guaranteed_rows["vendor_id"])
        for _, row in guaranteed_rows.iterrows():
            assert row["allocated_amount"] == row["required_amount"]
    finally:
        session.rollback()
        session.close()



# ---- Inactive category (docs/14 — renamed 2026-07-24 from "Exit"; the old
# EXIT category/tag and its fold-into-last-bucket behavior no longer exist
# anywhere in this codebase) ------------------------------------------------
# Inactive carries its own real, Finance-picked P5 tag and is grouped
# exactly like Normal — no folding, no display-only tag rewrite.


def test_inactive_category_exists_and_is_distinct():
    assert VendorCategory.INACTIVE.value not in {
        VendorCategory.MUST_PAY.value,
        VendorCategory.COMMITMENT.value,
        VendorCategory.NORMAL.value,
    }


def test_inactive_vendor_grouped_by_own_priority_tag_not_folded():
    """An Inactive vendor tagged P3 tracks a real P3 Normal vendor exactly —
    grouped by its own real tag, never folded into any fixed bucket."""
    session = SessionLocal()
    try:
        normal_p3 = (
            session.query(Vendor)
            .filter(Vendor.category == VendorCategory.NORMAL, Vendor.opening_balance > 100_000)
            .order_by(Vendor.id)
            .first()
        )
        inactive_vendor = (
            session.query(Vendor)
            .filter(
                Vendor.category == VendorCategory.NORMAL, Vendor.id != normal_p3.id, Vendor.opening_balance > 100_000
            )
            .order_by(Vendor.id)
            .offset(1)
            .first()
        )
        normal_p3.priority_tag = VendorPriorityTag.P3
        inactive_vendor.category = VendorCategory.INACTIVE
        inactive_vendor.priority_tag = VendorPriorityTag.P3
        session.flush()

        result = calculate_minimum_funds_required_v2(session=session)
        plan = generate_plan(available_funds=result["total"] * 0.5, session=session)  # shortfall -> real cutting
        allocations = plan["allocations"]

        inactive_row = allocations[allocations["vendor_id"] == inactive_vendor.id].iloc[0]
        p3_row = allocations[allocations["vendor_id"] == normal_p3.id].iloc[0]
        assert inactive_row["priority_tag"] == VendorPriorityTag.P3.value  # real tag, never rewritten to a display-only label
        assert inactive_row["status"] != AllocationStatus.GUARANTEED.value

        inactive_pct = (
            inactive_row["allocated_amount"] / inactive_row["required_amount"] if inactive_row["required_amount"] else 0
        )
        p3_pct = p3_row["allocated_amount"] / p3_row["required_amount"] if p3_row["required_amount"] else 0
        assert inactive_pct == pytest.approx(p3_pct, abs=1e-6)
    finally:
        session.rollback()
        session.close()


def test_inactive_vendor_with_no_priority_tag_falls_back_defensively():
    """Same defensive fallback as an untagged Normal vendor (docs/14 rule
    7) — Inactive gets no special-cased tagging behavior anywhere."""
    session = SessionLocal()
    try:
        vendor = (
            session.query(Vendor)
            .filter(Vendor.category == VendorCategory.NORMAL, Vendor.opening_balance > 1)
            .first()
        )
        vendor.category = VendorCategory.INACTIVE
        vendor.priority_tag = None
        session.flush()

        plan = generate_plan(available_funds=1_000_000_000, session=session)
        row = plan["allocations"][plan["allocations"]["vendor_id"] == vendor.id].iloc[0]
        assert row["priority_tag"] == VendorPriorityTag.P5.value
    finally:
        session.rollback()
        session.close()


def test_inactive_vendor_folds_into_a_hypothetical_new_bucket_added_via_the_table():
    """The one test that proves 'no hardcoding' (docs/11 Task 2 Part C's own
    emphasis): add a brand-new bucket row (a hypothetical P6, added purely
    through the PriorityBucket DB table — no monkeypatching of any Python
    list/dict) and confirm an Inactive vendor tagged into it resolves
    correctly, with zero code changes anywhere in allocator.py."""
    from backend.db.models import PriorityBucket
    from backend.models.new_model_2.allocator import _seed_and_read_buckets

    session = SessionLocal()
    try:
        _seed_and_read_buckets(session)  # ensure P2-P5 already exist before appending
        session.add(
            PriorityBucket(bucket_key="P6", display_label="P6", ceiling_pct=0.70, floor_pct=0.15, rotation_position=99)
        )
        session.flush()

        vendor = (
            session.query(Vendor)
            .filter(Vendor.category == VendorCategory.NORMAL, Vendor.opening_balance > 1)
            .first()
        )
        vendor.category = VendorCategory.INACTIVE
        vendor.priority_tag = None  # no VendorPriorityTag.P6 member exists — falls back to bucket_order[-1] == "P6"
        session.flush()

        plan = generate_plan(available_funds=1.0, session=session)  # severe shortfall -> real bucket assignment
        row = plan["allocations"][plan["allocations"]["vendor_id"] == vendor.id].iloc[0]
        assert row["priority_tag"] == "P6"  # the table-added bucket, picked up with zero allocator.py changes
        assert "P6" in plan["bucket_pct"]
    finally:
        session.rollback()
        session.close()


def test_inactive_vendor_cut_under_severe_shortfall_not_guaranteed():
    session = SessionLocal()
    try:
        vendor = (
            session.query(Vendor)
            .filter(Vendor.category == VendorCategory.NORMAL, Vendor.opening_balance > 100_000)
            .first()
        )
        vendor.category = VendorCategory.INACTIVE
        vendor.priority_tag = VendorPriorityTag.P5
        session.flush()

        result = calculate_minimum_funds_required_v2(session=session)
        plan = generate_plan(available_funds=result["total"] * 0.02, session=session)  # severe shortfall
        row = plan["allocations"][plan["allocations"]["vendor_id"] == vendor.id].iloc[0]
        assert row["status"] != AllocationStatus.GUARANTEED.value
        assert row["allocated_amount"] < row["required_amount"] - 1.0  # actually cut down, like real P4 peers
    finally:
        session.rollback()
        session.close()


def test_inactive_vendor_with_zero_outstanding_balance_excluded_from_planning():
    """Inactive is treated exactly like Normal for the shared
    has_outstanding_balance exclusion (docs/14) — reuses the existing
    _load_vendors_and_ledger filter, same as any other vendor."""
    session = SessionLocal()
    try:
        vendor = (
            session.query(Vendor)
            .filter(Vendor.category == VendorCategory.NORMAL, Vendor.opening_balance > 0)
            .first()
        )
        vendor.category = VendorCategory.INACTIVE
        vendor.paid_so_far_this_month = vendor.opening_balance  # fully paid -> zero outstanding
        session.flush()

        plan = generate_plan(available_funds=1_000_000_000, session=session)
        assert vendor.id not in plan["allocations"]["vendor_id"].values
    finally:
        session.rollback()
        session.close()


def test_mixed_categories_generate_plan_respects_funds_and_tags_inactive_rows():
    """Integration test: a real mix of Must Pay, Commitment, Normal
    (P2/P3/P4), and Inactive (P5) under a shortfall — total allocated never
    exceeds available_funds, and Inactive vendors show up correctly tagged."""
    session = SessionLocal()
    try:
        vendors = session.query(Vendor).order_by(Vendor.id).limit(4).all()
        vendors[0].category = VendorCategory.MUST_PAY
        vendors[1].category = VendorCategory.COMMITMENT
        vendors[1].commitment_months = 3
        vendors[2].category = VendorCategory.NORMAL
        vendors[2].priority_tag = VendorPriorityTag.P2
        vendors[3].category = VendorCategory.INACTIVE
        vendors[3].priority_tag = VendorPriorityTag.P5
        session.flush()

        result = calculate_minimum_funds_required_v2(session=session)
        plan = generate_plan(available_funds=result["total"] * 0.5, session=session)

        allocations = plan["allocations"]
        assert allocations["allocated_amount"].sum() <= result["total"] * 0.5 + 1.0

        inactive_row = allocations[allocations["vendor_id"] == vendors[3].id]
        if not inactive_row.empty:  # empty only if this vendor happened to have zero outstanding balance
            assert inactive_row.iloc[0]["priority_tag"] == "P5"
            assert inactive_row.iloc[0]["status"] != AllocationStatus.GUARANTEED.value
    finally:
        session.rollback()
        session.close()


if __name__ == "__main__":
    # ponytail: smallest runnable self-check, no framework/fixtures.
    test_absolute_funds_gives_100_percent_not_ceiling()
    test_ceiling_applies_once_funds_short_of_100_percent_for_everyone()
    test_must_pay_and_commitment_always_fully_funded_under_severe_shortfall()
    test_exceptional_shortfall_flagged_not_ground_below_floor()
    test_eligible_vendor_ids_excludes_locked_vendor_from_pool()
    test_no_allocation_exceeds_outstanding_balance()
    test_no_priority_tag_falls_back_to_last_bucket_defensively()
    test_find_bucket_pcts_returns_ceiling_when_it_fits()
    test_find_bucket_pcts_cuts_p4_first_on_a_small_shortfall()
    test_find_bucket_pcts_p4_drops_out_of_rotation_at_floor()
    test_find_bucket_pcts_exceptional_when_every_bucket_at_floor_still_short()
    test_pct_step_is_the_documented_5_percent()
    test_topup_noop_when_leftover_is_zero()
    test_topup_covers_exactly_one_p2_vendor_one_step()
    test_topup_pays_multiple_p2_vendors_in_the_same_pass()
    test_topup_skips_p2_lands_on_p3_when_p2_step_too_big()
    test_topup_same_p4_vendor_bumped_across_multiple_passes()
    test_topup_stops_cleanly_when_remainder_smaller_than_any_step()
    test_topup_never_bumps_vendor_already_at_100_percent()
    test_topup_respects_outstanding_balance_clamp()
    test_topup_is_deterministic_lower_vendor_id_wins_the_single_slot()
    test_topup_absolute_funds_scenario_is_a_noop()
    test_topup_guaranteed_rows_are_structurally_excluded()
    test_topup_raises_a_vendor_above_its_bucket_ceiling_in_shortfall()
    test_topup_never_pushes_total_allocated_past_available_funds()
    test_topup_full_generate_plan_is_deterministic()
    test_topup_must_pay_and_commitment_never_touched_even_with_leftover()
    test_inactive_category_exists_and_is_distinct()
    test_inactive_vendor_grouped_by_own_priority_tag_not_folded()
    test_inactive_vendor_with_no_priority_tag_falls_back_defensively()
    test_inactive_vendor_folds_into_a_hypothetical_new_bucket_added_via_the_table()
    test_inactive_vendor_cut_under_severe_shortfall_not_guaranteed()
    test_inactive_vendor_with_zero_outstanding_balance_excluded_from_planning()
    test_mixed_categories_generate_plan_respects_funds_and_tags_inactive_rows()
    print("all new_model_2 allocator self-checks passed")

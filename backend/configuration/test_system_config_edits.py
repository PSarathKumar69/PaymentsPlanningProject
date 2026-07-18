"""Tests against the real ingested database (backend/db/app.db) — no synthetic
data, always session.rollback()'d, never committed."""
import pytest

from backend.configuration.system_config_edits import (
    update_config_value,
    update_model1_weights,
    update_model3_score_thresholds,
)
from backend.db.models import AuditLog, Config
from backend.db.session import SessionLocal
from backend.models.model1_weighted_scoring.scorer import WEIGHT_KEYS, score_vendors
from backend.models.model3_priority_bucket.bucketer import generate_plan
from backend.shared.enums import ChangeSource


def test_valid_weights_update_works_and_audits_only_changed():
    session = SessionLocal()
    try:
        # Defaults (docs/03, revised): aging=0.50, outstanding=0.0,
        # consistency=0.25, trend=0.25. Only vary aging/outstanding here
        # (summing to the same 0.50 the two of them cover today) so
        # consistency/trend stay unchanged and "only these two audited" is
        # actually exercised.
        changed = update_model1_weights(0.30, 0.20, 0.25, 0.25, session=session)
        assert set(changed) == {"aging", "outstanding"}  # only these two actually differ from 0.50/0.0/0.25/0.25

        assert float(session.query(Config).filter_by(key=WEIGHT_KEYS["aging"]).one().value) == 0.30
        assert float(session.query(Config).filter_by(key=WEIGHT_KEYS["outstanding"]).one().value) == 0.20

        entries = session.query(AuditLog).filter(AuditLog.field_name.in_(WEIGHT_KEYS.values())).all()
        assert len(entries) == 2
        aging_entry = next(e for e in entries if e.field_name == WEIGHT_KEYS["aging"])
        assert aging_entry.old_value == "0.5"
        assert aging_entry.new_value == "0.3"
        assert aging_entry.source == ChangeSource.UI_EDIT.value
    finally:
        session.rollback()
        session.close()


def test_weights_not_summing_to_one_rejected():
    session = SessionLocal()
    try:
        with pytest.raises(ValueError):
            update_model1_weights(0.5, 0.3, 0.2, 0.2, session=session)  # sums to 1.2
        assert session.query(AuditLog).filter(AuditLog.field_name.in_(WEIGHT_KEYS.values())).count() == 0
        assert float(session.query(Config).filter_by(key=WEIGHT_KEYS["aging"]).one().value) == 0.50  # unchanged (docs/03 revised default)
    finally:
        session.rollback()
        session.close()


def test_negative_weight_rejected():
    session = SessionLocal()
    try:
        with pytest.raises(ValueError):
            update_model1_weights(-0.1, 0.4, 0.4, 0.3, session=session)
        assert session.query(AuditLog).filter(AuditLog.field_name.in_(WEIGHT_KEYS.values())).count() == 0
    finally:
        session.rollback()
        session.close()


def test_threshold_ordering_violation_rejected():
    session = SessionLocal()
    try:
        with pytest.raises(ValueError):
            update_model3_score_thresholds(50, 70, session=session)  # p2 must be > p3
        assert session.query(AuditLog).filter_by(field_name="model3.threshold.p2_min_score").count() == 0
    finally:
        session.rollback()
        session.close()


def test_threshold_out_of_range_rejected():
    session = SessionLocal()
    try:
        with pytest.raises(ValueError):
            update_model3_score_thresholds(150, 50, session=session)
        assert session.query(AuditLog).filter_by(field_name="model3.threshold.p2_min_score").count() == 0
    finally:
        session.rollback()
        session.close()


def test_valid_threshold_update_works():
    session = SessionLocal()
    try:
        changed = update_model3_score_thresholds(65, 45, session=session)
        assert set(changed) == {"model3.threshold.p2_min_score", "model3.threshold.p3_min_score"}
        assert float(session.query(Config).filter_by(key="model3.threshold.p2_min_score").one().value) == 65
        assert float(session.query(Config).filter_by(key="model3.threshold.p3_min_score").one().value) == 45
    finally:
        session.rollback()
        session.close()


def test_bucket_percentage_out_of_range_rejected():
    session = SessionLocal()
    try:
        with pytest.raises(ValueError):
            update_config_value("model3.target_pct.p2", 150, session=session)
        with pytest.raises(ValueError):
            update_config_value("model3.target_pct.p2", 0, session=session)
        assert session.query(AuditLog).filter_by(field_name="model3.target_pct.p2").count() == 0
    finally:
        session.rollback()
        session.close()


def test_unknown_config_key_rejected():
    session = SessionLocal()
    try:
        with pytest.raises(ValueError):
            update_config_value("model1.weight.aging", 50, session=session)  # not in the allow-list for this setter
        with pytest.raises(ValueError):
            update_config_value("made.up.key", 50, session=session)
        assert session.query(AuditLog).filter_by(field_name="model1.weight.aging").count() == 0
        assert session.query(AuditLog).filter_by(field_name="made.up.key").count() == 0
    finally:
        session.rollback()
        session.close()


def test_bucket_percentage_valid_update_converts_and_audits_as_percentage():
    session = SessionLocal()
    try:
        old_fraction, new_fraction = update_config_value("model3.target_pct.p3", 60, session=session)
        assert old_fraction == 0.50
        assert new_fraction == 0.60
        assert float(session.query(Config).filter_by(key="model3.target_pct.p3").one().value) == 0.60

        entry = session.query(AuditLog).filter_by(field_name="model3.target_pct.p3").one()
        assert entry.old_value == "50.0"
        assert entry.new_value == "60"
    finally:
        session.rollback()
        session.close()


def test_non_monotonic_bucket_percentages_allowed():
    """docs/05 doesn't require P2 >= P3 >= P4 among the target percentages —
    confirmed default: no ordering is enforced here."""
    session = SessionLocal()
    try:
        update_config_value("model3.target_pct.p4", 90, session=session)  # P4 way above P2/P3 defaults — allowed
        assert float(session.query(Config).filter_by(key="model3.target_pct.p4").one().value) == 0.90
    finally:
        session.rollback()
        session.close()


def test_weight_update_picked_up_by_score_vendors_no_caching():
    session = SessionLocal()
    try:
        before = score_vendors(session=session)
        update_model1_weights(0.10, 0.60, 0.20, 0.10, session=session)
        after = score_vendors(session=session)

        # Same vendor set, but the ranking must have actually shifted since
        # the weights changed — proves score_vendors() re-reads config live.
        assert not before["score"].reset_index(drop=True).equals(after["score"].reset_index(drop=True))

        # Deterministic proof, not just "something changed": each factor's
        # own normalized score (aging_score, outstanding_score, etc.) is
        # weight-independent, so recomputing the weighted sum with the NEW
        # weights from those same per-factor scores must match exactly —
        # if score_vendors() had cached the prior (pre-update) weights, it wouldn't.
        sample = after.iloc[0]
        expected = 100 * (
            0.10 * sample["aging_score"]
            + 0.60 * sample["outstanding_score"]
            + 0.20 * sample["consistency_score"]
            + 0.10 * sample["trend_score"]
        )
        assert sample["score"] == pytest.approx(expected, abs=1e-6)
    finally:
        session.rollback()
        session.close()


def test_threshold_and_percentage_update_picked_up_by_generate_plan_no_caching():
    session = SessionLocal()
    try:
        plan_before = generate_plan(available_funds=1, session=session)  # near-zero funds -> shortfall branch
        # Lower P2's threshold drastically so far more vendors land in P2,
        # and raise P2's target percentage so more of the (still tiny) funds
        # concentrate there — both must be reflected without restarting anything.
        update_model3_score_thresholds(1, 0, session=session)  # almost everyone becomes P2
        update_config_value("model3.target_pct.p2", 95, session=session)

        plan_after = generate_plan(available_funds=1, session=session)

        p2_count_before = (plan_before["allocations"]["bucket"] == "P2").sum()
        p2_count_after = (plan_after["allocations"]["bucket"] == "P2").sum()
        assert p2_count_after > p2_count_before  # threshold change picked up live
    finally:
        session.rollback()
        session.close()

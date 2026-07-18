"""System-config edits — Configuration module, system-config half only
(docs/11-configuration-module.md). No Excel equivalent for these (docs/11:
"those have no Excel representation, so they get their own persisted
store") — just the `config` table + audit_log, following the same
transaction-safety and validate-before-write conventions as
backend/configuration/vendor_edits.py's vendor-field edits.

In scope: Model 1's four weights, Model 3's two score thresholds, Model
3's three bucket target percentages — exactly the config keys those two
models already read. Nothing else.
"""
from datetime import datetime

from backend.configuration.vendor_edits import _clean_value
from backend.db.models import AuditLog, Config
from backend.db.session import SessionLocal
from backend.models.model1_weighted_scoring.scorer import DEFAULT_WEIGHTS, WEIGHT_KEYS
from backend.models.model3_priority_bucket.bucketer import SCORE_THRESHOLD_SPEC, TARGET_PCT_SPEC
from backend.shared.enums import ChangeSource

_TARGET_PCT_KEYS = set(TARGET_PCT_SPEC)  # the only keys update_config_value() may touch


def _validate_weights(weights):
    for factor, value in weights.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise ValueError(f"model1 weight {factor!r} must be a non-negative number, got {value!r}")
    total = sum(weights.values())
    if abs(total - 1.0) > 1e-6:
        raise ValueError(f"model1 weights must sum to 1.0 (within 1e-6), got {total}: {weights}")


def _validate_thresholds(p2_threshold, p3_threshold):
    for name, value in [("p2_threshold", p2_threshold), ("p3_threshold", p3_threshold)]:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not (0 <= value <= 100):
            raise ValueError(f"model3 {name} must be a number in 0-100, got {value!r}")
    if not p2_threshold > p3_threshold:
        raise ValueError(f"model3 p2_threshold ({p2_threshold}) must be greater than p3_threshold ({p3_threshold})")


def _validate_config_value(key, value):
    if key not in _TARGET_PCT_KEYS:
        raise ValueError(f"unknown/not-editable config key {key!r}; must be one of {sorted(_TARGET_PCT_KEYS)}")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not (0 < value <= 100):
        raise ValueError(f"{key} must be a percentage in (0, 100], got {value!r}")


def _upsert_and_audit(session, key, new_raw_value, description, source, old_display=None, new_display=None):
    """Writes one config row (insert if missing, else update) and one
    audit_log row — but only if the value actually changed. Returns
    (changed, old_raw). `old_display`/`new_display` override the audit
    log's text when the stored raw value isn't itself Finance-facing (see
    update_config_value's percentage -> fraction conversion); by default
    (None) the raw value is used as-is, which is correct for weights and
    thresholds — they're stored and shown as the same number."""
    row = session.query(Config).filter_by(key=key).first()
    old_raw = float(row.value) if row is not None else None
    if old_raw is not None and abs(old_raw - new_raw_value) <= 1e-9:
        return False, old_raw
    if row is None:
        session.add(Config(key=key, value=str(new_raw_value), description=description))
    else:
        row.value = str(new_raw_value)
    session.add(
        AuditLog(
            timestamp=datetime.now(),
            vendor_id=None,
            field_name=key,
            old_value=_clean_value(key, old_raw if old_display is None else old_display),
            new_value=_clean_value(key, new_raw_value if new_display is None else new_display),
            source=source.value,
        )
    )
    return True, old_raw


def update_model1_weights(aging, outstanding, consistency, trend, source=ChangeSource.UI_EDIT, session=None):
    """All four weights together — one interdependent distribution, so
    they're validated and written as a single unit. Returns the list of
    factor names whose value actually changed (an audit_log row is written
    only for those).
    """
    new_weights = {"aging": aging, "outstanding": outstanding, "consistency": consistency, "trend": trend}
    _validate_weights(new_weights)

    owns_session = session is None
    session = session or SessionLocal()
    try:
        changed = []
        for factor, new_value in new_weights.items():
            key = WEIGHT_KEYS[factor]
            was_changed, _ = _upsert_and_audit(session, key, new_value, DEFAULT_WEIGHTS[factor][1], source)
            if was_changed:
                changed.append(factor)
        session.flush()
        if owns_session:
            session.commit()
        return changed
    except Exception:
        session.rollback()
        raise
    finally:
        if owns_session:
            session.close()


def update_model3_score_thresholds(p2_threshold, p3_threshold, source=ChangeSource.UI_EDIT, session=None):
    """P2/P3 thresholds together — p2 > p3 is a cross-field constraint, so
    both are validated and written as a single unit."""
    _validate_thresholds(p2_threshold, p3_threshold)
    new_values = {
        "model3.threshold.p2_min_score": p2_threshold,
        "model3.threshold.p3_min_score": p3_threshold,
    }

    owns_session = session is None
    session = session or SessionLocal()
    try:
        changed = []
        for key, new_value in new_values.items():
            description = SCORE_THRESHOLD_SPEC[key][1]
            was_changed, _ = _upsert_and_audit(session, key, new_value, description, source)
            if was_changed:
                changed.append(key)
        session.flush()
        if owns_session:
            session.commit()
        return changed
    except Exception:
        session.rollback()
        raise
    finally:
        if owns_session:
            session.close()


def update_config_value(key, value, source=ChangeSource.UI_EDIT, session=None):
    """Generic setter — restricted (allow-listed) to Model 3's three bucket
    target percentages. No cross-field constraint here, unlike the two
    functions above.

    `value` is the Finance-facing percentage (e.g. 60 for 60%) — it's
    converted to the fraction bucketer.py already reads and multiplies
    directly (0.75/0.50/0.40 by default) before being stored, so that
    reader needs no change. The audit log records the percentage Finance
    actually entered, not the internal fraction.

    # DEFAULT — confirm with Sarath: docs/05 doesn't require P2 >= P3 >= P4
    # (or any other ordering) among these three percentages, so a
    # non-monotonic combination (e.g. P3 higher than P2) is accepted here,
    # not rejected. Only each individual value's own (0, 100] range is
    # enforced.
    """
    _validate_config_value(key, value)
    fraction = value / 100.0

    owns_session = session is None
    session = session or SessionLocal()
    try:
        # new_display=value uses the exact Finance-facing input directly
        # (no reversal needed); old_display reverses the stored fraction
        # back to a percentage, rounded to avoid float noise (e.g. a stored
        # 0.4 reversing to 39.99999999999999 instead of 40.0).
        existing = session.query(Config).filter_by(key=key).first()
        old_fraction = float(existing.value) if existing is not None else None
        old_percentage = None if old_fraction is None else round(old_fraction * 100, 6)
        changed, _ = _upsert_and_audit(
            session, key, fraction, TARGET_PCT_SPEC[key][1], source, old_display=old_percentage, new_display=value
        )
        session.flush()
        if owns_session:
            session.commit()
        return old_fraction, (fraction if changed else old_fraction)
    except Exception:
        session.rollback()
        raise
    finally:
        if owns_session:
            session.close()

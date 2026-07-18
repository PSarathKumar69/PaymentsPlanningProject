"""Shared status/state enums — defined once, referenced everywhere.

Per CLAUDE.md's no-hardcoding rule: status/state values must not be
re-typed as ad hoc strings scattered through the codebase.
"""
import enum


class ChangeSource(str, enum.Enum):
    """Where a vendor-data/config override came from, for audit_log.source."""

    UI_EDIT = "ui_edit"
    EXCEL_UPLOAD = "excel_upload"
    INGESTION = "ingestion"  # one-time initial load, not a Finance-initiated override


class VendorCategory(str, enum.Enum):
    """Finance-facing vendor category — drives Model 3's bucket assignment.

    MUST_PAY -> P0, COMMITMENT -> P1, NORMAL -> P2/P3/P4 by Model 1's score.
    See docs/05-model3-priority-bucket.md. Every vendor defaults to NORMAL.
    """

    MUST_PAY = "must_pay"
    COMMITMENT = "commitment"
    NORMAL = "normal"


class PaymentStatus(str, enum.Enum):
    """A vendor's payment status for the current month, computed from their
    running paid-so-far-this-month total. See
    docs/06-weekly-planning-regeneration.md and backend/shared/payment_logging.py.
    """

    NOT_PAID = "not_paid"
    PARTIAL = "partial"
    PAID_IN_FULL = "paid_in_full"


class AllocationStatus(str, enum.Enum):
    """A single allocation row's funding outcome — shared by Model 2's and
    Model 3's generate_plan() and the regeneration engine, so a plan's
    "status" column always means the same thing regardless of which model
    produced it.
    """

    GUARANTEED = "guaranteed"  # P0/P1 / Must Pay/Commitment — always fully funded, never shrunk by a shortfall
    FULL = "full"
    PARTIAL = "partial"
    ZERO = "zero"
    OVERRIDE_LOCKED = "override_locked"  # Model 1 only: Finance's override excludes this vendor from recomputation —
    # allocated_amount is frozen at whatever the model last computed, not recomputed and not the override itself.


class PriorityBucket(str, enum.Enum):
    """Model 3's priority buckets (docs/05-model3-priority-bucket.md).

    MUST_PAY -> P0, COMMITMENT -> P1 (always fully funded first); NORMAL
    vendors split into P2/P3/P4 by Model 1's score.
    """

    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"
    P4 = "P4"

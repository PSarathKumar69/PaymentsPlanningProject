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
    AI_COLUMN_MAPPING = "ai_column_mapping"  # AI-mapping task: a header the AI layer resolved/auto-persisted


class VendorCategory(str, enum.Enum):
    """Finance-facing vendor category — drives Model 3's bucket assignment.

    MUST_PAY -> P0, COMMITMENT -> P1, NORMAL -> P2/P3/P4 by Model 1's score.
    See docs/05-model3-priority-bucket.md. Every vendor defaults to NORMAL.

    INACTIVE (docs/14-new-model-2.md): a 4th category, New Model 2 only —
    originally added 2026-07-22 as "Exit," **renamed 2026-07-24 to
    Inactive** (straight rename, signed off by Sarath — not a behavior
    change to how a vendor gets tagged into it). A vendor Finance tags
    directly, rather than re-categorizing into Must Pay/Commitment (New
    Model 1's still-unchanged rule, see docs/13-new-model-1.md). Treated
    exactly like NORMAL everywhere else in the codebase (required-amount
    calculation, drops out once outstanding balance hits zero). Carries
    its own real, Finance-picked priority_tag — P5, and only P5 — grouped
    by that tag exactly like a Normal vendor; unlike the old "Exit"
    behavior this replaced, there is no folding into a fixed/last bucket
    anymore (see backend/models/new_model_2/allocator.py).
    """

    MUST_PAY = "must_pay"
    COMMITMENT = "commitment"
    NORMAL = "normal"
    INACTIVE = "inactive"


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


class VendorPriorityTag(str, enum.Enum):
    """New Model 2's Finance-assigned priority tag (docs/14-new-model-2.md)
    — a deliberately separate concept/field from PriorityBucket above (old
    Model 3's P0-P4, score-derived from Model 1's now-retired weighted
    score). This one is set directly by Finance, never computed, and lives
    on its own field (Vendor.priority_tag, not PriorityBucket/`bucket`) so
    the two are never confused.

    P0 (Must Pay) and P1 (Commitment) are display-only labels, same mapping
    old Model 3 used — they never enter New Model 2's ceiling/cutting math
    (backend/models/new_model_2/allocator.py's bucket_order is P2-P5 only;
    Must Pay/Commitment are always guaranteed at 100% regardless of any
    tag, CLAUDE.md rule 5). P2/P3/P4/P5 are the real, allocation-affecting
    tags — P2/P3/P4 for Normal vendors, P5 for Inactive vendors — extensible
    to more later, always appended below the existing tags with a lower
    ceiling (P2 >= P3 >= P4 >= P5 >= ...).

    P5 is Inactive's own real bucket — no longer a mirrored display-only
    tag the way the old "EXIT" tag was (renamed away entirely 2026-07-24;
    that member and its fold-into-last-bucket allocator behavior no longer
    exist). P5 is also the lowest-priority bucket: cut first in a
    shortfall (docs/14 "Allocation logic"), its own lower ceiling/floor.
    Bucket definitions themselves (which tags exist, their ceiling/floor/
    rotation order) live in the PriorityBucket DB table
    (backend/models/new_model_2/allocator.py), not hardcoded here — this
    enum only needs a member to exist so Vendor.priority_tag can store it.
    """

    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"
    P4 = "P4"
    P5 = "P5"

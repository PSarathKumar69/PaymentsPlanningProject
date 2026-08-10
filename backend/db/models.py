"""SQLAlchemy ORM models — see docs/12-database.md for the table-shape spec.

SQLite for now, accessed only through the ORM, to keep a Postgres migration
path open (no raw/SQLite-specific SQL here).
"""
from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    LargeBinary,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from backend.shared.enums import PaymentStatus, VendorCategory

MONEY = Numeric(18, 4)


class Base(DeclarativeBase):
    pass


class Vendor(Base):
    """Current-state master data — one row per vendor, Excel-backed."""

    __tablename__ = "vendors"
    # Real uniqueness key is (entity, erp_code), not erp_code alone
    # (entity-code-collision fix): the real master sheet has 28 confirmed
    # cases of two different legal entities (ARS/FP) sharing one bare
    # numeric erp_code, disambiguated by the sheet's own "Unique" column
    # (e.g. "ARSV00149" vs "FPV00149"). A bare-erp_code unique constraint
    # made the second entity's vendor impossible to store at all —
    # load_excel.py's first-row-wins duplicate handling was silently
    # dropping its entire ledger history, permanently, on every ingestion.
    # See load_excel.py's upsert lookup and find_duplicate_erp_codes()
    # (column_mapping.py), both rekeyed to match.
    __table_args__ = (UniqueConstraint("entity", "erp_code", name="uq_vendor_entity_erp_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    erp_code: Mapped[str] = mapped_column(String, index=True)
    entity: Mapped[str] = mapped_column(String)
    vendor_name: Mapped[str] = mapped_column(String)

    # Soft-deactivation (data-pipeline-upload Bug #1 fix): a replace-upload
    # whose sheet no longer lists this erp_code sets is_active=False instead
    # of deleting the row — PlanAllocation/Payment/AuditLog rows from before
    # this vendor was removed have a plain FK to vendors.id with no
    # ON DELETE, and SQLite FK enforcement is off by default here (no PRAGMA
    # foreign_keys=ON in db/session.py), so a hard delete would silently
    # orphan that historical data rather than error. Every "list active
    # vendors" query must filter is_active.isnot(False) (NOT ==True) —
    # db/session.py's _add_missing_columns ALTER TABLE never carries a
    # DEFAULT, so this column reads NULL on every vendor row that predates
    # this migration, and NULL must mean "active" (it always was), not get
    # silently excluded.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    removed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Current outstanding balance carried forward (= latest month's closing
    # balance in the Excel) — the starting point for the next cycle.
    opening_balance: Mapped[float] = mapped_column(MONEY)

    # Persistent, Finance-set tags — Excel-backed, follow the write-back rule.
    assigned_week: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Category-configuration task: was `Enum(VendorCategory)`, hardcapping
    # every vendor to the fixed must_pay/commitment/normal/inactive Python
    # enum — a Configuration-added category (e.g. "Contractor") could never
    # actually be stored. Plain String now, same fix already applied to
    # priority_tag below — validity is checked live against the two fixed
    # categories plus whatever's currently in the priority_buckets table
    # (vendor_edits.py::_validate), not a fixed Python enum. NOTE: existing
    # SQLite rows physically stored the enum MEMBER NAME ("NORMAL",
    # "MUST_PAY", ...), not its .value ("normal", "must_pay", ...) — every
    # other reader (API schemas, frontend, ingestion) already expects the
    # lowercase .value form, so db/session.py normalizes old rows once at
    # startup (LOWER()) rather than every reader guessing at both forms.
    category: Mapped[str] = mapped_column(String, default=VendorCategory.NORMAL.value, server_default="normal")
    # Only meaningful for COMMITMENT vendors (docs/04-model2-min-funds-advisory.md).
    # Defaults to 1 (full outstanding due this cycle) until Finance sets it via the UI.
    commitment_months: Mapped[int | None] = mapped_column(Integer, nullable=True, default=1, server_default="1")

    # DB-only, overwritten fresh at each regeneration, never historized or
    # written back to Excel (docs/06-weekly-planning-regeneration.md).
    reconsider: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # Finance's manual override — a permanent, per-vendor, whole-month
    # decision (docs/06 fix): once set, it stays exactly as typed across
    # every regeneration for the rest of the month, regardless of payment
    # status or funds changes, until Finance explicitly types a new value or
    # clears it. This is now the single source of truth for "what should
    # this vendor actually be paid this cycle" — PlanAllocation.override_amount
    # is only a per-row historical snapshot of this value at creation time.
    override_amount: Mapped[float | None] = mapped_column(MONEY, nullable=True)

    # Finance's own free-form "planned distribution across W1-W5" — sticky,
    # per-vendor, whole-month value that mirrors override_amount's exact
    # pattern (docs/06 fix, this task): once Finance edits any week via
    # PATCH /plan-allocations/{id}/week-distribution, this is the live
    # "current" distribution the UI reads going forward, and it carries
    # forward unchanged across every regeneration for the rest of the month
    # until Finance edits it again. PlanAllocation.week_distribution_plan is
    # now only a per-row historical snapshot of this value taken at that
    # row's creation time — exactly the same relationship
    # PlanAllocation.override_amount already has to this field's sibling.
    week_distribution_plan: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # "Budget" for the Vendor Payment Table — a stable snapshot of the
    # then-current effective amount (override if set, else the latest
    # plan_run allocation — New Model 2's today, the sole remaining model),
    # taken only when Finance clicks "Finalize Plan"
    # (backend/shared/payment_logging.py::finalize_plan()). Deliberately NOT
    # a live-following figure: further overrides/regenerations after a
    # Finalize click do not change this until Finance finalizes again — so
    # Finance can generate/override across several sessions (daily, weekly)
    # and only "publish" a stable number to pay against when ready. Same
    # whole-month, not-historized reset as override_amount/reconsider above.
    finalized_budget_amount: Mapped[float | None] = mapped_column(MONEY, nullable=True)

    # Monthly payment status — computed by backend/shared/payment_logging.py
    # from paid_so_far_this_month, not inferred ad hoc (docs/06). Both reset
    # at month start; not historized (month-end rollover starts fresh).
    payment_status: Mapped[PaymentStatus] = mapped_column(
        Enum(PaymentStatus), default=PaymentStatus.NOT_PAID, server_default=PaymentStatus.NOT_PAID.name
    )
    paid_so_far_this_month: Mapped[float] = mapped_column(MONEY, default=0, server_default="0")

    # Computed by backend/shared/scoring.py (relocated from the old Model 1
    # weighted-scoring package, still live infra for New Model 2) — not
    # populated by ingestion.
    score: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    current_aging_bucket: Mapped[str | None] = mapped_column(String, nullable=True)

    # New Model 2's Finance-assigned priority tag (docs/14-new-model-2.md) —
    # P2/P3/P4, only meaningful for NORMAL vendors. Deliberately its own
    # field, not old Model 3's bucket/PriorityBucket concept
    # (backend/models/model3_priority_bucket/bucketer.py), which is
    # score-derived and never persisted on Vendor at all — this one is a
    # direct Finance tag instead.
    #
    # Bug fix (this task): was `Enum(VendorPriorityTag)`, hardcapping every
    # vendor's tag to the fixed P0-P5 Python enum even though
    # priority_buckets (Configuration's own bucket table, allocator.py's
    # _seed_and_read_buckets) is meant to be extensible beyond P5 — a
    # Configuration-added P6+ bucket could never actually be assigned to a
    # vendor (see priority_bucket_edits.py::add_bucket's own docstring,
    # which already flagged exactly this). Plain String now — validity is
    # checked live against VendorPriorityTag's P0/P1 plus whatever's
    # currently in the priority_buckets table (vendor_edits.py::_validate),
    # not a fixed Python enum. SQLite's actual column was already a bare
    # VARCHAR with no CHECK constraint (confirmed via sqlite_master), so
    # this is a pure application-layer relaxation — no DB migration needed.
    priority_tag: Mapped[str | None] = mapped_column(String, nullable=True)

    monthly_ledger: Mapped[list["MonthlyLedger"]] = relationship(back_populates="vendor")
    payments: Mapped[list["Payment"]] = relationship(back_populates="vendor")
    plan_allocations: Mapped[list["PlanAllocation"]] = relationship(back_populates="vendor")


class MonthlyLedger(Base):
    """One row per vendor per month — normalizes the Excel's wide per-month columns."""

    __tablename__ = "monthly_ledger"
    __table_args__ = (UniqueConstraint("vendor_id", "month", name="uq_monthly_ledger_vendor_month"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    vendor_id: Mapped[int] = mapped_column(ForeignKey("vendors.id"), index=True)
    month: Mapped[date] = mapped_column(Date)
    payable: Mapped[float] = mapped_column(MONEY)
    payment: Mapped[float] = mapped_column(MONEY)
    opening_balance: Mapped[float] = mapped_column(MONEY)
    closing_balance: Mapped[float] = mapped_column(MONEY)

    vendor: Mapped["Vendor"] = relationship(back_populates="monthly_ledger")


class Payment(Base):
    """Daily manual payment log — single source of truth for weekly totals.

    Not populated by this ingestion: the source Excel has no day-level data,
    only monthly aggregates plus one current-cycle weekly breakdown. This
    table fills in going forward via Finance's daily manual entry.
    """

    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    vendor_id: Mapped[int] = mapped_column(ForeignKey("vendors.id"), index=True)
    payment_date: Mapped[date] = mapped_column(Date)
    amount: Mapped[float] = mapped_column(MONEY)
    week: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Free-text note Finance can attach when logging a payment (vendor detail
    # modal's Pay flow) — purely descriptive, never read by any model/
    # allocation math (CLAUDE.md rule 3).
    note: Mapped[str | None] = mapped_column(String, nullable=True)

    vendor: Mapped["Vendor"] = relationship(back_populates="payments")


class PlanRun(Base):
    """One record per plan generation/regeneration event."""

    __tablename__ = "plan_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    month: Mapped[date] = mapped_column(Date)
    model_used: Mapped[str] = mapped_column(String)
    funds_figure: Mapped[float | None] = mapped_column(MONEY, nullable=True)
    # Analytics funds-trend task: New Model 2's own Minimum Funds Required
    # total (backend/shared/min_funds_v2.py), captured at the same moment as
    # funds_figure (new_model_2.py's Generate Plan endpoint) so the two live
    # on the same row for the trend chart. NULL on every PlanRun that
    # predates this column, including old rows — never backfilled, per
    # CLAUDE.md rule "no invented/guessed history" (backend/analytics/
    # calculations.py's funds trend only reads rows where both this and
    # funds_figure are populated).
    min_funds_required: Mapped[float | None] = mapped_column(MONEY, nullable=True)
    # Funds Left card fix: allocator.py's own leftover_remaining, persisted
    # the same way as min_funds_required above so a Planning-tab remount can
    # restore the real post-allocation figure instead of approximating it
    # from funds_figure minus overrides. NULL on every row that predates
    # this column — not backfilled, same precedent as min_funds_required.
    leftover_remaining: Mapped[float | None] = mapped_column(MONEY, nullable=True)

    allocations: Mapped[list["PlanAllocation"]] = relationship(back_populates="plan_run")


class PlanAllocation(Base):
    """Per plan_run, per vendor (per assigned week): the code-computed allocation."""

    __tablename__ = "plan_allocations"

    id: Mapped[int] = mapped_column(primary_key=True)
    plan_run_id: Mapped[int] = mapped_column(ForeignKey("plan_runs.id"), index=True)
    vendor_id: Mapped[int] = mapped_column(ForeignKey("vendors.id"), index=True)
    assigned_week: Mapped[int] = mapped_column(Integer)
    within_week_order: Mapped[int | None] = mapped_column(Integer, nullable=True)
    allocated_amount: Mapped[float] = mapped_column(MONEY)

    # Per-row snapshot of vendor.override_amount at the moment this row was
    # created (docs: New Model 2, the sole remaining model, plus this
    # override now drive payment_status/cycle_allocation — see
    # payment_logging.py). NULL means "no override was set on
    # the vendor at that moment, use allocated_amount as-is". No longer the
    # live source of truth and no longer "never carried forward" — that was
    # the bug (docs/06 fix): Vendor.override_amount is now the sticky,
    # per-vendor, whole-month source of truth, and every fresh
    # PlanAllocation row is stamped with whatever that currently is at
    # creation time. This column stays purely for historical/audit accuracy
    # on old plan_runs (so an old row's snapshot doesn't retroactively change
    # if Finance later edits the override), never read for "what's the
    # override right now" — that's always vendor.override_amount.
    override_amount: Mapped[float | None] = mapped_column(MONEY, nullable=True)

    # Finance's own free-form "planned distribution across W1-W5" notes —
    # purely additive/display-only (never enforced: no validation that it
    # sums to allocated_amount/effective_amount, no ledger-integrity ceiling,
    # never read by payment_status/override math/Models 1-3). Sparse
    # {week_number_str: amount} mapping rather than a fixed
    # week1_amount..week5_amount column set — a month can have 4 or 5 weeks
    # (calendar_utils.py::weeks_in_month()), so a fixed 5-column schema would
    # hardcode "always 5" the same way CLAUDE.md already forbids. NULL until
    # a fresh plan seeds the default (planner.py/regeneration.py).
    week_distribution_plan: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # The required_amount/denominator allocated_amount was computed against,
    # frozen at the moment this row was created (this task's fix). A
    # vendor's live required_amount can change between generations (a
    # payment lands, a tranche clears), so recomputing "today's" required
    # amount for an OLD plan_run would show the wrong historical Suggested
    # %/Override % — this column is what lets the frontend show the
    # percentage exactly as it was at generation time, for every plan_run,
    # not just the still-cached latest one. NULL only for rows
    # backend/ingestion/load_excel.py seeds straight from the Excel's raw
    # W1-W5 columns — no model/required_amount concept is involved there at
    # all, and that plan_run (model_used="ingestion") never surfaces in any
    # model's Plan-N history view anyway.
    required_amount_snapshot: Mapped[float | None] = mapped_column(MONEY, nullable=True)

    plan_run: Mapped["PlanRun"] = relationship(back_populates="allocations")
    vendor: Mapped["Vendor"] = relationship(back_populates="plan_allocations")


class AuditLog(Base):
    """Append-only log of every vendor-data/config override. Never update/delete rows."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime)
    vendor_id: Mapped[int | None] = mapped_column(ForeignKey("vendors.id"), nullable=True)
    field_name: Mapped[str] = mapped_column(String)
    old_value: Mapped[str | None] = mapped_column(String, nullable=True)
    new_value: Mapped[str | None] = mapped_column(String, nullable=True)
    source: Mapped[str] = mapped_column(String)
    changed_by: Mapped[str | None] = mapped_column(String, nullable=True)


class VendorExtraField(Base):
    """Generic passthrough for an Excel column the mapping layer doesn't
    recognize (docs/11 upload task) — e.g. a new "Status" column Finance
    adds to a future upload. Stored, shown, and editable, but per CLAUDE.md
    rule 3 never read by any model/deterministic calculation — only
    load_excel.py (populates it) and the master-data grid endpoints (read/
    edit it) ever touch this table. value is always text; the UI widget
    (toggle/dropdown/free text) is derived at read time from the current
    spread of distinct values, never stored (a column's own natural
    "shape" can shift as Finance edits it, so freezing a widget-type
    column here would just go stale)."""

    __tablename__ = "vendor_extra_fields"
    __table_args__ = (UniqueConstraint("vendor_id", "column_name", name="uq_vendor_extra_field_vendor_column"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    vendor_id: Mapped[int] = mapped_column(ForeignKey("vendors.id"), index=True)
    column_name: Mapped[str] = mapped_column(String)  # literal Excel header text
    value: Mapped[str | None] = mapped_column(String, nullable=True)


class ColumnMappingOverride(Base):
    """AI-resolved column-header mapping (data-pipeline AI-mapping task) —
    one row per logical field (erp_code, entity, ..., priority_tag; see
    column_mapping.ALL_MAPPABLE_FIELDS). Lets a renamed header resolve
    deterministically on every future upload without re-invoking the AI:
    once set, build_sheet_map()/load_excel.py consult this before falling
    back to the field's fixed default header text. A later resolution for
    the same field replaces the earlier one outright — this is "what header
    means what right now," not a history (the audit_log is the history)."""

    __tablename__ = "column_mapping_overrides"

    id: Mapped[int] = mapped_column(primary_key=True)
    logical_field: Mapped[str] = mapped_column(String, unique=True, index=True)
    header_text: Mapped[str] = mapped_column(String)
    set_by: Mapped[str] = mapped_column(String)  # "ai" — only source today
    set_at: Mapped[datetime] = mapped_column(DateTime)


class PriorityBucket(Base):
    """New Model 2's priority-bucket definitions (docs/14, Task 2 Part C) —
    which buckets exist, their ceiling/floor percentages, and rotation
    (cutting) order. Replaces allocator.py's old hardcoded BUCKET_ORDER
    list + Config-table-only ceiling values: adding, editing, or removing a
    bucket is now a row change in this table, not a code change.
    Seeded with today's P2/P3/P4/P5 on first read — see
    backend/models/new_model_2/allocator.py::_seed_and_read_buckets().

    ceiling_pct/floor_pct are stored as fractions (0.95, not 95) — this
    table has no separate Finance-facing-percentage-vs-stored-fraction
    split the way the generic Config table does, since every reader/writer
    of this table is this bucket-CRUD feature itself.
    """

    __tablename__ = "priority_buckets"

    id: Mapped[int] = mapped_column(primary_key=True)
    bucket_key: Mapped[str] = mapped_column(String, unique=True, index=True)
    display_label: Mapped[str] = mapped_column(String)
    # Category-configuration task: the Finance-facing category name this
    # row belongs to (e.g. "Normal", "Inactive", or a custom name Finance
    # typed) — several rows can share one category_name (P2/P3/P4 all say
    # "Normal" today, each its own ceiling/floor tier per docs/14), so this
    # is NOT unique. Nullable only so `_add_missing_columns()`'s bare
    # ALTER TABLE ADD COLUMN can apply to a pre-existing table; every row
    # is immediately backfilled by `_seed_and_read_buckets()`.
    category_name: Mapped[str | None] = mapped_column(String, nullable=True)
    ceiling_pct: Mapped[float] = mapped_column(Numeric(6, 4))
    floor_pct: Mapped[float] = mapped_column(Numeric(6, 4))
    # Cutting/rotation order, lowest-position = highest priority (funded
    # first, cut last) — e.g. P2=0, P3=1, P4=2, P5=3. Unique so ordering is
    # always well-defined.
    rotation_position: Mapped[int] = mapped_column(Integer, unique=True)
    # Guaranteed-funding tier identity (pinned-role task, 2026-08-07) —
    # "must_pay"/"commitment" (VendorCategory's own .value strings — a
    # guaranteed row's pinned_role IS its category value, no separate
    # vocabulary needed) or NULL for an ordinary, fully reorderable/
    # removable bucket. Decouples allocator.py's guaranteed-tier detection
    # from this row's bucket_key/display text, which Finance can now freely
    # rename — see allocator.py's _seed_and_read_buckets()/
    # priority_bucket_edits.py's remove_bucket(). Nullable so
    # `_add_missing_columns()`'s bare ALTER TABLE ADD COLUMN applies to a
    # pre-existing table; backfilled once by
    # `db/session.py::_backfill_pinned_roles()`.
    pinned_role: Mapped[str | None] = mapped_column(String, nullable=True)


class Config(Base):
    """System-level settings: Model 1 weights, Model 3 bucket thresholds.

    Not populated by this ingestion — no values are decided yet, and the
    models that read these don't exist yet either.
    """

    __tablename__ = "config"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String, unique=True, index=True)
    value: Mapped[str] = mapped_column(String)
    description: Mapped[str | None] = mapped_column(String, nullable=True)


class MasterExcelBlob(Base):
    """Durable Postgres copy of the master Excel file's raw bytes.

    Vercel Functions have a read-only filesystem outside /tmp, and /tmp
    itself does not survive between separate container instances — so the
    literal on-disk file at load_excel.EXCEL_PATH can vanish between the
    request that uploads it and a later, different container's request
    that needs to read it back (the master-data grid, exports — see
    grid.py/new_model_2.py, which open EXCEL_PATH directly, unmodified).

    This one-row table is that durable source of truth once DATABASE_URL
    points at Postgres: load_excel.py rehydrates EXCEL_PATH's on-disk copy
    from here once per cold start, and upload.py's commit_upload()/
    revert_upload() write back here (current + the one backup slot) in the
    SAME transaction as everything else they commit. Exactly one row
    (id=1) — same "exactly one backup slot" design upload.py already uses
    for the file-level backup.
    """

    __tablename__ = "master_excel_blob"

    id: Mapped[int] = mapped_column(primary_key=True)
    content: Mapped[bytes] = mapped_column(LargeBinary)
    backup_content: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime)

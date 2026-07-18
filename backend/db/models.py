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

    id: Mapped[int] = mapped_column(primary_key=True)
    erp_code: Mapped[str] = mapped_column(String, unique=True, index=True)
    entity: Mapped[str] = mapped_column(String)
    vendor_name: Mapped[str] = mapped_column(String)

    # Current outstanding balance carried forward (= latest month's closing
    # balance in the Excel) — the starting point for the next cycle.
    opening_balance: Mapped[float] = mapped_column(MONEY)

    # Persistent, Finance-set tags — Excel-backed, follow the write-back rule.
    assigned_week: Mapped[int | None] = mapped_column(Integer, nullable=True)
    category: Mapped[VendorCategory] = mapped_column(
        Enum(VendorCategory), default=VendorCategory.NORMAL, server_default=VendorCategory.NORMAL.name
    )
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

    # "Budget" for the Vendor Payment Table — a stable snapshot of Model 1's
    # then-current effective amount (override if set, else Model 1's latest
    # plan_run allocation), taken only when Finance clicks "Finalize Plan"
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

    # Computed by Model 1 / Model 3 — not populated by ingestion.
    score: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    current_aging_bucket: Mapped[str | None] = mapped_column(String, nullable=True)

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

    vendor: Mapped["Vendor"] = relationship(back_populates="payments")


class PlanRun(Base):
    """One record per plan generation/regeneration event."""

    __tablename__ = "plan_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    month: Mapped[date] = mapped_column(Date)
    model_used: Mapped[str] = mapped_column(String)
    funds_figure: Mapped[float | None] = mapped_column(MONEY, nullable=True)

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
    # created (docs: Model 1 is now the sole driver of payment_status/
    # cycle_allocation — see payment_logging.py — so this is only
    # meaningfully used on Model 1's rows in practice, even though the
    # column exists on every model's). NULL means "no override was set on
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

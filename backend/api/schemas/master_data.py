"""Request/response schemas for the master-data router (data-pipeline-upload task)."""
from typing import Any

from pydantic import BaseModel


class IngestionReportOut(BaseModel):
    # load()'s own report shape, unchanged/reused (docs/07) — same keys
    # every other ingestion consumer already reads.
    vendor_count: int
    ledger_row_count: int
    allocation_count: int
    ledger_mismatches: list[tuple[str, float, float]]
    week_total_mismatches: list[tuple[str, float, float]]
    data_quality_notes: list[str]
    erp_codes_in_file: list[str]
    unmapped_columns: list[str]


class MasterDataDiffSummary(BaseModel):
    """The before/after diff computed internally by commit_upload() (no
    longer shown to Finance as a confirm-before-writing step — Sarath's
    course-correction — but still layered onto the response for the
    one-line post-upload summary, and it's what the per-vendor audit_log
    entries are built from)."""

    new_vendor_count: int
    new_vendors: list[str]
    existing_vendor_count: int
    missing_vendor_count: int
    missing_vendors: list[str]
    vendors_with_changed_ledger_count: int
    vendors_with_changed_ledger: list[str]
    changed_fields_by_vendor: dict[str, list[str]]


class MasterDataCommitResponse(IngestionReportOut, MasterDataDiffSummary):
    vendors_changed: int
    # Pre-existing gap fixed (P1 demo-readiness task): commit_upload() has
    # always returned this key, but it was never declared here — FastAPI's
    # response_model silently drops any key a Pydantic model doesn't
    # declare, so this banner (AI column-mapping messages, and now the new
    # sheet-start-month sanity warning) never actually reached the frontend
    # through the real HTTP endpoint, only when tests called commit_upload()
    # directly in-process.
    ai_column_mapping_messages: list[str]
    # Merge-rollover-into-upload task: whether this upload's own re-ingestion
    # landed a genuinely new month and, only then, reset every vendor's
    # payment-cycle state — see backend/ingestion/upload.py::commit_upload()'s
    # own docstring. False/0 for the ordinary same-month-correction and
    # first-upload-ever cases; never an error either way.
    cycle_reset: bool
    vendors_reset: int


class MasterDataRevertResponse(IngestionReportOut):
    """Revert has no upload diff to show — just load()'s own report plus
    the accepted-gap warning string."""

    warning: str


class ExtraFieldOut(BaseModel):
    column_name: str
    widget: str  # "toggle" | "dropdown" | "text" — derived, never stored (decision #5)
    options: list[str] | None  # populated for dropdown only
    values_by_vendor_id: dict[int, str | None]


class ExtraFieldPatchRequest(BaseModel):
    column_name: str
    new_value: str | None
    excel_path: str | None = None  # override for tests; production calls omit it


class ExtraFieldPatchResponse(BaseModel):
    old_value: str | None
    new_value: str | None


class GridColumnOut(BaseModel):
    header: str
    kind: str
    editable: bool
    month: str | None = None
    week_number: int | None = None
    field: str | None = None
    column_name: str | None = None


class GridVendorRowOut(BaseModel):
    vendor_id: int
    erp_code: str
    values: dict[str, Any]


class DuplicateDroppedRowOut(BaseModel):
    """A row from the source sheet that shared its ERP code with another
    row and was NOT loaded (last-row-wins, load_excel.py) — go-live "show
    every row" task. Identity fields only; not a full duplicate vendor
    record."""

    erp_code: str
    vendor_name: Any
    row: int


class MasterGridOut(BaseModel):
    columns: list[GridColumnOut]
    vendors: list[GridVendorRowOut]
    extra_field_widgets: dict[str, dict[str, Any]]
    # "No data yet" fix (Main-tab task) — False before any vendor has ever
    # been ingested (no upload yet, or the master file/DB got wiped), or if
    # the master file itself has gone missing since. columns/vendors/
    # extra_field_widgets are all empty whenever this is False; the
    # frontend uses this (not "arrays happen to be empty") to decide
    # whether to show the grid or a placeholder.
    has_data: bool = True
    # Go-live "show every row" task: every ERP code the live grid's own
    # vendor row was duplicated under in the source sheet (that vendor is
    # the "winner" — see load_excel.py's last-row-wins upsert) — frontend
    # highlights those main-grid rows. duplicate_dropped_rows is every row
    # that did NOT win, so Finance can trace it back to their sheet.
    duplicate_erp_codes: list[str] = []
    duplicate_dropped_rows: list[DuplicateDroppedRowOut] = []


class SheetStartMonthOut(BaseModel):
    """GET /master-data/sheet-start-month (Main-tab upload-confirm task) —
    the sheet's currently configured start month, "YYYY-MM", so the upload
    confirm modal can pre-fill its "auto-detected" Sheet start month field
    instead of leaving it blank."""

    sheet_start_month: str

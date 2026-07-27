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


class MasterGridOut(BaseModel):
    columns: list[GridColumnOut]
    vendors: list[GridVendorRowOut]
    extra_field_widgets: dict[str, dict[str, Any]]

"""Finance-facing Excel upload flow (data-pipeline-upload task, docs/07 +
docs/11). Sits directly on top of load_excel.py's existing upsert-by-
erp_code engine — this module never re-parses the sheet itself, it only
computes a before/after diff around one real load() call and manages the
backup/replace/revert file mechanics around it.

One upload button, one engine (decision #1): there is no separate
"master"/"update" mode here, and no wipe-everything reset — every upload,
first one or hundredth, replaces the current data via commit_upload() below.

REVISED (Sarath, course-correcting the original preview-then-confirm
design): no preview/confirmation step — Finance picks a file, clicks one
button, commit_upload() runs backup -> replace -> load() -> audit log in one
shot. The before/after diff computation below is KEPT (it still feeds the
per-vendor audit_log summary — a separate governance requirement, docs/11 /
CLAUDE.md rule 6) — only the "show Finance the diff and make them confirm
before anything is written" gate is gone.
"""
import os
import shutil
import tempfile
from datetime import date, datetime

import openpyxl

from backend.db.models import AuditLog, MonthlyLedger, Vendor, VendorExtraField
from backend.db.session import SessionLocal
from backend.ingestion import ai_column_mapper, column_mapping_store
from backend.ingestion.load_excel import EXCEL_PATH, load
from backend.shared.constants import MONEY_EPSILON
from backend.shared.enums import ChangeSource

# Exactly one backup slot (decision #3) — overwritten every commit, never
# timestamped/versioned. Same directory as the master file so the later
# os.replace in _atomic_replace is same-filesystem and atomic. Keeps the
# real .xlsx extension (".backup" inserted before it, not appended after)
# so the backup stays directly openable by openpyxl/Excel itself, not just
# by this module's own raw-byte copy — worth keeping valid on its own.
_root, _ext = os.path.splitext(EXCEL_PATH)
MASTER_EXCEL_BACKUP_PATH = f"{_root}.backup{_ext}"


def _atomic_replace(source_path, dest_path):
    """Stage-to-temp-then-os.replace — the same atomic-write pattern
    vendor_edits.py already uses for its own Excel writes, reused here
    rather than inventing a second one (task's own explicit instruction)."""
    directory = os.path.dirname(os.path.abspath(dest_path)) or "."
    fd, tmp_path = tempfile.mkstemp(suffix=".xlsx", dir=directory)
    os.close(fd)
    try:
        shutil.copyfile(source_path, tmp_path)
        os.replace(tmp_path, dest_path)
    except Exception:
        os.remove(tmp_path)
        raise


def _snapshot_vendor_state(session):
    """Before-state for every vendor currently in the DB, keyed by erp_code
    (not id — a brand-new vendor in the uploaded file has no id yet at
    snapshot time). Used only for the diff below; load() itself is the one
    and only thing that actually mutates this state."""
    snapshot = {}
    for vendor in session.query(Vendor).all():
        ledger = {
            row.month.isoformat(): (float(row.payable), float(row.payment))
            for row in session.query(MonthlyLedger).filter_by(vendor_id=vendor.id)
        }
        extra_fields = {
            row.column_name: row.value for row in session.query(VendorExtraField).filter_by(vendor_id=vendor.id)
        }
        snapshot[vendor.erp_code] = {
            "vendor_id": vendor.id,
            "opening_balance": float(vendor.opening_balance),
            "category": vendor.category.value,
            "commitment_months": vendor.commitment_months,
            "assigned_week": vendor.assigned_week,
            "ledger": ledger,
            "extra_fields": extra_fields,
        }
    return snapshot


def _changed_fields(erp_code, before, vendor, session):
    """Per-vendor list of short, human-readable change descriptions — NOT a
    full cell-by-cell log (task's own instruction: excessive noise at 83
    vendors x 14 months). Ledger changes are reported per affected month
    (e.g. "Mar-26 payment"), not as one generic "ledger changed" blob."""
    changed = []
    if abs(float(vendor.opening_balance) - before["opening_balance"]) > MONEY_EPSILON:
        changed.append("Opening balance")

    after_ledger = {
        row.month.isoformat(): (float(row.payable), float(row.payment))
        for row in session.query(MonthlyLedger).filter_by(vendor_id=vendor.id)
    }
    for month_iso, (after_payable, after_payment) in after_ledger.items():
        before_val = before["ledger"].get(month_iso)
        if before_val is None:
            continue  # a month that didn't exist before isn't a "change" to an existing figure
        before_payable, before_payment = before_val
        month_label = date.fromisoformat(month_iso).strftime("%b-%y")
        if abs(after_payable - before_payable) > MONEY_EPSILON:
            changed.append(f"{month_label} payable")
        if abs(after_payment - before_payment) > MONEY_EPSILON:
            changed.append(f"{month_label} payment")

    if vendor.category.value != before["category"]:
        changed.append("Category changed")
    if vendor.commitment_months != before["commitment_months"]:
        changed.append("Commitment months changed")
    if vendor.assigned_week != before["assigned_week"]:
        changed.append("Assigned week changed")

    after_extra = {
        row.column_name: row.value for row in session.query(VendorExtraField).filter_by(vendor_id=vendor.id)
    }
    for column_name, after_value in after_extra.items():
        if before["extra_fields"].get(column_name) != after_value:
            changed.append(f"{column_name} changed")

    return changed


def _diff_report(before, session, load_report):
    """Shared by preview_upload() and commit_upload() — the one place this
    diff is computed, so preview is guaranteed to reflect exactly what
    committing will do (task's own explicit instruction)."""
    erp_codes_in_file = set(load_report["erp_codes_in_file"])
    before_erp_codes = set(before)
    after_vendors = {v.erp_code: v for v in session.query(Vendor).all()}

    new_vendors = sorted(erp_codes_in_file - before_erp_codes)
    existing_vendors_in_file = sorted(erp_codes_in_file & before_erp_codes)
    missing_vendors = sorted(before_erp_codes - erp_codes_in_file)

    changed_fields_by_vendor = {}
    vendors_with_changed_ledger = []
    for erp_code in existing_vendors_in_file:
        vendor = after_vendors[erp_code]
        changed = _changed_fields(erp_code, before[erp_code], vendor, session)
        if changed:
            changed_fields_by_vendor[erp_code] = changed
        if any(" payable" in c or " payment" in c for c in changed):
            vendors_with_changed_ledger.append(erp_code)

    return {
        "new_vendor_count": len(new_vendors),
        "new_vendors": new_vendors,
        "existing_vendor_count": len(existing_vendors_in_file),
        "missing_vendor_count": len(missing_vendors),
        "missing_vendors": missing_vendors,
        "vendors_with_changed_ledger_count": len(vendors_with_changed_ledger),
        "vendors_with_changed_ledger": vendors_with_changed_ledger,
        "changed_fields_by_vendor": changed_fields_by_vendor,
    }


def commit_upload(uploaded_excel_path, excel_path=None, backup_path=None):
    """Part A — the entire upload flow, one call: Finance picks a file, this
    runs backup -> atomic replace -> load() -> audit log, in that order,
    with nothing shown or confirmed in between (no preview step — Sarath's
    explicit course-correction on the original two-step design).

    excel_path/backup_path: override the real master/backup paths — same
    convention vendor_edits.py's own excel_path=None param already uses, so
    tests can point this at a tmp_path copy instead of the real master file.
    Production callers omit both and get the real EXCEL_PATH/
    MASTER_EXCEL_BACKUP_PATH.

    Order (task's explicit spec, and it matters): (a) back up the CURRENT
    master to the one fixed backup slot, (b) atomically replace the master
    file with the uploaded one, (c) run load() for real against the new
    master, in the same transaction as Part B's VendorExtraField upserts
    (already true — load() upserts those itself), (d) one audit_log entry
    per vendor that actually changed.

    RESIDUAL RISK — flagged, not solved here (same house style as
    vendor_edits.py's own documented residual-risk window): steps (a)/(b)
    replace the real master file before step (c)'s DB commit happens, because
    load() must read the NEW file to produce the report/audit entries at
    all. If the process dies or the DB commit fails between (b) and (c)'s
    commit, the master Excel and the DB will disagree (Excel already
    replaced, DB not yet updated) with nothing to reconcile them
    automatically. Revert (below) is the recovery path for this, not a
    silent auto-fix.
    """
    excel_path = excel_path or EXCEL_PATH
    backup_path = backup_path or MASTER_EXCEL_BACKUP_PATH
    shutil.copyfile(excel_path, backup_path)  # (a) — exactly one slot, overwritten every time
    _atomic_replace(uploaded_excel_path, excel_path)  # (b)

    session = SessionLocal()
    try:
        before = _snapshot_vendor_state(session)

        # AI-assisted column mapping (data-pipeline AI-mapping task,
        # supersedes Part B of claude_code_prompt_priority_column_and_ai_mapping.md):
        # resolves any renamed/missing header — deterministically from a
        # prior upload's persisted mapping if already learned, via Gemini
        # otherwise — BEFORE load() runs, so load() either gets a complete
        # mapping or this raises a clear MissingRequiredColumnsError naming
        # the field, instead of load()'s own opaque ValueError deep inside
        # column_mapping.py. Opens the workbook once here purely to read
        # headers/samples; load() below opens it again for the real parse —
        # a second cheap read, not a second copy of any parsing logic.
        wb_for_mapping = openpyxl.load_workbook(excel_path, data_only=True)
        mapping_result = ai_column_mapper.resolve_column_mapping(session, wb_for_mapping.active)

        load_report = load(
            excel_path=excel_path, session=session, header_overrides=mapping_result["header_overrides"]
        )  # (c) — session supplied, load() doesn't commit
        diff = _diff_report(before, session, load_report)

        for entry in mapping_result["audit_entries"]:
            session.add(
                AuditLog(
                    timestamp=datetime.now(),
                    vendor_id=None,  # a mapping decision isn't per-vendor
                    field_name=entry["field_name"],
                    old_value=None,
                    new_value=entry["new_value"],
                    source=ChangeSource.AI_COLUMN_MAPPING.value,
                )
            )

        for erp_code, changed in diff["changed_fields_by_vendor"].items():
            vendor_id = before[erp_code]["vendor_id"]
            session.add(
                AuditLog(
                    timestamp=datetime.now(),
                    vendor_id=vendor_id,
                    field_name="excel_upload",
                    old_value=None,
                    new_value=", ".join(changed),
                    source=ChangeSource.EXCEL_UPLOAD.value,
                )
            )
        for erp_code in diff["new_vendors"]:
            session.add(
                AuditLog(
                    timestamp=datetime.now(),
                    vendor_id=session.query(Vendor).filter_by(erp_code=erp_code).one().id,
                    field_name="excel_upload",
                    old_value=None,
                    new_value="New vendor added via upload",
                    source=ChangeSource.EXCEL_UPLOAD.value,
                )
            )
        # Bug #1 fix: a vendor absent from this sheet is soft-deactivated by
        # load() (models.py's Vendor.is_active), not deleted — audited here
        # same as any other override (docs/11 / CLAUDE.md rule 6). Only the
        # erp_codes load() actually flipped this run, not diff["missing_vendors"]
        # (that set also includes vendors already deactivated by a PRIOR
        # upload, which would otherwise get a duplicate "removed" entry every
        # time the sheet is re-uploaded without them).
        for erp_code in load_report["deactivated_erp_codes"]:
            session.add(
                AuditLog(
                    timestamp=datetime.now(),
                    vendor_id=before[erp_code]["vendor_id"],
                    field_name="excel_upload",
                    old_value=None,
                    new_value="Vendor removed via upload (soft-deactivated, not deleted)",
                    source=ChangeSource.EXCEL_UPLOAD.value,
                )
            )

        session.commit()  # (d) — load()'s own writes + every audit_log row above, one transaction
        return {
            **load_report,
            **diff,
            "vendors_changed": len(diff["changed_fields_by_vendor"]) + len(diff["new_vendors"]),
            # Non-blocking, informational only (task's explicit "no human
            # gate" instruction) — e.g. a Master Data grid banner reading
            # '"priority_tag" column was auto-detected as "V-P" by AI — ...'.
            # Empty whenever every field already resolved deterministically.
            "ai_column_mapping_messages": mapping_result["banner_messages"],
        }
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def revert_upload(excel_path=None, backup_path=None):
    """Part A step 3 — restores the one backup slot over the master file,
    then re-ingests it so the DB matches. Known, accepted gap (task's own
    instruction, not solved here): any payment logged or generic-field edit
    made after the bad upload and before this revert is not part of the
    backup and will look "un-done" once the DB is re-ingested from the
    restored file — surfaced as a plain warning string below, not hidden.
    """
    excel_path = excel_path or EXCEL_PATH
    backup_path = backup_path or MASTER_EXCEL_BACKUP_PATH
    if not os.path.exists(backup_path):
        raise ValueError("no backup available yet — nothing to revert to (an upload must be committed first)")

    _atomic_replace(backup_path, excel_path)

    session = SessionLocal()
    try:
        # Persisted overrides only (data_store read, no Gemini call) — the
        # restored file is a KNOWN-GOOD prior state whose headers were
        # already resolved (by the AI or by matching the fixed defaults)
        # the last time it was committed; revert should never need to
        # re-invoke the AI, only remember what it already learned.
        header_overrides = column_mapping_store.load_overrides(session)
        load_report = load(excel_path=excel_path, session=session, header_overrides=header_overrides)
        session.commit()
        return {
            **load_report,
            "warning": (
                "Any payments or edits logged since the last upload are not covered by this revert — "
                "review before continuing."
            ),
        }
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

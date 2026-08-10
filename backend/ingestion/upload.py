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
import io
import os
import shutil
import tempfile
from datetime import date, datetime

import openpyxl
from sqlalchemy import func

from backend.db.models import AuditLog, MonthlyLedger, Vendor, VendorExtraField
from backend.db.session import SessionLocal
from backend.ingestion import ai_column_mapper, column_mapping_store
from backend.ingestion.column_mapping import build_sheet_map, normalize_entity_text, sheet_start_month_warning
from backend.ingestion.load_excel import EXCEL_PATH, get_workbook_bytes, load, set_workbook_bytes
from backend.month_end.rollover import _reset_vendor_cycle_state
from backend.shared.constants import MONEY_EPSILON
from backend.shared.enums import ChangeSource
from backend.shared.planning_month_store import set_current_planning_month

# Exactly one backup slot (decision #3) — overwritten every commit, never
# timestamped/versioned. Vercel-migration task: production now stores both
# slots as MasterWorkbook DB rows ("current"/"backup"), not local files —
# Vercel Functions can't rely on local disk persisting across invocations.
# This path constant (and the disk-based backup/replace mechanics below)
# survive only as the explicit-excel_path test/ops escape hatch — see
# commit_upload()/revert_upload()'s own docstrings.
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


def _vendor_key(entity, erp_code):
    """Identity key for diffing/matching a vendor across before/after
    snapshots — (entity, erp_code), not bare erp_code (entity-code-
    collision fix: 28 real vendor pairs share a bare code across two
    different entities, e.g. ARS/FP both "V00149" — keying by erp_code
    alone would collide them in this diff, one silently overwriting the
    other's before-state). Entity is normalized (trim/casefold, same as
    load_excel.py's upsert/dedup) so a stray whitespace/case difference
    between the sheet and the stored value can't split one vendor into two
    "different" keys."""
    return (normalize_entity_text(entity), erp_code)


def _snapshot_vendor_state(session):
    """Before-state for every vendor currently in the DB, keyed by
    _vendor_key() (not id — a brand-new vendor in the uploaded file has no
    id yet at snapshot time). Used only for the diff below; load() itself
    is the one and only thing that actually mutates this state."""
    snapshot = {}
    for vendor in session.query(Vendor).all():
        ledger = {
            row.month.isoformat(): (float(row.payable), float(row.payment))
            for row in session.query(MonthlyLedger).filter_by(vendor_id=vendor.id)
        }
        extra_fields = {
            row.column_name: row.value for row in session.query(VendorExtraField).filter_by(vendor_id=vendor.id)
        }
        snapshot[_vendor_key(vendor.entity, vendor.erp_code)] = {
            "vendor_id": vendor.id,
            "erp_code": vendor.erp_code,
            "entity": vendor.entity,
            "opening_balance": float(vendor.opening_balance),
            "category": vendor.category,
            "commitment_months": vendor.commitment_months,
            "assigned_week": vendor.assigned_week,
            "ledger": ledger,
            "extra_fields": extra_fields,
        }
    return snapshot


def _changed_fields(before, vendor, session):
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

    if vendor.category != before["category"]:
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
    committing will do (task's own explicit instruction).

    Entity-code-collision fix: every set/dict below is keyed by
    _vendor_key() (entity, erp_code), not bare erp_code — else the 28 real
    vendor pairs that share a bare code across two different entities would
    collide here (one silently classified/overwritten as the other). The
    API surface (MasterDataCommitResponse) still expects plain erp_code
    strings, so the public-facing lists/dict keys stay bare erp_code UNLESS
    that code is genuinely ambiguous within this diff (maps to more than
    one entity) — then it's suffixed "<code> (<entity>)" so Finance can
    tell the two apart, rather than silently merging them back into one
    string the way bare-erp_code keying used to.
    """
    erp_keys_in_file = {_vendor_key(entity, code) for entity, code in load_report["vendor_keys_in_file"]}
    before_keys = set(before)
    after_vendors = {_vendor_key(v.entity, v.erp_code): v for v in session.query(Vendor).all()}

    def _sort_key(key):
        return (str(key[1]), key[0] or "")

    new_keys = sorted(erp_keys_in_file - before_keys, key=_sort_key)
    existing_keys = sorted(erp_keys_in_file & before_keys, key=_sort_key)
    missing_keys = sorted(before_keys - erp_keys_in_file, key=_sort_key)

    entities_by_code = {}
    for _, code in erp_keys_in_file | before_keys:
        entities_by_code.setdefault(code, set())
    for norm_entity, code in erp_keys_in_file | before_keys:
        entities_by_code[code].add(norm_entity)
    ambiguous_codes = {code for code, entities in entities_by_code.items() if len(entities) > 1}

    def _label(key):
        norm_entity, code = key
        if code not in ambiguous_codes:
            return str(code)
        vendor = after_vendors.get(key)
        entity_display = vendor.entity if vendor is not None else before.get(key, {}).get("entity")
        return f"{code} ({entity_display})"

    new_vendors = [_label(k) for k in new_keys]
    missing_vendors = [_label(k) for k in missing_keys]

    changed_fields_by_vendor = {}
    vendors_with_changed_ledger = []
    for key in existing_keys:
        vendor = after_vendors[key]
        changed = _changed_fields(before[key], vendor, session)
        if changed:
            changed_fields_by_vendor[_label(key)] = changed
        if any(" payable" in c or " payment" in c for c in changed):
            vendors_with_changed_ledger.append(_label(key))

    return {
        "new_vendor_count": len(new_keys),
        "new_vendors": new_vendors,
        "existing_vendor_count": len(existing_keys),
        "missing_vendor_count": len(missing_keys),
        "missing_vendors": missing_vendors,
        "vendors_with_changed_ledger_count": len(vendors_with_changed_ledger),
        "vendors_with_changed_ledger": vendors_with_changed_ledger,
        "changed_fields_by_vendor": changed_fields_by_vendor,
    }


def commit_upload(uploaded_excel_path, excel_path=None, backup_path=None, sheet_start_month=None, planning_month=None):
    """Part A — the entire upload flow, one call: Finance picks a file, this
    runs backup -> atomic replace -> load() -> audit log, in that order,
    with nothing shown or confirmed in between (no preview step — Sarath's
    explicit course-correction on the original two-step design).

    excel_path/backup_path: explicit path override — same convention
    vendor_edits.py's own excel_path=None param already uses, so tests can
    point this at a tmp_path copy instead of a real file. Production
    callers omit both (Vercel-migration task): the master workbook then
    lives as two Postgres rows (MasterWorkbook, "current"/"backup" slots)
    instead of local disk, since Vercel Functions can't rely on local disk
    persisting across invocations.

    sheet_start_month (P1 demo-readiness task, stretch goal): optional,
    Finance-supplied override for THIS upload's sheet start month — if
    given, persisted to `config` (column_mapping_store) so every future
    upload/edit/grid-load remembers it too (same "learn it once" pattern
    already used for AI column-mapping overrides), and used for this
    upload's own load()/validation instead of re-reading the (about to be
    stale) previous config value. None (the default) uses whatever's
    currently configured.

    planning_month (Main-tab upload-confirm task): optional, Finance-
    confirmed "YYYY-MM" for the current cycle — if given, persisted via
    planning_month_store.set_current_planning_month() in the same
    transaction as everything else this call writes, so new_model_2.py's
    _resolve_planning_month() can read it back before any plan_run exists
    yet this cycle. None (the default, e.g. every existing caller/test)
    leaves whatever's currently stored untouched.

    Month-end cycle reset (merge-rollover-into-upload task): folded into
    every upload rather than a separate "Month-end rollover" action —
    Finance should never have to remember which button to click. Compares
    the DB's latest ledger month from before this call's own load() to the
    sheet's latest month after — only when it's strictly newer does this
    reset every vendor's payment-cycle state
    (backend/month_end/rollover.py::_reset_vendor_cycle_state()), in the
    SAME transaction as the ingestion (a failure during the reset rolls
    back the ingestion too, same guarantee the old standalone rollover had).
    An ordinary same-month correction re-upload — now the routine case,
    since every re-upload takes this same path — silently skips the reset
    and never raises; so does the very first upload ever (nothing to
    compare against yet). Response carries `cycle_reset`/`vendors_reset` so
    the frontend can tell Finance when (and only when) it actually happened.

    Order (task's explicit spec, and it matters): (a) back up the CURRENT
    master to the one fixed backup slot, (b) replace the master with the
    uploaded one, (c) run load() for real against the new master, in the
    same transaction as Part B's VendorExtraField upserts (already true —
    load() upserts those itself), (d) one audit_log entry per vendor that
    actually changed.

    Vercel-migration task: when excel_path/backup_path are omitted (every
    production call), (a) and (b) are now DB writes (MasterWorkbook rows)
    in the SAME transaction as everything else this call writes — strictly
    MORE atomic than the old disk-based version, which had a documented
    residual-risk window between replacing the file and the DB commit. The
    explicit-path mode below (tests/ops) keeps the old disk-based
    backup-then-atomic-replace mechanics unchanged, residual-risk window
    and all, since two real files genuinely can't share one DB transaction.
    """
    use_disk = excel_path is not None
    if use_disk:
        backup_path = backup_path or MASTER_EXCEL_BACKUP_PATH
        if os.path.exists(excel_path):
            shutil.copyfile(excel_path, backup_path)  # (a) — exactly one slot, overwritten every time
        _atomic_replace(uploaded_excel_path, excel_path)  # (b)

    with open(uploaded_excel_path, "rb") as f:
        uploaded_bytes = f.read()

    session = SessionLocal()
    try:
        before = _snapshot_vendor_state(session)

        if not use_disk:
            current_bytes = get_workbook_bytes(session, "current")
            if current_bytes is not None:
                set_workbook_bytes(session, "backup", current_bytes)  # (a)
            set_workbook_bytes(session, "current", uploaded_bytes)  # (b)

        if sheet_start_month is not None:
            column_mapping_store.set_sheet_start_month(session, sheet_start_month)
        sheet_start_month = column_mapping_store.get_sheet_start_month(session)

        if planning_month is not None:
            set_current_planning_month(session, planning_month.strftime("%Y-%m"))

        # AI-assisted column mapping (data-pipeline AI-mapping task,
        # supersedes Part B of claude_code_prompt_priority_column_and_ai_mapping.md):
        # resolves any renamed/missing header — deterministically from a
        # prior upload's persisted mapping if already learned, via Gemini
        # otherwise — BEFORE load() runs, so load() either gets a complete
        # mapping or this raises a clear MissingRequiredColumnsError naming
        # the field, instead of load()'s own opaque ValueError deep inside
        # column_mapping.py. Opens the workbook once here purely to read
        # headers/samples, straight from the just-uploaded bytes (same
        # content whether or not this call is also writing it to disk/DB
        # below); load() below opens it again for the real parse — a
        # second cheap read, not a second copy of any parsing logic.
        wb_for_mapping = openpyxl.load_workbook(io.BytesIO(uploaded_bytes), data_only=True)
        mapping_result = ai_column_mapper.resolve_column_mapping(session, wb_for_mapping.active)

        # Non-blocking sheet-start-month sanity check (P1 demo-readiness
        # task, CLAUDE.md rule 7): built from the SAME header_overrides/
        # sheet_start_month load() is about to use, so this can never warn
        # about a mismatch load() itself didn't also see.
        start_month_sheet_map = build_sheet_map(
            wb_for_mapping.active,
            header_overrides=mapping_result["header_overrides"],
            sheet_start_month=sheet_start_month,
            header_row=mapping_result["header_row"],
            data_start_row=mapping_result["data_start_row"],
        )
        start_month_warning = sheet_start_month_warning(wb_for_mapping.active, start_month_sheet_map, sheet_start_month)

        pre_month = session.query(func.max(MonthlyLedger.month)).scalar()

        load_report = load(
            excel_path=excel_path,
            session=session,
            header_overrides=mapping_result["header_overrides"],
            sheet_start_month=sheet_start_month,
            header_row=mapping_result["header_row"],
            data_start_row=mapping_result["data_start_row"],
        )  # (c) — session supplied, load() doesn't commit
        diff = _diff_report(before, session, load_report)

        # Month-end cycle reset — only on a genuine new month (never on the
        # very first upload ever, pre_month is None then). Same transaction
        # as everything else this call writes (see docstring).
        post_month = session.query(func.max(MonthlyLedger.month)).scalar()
        cycle_reset = pre_month is not None and post_month is not None and post_month > pre_month
        vendors_reset = _reset_vendor_cycle_state(session) if cycle_reset else 0

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

        # Audit-log revamp (Task B): one summary row per upload, not one row
        # per vendor touched — a 400+ vendor sheet used to write 400+
        # near-identical rows every re-upload, drowning out everything else
        # in the log. Per-vendor detail (which fields, which vendor) is
        # still derivable from `diff`/the DB itself if ever needed; it just
        # isn't worth an audit_log row each on its own.
        new_count = len(diff["new_vendors"])
        changed_count = len(diff["changed_fields_by_vendor"])
        # Only erp_codes load() actually flipped THIS run, not
        # diff["missing_vendors"] (that set also includes vendors already
        # deactivated by a PRIOR upload, which would otherwise get counted
        # again every time the sheet is re-uploaded without them).
        removed_count = len(load_report["deactivated_erp_codes"])
        session.add(
            AuditLog(
                timestamp=datetime.now(),
                vendor_id=None,  # a whole-upload summary isn't per-vendor
                field_name="excel_upload",
                old_value=None,
                new_value=(
                    f"{load_report['vendor_count']} vendors processed — {new_count} new, "
                    f"{changed_count} changed, {removed_count} removed (soft-deactivated)"
                ),
                source=ChangeSource.EXCEL_UPLOAD.value,
            )
        )

        session.commit()  # (d) — load()'s own writes + every audit_log row above, one transaction
        # Sheet-start-month warning first (loudest, most demo-critical —
        # P1 task's "impossible to miss" instruction), then the AI
        # column-mapping banners. Empty whenever nothing needs flagging.
        banner_messages = (
            ([start_month_warning] if start_month_warning else [])
            + load_report["category_priority_conflicts"]
            + load_report["duplicate_erp_code_notes"]
            + mapping_result["banner_messages"]
        )
        return {
            **load_report,
            **diff,
            "vendors_changed": len(diff["changed_fields_by_vendor"]) + len(diff["new_vendors"]),
            # Non-blocking, informational only (task's explicit "no human
            # gate" instruction) — e.g. a Master Data grid banner reading
            # '"priority_tag" column was auto-detected as "V-P" by AI — ...'.
            # Empty whenever every field already resolved deterministically.
            "ai_column_mapping_messages": banner_messages,
            "cycle_reset": cycle_reset,
            "vendors_reset": vendors_reset,
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

    excel_path/backup_path: explicit path override (tests/ops), same
    escape hatch as commit_upload()'s own. Production callers omit both
    (Vercel-migration task) — the backup then comes from the MasterWorkbook
    "backup" DB row, restored into the "current" row in the same
    transaction as the re-ingestion below, rather than a disk copy.
    """
    use_disk = excel_path is not None
    if use_disk:
        backup_path = backup_path or MASTER_EXCEL_BACKUP_PATH
        if not os.path.exists(backup_path):
            raise ValueError("no backup available yet — nothing to revert to (an upload must be committed first)")
        _atomic_replace(backup_path, excel_path)

    session = SessionLocal()
    try:
        if not use_disk:
            backup_bytes = get_workbook_bytes(session, "backup")
            if backup_bytes is None:
                raise ValueError("no backup available yet — nothing to revert to (an upload must be committed first)")
            set_workbook_bytes(session, "current", backup_bytes)

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

"""Configurable column-mapping layer for the vendor master Excel.

Per CLAUDE.md's no-hardcoding rule, we don't map by fixed column letters —
and (this task) not by a fixed header ROW either. Real Finance sheets vary
in shape month over month: one confirmed real sheet has a two-row header
(row 2 merged block labels, row 3 actual column headers, data from row 4);
another confirmed real sheet has a single header row (row 1), data from
row 2, no master-block row at all. Columns are located by header text
where the label is reliable, and by position relative to a reliable
neighbor where the label is known to move or be unreliable — confirmed
real cases:

- The closing-balance header changes every re-upload (e.g. "As on 31st
  May'26" today) — docs/07-data-pipeline-and-master-sheet.md.
- Month headers mix plain strings ("Apr-25") and Excel datetime objects,
  and contain at least one confirmed real typo (column O is labeled
  "Nov'26" but is structurally Nov-25, the 8th of 14 consecutive months) —
  so month identity is derived from position, not by parsing the label.

The header row itself is DETECTED per upload (detect_header_row()/
resolve_header_row() below), never assumed to sit at a fixed row number —
see those functions' docstrings for the scoring approach and the AI
confirmation step (ai_column_mapper.py).
"""
import difflib
import re
from dataclasses import dataclass
from datetime import date, datetime

from backend.shared.enums import VendorCategory, VendorPriorityTag

# Shared between vendor_edits.py (writes Finance's edits back to these
# columns) and load_excel.py (re-ingestion must read the SAME columns back,
# not recompute a default — CLAUDE.md rule 6). Lives here, not in
# vendor_edits.py, so load_excel.py can import it without a circular import
# (vendor_edits.py already imports EXCEL_PATH from load_excel.py).
#
# priority_tag (AI-mapping task's Part A prerequisite): mirrors
# category/commitment_months/assigned_week exactly — a dedicated column,
# found by header text, safe-defaults to None (unchanged/unset) when the
# column or a value is missing, never crashes the upload. Sheet values are
# expected to already be the literal enum labels (P0-P5, confirmed in
# real test sheets), so no separate label-translation dict is needed the
# way CATEGORY_EXCEL_LABEL is for category below.
FIELD_TO_EXCEL_HEADER = {
    "category": "Category",
    "commitment_months": "Commitment Months",
    "assigned_week": "Assigned Week",
    "priority_tag": "Priority Tag",
}

CATEGORY_EXCEL_LABEL = {
    VendorCategory.MUST_PAY: "Must Pay",
    VendorCategory.COMMITMENT: "Commitment",
    VendorCategory.NORMAL: "Normal",
    VendorCategory.INACTIVE: "Inactive",
}
CATEGORY_FROM_EXCEL_LABEL = {label: category for category, label in CATEGORY_EXCEL_LABEL.items()}

# Category<->priority_tag intelligent mapping (this task): category and
# priority_tag are two views of the same Finance signal — some real sheets
# give it as a numeric tag (P0-P5), others as a string label (Must Pay/
# Commitment/Normal/Inactive), sometimes only one of the two. Confirmed real
# case: the live master sheet's "Category" column holds "Vendor"/"Non
# Vendor" (an entity-type flag, matches neither vocabulary) while its
# "Prioity" column holds real P0-P4 tags directly.
_CATEGORY_LABEL_ALIASES = {
    "mustpay": VendorCategory.MUST_PAY,
    "commit": VendorCategory.COMMITMENT,
    "commitment": VendorCategory.COMMITMENT,
    "normal": VendorCategory.NORMAL,
    "inactive": VendorCategory.INACTIVE,
}

# Forward: numeric tag -> category (docs/14, VendorPriorityTag's own
# docstring). P2/P3/P4 all fold into Normal.
PRIORITY_TAG_TO_CATEGORY = {
    "P0": VendorCategory.MUST_PAY,
    "P1": VendorCategory.COMMITMENT,
    "P2": VendorCategory.NORMAL,
    "P3": VendorCategory.NORMAL,
    "P4": VendorCategory.NORMAL,
    "P5": VendorCategory.INACTIVE,
}
# Reverse: string label -> priority_tag, used only when no (recognized)
# numeric tag is present to derive from instead. Normal -> P2 specifically
# (confirmed default with Sarath — never P3/P4, never left blank).
CATEGORY_TO_DEFAULT_PRIORITY_TAG = {
    VendorCategory.MUST_PAY: "P0",
    VendorCategory.COMMITMENT: "P1",
    VendorCategory.NORMAL: "P2",
    VendorCategory.INACTIVE: "P5",
}


def _dynamic_category_maps(session):
    """Category-configuration task: category_from_label()/
    derive_category_and_priority_tag() used to reconcile against a fixed
    4-value vocabulary (Must Pay/Commitment/Normal/Inactive) — now that
    Finance can add a custom category through Configuration
    (backend/configuration/priority_bucket_edits.py), those two functions
    must recognize it too, or a brand-new category would ingest fine but
    silently fail to reconcile against its own priority_tag on the very
    next upload.

    Builds the same three lookups (alias dict, tag->category,
    category->default-tag) from the LIVE priority_buckets table (Must
    Pay/Commitment are fixed, never rows there) instead of the old
    hardcoded 4-entry dicts. `session=None` (no DB access — e.g. a unit
    test with no fixture DB) falls back to those original hardcoded dicts
    unchanged, so existing no-session callers keep working exactly as
    before.
    """
    if session is None:
        return dict(_CATEGORY_LABEL_ALIASES), dict(PRIORITY_TAG_TO_CATEGORY), dict(CATEGORY_TO_DEFAULT_PRIORITY_TAG)

    from backend.configuration.priority_bucket_edits import list_buckets

    aliases = {"mustpay": VendorCategory.MUST_PAY.value, "commit": VendorCategory.COMMITMENT.value,
               "commitment": VendorCategory.COMMITMENT.value}
    tag_to_category = {"P0": VendorCategory.MUST_PAY.value, "P1": VendorCategory.COMMITMENT.value}
    category_to_default_tag = {VendorCategory.MUST_PAY.value: "P0", VendorCategory.COMMITMENT.value: "P1"}
    for bucket in list_buckets(session):
        category_name = bucket["category_name"]
        tag_to_category[bucket["bucket_key"]] = category_name
        # First (highest-priority, by rotation_position — list_buckets()
        # already orders by it) bucket for a category wins its default tag
        # — matches today's Normal -> P2 convention (P2/P3/P4 all -> Normal,
        # P2 is rotation_position 0).
        category_to_default_tag.setdefault(category_name, bucket["bucket_key"])
        aliases.setdefault(re.sub(r"[\s\-_]+", "", category_name.strip().lower()), category_name)
    return aliases, tag_to_category, category_to_default_tag


def valid_priority_tag_values(session=None):
    """The full set of priority_tag values a sheet cell can legitimately
    hold right now — P0/P1 (fixed) plus every live priority_buckets
    bucket_key (P2-P5 today, plus any custom tag Finance has added via
    Configuration). Superset of the old fixed `PRIORITY_TAG_VALUES`
    constant, which stops covering a custom tag once one exists — used by
    load_excel.py to recognize a custom tag re-ingested from a Excel cell
    Finance's own UI edit already wrote back."""
    _, tag_to_category, _ = _dynamic_category_maps(session)
    return set(tag_to_category)


def category_from_label(raw, session=None):
    """Best-effort normalized match of an Excel category cell against
    Finance's category vocabulary (Must Pay/Commitment, fixed, plus
    whatever's currently in the priority_buckets table) — accepts
    case/spacing/hyphen variants ("Must-Pay", "MUSTPAY", "Commit"). None if
    `raw` doesn't match any of them (e.g. the live sheet's actual Category
    column, which holds "Vendor"/"Non Vendor" instead — a different
    vocabulary entirely, not a typo of this one)."""
    if raw is None:
        return None
    aliases, _, _ = _dynamic_category_maps(session)
    key = re.sub(r"[\s\-_]+", "", str(raw).strip().lower())
    return aliases.get(key)


def derive_category_and_priority_tag(category_label, tag_value, session=None):
    """The one place category<->priority_tag get reconciled into a single,
    consistent pair for a vendor row.

    category_label: a category already resolved via category_from_label(),
    or None if the row's category cell didn't match any recognized label.
    tag_value: a recognized priority_tag string (one of PRIORITY_TAG_VALUES,
    or any live priority_buckets bucket_key), or None if the row's tag cell
    was blank/unrecognized.

    Returns (category, priority_tag, conflict):
    - Both recognized and they agree: that (category, tag) pair, conflict=None.
    - Both recognized and they DISAGREE: the category label wins (confirmed
      with Sarath) — returns (category_label, the tag category_label implies,
      conflict=(tag_value, category_label)) so the caller can log/audit which
      one won, naming both raw values.
    - Only one recognized: derive the other from it, conflict=None.
    - Neither recognized: (None, None, None) — caller falls back to the
      vendor's existing/default value itself, same as before this task.
    """
    _, tag_to_category, category_to_default_tag = _dynamic_category_maps(session)
    if category_label is not None and tag_value is not None:
        expected_category = tag_to_category.get(tag_value)
        if expected_category == category_label:
            return category_label, tag_value, None
        return category_label, category_to_default_tag[category_label], (tag_value, category_label)
    if category_label is not None:
        return category_label, category_to_default_tag[category_label], None
    if tag_value is not None and tag_value in tag_to_category:
        return tag_to_category[tag_value], tag_value, None
    return None, None, None

# REQUIRED_HEADER_DEFAULTS: the sheet's structural, single-header-lookup
# fields — the ones build_sheet_map() has always fail-fast raised on if
# missing (never had a safe default, unlike category/commitment_months/
# assigned_week/priority_tag above). These plus FIELD_TO_EXCEL_HEADER
# together are every logical field the AI-mapping task's gap-filler can
# possibly resolve — anything positional/regex-derived (Closing Balance,
# the monthly Payable/Payment blocks, W1-W5) has no single fixed header to
# search for and is deliberately out of scope for that mechanism.
REQUIRED_HEADER_DEFAULTS = {
    "erp_code": "ERP Code",
    "entity": "Entity",
    "vendor_name": "Vendor Name",
    "opening_balance": "Op. Balance",
    "total_payable": "Total Payable",
    "total_payment": "Total Payment",
}
REQUIRED_FIELDS = frozenset(REQUIRED_HEADER_DEFAULTS)

ALL_MAPPABLE_FIELDS = {**REQUIRED_HEADER_DEFAULTS, **FIELD_TO_EXCEL_HEADER}

# One-line, finance-analyst-readable description per field — fed verbatim
# into the AI column-mapper's prompt (ai_column_mapper.py) so it gets the
# same context a human would need, not just a bare field name.
FIELD_DESCRIPTIONS = {
    "erp_code": "Unique vendor/ERP identifier code — one row per vendor, values are short alphanumeric codes.",
    "entity": "The legal entity/business unit this vendor's bills are booked under.",
    "vendor_name": "The vendor's business/company name.",
    "opening_balance": "The vendor's outstanding balance carried forward, BEFORE this sheet's monthly Payable/Payment columns are applied.",
    "total_payable": "The sum total of every monthly Payable column for this vendor.",
    "total_payment": "The sum total of every monthly Payment column for this vendor.",
    "category": "Finance's vendor category tag — one of exactly: Must Pay, Commitment, Normal, Inactive.",
    "commitment_months": "For Commitment-category vendors only: an integer, the number of months their balance amortizes over.",
    "assigned_week": "Which week (an integer, typically 1-5) of the current payment cycle this vendor is assigned to. "
    "Values look like a bare number (1-5), a plain 'W1'..'W5' tag, OR a combined 'W2-P3'-style tag — in that combined "
    "form the letter-P part is a same-week PAY ORDER/rank (which vendor in week 2 gets paid first), NOT the vendor's "
    "own priority_tag field below, even though it reuses the letter 'P'. A column whose values are consistently "
    "'W<n>', bare small integers, or 'W<n>-P<n>' is this field, never priority_tag.",
    "priority_tag": "Finance's assigned urgency tag for this vendor — one of exactly: P0, P1, P2, P3, P4, P5 (or a "
    "further custom tag Finance has added). Values are ALWAYS a bare 'P<n>' with no leading 'W' anywhere in the "
    "cell — a cell shaped like 'W2-P3' belongs to assigned_week above, not here.",
}

PRIORITY_TAG_VALUES = {tag.value for tag in VendorPriorityTag}

# CLAUDE.md rule 7 fix (P1 demo-readiness task): this used to be the sole
# source of truth (a bare module constant). It's now only the SEED default —
# the real, live value lives in the `config` table
# (column_mapping_store.SHEET_START_MONTH_CONFIG_KEY /
# get_sheet_start_month()/set_sheet_start_month()), so Finance can correct it
# without a redeploy if a future re-upload shifts the month range (this
# can't be derived from the sheet itself since month labels aren't
# trustworthy). build_sheet_map()'s own `sheet_start_month` parameter
# defaults to this constant only for callers with no session/config access
# (tests, one-off scripts) — every real ingestion call path resolves it from
# Config instead and passes it in explicitly.
DEFAULT_SHEET_START_MONTH = date(2025, 4, 1)

_MONTH_HEADER_FORMATS = ("%b-%y", "%b'%y", "%B-%Y", "%b-%Y", "%B %Y", "%b %Y", "%B'%y", "%b %y", "%B-%y")

_WEEK_RE = re.compile(r"^W(\d+)$", re.IGNORECASE)
_ASSIGNED_WEEK_ORDER_RE = re.compile(r"^W(\d+)-P(\d+)$", re.IGNORECASE)

# --- Header row detection (this task) ---------------------------------------
#
# Sarath's explicit standing rule: never hardcode a row or column position —
# real sheets change shape month over month. The header row is detected by
# CONTENT, not assumed at a fixed offset: a real header row is (almost)
# entirely text/labels; every row below it is numeric-heavy (money amounts).
# This holds even when headers are renamed (a renamed label is still text,
# not a number), so it generalizes beyond the two sheet shapes seen so far.
#
# Scoring (documented default — no spec dictates exact weights, flagged for
# Sarath same as any other undecided formula in this codebase):
#   score = text_fraction * coverage + literal_bonus
#   - coverage: (non-blank cells in the row) / (sheet's total columns) — a
#     real header row has almost every column populated. This is what tells
#     a real per-column header row apart from a sparse merged/master-block
#     label row (e.g. one "Payable" label spanning several blank cells).
#   - text_fraction: (cells that look like a label) / (non-blank cells) —
#     near 1.0 for a header row, near 0 for a data row (money figures).
#   - literal_bonus: a small, capped boost per REQUIRED_HEADER_DEFAULTS
#     value found verbatim in the row — strong same-signal evidence when
#     headers weren't renamed, but deliberately capped low enough that it
#     can never outweigh text_fraction*coverage on its own (a renamed-header
#     sheet has ~zero literal hits and must still win on shape alone).
_HEADER_SCAN_MAX_ROWS_DEFAULT = 15
_HEADER_MIN_SCORE = 0.5  # below this, not a plausible header row at all
_HEADER_AMBIGUITY_MARGIN = 0.08  # top two candidates within this margin -> too close to call
_HEADER_LITERAL_BONUS_PER_HIT = 0.1
_HEADER_LITERAL_BONUS_CAP = 0.3

_REQUIRED_HEADER_TEXTS_LOWER = {text.lower() for text in REQUIRED_HEADER_DEFAULTS.values()}


def _looks_like_text(value):
    """True for a genuine label, False for a number (or a numeric-looking
    string — e.g. a stray "35924573.7" — which counts as data, not a
    label, even though Python's type is str for a formula-less numeric
    string edge case)."""
    if value is None or isinstance(value, bool):
        return False
    if isinstance(value, (int, float, date, datetime)):
        return False
    text = str(value).strip()
    if text == "":
        return False
    try:
        float(text)
        return False
    except ValueError:
        return True


def _score_row(ws, row_index, max_col):
    non_blank = []
    for c in range(1, max_col + 1):
        v = ws.cell(row=row_index, column=c).value
        if v is None or str(v).strip() == "":
            continue
        non_blank.append(v)
    if not non_blank:
        return 0.0
    coverage = len(non_blank) / max_col
    text_fraction = sum(1 for v in non_blank if _looks_like_text(v)) / len(non_blank)
    literal_hits = sum(1 for v in non_blank if str(v).strip().lower() in _REQUIRED_HEADER_TEXTS_LOWER)
    literal_bonus = min(_HEADER_LITERAL_BONUS_CAP, _HEADER_LITERAL_BONUS_PER_HIT * literal_hits)
    return text_fraction * coverage + literal_bonus


@dataclass
class HeaderRowCandidate:
    row_index: int
    score: float


@dataclass
class HeaderRowDetection:
    row_index: int
    data_start_row: int
    score: float
    runner_up: HeaderRowCandidate | None
    confident: bool


def detect_header_row(ws, max_scan_rows=_HEADER_SCAN_MAX_ROWS_DEFAULT):
    """Deterministic, content-based header-row guess — no header text, no
    row-number assumption. Scans the first `max_scan_rows` rows (bounded by
    the sheet's real row count), scores each (see module comment above for
    the formula), and returns the best-scoring row plus enough information
    (score, runner-up) for a caller to judge whether the guess is confident
    or genuinely ambiguous. Never raises — ambiguity is data here, not an
    exception; see resolve_header_row() for the "fail loudly" decision."""
    max_col = ws.max_column
    last_row = min(max_scan_rows, ws.max_row)
    scored = sorted(
        ((r, _score_row(ws, r, max_col)) for r in range(1, last_row + 1)),
        key=lambda pair: pair[1],
        reverse=True,
    )
    best_row, best_score = scored[0]
    runner_up = HeaderRowCandidate(*scored[1]) if len(scored) > 1 else None
    confident = best_score >= _HEADER_MIN_SCORE and (
        runner_up is None or (best_score - runner_up.score) >= _HEADER_AMBIGUITY_MARGIN
    )
    return HeaderRowDetection(
        row_index=best_row,
        data_start_row=best_row + 1,
        score=best_score,
        runner_up=runner_up,
        confident=confident,
    )


class AmbiguousHeaderRowError(ValueError):
    """Raised by resolve_header_row() when the deterministic scan can't
    confidently pick a header row AND no high-confidence AI override was
    given either — this is the "never silently apply a guess" backstop for
    the row-position question, same principle resolve_column_mapping()
    already applies per individual column."""


def resolve_header_row(ws, ai_override=None, max_scan_rows=_HEADER_SCAN_MAX_ROWS_DEFAULT):
    """The one place a header row actually gets decided.

    ai_override: optional {"row_index": int, "confidence": "high"|"medium"|
    "low"} — the AI's opinion, folded into the SAME Gemini call
    ai_column_mapper.py already makes for column mapping (never a second
    call). Precedence (mirrors resolve_column_mapping()'s own per-field
    rule exactly): the AI can only override the deterministic candidate on
    "high" confidence with a real, different row index — "medium"/"low" or
    an agreeing answer changes nothing. None (the default) is every non-
    upload caller (grid.py, vendor_edits.py) — these never re-invoke the AI,
    they just trust the deterministic scan fresh, every time (never
    persisted — real sheets change shape month over month, caching this
    would recreate the exact bug this task fixes, one layer up).

    Raises AmbiguousHeaderRowError if the deterministic scan isn't
    confident and the AI didn't confidently resolve it either — never
    silently guesses a row to build a plan on.
    """
    detection = detect_header_row(ws, max_scan_rows=max_scan_rows)
    if (
        ai_override
        and ai_override.get("confidence") == "high"
        and isinstance(ai_override.get("row_index"), int)
        and ai_override["row_index"] != detection.row_index
        and 1 <= ai_override["row_index"] <= ws.max_row
    ):
        row_index = ai_override["row_index"]
    elif detection.confident:
        row_index = detection.row_index
    else:
        runner_up_desc = (
            f"row {detection.runner_up.row_index} scored {detection.runner_up.score:.2f}"
            if detection.runner_up
            else "no other candidate row"
        )
        raise AmbiguousHeaderRowError(
            f"Could not confidently determine the sheet's header row: row {detection.row_index} scored "
            f"{detection.score:.2f} ({runner_up_desc}) — too close to call, or below the confidence "
            "threshold. Sheet layout needs manual review before this upload can proceed."
        )
    return row_index, row_index + 1


def _header_cells(ws, header_row):
    return [ws.cell(row=header_row, column=c) for c in range(1, ws.max_column + 1)]


def _find_col(header_cells, text, header_row):
    for cell in header_cells:
        if cell.value is not None and str(cell.value).strip().lower() == text.lower():
            return cell.column
    raise ValueError(f"Column with header {text!r} not found in row {header_row}")


def find_column(ws, header_text, header_row=None):
    """Column index for header_text if it exists, else None — never creates
    it. Shared by vendor_edits.py's _find_or_create_column (which creates a
    missing column) and load_excel.py (which must NOT mutate the workbook
    just to check whether a Finance edit has ever touched this field).
    `header_row`: the resolved header row (SheetMap.header_row for any
    caller that already has a sheet_map) — never a fixed constant, see
    module comment above. None (callers with no sheet_map handy, e.g.
    tests/one-off scripts) resolves it fresh via resolve_header_row(ws)
    (deterministic-only)."""
    if header_row is None:
        header_row, _ = resolve_header_row(ws)
    for cell in _header_cells(ws, header_row):
        if cell.value is not None and str(cell.value).strip().lower() == header_text.lower():
            return cell.column
    return None


def _find_week_cols(header_cells):
    cols = []
    for cell in header_cells:
        if cell.value is None:
            continue
        m = _WEEK_RE.match(str(cell.value).strip())
        if m:
            cols.append((int(m.group(1)), cell.column))
    return sorted(cols)


# Deterministic priority_tag/assigned_week fallback detection (confirmed
# real bug fix, Sarath): the AI-mapping gap-filler's own confidence rating
# can rate a genuinely correct guess "low" on an ambiguous single-word
# header like "Week" — see ai_column_mapper.py's precedence rules, which
# discard low-confidence answers for a still-missing optional field rather
# than guess. Runs BEFORE the AI is even asked: a header-name alias match,
# else a value-shape scan of the column's own data. The two fields are
# distinguished by their VALUES, never confused despite both sometimes
# containing the letter "P": a vendor's own priority tag is always a bare
# "P<n>" (P0, P1, P2...); a weekly column is either a bare week number,
# "W<n>", or "W<n>-P<n>" where the trailing P<n> is a same-week pay
# ORDER/rank (parse_assigned_week_order()'s within_week_order) — never the
# vendor's own priority tag.
# Canonical (correctly-spelled) alias names only — real-world typos like
# "Prioity" are caught generically by the fuzzy match below, not hardcoded
# one by one.
_PRIORITY_TAG_HEADER_ALIASES = {"vendorpriority", "priority", "vpriority", "prioritytag"}
_WEEKLY_PRIORITY_HEADER_ALIASES = {"weeklypriority", "week", "wpriority", "weeklyassigned", "assignedweek"}
_PRIORITY_TAG_VALUE_RE = re.compile(r"^P\d+$", re.IGNORECASE)
_WEEKLY_PRIORITY_VALUE_RE = re.compile(r"^(W\d+(-P\d+)?|\d+)$", re.IGNORECASE)
_VALUE_SCAN_ROWS = 10
_VALUE_SCAN_MIN_MATCH_RATIO = 0.5
_HEADER_FUZZY_MATCH_RATIO = 0.82


def _normalize_header_text(text):
    return re.sub(r"[\s\-_]+", "", str(text).strip().lower())


def _fuzzy_alias_match(normalized_header, aliases):
    """True if `normalized_header` is a close-enough typo of any alias
    (stdlib difflib, no exact match required) — e.g. the real sheet's
    confirmed genuine typo "Prioity" against alias "priority"."""
    return any(
        difflib.SequenceMatcher(None, normalized_header, alias).ratio() >= _HEADER_FUZZY_MATCH_RATIO
        for alias in aliases
    )


def _column_values_match_pattern(ws, col, data_start_row, value_pattern):
    values = []
    for row in range(data_start_row, data_start_row + _VALUE_SCAN_ROWS):
        v = ws.cell(row=row, column=col).value
        if v is not None and str(v).strip() != "":
            values.append(str(v).strip())
    if not values:
        return False
    matches = sum(1 for v in values if value_pattern.match(v))
    return matches / len(values) > _VALUE_SCAN_MIN_MATCH_RATIO


def detect_column_by_alias_or_pattern(ws, header_row, data_start_row, aliases, value_pattern, exclude_cols=frozenset()):
    """Three signals, in order of trust:
    1. Exact header-name alias match (case/space/hyphen-insensitive) —
       trusted alone, same as any fixed-default header lookup elsewhere.
    2. A close-typo (fuzzy) alias match — e.g. "Prioity" for "Priority" —
       is NOT trusted on header text alone; the column's own values must
       also satisfy `value_pattern`, since a near-miss on spelling is
       weaker evidence than an exact one.
    3. No usable header hint at all: a value-shape scan of the first
       `_VALUE_SCAN_ROWS` data rows — a column whose non-blank sampled
       values are more than `_VALUE_SCAN_MIN_MATCH_RATIO` matches for
       `value_pattern` counts as found.
    `exclude_cols`: columns already claimed by another resolved field,
    never reconsidered here. Returns the column index, or None if nothing
    finds it."""
    header_cells = [c for c in _header_cells(ws, header_row) if c.column not in exclude_cols and c.value is not None]

    for cell in header_cells:
        if _normalize_header_text(cell.value) in aliases:
            return cell.column

    for cell in header_cells:
        normalized = _normalize_header_text(cell.value)
        if normalized not in aliases and _fuzzy_alias_match(normalized, aliases):
            if _column_values_match_pattern(ws, cell.column, data_start_row, value_pattern):
                return cell.column

    for col in range(1, ws.max_column + 1):
        if col in exclude_cols:
            continue
        if _column_values_match_pattern(ws, col, data_start_row, value_pattern):
            return col
    return None


def detect_priority_and_weekly_columns(ws, header_row, data_start_row, exclude_cols=frozenset()):
    """priority_tag/assigned_week columns found via detect_column_by_alias_or_pattern()
    above, in that order (priority_tag first, its own match excluded from the
    weekly scan so the same column can never be claimed by both). Returns
    {field: column_index} for whichever of the two it found — a missing key
    means neither signal found that field, same as today's "leave it
    unresolved rather than guess" behavior."""
    excl = set(exclude_cols) | {c for _, c in _find_week_cols(_header_cells(ws, header_row))}
    found = {}
    priority_col = detect_column_by_alias_or_pattern(
        ws, header_row, data_start_row, _PRIORITY_TAG_HEADER_ALIASES, _PRIORITY_TAG_VALUE_RE, excl
    )
    if priority_col is not None:
        found["priority_tag"] = priority_col
        excl.add(priority_col)
    weekly_col = detect_column_by_alias_or_pattern(
        ws, header_row, data_start_row, _WEEKLY_PRIORITY_HEADER_ALIASES, _WEEKLY_PRIORITY_VALUE_RE, excl
    )
    if weekly_col is not None:
        found["assigned_week"] = weekly_col
    return found


def parse_week_number(raw):
    """A cell expressing "which week" as either a bare int (2) or a
    "W<n>"-style string tag ('W2', same _WEEK_RE this module's week-column
    detection already uses) -> the int week number, or None if it's
    neither (never raises — callers fall back and log, same pattern as
    parse_assigned_week_order())."""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return int(raw)
    text = str(raw).strip()
    try:
        return int(text)
    except ValueError:
        pass
    m = _WEEK_RE.match(text)
    return int(m.group(1)) if m else None


def _add_months(start, n):
    year = start.year + (start.month - 1 + n) // 12
    month = (start.month - 1 + n) % 12 + 1
    return date(year, month, 1)


@dataclass
class SheetMap:
    erp_code_col: int
    entity_col: int
    vendor_name_col: int
    opening_balance_col: int
    payable_cols: list  # [(month_date, col), ...] oldest first
    total_payable_col: int
    payment_cols: list  # [(month_date, col), ...] oldest first
    total_payment_col: int
    closing_balance_col: int
    week_cols: list  # [(week_number, col), ...] — empty if the sheet has no W<n> breakdown yet
    week_total_col: int | None  # None when week_cols is empty
    assigned_week_order_col: int
    header_row: int
    data_start_row: int


class MissingRequiredColumnsError(ValueError):
    """Raised by build_sheet_map() when one of REQUIRED_HEADER_DEFAULTS'
    headers can't be found under either a persisted override or its fixed
    default text. `missing_fields` names every one that failed (all of
    them, not just the first — the AI-mapping gap-filler needs the full
    list in one shot, not one Gemini call per field)."""

    def __init__(self, missing_fields):
        self.missing_fields = list(missing_fields)
        super().__init__(
            f"Required column(s) not found in the sheet: {', '.join(self.missing_fields)}"
        )


def resolve_header(field, header_overrides=None):
    """The header text to search for `field` — a persisted AI/config
    override if one exists for it, else the fixed default. Pure text
    lookup, never touches the workbook."""
    default = ALL_MAPPABLE_FIELDS[field]
    if header_overrides and field in header_overrides:
        return header_overrides[field]
    return default


def missing_mappable_fields(ws, fields, header_overrides=None, header_row=None):
    """Non-raising pre-flight check: which of `fields` (a subset of
    ALL_MAPPABLE_FIELDS) can't currently be resolved on this sheet, given
    `header_overrides`. Used by the AI-mapping gap-filler to discover every
    unresolved field before calling build_sheet_map() (which still fails
    fast on the first missing required field, same as it always has).
    `header_row`: resolved row to search; None resolves it fresh via
    resolve_header_row() (deterministic-only — this is a pre-flight check,
    not itself part of the AI's header-row confirmation loop)."""
    if header_row is None:
        header_row, _ = resolve_header_row(ws)
    return [f for f in fields if find_column(ws, resolve_header(f, header_overrides), header_row) is None]


def build_sheet_map(ws, header_overrides=None, sheet_start_month=None, header_row=None, data_start_row=None) -> SheetMap:
    """header_overrides: optional {logical_field: header_text}, sourced from
    column_mapping_store.py's persisted AI-mapping table — consulted before
    falling back to each required field's fixed default header text. None
    (the default) reproduces the exact behavior this function has always
    had.

    sheet_start_month: the calendar month of the first Payable/Payment
    column pair, used to derive every other month by position (module
    docstring — month labels aren't trustworthy). None falls back to
    DEFAULT_SHEET_START_MONTH; real callers with DB access should instead
    resolve this from the `config` table (column_mapping_store.
    get_sheet_start_month()) and pass it in explicitly.

    header_row/data_start_row: the resolved header/data-start row (this
    task). None (every caller with no AI adjudication available — grid.py,
    vendor_edits.py, tests, one-off scripts) resolves them fresh via
    resolve_header_row(ws) — deterministic-only, raises AmbiguousHeaderRowError
    if the sheet's shape genuinely can't be told apart. The upload path
    (upload.py's commit_upload()) passes both explicitly, already reconciled
    with the AI's confirmation/override (ai_column_mapper.resolve_column_mapping()).
    """
    sheet_start_month = sheet_start_month or DEFAULT_SHEET_START_MONTH
    if header_row is None or data_start_row is None:
        header_row, data_start_row = resolve_header_row(ws)
    headers = _header_cells(ws, header_row)

    try:
        opening_balance_col = _find_col(headers, resolve_header("opening_balance", header_overrides), header_row)
        total_payable_col = _find_col(headers, resolve_header("total_payable", header_overrides), header_row)
        total_payment_col = _find_col(headers, resolve_header("total_payment", header_overrides), header_row)
    except ValueError:
        raise MissingRequiredColumnsError(
            missing_mappable_fields(ws, REQUIRED_FIELDS, header_overrides, header_row)
        ) from None

    payable_start = opening_balance_col + 1
    payable_n = total_payable_col - payable_start
    payment_start = total_payable_col + 1
    payment_n = total_payment_col - payment_start

    if payable_n != payment_n:
        raise ValueError(
            f"Payable block ({payable_n} cols) and Payment block ({payment_n} cols) "
            "differ in length — sheet layout has changed, mapping needs review"
        )

    payable_cols = [(_add_months(sheet_start_month, i), payable_start + i) for i in range(payable_n)]
    payment_cols = [(_add_months(sheet_start_month, i), payment_start + i) for i in range(payment_n)]

    # Closing balance sits right after Total Payment — located by position
    # since its header label moves (see module docstring).
    closing_balance_col = total_payment_col + 1

    # W1-W5 breakdown: docs/07 — this is something the SYSTEM generates and
    # writes back to Excel going forward, not a required Finance-provided
    # input, so a fresh real sheet legitimately has none yet. Optional, not
    # a MissingRequiredColumnsError-style failure.
    week_cols = _find_week_cols(headers)
    if week_cols:
        week_total_col = max(c for _, c in week_cols) + 1
        assigned_week_order_col = week_total_col + 1
    else:
        week_total_col = None
        # No week block to sit after — falls back to right after Closing
        # Balance instead (documented default, flagged: no sheet without a
        # week block has confirmed whether this legacy packed column exists
        # at all here, or anywhere in particular).
        assigned_week_order_col = closing_balance_col + 1

    try:
        erp_code_col = _find_col(headers, resolve_header("erp_code", header_overrides), header_row)
        entity_col = _find_col(headers, resolve_header("entity", header_overrides), header_row)
        vendor_name_col = _find_col(headers, resolve_header("vendor_name", header_overrides), header_row)
    except ValueError:
        raise MissingRequiredColumnsError(
            missing_mappable_fields(ws, REQUIRED_FIELDS, header_overrides, header_row)
        ) from None

    return SheetMap(
        erp_code_col=erp_code_col,
        entity_col=entity_col,
        vendor_name_col=vendor_name_col,
        opening_balance_col=opening_balance_col,
        payable_cols=payable_cols,
        total_payable_col=total_payable_col,
        payment_cols=payment_cols,
        total_payment_col=total_payment_col,
        closing_balance_col=closing_balance_col,
        week_cols=week_cols,
        week_total_col=week_total_col,
        assigned_week_order_col=assigned_week_order_col,
        header_row=header_row,
        data_start_row=data_start_row,
    )


def parse_assigned_week_order(raw):
    """Split a combined 'W2-P3' cell into (assigned_week, within_week_order).

    0 means the vendor wasn't part of this cycle's plan at all -> (None, None).
    """
    if raw is None:
        return None, None
    text = str(raw).strip()
    if text in ("0", ""):
        return None, None
    m = _ASSIGNED_WEEK_ORDER_RE.match(text)
    if not m:
        raise ValueError(f"Unrecognized assigned-week/order value: {raw!r}")
    return int(m.group(1)), int(m.group(2))


def known_column_indices(sheet_map, extra_cols=()):
    """Every column index build_sheet_map()/load() already understands.
    `extra_cols`: the dedicated Finance-edit columns (Category, Commitment
    Months, Assigned Week) — found via find_column() by the caller (they may
    not exist yet on a sheet nobody's edited through the UI yet), passed in
    rather than re-found here to avoid a second lookup. Feeds
    unmapped_header_columns() below — the data-pipeline-upload task's
    generic-passthrough-field detection (docs/11's "store and show it, never
    feed it into any model" rule)."""
    known = {
        sheet_map.erp_code_col,
        sheet_map.entity_col,
        sheet_map.vendor_name_col,
        sheet_map.opening_balance_col,
        sheet_map.total_payable_col,
        sheet_map.total_payment_col,
        sheet_map.closing_balance_col,
        sheet_map.week_total_col,
        sheet_map.assigned_week_order_col,
    }
    known.update(c for _, c in sheet_map.payable_cols)
    known.update(c for _, c in sheet_map.payment_cols)
    known.update(c for _, c in sheet_map.week_cols)
    known.update(c for c in extra_cols if c is not None)
    return known


def unmapped_header_columns(ws, sheet_map, extra_cols=()):
    """[(col_index, header_text), ...] for every header cell NOT already
    understood by the mapping above, in column order — these become
    VendorExtraField generic-passthrough columns (load_excel.py), never fed
    into any model (CLAUDE.md rule 3). Skips blank headers (trailing empty
    columns past the sheet's real content)."""
    known = known_column_indices(sheet_map, extra_cols)
    return [
        (cell.column, format_header_value(cell.value))
        for cell in _header_cells(ws, sheet_map.header_row)
        if cell.column not in known and cell.value is not None and str(cell.value).strip() != ""
    ]


def normalize_entity_text(value):
    """Casefold+trim identity key for matching a vendor's entity value
    (entity-code-collision fix) — e.g. "ARS " vs "ARS" must resolve to the
    same entity for upsert/dedup purposes, same precedent as this
    codebase's existing vendor-name whitespace handling. None passes
    through unchanged. Display and Excel write-back always use the RAW
    entity text — this is for equality checks only."""
    if value is None:
        return None
    return str(value).strip().casefold()


def find_duplicate_erp_codes(ws, sheet_map):
    """[{"erp_code": code, "entity": entity, "rows": [{"row": r, "vendor_name": name}, ...]}, ...]
    for every (entity, erp_code) pair that appears on more than one data
    row, rows sorted ascending — the FIRST entry is always the one load()'s
    per-(entity, erp_code) upsert actually keeps (first-row-wins).

    Entity-code-collision fix: grouped by (normalized entity, erp_code),
    not bare erp_code — a bare code shared by two DIFFERENT entities (28
    confirmed real cases, e.g. ARS/FP both using "V00149", disambiguated by
    the sheet's own "Unique" column) is not a duplicate at all; Vendor's
    real uniqueness key is (entity, erp_code) (models.py), so both ingest
    as separate rows. What's left here is a genuine same-entity data-entry
    collision (confirmed real case: ARS "INC", two different vendors
    typo'd onto the same code) — Finance needs to assign a distinct code,
    the fix doesn't resolve this automatically.

    Shared by load_excel.py (its own duplicate_erp_code_notes warning) and
    grid.py (Go-live "show every row" task — flags the winning vendor row
    plus lists every row that got skipped), so both agree on exactly which
    rows are duplicates.
    """
    rows_by_key = {}
    for row in range(sheet_map.data_start_row, ws.max_row + 1):
        code = ws.cell(row=row, column=sheet_map.erp_code_col).value
        if code is None:
            continue
        entity = ws.cell(row=row, column=sheet_map.entity_col).value
        rows_by_key.setdefault((normalize_entity_text(entity), code), []).append(row)

    duplicates = []
    for (_, code), rows in rows_by_key.items():
        if len(rows) <= 1:
            continue
        duplicates.append({
            "erp_code": code,
            "entity": ws.cell(row=rows[0], column=sheet_map.entity_col).value,
            "rows": [
                {"row": r, "vendor_name": ws.cell(row=r, column=sheet_map.vendor_name_col).value}
                for r in rows
            ],
        })
    return duplicates


def to_number(raw):
    """Coerce a cell value to a float, treating blanks/whitespace/None as 0.

    Real data-entry artifact confirmed in the source sheet: a handful of
    monthly payment cells contain a stray ' ' string instead of a number.
    """
    if raw is None:
        return 0.0
    if isinstance(raw, str):
        raw = raw.strip()
        if raw == "":
            return 0.0
        return float(raw)
    return float(raw)


def format_header_value(value):
    """A header cell's display label — "Apr-26" style for a real
    datetime/date cell (module docstring: month headers mix plain strings
    and Excel datetime objects), else the same str().strip() every other
    header already goes through. Used anywhere a header is shown to
    Finance as a column name (grid.py, unmapped_header_columns() below) —
    never for month IDENTITY, which stays position-derived per this
    module's own rule; this is display formatting only."""
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.strftime("%b-%y")
    return str(value).strip()


def _parse_header_month(raw):
    """Best-effort: a payable/payment header cell's OWN claimed month, or
    None if it can't be read as one at all (this is a sanity cross-check,
    never the actual mapping mechanism — see module docstring on why month
    identity is derived by position, not by parsing this label)."""
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return date(raw.year, raw.month, 1)
    if isinstance(raw, date):
        return date(raw.year, raw.month, 1)
    text = str(raw).strip()
    for fmt in _MONTH_HEADER_FORMATS:
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        return date(parsed.year, parsed.month, 1)
    return None


def sheet_start_month_warning(ws, sheet_map, sheet_start_month):
    """Non-blocking safety net (P1 demo-readiness task, CLAUDE.md rule 7):
    cross-checks every payable/payment column's OWN header text (wherever it
    happens to parse as a date/"Mon-YY"-style string) against the month its
    POSITION implies given `sheet_start_month`. Returns a warning string if a
    meaningful number disagree, else None.

    Threshold: allow exactly ONE mismatch without firing — the sheet has one
    confirmed real typo (a "Nov'26" header that's structurally Nov-25,
    module docstring), and a single stray label shouldn't cry wolf. Two or
    more mismatches is a much stronger signal that `sheet_start_month`
    itself is configured wrong for this sheet, not just a one-off typo.
    """
    header_by_col = {cell.column: cell.value for cell in _header_cells(ws, sheet_map.header_row)}
    mismatches = []
    parseable = 0
    for month, col in sheet_map.payable_cols + sheet_map.payment_cols:
        parsed = _parse_header_month(header_by_col.get(col))
        if parsed is None:
            continue
        parseable += 1
        if parsed != month:
            mismatches.append((col, header_by_col.get(col), month))

    if len(mismatches) < 2:
        return None
    return (
        f"Sheet's month columns don't appear to start at {sheet_start_month:%b-%Y} — check the "
        "configured sheet start month before trusting Aging/Min Funds/allocation figures. "
        f"({len(mismatches)} of {parseable} parseable month headers disagree with their "
        "position-derived month.)"
    )


class SheetLayoutError(ValueError):
    """Raised by validate_sheet_layout() — the last-resort backstop (Part 5)
    right before anything reaches the database. Independent of how good
    detection gets above, this must hold even if every AI/deterministic
    layer somehow got the header row wrong."""


def validate_sheet_layout(ws, sheet_map):
    """Final sanity gate, called by load() right before any DB write:
    confirms the resolved header row's required-field cells are genuinely
    text/labels, and the first data row's money columns are genuinely
    numeric-or-blank — never a repeat of header-like text (the exact
    failure mode this task's bug produced: reading a data row as if it
    were headers). Raises SheetLayoutError naming the specific field/row
    that looks wrong; never silently proceeds."""
    required_cols = {
        "erp_code": sheet_map.erp_code_col,
        "entity": sheet_map.entity_col,
        "vendor_name": sheet_map.vendor_name_col,
        "opening_balance": sheet_map.opening_balance_col,
        "total_payable": sheet_map.total_payable_col,
        "total_payment": sheet_map.total_payment_col,
    }
    for field, col in required_cols.items():
        value = ws.cell(row=sheet_map.header_row, column=col).value
        if not _looks_like_text(value):
            raise SheetLayoutError(
                f"Sanity check failed: {field!r}'s column (col {col}) at header row {sheet_map.header_row} "
                f"doesn't look like a header label (got {value!r}) — sheet layout detection may be wrong, "
                "rejecting this upload rather than risk ingesting it against the wrong row."
            )

    money_cols = {
        "opening_balance": sheet_map.opening_balance_col,
        "total_payable": sheet_map.total_payable_col,
        "total_payment": sheet_map.total_payment_col,
    }
    for field, col in money_cols.items():
        value = ws.cell(row=sheet_map.data_start_row, column=col).value
        if value is not None and _looks_like_text(value):
            raise SheetLayoutError(
                f"Sanity check failed: {field!r}'s column (col {col}) at data row {sheet_map.data_start_row} "
                f"looks like header text (got {value!r}), not a number/blank — sheet layout detection may be "
                "wrong, rejecting this upload rather than risk ingesting it against the wrong row."
            )

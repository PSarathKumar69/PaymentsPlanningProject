"""AI-assisted column-header identification for the Excel upload pipeline
(data-pipeline AI-mapping task — supersedes Part B of
claude_code_prompt_priority_column_and_ai_mapping.md). Physically separate
from column_mapping.py/load_excel.py (CLAUDE.md rule 3): this module never
receives raw ledger figures to compute anything — it only ever sees a
handful of sample cell values per column, and only ever proposes which
header text corresponds to which logical field. The deterministic layer
(column_mapping.py) is the only thing that ever reads real vendor data for
real, using whatever header text this module (or a human, later) resolved.

AMBIGUOUS SPEC DETAIL, flagged (house style, see allocator.py's
FALLBACK_BUCKET comment): the task's own prose says the AI's cross-check
pass runs "every single upload — not just when something's broken", but
also requires (explicit test case) that a second upload with the same
already-resolved renamed header make NO second Gemini call. Both can't be
literally true (a per-upload cross-check call would necessarily re-invoke
Gemini every time, even once nothing is missing). Resolved here in favor of
the explicit, asserted test behavior: Gemini is invoked only when at least
one field (required or soft) is still unresolved after the deterministic
pass + persisted overrides; when it IS invoked, that single call does both
gap-filling AND cross-checking the fields that already resolved, using the
same sample data. A sheet where everything already resolves deterministically
never calls Gemini at all — which still satisfies "logs nothing, changes no
mapping" for that case, just for a more literal reason (never asked).
"""
import json

from backend.ai_layer import gemini_client
from backend.ingestion import column_mapping, column_mapping_store
from backend.ingestion.column_mapping import DATA_START_ROW, HEADER_ROW, MissingRequiredColumnsError

_MAX_SAMPLE_ROWS = 10

_INSTRUCTIONS = """You are an experienced Indian finance/accounts-payable analyst reviewing a vendor payment master Excel sheet. Column headers are sometimes abbreviated, renamed, or non-standard — use both the header text AND the actual sample values beneath each column to figure out what it represents, the way an experienced analyst would when opening an unfamiliar sheet for the first time. Sample values are often the strongest signal (e.g. a column repeating "P0, P1, P2, P3, P4, P5" is unmistakably a priority tag, even with a cryptic header).

You will be given, as JSON:
- "header_row": the sheet's real header row, in column order (left to right). Some header text repeats (e.g. a month name appears once under a "Payable" block and again under a "Payment" block) — use column POSITION, not just the text, to tell them apart.
- "sample_data_rows": up to 10 real data rows, each a list of values in the SAME column order as header_row.
- "fields_needing_a_column": logical fields the deterministic pass could NOT resolve — {field_name: description}. For each, decide which header_row entry (by exact header text) it corresponds to, or that you genuinely cannot tell.
- "fields_already_matched_sanity_check_only": logical fields the deterministic pass DID resolve — {field_name: {"header": header_text_it_matched, "description": ...}}. Do NOT propose a different column for these. Only flag one if its sample values clearly contradict its description.

Respond with ONLY a single JSON object, no markdown code fences, no other text, in exactly this shape:
{
  "mappings": {
    "<field_name>": {"column": "<exact header text from header_row, or null if you cannot tell>", "confidence": "high" | "medium" | "low", "reason": "<one sentence citing the header text and/or sample values>"}
  },
  "warnings": ["<short warning string about an already-matched field that looks wrong, if any>"]
}

Only include an entry in "mappings" for fields listed under "fields_needing_a_column". If you are not genuinely confident, set "column" to null and "confidence" to "low" rather than guessing — a wrong guess on a required field is worse than admitting you can't tell."""


def _extract_header_and_samples(ws, max_rows=_MAX_SAMPLE_ROWS):
    """Ordered (not dict-keyed) so a repeated header text — e.g. "Apr-25"
    appearing in both the Payable and Payment blocks — never collides;
    position is preserved exactly as a human reading the sheet would see
    it."""
    max_col = ws.max_column
    header_row = [
        (str(ws.cell(row=HEADER_ROW, column=c).value).strip() if ws.cell(row=HEADER_ROW, column=c).value is not None else None)
        for c in range(1, max_col + 1)
    ]
    sample_rows = []
    row = DATA_START_ROW
    while len(sample_rows) < max_rows and row <= ws.max_row:
        values = [ws.cell(row=row, column=c).value for c in range(1, max_col + 1)]
        if any(v is not None for v in values):  # skip a genuinely blank trailing row
            sample_rows.append(values)
        row += 1
    return header_row, sample_rows


def build_prompt(header_row, sample_rows, fields_to_map, resolved_field_headers):
    fields_needing_a_column = {f: column_mapping.FIELD_DESCRIPTIONS[f] for f in fields_to_map}
    fields_already_matched = {
        f: {"header": header, "description": column_mapping.FIELD_DESCRIPTIONS[f]}
        for f, header in resolved_field_headers.items()
    }
    payload = {
        "header_row": header_row,
        "sample_data_rows": sample_rows,
        "fields_needing_a_column": fields_needing_a_column,
        "fields_already_matched_sanity_check_only": fields_already_matched,
    }
    return f"{_INSTRUCTIONS}\n\n{json.dumps(payload, indent=2, default=str)}"


def _parse_response(response_text):
    text = (response_text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"AI column mapper returned non-JSON response: {response_text!r}") from e
    if not isinstance(parsed, dict) or "mappings" not in parsed:
        raise ValueError(f"AI column mapper response missing 'mappings' key: {response_text!r}")
    return {"mappings": parsed.get("mappings") or {}, "warnings": parsed.get("warnings") or []}


def map_columns(header_row, sample_rows, fields_to_map, resolved_field_headers):
    """One Gemini call. `resolved_field_headers`: {field: header_text} for
    fields the deterministic pass already resolved — cross-checked only,
    NEVER overridden by the result (the caller must not apply a "mapping"
    entry for a field that wasn't in fields_to_map, even if the AI
    includes one). Returns {"mappings": {...}, "warnings": [...]}."""
    prompt = build_prompt(header_row, sample_rows, fields_to_map, resolved_field_headers)
    response_text = gemini_client.generate_text(prompt)
    return _parse_response(response_text)


def resolve_column_mapping(session, ws):
    """The full Part-B flow for one upload. Deterministic-first is already
    reflected in `missing` below (a field already resolvable via a
    persisted override or its fixed default never reaches the AI at all).

    Returns:
      {"header_overrides": {field: header_text, ...},  # feed straight into load()
       "audit_entries": [{"field_name": ..., "new_value": ...}, ...],
       "banner_messages": ["... auto-detected as ... by AI ...", ...]}

    Raises MissingRequiredColumnsError if a REQUIRED field (see
    column_mapping.REQUIRED_FIELDS) still can't be resolved after the AI
    layer too — including when the AI itself reports low confidence, which
    this treats identically to "couldn't resolve it" (task's own explicit
    instruction: never silently apply a guess the model wasn't sure about).
    """
    overrides = column_mapping_store.load_overrides(session)
    all_fields = list(column_mapping.ALL_MAPPABLE_FIELDS)
    missing = column_mapping.missing_mappable_fields(ws, all_fields, overrides)

    if not missing:
        return {"header_overrides": overrides, "audit_entries": [], "banner_messages": []}

    resolved_field_headers = {
        field: column_mapping.resolve_header(field, overrides) for field in all_fields if field not in missing
    }
    header_row, sample_rows = _extract_header_and_samples(ws)
    ai_result = map_columns(header_row, sample_rows, missing, resolved_field_headers)

    new_overrides = dict(overrides)
    audit_entries = []
    banner_messages = []
    still_missing = []

    for field in missing:
        mapping = ai_result["mappings"].get(field) or {}
        column_text = mapping.get("column")
        confidence = mapping.get("confidence")
        reason = mapping.get("reason", "")

        # Low confidence is the same as "couldn't resolve it" — never
        # silently applied (task's own explicit instruction).
        if not column_text or confidence == "low":
            if field in column_mapping.REQUIRED_FIELDS:
                still_missing.append(field)
            continue

        # Never trust the AI's claimed header blindly — confirm it's a
        # real column on this sheet before persisting/using it.
        if column_mapping.find_column(ws, column_text) is None:
            if field in column_mapping.REQUIRED_FIELDS:
                still_missing.append(field)
            continue

        new_overrides[field] = column_text
        column_mapping_store.save_override(session, field, column_text, set_by="ai")
        audit_entries.append(
            {
                "field_name": f"column_mapping:{field}",
                "new_value": f'"{column_text}" (confidence={confidence}; {reason})',
            }
        )
        banner_messages.append(f'"{field}" column was auto-detected as "{column_text}" by AI — {reason}')

    if still_missing:
        raise MissingRequiredColumnsError(still_missing)

    for warning in ai_result["warnings"]:
        audit_entries.append({"field_name": "column_mapping_warning", "new_value": warning})
        banner_messages.append(f"AI cross-check warning: {warning}")

    return {"header_overrides": new_overrides, "audit_entries": audit_entries, "banner_messages": banner_messages}

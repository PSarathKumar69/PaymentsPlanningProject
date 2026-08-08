"""AI-assisted column-header identification for the Excel upload pipeline
(data-pipeline AI-mapping task — supersedes Part B of
claude_code_prompt_priority_column_and_ai_mapping.md). Physically separate
from column_mapping.py/load_excel.py (CLAUDE.md rule 3): this module never
receives raw ledger figures to compute anything — it only ever sees a
handful of sample cell values per column, and only ever proposes which
header text corresponds to which logical field. The deterministic layer
(column_mapping.py) is the only thing that ever reads real vendor data for
real, using whatever header text this module (or a human, later) resolved.

AI-first, every upload (CONFIRMED by Sarath, overriding an earlier resolution
here): a previous version of this module documented an "AMBIGUOUS SPEC
DETAIL" — the original spec said the cross-check should run "every single
upload", but an explicit test also required a second upload with an
already-resolved renamed header to make ZERO Gemini calls, so this module
resolved in favor of "0 calls when nothing's missing." Sarath has now
explicitly overridden that: real Finance sheets are messy enough (scrambled
column order, look-alike headers meaning different fields) that a
coincidental exact-header-text match — a fixed default OR a stale persisted
override — is not proof the column still means what it used to. AI now
identifies EVERY field in column_mapping.ALL_MAPPABLE_FIELDS, EVERY upload,
in one batched Gemini call (never one call per field — cost stays flat,
just "always 1" instead of "0 or 1"), whether or not a candidate already
exists for it. A field that already has a candidate (fixed default or a
persisted override — the AI doesn't know or care which) can now actually be
overridden, not just flagged, but only on a genuinely confident, different
answer — see resolve_column_mapping()'s own docstring for the exact
precedence rules.
"""
import json

import httpx
from google.genai import errors as genai_errors

from backend.ai_layer import gemini_client
from backend.ingestion import column_mapping, column_mapping_store
from backend.ingestion.column_mapping import MissingRequiredColumnsError

_MAX_SAMPLE_ROWS = 10
_RAW_ROWS_AROUND_CANDIDATE = 10  # candidate row + this many rows below, unsliced (header-row confirmation)


class AIMappingUnavailableError(Exception):
    """Raised when the one-per-upload Gemini call can't complete at all —
    a real outage, retries already exhausted by the SDK's own retry policy
    (gemini_client.py). Distinct from any answer-quality concern (that's
    what "low confidence" is for) and from a missing/placeholder API key
    (still a plain ValueError — a config problem, not an outage).

    Sarath's explicit decision: never silently fall back to deterministic-
    only column mapping when this happens — that fallback already proved
    unreliable on real messy sheets this session (the entire reason AI-first
    mapping exists). Fail the upload loudly and safely instead; the natural
    recovery is just retrying the upload once Gemini is back."""

_INSTRUCTIONS = """You are an experienced Indian finance/accounts-payable analyst reviewing a vendor payment master Excel sheet. Column headers are sometimes abbreviated, renamed, scrambled out of their usual order, or non-standard — use the header text, the merged master-header row above it, AND the actual sample values beneath each column to figure out what it represents, the way an experienced analyst would when opening an unfamiliar sheet for the first time. Sample values are often the strongest signal (e.g. a column repeating "P0, P1, P2, P3, P4, P5" is unmistakably a priority tag, even with a cryptic header).

You will be given, as JSON:
- "header_row": a deterministic, content-based BEST-GUESS at the sheet's real header row, in column order (left to right). Some header text repeats (e.g. a month name appears once under a "Payable" block and again under a "Payment" block) — use column POSITION, not just the text, to tell them apart.
- "master_header_row": the row directly above the guessed header_row — a merged/master header block label (e.g. a single "Payable" or "Payment" label spanning several month columns beneath it), or null if the guessed header row is row 1 (nothing above it) or there simply isn't one. Additional positional/contextual evidence, same spirit as using column position to disambiguate a repeated month header.
- "sample_data_rows": up to 10 real data rows (assuming the guessed header_row is correct), each a list of values in the SAME column order as header_row.
- "header_row_candidate": {"row_index": ..., "confidence": "high"|"low"} — how confident a separate, purely structural check (fraction of text vs. numeric cells per row) already is that the guessed row above is really the header row. "low" means that check itself found the sheet's shape genuinely ambiguous.
- "raw_rows": the candidate row and the several rows immediately below it, completely UNSLICED (every column, exactly as stored) — given specifically so you can judge the header_row/data-row boundary for yourself, independent of the pre-sliced header_row/sample_data_rows above (which already assume the candidate is correct, so they can't reveal a wrong guess on their own — you need to look at raw_rows directly for that).
- "fields_needing_a_column": logical fields the deterministic pass could NOT resolve at all — {field_name: description}. For each, decide which header_row entry (by exact header text) it corresponds to, or that you genuinely cannot tell.
- "fields_already_matched_sanity_check_only": logical fields the deterministic pass DID resolve to some column — {field_name: {"header": header_text_it_matched, "description": ...}}. Usually this existing match is correct — if you agree, simply omit that field from "mappings" entirely. Only include an entry for one of these fields in "mappings" if the sample data clearly shows the existing match is WRONG (the header text is a false friend — it reads right but the values underneath mean something else). When you do flag one, set "confidence": "high" ONLY if the sample data unambiguously contradicts the current match, and write a "reason" that cites the specific sample values, not just a hunch — a "medium"/"low"-confidence disagreement (or simply repeating the current column back) will NOT change anything, it only gets logged as a warning, so don't propose one unless you're genuinely confident.

IMPORTANT — a general confusion pattern to watch for on every sheet, not just a one-off: two different logical fields can have header wording that looks similar or means something similar in plain English, even though they are NOT the same field. The concrete case to watch for here: a header containing a word like "priority" could refer to EITHER the vendor's own priority tag (logical field priority_tag — values are literally the short strings P0, P1, P2, P3, P4, or P5) OR which week of the payment cycle a vendor is assigned to (logical field assigned_week — values are small plain integers like 1-5, or "Wn"-style labels like "W2"). These are different fields and must never be conflated just because both headers happen to contain the word "priority". Whenever header wording is ambiguous, or two candidate fields could plausibly share similar wording, the actual sample data values beneath each column are the deciding signal — not the header text. Apply this same reasoning to any other look-alike pair you notice, not only this one.

Respond with ONLY a single JSON object, no markdown code fences, no other text, in exactly this shape:
{
  "mappings": {
    "<field_name>": {"column": "<exact header text from header_row, or null if you cannot tell>", "confidence": "high" | "medium" | "low", "reason": "<one sentence citing the header text and/or sample values>"}
  },
  "header_row": {"row_index": <int, from raw_rows>, "confidence": "high" | "medium" | "low", "reason": "<one sentence>"},
  "warnings": ["<short warning string about an already-matched field that looks wrong, if any>"]
}

Include an entry in "mappings" for every field listed under "fields_needing_a_column". Only include an entry for a field listed under "fields_already_matched_sanity_check_only" if you believe its current match is genuinely wrong (see above) — omit it entirely if you agree with the existing match. If you are not genuinely confident about a field in "fields_needing_a_column", set "column" to null and "confidence" to "low" rather than guessing — a wrong guess on a required field is worse than admitting you can't tell.

For "header_row": look at "raw_rows" and decide for yourself which row index actually looks like the real header row (near-100% text/labels, not numbers) versus a data row (numbers) or a sparse master-block-label row (mostly blank). If you agree the given "header_row_candidate"["row_index"] is correct, set "confidence" to "high" or "medium" and use that SAME row_index (this is the normal case — only propose a DIFFERENT row_index when raw_rows clearly shows the candidate is wrong, e.g. it's full of numbers/vendor names instead of labels). A "medium"/"low"-confidence disagreement, or repeating the same row_index back, changes nothing — only a "high"-confidence, genuinely different row_index actually overrides the candidate."""


def _extract_header_and_samples(ws, header_row_index, data_start_row, max_rows=_MAX_SAMPLE_ROWS):
    """Ordered (not dict-keyed) so a repeated header text — e.g. "Apr-25"
    appearing in both the Payable and Payment blocks — never collides;
    position is preserved exactly as a human reading the sheet would see
    it. `header_row_index`/`data_start_row`: the deterministic candidate
    (column_mapping.detect_header_row()) — this task no longer assumes a
    fixed row 2/row 3, since a real sheet can have its header on row 1 with
    no master-block row above it at all (master_header_row is None then)."""
    max_col = ws.max_column

    def _row_values(row):
        return [
            (str(ws.cell(row=row, column=c).value).strip() if ws.cell(row=row, column=c).value is not None else None)
            for c in range(1, max_col + 1)
        ]

    master_header_row = _row_values(header_row_index - 1) if header_row_index > 1 else None
    header_row = _row_values(header_row_index)

    sample_rows = []
    row = data_start_row
    while len(sample_rows) < max_rows and row <= ws.max_row:
        values = [ws.cell(row=row, column=c).value for c in range(1, max_col + 1)]
        if any(v is not None for v in values):  # skip a genuinely blank trailing row
            sample_rows.append(values)
        row += 1
    return header_row, master_header_row, sample_rows


def _extract_raw_rows(ws, header_row_index, count=_RAW_ROWS_AROUND_CANDIDATE):
    """The candidate header row and the `count` rows immediately below it,
    completely unsliced (every column, raw values) — given to the AI
    specifically so it can judge the header/data-row boundary itself, not
    just trust the pre-sliced header_row/sample_data_rows (which already
    assume the candidate is right and so can't reveal a wrong guess on
    their own, this task's whole point)."""
    max_col = ws.max_column
    last_row = min(header_row_index + count, ws.max_row)
    return [
        [ws.cell(row=r, column=c).value for c in range(1, max_col + 1)]
        for r in range(header_row_index, last_row + 1)
    ]


def build_prompt(
    header_row,
    sample_rows,
    fields_to_map,
    resolved_field_headers,
    master_header_row=None,
    header_row_candidate=None,
    raw_rows=None,
):
    fields_needing_a_column = {f: column_mapping.FIELD_DESCRIPTIONS[f] for f in fields_to_map}
    fields_already_matched = {
        f: {"header": header, "description": column_mapping.FIELD_DESCRIPTIONS[f]}
        for f, header in resolved_field_headers.items()
    }
    payload = {
        "header_row": header_row,
        "master_header_row": master_header_row,
        "sample_data_rows": sample_rows,
        "header_row_candidate": header_row_candidate,
        "raw_rows": raw_rows,
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
    return {
        "mappings": parsed.get("mappings") or {},
        "warnings": parsed.get("warnings") or [],
        # None when the AI didn't answer at all (e.g. conftest.py's default
        # stub) — treated identically to "agrees with the candidate" by
        # resolve_header_row(), never as an override.
        "header_row": parsed.get("header_row") or None,
    }


def map_columns(
    header_row,
    sample_rows,
    fields_to_map,
    resolved_field_headers,
    master_header_row=None,
    header_row_candidate=None,
    raw_rows=None,
):
    """One Gemini call, batching every field regardless of whether it's
    missing or already has a candidate, PLUS the header-row confirmation
    (this task) — never a second call. `resolved_field_headers`: {field:
    header_text} for fields the deterministic pass already resolved (a
    fixed default OR a persisted override — this module doesn't
    distinguish) — sent as context the AI can confirm OR, on a genuinely
    confident disagreement, propose a correction for (see
    resolve_column_mapping()). Returns {"mappings": {...}, "warnings": [...],
    "header_row": {...} | None}.
    """
    prompt = build_prompt(
        header_row, sample_rows, fields_to_map, resolved_field_headers, master_header_row, header_row_candidate, raw_rows
    )
    try:
        response_text = gemini_client.generate_text(prompt)
    except ValueError:
        raise  # missing/placeholder API key — a distinct, already-clear config error, not an outage
    except (genai_errors.APIError, httpx.HTTPError) as e:
        # genai_errors.APIError: Gemini answered with an error status (4xx/5xx
        # the SDK's own retry gave up on). httpx.HTTPError: the request never
        # got a response at all — connection refused, DNS failure, TLS/SSL
        # handshake failure (confirmed real case: a corporate proxy/firewall
        # killing the TLS connection produced httpx.ConnectError, which is
        # NOT a genai_errors.APIError subclass and was falling through
        # uncaught here, crashing the whole upload with a raw 500 instead of
        # this endpoint's own clean 503 (backend/api/main.py's
        # AIMappingUnavailableError handler) — same "outage, not a bug"
        # treatment either way, network-level or API-level.
        raise AIMappingUnavailableError(
            "AI column mapping is temporarily unavailable — please try the upload again in a few minutes."
        ) from e
    return _parse_response(response_text)


def resolve_column_mapping(session, ws):
    """The full Part-B flow for one upload — AI-FIRST, every upload, every
    field (Sarath's explicit instruction, see module docstring): Gemini is
    always called exactly once, batching every field in
    column_mapping.ALL_MAPPABLE_FIELDS, PLUS (this task) the header-row
    confirmation — never a second call for that either.

    Header row (this task): column_mapping.detect_header_row() proposes a
    candidate purely from sheet content (never a fixed row number). The
    SAME Gemini call is given that candidate plus the raw surrounding rows
    and can override it, but only on "high" confidence with a genuinely
    different row — same precedence rule as every column-mapping field
    below (see column_mapping.resolve_header_row()). If the deterministic
    candidate isn't confident AND the AI doesn't confidently resolve it
    either, this raises AmbiguousHeaderRowError — never silently guesses a
    row to build a plan on.

    DOCUMENTED SIMPLIFICATION (flagged, not silently decided): the
    field-mapping portion of this SAME call is built using the
    DETERMINISTIC candidate's header row (sample rows, resolved_field_headers
    lookups, etc. are all computed against it) — this is necessary to ask
    both questions in one call. If the AI ends up CORRECTING the header row,
    every field-mapping answer in that same response was judged against the
    wrong row's data and cannot be trusted, so it is discarded outright
    (never applied, never persisted) — this upload proceeds using only
    whatever's already resolvable via fixed defaults/persisted overrides
    against the CORRECTED row, exactly like a fresh sheet no AI has ever
    seen; a genuinely renamed field on this same sheet gets resolved on the
    NEXT upload's call instead of a second Gemini call on this one. A
    required field that's still unresolvable at the corrected row still
    raises MissingRequiredColumnsError, same as always — this never
    silently ingests against an uncertain layout.

    Precedence, per column-mapping field (unchanged from before this task):
      - No existing candidate at all (not in a persisted override, no
        fixed-default header match): the AI's answer applies if confidence
        is "high" or "medium" AND the claimed column really exists on the
        sheet (column_mapping.find_column() check). "low" confidence or no
        answer -> unresolved — fails fast via MissingRequiredColumnsError
        for a required field, stays out of the mapping for a soft one.
      - Already has a candidate (fixed default OR a persisted override —
        passed to the AI as context, exactly the same either way): the
        AI's answer can now actually REPLACE it, but only when the AI
        proposes a DIFFERENT column, with "high" confidence, and that
        column is real on the sheet. A "medium"/"low"-confidence
        disagreement, or the AI simply repeating the same column back,
        never changes anything — logged as a non-blocking warning only.
        An actual override is loud: a `column_mapping:{field}` audit_log
        entry naming the old header, the new header, and the AI's reason,
        plus a banner message prefixed "AI OVERRODE..." — visibly
        different from the "auto-detected as..." wording a fresh
        (previously-missing) resolution gets, so Finance can tell the two
        apart at a glance.

    Returns:
      {"header_overrides": {field: header_text, ...},  # feed straight into load()
       "header_row": int, "data_start_row": int,        # feed straight into load()/build_sheet_map()
       "audit_entries": [{"field_name": ..., "new_value": ...}, ...],
       "banner_messages": ["... auto-detected as ... by AI ...", ...]}

    Raises MissingRequiredColumnsError if a REQUIRED field still can't be
    resolved after the AI layer too, and AmbiguousHeaderRowError if the
    header row itself can't be confidently determined either way.
    """
    detection = column_mapping.detect_header_row(ws)
    overrides = column_mapping_store.load_overrides(session)
    all_fields = list(column_mapping.ALL_MAPPABLE_FIELDS)
    missing = column_mapping.missing_mappable_fields(ws, all_fields, overrides, detection.row_index)
    missing_set = set(missing)

    audit_entries = []
    banner_messages = []

    resolved_field_headers = {
        field: column_mapping.resolve_header(field, overrides) for field in all_fields if field not in missing_set
    }
    header_row, master_header_row, sample_rows = _extract_header_and_samples(
        ws, detection.row_index, detection.data_start_row
    )
    raw_rows = _extract_raw_rows(ws, detection.row_index)
    header_row_candidate = {"row_index": detection.row_index, "confidence": "high" if detection.confident else "low"}

    ai_unavailable = False
    try:
        ai_result = map_columns(
            header_row, sample_rows, missing, resolved_field_headers, master_header_row, header_row_candidate, raw_rows
        )
    except AIMappingUnavailableError:
        ai_unavailable = True
        # Confirmed real-world case: "AI-FIRST, every upload" (see docstring
        # above) means a Gemini outage used to hard-503 EVERY upload, even
        # one where every field already resolves from a fixed default or a
        # previously-persisted override and needs no AI help at all. Treat
        # the outage as "AI has no opinion on anything" — identical to a
        # no-answer Gemini response — and fall through to the exact same
        # per-field logic below. A field that's genuinely unresolvable
        # without the AI (in `missing` and REQUIRED) still fails below, with
        # a clear reason; header row falls back to the deterministic scan,
        # raising AmbiguousHeaderRowError itself if that isn't confident
        # either — never silently guessed.
        banner_messages.append(
            "AI column mapping is temporarily unavailable — proceeded using only already-resolved columns "
            "(fixed defaults / persisted overrides); nothing new could be auto-detected this upload."
        )
        ai_result = {"mappings": {}, "warnings": [], "header_row": None}

    final_header_row, final_data_start_row = column_mapping.resolve_header_row(ws, ai_override=ai_result["header_row"])

    if final_header_row != detection.row_index:
        # Header row was corrected — see docstring's DOCUMENTED SIMPLIFICATION
        # above: this same call's field mappings were judged against the
        # wrong row and are discarded, never applied/persisted.
        ai_reason = (ai_result["header_row"] or {}).get("reason", "")
        audit_entries.append(
            {
                "field_name": "header_row_correction",
                "new_value": (
                    f"AI corrected the sheet's header row from {detection.row_index} to {final_header_row} "
                    f"({ai_reason}) — this upload's column-mapping answers were discarded (judged against the "
                    "wrong row); resolved from existing defaults/persisted overrides only."
                ),
            }
        )
        banner_messages.append(
            f"AI corrected the detected header row from row {detection.row_index} to row {final_header_row} — "
            f"{ai_reason}"
        )
        new_overrides = dict(overrides)
        corrected_missing = column_mapping.missing_mappable_fields(ws, all_fields, overrides, final_header_row)
        still_missing = [f for f in corrected_missing if f in column_mapping.REQUIRED_FIELDS]
        if still_missing:
            raise MissingRequiredColumnsError(still_missing)
        return {
            "header_overrides": new_overrides,
            "header_row": final_header_row,
            "data_start_row": final_data_start_row,
            "audit_entries": audit_entries,
            "banner_messages": banner_messages,
        }

    # Deterministic priority_tag/assigned_week detection (confirmed real bug
    # fix, Sarath) — a still-missing field with an unambiguous VALUE shape
    # (bare "P<n>" vs "W<n>"/"W<n>-P<n>"/a bare week number, see
    # column_mapping.detect_priority_and_weekly_columns()'s docstring) is
    # resolved here directly, never left to depend on Gemini's own (sometimes
    # over-cautious) confidence rating on an ambiguous header like "Week".
    # Runs only once the header row is confirmed stable (past the correction
    # branch above) so it's never judged against a row later found wrong.
    # AI can still override it below on the same "high confidence, genuinely
    # different, real column" terms as any other existing candidate.
    already_resolved_cols = {
        col
        for field in all_fields
        if field not in missing_set
        for col in [column_mapping.find_column(ws, column_mapping.resolve_header(field, overrides), final_header_row)]
        if col is not None
    }
    for field, col in column_mapping.detect_priority_and_weekly_columns(
        ws, final_header_row, final_data_start_row, exclude_cols=already_resolved_cols
    ).items():
        if field not in missing_set:
            continue
        header_cell_value = ws.cell(row=final_header_row, column=col).value
        if header_cell_value is None or str(header_cell_value).strip() == "":
            continue
        header_text = str(header_cell_value).strip()
        overrides[field] = header_text
        column_mapping_store.save_override(session, field, header_text, set_by="pattern-detector")
        resolved_field_headers[field] = header_text
        missing_set.discard(field)
        audit_entries.append(
            {
                "field_name": f"column_mapping:{field}",
                "new_value": f'"{header_text}" (deterministically detected by header name/value pattern, no AI needed)',
            }
        )
        banner_messages.append(
            f'"{field}" column was auto-detected as "{header_text}" from its header name/value pattern — no AI needed.'
        )

    new_overrides = dict(overrides)
    still_missing = []

    for field in all_fields:
        mapping = ai_result["mappings"].get(field) or {}
        column_text = mapping.get("column")
        confidence = mapping.get("confidence")
        reason = mapping.get("reason", "")

        if field in missing_set:
            # No existing candidate at all — unchanged from before this task.
            if not column_text or confidence == "low":
                if field in column_mapping.REQUIRED_FIELDS:
                    still_missing.append(field)
                else:
                    # Bug fix: an unresolved OPTIONAL field (e.g. priority_tag,
                    # assigned_week) used to just `continue` here with zero
                    # trace — load() then silently writes None for every
                    # vendor with no existing DB value, indistinguishable from
                    # the sheet genuinely having no data for that field. Flag
                    # it exactly like the "already matched but AI disagrees"
                    # branch below already does, so a low-confidence/no-answer
                    # Gemini response is always visible to Finance, never a
                    # silent data loss.
                    audit_entries.append(
                        {
                            "field_name": "column_mapping_warning",
                            "new_value": (
                                f'Could not confidently identify a column for "{field}" (confidence='
                                f'{confidence or "none"}) — this field will be left blank for any vendor '
                                f"without an existing value. {reason}"
                            ),
                        }
                    )
                    banner_messages.append(
                        f'AI could not confidently identify a column for "{field}" (confidence='
                        f'{confidence or "none"}) — leaving it unresolved rather than guessing. {reason}'
                    )
                continue
            if column_mapping.find_column(ws, column_text, final_header_row) is None:
                if field in column_mapping.REQUIRED_FIELDS:
                    still_missing.append(field)
                else:
                    audit_entries.append(
                        {
                            "field_name": "column_mapping_warning",
                            "new_value": (
                                f'AI proposed "{column_text}" for "{field}" but that column does not exist '
                                "on this sheet — left unresolved rather than guessing."
                            ),
                        }
                    )
                    banner_messages.append(
                        f'AI cross-check warning: "{field}" was proposed as "{column_text}", but that column '
                        "was not found on this sheet — left unresolved rather than guessing."
                    )
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
            continue

        # Already has a candidate (fixed default or persisted override).
        current_header = resolved_field_headers[field]
        if not column_text or column_text == current_header:
            continue  # AI agrees (or had no opinion) — nothing to do, nothing to log

        if confidence == "high" and column_mapping.find_column(ws, column_text, final_header_row) is not None:
            # Confident, real, and different -> an actual override (this
            # task's reversal — used to be sanity-check-only, never applied).
            new_overrides[field] = column_text
            column_mapping_store.save_override(session, field, column_text, set_by="ai")
            audit_entries.append(
                {
                    "field_name": f"column_mapping:{field}",
                    "new_value": (
                        f'AI OVERRODE "{current_header}" -> "{column_text}" (confidence={confidence}; {reason})'
                    ),
                }
            )
            banner_messages.append(
                f'AI OVERRODE a previously-resolved column: "{field}" was "{current_header}", '
                f'now "{column_text}" — {reason}'
            )
        else:
            # Not confident (or confident but not actually a real column) —
            # never silently applied. Logged as a warning only, same as the
            # pre-existing cross-check-warning path.
            audit_entries.append(
                {
                    "field_name": "column_mapping_warning",
                    "new_value": (
                        f'AI suggested "{field}" might be "{column_text}" (confidence={confidence}) instead of '
                        f'"{current_header}", but this wasn\'t a high-confidence, verified override — kept the '
                        f"existing mapping. {reason}"
                    ),
                }
            )
            banner_messages.append(
                f'AI cross-check warning: "{field}" might be "{column_text}" instead of "{current_header}" '
                f"(confidence={confidence}) — kept existing mapping. {reason}"
            )

    if still_missing:
        if ai_unavailable:
            # These fields have no fixed default and no persisted override —
            # only the (unavailable) AI could have resolved them. Say so:
            # "required column not found" reads as a permanent data problem
            # and would send Finance chasing the wrong fix.
            raise AIMappingUnavailableError(
                "AI column mapping is temporarily unavailable, and the following required column(s) have no "
                f"existing mapping to fall back on: {', '.join(still_missing)}. Please try the upload again in "
                "a few minutes."
            )
        raise MissingRequiredColumnsError(still_missing)

    for warning in ai_result["warnings"]:
        audit_entries.append({"field_name": "column_mapping_warning", "new_value": warning})
        banner_messages.append(f"AI cross-check warning: {warning}")

    return {
        "header_overrides": new_overrides,
        "header_row": final_header_row,
        "data_start_row": final_data_start_row,
        "audit_entries": audit_entries,
        "banner_messages": banner_messages,
    }

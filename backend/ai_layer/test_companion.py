"""Self-checks for the vendor talking points companion
(backend/ai_layer/context_builder.py's build_vendor_talking_points_fact_pack,
prompt_template.py's build_talking_points_prompt, companion.py's
generate_vendor_talking_points). Run with: pytest backend/ai_layer/test_companion.py

Never calls the real Gemini API — gemini_client.generate_text is
monkeypatched, same convention as test_talking_script.py. Concrete,
hand-built inputs throughout — this module never touches vendor/ledger data.

The prior "broader Q&A" mode (build_plan_summary_fact_pack, build_qa_prompt,
answer_plan_summary_question/answer_vendor_question) was removed in this
task (Sarath: "this functionality is enough" — vendor talking points only) —
no tests for it remain here.
"""
import pytest

from backend.ai_layer import companion, context_builder, gemini_client, prompt_template


# ---- build_vendor_talking_points_fact_pack --------------------------------


def test_vendor_talking_points_fact_pack_full_status_not_cut():
    aging = {
        "oldest_bucket": "0_30",
        "oldest_bucket_months_back": 0,
        "bucket_balances": {"0_30": 5000.0},
        "total_outstanding": 5000.0,
        "oldest_bucket_amount": 5000.0,
        "monthly_breakdown": [{"month": "2026-07-01", "label": "0-30", "amount": 5000.0}],
    }
    min_funds_breakdown = {
        "total": 5000.0,
        "rule": "v2_only_current",
        "category": "normal",
        "current_month": "2026-07-01",
        "current_amount": 5000.0,
        "oldest_month": None,
        "oldest_amount": None,
        # a caller (router) always sends the full min_funds_breakdown_v2()
        # shape, but a field this branch never populates (opening_balance)
        # is still None here -> confirms it gets dropped, not sent as null.
        "opening_balance": None,
    }

    fact_pack = context_builder.build_vendor_talking_points_fact_pack(
        vendor_id=1,
        erp_code="V001",
        vendor_name="Alpha Traders",
        category="normal",
        priority_tag="P2",
        aging=aging,
        min_funds_breakdown=min_funds_breakdown,
        allocated_amount=5000.0,
        required_amount=5000.0,
        status="full",
    )

    assert fact_pack == {
        "vendor_id": 1,
        "erp_code": "V001",
        "vendor_name": "Alpha Traders",
        "category": "normal",
        "priority_tag": "P2",
        "required_amount": 5000.0,
        "allocated_amount": 5000.0,
        "status": "full",
        "cut_from_full": False,
        "aging": aging,
        "min_funds_breakdown": {k: v for k, v in min_funds_breakdown.items() if v is not None},
    }


def test_vendor_talking_points_fact_pack_zero_status_is_cut():
    fact_pack = context_builder.build_vendor_talking_points_fact_pack(
        vendor_id=3,
        erp_code="V003",
        vendor_name="Gamma Co",
        category="normal",
        priority_tag="P4",
        aging={},
        min_funds_breakdown={"total": 8000.0, "rule": "v2_oldest_only", "category": "normal"},
        allocated_amount=0.0,
        required_amount=8000.0,
        status="zero",
    )
    assert fact_pack["cut_from_full"] is True
    assert fact_pack["aging"] == {}


# ---- build_talking_points_prompt / generate_vendor_talking_points, one per category --

_CATEGORY_CASES = [
    (
        "must_pay",
        dict(
            vendor_id=1, erp_code="V001", vendor_name="Alpha Freight", category="must_pay", priority_tag="P0",
            aging={"oldest_bucket": "0_30", "total_outstanding": 100000.0},
            min_funds_breakdown={"total": 100000.0, "rule": "v2_only_current", "category": "must_pay"},
            allocated_amount=100000.0, required_amount=100000.0, status="guaranteed",
        ),
    ),
    (
        "commitment",
        dict(
            vendor_id=2, erp_code="V002", vendor_name="Beta Logistics", category="commitment", priority_tag="P1",
            aging={"oldest_bucket": None, "total_outstanding": 0.0},
            min_funds_breakdown={"total": 40000.0, "rule": "commitment_opening_balance_over_months", "category": "commitment", "opening_balance": 240000.0, "commitment_months": 6},
            allocated_amount=40000.0, required_amount=40000.0, status="guaranteed",
        ),
    ),
    (
        "normal",
        dict(
            vendor_id=3, erp_code="V003", vendor_name="Gamma Supplies", category="normal", priority_tag="P3",
            aging={"oldest_bucket": "60_90", "total_outstanding": 20000.0},
            min_funds_breakdown={"total": 20000.0, "rule": "v2_oldest_only", "category": "normal"},
            allocated_amount=9000.0, required_amount=20000.0, status="partial",
        ),
    ),
    (
        "inactive",
        dict(
            vendor_id=4, erp_code="V004", vendor_name="Delta Winddown", category="inactive", priority_tag="P5",
            aging={"oldest_bucket": "120_plus", "total_outstanding": 50000.0},
            min_funds_breakdown={"total": 50000.0, "rule": "v2_oldest_only", "category": "inactive"},
            allocated_amount=0.0, required_amount=50000.0, status="zero",
        ),
    ),
]


@pytest.mark.parametrize("category, vendor_fields", _CATEGORY_CASES, ids=[c[0] for c in _CATEGORY_CASES])
def test_generate_vendor_talking_points_per_category(monkeypatch, category, vendor_fields):
    calls = []
    monkeypatch.setattr(gemini_client, "generate_text", lambda prompt: calls.append(prompt) or "SCRIPT")

    script = companion.generate_vendor_talking_points(**vendor_fields)

    assert script == "SCRIPT"
    prompt = calls[0]

    # The right facts reached the prompt (as JSON, not recomputed/reworded).
    assert vendor_fields["vendor_name"] in prompt
    assert str(vendor_fields["required_amount"]) in prompt
    assert str(vendor_fields["allocated_amount"]) in prompt
    assert vendor_fields["status"] in prompt

    # Guardrail instructions present in the system prompt text.
    assert "Never state or imply a legal or binding commitment" in prompt
    assert "Never expose internal mechanics" in prompt
    assert "Never compare this vendor to any other vendor" in prompt
    assert "Never use language that could read as threatening, discriminatory, or non-compliant" in prompt
    assert "say so plainly in the script" in prompt


def test_talking_points_prompt_separates_operator_voice_from_script_voice():
    """The prompt must keep 'how you (the assistant) operate' instructions
    and 'what the vendor-facing script must sound like' instructions as two
    distinct blocks, not blended into one — so the model can't bleed the
    casual internal register into words a vendor actually hears."""
    prompt = prompt_template.build_talking_points_prompt({"vendor_name": "Alpha", "status": "full"})

    operator_marker = "HOW YOU OPERATE"
    script_marker = "WHAT THE SCRIPT ITSELF MUST SOUND LIKE"
    assert operator_marker in prompt
    assert script_marker in prompt
    # Distinct blocks, in order, operator voice first — not interleaved.
    assert prompt.index(operator_marker) < prompt.index(script_marker)
    # The operator block explicitly says the internal register never leaks
    # into the output; the script block explicitly says professional/never
    # slangy — the two instructions must not be the same sentence.
    operator_block = prompt[prompt.index(operator_marker):prompt.index(script_marker)]
    script_block = prompt[prompt.index(script_marker):]
    assert "Finance Bro" in operator_block
    assert "professional" in script_block.lower()
    assert "Finance Bro" not in script_block


def test_talking_points_prompt_never_bullet_format_instructions():
    """This task's revamp: plain spoken prose, not the old bullet-list
    structure — confirms the new instructions actually say so."""
    prompt = prompt_template.build_talking_points_prompt({"vendor_name": "Alpha"})
    assert "no bullet points" in prompt.lower()
    assert "AuthBridge Finance" in prompt


def test_zero_only_build_prompt_is_unchanged():
    """backend/ai_layer/talking_script.py's original zero-only template must
    stay completely untouched by this task."""
    fact_pack = {"vendor_name": "Alpha", "status": "zero"}
    prompt = prompt_template.build_prompt(fact_pack)
    assert "**Why we're not paying this cycle**" in prompt
    assert "**Talking points (step by step)**" in prompt


def test_build_prompt_instructs_indian_rupees_not_dollars():
    """Real bug: Gemini defaulted to '$' since nothing told it this is an
    Indian Rupee amount. The static instruction text itself must demand
    '₹' and explicitly ban '$'/USD/dollars — never just a passing mention."""
    prompt = prompt_template.build_prompt({"vendor_name": "Alpha", "status": "zero"})
    assert "₹" in prompt
    assert "Indian Rupees" in prompt
    assert "Indian-style digit grouping" in prompt
    assert "$" not in prompt


def test_talking_points_prompt_instructs_indian_rupees_not_dollars():
    prompt = prompt_template.build_talking_points_prompt({"vendor_name": "Alpha", "status": "full"})
    assert "₹" in prompt
    assert "Indian Rupees" in prompt
    assert "Indian-style digit grouping" in prompt
    assert "$" not in prompt


# ---- Banned internal-process phrasing (Sarath's flagged real example) -----

_BANNED_PHRASES = [
    "our current operational plan",
    "prioritizes invoices",
    "aging period",
    "as per the plan",
]


def test_talking_points_guardrails_ban_internal_process_phrasing():
    """Real flagged example: 'This allocation is based on our current
    operational plan, which prioritizes invoices within the most recent
    aging period.' None of these literally name a bucket/model term (rule 2
    already bans that), but they're exactly as internal-sounding and
    unwelcome — the new rule must call them out by name, not just gesture
    at the general idea."""
    prompt = prompt_template.build_talking_points_prompt({"vendor_name": "Alpha", "status": "full"})
    for phrase in _BANNED_PHRASES:
        assert phrase in prompt  # named as a banned example, not present as live output


def test_email_guardrails_ban_internal_process_phrasing():
    prompt = prompt_template.build_vendor_email_prompt({"vendor_name": "Alpha", "status": "full"})
    for phrase in _BANNED_PHRASES:
        assert phrase in prompt


# ---- build_vendor_email_prompt / format toggle -----------------------------


def test_build_vendor_email_prompt_has_email_shape():
    prompt = prompt_template.build_vendor_email_prompt({"vendor_name": "Alpha Freight", "status": "full"})
    assert "Dear [Vendor Name] team" in prompt
    assert "AuthBridge Finance Team" in prompt
    assert "no bullet points" in prompt.lower()
    assert "WHAT THE EMAIL ITSELF MUST SOUND LIKE" in prompt
    # Shares the same non-negotiable guardrails as the talking-points prompt.
    assert "Never state or imply a legal or binding commitment" in prompt
    assert "Never expose internal mechanics" in prompt


def test_build_vendor_email_prompt_instructs_indian_rupees_not_dollars():
    prompt = prompt_template.build_vendor_email_prompt({"vendor_name": "Alpha", "status": "full"})
    assert "₹" in prompt
    assert "Indian Rupees" in prompt
    assert "$" not in prompt


def test_generate_vendor_talking_points_defaults_to_talking_format(monkeypatch):
    calls = []
    monkeypatch.setattr(gemini_client, "generate_text", lambda prompt: calls.append(prompt) or "SCRIPT")
    companion.generate_vendor_talking_points(
        vendor_id=1, erp_code="V001", vendor_name="Alpha", category="normal", priority_tag="P2",
        aging={}, min_funds_breakdown={}, allocated_amount=100.0, required_amount=100.0, status="full",
    )
    assert "WHAT THE SCRIPT ITSELF MUST SOUND LIKE" in calls[0]


def test_generate_vendor_talking_points_email_format_uses_email_prompt(monkeypatch):
    calls = []
    monkeypatch.setattr(gemini_client, "generate_text", lambda prompt: calls.append(prompt) or "EMAIL")
    companion.generate_vendor_talking_points(
        format="email",
        vendor_id=1, erp_code="V001", vendor_name="Alpha", category="normal", priority_tag="P2",
        aging={}, min_funds_breakdown={}, allocated_amount=100.0, required_amount=100.0, status="full",
    )
    assert "WHAT THE EMAIL ITSELF MUST SOUND LIKE" in calls[0]
    assert "Dear [Vendor Name] team" in calls[0]


def test_qa_mode_fully_removed():
    """Sarath's scope correction: the broader Q&A path is gone, not just
    unused — confirms the deleted names really don't exist anymore."""
    assert not hasattr(prompt_template, "build_qa_prompt")
    assert not hasattr(context_builder, "build_plan_summary_fact_pack")
    assert not hasattr(companion, "answer_plan_summary_question")
    assert not hasattr(companion, "answer_vendor_question")

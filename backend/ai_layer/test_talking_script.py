"""Self-checks for the AI layer (backend/ai_layer/). Run with:
pytest backend/ai_layer/test_talking_script.py

Never calls the real Gemini API — gemini_client.generate_text is monkeypatched.
Uses a hand-built mixed-status allocations fixture rather than the real DB,
since this module never touches vendor/ledger data in the first place.
"""
import pytest

from backend.ai_layer import context_builder, gemini_client, talking_script
from backend.shared.enums import AllocationStatus

MIXED_ALLOCATIONS = [
    {
        "vendor_id": 1,
        "erp_code": "V001",
        "vendor_name": "Alpha Traders",
        "category": "normal",
        "outstanding_balance": 5000.0,
        "allocated_amount": 0.0,
        "status": AllocationStatus.ZERO.value,
        "required_amount": 5000.0,
        "rule": "normal_oldest_bucket_90_120",
        "oldest_bucket": "90_120",
        "oldest_bucket_age_days": 100,
    },
    {
        "vendor_id": 2,
        "erp_code": "V002",
        "vendor_name": "Beta Supplies",
        "category": "normal",
        "outstanding_balance": 3000.0,
        "allocated_amount": 3000.0,
        "status": AllocationStatus.FULL.value,
        "required_amount": 3000.0,
        "rule": "normal_oldest_bucket_30_60",
    },
    {
        "vendor_id": 3,
        "erp_code": "V003",
        "vendor_name": "Gamma Co",
        "category": "must_pay",
        "outstanding_balance": 0.0,
        "allocated_amount": 2000.0,
        "status": AllocationStatus.GUARANTEED.value,
        "required_amount": 2000.0,
        "rule": "must_pay_last_month_payable",
    },
    {
        "vendor_id": 4,
        "erp_code": "V004",
        "vendor_name": "Delta Ltd",
        "category": "normal",
        "outstanding_balance": 1000.0,
        "allocated_amount": 200.0,
        "status": AllocationStatus.PARTIAL.value,
        "required_amount": 1000.0,
        "rule": "normal_oldest_bucket_0_30",
    },
]


def test_only_zero_status_rows_are_selected():
    """Before/after: 4 mixed-status rows in -> only the 1 ZERO row survives."""
    rows = context_builder.zero_allocation_rows(MIXED_ALLOCATIONS)
    assert [r["vendor_id"] for r in rows] == [1]


def test_fact_pack_only_contains_whitelisted_already_computed_fields():
    row = MIXED_ALLOCATIONS[0]
    fact_pack = context_builder.build_fact_pack(row)
    assert set(fact_pack) <= set(context_builder._FACT_FIELDS)
    assert set(fact_pack).issubset(row.keys())  # nothing computed beyond the row itself
    assert fact_pack["vendor_name"] == "Alpha Traders"
    assert fact_pack["oldest_bucket"] == "90_120"


def test_generate_talking_scripts_only_calls_gemini_for_zero_rows(monkeypatch):
    calls = []

    def fake_generate_text(prompt):
        calls.append(prompt)
        return "**Why we're not paying this cycle**\n- test\n\n**Talking points (step by step)**\n- test"

    monkeypatch.setattr(talking_script.gemini_client, "generate_text", fake_generate_text)

    scripts = talking_script.generate_talking_scripts(MIXED_ALLOCATIONS)

    assert len(calls) == 1  # only the one ZERO-status vendor triggers a Gemini call
    assert "Alpha Traders" in calls[0] and "5000.0" in calls[0]  # only already-computed facts reached the prompt

    assert scripts == [
        {
            "vendor_id": 1,
            "erp_code": "V001",
            "vendor_name": "Alpha Traders",
            "script_text": "**Why we're not paying this cycle**\n- test\n\n**Talking points (step by step)**\n- test",
        }
    ]


def test_missing_api_key_raises_clear_error(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    with pytest.raises(ValueError, match="GEMINI_API_KEY is not set"):
        gemini_client.generate_text("irrelevant prompt")


def test_placeholder_api_key_raises_clear_error(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "your-gemini-api-key-here")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    with pytest.raises(ValueError, match="GEMINI_API_KEY is not set"):
        gemini_client.generate_text("irrelevant prompt")

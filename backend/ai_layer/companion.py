"""Orchestrates any-status vendor talking points: context_builder ->
prompt_template -> gemini_client, one Gemini call per request (isolates a
single vendor's failure, same as talking_script.py's own per-vendor
isolation).
"""
from . import context_builder, gemini_client, prompt_template

_PROMPT_BUILDERS = {
    "talking": prompt_template.build_talking_points_prompt,
    "email": prompt_template.build_vendor_email_prompt,
}


def generate_vendor_talking_points(format="talking", **vendor_fields):
    """vendor_fields matches
    context_builder.build_vendor_talking_points_fact_pack()'s own kwargs.
    `format`: "talking" (spoken phone script, default — unchanged behavior
    for every existing caller) or "email" (written email) — same fact pack,
    same guardrails, a different prompt shape (Talking/Email toggle task)."""
    fact_pack = context_builder.build_vendor_talking_points_fact_pack(**vendor_fields)
    prompt = _PROMPT_BUILDERS[format](fact_pack)
    return gemini_client.generate_text(prompt)

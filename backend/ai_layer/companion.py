"""Orchestrates any-status vendor talking points: context_builder ->
prompt_template -> gemini_client, one Gemini call per request (isolates a
single vendor's failure, same as talking_script.py's own per-vendor
isolation).
"""
from . import context_builder, gemini_client, prompt_template


def generate_vendor_talking_points(**vendor_fields):
    """vendor_fields matches
    context_builder.build_vendor_talking_points_fact_pack()'s own kwargs."""
    fact_pack = context_builder.build_vendor_talking_points_fact_pack(**vendor_fields)
    prompt = prompt_template.build_talking_points_prompt(fact_pack)
    return gemini_client.generate_text(prompt)

"""Orchestrates the zero-allocation talking script: context_builder ->
prompt_template -> gemini_client, one Gemini call per vendor (isolates a
single vendor's failure from the rest)."""
from . import context_builder, gemini_client, prompt_template


def generate_talking_scripts(vendor_allocations):
    """vendor_allocations: same shape build_weekly_view() consumes. Returns
    a list of {vendor_id, erp_code, vendor_name, script_text}, one per
    ZERO-status vendor only."""
    scripts = []
    for row in context_builder.zero_allocation_rows(vendor_allocations):
        fact_pack = context_builder.build_fact_pack(row)
        prompt = prompt_template.build_prompt(fact_pack)
        script_text = gemini_client.generate_text(prompt)
        scripts.append(
            {
                "vendor_id": row["vendor_id"],
                "erp_code": row["erp_code"],
                "vendor_name": row["vendor_name"],
                "script_text": script_text,
            }
        )
    return scripts

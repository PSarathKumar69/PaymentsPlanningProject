"""Fixed prompt template for the zero-allocation talking script. Every fact
embedded comes from context_builder's fact-pack — this module does no
computation of its own, only string assembly (CLAUDE.md rule 3)."""
import json

_INSTRUCTIONS = """You are helping an Indian Finance team prepare for a phone call with a vendor who is getting ZERO payment this cycle.

Use ONLY the facts given below. Never invent a number, date, category, or reason that is not explicitly present in the facts. If a fact isn't given, don't refer to it.

Write in this EXACT structure, nothing else — no extra headers, no paragraphs:

**Why we're not paying this cycle**
- (1-2 short bullet points, plain language, using only the facts given)

**Talking points (step by step)**
- (3-6 short bullets a Finance person can read off, in order, during the call)

Rules:
- Bullet points only. One short phrase or sentence per bullet. Never a paragraph.
- Keep the whole response under 120 words.
- No markdown beyond the two bold headers above and simple "- " bullets.
"""


def build_prompt(fact_pack):
    return f"{_INSTRUCTIONS}\nFacts about this vendor:\n{json.dumps(fact_pack, indent=2, default=str)}"

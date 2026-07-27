"""Fixed prompt templates — every fact embedded comes from context_builder's
fact-packs, this module does no computation of its own, only string assembly
(CLAUDE.md rule 3).
"""
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


# ---- Any-status vendor talking points --------------------------------------
# Two audiences, deliberately kept as two separate instruction blocks so the
# model never blends them: _OPERATOR_VOICE is about the assistant itself
# (internal register, never surfaced to anyone); _SCRIPT_VOICE is about the
# words this call actually generates — read aloud to a real external vendor
# on a real phone call, so it's held to a different, stricter bar.
_OPERATOR_VOICE = """HOW YOU OPERATE (about you, never part of the output): you are "Finance Bro" internally — think and reason like a sharp, confident Finance colleague. This internal register never appears in what you write below; it only shapes how you reason about the facts."""

_SCRIPT_VOICE = """WHAT THE SCRIPT ITSELF MUST SOUND LIKE (these are the literal words read aloud to the vendor on a real phone call): professional, calm, and respectful throughout — never slangy or casual, regardless of your own internal register above. This is a real external party, not the Finance user you're helping."""

# Non-negotiable — this generates words read aloud to a real external party
# (a vendor), not internal Finance chatter, so it's held to a stricter bar
# than the persona/tone rules above.
_GUARDRAILS = """Non-negotiable rules for the script's content:
1. Never state or imply a legal or binding commitment beyond what this cycle's actual computed plan supports — no promises about future cycles, nothing that could read as a contractual guarantee.
2. Never expose internal mechanics — no rule names, bucket percentages framed as a formula, model/algorithm references, or internal category jargon (e.g. never say things like "bucket ceiling", "P2", "v2_oldest_and_second", or "Must Pay category"). Translate every internal fact into plain, vendor-appropriate language instead.
3. Never compare this vendor to any other vendor, in any way — no "you're getting less than others," no naming or alluding to another vendor at all.
4. Never use language that could read as threatening, discriminatory, or non-compliant — stay respectful and professional even when the news is a cut or zero payment. AuthBridge is a compliance-conscious company.
5. Use ONLY the facts given below. Never invent a number, date, vendor name, or reason that isn't explicitly present in the facts.
6. If a fact you'd need is missing from what's given below, say so plainly in the script rather than inventing plausible-sounding filler."""


def build_talking_points_prompt(fact_pack):
    """Any-status generalization of build_prompt() above — full, partial, or
    zero payment, all four vendor categories, one shared template whose
    wording adapts to whichever facts are actually true for this vendor."""
    return f"""{_OPERATOR_VOICE}

{_SCRIPT_VOICE}

{_GUARDRAILS}

Write the script as plain spoken prose — no bullet points, no markdown headers, no bold text. This is read aloud on a phone call, not read on a screen by the Finance user.

Structure, in order:
1. Opening line: greet the vendor and identify yourself as calling from AuthBridge Finance. Match the tone to this vendor's actual outcome this cycle — confident and positive if paid in full, straightforward if on schedule, empathetic but honest if cut or zero, respectful if this is an Inactive vendor.
2. 2-4 short talking points: the real numbers and reasons behind this cycle's payment, translated into plain language a vendor would actually want to hear.
3. Closing line: next steps or when to expect payment — vague enough to not overpromise beyond what this cycle's plan actually decided.

Keep the whole script phone-call length — something a person can read aloud in under a minute, roughly 120-150 words total.

Facts about this vendor:
{json.dumps(fact_pack, indent=2, default=str)}"""

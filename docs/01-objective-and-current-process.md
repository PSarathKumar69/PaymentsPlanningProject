# Objective & Current Process

## Objective

Build a tool that helps Finance decide, every payment cycle, how much to pay to which vendors — especially in months when available funds are less than total amounts owed. The tool generates data-driven, suggested payment plans; it does not execute payments or override Finance's judgment. Finance reviews the suggestions and makes the final call every time.

## Current manual process (what exists today, before this tool)

Today, vendor payments are planned entirely through manual judgement, with no written framework, formula, or system behind it:

- Every payment cycle, Finance manually reviews the vendor list and decides who gets paid, how much (in full, partially, or held back), based on individual experience and relationship knowledge — not a fixed formula or percentage split.
- Prioritization — who gets paid first when funds fall short — is decided mentally each time, based on the finance person's own sense of urgency and vendor relationship, not any documented ranking or rule.
- This knowledge lives with individuals rather than in any system or written process, which makes it slow, inconsistent from cycle to cycle, and difficult to hand over or scale.

This tool does not need to replicate this process faithfully — the goal is to replace it with something consistent and auditable. Where the code needs a default behavior and the manual process gives no useful signal, prefer the explicit rules in the other docs over guessing what a human might have done.

## Success metrics

1. **Planning time** — reduction in time Finance spends building a payment plan each cycle, compared to today's manual process.
2. **Adoption** — percentage of payment cycles where Finance uses the model's suggestion as the basis for their plan.
3. **Plan relevance** — percentage of suggested payments Finance accepts as-is or with only minor adjustment.
4. **Payables trend** — whether the growth rate of total outstanding dues slows down compared to the historical trend (only relevant if Finance chooses to act on suggestions aimed at reducing it; the tool itself doesn't force this).

None of these are things the code directly optimizes for at runtime — they're how the project's success gets judged later. Don't build tracking for these unless asked.

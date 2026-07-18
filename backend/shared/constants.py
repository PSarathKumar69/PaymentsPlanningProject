"""Shared tolerance constants for the deterministic layer.

Real currency values carry ordinary float/rounding noise; every equality or
inequality check against a rupee amount should absorb up to this much drift
rather than tripping on it (docs/07's ledger-integrity formula must still
reconcile, but not to sub-paisa precision).
"""
MONEY_EPSILON = 1.0

# PlanRun.model_used literal for Model 1's own generate-plan-and-weekly-view
# route (backend/api/routers/model1.py). Model 1 is now the sole driver of
# payment_status/cycle_allocation (backend/shared/payment_logging.py) — that
# lookup needs this exact same value, so it's defined once here rather than
# re-typed as a coincidental ad hoc string in two files (CLAUDE.md rule 7).
MODEL_1_PLAN_RUN_LABEL = "model1"

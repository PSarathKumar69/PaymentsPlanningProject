"""Shared tolerance constants for the deterministic layer.

Real currency values carry ordinary float/rounding noise; every equality or
inequality check against a rupee amount should absorb up to this much drift
rather than tripping on it (docs/07's ledger-integrity formula must still
reconcile, but not to sub-paisa precision).
"""
MONEY_EPSILON = 1.0

# PlanRun.model_used literal for New Model 2's own generate-plan-and-weekly-
# view route (backend/api/routers/new_model_2.py, docs/14-new-model-2.md).
# "model5" (not "new_model2") so it still matches plan_history.py's shared
# "model\d+" family-prefix regex unmodified — is_latest_plan_run()/
# plan_runs_for_model() work with zero changes there.
NEW_MODEL_2_PLAN_RUN_LABEL = "model5"

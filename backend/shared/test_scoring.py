"""Tests against the real ingested database (backend/db/app.db) — no synthetic data.

Requires the ingestion step to have already run (see backend/ingestion/load_excel.py).

Scoring (score_vendors) is shared infra — backend/weekly_planning/planner.py
calls it directly, and New Model 2's router reaches it transitively through
build_weekly_view().
"""
import math

from backend.shared.scoring import DEFAULT_WEIGHTS, score_vendors
from backend.shared.aging import AGING_BUCKETS

_BUCKET_ORDER = [label for label, _, _ in AGING_BUCKETS]  # freshest -> oldest


def test_weights_sum_to_one():
    assert sum(v for v, _ in DEFAULT_WEIGHTS.values()) == 1.0


def test_scores_all_83_real_vendors_with_no_missing_values():
    df = score_vendors()
    assert len(df) == 83
    for col in ["score", "aging_score", "outstanding_score", "consistency_score", "trend_score"]:
        assert df[col].notna().all(), f"{col} has a null for at least one vendor"
        assert not df[col].apply(math.isnan).any(), f"{col} has a NaN for at least one vendor"


def test_scores_are_ranked_descending():
    df = score_vendors()
    assert (df["score"].diff().dropna() <= 1e-9).all()


def test_worse_vendor_outranks_better_vendor():
    """An old-large-inconsistent-worsening vendor should outrank a
    recent-small-consistent-improving one. As of today, real aging is
    concentrated in 120+ (the historical sheet runs through May-26 only) —
    so "recent" here means this dataset's freshest available bucket, not
    a fabricated 0-30 case.
    """
    df = score_vendors()
    present_buckets = [b for b in _BUCKET_ORDER if b in df["aging_bucket"].values]
    freshest_bucket, oldest_bucket = present_buckets[0], present_buckets[-1]
    worst = df[df["aging_bucket"] == oldest_bucket].sort_values(
        ["outstanding_balance", "consistency_cv", "trend_metric"]
    ).iloc[-1]
    best = df[df["aging_bucket"] == freshest_bucket].sort_values(
        ["outstanding_balance", "consistency_cv", "trend_metric"]
    ).iloc[0]
    assert worst["score"] > best["score"]

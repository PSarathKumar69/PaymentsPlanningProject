"""Thin wrapper around the google-genai SDK — the only place in ai_layer/
that talks to Gemini directly. No other ai_layer module imports google.genai.
"""
import os

from google import genai

_PLACEHOLDER = "your-gemini-api-key-here"
DEFAULT_MODEL = "gemini-2.5-flash-lite"


def _model_name():
    return os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)


def _check_api_key():
    # SDK precedence: GOOGLE_API_KEY wins over GEMINI_API_KEY if both are set.
    key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not key or key == _PLACEHOLDER:
        raise ValueError("GEMINI_API_KEY is not set — add your real key to .env")


def generate_text(prompt):
    """Calls Gemini once with `prompt`, returns the plain response text.

    Retries on transient failure (5xx/429) are handled by the SDK itself,
    not reimplemented here — checked, not thin: 5 attempts, exponential
    backoff from 1s up to a 60s cap, jittered (google.genai.types.
    HttpRetryOptions' own defaults). Widening this further would mostly
    just make a single upload request block the caller's browser longer
    per attempt, for little real benefit against a genuine outage — see
    ai_column_mapper.py's AIMappingUnavailableError for what happens once
    retries are exhausted (fails loudly, never a silent fallback).
    """
    _check_api_key()
    client = genai.Client()
    response = client.models.generate_content(model=_model_name(), contents=prompt)
    return response.text

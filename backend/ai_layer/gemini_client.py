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
    """Calls Gemini once with `prompt`, returns the plain response text."""
    _check_api_key()
    client = genai.Client()
    response = client.models.generate_content(model=_model_name(), contents=prompt)
    return response.text

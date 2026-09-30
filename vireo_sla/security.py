"""Small, dependency-free safety helpers used by the app, the AI layer and the exports."""
from __future__ import annotations

import os
import re

import pandas as pd

SECRET_ENV_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_WORKSPACE_ID")
_KEY_PATTERN = re.compile(r"sk-ant-[A-Za-z0-9_\-]{6,}")

# Anything that would make rendered markdown fetch or link to something, or inject markup.
UNSAFE_MARKUP = re.compile(r"https?://|www\.|\]\(|!\[|<\s*[A-Za-z/!]|javascript:|data:", re.IGNORECASE)

# Spreadsheet formula triggers (OWASP CSV injection guidance).
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")

# Roster labels (names, teams, shifts) must look like labels, not instructions or numbers.
_SAFE_LABEL = re.compile(r"[A-Za-z][A-Za-z .&'\-]{0,59}")


def secret_values() -> list[str]:
    """Values of credential env vars that are long enough to be meaningful secrets."""
    return [v for k in SECRET_ENV_VARS if (v := os.environ.get(k)) and len(v) >= 8]


def redact_secrets(text: str) -> str:
    """Remove credential values and anything shaped like an Anthropic key from text shown to users."""
    for v in secret_values():
        text = text.replace(v, "[REDACTED]")
    return _KEY_PATTERN.sub("[REDACTED]", text)


def contains_secret(text: str) -> bool:
    return any(v in text for v in secret_values()) or bool(_KEY_PATTERN.search(text))


def safe_label(value, fallback: str) -> str:
    """Return a roster label only if it is a plain label; otherwise the fallback.

    Stops roster text from carrying instructions or numbers into the LLM fact sheet (where
    numbers would also be whitelisted by the grounding check).
    """
    s = str(value) if value is not None else ""
    return s if _SAFE_LABEL.fullmatch(s) else fallback


def csv_safe(df: pd.DataFrame) -> pd.DataFrame:
    """Neutralise spreadsheet formula injection in text columns by prefixing an apostrophe.

    Numeric columns are untouched, so every count, rate and rupee figure is unchanged.
    """
    out = df.copy()
    for col in out.columns:
        if out[col].dtype == object or pd.api.types.is_string_dtype(out[col]):
            out[col] = out[col].map(
                lambda v: "'" + v if isinstance(v, str) and v.startswith(_FORMULA_START) else v)
    return out

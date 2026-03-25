from __future__ import annotations

import os
from contextvars import ContextVar, Token
from typing import Optional

_DEFAULT_LLM_LANGUAGE_ENV_VARS = (
    "LLM_RESPONSE_LANGUAGE",
    "LLM_LANGUAGE",
    "DEFAULT_LLM_LANGUAGE",
)

_REQUEST_LLM_LANGUAGE: ContextVar[Optional[str]] = ContextVar("request_llm_language", default=None)

_LANGUAGE_ALIASES = {
    "en": "English",
    "english": "English",
    "es": "Spanish",
    "español": "Spanish",
    "espanol": "Spanish",
    "spanish": "Spanish",
    "fr": "French",
    "french": "French",
    "pt": "Portuguese",
    "portuguese": "Portuguese",
    "pt-br": "Portuguese (Brazil)",
    "pt_br": "Portuguese (Brazil)",
}


def normalize_language(value: Optional[str], *, default: str = "English") -> str:
    raw = str(value or "").strip()
    if not raw:
        return default

    normalized = raw.strip().lower()
    mapped = _LANGUAGE_ALIASES.get(normalized)
    if mapped:
        return mapped
    return raw


def get_default_llm_language() -> str:
    request_value = _REQUEST_LLM_LANGUAGE.get()
    if request_value and request_value.strip():
        return normalize_language(request_value)
    for env_var in _DEFAULT_LLM_LANGUAGE_ENV_VARS:
        value = os.getenv(env_var)
        if value and value.strip():
            return normalize_language(value)
    return "English"


def set_request_llm_language(value: Optional[str]) -> Token[Optional[str]]:
    return _REQUEST_LLM_LANGUAGE.set(value)


def reset_request_llm_language(token: Token[Optional[str]]) -> None:
    _REQUEST_LLM_LANGUAGE.reset(token)


def build_llm_language_system_prompt(language: Optional[str] = None) -> str:
    effective = normalize_language(language, default=get_default_llm_language())
    return (
        f"Respond in {effective}. "
        "If you output JSON, keep JSON keys, IDs, and code as-is; translate only natural-language text values."
    )

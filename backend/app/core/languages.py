"""Supported-language registry.

Single source of truth for every locale the platform understands. Adding
a new Indian language (Tamil, Bengali, Gujarati, …) is a one-line change
here plus a new messages bundle in the frontend — no other backend
service needs to be touched because everything reads through
``normalize_language`` and ``language_label``.

Each entry:
    label   -> the English-language name the LLM sees as target_language.
               Generation/translation prompts read this verbatim.
    native  -> the language's own name. Used by the LanguageSwitcher UI.
    iso     -> the BCP-47 / ISO-639 code passed to ``toLocaleDateString``
               on the frontend (drives IST formatting + numeric formats).
    aliases -> tolerated user-facing variants for the URL parameter and
               legacy stored values (Hindi could appear as "hi", "hin",
               "hindi", "हिन्दी"). Normalised at the door.

Order in the dict matters: the LanguageSwitcher and the SOP-translate
dropdown render in registry order.
"""
from __future__ import annotations

from typing import Dict, Iterable, TypedDict


class LanguageEntry(TypedDict):
    label: str
    native: str
    iso: str
    aliases: tuple[str, ...]


# Order: English baseline first, then Indian languages.
SUPPORTED_LANGUAGES: Dict[str, LanguageEntry] = {
    "en": {
        "label": "English",
        "native": "English",
        "iso": "en-IN",
        "aliases": ("en", "eng", "english"),
    },
    "hi": {
        "label": "Hindi",
        "native": "हिन्दी",
        "iso": "hi-IN",
        "aliases": ("hi", "hin", "hindi", "हिंदी", "हिन्दी"),
    },
    "mr": {
        "label": "Marathi",
        "native": "मराठी",
        "iso": "mr-IN",
        "aliases": ("mr", "mar", "marathi", "मराठी"),
    },
    # NEXT (Tamil, Bengali, Telugu, Gujarati, Kannada, Punjabi): add a new
    # dict entry here + drop a partial messages/<code>.json on the frontend
    # — the rest of the platform picks it up automatically.
}

DEFAULT_LANGUAGE = "en"


def supported_codes() -> list[str]:
    """Stable list of canonical 2-letter codes in registry order."""
    return list(SUPPORTED_LANGUAGES.keys())


def normalize_language(value: str | None) -> str:
    """Coerce any user-facing string to a canonical 2-letter code.

    Unknown values fall back to the platform default (English) so the
    LLM never sees an empty target_language. Case + whitespace tolerant.
    """
    if not value:
        return DEFAULT_LANGUAGE
    normalized = value.strip().lower().replace("_", "-").split("-", 1)[0]
    for code, entry in SUPPORTED_LANGUAGES.items():
        if normalized == code:
            return code
        if normalized in entry["aliases"]:
            return code
        # Native scripts may arrive as-is in legacy rows; check the raw
        # value too (case-sensitive comparison, but native names are
        # already lower-case-equivalent in Devanagari).
        if value.strip() in entry["aliases"]:
            return code
    return DEFAULT_LANGUAGE


def language_label(code: str | None) -> str:
    """English-language name of the language. This is what the LLM sees."""
    return SUPPORTED_LANGUAGES[normalize_language(code)]["label"]


def native_name(code: str | None) -> str:
    """The language's own script-native name. UI display only."""
    return SUPPORTED_LANGUAGES[normalize_language(code)]["native"]


def iso_locale(code: str | None) -> str:
    """BCP-47 locale for ``toLocaleDateString`` etc."""
    return SUPPORTED_LANGUAGES[normalize_language(code)]["iso"]


def registry_items() -> Iterable[tuple[str, LanguageEntry]]:
    """Iterate (code, entry) pairs in registry order. Used by API listing."""
    return SUPPORTED_LANGUAGES.items()

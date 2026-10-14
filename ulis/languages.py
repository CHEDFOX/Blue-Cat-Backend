"""Language codes and the PostgreSQL text-search configuration used for each language.

Languages without a PostgreSQL stemmer fall back to the ``simple`` configuration
(exact-token matching).  That is a known limitation for, e.g., Hindi and other Indic
languages and is exactly where Phase 3 multilingual embeddings are needed.
"""

from __future__ import annotations

import re

ISO639_2_TO_BCP47 = {
    "eng": "en",
    "fra": "fr",
    "fre": "fr",
    "deu": "de",
    "ger": "de",
    "spa": "es",
    "ita": "it",
    "por": "pt",
    "nld": "nl",
    "dut": "nl",
    "swe": "sv",
    "dan": "da",
    "fin": "fi",
    "hun": "hu",
    "ron": "ro",
    "rum": "ro",
    "rus": "ru",
    "tur": "tr",
    "nor": "no",
    "hin": "hi",
    "ara": "ar",
    "zho": "zh",
    "chi": "zh",
    "jpn": "ja",
    "cym": "cy",
    "wel": "cy",
    "gle": "ga",
    "ell": "el",
    "gre": "el",
    "pol": "pl",
    "ces": "cs",
    "cze": "cs",
    "ben": "bn",
    "tam": "ta",
    "urd": "ur",
}

# BCP 47 primary language -> PostgreSQL regconfig.
SEARCH_CONFIGS = {
    "en": "english",
    "fr": "french",
    "de": "german",
    "es": "spanish",
    "it": "italian",
    "pt": "portuguese",
    "nl": "dutch",
    "sv": "swedish",
    "da": "danish",
    "fi": "finnish",
    "hu": "hungarian",
    "ro": "romanian",
    "ru": "russian",
    "tr": "turkish",
    "no": "norwegian",
    "el": "greek",
}
FALLBACK_CONFIG = "simple"
_CONFIG_RE = re.compile(r"^[a-z_]+$")


def to_bcp47(code: str) -> str:
    code = code.strip().lower()
    if len(code) == 3:
        return ISO639_2_TO_BCP47.get(code, code)
    return code


def search_config(language: str) -> str:
    return SEARCH_CONFIGS.get(language.split("-")[0].lower(), FALLBACK_CONFIG)


def all_search_configs() -> list[str]:
    configs = sorted({*SEARCH_CONFIGS.values(), FALLBACK_CONFIG})
    # Defensive: these names are interpolated into SQL as regconfig literals.
    assert all(_CONFIG_RE.match(c) for c in configs)
    return configs

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

# Han, Hiragana, Katakana and Hangul are written without spaces between words, and PostgreSQL
# has no word segmenter for them: such runs are indexed and queried as overlapping character
# bigrams ("個人情報" -> "個人 人情 情報"), a standard, dictionary-free approach.
_CJK_RUN = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uac00-\ud7af\u31f0-\u31ff]+")


def _bigrams(run: str) -> str:
    return run if len(run) == 1 else " ".join(run[i : i + 2] for i in range(len(run) - 1))


def segment_for_search(text: str) -> str | None:
    """Text prepared for full-text search when it contains CJK runs; None when unchanged."""
    if not _CJK_RUN.search(text):
        return None
    return _CJK_RUN.sub(lambda m: f" {_bigrams(m.group(0))} ", text)


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

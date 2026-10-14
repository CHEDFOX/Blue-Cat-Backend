"""Input hygiene for text that is DATA, never instructions (the case file, the question)."""

from __future__ import annotations

import re
import secrets
import unicodedata

# Format characters that are invisible or reorder text (bidi overrides, zero-width space, word joiner,
# BOM, the "tag" block used for invisible prompt injection). ZWJ/ZWNJ stay: Persian and Indic scripts need them.
_INVISIBLE = re.compile("[​⁠﻿‪-‮⁦-⁩\U000e0000-\U000e007f]")
_TAG_LIKE = re.compile(r"<\s*/?\s*(untrusted_case_file|question|system|assistant|human|user)\b", re.IGNORECASE)


def clean_text(value: str) -> str:
    """NFC-normalise and drop control / invisible characters; keep ordinary whitespace."""
    value = unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n"))
    value = _INVISIBLE.sub("", value)
    return "".join(ch for ch in value if ch in "\n\t" or unicodedata.category(ch) != "Cc")


def neutralise_markup(value: str) -> str:
    """Stop text from imitating the delimiters / roles the prompt uses (``</untrusted_case_file>``)."""
    return _TAG_LIKE.sub(lambda m: "[" + m.group(0)[1:].lstrip(), value)


def wrap_untrusted(label: str, value: str, token: str | None = None) -> str:
    """Wrap ``value`` in unguessable delimiters; ``label`` names what it is."""
    token = token or secrets.token_hex(8)
    body = neutralise_markup(clean_text(value))
    return f'<{label} id="{token}">\n{body}\n</{label} id="{token}">'

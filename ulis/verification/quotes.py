"""Exact-quotation checks.

``exact`` means the quote occurs verbatim in the stored text.  ``match_after_normalisation``
means it only matches after whitespace, quotation-mark or dash normalisation - reported with
the normalisations applied, because a court filing may need the verbatim form.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

_QUOTES = str.maketrans({c: '"' for c in "\u201c\u201d\u201e\u00ab\u00bb"} | {c: "'" for c in "\u2018\u2019\u2032"})
_DASHES = str.maketrans({"\u2013": "-", "\u2014": "-", "\u2212": "-", "\u00a0": " "})


@dataclass
class QuoteResult:
    result: Literal["exact", "match_after_normalisation", "not_found"]
    offset: int | None = None
    normalisations: list[str] = field(default_factory=list)


def check_quote(quote: str, text: str) -> QuoteResult:
    quote = quote.strip()
    if not quote:
        return QuoteResult("not_found")
    idx = text.find(quote)
    if idx >= 0:
        return QuoteResult("exact", offset=idx)
    applied: list[str] = []
    q, t = quote, text
    steps = [
        ("whitespace", lambda s: re.sub(r"\s+", " ", s)),
        ("quotation_marks", lambda s: s.translate(_QUOTES)),
        ("dashes_and_spaces", lambda s: s.translate(_DASHES)),
    ]
    for name, fn in steps:
        nq, nt = fn(q), fn(t)
        if (nq, nt) != (q, t):
            applied.append(name)
        q, t = nq, nt
        if q in t:
            return QuoteResult("match_after_normalisation", normalisations=applied)
    return QuoteResult("not_found", normalisations=applied)

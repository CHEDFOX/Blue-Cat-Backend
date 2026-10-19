"""Query text: cleaning, and the clauses of a web-search-style query.

The lexical stage hands the whole query to PostgreSQL's ``websearch_to_tsquery``, which requires every word. When that
finds too little, search relaxes the query to "most of the words", and for that it needs the query's clauses as
``websearch_to_tsquery`` sees them: a word, a "quoted phrase", ``a or b``, and ``-word`` (excluded). Each clause is
still parsed by PostgreSQL itself, one at a time, so stemming and stopwords stay exactly those of the full query.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# C0 controls (a NUL byte cannot be sent to PostgreSQL at all) and DEL become spaces; tab and newlines are kept.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# Invisible characters that copy-paste carries into words: soft hyphen, zero-width space, word joiner, BOM.
_INVISIBLE = re.compile("[­​⁠﻿]")
_TOKEN = re.compile(r'(-?)"([^"]*)"?|(\S+)')

# Written without spaces between words; matched as overlapping character pairs (see ulis.languages).
_CJK = r"぀-ヿ㐀-䶿一-鿿豈-﫿가-힯ㇰ-ㇿ"
_CJK_WORD = re.compile(rf"[{_CJK}]")
# Kanji, katakana and hangul carry the content; hiragana runs are mostly particles and inflections.
_CJK_CONTENT = re.compile(r"[゠-ヿ㐀-䶿一-鿿豈-﫿가-힯ㇰ-ㇿ]+")
_HIRAGANA = re.compile(r"[぀-ゟ]+")


def clean_query(q: str) -> str:
    return _INVISIBLE.sub("", _CONTROL.sub(" ", q)).strip()


@dataclass(frozen=True)
class Clause:
    text: str  # what is passed to websearch_to_tsquery (or segmented, for CJK)
    label: str  # how the clause is shown to the user
    negated: bool = False
    quoted: bool = False


def parse_clauses(q: str) -> list[Clause]:
    """Split a query the way ``websearch_to_tsquery`` reads it; ``a or b`` stays one clause."""
    clauses: list[Clause] = []
    pending_or = False
    for dash, quoted, word in (m.groups() for m in _TOKEN.finditer(q)):
        if word is not None and word.lower() == "or":
            pending_or = bool(clauses)
            continue
        if word is not None and word.startswith("-") and len(word) > 1:
            clause = Clause(word[1:], word[1:], negated=True)
        elif word is not None:
            clause = Clause(word, word)
        else:
            if not quoted.strip():
                pending_or = False
                continue
            clause = Clause(f'"{quoted}"', f'"{quoted}"', negated=bool(dash), quoted=True)
        if pending_or and not clause.negated and not clauses[-1].negated:
            prev = clauses.pop()
            clause = Clause(f"{prev.text} or {clause.text}", f"{prev.label} OR {clause.label}")
        clauses.append(clause)
        pending_or = False
    return clauses


def has_cjk(text: str) -> bool:
    return bool(_CJK_WORD.search(text))


def cjk_units(text: str) -> list[tuple[str, str]]:
    """The character pairs (and single characters) of the content words of an unspaced CJK text, each with its word.

    "個人情報の第三者提供" -> 個人 人情 情報 (word 個人情報), 第三 三者 者提 提供 (word 第三者提供): the particle の is
    dropped, and no pair spans it. Text without kanji, katakana or hangul keeps its hiragana runs.
    """
    runs = _CJK_CONTENT.findall(text) or _HIRAGANA.findall(text)
    units: dict[str, str] = {}
    for run in runs:
        for unit in [run] if len(run) == 1 else [run[i : i + 2] for i in range(len(run) - 1)]:
            units.setdefault(unit, run)
    return list(units.items())

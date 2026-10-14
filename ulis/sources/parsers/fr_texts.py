"""Strict recognition of references to French codes, laws and the Constitution in decisions.

A reference is made only when one or more article numbers are directly followed by the name of a text of the
tables in ``fr_texts_data`` (``articles 1569, 1570 et 1574 du code civil``, ``article L. 761-1 du code de justice
administrative``, ``article 22 de la loi n° 78-17``, ``article 61-1 de la Constitution``). Each reference is an
article of exactly one consolidated text, with the key ``/fr/legi/<id>/~art-<number>`` that the ``dila_legi_xml``
parser gives that article.
Anything else (several texts in one phrase, "du même code", paragraphs ("alinéas") named before the article, a text
without an entry in the tables) is not linked.

A few codes exist in two versions (the 1810 and the 1994 Code pénal; the 1807 and the 2000 Code de commerce); a decision
is linked to the current code only when it was made after that code came into force (``SINCE``).
"""

from __future__ import annotations

import re
from datetime import date

from ulis.sources.normalized import ParsedRef
from ulis.sources.parsers.dila_legi import article_slug, work_uri
from ulis.sources.parsers.fr_texts_data import CODES, LAWS

CONSTITUTION = "JORFTEXT000000571356"
# Codes that replaced a code of the same name: decisions made before are about the earlier code.
SINCE = {
    "LEGITEXT000006070719": date(1994, 3, 1),  # Code pénal
    "LEGITEXT000005634379": date(2000, 9, 21),  # Code de commerce
}
_NUM = (
    r"(?:(?:L\.?\s?O|L|R|D|A)\.?\s?)?\d+(?:\s?-\s?\d+)*"
    r"(?:\s?(?:bis|ter|quater|quinquies|sexies|septies|octies|nonies|decies))?(?:er)?(?![\w-])"
)
_LIST = rf"{_NUM}(?:\s*(?:,|et|ou)\s*{_NUM})*"
_ALINEA = r"(?:\s*,?\s*(?:alinéas?|al\.)\s*(?:\d+|[a-zéè]+)(?:\s*(?:,|et)\s*(?:\d+|[a-zéè]+))*\s*,?)?"
_TARGET_PREFIX = rf"\b[Aa]rticles?\s+(?P<nums>{_LIST}){_ALINEA}\s+(?:du|de la|de l['’])\s*"


def _name_pattern(name: str) -> str:
    return re.escape(name).replace(r"\ ", r"\s+").replace("'", "['’]")


_CODE_NAMES = "|".join(_name_pattern(n) for n in sorted(CODES, key=len, reverse=True))
CODE_REFERENCE = re.compile(rf"{_TARGET_PREFIX}(?P<code>{_CODE_NAMES})(?![\w-])", re.I)
CONSTITUTION_REFERENCE = re.compile(rf"{_TARGET_PREFIX}Constitution(?![\w-])", re.I)
_LAW_KINDS = "loi organique|loi constitutionnelle|loi|ordonnance|décret-loi"
LAW_REFERENCE = re.compile(
    rf"{_TARGET_PREFIX}(?P<kind>{_LAW_KINDS})\s+n\s*°\s*(?P<ref>\d{{2,4}}-\d+)",
    re.I,
)
_NUMBER_IN_LIST = re.compile(_NUM)


def _numbers(listing: str) -> list[str]:
    out = []
    for match in _NUMBER_IN_LIST.finditer(listing):
        num = re.sub(r"\s+", " ", match.group(0)).strip()
        out.append(num[:-2] if num.endswith("er") and num[:-2].isdigit() else num)
    return out


def _refs(text_id: str, listing: str, phrase: str) -> list[ParsedRef]:
    refs = []
    for num in _numbers(listing):
        eid = f"art-{article_slug(num)}"
        refs.append(ParsedRef(f"{work_uri(text_id)}/~{eid}", phrase, "cites", "pattern"))
    return refs


def statute_refs(text: str, on: date | None) -> list[ParsedRef]:
    """References to articles of the tabled texts found in ``text``; ``on`` is the date of the decision."""
    refs: dict[str, ParsedRef] = {}
    for match in CODE_REFERENCE.finditer(text):
        cid = CODES[re.sub(r"\s+", " ", match["code"].lower().replace("’", "'"))]
        since = SINCE.get(cid)
        if since is not None and (on is None or on < since):
            continue
        for ref in _refs(cid, match["nums"], re.sub(r"\s+", " ", match.group(0))):
            refs.setdefault(ref.href, ref)
    for match in CONSTITUTION_REFERENCE.finditer(text):
        for ref in _refs(CONSTITUTION, match["nums"], re.sub(r"\s+", " ", match.group(0))):
            refs.setdefault(ref.href, ref)
    for match in LAW_REFERENCE.finditer(text):
        law = LAWS.get((match["kind"].lower(), match["ref"]))
        if law is not None:
            for ref in _refs(law, match["nums"], re.sub(r"\s+", " ", match.group(0))):
                refs.setdefault(ref.href, ref)
    return list(refs.values())

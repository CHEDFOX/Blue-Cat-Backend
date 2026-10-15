"""Slovenia module: citation conventions for acts published in Uradni list RS.

Slovenian legislative practice cites from the smallest unit to the largest, the cited unit in the nominative and every
container in the genitive, with ordinal words for paragraphs:

* ``3. člen ZJRM-2`` - article (člen); an inserted article is ``12.a člen``;
* ``prvi odstavek 3. člena ZJRM-2`` - paragraph (odstavek) of an article;
* ``2. točka prvega odstavka 3. člena ZJRM-2`` - point (točka) of a paragraph; ``2. točka 3. člena`` when the
  article has no
  numbered paragraphs;
* ``podtočka a) 2. točke prvega odstavka 3. člena ZJRM-2`` - sub-point (podtočka);
* units outside the articles: ``uvodni del`` (decree and title), ``sklepni del`` (signature lines), ``priloga 1``.

The act is named by the abbreviation printed in the last parenthesis of its title ("(ZJRM-2)"); a title without one is
cited in the genitive when it is regular (``Zakon o ...`` -> ``Zakona o ...``), else the title is put first:
``<title>, 3. člen``. Paragraphs numbered above 20 are written ``21. odstavek`` (no ordinal word).

Formatting conventions only; to be confirmed by a Slovenian reviewer before the SI profile is "reviewed".
"""

from __future__ import annotations

import re
from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from ulis.jurisdictions.modules.base import JurisdictionModule, ProvisionCitationInput

ORDINALS = [
    ("prvi", "prvega"),
    ("drugi", "drugega"),
    ("tretji", "tretjega"),
    ("četrti", "četrtega"),
    ("peti", "petega"),
    ("šesti", "šestega"),
    ("sedmi", "sedmega"),
    ("osmi", "osmega"),
    ("deveti", "devetega"),
    ("deseti", "desetega"),
    ("enajsti", "enajstega"),
    ("dvanajsti", "dvanajstega"),
    ("trinajsti", "trinajstega"),
    ("štirinajsti", "štirinajstega"),
    ("petnajsti", "petnajstega"),
    ("šestnajsti", "šestnajstega"),
    ("sedemnajsti", "sedemnajstega"),
    ("osemnajsti", "osemnajstega"),
    ("devetnajsti", "devetnajstega"),
    ("dvajseti", "dvajsetega"),
]


class SiExt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ul_id: str  # "2025-01-3978": year, part of the gazette, number of the act in the year
    ul_year: int
    ul_part: str
    act_number: int
    issue: str  # "112/2025"
    ul_citation: str  # "Uradni list RS, št. 112/2025"
    consolidated_text: bool = False  # an "uradno prečiščeno besedilo" published in the gazette
    abbreviation: str | None = None  # "ZJRM-2", from the last parenthesis of the title


def _number(key: str) -> str:
    """``12`` -> ``12.``; ``12a`` -> ``12.a`` (the number as it stands before "člen" or "točka")."""
    m = re.fullmatch(r"(\d+)([a-z]*)", key)
    if not m:
        return key
    return f"{m.group(1)}.{m.group(2)}" if m.group(2) else f"{m.group(1)}."


def _ordinal(key: str, genitive: bool) -> str:
    if key.isdigit() and 1 <= int(key) <= len(ORDINALS):
        return ORDINALS[int(key) - 1][1 if genitive else 0]
    return _number(key)


def _phrase(tag: str, key: str, genitive: bool) -> str:
    if tag == "art":
        return f"{_number(key)} {'člena' if genitive else 'člen'}"
    if tag == "par":
        return f"{_ordinal(key, genitive)} {'odstavka' if genitive else 'odstavek'}"
    if tag == "pt":
        return f"{_number(key)} {'točke' if genitive else 'točka'}"
    if tag == "sub":
        return f"{'podtočke' if genitive else 'podtočka'} {key})"
    return f"{tag} {key}"


def genitive(title: str) -> str | None:
    for nominative, gen in (
        ("Zakon", "Zakona"),
        ("Ustava", "Ustave"),
        ("Uredba", "Uredbe"),
        ("Pravilnik", "Pravilnika"),
    ):
        if re.match(rf"^{nominative}\b", title):
            return gen + title[len(nominative) :]
    return None


class SiModule(JurisdictionModule):
    id: ClassVar[str] = "si"
    version: ClassVar[str] = "1.0.0"
    ext_model: ClassVar[type[BaseModel]] = SiExt

    def pinpoint(self, c: ProvisionCitationInput) -> str:
        parts = [p for p in c.num_path if p]
        if not parts:
            return ""
        first_tag, _, first_key = parts[0].partition(":")
        if parts[0] == "preamble":
            return "uvodni del"
        if first_tag == "zav":
            return "sklepni del"
        if first_tag == "pril":
            return f"priloga {first_key}"
        pieces = []
        for i, part in enumerate(reversed(parts)):
            tag, _, key = part.partition(":")
            pieces.append(_phrase(tag, key, genitive=i > 0))
        return " ".join(pieces)

    def provision_citation(self, c: ProvisionCitationInput) -> str:
        pin = self.pinpoint(c)
        name = c.ext.get("abbreviation")
        if not name:
            gen = genitive(re.sub(r"\s*\(uradno prečiščeno besedilo\)\s*", " ", c.instrument_title).strip())
            name = gen
        if name:
            return f"{pin} {name}".strip()
        return f"{c.instrument_title}, {pin}" if pin else c.instrument_title

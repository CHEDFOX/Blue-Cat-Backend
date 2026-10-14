"""France module: citations of consolidated legislation (codes, laws, ordinances...) in French.

* codes: ``C. civ., art. 1240``, ``C. trav., art. L. 1234-5`` (the abbreviation chosen in the profile's
  ``extensions.fr.abbreviations``, else the title of the code in the DILA database: ``Code civil, art. 1240``)
* laws and other texts: ``Loi n° 78-17 du 6 janvier 1978, art. 22``
* sections: ``Code civil, livre III``, ``Loi n° 78-17 du 6 janvier 1978, chapitre Ier`` (the section's own number)

The article number is the DILA's ``NUM`` (``L233-3``, ``R*229-8``, ``1240``); it is printed in the French way, a letter
and a point before the figures (``L. 233-3``). Formatting conventions only; to be confirmed by a French reviewer
before the FR profile is "reviewed" (in particular the abbreviations).
"""

from __future__ import annotations

import re
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from ulis.jurisdictions.modules.base import JurisdictionModule, ProvisionCitationInput

SECTION_NAMES = {
    "part": "partie",
    "book": "livre",
    "title": "titre",
    "subtitle": "sous-titre",
    "chapter": "chapitre",
    "section": "section",
    "subsection": "sous-section",
    "paragraph": "paragraphe",
    "subparagraph": "sous-paragraphe",
    "subsubparagraph": "sous-sous-paragraphe",
    "annex": "annexe",
}
ARTICLE_NUMBER = re.compile(r"^(?P<letter>LO|LP|L|R|D|A)(?P<star>\*{0,2})\s*\.?\s*(?P<rest>\d.*)$")


class FrExt(BaseModel):
    """Identifiers of a consolidated text in the DILA LEGI database (``dila_legi_xml`` parser)."""

    model_config = ConfigDict(extra="forbid")
    cid: str  # chronical identifier: LEGITEXT... for a code, JORFTEXT... for a law, ordinance...
    legitext: str | None = None  # identifier of the consolidated version of the text
    nature: str | None = None  # CODE, LOI, ORDONNANCE, CONSTITUTION...
    nor: str | None = None
    etat: str | None = None  # state of the text in the database: VIGUEUR, ABROGE, MODIFIE...
    tree: str | None = None  # position in the dump: code_en_vigueur, TNC_en_vigueur...


class Abbreviation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str  # the title of the code in the DILA database (checked at citation time)
    cite_as: str  # the conventional abbreviation


class FrProfileExt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    abbreviations: dict[str, Abbreviation] = Field(default_factory=dict)  # by text identifier (cid)


def format_article(num: str) -> str:
    """``L233-3`` -> ``L. 233-3``; ``R*229-8`` -> ``R*. 229-8``; ``1240`` and ``1 A`` stay as they are."""
    match = ARTICLE_NUMBER.match(num.strip())
    if not match:
        return num.strip()
    return f"{match['letter']}{match['star']}. {match['rest']}"


class FrModule(JurisdictionModule):
    id: ClassVar[str] = "fr"
    version: ClassVar[str] = "1.0.0"
    ext_model: ClassVar[type[BaseModel]] = FrExt
    profile_extension_model: ClassVar[type[BaseModel]] = FrProfileExt

    def pinpoint(self, c: ProvisionCitationInput) -> str:
        parts = [p for p in c.num_path if p]
        if c.top_kind == "article":
            return f"art. {format_article(parts[0])}" if parts else ""
        name = SECTION_NAMES.get(c.top_kind, c.top_kind)
        return f"{name} {parts[0]}" if parts else name

    def provision_citation(self, c: ProvisionCitationInput) -> str:
        name = c.instrument_title
        entry = c.profile_extensions.get(self.id, {}).get("abbreviations", {}).get(c.ext.get("cid", ""))
        if entry and entry.get("title") == c.instrument_title:
            name = entry["cite_as"]
        pin = self.pinpoint(c)
        return f"{name}, {pin}" if pin else name

    def validate_ext(self, ext: dict[str, Any]) -> dict[str, Any]:
        return super().validate_ext(ext)

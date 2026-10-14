"""France module: citations of consolidated legislation and of court decisions, in French.

* codes: ``C. civ., art. 1240``, ``C. trav., art. L. 1234-5`` (the abbreviation chosen in the profile's
  ``extensions.fr.abbreviations``, else the title of the code in the DILA database: ``Code civil, art. 1240``)
* laws and other texts: ``Loi n° 78-17 du 6 janvier 1978, art. 22``
* sections: ``Code civil, livre III``, ``Loi n° 78-17 du 6 janvier 1978, chapitre Ier`` (the section's own number)
* decisions, by court, formation, date and number, with the numbered paragraph as pinpoint:
  ``Cons. const., 7 janv. 2022, n° 2021-960 QPC, § 5``, ``Cass. civ. 1re, 12 juill. 2012, n° 11-17.738``,
  ``CE, 5 mars 1999, n° 196283``, ``CAA Paris, 11 juill. 2025, n° 22PA02724``, ``TA Lyon, 3 mars 2020, n° 1805678``

The article number is the DILA's ``NUM`` (``L233-3``, ``R*229-8``, ``1240``); it is printed in the French way, a letter
and a point before the figures (``L. 233-3``). Formatting conventions only; to be confirmed by a French reviewer
before the FR profile is "reviewed" (in particular the abbreviations, the "§" pinpoint and the chambers).
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from ulis.jurisdictions.modules.base import JudgmentCitationInput, JurisdictionModule, ProvisionCitationInput

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
MONTHS = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juill.", "août", "sept.", "oct.", "nov.", "déc."]
# Chambers of the Cour de cassation, from the publisher's FORMATION
CHAMBERS = {
    "chambre_civile_1": "civ. 1re",
    "chambre_civile_2": "civ. 2e",
    "chambre_civile_3": "civ. 3e",
    "chambre_civile": "civ.",
    "chambre_commerciale": "com.",
    "chambre_sociale": "soc.",
    "chambre_criminelle": "crim.",
    "assemblee_pleniere": "ass. plén.",
    "chambre_mixte": "ch. mixte",
    "chambres_reunies": "ch. réunies",
    "avis": "avis",
}
ARTICLE_NUMBER = re.compile(r"^(?P<letter>LO|LP|L|R|D|A)(?P<star>\*{0,2})\s*\.?\s*(?P<rest>\d.*)$")
PAYMENT_NUMBER = re.compile(r"^((?:[A-Z] )?\d{2}-\d{2})(\d{3})$")


class FrExt(BaseModel):
    """Identifiers of a consolidated text in the DILA LEGI database (``dila_legi_xml`` parser)."""

    model_config = ConfigDict(extra="forbid")
    cid: str  # chronical identifier: LEGITEXT... for a code, JORFTEXT... for a law, ordinance...
    legitext: str | None = None  # identifier of the consolidated version of the text
    nature: str | None = None  # CODE, LOI, ORDONNANCE, CONSTITUTION...
    nor: str | None = None
    etat: str | None = None  # state of the text in the database: VIGUEUR, ABROGE, MODIFIE...
    tree: str | None = None  # position in the dump: code_en_vigueur, TNC_en_vigueur...


class FrJudgmentExt(BaseModel):
    """Facts of a decision from the DILA case-law data (``dila_juri_xml`` parser) that have no column of their own.

    No name of a judge, lawyer or party is kept outside the (pseudonymised) text."""

    model_config = ConfigDict(extra="forbid")
    dila_id: str  # CONSTEXT..., JURITEXT..., CETATEXT...
    family: str  # constit | judi | admin
    nature: str | None = None  # DC, QPC, AN... / ARRET, AVIS / Texte
    juridiction: str  # as published: "Conseil constitutionnel", "Cour de cassation", "CAA de PARIS"...
    solution: str | None = None
    place: str | None = None  # the city of a cour administrative d'appel / tribunal administratif
    ecli: str | None = None
    formation: str | None = None  # chamber / formation
    nature_qualified: str | None = None
    nor: str | None = None
    journal: str | None = None  # where the Conseil constitutionnel decision was published
    published_in_bulletin: bool | None = None
    appealed_court: str | None = None
    appealed_date: str | None = None
    appeal_type: str | None = None
    published_in_recueil: str | None = None


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


def format_date(d: date) -> str:
    return f"{'1er' if d.day == 1 else d.day} {MONTHS[d.month - 1]} {d.year}"


def format_docket(court_id: str, docket: str | None) -> str | None:
    """The case number as French practice prints it: ``25-70009`` -> ``25-70.009`` for the Cour de cassation."""
    if not docket:
        return None
    if court_id != "FR-CCASS":
        return docket
    return "; ".join(PAYMENT_NUMBER.sub(r"\1.\2", part.strip()) for part in docket.split(";"))


class FrModule(JurisdictionModule):
    id: ClassVar[str] = "fr"
    version: ClassVar[str] = "1.0.0"
    ext_model: ClassVar[type[BaseModel]] = FrExt
    profile_extension_model: ClassVar[type[BaseModel]] = FrProfileExt

    def validate_ext(self, ext: dict[str, Any]) -> dict[str, Any]:
        # A decision's ext (from the DILA case-law data) is recognised by its identifier; legislation's by its cid.
        if "dila_id" in ext:
            return FrJudgmentExt.model_validate(ext).model_dump(exclude_none=True)
        return super().validate_ext(ext)

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

    def judgment_citation(self, c: JudgmentCitationInput) -> str:
        ext = c.ext or {}
        place = str(ext["place"]).title() if ext.get("place") else None
        court = {
            "FR-CC": "Cons. const.",
            "FR-CE": "CE",
            "FR-TC": "T. confl.",
            "FR-CAA": f"CAA {place}" if place else "CAA",
            "FR-TA": f"TA {place}" if place else "TA",
        }.get(c.court_id)
        if c.court_id == "FR-CCASS":
            chamber = CHAMBERS.get(str(ext.get("formation") or "").lower())
            court = f"Cass. {chamber}" if chamber else "Cass."
        text = f"{court or c.court_name}, {format_date(c.decision_date)}"
        number = format_docket(c.court_id, c.docket)
        if number:
            text += f", n° {number}"
        elif c.neutral_citation:
            text += f", {c.neutral_citation}"
        if c.paragraph:
            text += f", § {c.paragraph}"
        return text

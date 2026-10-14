"""Netherlands module: Dutch citation conventions for national legislation and court decisions.

* ``art. 6:162 lid 1 BW`` (Burgerlijk Wetboek: book number before the article number)
* ``art. 3:4 lid 2 Awb`` (the Awb numbers its articles ``chapter:article`` itself)
* ``art. 310 Sr``, ``art. 1 Grondwet``, ``art. 23 lid 4 Grondwet``
* list items: ``art. 1:3 lid 1 onder a Awb``; a nested item: ``art. 7 lid 2 onder a sub 1° UAVG``
* annexes: ``Bijlage 2 Awb``; an unnumbered article by its heading: ``Algemene bepaling Grondwet``

The name after the pinpoint is the abbreviation chosen in the profile (``extensions.nl.
abbreviations``), which must be one of the abbreviations the source's wetstechnische informatie
(WTI) lists for that regulation, or its official citation title (``citeertitel``); otherwise the
citation title from the toestand itself. Formatting conventions only; to be confirmed by a Dutch
reviewer before the NL profile is "reviewed" (e.g. "onder a" versus "sub a" for list items).

Court decisions are cited by court, date and ECLI, with the numbered consideration (rechtsoverweging)
as pinpoint: ``HR 3 april 2020, ECLI:NL:HR:2020:591, r.o. 2``; ``ABRvS 2 maart 2022,
ECLI:NL:RVS:2022:635, r.o. 5.2``. The court abbreviations follow common Dutch usage (HR, ABRvS, CRvB,
CBb, Hof, Rb.); to be confirmed by a Dutch reviewer.
"""

from __future__ import annotations

import re
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ulis.jurisdictions.modules.base import JudgmentCitationInput, JurisdictionModule, ProvisionCitationInput

MONTHS = [
    "januari",
    "februari",
    "maart",
    "april",
    "mei",
    "juni",
    "juli",
    "augustus",
    "september",
    "oktober",
    "november",
    "december",
]
# Citation form of the courts in the profile; any other court is cited by its name.
COURT_ABBREVIATIONS = {
    "NL-HR": "HR",
    "NL-ABRVS": "ABRvS",
    "NL-CRVB": "CRvB",
    "NL-CBB": "CBb",
    "NL-GHDHA": "Hof Den Haag",
    "NL-RBDHA": "Rb. Den Haag",
}


class NlExt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bwb_id: str  # Basiswettenbestand identifier, e.g. BWBR0005289
    jci: str  # Juriconnect identifier of the regulation, e.g. jci1.3:c:BWBR0005289
    citeertitel: str | None = None  # official citation title, e.g. "Burgerlijk Wetboek Boek 6"
    soort: str | None = None  # BWB regulation type (wetgeving/@soort), e.g. "wet"
    boek: str | None = None  # Burgerlijk Wetboek book number, from the citation title


class NlJudgmentExt(BaseModel):
    """Facts from the Rechtspraak open data that have no column of their own (``rechtspraak`` parser)."""

    model_config = ConfigDict(extra="forbid")
    ecli: str
    zaaknummers: list[str] = Field(default_factory=list)  # case numbers (psi:zaaknummer)
    procedure: list[str] = Field(default_factory=list)  # e.g. "Cassatie", "Hoger beroep"
    rechtsgebied: list[str] = Field(default_factory=list)  # e.g. "Civiel recht"
    vindplaatsen: list[str] = Field(default_factory=list)  # publications, as the data lists them
    replaces: list[str] = Field(default_factory=list)  # older identifiers (LJN, replaced ECLIs)
    issued: str | None = None  # publication date of the ECLI register entry
    modified: str | None = None  # last modification of the register entry (as published)
    publisher_title: str | None = None  # the publisher's suggested title


class Abbreviation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    citeertitel: str
    afkortingen: list[str] = Field(default_factory=list)  # as listed in the regulation's WTI
    cite_as: str

    @model_validator(mode="after")
    def _from_source(self) -> Abbreviation:
        if self.cite_as not in [*self.afkortingen, self.citeertitel]:
            raise ValueError(f"cite_as {self.cite_as!r} is neither a source abbreviation nor the citation title")
        return self


class NlProfileExt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    abbreviations: dict[str, Abbreviation] = Field(default_factory=dict)  # by BWB id


def _item(label: str) -> str:
    return label.strip().rstrip(".")


class NlModule(JurisdictionModule):
    id: ClassVar[str] = "nl"
    version: ClassVar[str] = "1.0.0"
    ext_model: ClassVar[type[BaseModel]] = NlExt
    profile_extension_model: ClassVar[type[BaseModel]] = NlProfileExt

    def validate_ext(self, ext: dict[str, Any]) -> dict[str, Any]:
        # A judgment's ext (from the Rechtspraak data) is recognised by its ECLI; legislation's by its BWB id.
        if "ecli" in ext:
            return NlJudgmentExt.model_validate(ext).model_dump(exclude_none=True)
        return super().validate_ext(ext)

    def pinpoint(self, c: ProvisionCitationInput) -> str:
        parts = [p for p in c.num_path if p]
        if not parts:
            return ""
        if c.top_kind in ("annex", "general_provision"):
            out = parts[0]
        else:
            book = c.ext.get("boek")
            out = f"art. {book}:{parts[0]}" if book else f"art. {parts[0]}"
        items = 0
        for part in parts[1:]:
            if re.match(r"^lid\s", part):
                out += f" {part}"
            else:
                out += f" {'onder' if items == 0 else 'sub'} {_item(part)}"
                items += 1
        return out

    def provision_citation(self, c: ProvisionCitationInput) -> str:
        name = c.ext.get("citeertitel") or c.instrument_title
        entry = c.profile_extensions.get(self.id, {}).get("abbreviations", {}).get(c.ext.get("bwb_id", ""))
        if entry and entry.get("citeertitel") == c.ext.get("citeertitel"):
            name = entry["cite_as"]
        pin = self.pinpoint(c)
        return f"{pin} {name}" if pin else name

    def judgment_citation(self, c: JudgmentCitationInput) -> str:
        court = COURT_ABBREVIATIONS.get(c.court_id, c.court_name)
        d = c.decision_date
        text = f"{court} {d.day} {MONTHS[d.month - 1]} {d.year}"
        if c.neutral_citation:
            text += f", {c.neutral_citation}"
        elif c.docket:
            text += f", zaaknr. {c.docket}"
        if c.paragraph:
            text += f", r.o. {c.paragraph}"
        return text

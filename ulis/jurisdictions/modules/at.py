"""Austria module: citations in the usual Austrian form, in German (the only official language).

* Paragraphs: ``§ 83 Abs. 3 Z 1 StGB``; litera and sublitera: ``§ 4 Z 2 lit. a DSG``
* Articles: ``Art. 7 Abs. 1 B-VG``; annexes: ``Anl. 1 UGB``
* Decisions: court, decision date, Geschäftszahl as printed in RIS, Randzahl if the decision
  numbers its paragraphs, and the ECLI: ``OGH 10.06.2021, 7Ob65/21s, Rz 12 (ECLI:AT:...)``

The instrument is named by its RIS abbreviation for the version cited (e.g. "DSG 2000" for the
pre-2018 versions of what is now the "DSG"), else by its short title. Formatting conventions
only; to be confirmed by an Austrian reviewer before the AT profile is "reviewed".
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from ulis.jurisdictions.modules.base import JudgmentCitationInput, JurisdictionModule, ProvisionCitationInput

TOP = {"section": "§", "article": "Art.", "annex": "Anl."}
SUB = {"abs": "Abs.", "z": "Z", "lit": "lit.", "sublit": "sublit."}
COURTS = {"AT-VFGH": "VfGH", "AT-VWGH": "VwGH", "AT-OGH": "OGH"}


class AtExt(BaseModel):
    """Native identifiers: RIS Gesetzesnummer and norm type for laws; RIS data for decisions."""

    model_config = ConfigDict(extra="forbid")
    gesetzesnummer: str | None = Field(default=None, pattern=r"^\d{8}$")
    ris_type: str | None = None
    ris_document_number: str | None = None
    geschaeftszahlen: list[str] | None = None
    norms: list[str] | None = None
    decision_type: str | None = None


def subdivisions(parts: list[str]) -> str:
    """``["abs:3", "z:1", "lit:a"]`` -> ``Abs. 3 Z 1 lit. a``; unknown tags print their label."""
    out: list[str] = []
    for part in parts:
        tag, _, key = part.partition(":")
        out.append(f"{SUB[tag]} {key}" if tag in SUB else key or tag)
    return " ".join(out)


class AtModule(JurisdictionModule):
    id: ClassVar[str] = "at"
    version: ClassVar[str] = "1.0.0"
    ext_model: ClassVar[type[BaseModel]] = AtExt

    def pinpoint(self, c: ProvisionCitationInput) -> str:
        parts = [p for p in c.num_path if p]
        if not parts:
            return ""
        head = f"{TOP.get(c.top_kind, c.top_kind)} {parts[0]}"
        rest = subdivisions(parts[1:])
        return f"{head} {rest}" if rest else head

    def provision_citation(self, c: ProvisionCitationInput) -> str:
        pin = self.pinpoint(c)
        return f"{pin} {c.instrument_title}" if pin else c.instrument_title

    def judgment_citation(self, c: JudgmentCitationInput) -> str:
        court = COURTS.get(c.court_id, c.court_name)
        d = c.decision_date
        text = f"{court} {d.day:02d}.{d.month:02d}.{d.year}"
        if c.docket:
            text += f", {c.docket}"
        if c.paragraph:
            text += f", Rz {c.paragraph}"
        if c.neutral_citation:
            text += f" ({c.neutral_citation})"
        return text

"""Modules for the fictional sample jurisdictions (Aurelia ``XA*`` and Borealis ``XB``).

They are deliberately different from each other so that the extension mechanism is
exercised: Aurelia uses common-law style citations with a jurisdiction tag; Borealis uses
language-dependent civil-law citations ("art. 28 al. 2 CC-XB" vs "Art. 28 Abs. 2 ZGB-XB").
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from ulis.jurisdictions.modules.base import (
    JudgmentCitationInput,
    JurisdictionModule,
    ProvisionCitationInput,
)


class AureliaInstrumentExt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chapter: str | None = Field(default=None, pattern=r"^Cap\. \d+[A-Z]?$")


class AureliaModule(JurisdictionModule):
    id: ClassVar[str] = "xa"
    version: ClassVar[str] = "1.0.0"
    ext_model: ClassVar[type[BaseModel]] = AureliaInstrumentExt

    def provision_citation(self, c: ProvisionCitationInput) -> str:
        if c.instrument_type == "constitution":
            return f"{c.instrument_title} {self.pinpoint(c)}"
        return f"{c.instrument_title} ({c.jurisdiction_id}) {self.pinpoint(c)}"

    def judgment_citation(self, c: JudgmentCitationInput) -> str:
        pin = f" [{c.paragraph}]" if c.paragraph else ""
        return f"{c.case_name} {c.neutral_citation or c.decision_date.isoformat()}{pin}"


class BorealisInstrumentExt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code_key: str | None = None


class BorealisProfileExt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code_abbreviations: dict[str, str] = Field(default_factory=dict)


MONTHS = {
    "fr": [
        "janvier",
        "février",
        "mars",
        "avril",
        "mai",
        "juin",
        "juillet",
        "août",
        "septembre",
        "octobre",
        "novembre",
        "décembre",
    ],
    "de": [
        "Januar",
        "Februar",
        "März",
        "April",
        "Mai",
        "Juni",
        "Juli",
        "August",
        "September",
        "Oktober",
        "November",
        "Dezember",
    ],
}
COURT_ABBREVIATIONS = {"XB-CC": {"fr": "Cass. XB", "de": "KassH XB"}, "XB-CA": {"fr": "CA XB", "de": "AppG XB"}}


class BorealisModule(JurisdictionModule):
    id: ClassVar[str] = "xb"
    version: ClassVar[str] = "1.0.0"
    ext_model: ClassVar[type[BaseModel]] = BorealisInstrumentExt
    profile_extension_model: ClassVar[type[BaseModel]] = BorealisProfileExt

    def provision_citation(self, c: ProvisionCitationInput) -> str:
        lang = "de" if c.language == "de" else "fr"
        art, sub = ("Art.", "Abs.") if lang == "de" else ("art.", "al.")
        pin = f"{art} {c.num_path[0]}" if c.num_path else ""
        if len(c.num_path) > 1:
            pin += f" {sub} {c.num_path[1].strip('(). ')}"
        if c.instrument_type == "code":
            abbreviations = c.profile_extensions.get(self.id, {}).get("code_abbreviations", {})
            return f"{pin} {abbreviations.get(lang, c.instrument_title)}"
        return f"{pin} {c.instrument_title}"

    def judgment_citation(self, c: JudgmentCitationInput) -> str:
        lang = "de" if c.language == "de" else "fr"
        court = COURT_ABBREVIATIONS.get(c.court_id, {}).get(lang, c.court_name)
        d = c.decision_date
        when = (
            f"{d.day}. {MONTHS[lang][d.month - 1]} {d.year}"
            if lang == "de"
            else (f"{d.day} {MONTHS[lang][d.month - 1]} {d.year}")
        )
        number = f", {'Nr.' if lang == 'de' else 'n°'} {c.docket}" if c.docket else ""
        pin = f", {'Rz.' if lang == 'de' else '§'} {c.paragraph}" if c.paragraph else ""
        return f"{court}, {when}{number}{pin}"

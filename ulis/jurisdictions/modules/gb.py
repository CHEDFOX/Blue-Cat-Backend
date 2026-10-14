"""United Kingdom module: OSCOLA-style citations for legislation.gov.uk material.

* Acts and Measures: ``Human Rights Act 1998, s 4(5)(a)``; schedules: ``Theft Act 1968, sch 1, para 2(1)``
* Statutory instruments: ``Data Protection (Charges and Information) Regulations 2018, SI 2018/480, reg 2(1)``

* UK Supreme Court judgments (OSCOLA): the case name, the neutral citation and the paragraph in square brackets:
  ``Abbey Healthcare (Mill Hill) Ltd v Augusta 2008 LLP [2024] UKSC 23 [47]``

Formatting conventions only; to be confirmed by a UK reviewer before the GB profile is "reviewed".
"""

from __future__ import annotations

import re
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from ulis.jurisdictions.modules.base import (
    JudgmentCitationInput,
    JurisdictionModule,
    ProvisionCitationInput,
    subdivision,
)

SI_SERIES = {"uksi": "SI", "ssi": "SSI", "wsi": "SI", "nisr": "SR"}
PREFIX = {"section": "s", "regulation": "reg", "article": "art", "rule": "r", "paragraph": "para"}


class GbExt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    leg_type: str
    year: int
    number: str
    chapter: str | None = None


class GbJudgmentExt(BaseModel):
    """Extension data of a UK Supreme Court judgment (the Court's website)."""

    model_config = ConfigDict(extra="forbid")
    case_id: str  # e.g. "UKSC/2022/0124"
    appellants: list[str] = Field(default_factory=list)
    respondents: list[str] = Field(default_factory=list)
    interveners: list[str] = Field(default_factory=list)
    judgment_appealed: str | None = None  # neutral citation of the decision appealed, e.g. "[2022] EWCA Civ 823"
    justices: list[str] = Field(default_factory=list)
    hearing_start: str | None = None
    hearing_end: str | None = None


class GbModule(JurisdictionModule):
    id: ClassVar[str] = "gb"
    version: ClassVar[str] = "1.1.0"
    ext_model: ClassVar[type[BaseModel]] = GbExt

    def validate_ext(self, ext: dict[str, Any]) -> dict[str, Any]:
        # A judgment's ext (UKSC) is recognised by its case id; legislation's by its type and number.
        if "case_id" in ext:
            return GbJudgmentExt.model_validate(ext).model_dump(exclude_none=True)
        return super().validate_ext(ext)

    def pinpoint(self, c: ProvisionCitationInput) -> str:
        parts = [p for p in c.num_path if p]
        if not parts:
            return ""
        schedule = re.match(r"^schedule-(\w+?)(?:-|$)", c.eid or "")
        head = f"{PREFIX.get(c.top_kind, c.top_kind)} {parts[0]}" + "".join(subdivision(p) for p in parts[1:])
        if schedule:
            return f"sch {schedule.group(1)}, para {parts[0]}" + "".join(subdivision(p) for p in parts[1:])
        return head

    def provision_citation(self, c: ProvisionCitationInput) -> str:
        pin = self.pinpoint(c)
        series = SI_SERIES.get(str(c.ext.get("leg_type", "")))
        title = c.instrument_title
        if series:
            title = f"{title}, {series} {c.ext.get('year')}/{c.ext.get('number')}"
        return f"{title}, {pin}" if pin else title

    def judgment_citation(self, c: JudgmentCitationInput) -> str:
        """``Case name [2024] UKSC 23 [47]`` (OSCOLA); without a neutral citation the court and date."""
        reference = c.neutral_citation or f"({c.court_name}, {c.decision_date.isoformat()})"
        pin = f" [{c.paragraph}]" if c.paragraph else ""
        return f"{c.case_name} {reference}{pin}"

"""United Kingdom module: OSCOLA-style citations for legislation.gov.uk material.

* Acts and Measures: ``Human Rights Act 1998, s 4(5)(a)``; schedules: ``Theft Act 1968, sch 1, para 2(1)``
* Statutory instruments: ``Data Protection (Charges and Information) Regulations 2018, SI 2018/480, reg 2(1)``

Formatting conventions only; to be confirmed by a UK reviewer before the GB profile is "reviewed".
"""

from __future__ import annotations

import re
from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from ulis.jurisdictions.modules.base import JurisdictionModule, ProvisionCitationInput, subdivision

SI_SERIES = {"uksi": "SI", "ssi": "SSI", "wsi": "SI", "nisr": "SR"}
PREFIX = {"section": "s", "regulation": "reg", "article": "art", "rule": "r", "paragraph": "para"}


class GbExt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    leg_type: str
    year: int
    number: str
    chapter: str | None = None


class GbModule(JurisdictionModule):
    id: ClassVar[str] = "gb"
    version: ClassVar[str] = "1.0.0"
    ext_model: ClassVar[type[BaseModel]] = GbExt

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

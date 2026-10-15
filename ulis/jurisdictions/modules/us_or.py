"""Oregon module: ``ORS 163.115`` (Oregon Revised Statutes; the number is chapter.section)."""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from ulis.jurisdictions.modules.base import ProvisionCitationInput
from ulis.jurisdictions.modules.us_state_base import UsStateModule


class UsOrExt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ors_chapter: str
    edition_year: int | None = None


class UsOrModule(UsStateModule):
    id: ClassVar[str] = "us_or"
    version: ClassVar[str] = "1.0.0"
    ext_model: ClassVar[type[BaseModel]] = UsOrExt

    def code_citation(self, c: ProvisionCitationInput) -> str:
        return "ORS"

    def unit_sign(self, c: ProvisionCitationInput) -> str:
        return ""

    def provision_citation(self, c: ProvisionCitationInput) -> str:
        if not [p for p in c.num_path if p]:
            return f"ORS ch. {c.ext.get('ors_chapter', c.instrument_title)}"
        return super().provision_citation(c)

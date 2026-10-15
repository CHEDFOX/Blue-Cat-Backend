"""Washington module: ``RCW 9A.32.030`` (Revised Code of Washington; the number is title.chapter.section)."""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from ulis.jurisdictions.modules.base import ProvisionCitationInput
from ulis.jurisdictions.modules.us_state_base import UsStateModule


class UsWaExt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rcw_title: str
    rcw_chapter: str


class UsWaModule(UsStateModule):
    id: ClassVar[str] = "us_wa"
    version: ClassVar[str] = "1.0.0"
    ext_model: ClassVar[type[BaseModel]] = UsWaExt

    def code_citation(self, c: ProvisionCitationInput) -> str:
        return "RCW"

    def unit_sign(self, c: ProvisionCitationInput) -> str:
        return ""

    def provision_citation(self, c: ProvisionCitationInput) -> str:
        if not [p for p in c.num_path if p]:
            return f"RCW ch. {c.ext.get('rcw_chapter', c.instrument_title)}"
        return super().provision_citation(c)

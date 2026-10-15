"""Australian States and Territories: AGLC-style citations of State and Territory Acts.

* Acts: ``Criminal Code Act 1899 (Qld) s 1``, ``Acts Interpretation Act 1954 (Qld) s 14A(1)``;
* a schedule enacted as a code: ``Criminal Code (Qld) s 302``, ``Criminal Code (Tas) s 158``;
* definitions: ``Acts Interpretation Act 1954 (Qld) s 36 (definition of ‘document’)``; schedules:
  ``Human Rights Act 2019 (Qld) sch 1 (definition of ‘act’)``.

Pinpoint rules are those of the Commonwealth module (section and schedule numbers are the source's own);
only the jurisdiction abbreviation and the code title differ. Formatting conventions only, to be confirmed
by an Australian reviewer before the State profiles are "reviewed".
"""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict

from ulis.jurisdictions.modules.au import AuModule
from ulis.jurisdictions.modules.base import ProvisionCitationInput


class AuStateExt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state: Literal["NSW", "Vic", "Qld", "WA", "SA", "Tas", "ACT", "NT"]
    legislation_id: str  # the publisher's identifier of the Act, e.g. act-1899-009
    act_number: str | None = None  # e.g. "No. 9 of 1899"
    code_title: str | None = None  # a schedule enacted as a code (e.g. "Criminal Code")


class AuStateModule(AuModule):
    id: ClassVar[str] = "au_state"
    version: ClassVar[str] = "1.0.0"
    ext_model: ClassVar[type[BaseModel]] = AuStateExt

    def _title(self, c: ProvisionCitationInput) -> str:
        state = c.ext.get("state", "")
        if c.top_kind in ("code_section", "code_dictionary") and c.ext.get("code_title"):
            return f"{c.ext['code_title']} ({state})"
        return f"{c.instrument_title} ({state})"

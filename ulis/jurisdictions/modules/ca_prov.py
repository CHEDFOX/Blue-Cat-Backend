"""Canadian provinces and territories: McGill Guide-style citations of provincial statutes (English).

* Ontario (Revised Statutes of Ontario): ``Courts of Justice Act, RSO 1990, c C.43, s 1(1)``;
* Manitoba (Continuing Consolidation of the Statutes of Manitoba): ``The Human Rights Code, CCSM c H175, s 8(1.1)``;
* definitions: ``Courts of Justice Act, RSO 1990, c C.43, s 1(1) “action”``.

The statute citation (``RSO 1990, c C.43``) is the one the source prints for the Act; the title is the Act's
official short title. Pinpoints reuse the federal Canadian rules (``s``, ``Sch``). Formatting conventions
only, to be confirmed by a Canadian reviewer before the profiles are "reviewed". French-language provincial
texts are not ingested yet, so only English forms exist.
"""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict

from ulis.jurisdictions.modules.base import ProvisionCitationInput
from ulis.jurisdictions.modules.ca import CaModule


class CaProvExt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    province: Literal["ON", "BC", "AB", "QC", "MB", "NS", "SK", "NB", "NL", "PE", "YT", "NT", "NU"]
    provincial_id: str  # the publisher's identifier of the Act, e.g. 90c43 (Ontario), h175 (Manitoba)
    statute_citation: str | None = None  # e.g. "RSO 1990, c C.43", "CCSM c H175"


class CaProvModule(CaModule):
    id: ClassVar[str] = "ca_prov"
    version: ClassVar[str] = "1.0.0"
    ext_model: ClassVar[type[BaseModel]] = CaProvExt

    def provision_citation(self, c: ProvisionCitationInput) -> str:
        source = c.ext.get("statute_citation")
        title = f"{c.instrument_title}, {source}" if source else c.instrument_title
        pin = self.pinpoint(c)
        return f"{title}, {pin}" if pin else title

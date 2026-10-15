"""Texas module: ``Tex. Penal Code § 19.02``, ``Tex. Code Crim. Proc. art. 38.23``.

The abbreviations follow the Texas Rules of Form (the Greenbook) as far as the code names go; the Council's list
of codes also holds uncodified collections (Vernon's Civil Statutes, Insurance Code - Not Codified, Auxiliary Water
Laws) whose abbreviation is not fixed by the Rules and is built from the collection's name.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from ulis.jurisdictions.modules.base import ProvisionCitationInput
from ulis.jurisdictions.modules.us_state_base import UsStateModule

CITATIONS = {
    "AG": "Tex. Agric. Code",
    "AL": "Tex. Alco. Bev. Code",
    "WL": "Tex. Aux. Water Laws",
    "BC": "Tex. Bus. & Com. Code",
    "BO": "Tex. Bus. Orgs. Code",
    "CP": "Tex. Civ. Prac. & Rem. Code",
    "CR": "Tex. Code Crim. Proc.",
    "ED": "Tex. Educ. Code",
    "EL": "Tex. Elec. Code",
    "ES": "Tex. Est. Code",
    "FA": "Tex. Fam. Code",
    "FI": "Tex. Fin. Code",
    "GV": "Tex. Gov't Code",
    "HS": "Tex. Health & Safety Code",
    "HR": "Tex. Hum. Res. Code",
    "IN": "Tex. Ins. Code",
    "I1": "Tex. Ins. Code (not codified)",
    "LA": "Tex. Lab. Code",
    "LG": "Tex. Loc. Gov't Code",
    "NR": "Tex. Nat. Res. Code",
    "OC": "Tex. Occ. Code",
    "PW": "Tex. Parks & Wild. Code",
    "PE": "Tex. Penal Code",
    "PR": "Tex. Prop. Code",
    "SD": "Tex. Spec. Dist. Code",
    "TX": "Tex. Tax Code",
    "TN": "Tex. Transp. Code",
    "UT": "Tex. Util. Code",
    "WA": "Tex. Water Code",
    "CV": "Tex. Rev. Civ. Stat.",
}


class UsTxExt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    code_name: str


class UsTxModule(UsStateModule):
    id: ClassVar[str] = "us_tx"
    version: ClassVar[str] = "1.0.0"
    ext_model: ClassVar[type[BaseModel]] = UsTxExt

    def code_citation(self, c: ProvisionCitationInput) -> str:
        code = str(c.ext.get("code", ""))
        return CITATIONS.get(code) or f"Tex. {c.ext.get('code_name', c.instrument_title)}"

    def unit_sign(self, c: ProvisionCitationInput) -> str:
        return "art." if c.top_kind == "article" else "§"

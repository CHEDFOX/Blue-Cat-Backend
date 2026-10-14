"""Switzerland module: Swiss citations of federal enactments, in German, French and Italian.

* ``Art. 8 Abs. 2 BV`` / ``art. 8 al. 2 Cst.`` / ``art. 8 cpv. 2 Cost.``
* letters and numbers: ``Art. 5 lit. c Ziff. 1 DSG`` / ``art. 5 let. c ch. 1 LPD`` /
  ``art. 5 lett. c n. 1 LPD``
* final title of the Civil Code: ``Art. 1 SchlT ZGB`` / ``art. 1 Tit. fin. CC`` / ``art. 1 Tit. fin. CC``
* annexes: ``Anhang 1 DSG`` / ``annexe 1 LPD`` / ``allegato 1 LPD``

The abbreviation is the official short form published with each language version (Fedlex
``FRBRname/@shortForm``: BV, Cst., Cost.). Formatting conventions only; to be confirmed by a
Swiss reviewer before the CH profile is "reviewed".
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from ulis.jurisdictions.modules.base import JurisdictionModule, ProvisionCitationInput

TERMS = {
    "de": {"art": "Art.", "para": "Abs.", "let": "lit.", "num": "Ziff.", "annex": "Anhang", "final_title": "SchlT"},
    "fr": {"art": "art.", "para": "al.", "let": "let.", "num": "ch.", "annex": "annexe", "final_title": "Tit. fin."},
    "it": {"art": "art.", "para": "cpv.", "let": "lett.", "num": "n.", "annex": "allegato", "final_title": "Tit. fin."},
    "en": {"art": "Art.", "para": "para.", "let": "let.", "num": "no.", "annex": "Annex", "final_title": "Final Title"},
}


class ChExt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Number in the Classified Compilation of Federal Legislation (SR / RS), e.g. "101", "311.0".
    sr_number: str = Field(pattern=r"^\d+(?:\.\d+)*$")
    # Fedlex ELI of the consolidated work, e.g. https://fedlex.data.admin.ch/eli/cc/1999/404
    eli: str = Field(pattern=r"^https://fedlex\.data\.admin\.ch/eli/cc/[0-9A-Za-z]+/[0-9a-z_]+$")
    entry_into_force: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")


class ChModule(JurisdictionModule):
    id: ClassVar[str] = "ch"
    version: ClassVar[str] = "1.0.0"
    ext_model: ClassVar[type[BaseModel]] = ChExt

    def pinpoint(self, c: ProvisionCitationInput) -> str:
        t = TERMS.get(c.language, TERMS["de"])
        parts = [p for p in c.num_path if p != "final_title"]
        out: list[str] = []
        for i, part in enumerate(parts):
            kind, _, label = part.rpartition(":")
            if kind == "annex":
                out.append(f"{t['annex']} {label}".strip())
            elif not kind and i == 0:
                out.append(f"{t['art']} {label}")
            elif kind in ("para", "let", "num"):
                out.append(f"{t[kind]} {label}")
        if "final_title" in c.num_path:
            out.append(t["final_title"])
        return " ".join(out)

    def provision_citation(self, c: ProvisionCitationInput) -> str:
        pin = self.pinpoint(c)
        return f"{pin} {c.instrument_title}".strip() if pin else c.instrument_title

"""Czechia module: Czech citation conventions for acts of the Sbírka zákonů.

Czech practice puts the pinpoint before the name of the act, in the genitive:

* a code is cited by its name: ``§ 2910 občanského zákoníku``, ``§ 40 odst. 1 trestního zákoníku``,
  ``čl. 2 odst. 3 Ústavy České republiky``;
* any other act by its number: ``§ 5 odst. 2 zákona č. 110/2019 Sb.``, a decree as ``vyhlášky č. 123/2020 Sb.``;
* pinpoint abbreviations: ``čl.`` (článek), ``§`` (paragraf), ``odst.`` (odstavec), ``písm. a)`` (písmeno), ``bod``
  (bod), in that order, as in ``§ 73 odst. 3 písm. a) bod 1``.

The names of the codes are a small table of genitive forms (the source gives only the nominative title); an act that
is not in the table is cited by its number, which is always correct. Formatting conventions only, to be confirmed by
a Czech reviewer before the CZ profile is "reviewed" (e.g. whether the title should follow the number).
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from ulis.jurisdictions.modules.base import JurisdictionModule, ProvisionCitationInput

# genitive form of the name of an act, by "<year>/<number>" (acts commonly cited by name)
NAMES = {
    "1993/1": "Ústavy České republiky",
    "1993/2": "Listiny základních práv a svobod",
    "2012/89": "občanského zákoníku",
    "2009/40": "trestního zákoníku",
    "2006/262": "zákoníku práce",
    "1963/99": "občanského soudního řádu",
    "1961/141": "trestního řádu",
    "2012/90": "zákona o obchodních korporacích",
    "2002/150": "soudního řádu správního",
    "2006/500": "správního řádu",
    "2000/121": "autorského zákona",
    "2009/280": "daňového řádu",
}
PINPOINT = {"art": "čl.", "sec": "§", "sub": "odst.", "item": "bod"}
BARE = {"preamble": "preambule", "annex": "příloha"}
KINDS = {
    "constitutional_act": "ústavního zákona",
    "act": "zákona",
    "decree": "dekretu",
    "regulation": "předpisu",
    "notice": "předpisu",
}
REGULATION_WORDS = (
    ("nařízení vlády", "nařízení vlády"),
    ("vyhláška", "vyhlášky"),
    ("vládní vyhláška", "vládní vyhlášky"),
    ("nařízení", "nařízení"),
)


class CzExt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    year: int  # year in the number of the act: 89/2012 Sb. -> 2012
    number: str  # 89
    collection: str  # "sb" (Sbírka zákonů)
    kind: str | None = None  # e-Sbírka's kind of act (druh aktu), e.g. ZAKON, VYHLASKA, NARIZVLA


class CzModule(JurisdictionModule):
    id: ClassVar[str] = "cz"
    version: ClassVar[str] = "1.0.0"
    ext_model: ClassVar[type[BaseModel]] = CzExt

    def pinpoint(self, c: ProvisionCitationInput) -> str:
        out: list[str] = []
        for part in c.num_path:
            kind, _, label = part.partition(":")
            if kind == "pt" and label:
                out.append(f"písm. {label})")
            elif kind in PINPOINT and label:
                out.append(f"{PINPOINT[kind]} {label}")
            elif kind in BARE:
                out.append(BARE[kind])
        return " ".join(out)

    def act_name(self, c: ProvisionCitationInput) -> str:
        key = f"{c.ext.get('year')}/{c.ext.get('number')}"
        if key in NAMES:
            return NAMES[key]
        if not (c.ext.get("number") and c.ext.get("year")):
            return c.instrument_title
        genitive = KINDS.get(c.instrument_type, "předpisu")
        if c.instrument_type in ("regulation", "notice"):
            lead = c.instrument_title.lower()
            genitive = next((g for nominative, g in REGULATION_WORDS if lead.startswith(nominative)), genitive)
        return f"{genitive} č. {c.ext['number']}/{c.ext['year']} Sb."

    def provision_citation(self, c: ProvisionCitationInput) -> str:
        pin = self.pinpoint(c)
        name = self.act_name(c)
        return f"{pin} {name}" if pin else name

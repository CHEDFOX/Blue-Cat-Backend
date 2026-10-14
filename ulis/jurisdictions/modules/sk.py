"""Slovakia module: Slovak citation conventions for acts of the Zbierka zákonov.

Slovak practice puts the pinpoint before the name of the act, in the genitive:

* a code is cited by its name: ``§ 420 Občianskeho zákonníka``, ``§ 144 ods. 1 Trestného zákona``,
  ``čl. 2 ods. 1 Ústavy Slovenskej republiky``;
* any other act by its number: ``§ 5 ods. 2 zákona č. 18/2018 Z. z.`` (``Zb.`` for acts of 1992 and earlier, e.g.
  ``zákona č. 40/1964 Zb.``);
* pinpoint abbreviations: ``čl.`` (článok), ``§`` (paragraf), ``ods.`` (odsek), ``písm. a)`` (písmeno), ``bod`` (bod),
  in that order, as in ``§ 33 ods. 1 písm. b) bod 2``.

The names of the codes are a small table of genitive forms (the source gives only the nominative title); an act that
is not in the table is cited by its number, which is always correct. Formatting conventions only, to be confirmed by a
Slovak reviewer before the SK profile is "reviewed" (e.g. whether the full title should follow the number).
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from ulis.jurisdictions.modules.base import JurisdictionModule, ProvisionCitationInput

# genitive form of the name of an act, by "<year>/<number>" (acts commonly cited by name)
NAMES = {
    "1992/460": "Ústavy Slovenskej republiky",
    "1964/40": "Občianskeho zákonníka",
    "1991/513": "Obchodného zákonníka",
    "2005/300": "Trestného zákona",
    "2005/301": "Trestného poriadku",
    "2001/311": "Zákonníka práce",
    "2015/160": "Civilného sporového poriadku",
    "2015/161": "Civilného mimosporového poriadku",
    "2015/162": "Správneho súdneho poriadku",
    "1995/233": "Exekučného poriadku",
    "1967/71": "Správneho poriadku",
    "2015/185": "Autorského zákona",
}
PINPOINT = {"art": "čl.", "sec": "§", "sub": "ods.", "item": "bod"}
BARE = {"preamble": "preambula", "annex": "príloha"}
KINDS = (
    ("ústavný zákon", "ústavného zákona"),
    ("zákon", "zákona"),
    ("nariadenie vlády", "nariadenia vlády"),
    ("vyhláška", "vyhlášky"),
    ("nariadenie", "nariadenia"),
    ("opatrenie", "opatrenia"),
)


class SkExt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    year: int  # year in the number of the act: 311/2001 Z. z. -> 2001
    number: str  # 311
    collection: str  # "Z. z." (Zbierka zákonov since 1993) or "Zb." (until 1992)


class SkModule(JurisdictionModule):
    id: ClassVar[str] = "sk"
    version: ClassVar[str] = "1.0.0"
    ext_model: ClassVar[type[BaseModel]] = SkExt

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
        lead = c.instrument_title.lower()
        genitive = next((g for nominative, g in KINDS if lead.startswith(nominative)), "zákona")
        if c.ext.get("number") and c.ext.get("year"):
            return f"{genitive} č. {c.ext['number']}/{c.ext['year']} {c.ext.get('collection') or 'Z. z.'}"
        return c.instrument_title

    def provision_citation(self, c: ProvisionCitationInput) -> str:
        pin = self.pinpoint(c)
        name = self.act_name(c)
        return f"{pin} {name}" if pin else name

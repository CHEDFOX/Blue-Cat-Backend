"""Finland module: citations of consolidated statutes in Finnish and Swedish.

One documented style, title first, with the statute number as published in the Statute Book
(``number/year``):

* ``Suomen perustuslaki (731/1999) 6 § 2 mom.`` / ``Finlands grundlag (731/1999) 6 § 2 mom.``
* Sections numbered within chapters carry the chapter:
  ``Rikoslaki (39/1889) 21 luku 1 §`` / ``Strafflag (39/1889) 21 kap. 1 §``
* Paragraphs (kohta / punkt) and subparagraphs (alakohta / underpunkt):
  ``Työsopimuslaki (55/2001) 13 luku 7 § 1 mom. 3 a kohta`` /
  ``Arbetsavtalslag (55/2001) 13 kap. 7 § 1 mom. 3 a punkten``;
  ``Rikoslaki (39/1889) 34 luku 4 § 1 mom. 4 kohdan a alakohta`` /
  ``Strafflag (39/1889) 34 kap. 4 § 1 mom. 4 punkten underpunkt a``

Customary abbreviations (``RL 21:1``, ``PL 6 §``) and inflected forms (``rikoslain 21 luvun
1 §``, ``6 § grundlagen``) are not generated: the source gives neither. Formatting conventions
only; to be confirmed by a Finnish reviewer before the FI profile is "reviewed".
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from ulis.jurisdictions.modules.base import JurisdictionModule, ProvisionCitationInput

WORDS = {
    "fi": {"ch": "luku", "ss": "mom.", "p": "kohta", "p_of": "kohdan", "sp": "alakohta"},
    "sv": {"ch": "kap.", "ss": "mom.", "p": "punkten", "p_of": "punkten", "sp": "underpunkt"},
}


class FiExt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    statute_number: str  # as printed in the source, e.g. "731/1999"
    year: int
    number: str  # Finlex number, e.g. "731" or "39-001"
    sections_per_chapter: bool = False  # sections restart in each chapter (cite the chapter)
    eli: str | None = None


def _parts(num_path: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in num_path:
        kind, _, label = part.partition(":")
        if label:
            out[kind] = label
    return out


class FiModule(JurisdictionModule):
    id: ClassVar[str] = "fi"
    version: ClassVar[str] = "1.0.0"
    ext_model: ClassVar[type[BaseModel]] = FiExt

    def pinpoint(self, c: ProvisionCitationInput) -> str:
        words = WORDS["sv" if c.language == "sv" else "fi"]
        parts = _parts(c.num_path)
        out: list[str] = []
        if "ch" in parts and c.ext.get("sections_per_chapter"):
            out.append(f"{parts['ch']} {words['ch']}")
        if "s" in parts:
            out.append(f"{parts['s']} §")
        if "ss" in parts:
            out.append(f"{parts['ss']} {words['ss']}")
        if "p" in parts:
            if "sp" in parts:
                out.append(f"{parts['p']} {words['p_of']} {parts['sp']} {words['sp']}")
                if c.language == "sv":
                    out[-1] = f"{parts['p']} {words['p']} {words['sp']} {parts['sp']}"
            else:
                out.append(f"{parts['p']} {words['p']}")
        return " ".join(out)

    def provision_citation(self, c: ProvisionCitationInput) -> str:
        number = c.ext.get("statute_number")
        title = f"{c.instrument_title} ({number})" if number else c.instrument_title
        pin = self.pinpoint(c)
        return f"{title} {pin}" if pin else title

"""Sweden module: citations of statutes (SFS) in Swedish.

Swedish order: the pinpoint first, then the statute's name.

* Codes (balkar) and fundamental laws by name: ``3 kap. 1 § brottsbalken``,
  ``1 kap. 2 § regeringsformen``;
* statutes with an established short name: ``36 § avtalslagen``;
* other named statutes with their SFS number: ``9 § förvaltningslagen (2017:900)``;
* others by their title: ``7 § lagen (1982:80) om anställningsskydd``.

The chapter is cited only when sections restart in each chapter (detected from the document).
Paragraphs (stycken) are cited by ordinal, points by number, sub-points by letter and dash items
by ordinal: ``2 kap. 1 § första stycket 4 regeringsformen``, ``1 kap. 3 § första stycket 4 lagen
(2018:218) med kompletterande bestämmelser till EU:s dataskyddsförordning``, ``30 kap. 1 § första
stycket andra strecksatsen brottsbalken``. A paragraph is always cited, also in a section that has
only one. Transitional provisions: ``regeringsformen, övergångsbestämmelserna till SFS 1976:871
punkt 2``. Customary abbreviations (BrB, RF, LAS) are not generated.

Formatting conventions only; to be confirmed by a Swedish reviewer before the SE profile is
"reviewed".
"""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict

from ulis.jurisdictions.modules.base import JurisdictionModule, ProvisionCitationInput

ORDINALS = [
    "första",
    "andra",
    "tredje",
    "fjärde",
    "femte",
    "sjätte",
    "sjunde",
    "åttonde",
    "nionde",
    "tionde",
    "elfte",
    "tolfte",
    "trettonde",
    "fjortonde",
    "femtonde",
    "sextonde",
    "sjuttonde",
    "artonde",
    "nittonde",
    "tjugonde",
]


class SeExt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sfs_number: str  # e.g. "1962:700"
    year: int
    number: int
    citation_name: str  # e.g. "brottsbalken", "lagen (1982:80) om anställningsskydd"
    citation_name_basis: Literal["title", "fundamental_law", "established_short_name"] = "title"
    sections_per_chapter: bool = False  # sections restart in each chapter (cite the chapter)
    consolidated_to: str | None = None  # "SFS 2026:1656": amendments worked into the consolidation
    department: str | None = None
    reprints: str | None = None  # omtryck
    editorial_remark: str | None = None  # e.g. "Rättelsesida 2019:310 har iakttagits."
    record_published: str | None = None  # when the Riksdag published the record (ISO date-time)


def ordinal(n: str) -> str:
    """``2`` -> ``andra``."""
    try:
        i = int(n)
    except ValueError:
        return n
    return ORDINALS[i - 1] if 1 <= i <= len(ORDINALS) else f"{i}:e"


def _parts(num_path: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in num_path:
        kind, _, label = part.partition(":")
        if kind:
            out[kind] = label
    return out


class SeModule(JurisdictionModule):
    id: ClassVar[str] = "se"
    version: ClassVar[str] = "1.0.0"
    ext_model: ClassVar[type[BaseModel]] = SeExt

    def pinpoint(self, c: ProvisionCitationInput) -> str:
        parts = _parts(c.num_path)
        if "tr" in parts:
            head = f"övergångsbestämmelserna till SFS {parts['tr']}" if parts["tr"] else "övergångsbestämmelserna"
            return f"{head} punkt {parts['p']}" if "p" in parts else head
        out: list[str] = []
        if "ch" in parts and c.ext.get("sections_per_chapter"):
            out.append(f"{parts['ch']} kap.")
        if "s" in parts:
            out.append(f"{parts['s']} §")
        if "st" in parts:
            out.append(f"{ordinal(parts['st'])} stycket")
        if "p" in parts:
            out.append(parts["p"])
        if "sp" in parts:
            out.append(f"{parts['sp']})")
        if "ind" in parts:
            out.append(f"{ordinal(parts['ind'])} strecksatsen")
        return " ".join(out)

    def provision_citation(self, c: ProvisionCitationInput) -> str:
        name = str(c.ext.get("citation_name") or c.instrument_title)
        pin = self.pinpoint(c)
        if not pin:
            return name
        if pin.startswith("övergångsbestämmelserna"):
            return f"{name}, {pin}"
        return f"{pin} {name}"

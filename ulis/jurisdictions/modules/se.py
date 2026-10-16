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

Case law reports are cited by their official report number with the paragraph (punkt) of the court's reasons:
``NJA 2026 s. 347 p. 7``, ``HFD 2026 ref. 35 p. 3``, ``MIG 2026:9``; a report without a number by the court, date
and case number (``Högsta domstolen 2026-06-12, mål nr FT 1040-25``).

Formatting conventions only; to be confirmed by a Swedish reviewer before the SE profile is
"reviewed".
"""

from __future__ import annotations

from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from ulis.jurisdictions.modules.base import JudgmentCitationInput, JurisdictionModule, ProvisionCitationInput


class SeJudgmentExt(BaseModel):
    """Extension data of a case law report from Domstolsverket's open data (Sök rättspraxis)."""

    model_config = ConfigDict(extra="forbid")
    publication_id: str
    group_id: str | None = None  # links the report to the judgment/decision it reports
    court_code: str  # the API's court code, e.g. HDO, HFD, HSV
    court_name: str | None = None  # the actual court, e.g. "Svea hovrätt" (the ULIS court may be a group)
    type: str | None = None  # PREJUDIKAT, VAGLEDANDE_MEN_EJ_PREJUDICERANDE ...
    references: list[str] = Field(default_factory=list)  # e.g. ["NJA 2026:30", "NJA 2026 s. 347"]
    ecli: str | None = None
    case_numbers: list[str] = Field(default_factory=list)
    labour_court_number: str | None = None
    nickname: str | None = None  # benämning, e.g. "Bryggan på Sippsö"
    keywords: list[str] = Field(default_factory=list)
    legal_areas: list[str] = Field(default_factory=list)
    statutes: list[dict[str, Any]] = Field(default_factory=list)  # {"sfs": "1972:207", "reference": "3 kap. 4 § ..."}
    cited_cases: list[str] = Field(default_factory=list)  # free text, as published
    literature: list[dict[str, Any]] = Field(default_factory=list)
    preparatory_works: list[str] = Field(default_factory=list)
    published: str | None = None


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
    version: ClassVar[str] = "1.2.0"
    ext_model: ClassVar[type[BaseModel]] = SeExt

    def validate_ext(self, ext: dict[str, Any]) -> dict[str, Any]:
        # A report's ext (Sök rättspraxis) is recognised by its court code; a statute's by its SFS number.
        if "court_code" in ext:
            return SeJudgmentExt.model_validate(ext).model_dump(exclude_none=True)
        return super().validate_ext(ext)

    def pinpoint(self, c: ProvisionCitationInput) -> str:
        parts = _parts(c.num_path)
        if "tr" in parts:
            head = f"övergångsbestämmelserna till SFS {parts['tr']}" if parts["tr"] else "övergångsbestämmelserna"
            return f"{head} punkt {parts['p']}" if "p" in parts else head
        if "bil" in parts:  # an annex: "bilaga 2", or "bilagan" when the statute has one unnumbered annex
            return f"bilaga {parts['bil']}" if parts["bil"] else "bilagan"
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
        if pin.startswith("bilaga"):
            return f"{pin} till {name}"
        if pin.startswith("övergångsbestämmelserna"):
            return f"{name}, {pin}"
        return f"{pin} {name}"

    def judgment_citation(self, c: JudgmentCitationInput) -> str:
        """``NJA 2026 s. 347 p. 7`` (the report number, then the paragraph of the reasons); without a report number
        the court, date and case number."""
        pin = f" p. {c.paragraph}" if c.paragraph else ""
        if c.neutral_citation:
            return f"{c.neutral_citation}{pin}"
        court = str((c.ext or {}).get("court_name") or c.court_name)
        text = f"{court} {c.decision_date.isoformat()}"
        if c.docket:
            text += f", mål nr {c.docket}"
        return f"{text}{pin}"

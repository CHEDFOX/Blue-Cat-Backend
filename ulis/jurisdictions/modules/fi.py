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

Decisions of the Data Protection Ombudsman (Finlex): ``Tietosuojavaltuutettu, päätös 31.3.2026, dnro
TSV/5875/2024`` / ``Dataombudsmannen, beslut 13.12.2022, dnr 4672/161/22``.

Customary abbreviations (``RL 21:1``, ``PL 6 §``) and inflected forms (``rikoslain 21 luvun
1 §``, ``6 § grundlagen``) are not generated: the source gives neither. Formatting conventions
only; to be confirmed by a Finnish reviewer before the FI profile is "reviewed".
"""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from ulis.jurisdictions.modules.base import JudgmentCitationInput, JurisdictionModule, ProvisionCitationInput

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


class FiJudgmentExt(BaseModel):
    """Extension data of a decision of the Data Protection Ombudsman (Finlex open data)."""

    model_config = ConfigDict(extra="forbid")
    subtype: str  # Finlex document type, e.g. "data-protection-ombudsman-decision"
    year: int  # the year in the Finlex identifier (the case year, not always the decision year)
    number: str
    diary_number: str | None = None  # dnro, e.g. "TSV/5875/2024"
    legal_basis: str | None = None  # Finlex's classification, e.g. "EU:n yleisen tietosuoja-asetuksen mukainen päätös"
    keywords: list[str] = Field(default_factory=list)
    date_produced: str | None = None  # when Finlex produced this expression


# Citation forms of the deciding bodies in the profile: (Finnish, Swedish) names, "decision" word, diary number word.
BODIES = {"FI-TSV": ("Tietosuojavaltuutettu", "Dataombudsmannen")}
DECISION = {"fi": ("päätös", "dnro"), "sv": ("beslut", "dnr")}


def _parts(num_path: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in num_path:
        kind, _, label = part.partition(":")
        if label:
            out[kind] = label
    return out


class FiModule(JurisdictionModule):
    id: ClassVar[str] = "fi"
    version: ClassVar[str] = "1.1.0"
    ext_model: ClassVar[type[BaseModel]] = FiExt

    def validate_ext(self, ext: dict[str, Any]) -> dict[str, Any]:
        # A decision's ext (Finlex judgment) is recognised by its document type; a statute's by its number.
        if "subtype" in ext:
            return FiJudgmentExt.model_validate(ext).model_dump(exclude_none=True)
        return super().validate_ext(ext)

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

    def judgment_citation(self, c: JudgmentCitationInput) -> str:
        """``Tietosuojavaltuutettu, päätös 31.3.2026, dnro TSV/5875/2024`` (Swedish: ``Dataombudsmannen, beslut ...``).

        Decisions have no numbered paragraphs; a paragraph number, if ever given, follows as ``kohta`` / ``punkt``.
        """
        lang = "sv" if c.language == "sv" else "fi"
        body = BODIES.get(c.court_id, (c.court_name, c.court_name))[1 if lang == "sv" else 0]
        word, diary_word = DECISION[lang]
        d = c.decision_date
        text = f"{body}, {word} {d.day}.{d.month}.{d.year}"
        if c.docket:
            text += f", {diary_word} {c.docket}"
        if c.paragraph:
            text += f", {'punkt' if lang == 'sv' else 'kohta'} {c.paragraph}"
        return text

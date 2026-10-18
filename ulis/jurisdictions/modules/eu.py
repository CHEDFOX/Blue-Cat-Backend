"""European Union module: citations of EU law in English, French and German.

Conventions (after the Interinstitutional Style Guide and common practice; to be confirmed by
an EU-law reviewer before the EU profile is "reviewed"):

* acts: ``Regulation (EU) 2016/679, art 6(1)(a)`` /
  ``Règlement (UE) 2016/679, art. 6, par. 1, point a)`` /
  ``Verordnung (EU) 2016/679, Art. 6 Abs. 1 Buchst. a``;
* acts numbered before 2015: ``Regulation (EC) No 593/2008``, ``Directive 2011/83/EU``,
  ``Directive 93/13/EEC`` (two-digit year before 1999);
* points numbered with digits: ``art 4(1)`` / ``art. 4, point 1)`` / ``Art. 4 Nr. 1``;
  subpoints ``(a)(i)`` / ``point a) i)`` / ``Buchst. a Ziff. i``; points of a later subparagraph:
  ``art 6(1), subpara 2, point (a)`` / ``art. 6, par. 1, al. 2, point a)`` /
  ``Art. 6 Abs. 1 UAbs. 2 Buchst. a``;
* Treaties: ``TFEU, art 101(1)`` / ``TFUE, art. 101, par. 1`` / ``AEUV, Art. 101 Abs. 1``;
  the Charter by its title in the language (``Charter of Fundamental Rights of the European
  Union, art 8(1)``);
* annexes: ``Annex III`` / ``annexe III`` / ``Anhang III``.

Pinpoints are rendered from the language-neutral eId segments (``art_6__para_1__point_a``), so
every language version of a provision gets the same structure. Languages other than English,
French and German fall back to the English pattern with the instrument's own title.
"""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict

from ulis.jurisdictions.modules.base import JudgmentCitationInput, JurisdictionModule, ProvisionCitationInput

ActType = Literal["regulation", "directive", "decision"]
TYPE_WORD = {
    "regulation": {"en": "Regulation", "fr": "Règlement", "de": "Verordnung"},
    "directive": {"en": "Directive", "fr": "Directive", "de": "Richtlinie"},
    "decision": {"en": "Decision", "fr": "Décision", "de": "Beschluss"},
}
DOMAIN = {
    "EU": {"en": "EU", "fr": "UE", "de": "EU"},
    "EC": {"en": "EC", "fr": "CE", "de": "EG"},
    "EEC": {"en": "EEC", "fr": "CEE", "de": "EWG"},
    "Euratom": {"en": "Euratom", "fr": "Euratom", "de": "Euratom"},
    # Second and third pillar acts before the Treaty of Lisbon: 2005/495/CFSP, 2002/584/JHA.
    "CFSP": {"en": "CFSP", "fr": "PESC", "de": "GASP"},
    "JHA": {"en": "JHA", "fr": "JAI", "de": "JI"},
}
# The domain code as printed in each language's act number -> the language-neutral code.
DOMAIN_CODES = {
    "EU": "EU",
    "UE": "EU",
    "EC": "EC",
    "CE": "EC",
    "EG": "EC",
    "EEC": "EEC",
    "CEE": "EEC",
    "EWG": "EEC",
    "EURATOM": "Euratom",
    "CFSP": "CFSP",
    "PESC": "CFSP",
    "GASP": "CFSP",
    "JHA": "JHA",
    "JAI": "JHA",
    "JI": "JHA",
}
NUMBER_WORD = {"en": "No", "fr": "n°", "de": "Nr."}
TREATY_ABBREVIATION = {
    "TEU": {"en": "TEU", "fr": "TUE", "de": "EUV"},
    "TFEU": {"en": "TFEU", "fr": "TFUE", "de": "AEUV"},
}
ANNEX_WORD = {"en": "Annex", "fr": "annexe", "de": "Anhang"}
CASE_WORD = {"en": "Case", "fr": "Affaire", "de": "Rechtssache"}
PARAGRAPH_WORD = {"en": "para", "fr": "point", "de": "Rn."}
OPERATIVE_PART = {"en": "operative part, point", "fr": "dispositif, point", "de": "Tenor, Nr."}
LANGS = ("en", "fr", "de")


class EuExt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    celex: str
    eli: str | None = None
    # Publications Office resource type (REG, DIR, DEC, TREATY, JUDG, ...).
    resource_type: str | None = None
    act_type: ActType | None = None
    year: int | None = None
    number: str | None = None
    domain: Literal["EU", "EC", "EEC", "Euratom", "CFSP", "JHA"] | None = None
    treaty: Literal["TEU", "TFEU", "CHARTER"] | None = None
    # A consolidated version of an act: the CELEX number of the base act (``celex`` is the version's own).
    base_celex: str | None = None
    ecli: str | None = None
    # Judgments: the case designation in each language version as Cellar gives it ("Case C-362/14",
    # "Affaire C-362/14"), the label by which the Court itself cites the case, and the language of
    # the case (its text is the authentic one).
    designations: dict[str, str] | None = None
    short_name: str | None = None
    procedure_language: str | None = None


def designation(
    act_type: str | None, year: int | None, number: str | None, domain: str | None, language: str
) -> str | None:
    """``Regulation (EU) 2016/679``, ``Règlement (CE) no 593/2008``, ``Richtlinie 2011/83/EU``.

    Acts numbered from 2015 put the domain first and the year first; earlier regulations are
    ``No number/year`` and earlier directives and decisions ``year/number/domain`` (two-digit
    years before 1999). Returns None when a part is missing or the language is not supported.
    """
    if act_type not in TYPE_WORD or not year or not number or domain not in DOMAIN or language not in LANGS:
        return None
    word, dom = TYPE_WORD[act_type][language], DOMAIN[domain][language]
    if year >= 2015:
        return f"{word} ({dom}) {year}/{number}"
    if act_type == "regulation":
        return f"{word} ({dom}) {NUMBER_WORD[language]} {number}/{year}"
    short_year = str(year) if year >= 1999 else str(year)[2:]
    return f"{word} {short_year}/{number}/{dom}"


def _segments(eid: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for seg in eid.split("__"):
        kind, _, value = seg.partition("_")
        out.append((kind, value))
    return out


def pinpoint(eid: str, language: str) -> str:
    """Render a pinpoint from language-neutral eId segments (see module docstring)."""
    lang = language if language in LANGS else "en"
    segments = _segments(eid)
    if not segments or not segments[0][1] and segments[0][0] != "anx":
        return ""
    head_kind, head = segments[0]
    if head_kind == "anx":
        return f"{ANNEX_WORD[lang]} {head}".strip() if head else ANNEX_WORD[lang]
    if head_kind != "art":
        return ""
    out = {"en": f"art {head}", "fr": f"art. {head}", "de": f"Art. {head}"}[lang]
    after_subpara = False
    for kind, value in segments[1:]:
        if kind == "para":
            out += {"en": f"({value})", "fr": f", par. {value}", "de": f" Abs. {value}"}[lang]
        elif kind == "subpara":
            out += {"en": f", subpara {value}", "fr": f", al. {value}", "de": f" UAbs. {value}"}[lang]
            after_subpara = True
        elif kind == "point":
            if lang == "en":
                out += f", point ({value})" if after_subpara else f"({value})"
            elif lang == "fr":
                out += f", point {value})"
            else:
                out += f" Nr. {value}" if value[:1].isdigit() else f" Buchst. {value}"
            after_subpara = False
        elif kind == "subpoint":
            out += {"en": f"({value})", "fr": f" {value})", "de": f" Ziff. {value}"}[lang]
    return out


class EuModule(JurisdictionModule):
    id: ClassVar[str] = "eu"
    version: ClassVar[str] = "1.1.0"
    ext_model: ClassVar[type[BaseModel]] = EuExt

    def pinpoint(self, c: ProvisionCitationInput) -> str:
        return pinpoint(c.eid, c.language)

    def instrument_name(self, c: ProvisionCitationInput) -> str:
        """The instrument as cited in ``c.language``."""
        ext = c.ext or {}
        treaty = ext.get("treaty")
        if treaty in TREATY_ABBREVIATION and c.language in LANGS:
            return TREATY_ABBREVIATION[treaty][c.language]
        name = designation(ext.get("act_type"), ext.get("year"), ext.get("number"), ext.get("domain"), c.language)
        return name or c.instrument_title

    def provision_citation(self, c: ProvisionCitationInput) -> str:
        pin = self.pinpoint(c)
        name = self.instrument_name(c)
        return f"{name}, {pin}" if pin else name

    def judgment_citation(self, c: JudgmentCitationInput) -> str:
        """``Case C-362/14 Schrems (EU:C:2015:650), para 94`` (ECLI without its ``ECLI:`` prefix).

        The designation ("Case C-362/14", "Affaire C-362/14", "Joined cases C-46/93 and C-48/93") is
        the one Cellar records for the language version; the name is the label by which the Court
        cites the case (else the parties). Points of the operative part are numbered ``op-N``.
        """
        ext = c.ext or {}
        lang = c.language if c.language in LANGS else "en"
        designation = (ext.get("designations") or {}).get(c.language) or (
            f"{CASE_WORD[lang]} {c.docket}" if c.docket else ""
        )
        name = ext.get("short_name") or c.case_name
        reference = (c.neutral_citation or "").removeprefix("ECLI:") or c.decision_date.isoformat()
        text = f"{' '.join(p for p in (designation, name) if p)} ({reference})"
        if not c.paragraph:
            return text
        if c.paragraph.startswith("op-"):
            return f"{text}, {OPERATIVE_PART[lang]} {c.paragraph[3:]}"
        return f"{text}, {PARAGRAPH_WORD[lang]} {c.paragraph}"

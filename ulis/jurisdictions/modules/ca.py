"""Canada module: McGill Guide-style citations for federal Acts and regulations, in English and French.

Acts:

* ``Access to Information Act, RSC 1985, c A-1, s 4(1)(a)``
* ``Loi sur l’accès à l’information, LRC 1985, c A-1, art 4(1)a)``
* Definitions: ``Access to Information Act, RSC 1985, c A-1, s 3 “business day”(a)``
* Acts outside the Revised Statutes: ``..., SC 2000, c 5, s 2(1)`` / ``..., LC 2000, c 5, art 2(1)``
* Supplements to the Revised Statutes: ``Divorce Act, RSC 1985, c 3 (2nd Supp.), s 8(1)``

Regulations (the instrument number is stored in its English form and rendered per language):

* ``Leamy Lake Navigation Channel Regulations, SOR/98-547, s 2`` /
  ``Règlement sur le chenal de navigation du lac Leamy, DORS/98-547, art 2``
* Statutory instruments: ``..., SI/2000-82`` / ``..., TR/2000-82``
* Consolidated Regulations of Canada, 1978: ``Armed Forces Postal Regulations, CRC, c 1274, s 4`` /
  ``Règlement des postes pour les forces armées, CRC, c 1274, art 4``
* Schedules: ``..., SOR/98-547, Sch 1`` / ``..., DORS/98-547, ann 1``; the making order has no pinpoint.

Formatting conventions only; to be confirmed by a Canadian reviewer before the CA profile is "reviewed".
"""

from __future__ import annotations

import re
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from ulis.jurisdictions.modules.base import JurisdictionModule, ProvisionCitationInput

PREFIX = {"en": {"section": "s", "schedule": "Sch"}, "fr": {"section": "art", "schedule": "ann"}}
ISO_DATE = r"^\d{4}-\d{2}-\d{2}$"


class CaExt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Acts: the chapter of the consolidation (A-1) and, when known, the statute volume it is cited from.
    consolidated_number: str | None = None
    statute_series: Literal["RSC", "SC"] | None = None  # Revised Statutes of Canada / Statutes of Canada
    statute_year: int | None = None
    statute_chapter: str | None = None
    # Regulations: the instrument number in its English form (SOR/2001-227, SI/2000-100, C.R.C., c. 870), its series,
    # the registration date, the Acts it is made under (their consolidated chapters) and the order that made it.
    instrument_number: str | None = None
    instrument_series: Literal["SOR", "SI", "CRC"] | None = None
    registration_date: str | None = Field(default=None, pattern=ISO_DATE)
    enabling_acts: list[str] | None = None
    order_number: str | None = None
    order_date: str | None = Field(default=None, pattern=ISO_DATE)


def _subs(parts: list[str], fr: bool) -> str:
    """Render language-neutral labels: ``(1)(a)(i)`` in English, ``(1)a)(i)`` in French."""
    out, prev = "", ""
    for part in parts:
        if part.startswith("def:"):  # a defined term: s 3 “business day”(a) / art 3 « jour ouvrable » a)
            en, _, fr_term = part[4:].partition("|")
            out += f" « {fr_term or en} »" if fr else f" “{en or fr_term}”"
            prev = "definition"
            continue
        core = part.strip().strip("()").strip()
        if core[:1].isdigit():
            kind = "subsection"
        elif core[:1].islower():
            kind = "subparagraph" if prev == "paragraph" else "paragraph"
        else:
            kind = "clause"
        label = f"{core})" if fr and kind == "paragraph" else f"({core})"
        out += f" {label}" if fr and prev == "definition" else label
        prev = kind
    return out


def regulation_citation_number(number: str, language: str) -> str:
    """McGill form of an instrument number: ``SOR/98-547`` / ``DORS/98-547``, ``SI/2000-82`` / ``TR/2000-82``,
    ``C.R.C., c. 1274`` -> ``CRC, c 1274``, ``P.C. 1945-7421`` -> ``PC 1945-7421`` / ``CP 1945-7421``."""
    if language == "fr":
        number = re.sub(r"^SOR/", "DORS/", number)
        number = re.sub(r"^SI/", "TR/", number)
        number = re.sub(r"^P\.C\.", "C.P.", number)
        number = number.replace(" s. ", " art. ")
    return number.replace(".", "")


class CaModule(JurisdictionModule):
    id: ClassVar[str] = "ca"
    version: ClassVar[str] = "1.1.0"
    ext_model: ClassVar[type[BaseModel]] = CaExt

    def pinpoint(self, c: ProvisionCitationInput) -> str:
        parts = [p for p in c.num_path if p]
        lang = "fr" if c.language == "fr" else "en"
        if c.top_kind == "order":  # the making order is unnumbered
            return ""
        if c.top_kind == "schedule":
            head = f"{PREFIX[lang]['schedule']} {parts[0]}" if parts else PREFIX[lang]["schedule"]
            if len(parts) > 1:
                head += f", {PREFIX[lang]['section']} {parts[1]}" + _subs(parts[2:], lang == "fr")
            return head
        if not parts:
            return ""
        return f"{PREFIX[lang]['section']} {parts[0]}" + _subs(parts[1:], lang == "fr")

    def provision_citation(self, c: ProvisionCitationInput) -> str:
        series = c.ext.get("statute_series")
        if c.ext.get("instrument_number"):
            source = regulation_citation_number(str(c.ext["instrument_number"]), c.language)
        elif series and c.ext.get("statute_year") and c.ext.get("statute_chapter"):
            name = {"RSC": "LRC", "SC": "LC"}[series] if c.language == "fr" else series
            source = f"{name} {c.ext['statute_year']}, c {c.ext['statute_chapter']}"
        else:
            source = ""
        title = f"{c.instrument_title}, {source}" if source else c.instrument_title
        pin = self.pinpoint(c)
        return f"{title}, {pin}" if pin else title

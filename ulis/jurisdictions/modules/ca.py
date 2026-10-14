"""Canada module: McGill Guide-style citations for federal Acts, in English and French.

* ``Access to Information Act, RSC 1985, c A-1, s 4(1)(a)``
* ``Loi sur l’accès à l’information, LRC 1985, c A-1, art 4(1)a)``
* Definitions: ``Access to Information Act, RSC 1985, c A-1, s 3 “business day”(a)``
* Acts outside the Revised Statutes: ``..., SC 2000, c 5, s 2(1)`` / ``..., LC 2000, c 5, art 2(1)``
* Supplements to the Revised Statutes: ``Divorce Act, RSC 1985, c 3 (2nd Supp.), s 8(1)``

Formatting conventions only; to be confirmed by a Canadian reviewer before the CA profile is "reviewed".
"""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict

from ulis.jurisdictions.modules.base import JurisdictionModule, ProvisionCitationInput

PREFIX = {"en": {"section": "s", "schedule": "Sch"}, "fr": {"section": "art", "schedule": "ann"}}


class CaExt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    consolidated_number: str
    statute_series: Literal["RSC", "SC"] | None = None  # Revised Statutes of Canada / Statutes of Canada
    statute_year: int | None = None
    statute_chapter: str | None = None


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


class CaModule(JurisdictionModule):
    id: ClassVar[str] = "ca"
    version: ClassVar[str] = "1.0.0"
    ext_model: ClassVar[type[BaseModel]] = CaExt

    def pinpoint(self, c: ProvisionCitationInput) -> str:
        parts = [p for p in c.num_path if p]
        lang = "fr" if c.language == "fr" else "en"
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
        if series and c.ext.get("statute_year") and c.ext.get("statute_chapter"):
            name = {"RSC": "LRC", "SC": "LC"}[series] if c.language == "fr" else series
            source = f"{name} {c.ext['statute_year']}, c {c.ext['statute_chapter']}"
        else:
            source = ""
        title = f"{c.instrument_title}, {source}" if source else c.instrument_title
        pin = self.pinpoint(c)
        return f"{title}, {pin}" if pin else title

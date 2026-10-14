"""Italy module: citations of State legislation in the Italian convention.

* article: ``art. 2043 c.c.``, ``art. 575 c.p.``, ``art. 2-bis, L. 241/1990``
* comma and lettera: ``art. 3, comma 1, Cost.``, ``art. 2, comma 1, lett. a), d.lgs. 196/2003``
* numero within a lettera or comma: ``art. 640, comma 2, n. 1, c.p.``
* preliminary provisions of the Codice civile (preleggi): ``art. 12 disp. prel. c.c.``
* transitional and final provisions of the Constitution: ``XIII disp. trans. fin. Cost.``

The abbreviation of the act is stored with the instrument (``ext.citation_label``): ``Cost.``,
``c.c.``, ``c.p.``, and ``<type> <number>/<year>`` for other acts (``L. 300/1970``,
``d.lgs. 196/2003``). The separator is a space after a bare article and a comma otherwise.
Formatting conventions only; to be confirmed by an Italian reviewer before the IT profile is "reviewed".
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from ulis.jurisdictions.modules.base import JurisdictionModule, ProvisionCitationInput

_DATE = r"^\d{4}-\d{2}-\d{2}$"


class ItExt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Kind of act as in its URN:NIR ("legge", "decreto.legislativo", "costituzione", "codice.civile", ...).
    act_type: str = Field(pattern=r"^[a-z.]+$")
    # Abbreviation used in citations ("Cost.", "c.c.", "c.p.", "L. 241/1990", "d.lgs. 196/2003").
    citation_label: str = Field(min_length=3, max_length=40)
    # Redactional code of the Gazzetta Ufficiale, e.g. "047U0001".
    codice_redazionale: str = Field(pattern=r"^\d{3}[A-Z]\d{4}$")
    urn: str = Field(pattern=r"^urn:nir:stato:[a-z.]+(?::\d{4}-\d{2}-\d{2}(?:;\d+)?)?$")
    gazzetta_ufficiale_date: str = Field(pattern=_DATE)
    gazzetta_ufficiale_number: str | None = Field(default=None, pattern=r"^\d+$")
    number: str | None = Field(default=None, pattern=r"^\d+$")
    year: int | None = Field(default=None, ge=1861, le=2100)
    entry_into_force: str | None = Field(default=None, pattern=_DATE)
    # For the codes: the decree that approves them (the code is annexed to it).
    approved_by: str | None = None


class ItModule(JurisdictionModule):
    id: ClassVar[str] = "it"
    version: ClassVar[str] = "1.0.0"
    ext_model: ClassVar[type[BaseModel]] = ItExt

    def pinpoint(self, c: ProvisionCitationInput) -> str:
        if not c.num_path:
            return ""
        first = c.num_path[0]
        out = [
            f"{first.removeprefix('trans:')} disp. trans. fin."
            if first.startswith("trans:")
            else f"art. {first.removeprefix('prel:')}"
        ]
        for part in c.num_path[1:]:
            kind, _, label = part.partition(":")
            if kind == "comma":
                out.append(f"comma {label}")
            elif kind == "lett":
                out.append(f"lett. {label})")
            elif kind == "num":
                out.append(f"n. {label}")
        return ", ".join(out)

    def provision_citation(self, c: ProvisionCitationInput) -> str:
        pin = self.pinpoint(c)
        if not pin:
            return c.instrument_title
        label = str(c.ext.get("citation_label") or c.instrument_title)
        if c.num_path[0].startswith("prel:"):
            label = f"disp. prel. {label}"
        return f"{pin}{', ' if len(c.num_path) > 1 else ' '}{label}"

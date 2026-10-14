"""Netherlands module: Dutch citation conventions for national legislation (Basiswettenbestand).

* ``art. 6:162 lid 1 BW`` (Burgerlijk Wetboek: book number before the article number)
* ``art. 3:4 lid 2 Awb`` (the Awb numbers its articles ``chapter:article`` itself)
* ``art. 310 Sr``, ``art. 1 Grondwet``, ``art. 23 lid 4 Grondwet``
* list items: ``art. 1:3 lid 1 onder a Awb``; a nested item: ``art. 7 lid 2 onder a sub 1° UAVG``
* annexes: ``Bijlage 2 Awb``; an unnumbered article by its heading: ``Algemene bepaling Grondwet``

The name after the pinpoint is the abbreviation chosen in the profile (``extensions.nl.
abbreviations``), which must be one of the abbreviations the source's wetstechnische informatie
(WTI) lists for that regulation, or its official citation title (``citeertitel``); otherwise the
citation title from the toestand itself. Formatting conventions only; to be confirmed by a Dutch
reviewer before the NL profile is "reviewed" (e.g. "onder a" versus "sub a" for list items).
"""

from __future__ import annotations

import re
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ulis.jurisdictions.modules.base import JurisdictionModule, ProvisionCitationInput


class NlExt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bwb_id: str  # Basiswettenbestand identifier, e.g. BWBR0005289
    jci: str  # Juriconnect identifier of the regulation, e.g. jci1.3:c:BWBR0005289
    citeertitel: str | None = None  # official citation title, e.g. "Burgerlijk Wetboek Boek 6"
    soort: str | None = None  # BWB regulation type (wetgeving/@soort), e.g. "wet"
    boek: str | None = None  # Burgerlijk Wetboek book number, from the citation title


class Abbreviation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    citeertitel: str
    afkortingen: list[str] = Field(default_factory=list)  # as listed in the regulation's WTI
    cite_as: str

    @model_validator(mode="after")
    def _from_source(self) -> Abbreviation:
        if self.cite_as not in [*self.afkortingen, self.citeertitel]:
            raise ValueError(f"cite_as {self.cite_as!r} is neither a source abbreviation nor the citation title")
        return self


class NlProfileExt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    abbreviations: dict[str, Abbreviation] = Field(default_factory=dict)  # by BWB id


def _item(label: str) -> str:
    return label.strip().rstrip(".")


class NlModule(JurisdictionModule):
    id: ClassVar[str] = "nl"
    version: ClassVar[str] = "1.0.0"
    ext_model: ClassVar[type[BaseModel]] = NlExt
    profile_extension_model: ClassVar[type[BaseModel]] = NlProfileExt

    def pinpoint(self, c: ProvisionCitationInput) -> str:
        parts = [p for p in c.num_path if p]
        if not parts:
            return ""
        if c.top_kind in ("annex", "general_provision"):
            out = parts[0]
        else:
            book = c.ext.get("boek")
            out = f"art. {book}:{parts[0]}" if book else f"art. {parts[0]}"
        items = 0
        for part in parts[1:]:
            if re.match(r"^lid\s", part):
                out += f" {part}"
            else:
                out += f" {'onder' if items == 0 else 'sub'} {_item(part)}"
                items += 1
        return out

    def provision_citation(self, c: ProvisionCitationInput) -> str:
        name = c.ext.get("citeertitel") or c.instrument_title
        entry = c.profile_extensions.get(self.id, {}).get("abbreviations", {}).get(c.ext.get("bwb_id", ""))
        if entry and entry.get("citeertitel") == c.ext.get("citeertitel"):
            name = entry["cite_as"]
        pin = self.pinpoint(c)
        return f"{pin} {name}" if pin else name

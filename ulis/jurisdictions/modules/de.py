"""Germany module: German citation conventions for federal law (Gesetze im Internet).

* ``§ 823 Abs. 1 BGB``, ``Art. 1 Abs. 1 GG``, ``§ 242 Abs. 1 StGB``
* items: ``§ 1 Abs. 2 Nr. 1 Buchst. a KSchG``; ``Doppelbuchst. aa``, ``Dreifachbuchst. aaa``
* sentences only where the source numbers them (tax laws): ``§ 2 Abs. 1 Satz 2 GewStG``,
  ``§ 8 Nr. 1 Buchst. d Satz 2 Doppelbuchst. aa GewStG``
* repealed ranges: ``§§ 3 bis 6 BGB``; other units: ``Präambel GG``
* annexes: ``Anlage 1 zum EStG`` (``zum`` only for abbreviations of a ...Gesetz/...Gesetzbuch)

The abbreviation is the official one (``amtabk``) when the source gives it, else the juris
abbreviation (``jurabk``). The source does not mark sentence numbers outside tax laws, so other
citations stop at Absatz/Nummer/Buchstabe even where practice would add ``Satz n``.

Formatting conventions only; to be confirmed by a German reviewer before the DE profile is "reviewed".
"""

from __future__ import annotations

import re
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from ulis.jurisdictions.modules.base import JurisdictionModule, ProvisionCitationInput


class StandAngabe(BaseModel):
    model_config = ConfigDict(extra="forbid")
    typ: str
    kommentar: str


class DeExt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    jurabk: str  # juris abbreviation (also the site's path, e.g. "BDSG 2018" -> bdsg_2018)
    amtabk: str | None = None  # official abbreviation, when the law has one
    doknr: str  # juris document number of the law, e.g. BJNR001950896
    fundstelle: str | None = None  # promulgation reference of the original Act, e.g. "RGBl 1896, 195"
    standangaben: list[StandAngabe] = Field(default_factory=list)


_LABELS = (
    (re.compile(r"^\((\d+[a-z]*)\)$"), "Abs."),
    (re.compile(r"^\^(\d+)$"), "Satz"),
    (re.compile(r"^(\d+[a-z]*)\.$"), "Nr."),
    (re.compile(r"^([a-z])\)$"), "Buchst."),
    (re.compile(r"^(([a-z])\2)\)$"), "Doppelbuchst."),
    (re.compile(r"^(([a-z])\2\2)\)$"), "Dreifachbuchst."),
)


def _subdivision(label: str) -> str:
    for pattern, word in _LABELS:
        m = pattern.match(label)
        if m:
            return f"{word} {m.group(1)}"
    return label


def _head(label: str) -> str:
    """The source prints ``Art 1``; citations write ``Art. 1``."""
    return re.sub(r"^Art\.?\s+", "Art. ", label)


class DeModule(JurisdictionModule):
    id: ClassVar[str] = "de"
    version: ClassVar[str] = "1.0.0"
    ext_model: ClassVar[type[BaseModel]] = DeExt

    def pinpoint(self, c: ProvisionCitationInput) -> str:
        parts = [p for p in c.num_path if p]
        if not parts:
            return ""
        return " ".join([_head(parts[0]), *(_subdivision(p) for p in parts[1:])])

    def provision_citation(self, c: ProvisionCitationInput) -> str:
        abbreviation = c.ext.get("amtabk") or c.ext.get("jurabk") or c.instrument_title
        pin = self.pinpoint(c)
        if not pin:
            return abbreviation
        if c.top_kind == "annex" and re.search(r"G[B]?$", abbreviation):
            return f"{pin} zum {abbreviation}"
        return f"{pin} {abbreviation}"

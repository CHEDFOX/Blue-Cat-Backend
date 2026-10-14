"""Portugal module: citations of Portuguese legislation, in Portuguese.

* article: ``artigo 483.º do Código Civil``, ``artigo 1.º-A do Código Penal``
* number (n.º) and letter (alínea): ``artigo 483.º, n.º 1, do Código Civil``,
  ``artigo 5.º, n.º 1, alínea a), da Lei n.º 58/2019``

The article label is the one the gazette prints (``483``, ``1-A``); the ordinal sign is added here. The preposition
(do / da / de) is chosen from the first word of the instrument's title. Portugal has no source in ULIS yet (the
Diário da República could not be fetched, see ``config/sources/pt-diario-da-republica.yaml``), so this module only
formats and validates; the conventions are to be confirmed by a Portuguese reviewer before the PT profile is
"reviewed".
"""

from __future__ import annotations

import re
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from ulis.jurisdictions.modules.base import JurisdictionModule, ProvisionCitationInput

PREPOSITIONS = {
    "código": "do",
    "constituição": "da",
    "lei": "da",
    "portaria": "da",
    "resolução": "da",
    "decreto": "do",
    "decreto-lei": "do",
    "regulamento": "do",
    "estatuto": "do",
    "regime": "do",
    "despacho": "do",
}


class PtExt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # The diploma's number as printed, e.g. "Lei n.º 58/2019" or "Decreto-Lei n.º 47344/66".
    diploma: str | None = Field(default=None, max_length=120)
    # The Diário da República identifier of the diploma, when a source gives one.
    dre_id: str | None = Field(default=None, pattern=r"^[0-9A-Za-z_/.-]+$")


def article_label(label: str) -> str:
    """``483`` -> ``483.º``; ``1-A`` -> ``1.º-A``; a label that does not start with a number is left alone."""
    m = re.match(r"^(\d+)(.*)$", label.strip())
    return f"{m.group(1)}.º{m.group(2)}" if m else label.strip()


def with_preposition(title: str) -> str:
    first = re.split(r"[\s,]", title.strip(), maxsplit=1)[0].lower()
    return f"{PREPOSITIONS.get(first, 'de')} {title.strip()}"


class PtModule(JurisdictionModule):
    id: ClassVar[str] = "pt"
    version: ClassVar[str] = "1.0.0"
    ext_model: ClassVar[type[BaseModel]] = PtExt

    def pinpoint(self, c: ProvisionCitationInput) -> str:
        if not c.num_path:
            return ""
        out = [f"artigo {article_label(c.num_path[0])}"]
        for part in c.num_path[1:]:
            kind, _, label = part.partition(":")
            if kind == "para" and label:
                out.append(f"n.º {label}")
            elif kind == "let" and label:
                out.append(f"alínea {label})")
        return ", ".join(out)

    def provision_citation(self, c: ProvisionCitationInput) -> str:
        pin = self.pinpoint(c)
        if not pin:
            return c.instrument_title
        # "artigo 483.º do Código Civil" but "artigo 483.º, n.º 1, do Código Civil"
        joint = ", " if len(c.num_path) > 1 else " "
        return f"{pin}{joint}{with_preposition(c.instrument_title)}"

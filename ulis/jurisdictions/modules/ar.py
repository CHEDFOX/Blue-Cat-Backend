"""Argentina module: citations of national legislation in Argentine legal style.

* codes and the Constitution with their conventional abbreviations: ``art. 1109 CCCN``, ``art. 79 CP``,
  ``art. 14 bis CN``, ``art. 245 LCT``;
* other laws by number, the article first: ``art. 4 inc. a), Ley 25.326`` (items: ``inc.``; items of an
  item: ``ap.``), ``art. 14, Ley 19.549``;
* transitory clauses of the Constitution: ``disp. trans. primera CN``; the preamble: ``CN, preámbulo``.

Numbers are printed with the thousands dot used in Argentina (``Ley 25.326``); InfoLEG's own numbering
of articles (``14 bis``) is kept. The abbreviations are the conventional ones (to be confirmed by an
Argentine reviewer before the AR profile is "reviewed"). ULIS stores articles as leaf provisions, so the
``inc.`` / ``ap.`` forms are produced only when a source gives items their own provisions.
"""

from __future__ import annotations

import re
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from ulis.jurisdictions.modules.base import JurisdictionModule, ProvisionCitationInput

# InfoLEG norm id -> conventional abbreviation
ABBREVIATIONS = {
    804: "CN",  # Constitución de la Nación Argentina (texto oficial, Ley 24.430)
    235975: "CCCN",  # Código Civil y Comercial de la Nación (Ley 26.994, Anexo I)
    16546: "CP",  # Código Penal (Ley 11.179)
    25552: "LCT",  # Ley de Contrato de Trabajo (Ley 20.744)
}


class ArExt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    infoleg_id: int = Field(ge=1)
    tipo: str  # "Ley": the kind of norm InfoLEG files the text under
    numero: str = Field(pattern=r"^\d{1,6}$")  # number of that norm, without separators
    texto: Literal["actualizado", "original"]
    organismo: str | None = None
    fecha_sancion: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    fecha_boletin: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    boletin_numero: str | None = None
    boletin_pagina: str | None = None


def dotted(number: str) -> str:
    """``25326`` -> ``25.326``."""
    return f"{int(number):,}".replace(",", ".")


def _item(value: str) -> str:
    """Items: letters are closed with ``)``, numbers are printed as they are (``inc. 2``, ``inc. a)``)."""
    return f"{value})" if re.fullmatch(r"[a-zñ]", value) else value


class ArModule(JurisdictionModule):
    id: ClassVar[str] = "ar"
    version: ClassVar[str] = "1.0.0"
    ext_model: ClassVar[type[BaseModel]] = ArExt

    def pinpoint(self, c: ProvisionCitationInput) -> str:
        parts = [p for p in c.num_path if p]
        if c.top_kind == "article" and parts:
            out = f"art. {parts[0]}"
            if len(parts) > 1:
                out += f" inc. {_item(parts[1])}"
            if len(parts) > 2:
                out += " ap. " + ", ".join(_item(p) for p in parts[2:])
            return out
        if c.top_kind == "transitional_provision" and parts:
            return f"disp. trans. {parts[0]}"
        if c.top_kind == "preamble":
            return "preámbulo"
        return " ".join(parts)

    def provision_citation(self, c: ProvisionCitationInput) -> str:
        pin = self.pinpoint(c)
        abbreviation = ABBREVIATIONS.get(int(c.ext.get("infoleg_id", 0)))
        if abbreviation:
            return f"{abbreviation}, {pin}" if c.top_kind == "preamble" else f"{pin} {abbreviation}"
        tipo, numero = c.ext.get("tipo"), c.ext.get("numero")
        designation = f"{tipo} {dotted(str(numero))}" if tipo and numero else c.instrument_title
        return f"{pin}, {designation}" if pin else designation

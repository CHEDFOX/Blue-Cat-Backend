"""Spain module: citations of State legislation in Spanish legal style.

* ``art. 24.1 CE``, ``art. 1902 CC``, ``art. 138.1 CP``, ``art. 2.2.a) LOPDGDD``, ``art. 22.1.ª CP``
* dispositions: ``DA 1.ª CE``, ``DT 4.ª, apdo. 2 LOPDGDD``, ``DD CE``, ``DD única LOPDGDD``
* ``arts. 617 a 622 CP`` (range blocks), ``CE, preámbulo``
* laws without a conventional abbreviation: ``art. 32.1 de la Ley 39/2015``
* provisions of a decree approving a consolidated text (e.g. the Workers' Statute):
  ``art. único del Real Decreto Legislativo 2/2015``

The abbreviations are the conventional ones (to be confirmed by a Spanish reviewer before the ES
profile is "reviewed"); numbering is the law's own, as printed by the BOE consolidated text.
"""

from __future__ import annotations

import re
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from ulis.jurisdictions.modules.base import JurisdictionModule, ProvisionCitationInput
from ulis.sources.parsers.boe_consolidada import ordinal_number

# BOE identifier -> conventional abbreviation
ABBREVIATIONS = {
    "BOE-A-1978-31229": "CE",  # Constitución Española
    "BOE-A-1889-4763": "CC",  # Código Civil
    "BOE-A-1995-25444": "CP",  # Código Penal (LO 10/1995)
    "BOE-A-2000-323": "LEC",  # Ley 1/2000, de Enjuiciamiento Civil
    "BOE-A-1882-6036": "LECrim",  # Ley de Enjuiciamiento Criminal
    "BOE-A-2018-16673": "LOPDGDD",  # LO 3/2018, de Protección de Datos Personales y garantía de los derechos digitales
    "BOE-A-2015-10565": "LPAC",  # Ley 39/2015, del Procedimiento Administrativo Común
    "BOE-A-2015-10566": "LRJSP",  # Ley 40/2015, de Régimen Jurídico del Sector Público
    "BOE-A-2015-11430": "ET",  # RDLeg 2/2015, texto refundido de la Ley del Estatuto de los Trabajadores
}
DISPOSITIONS = {
    "additional_provision": "DA",
    "transitional_provision": "DT",
    "repealing_provision": "DD",
    "final_provision": "DF",
}
LABELS = {"preamble": "preámbulo", "enacting_terms": "parte dispositiva", "final_part": "parte final"}
# Rangos whose Spanish noun is masculine: "del Real Decreto 2/2015", "de la Orden ECO/805/2003".
MASCULINE_RANGOS = ("Real Decreto", "Decreto", "Acuerdo", "Reglamento")


class EsExt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    boe_id: str = Field(pattern=r"^BOE-[A-Z]-\d{4}-\d{1,6}$")
    rango: str
    numero_oficial: str | None = None
    eli: str | None = None
    departamento: str | None = None
    # Whole-norm repeal or annulment and "vigencia agotada", as the BOE metadata state them.
    fecha_derogacion: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    fecha_anulacion: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    vigencia_agotada: bool | None = None


_PURPOSE = re.compile(r",\s+(?:por\s+(?:la|el|lo)\s+que|sobre|relativ[ao]|en relaci[oó]n)\b")
DESIGNATION_LIMIT = 160


def short_designation(title: str) -> str:
    """The identifying head of an unnumbered norm's title (``Resolución de 31 de mayo de 2016, de la Dirección
    General de ...``), without the clause that says what it does, and never more than 160 characters."""
    head = _PURPOSE.split(title.strip().rstrip("."), maxsplit=1)[0].strip()
    if len(head) > DESIGNATION_LIMIT:
        head = head[:DESIGNATION_LIMIT].rsplit(" ", 1)[0].rstrip(",;: ") + " [...]"
    return head


def ordinal_label(word: str) -> str:
    """``primera`` -> ``1.ª``; ``única`` and unknown words are kept as printed."""
    n = ordinal_number(word) if word else None
    return f"{n}.ª" if n else word


def _subdivisions(parts: list[str]) -> str:
    """Apartados, letters and ordinal items: ``1``, ``a`` -> ``1.a)``; ``1.ª`` stays ``1.ª``."""
    return ".".join(f"{p})" if re.fullmatch(r"[a-zñ]", p) else p for p in parts if p)


class EsModule(JurisdictionModule):
    id: ClassVar[str] = "es"
    version: ClassVar[str] = "1.0.0"
    ext_model: ClassVar[type[BaseModel]] = EsExt

    def pinpoint(self, c: ProvisionCitationInput) -> str:
        kind = c.top_kind.removeprefix("decree_")
        parts = list(c.num_path)
        if kind == "article":
            return "art. " + _subdivisions(parts)
        if kind == "article_range":
            return f"arts. {parts[0]}" if parts else "arts."
        if kind in DISPOSITIONS:
            head = DISPOSITIONS[kind] + (f" {ordinal_label(parts[0])}" if parts and parts[0] else "")
            sub = _subdivisions(parts[1:])
            return f"{head}, apdo. {sub}" if sub else head
        if kind in LABELS:
            return LABELS[kind]
        return " ".join(p for p in parts if p)

    def provision_citation(self, c: ProvisionCitationInput) -> str:
        pin = self.pinpoint(c)
        rango, numero = c.ext.get("rango"), c.ext.get("numero_oficial")
        designation = f"{rango} {numero}" if rango and numero else None
        abbreviation = ABBREVIATIONS.get(str(c.ext.get("boe_id")))
        if c.top_kind.startswith("decree_") or not abbreviation:
            if designation is None:
                title = short_designation(c.instrument_title)
                return f"{title}, {pin}" if pin else title
            article = "del" if str(rango).startswith(MASCULINE_RANGOS) else "de la"
            return f"{pin} {article} {designation}" if pin else designation
        if c.top_kind in LABELS:
            return f"{abbreviation}, {pin}"
        return f"{pin} {abbreviation}" if pin else abbreviation

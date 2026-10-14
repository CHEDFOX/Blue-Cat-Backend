"""Uruguay module: citations of national legislation in Uruguayan legal style.

The article first, then the instrument, written out as IMPO and the Uruguayan courts do:

* the Constitution: ``artículo 72 de la Constitución de la República``;
* a code: ``artículo 1308 del Código Civil`` (the code's own name, as IMPO titles it);
* a law: ``artículo 9 de la Ley Nº 18.331`` (thousands dot, ``Nº``);
* a decree: ``artículo 3 del Decreto Nº 414/009`` (number, bar, the last three digits of the year).

Article labels are IMPO's: ``9-BIS`` is read ``artículo 9 bis``; ``131-144`` (a range IMPO keeps as one entry) is
``artículos 131 a 144``; ``242-1`` (an article numbered inside the code) stays ``artículo 242-1``. A label with two
numbers is a range when the second is larger than the first - the only way to tell the two apart, since IMPO
numbers articles inside a code with a small second number. Abbreviations and the ``art.`` form are not used: they
are conventions of other courts, and the full word is what the source prints.
"""

from __future__ import annotations

import re
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from ulis.jurisdictions.modules.base import JurisdictionModule, ProvisionCitationInput

_ISO_DATE = r"^\d{4}-\d{2}-\d{2}$"


class UyExt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    impo_path: str = Field(pattern=r"^[a-z][a-z0-9-]*/[1-9]\d{0,6}-\d{4}$")  # IMPO's address: leyes/18331-2008
    kind: str = Field(pattern=r"^[a-z][a-z0-9-]*$")  # constitucion | leyes | decretos | codigo-...
    tipo: str  # tipoNorma as IMPO prints it ("Ley", "Código Civil", "CONSTITUCION DE LA REPUBLICA")
    numero: str | None = Field(default=None, pattern=r"^\d{1,7}$")
    anio: int | None = Field(default=None, ge=1800, le=2200)
    nombre: str | None = None  # nombreNorma
    fecha_promulgacion: str | None = Field(default=None, pattern=_ISO_DATE)
    fecha_publicacion: str | None = Field(default=None, pattern=_ISO_DATE)


def dotted(number: str) -> str:
    """``18331`` -> ``18.331``."""
    return f"{int(number):,}".replace(",", ".")


def article_phrase(label: str) -> tuple[str, str]:
    """(``artículo``/``artículos``, the number as printed): ``9-BIS`` -> ``9 bis``, ``131-144`` -> ``131 a 144``."""
    m = re.fullmatch(r"(\d+)-(\d+)", label)
    if m and int(m.group(2)) > int(m.group(1)):
        return "artículos", f"{m.group(1)} a {m.group(2)}"
    m = re.fullmatch(r"(\d+)-([A-Za-zÑñ]+)", label)
    if m:
        return "artículo", f"{m.group(1)} {m.group(2).lower()}"
    return "artículo", label


class UyModule(JurisdictionModule):
    id: ClassVar[str] = "uy"
    version: ClassVar[str] = "1.0.0"
    ext_model: ClassVar[type[BaseModel]] = UyExt

    def pinpoint(self, c: ProvisionCitationInput) -> str:
        parts = [p for p in c.num_path if p]
        if c.top_kind == "article" and parts:
            word, number = article_phrase(parts[0])
            return f"{word} {number}"
        return " ".join(parts)

    def provision_citation(self, c: ProvisionCitationInput) -> str:
        pin = self.pinpoint(c)
        kind = str(c.ext.get("kind") or "")
        tipo, numero, anio = c.ext.get("tipo"), c.ext.get("numero"), c.ext.get("anio")
        if kind == "constitucion":
            of = "de la Constitución de la República"
        elif kind.startswith("codigo-") and tipo:
            of = f"del {tipo}"
        elif kind == "leyes" and numero:
            of = f"de la Ley Nº {dotted(str(numero))}"
        elif kind == "decretos" and numero and anio:
            of = f"del Decreto Nº {numero}/{int(anio) % 1000:03d}"
        else:
            of = f"de {c.instrument_title}"
        return f"{pin} {of}" if pin else of.removeprefix("de la ").removeprefix("del ").removeprefix("de ")

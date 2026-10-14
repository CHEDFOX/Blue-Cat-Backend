"""Estonia module: Estonian citation conventions for acts published in Riigi Teataja.

The act's official abbreviation (``lyhend`` in Riigi Teataja's metadata) comes first, then the
pinpoint with ``§`` (paragrahv), ``lg`` (lõige), ``p`` (punkt) and ``alap`` (alapunkt), as in the
Riigi Teataja style used by Estonian courts and legislation:

* ``PS § 10``, ``KarS § 113 lg 1``, ``KarS § 121 lg 2 p 1``
* an inserted provision keeps its superscript index: ``VÕS § 5¹ lg 2``, ``KarS § 113¹``
* a nested item: ``HMS § 40 lg 1 p 2 alap a``

An act without an abbreviation is cited by its title. The genitive form used in running text
("karistusseadustiku § 113") and the full reference to the consolidated text ("RT I, 04.12.2019, 2")
are not generated; each consolidated text is identified by its expression (``est@<globaalID>``).
Formatting conventions only; to be confirmed by an Estonian reviewer before the EE profile is
"reviewed" (e.g. whether ``alap`` or ``alapunkt`` is preferred, and the position of the act name).
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from ulis.jurisdictions.modules.base import JurisdictionModule, ProvisionCitationInput

# num_path kind prefix -> abbreviation in the pinpoint
PINPOINT = {"sec": "§", "sub": "lg", "pt": "p", "subpt": "alap", "subsubpt": "alap"}
# a provision named by its label alone (the preamble of the Constitution: "PS preambul")
BARE = ("preamble",)


class EeExt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    group_id: str  # Riigi Teataja terviktekstiGrupiID: the same for every consolidated text of the act
    global_id: str  # globaalID of the consolidated text (redaktsioon) this expression was parsed from
    document_type: str  # dokumentLiik, e.g. "seadus"
    abbreviation: str | None = None  # lyhend, e.g. "KarS"
    issuer: str | None = None  # valjaandja, e.g. "Riigikogu"
    original_publication: str | None = None  # where the original act was published, e.g. "RT I 1999, 30, 415"
    publication: str | None = None  # where this consolidated text was published, e.g. "RT I, 28.05.2024, 5"


class EeModule(JurisdictionModule):
    id: ClassVar[str] = "ee"
    version: ClassVar[str] = "1.0.0"
    ext_model: ClassVar[type[BaseModel]] = EeExt

    def pinpoint(self, c: ProvisionCitationInput) -> str:
        out: list[str] = []
        for part in c.num_path:
            kind, _, label = part.partition(":")
            if kind in PINPOINT and label:
                out.append(f"{PINPOINT[kind]} {label}")
            elif kind in BARE and label:
                out.append(label)
        return " ".join(out)

    def provision_citation(self, c: ProvisionCitationInput) -> str:
        name = c.ext.get("abbreviation") or c.instrument_title
        pin = self.pinpoint(c)
        return f"{name} {pin}" if pin else name

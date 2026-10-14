"""Modules for the Council of Europe (``coe``) and the international legal order (``int``): treaty citations.

The treaties are read from the Swiss Classified Compilation (SR 0.x, German / French / Italian), so a
provision is cited as that publication does, with the language version's short form (``CEDH`` / ``EMRK`` /
``CEDU`` for the European Convention on Human Rights) when it has one, else its title:

* ``art. 8 al. 1 CEDH`` / ``Art. 8 Abs. 1 EMRK`` / ``art. 8 cpv. 1 CEDU``
* ``art. 2 al. 4 Charte des Nations Unies du 26 juin 1945`` (no short form is published for that title).

The Swiss terms ``al.`` / ``Abs.`` / ``cpv.`` and ``let.`` / ``lit.`` / ``lett.`` apply (see
:mod:`ulis.jurisdictions.modules.ch`, whose pinpoint code is reused).

Formatting conventions only; the authentic citation form of each treaty (for example "Article 8 § 1 of the
Convention") is not modelled. To be confirmed by a reviewer before the profiles are "reviewed".
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from ulis.jurisdictions.modules.ch import ChModule


class TreatyExt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Number in the Swiss Classified Compilation, e.g. "0.101".
    sr_number: str = Field(pattern=r"^\d+(?:\.\d+)+$")
    eli: str = Field(pattern=r"^https://fedlex\.data\.admin\.ch/eli/cc/[0-9A-Za-z]+/[0-9a-z_]+$")
    # True for the original-language file ("Texte original"), false for a Swiss translation.
    original_text: bool
    # Date the treaty entered into force for Switzerland (Fedlex jolux:dateEntryInForce), not for the treaty.
    ch_entry_into_force: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    # States listed in the file's scope table, and how many of them have a profile (stored as parties).
    parties_in_table: int = Field(ge=0)
    parties_recorded: int = Field(ge=0)


class CoeModule(ChModule):
    id: ClassVar[str] = "coe"
    version: ClassVar[str] = "1.0.0"
    ext_model: ClassVar[type[BaseModel]] = TreatyExt


class IntModule(ChModule):
    id: ClassVar[str] = "int"
    version: ClassVar[str] = "1.0.0"
    ext_model: ClassVar[type[BaseModel]] = TreatyExt

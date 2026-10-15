"""Module for jurisdictions whose only ingested law is the text of their constitution (``constitution_pages_b`` source).

The citation is the same in every language, as the document numbers its provisions:

* an article: ``Costa Rica Constitution art. 12`` (the article label is the document's own: ``12``, ``12 bis``, ``1o``);
* an article of a transitional or final section: ``Costa Rica Constitution art. T1`` (``T`` is ULIS's marker for a
  separate numbering series; the document's own heading for it is kept in the provision's context heading);
* the preamble: ``Costa Rica Constitution preamble``;
* a block of paragraphs where the document's structure could not be recognised: ``Costa Rica Constitution text block 7``
  (the blocks are ULIS's own division of the text, in reading order; they are not provisions of the constitution).

The country name in the citation is English and comes from the document's manifest entry (``ext.country_name``); a
profile for a country with its own module (Portugal, Brazil ...) does not use this module.
"""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from ulis.jurisdictions.modules.base import JurisdictionModule, ProvisionCitationInput


class ConstitutionBExt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    country_name: str = Field(min_length=2, max_length=80)
    document_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,80}$")
    source_url: str | None = Field(default=None, max_length=500)
    # What the publisher says about the edition (date of the last reform, "texto vigente" ...), in its own words.
    edition_note: str | None = Field(default=None, max_length=500)
    numbering: Literal["articles", "blocks", "mixed"]
    citation_label: str | None = Field(default=None, max_length=120)


class ConstitutionBModule(JurisdictionModule):
    id: ClassVar[str] = "constitution_b"
    version: ClassVar[str] = "1.0.0"
    ext_model: ClassVar[type[BaseModel]] = ConstitutionBExt

    def pinpoint(self, c: ProvisionCitationInput) -> str:
        label = c.num_path[0] if c.num_path else ""
        if c.top_kind == "article" and label:
            return f"art. {label}"
        if c.top_kind == "preamble":
            return "preamble"
        if c.top_kind == "block" and label:
            return f"text block {label}"
        return " ".join(p for p in c.num_path if p)

    def provision_citation(self, c: ProvisionCitationInput) -> str:
        label = c.ext.get("citation_label") or f"{c.ext.get('country_name') or c.jurisdiction_id} Constitution"
        pin = self.pinpoint(c)
        return f"{label} {pin}" if pin else str(label)

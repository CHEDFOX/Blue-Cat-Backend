"""Module for the OECD Compendium of Legal Instruments (``oecd_legal_instruments`` source).

An instrument is identified by its Compendium key (``OECD/LEGAL/0449``), which the OECD prints on every instrument, so
that is the
citation's first part; the pinpoint is the unit the text itself numbers:

* a convention's article: ``OECD/LEGAL/0293 art. 5``;
* a Council act's Roman operative paragraph, or a numbered paragraph under it: ``OECD/LEGAL/0188 para. II``,
  ``OECD/LEGAL/0188 para. II.4`` (``II`` the Roman paragraph, ``4`` the numbered paragraph below it);
* a lettered sub-principle under a Roman paragraph: ``OECD/LEGAL/0413 appendix para. I.A``;
* a decimal-numbered principle or recommendation: ``OECD/LEGAL/0449 para. 1.2``;
* a section heading: ``OECD/LEGAL/0449 section 1``;
* the units of an annex or appendix carry its name: ``OECD/LEGAL/0413 appendix para. II.B`` (the numbering restarts
  there);
* the recitals before the first operative unit: ``OECD/LEGAL/0449 preamble``;
* text the parser could not divide (and the closing notes the OECD prints after a rule): ``OECD/LEGAL/0383 text block
  1``
  (ULIS's own division, in reading order).
"""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from ulis.jurisdictions.modules.base import JurisdictionModule, ProvisionCitationInput


class OecdExt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(pattern=r"^OECD/LEGAL/\d{4}$")
    oecd_id: int = Field(gt=0)
    record_url: str | None = Field(default=None, max_length=300)
    numbering: Literal["articles", "paragraphs", "blocks"]
    # The Compendium's abrogation date, when the instrument is no longer in force.
    abrogated_on: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")


class OecdModule(JurisdictionModule):
    id: ClassVar[str] = "oecd"
    version: ClassVar[str] = "1.0.0"
    ext_model: ClassVar[type[BaseModel]] = OecdExt

    def pinpoint(self, c: ProvisionCitationInput) -> str:
        if c.top_kind == "preamble":
            return "preamble"
        if not c.num_path:
            return ""
        if c.top_kind == "annex":
            return c.num_path[0].lower()
        if c.top_kind in ("paragraph", "section") and len(c.num_path) > 1:
            # a unit of an annex or appendix: "annex 1 para. I.A", "appendix section 2"
            unit = "para." if c.top_kind == "paragraph" else "section"
            return f"{c.num_path[0].lower()} {unit} {'.'.join(c.num_path[1:])}"
        if c.top_kind == "article":
            return f"art. {c.num_path[0]}"
        if c.top_kind == "paragraph":
            return f"para. {c.num_path[0]}"
        if c.top_kind == "section":
            return f"section {c.num_path[0]}"
        if c.top_kind == "block":
            return f"text block {c.num_path[0]}"
        return " ".join(p for p in c.num_path if p)

    def provision_citation(self, c: ProvisionCitationInput) -> str:
        key = str(c.ext.get("key") or c.instrument_title)
        pin = self.pinpoint(c)
        return f"{key} {pin}" if pin else key

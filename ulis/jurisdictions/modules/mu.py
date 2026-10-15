"""Mauritius module: neutral-citation style for the decisions of the courts of Mauritius.

Mauritian practice cites a decision by its parties and a bracketed neutral reference of year, court series
and number, as in the decisions themselves: ``Ramgoolam v Hurdoyal & Ors [2021 SCJ 394]``, and a paragraph as
``para 12``. The court publishes the decisions ULIS ingests with a *document number* of the same form
(``2026 INT 287``); that number is used as the neutral reference. The three-letter series names a court
division or sitting (``INT`` Intermediate Court, ``PL3`` a Port Louis district court division, ...), so the
court is named by the profile's court entry, not by the series. Formatting conventions only; to be
confirmed by a Mauritian reviewer before the MU profile is "reviewed" (the bracket style and "para" for
lower-court decisions in particular).
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from ulis.jurisdictions.modules.base import JudgmentCitationInput, JurisdictionModule

MONTHS = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]


class MuJudgmentExt(BaseModel):
    """Facts from the Supreme Court website's Judgment Search that have no column of their own."""

    model_config = ConfigDict(extra="forbid")
    node_id: str  # the site's content id (Drupal node), stable per decision
    document_number: str | None = None  # as printed, e.g. "2026 INT 287"
    delivered_by: str | None = None  # the listing's "delivered by" text: judicial officer and office
    court_basis: str  # how the court was told: listing_delivered_by or header_text
    pdf_url: str
    pdf_sha256: str
    summary: str | None = None  # the listing's summary column, when filled
    language_basis: str  # "word_count_heuristic": the language is not stated by the source


class MuModule(JurisdictionModule):
    id: ClassVar[str] = "mu"
    version: ClassVar[str] = "1.0.0"
    ext_model: ClassVar[type[BaseModel]] = MuJudgmentExt

    def judgment_citation(self, c: JudgmentCitationInput) -> str:
        if c.neutral_citation:
            text = f"{c.case_name} [{c.neutral_citation}]"
        else:
            d = c.decision_date
            text = f"{c.case_name} ({c.court_name}, {d.day} {MONTHS[d.month - 1]} {d.year})"
        return f"{text}, para {c.paragraph}" if c.paragraph else text

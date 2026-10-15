"""Constitution publishers that were read by the constitutions sweep and could not be ingested.

Each is recorded with the publisher's own words (terms or the refusal); the licence gate refuses all of them and
nothing is registered for them in the pipeline.
"""

from __future__ import annotations

from datetime import date

import pytest

from ulis.catalog import Catalog
from ulis.ingestion.pipeline import CONNECTORS, PARSERS
from ulis.sources.registry import LicenceNotApproved, licence_gate

REJECTED = [
    "constitute-project",
    "wipo-lex",
    "jo-representatives-constitution",
    "bh-lloc-constitution",
    "ao-parlamento-constitution",
    "om-decree-om",
]
PLANNED = ["faolex", "ilo-natlex", "unhcr-refworld", "iq-parliament-constitution"]


@pytest.mark.parametrize("source_id", REJECTED + PLANNED)
def test_publisher_is_recorded_and_the_licence_gate_refuses_it(catalog: Catalog, source_id: str) -> None:
    source = catalog.sources.get(source_id)
    assert source.status == ("rejected" if source_id in REJECTED else "planned")
    assert source.licence.review_status in ("pending_review", "rejected")
    assert source.licence.reviewed_on == date(2026, 10, 15)
    assert "robots.txt" in (source.licence.reviewed_by or "")
    assert source.document_kinds == ["constitution"]
    assert source.connector is None and source.parser is None
    assert source.licence.attribution_text is None
    with pytest.raises(LicenceNotApproved):
        licence_gate(source)
    assert source_id not in CONNECTORS and not any(source_id in name for name in PARSERS)


def test_rejected_publishers_quote_the_words_that_forbid_the_use(catalog: Catalog) -> None:
    words = {
        "constitute-project": "commercial use is expressly prohibited",
        "wipo-lex": "perform web scraping",
        "jo-representatives-constitution": "إعادة نشر أجزاء بسيطة من المعلومات",
        "bh-lloc-constitution": "is a violation of the Legislation and Legal Opinion Commission",
        "ao-parlamento-constitution": "não pode ser reproduzida ou transmitida",
        "om-decree-om": "the use of bots and other automation tools to extract Decree Content is not permitted",
    }
    for source_id, phrase in words.items():
        source = catalog.sources.get(source_id)
        assert phrase in (source.licence.reviewed_by or ""), source_id
        assert source.licence.automated_processing == "not_allowed" and source.licence.bulk_download == "not_allowed"

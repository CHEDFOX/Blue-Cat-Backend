"""Africa, Asia, Middle East and Oceania: the sources that were evaluated and could not be ingested.

Each one is recorded with the publisher's own findings; the licence gate refuses all of them and nothing is
registered for them in the pipeline.
"""

from __future__ import annotations

from datetime import date

import pytest

from ulis.catalog import Catalog
from ulis.ingestion.pipeline import CONNECTORS, PARSERS
from ulis.sources.registry import LicenceNotApproved, licence_gate

PLANNED = [
    "za-gov-legislation",
    "za-saflii",
    "za-concourt",
    "ke-kenya-law",
    "ma-sgg-legislation",
    "sg-statutes-online",
    "sg-elitigation-judgments",
    "kr-law-go-kr",
    "my-lom-agc",
    "bd-bdlaws",
    "pk-pakistan-code",
    "id-jdih-setneg",
    "vn-chinhphu-vanban",
    "th-krisdika",
    "mn-legalinfo",
    "np-law-commission",
    "il-knesset-legislation",
    "tr-mevzuat",
    "ae-uae-legislation",
    "sa-boe-laws",
    "qa-almeezan",
    "jo-lob",
    "lb-legal-informatics-centre",
    "fj-laws",
    "pacific-paclii",
]
REJECTED = ["laws-africa-lii-network", "id-mahkamah-agung-putusan"]


@pytest.mark.parametrize("source_id", PLANNED + REJECTED)
def test_source_is_recorded_and_the_licence_gate_refuses_it(catalog: Catalog, source_id: str) -> None:
    source = catalog.sources.get(source_id)
    assert source.status == ("rejected" if source_id in REJECTED else "planned")
    assert source.licence.review_status in ("pending_review", "rejected")
    assert source.licence.reviewed_on == date(2026, 10, 14)
    assert "robots.txt" in (source.licence.reviewed_by or "")
    assert source.connector is None and source.parser is None
    assert source.licence.attribution_text is None
    with pytest.raises(LicenceNotApproved):
        licence_gate(source)
    assert source_id not in CONNECTORS and not any(source_id in name for name in PARSERS)


@pytest.mark.parametrize("source_id", REJECTED)
def test_rejected_sources_record_that_automated_use_is_not_allowed(catalog: Catalog, source_id: str) -> None:
    licence = catalog.sources.get(source_id).licence
    assert licence.review_status == "rejected"
    assert licence.automated_processing == "not_allowed" and licence.bulk_download == "not_allowed"


def test_laws_africa_network_covers_the_platform_countries(catalog: Catalog) -> None:
    source = catalog.sources.get("laws-africa-lii-network")
    assert {"ZA", "UG", "TZ", "ZM", "NG", "GH", "NA", "RW", "MU"} <= set(source.jurisdictions)
    text = source.licence.reviewed_by or ""
    assert "strictly prohibited" in text and "NonCommercial" in text and "ai-input=no" in text


def test_only_mauritius_has_a_profile_among_the_countries_evaluated(catalog: Catalog) -> None:
    covered = {j for sid in PLANNED + REJECTED for j in catalog.sources.get(sid).jurisdictions}
    profiles = catalog.jurisdictions.profiles
    # the minimal profiles of the constitutions sweep (only the constitution is loaded) are not full profiles
    full = {j for j in covered & set(profiles) if profiles[j].instrument_types != ["constitution"]}
    assert full == {"MU"}


def test_mauritius_is_planned_blocked_by_robots_and_nothing_is_registered(catalog: Catalog) -> None:
    source = catalog.sources.get("mu-supreme-court-judgments")
    assert source.status == "planned" and source.connector is None and source.parser is None
    assert "robots.txt" in (source.licence.reviewed_by or "")
    with pytest.raises(LicenceNotApproved):
        licence_gate(source)
    assert "mu-supreme-court-judgments" not in CONNECTORS
    assert not any("mu_supreme" in name or "mu_judg" in name for name in list(PARSERS) + list(CONNECTORS))

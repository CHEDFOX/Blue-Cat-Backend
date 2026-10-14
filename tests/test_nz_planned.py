"""New Zealand: the source is planned (API key and bot challenge block access) and nothing is ingested."""

from __future__ import annotations

import pytest

from ulis.catalog import Catalog
from ulis.ingestion.pipeline import CONNECTORS, PARSERS
from ulis.sources.registry import LicenceNotApproved, licence_gate


def test_source_is_planned_and_the_licence_gate_refuses_it(catalog: Catalog) -> None:
    source = catalog.sources.get("nz-pco-legislation")
    assert source.status == "planned"
    assert source.licence.review_status == "pending_review"
    # Nothing about reuse could be verified, so no permission is claimed.
    assert {
        source.licence.redistribution,
        source.licence.bulk_download,
        source.licence.automated_processing,
        source.licence.commercial_use,
    } == {"unknown"}
    assert source.licence.attribution_text is None
    with pytest.raises(LicenceNotApproved):
        licence_gate(source)


def test_no_connector_or_parser_is_registered_for_the_blocked_source(catalog: Catalog) -> None:
    source = catalog.sources.get("nz-pco-legislation")
    assert source.connector is None
    assert source.parser is None
    assert "nz-pco-legislation" not in CONNECTORS
    assert not any("nz" in name.lower() for name in PARSERS)
    assert "api key" in (source.access.authentication or "").lower()


def test_profile_is_a_documented_stub(catalog: Catalog) -> None:
    profile = catalog.jurisdictions.get("NZ")
    assert profile.verification.status == "draft_unverified"
    assert profile.coverage.corpus_status == "none"
    assert profile.legal_system.families == ["common_law"]
    assert profile.legal_system.treaty_reception == "dualist"
    assert [c.id for c in profile.courts] == ["NZ-SC", "NZ-CA", "NZ-HC"]
    assert profile.publication.official_sources == ["nz-pco-legislation"]
    assert not profile.memberships  # no EU (or other) membership entries
    gaps = " ".join(profile.coverage.known_gaps)
    assert "No New Zealand legislation" in gaps and "Te reo Maori" in gaps

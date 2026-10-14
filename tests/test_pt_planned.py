"""Portugal: the source is planned (JavaScript-only portal, terms unread, no open dataset) and nothing is ingested."""

from __future__ import annotations

import pytest

from ulis.catalog import Catalog
from ulis.ingestion.pipeline import CONNECTORS, PARSERS
from ulis.jurisdictions.modules.base import ProvisionCitationInput
from ulis.sources.registry import LicenceNotApproved, licence_gate


def test_source_is_planned_and_the_licence_gate_refuses_it(catalog: Catalog) -> None:
    source = catalog.sources.get("pt-diario-da-republica")
    assert source.status == "planned"
    assert source.licence.review_status == "pending_review"
    assert {
        source.licence.redistribution,
        source.licence.bulk_download,
        source.licence.automated_processing,
        source.licence.commercial_use,
    } == {"unknown"}
    assert source.licence.attribution_text is None
    with pytest.raises(LicenceNotApproved):
        licence_gate(source)


def test_no_connector_or_parser_is_registered_for_the_planned_source(catalog: Catalog) -> None:
    source = catalog.sources.get("pt-diario-da-republica")
    assert source.connector is None and source.parser is None
    assert "pt-diario-da-republica" not in CONNECTORS
    assert not any("dre" in name.lower() or name.startswith("pt") for name in PARSERS)
    assert "JavaScript" in (source.access.authentication or "")


def test_profile_is_a_documented_stub(catalog: Catalog) -> None:
    profile = catalog.jurisdictions.get("PT")
    assert profile.verification.status == "draft_unverified"
    assert profile.coverage.corpus_status == "none"
    assert profile.official_languages == ["pt"]
    assert profile.publication.official_sources == ["pt-diario-da-republica"]
    assert not profile.memberships
    assert "No Portuguese legislation is ingested" in " ".join(profile.coverage.known_gaps)


def _cite(catalog: Catalog, title: str, path: list[str]) -> str:
    module = catalog.module_for("PT")
    return module.provision_citation(ProvisionCitationInput(title, "law", "PT", "article", path, "pt"))


def test_citations_follow_portuguese_practice(catalog: Catalog) -> None:
    assert _cite(catalog, "Código Civil", ["483"]) == "artigo 483.º do Código Civil"
    assert _cite(catalog, "Código Civil", ["483", "para:1"]) == "artigo 483.º, n.º 1, do Código Civil"
    assert _cite(catalog, "Código Penal", ["1-A"]) == "artigo 1.º-A do Código Penal"
    assert (
        _cite(catalog, "Lei n.º 58/2019", ["5", "para:1", "let:a"])
        == "artigo 5.º, n.º 1, alínea a), da Lei n.º 58/2019"
    )
    assert _cite(catalog, "Constituição da República Portuguesa", ["13"]) == (
        "artigo 13.º da Constituição da República Portuguesa"
    )

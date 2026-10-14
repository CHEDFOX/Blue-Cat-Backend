"""Latin America: the sources that could not be used are recorded as planned, and the licence gate refuses them."""

from __future__ import annotations

import pytest

from ulis.catalog import Catalog
from ulis.ingestion.pipeline import CONNECTORS, PARSERS
from ulis.sources.registry import LicenceNotApproved, licence_gate

PLANNED = {
    "cl-bcn-leychile": "CL",
    "br-planalto-legislacao": "BR",
    "co-secretaria-senado": "CO",
    "pe-spij": "PE",
    "cr-sinalevi": "CR",
    "mx-diputados-leyes-federales": "MX",
}


@pytest.mark.parametrize("source_id", sorted(PLANNED))
def test_source_is_planned_and_the_licence_gate_refuses_it(catalog: Catalog, source_id: str) -> None:
    source = catalog.sources.get(source_id)
    assert source.status == "planned" and source.jurisdictions == [PLANNED[source_id]]
    assert source.licence.review_status == "pending_review"
    assert source.connector is None and source.parser is None
    assert source_id not in CONNECTORS and source_id not in PARSERS
    with pytest.raises(LicenceNotApproved) as exc:
        licence_gate(source)
    assert "status is 'planned'" in str(exc.value) and "pending_review" in str(exc.value)
    # nothing about reuse was verified, so no permission is claimed beyond what the terms state
    assert source.licence.attribution_text is None and source.licence.redistribution == "unknown"
    assert source.licence.commercial_use == "unknown"


def test_chile_records_what_the_terms_of_the_api_say(catalog: Catalog) -> None:
    source = catalog.sources.get("cl-bcn-leychile")
    assert source.licence.id == "CC-BY-NC-ND-4.0" and source.licence.bulk_download == "not_allowed"
    assert "API key" in (source.access.authentication or "")
    assert "500 requests" in (source.rate_limits or "")
    conditions = " ".join(source.licence.conditions)
    assert "in writing" in conditions and "authorised" in conditions


def test_hosts_that_did_not_answer_are_documented_not_worked_around(catalog: Catalog) -> None:
    for source_id in ("br-planalto-legislacao", "co-secretaria-senado", "pe-spij"):
        source = catalog.sources.get(source_id)
        assert source.licence.id == "UNVERIFIED"
        assert any("do not try to pass" in c or "Do not fetch" in c for c in source.licence.conditions)
    mexico = catalog.sources.get("mx-diputados-leyes-federales")
    assert "PDF" in mexico.known_limitations[0]
    costa_rica = catalog.sources.get("cr-sinalevi")
    assert "Todos los derechos reservados" in (costa_rica.licence.name + (costa_rica.licence.reviewed_by or ""))

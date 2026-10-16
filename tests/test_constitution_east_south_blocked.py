"""East and Southern Africa constitutions that could not be loaded: each publisher is recorded with the findings and the
licence gate refuses it; nothing is registered for it in the pipeline."""

from __future__ import annotations

from datetime import date

import pytest

from ulis.catalog import Catalog
from ulis.ingestion.pipeline import CONNECTORS, PARSERS
from ulis.sources.registry import LicenceNotApproved, licence_gate

REJECTED = ["bw-parliament-constitution"]
PLANNED = [
    "et-fsc-constitution",
    "ug-parliament-constitution",
    "zm-parliament-constitution",
    "zw-parlzim-constitution",
    "mz-cconstitucional-constitution",
    "bi-presidence-constitution",
    "km-coursupreme-constitution",
    "ss-mojca-constitution",
    "sz-gov-constitution",
    "ls-gov-constitution",
    "mw-justice-constitution",
]
# the words of the publisher or of the failed request that decided the outcome
FINDINGS = {
    "bw-parliament-constitution": "Copyright Government of Botswana",
    "et-fsc-constitution": "Any rights not expressly granted herein are reserved",
    "ug-parliament-constitution": "unable to get local issuer certificate",
    "zm-parliament-constitution": "unable to get local issuer certificate",
    "zw-parlzim-constitution": "HTTP 403",
    "mz-cconstitucional-constitution": "has no text layer",
    "bi-presidence-constitution": "a scan with no text layer",
    "km-coursupreme-constitution": "a scan with no text layer",
    "ss-mojca-constitution": "certificate has expired",
    "sz-gov-constitution": "Connection reset by peer",
    "ls-gov-constitution": "Government of Lesotho©All Rights Reserved-2000",
    "mw-justice-constitution": "as amended up to Act No. 38 of 1998",
}


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


def test_the_findings_quote_what_decided_the_outcome(catalog: Catalog) -> None:
    assert set(FINDINGS) == set(REJECTED + PLANNED)
    for source_id, phrase in FINDINGS.items():
        assert phrase in (catalog.sources.get(source_id).licence.reviewed_by or ""), source_id
    rejected = catalog.sources.get("bw-parliament-constitution").licence
    assert rejected.automated_processing == "not_allowed" and rejected.bulk_download == "not_allowed"

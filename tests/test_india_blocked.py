"""India: the sources that refused the bot, did not answer, or rest on solved CAPTCHAs stay out (licence gate).

Findings were recorded on 2026-10-16 in the source descriptors; nothing from these sources is stored.
"""

from __future__ import annotations

import pytest

from ulis.catalog import Catalog
from ulis.sources.registry import LicenceNotApproved, licence_gate

BLOCKED = {
    "in-india-code": ("planned", "HTTP 403"),
    "in-legislative-constitution": ("planned", "HTTP 403"),
    "in-egazette": ("planned", "Connection reset by peer"),
    "in-lawcommission": ("planned", "Connection reset by peer"),
    "in-data-gov-in": ("planned", "Connection reset by peer"),
    "in-dattam-court-judgments": ("rejected", "CAPTCHA"),
}


@pytest.mark.parametrize("source_id", sorted(BLOCKED))
def test_blocked_indian_sources_are_refused_by_the_licence_gate(catalog: Catalog, source_id: str) -> None:
    source = catalog.sources.get(source_id)
    status, finding = BLOCKED[source_id]
    assert source.status == status and source.jurisdictions == ["IN"]
    assert source.connector is None and source.parser is None
    text = " ".join(
        [source.licence.reviewed_by or "", source.rate_limits or "", source.licence.name, *source.licence.conditions]
    )
    assert finding.lower() in text.lower()
    with pytest.raises(LicenceNotApproved):
        licence_gate(source)
    with pytest.raises(LicenceNotApproved):
        licence_gate(source, commercial=False)


def test_the_captcha_dataset_is_rejected_with_the_reason_recorded(catalog: Catalog) -> None:
    source = catalog.sources.get("in-dattam-court-judgments")
    assert source.licence.review_status == "rejected" and source.licence.id == "CC-BY-4.0"
    assert "CAPTCHA" in (source.licence.conditions[0] + (source.licence.notes or ""))

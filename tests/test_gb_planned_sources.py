"""UK case law sources that were looked at and not used: the licence gate refuses them, with the findings on record."""

from __future__ import annotations

import re

import pytest

from ulis.catalog import Catalog
from ulis.settings import REPO_ROOT
from ulis.sources.registry import LicenceNotApproved, licence_gate

SOURCES = REPO_ROOT / "config" / "sources"


def _header(name: str) -> str:
    """The comment header of a source file as running text."""
    return re.sub(r"\s*\n#\s*", " ", (SOURCES / name).read_text(encoding="utf-8"))


def test_find_case_law_needs_a_licence_for_computational_analysis_and_is_not_ingested(catalog: Catalog) -> None:
    source = catalog.sources.get("gb-find-case-law")
    assert source.status == "planned" and source.connector is None and source.parser is None
    assert source.licence.review_status == "rejected" and source.licence.automated_processing == "not_allowed"
    assert source.licence.bulk_download == "not_allowed"
    with pytest.raises(LicenceNotApproved, match="automated_processing"):
        licence_gate(source)
    text = _header("gb-find-case-law.yaml")
    # The findings quote the publisher's pages, with their URLs.
    for needle in (
        "The Open Justice Licence does not permit computational analysis",
        "you will need to apply for a licence",
        "building vector databases for semantic search",
        "https://caselaw.nationalarchives.gov.uk/re-use-find-case-law-records",
        "https://caselaw.nationalarchives.gov.uk/when-you-need-permission",
        "https://caselaw.nationalarchives.gov.uk/terms-of-use",
    ):
        assert needle in text
    assert not (REPO_ROOT / "config" / "manifests" / "gb-find-case-law.yaml").exists()


def test_house_of_lords_judgments_are_behind_a_challenge_and_are_not_ingested(catalog: Catalog) -> None:
    source = catalog.sources.get("gb-hl-judgments")
    assert source.status == "planned" and source.connector is None and source.parser is None
    assert source.licence.review_status == "rejected" and source.licence.automated_processing == "not_allowed"
    with pytest.raises(LicenceNotApproved, match="status is 'planned'"):
        licence_gate(source)
    text = _header("gb-hl-judgments.yaml")
    assert "cf-mitigated: challenge" in text and "https://publications.parliament.uk/robots.txt" in text
    assert "ULIS does not pass challenges" in text

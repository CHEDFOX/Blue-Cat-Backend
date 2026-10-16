"""The CourtListener bulk source record, the evidence behind it, and the records of the other US case-law collections.

Evidence files (tests/fixtures/us_case_law_sources/retest-2026-10-16/) are the robots.txt files read before anything
else was requested from each host, on 2026-10-16. The bucket host publishes no robots.txt (HTTP 404), which the source
YAML records.
"""

from __future__ import annotations

import urllib.robotparser
from pathlib import Path

import pytest
import yaml

from ulis.catalog import Catalog
from ulis.ingestion.pipeline import CONNECTORS, PARSER_VERSIONS, PARSERS
from ulis.settings import REPO_ROOT
from ulis.sources.connectors import courtlistener_bulk as cl
from ulis.sources.connectors.moj_laws import robots_allows
from ulis.sources.connectors.polite_http import FetchError
from ulis.sources.registry import LicenceNotApproved, licence_gate

RETEST = REPO_ROOT / "tests" / "fixtures" / "us_case_law_sources" / "retest-2026-10-16"
UA = yaml.safe_load((REPO_ROOT / "config/manifests/us-uscode-house.yaml").read_text())["user_agent"]
SOURCE = "us-courtlistener-bulk-s3"


def robots(name: str) -> str:
    return (RETEST / f"{name}.robots.txt").read_text(encoding="utf-8")


def test_source_is_active_approved_and_registered(catalog: Catalog) -> None:
    source = catalog.sources.get(SOURCE)
    assert source.status == "active" and source.connector == "courtlistener_bulk" == source.parser
    assert source.kind == "open_data_repository" and source.authority == "unofficial"
    assert source.document_kinds == ["judgment"] and source.jurisdictions == ["US"] and source.languages == ["en"]
    licence_gate(source)
    licence_gate(source, commercial=True)  # the Public Domain Mark does not exclude commercial use
    assert source.licence.id == "PDM-1.0" and source.licence.review_status == "approved"
    assert source.licence.url == "https://wiki.free.law/c/courtlistener/help/api/bulk-data/bulk-legal-data"
    assert CONNECTORS["courtlistener_bulk"] is cl.CourtListenerBulkConnector
    assert PARSER_VERSIONS["courtlistener_bulk"] == "1.0.0" and "courtlistener_bulk" in PARSERS
    assert "{retrieved_date}" in (source.licence.attribution_text or "")


def test_the_licence_conditions_name_the_columns_and_flags_that_are_never_used(catalog: Catalog) -> None:
    conditions = " ".join(catalog.sources.get(SOURCE).licence.conditions)
    for needle in ("html_with_citations", "html_anon_2020", "xml_scan", "blocked", "date_blocked", "courtsummary"):
        assert needle in conditions, needle
    # and the code agrees with the words
    assert "html_with_citations" not in cl.TEXT_COLUMNS_ALL and "html_anon_2020" not in cl.TEXT_COLUMNS_DEFAULT
    assert cl.TEXT_COLUMNS_ALL[:5] == cl.TEXT_COLUMNS_DEFAULT


def test_the_website_refusal_stays_on_file_and_is_never_contacted(catalog: Catalog) -> None:
    refused = catalog.sources.get("us-courtlistener-bulk")
    assert refused.status == "planned" and "403" in (refused.rate_limits or "")
    with pytest.raises(LicenceNotApproved):
        licence_gate(refused)
    manifest = cl.load_manifest(REPO_ROOT / "config/manifests/us-courtlistener-bulk-s3.yaml", SOURCE)
    files = cl.CourtListenerFiles(manifest, Path("/nonexistent"), offline=True)
    with pytest.raises(FetchError, match="only https://com-courtlistener-storage"):
        files._download("https://www.courtlistener.com/api/bulk-data/opinions.csv.bz2", Path("/nonexistent/x"), 1)
    assert all(
        files.url(k).startswith("https://com-courtlistener-storage.s3-us-west-2.amazonaws.com/bulk-data/")
        for k in cl.FILE_KEYS
    )


def test_robots_txt_of_the_documentation_hosts_allow_what_the_source_reads() -> None:
    free = robots("free.law")
    assert robots_allows(free, UA, "https://free.law/2024/03/01/anything")
    wiki = robots("wiki.free.law")
    assert robots_allows(wiki, UA, "https://wiki.free.law/c/courtlistener/help/api/bulk-data/bulk-legal-data")
    assert not robots_allows(wiki, UA, "https://wiki.free.law/api/anything")
    lil = robots("lil.law.harvard.edu")
    assert "Crawl-delay: 10" in lil and robots_allows(lil, UA, "https://lil.law.harvard.edu/blog/2024/03/26/x")


def test_caselaw_access_project_host_still_refuses_every_robot() -> None:
    parser = urllib.robotparser.RobotFileParser()
    parser.parse(robots("static.case.law").splitlines())
    assert not parser.can_fetch(UA, "https://static.case.law/ReportersMetadata.json")
    assert not parser.can_fetch("anything", "https://static.case.law/")


def test_other_planned_us_sources_stay_planned_for_the_reasons_recorded(catalog: Catalog) -> None:
    sc = robots("supremecourt.gov")
    assert "Crawl-delay:1" in sc and "Crawl-delay:10" not in sc  # unchanged since 2026-10-14
    assert robots_allows(sc, UA, "https://www.supremecourt.gov/opinions/USReports.aspx")
    assert robots_allows(sc, UA, "https://www.supremecourt.gov/pdfs/USReports/USREPORTS-4_PDFA.pdf")
    assert not robots_allows(sc, UA, "https://www.supremecourt.gov/rss/x")
    for host in ("www.justia.com", "supreme.justia.com"):
        assert robots_allows(
            robots(host), UA, f"https://{host}/cases/federal/us/410/113/"
        )  # allowed, but the terms are HTTP 403
    for source_id in ("us-caselaw-access-project", "us-justia-supreme-court", "us-supremecourt-gov-opinions"):
        assert catalog.sources.get(source_id).status == "planned"
        assert "RE-TESTED 2026-10-16" in (REPO_ROOT / f"config/sources/{source_id}.yaml").read_text(encoding="utf-8")

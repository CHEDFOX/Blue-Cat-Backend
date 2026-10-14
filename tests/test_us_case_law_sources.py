"""US case-law sources that were examined and are not ingested: the licence gate refuses them, the evidence stays.

CourtListener answered HTTP 403 to the ULIS bot (nothing could be read or fetched), Cornell LII's terms restrict
commercial use of its compilation, Justia's terms page answered HTTP 403, and the Supreme Court's own site publishes
opinions only as PDF files (planned). The robots.txt copies in tests/fixtures/us_case_law_sources/ were read before
anything else was requested from those hosts; GovInfo's is in tests/fixtures/govinfo_usreports/.
"""

from __future__ import annotations

import pytest
import yaml

from ulis.catalog import Catalog
from ulis.ingestion.pipeline import CONNECTORS, PARSERS
from ulis.settings import REPO_ROOT
from ulis.sources.connectors.moj_laws import robots_allows
from ulis.sources.registry import LicenceNotApproved, licence_gate

FIX = REPO_ROOT / "tests" / "fixtures" / "us_case_law_sources"
UA = yaml.safe_load((REPO_ROOT / "config/manifests/us-uscode-house.yaml").read_text())["user_agent"]

NOT_INGESTED = {
    "us-courtlistener-bulk": "planned",
    "us-cornell-lii": "rejected",
    "us-justia-supreme-court": "planned",
    "us-supremecourt-gov-opinions": "planned",
}


@pytest.mark.parametrize("source_id", sorted(NOT_INGESTED))
def test_source_is_not_ingestible_and_the_licence_gate_refuses_it(catalog: Catalog, source_id: str) -> None:
    source = catalog.sources.get(source_id)
    assert source.status == NOT_INGESTED[source_id] and source.connector is None and source.parser is None
    assert source.document_kinds == ["judgment"] and source.jurisdictions == ["US"]
    assert source.licence.review_status in ("pending_review", "rejected")
    with pytest.raises(LicenceNotApproved, match="status is|no connector"):
        licence_gate(source)
    assert source_id not in CONNECTORS and not any(source_id.split("us-")[1] in name for name in PARSERS)
    assert source.known_limitations and source.known_limitations[0].startswith("Not ingested")


def test_courtlistener_refusal_is_recorded_not_worked_around(catalog: Catalog) -> None:
    source = catalog.sources.get("us-courtlistener-bulk")
    assert "403" in (source.rate_limits or "") and "CloudFront" in (source.rate_limits or "")
    assert source.licence.id == "unknown" and source.licence.bulk_download == "not_allowed"
    assert source.licence.redistribution == "unknown" and source.licence.commercial_use == "unknown"
    assert source.provenance == "Not retrieved."  # nothing was fetched from the host, not even its S3 bucket


def test_supreme_court_site_allows_robots_on_the_opinion_pages_but_publishes_pdf_only(catalog: Catalog) -> None:
    robots = (FIX / "supremecourt.gov.robots.txt").read_text(encoding="utf-8")
    base = "https://www.supremecourt.gov"
    for path in ("/opinions/slipopinion/25", "/opinions/USReports.aspx", "/opinions/boundvolumes/587BV.pdf"):
        assert robots_allows(robots, UA, base + path), path
    for path in ("/rss/", "/images/x.png", "/cdn/x"):
        assert not robots_allows(robots, UA, base + path), path
    assert not robots_allows(robots, "SOTScraper/1.0", base + "/opinions/USReports.aspx")
    assert "Crawl-delay:1" in robots
    source = catalog.sources.get("us-supremecourt-gov-opinions")
    assert source.kind == "official_court_database" and source.licence.bulk_download == "allowed_with_conditions"
    assert any("PDF" in lim for lim in source.known_limitations)  # not refused: the work is not done


def test_other_hosts_robots_txt_as_recorded() -> None:
    lii = (FIX / "law.cornell.edu.robots.txt").read_text(encoding="utf-8")
    assert "Crawl-delay: 10" in lii and robots_allows(lii, UA, "https://www.law.cornell.edu/supremecourt/text/410/113")
    for name in ("supreme.justia.com", "www.justia.com"):
        justia = (FIX / f"{name}.robots.txt").read_text(encoding="utf-8")
        assert robots_allows(justia, UA, f"https://{name}/cases/federal/us/410/113/")
    # robots.txt alone grants no reuse right: LII's terms restrict commercial use, Justia's terms were unreadable
    assert (
        "CC-BY-NC-SA" in yaml.safe_load((REPO_ROOT / "config/sources/us-cornell-lii.yaml").read_text())["licence"]["id"]
    )

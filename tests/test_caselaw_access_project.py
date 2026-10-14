"""Caselaw Access Project (US case law): recorded as planned, not ingested.

The data is licensed CC0, but the host that serves it (static.case.law) publishes a robots.txt that
disallows every robot, so ULIS fetched no case. These tests keep that decision and its evidence
visible, and pin down how Supreme Court decisions rank in the US court hierarchy once a permitted
source exists.
"""

from __future__ import annotations

import urllib.robotparser

import pytest
import yaml

from ulis.catalog import Catalog
from ulis.ingestion.pipeline import CONNECTORS, PARSERS
from ulis.retrieval.authority import classify_judgment
from ulis.settings import REPO_ROOT
from ulis.sources.registry import LicenceNotApproved, licence_gate

SOURCE = "us-caselaw-access-project"
FIX = REPO_ROOT / "tests" / "fixtures" / "caselaw_access_project"
USER_AGENT = yaml.safe_load((REPO_ROOT / "config/manifests/us-uscode-house.yaml").read_text())["user_agent"]
CIRCUITS = [f"US-CA{n}" for n in range(1, 12)] + ["US-CADC", "US-CAFC"]


def _robots(host: str) -> urllib.robotparser.RobotFileParser:
    parser = urllib.robotparser.RobotFileParser()
    parser.parse((FIX / f"{host}.robots.txt").read_text(encoding="utf-8").splitlines())
    return parser


def test_source_is_planned_and_refused_by_the_licence_gate(catalog: Catalog) -> None:
    source = catalog.sources.get(SOURCE)
    assert source.status == "planned" and source.connector is None and source.parser is None
    assert source.kind == "open_data_repository" and source.authority == "unofficial"
    assert source.document_kinds == ["judgment"] and source.jurisdictions == ["US"]
    # The data licence is CC0; the refusal is about the access policy of the host.
    assert source.licence.id == "CC0-1.0" and source.licence.review_status == "pending_review"
    assert source.licence.bulk_download == "not_allowed"
    assert "Disallow: /" in (source.rate_limits or "")
    with pytest.raises(LicenceNotApproved, match="bulk_download"):
        licence_gate(source)
    assert SOURCE not in CONNECTORS and not any("caselaw" in name.lower() for name in PARSERS)


def test_recorded_robots_txt_disallows_the_ulis_bot_on_the_data_host() -> None:
    data = _robots("static.case.law")
    base = "https://static.case.law"
    for path in ("/", "/ReportersMetadata.json", "/us/", "/us/410/cases/0113-01.json", "/f3d/1/VolumesMetadata.json"):
        assert not data.can_fetch(USER_AGENT, base + path), path
    # The project website only disallows its case browser; its terms pages may be read.
    site = _robots("case.law")
    assert site.can_fetch(USER_AGENT, "https://case.law/terms/")
    assert not site.can_fetch(USER_AGENT, "https://case.law/caselaw/")


def test_supreme_court_ranks_above_the_courts_of_appeals(catalog: Catalog) -> None:
    reg = catalog.jurisdictions
    profile = reg.get("US")
    assert [c.id for c in profile.courts] == ["US-SC", *CIRCUITS]
    assert min(c.rank for c in profile.courts) == 1
    assert [c.id for c in profile.courts if c.rank == 1] == ["US-SC"]
    assert reg.courts_above("US-CA9") == ["US-SC"]
    assert reg.courts_below("US-SC") == sorted(CIRCUITS)
    assert "ULIS" not in profile.coverage.known_gaps  # sanity: gaps are prose about sources, not the project
    gaps = " ".join(profile.coverage.known_gaps)
    assert "Caselaw Access Project" in gaps and "robots.txt" in gaps and "never be inferred" in gaps


def test_supreme_court_decisions_are_binding_on_the_circuits_and_a_circuit_only_on_its_own_courts(
    catalog: Catalog,
) -> None:
    reg = catalog.jurisdictions

    def classify(court: str, forum: str | None) -> str:
        return classify_judgment(
            reg, target="US", judgment_jurisdiction="US", court_id=court, forum_court=forum, overruled_by=None
        ).category

    # Without a forum court the Supreme Court is the one court whose decisions bind courts below it.
    supreme = classify_judgment(
        reg, target="US", judgment_jurisdiction="US", court_id="US-SC", forum_court=None, overruled_by=None
    )
    assert supreme.category == "precedent_vertical" and "Ninth Circuit" in supreme.rationale
    assert classify("US-SC", "US-CA9") == "binding_precedent"
    assert classify("US-SC", "US-CAFC") == "binding_precedent"
    # A circuit binds itself, but is not above any other circuit; no lower court is modelled.
    assert classify("US-CA9", "US-CA9") == "binding_precedent"
    assert classify("US-CA9", "US-CA2") == "persuasive_precedent"
    assert classify("US-CA9", "US-SC") == "persuasive_precedent"
    # The data sets examined carry no judicial treatment: nothing is marked overruled unless a source says so.
    overruled = classify_judgment(
        reg, target="US", judgment_jurisdiction="US", court_id="US-SC", forum_court=None, overruled_by="/x"
    )
    assert overruled.category == "overruled_precedent"

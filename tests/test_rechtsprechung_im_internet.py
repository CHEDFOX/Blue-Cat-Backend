"""Rechtsprechung im Internet (German federal case law): recorded as planned, not ingested.

The publisher's robots.txt disallows automated access for every user agent except the European
e-Justice Portal's ECLI crawler, so ULIS fetched nothing beyond robots.txt. These tests keep that
decision and its evidence visible, and pin down how German federal decisions would be classified
under the current DE profile once a permitted source exists.
"""

from __future__ import annotations

import urllib.robotparser

import pytest
import yaml

from ulis.catalog import Catalog
from ulis.retrieval.authority import classify_judgment
from ulis.settings import REPO_ROOT
from ulis.sources.registry import LicenceNotApproved, licence_gate

SOURCE = "de-rechtsprechung-im-internet"
FIX = REPO_ROOT / "tests" / "fixtures" / "rechtsprechung_im_internet"
USER_AGENT = yaml.safe_load((REPO_ROOT / "config/manifests/de-gesetze-im-internet.yaml").read_text())["user_agent"]


def _robots(host: str) -> urllib.robotparser.RobotFileParser:
    parser = urllib.robotparser.RobotFileParser()
    parser.parse((FIX / f"{host}.robots.txt").read_text(encoding="utf-8").splitlines())
    return parser


def test_source_is_planned_and_refused_by_the_licence_gate(catalog: Catalog) -> None:
    source = catalog.sources.get(SOURCE)
    assert source.status == "planned" and source.connector is None and source.parser is None
    assert source.kind == "official_court_database" and source.document_kinds == ["judgment"]
    assert source.licence.bulk_download == "not_allowed"
    assert "Disallow: /" in (source.rate_limits or "")
    with pytest.raises(LicenceNotApproved, match="bulk_download"):
        licence_gate(source)


def test_recorded_robots_txt_disallows_the_ulis_bot() -> None:
    rii = _robots("www.rechtsprechung-im-internet.de")
    base = "https://www.rechtsprechung-im-internet.de"
    for path in ("/", "/rii-toc.xml", "/jportal/docs/bsjrs/", "/jportal/portal/page/bsjrsprod.psml"):
        assert not rii.can_fetch(USER_AGENT, base + path), path
        # The exclusion is targeted: the European e-Justice Portal's ECLI crawler is let in.
        assert rii.can_fetch("DG_JUSTICE_CRAWLER", base + path), path
    # The test-phase federal legal information portal (NeuRIS) was checked as an alternative.
    neuris = _robots("testphase.rechtsinformationen.bund.de")
    assert not neuris.can_fetch(USER_AGENT, "https://testphase.rechtsinformationen.bund.de/v1/case-law")


def test_federal_court_decisions_would_be_persuasive_with_the_bverfgg_note(catalog: Catalog) -> None:
    """No formal stare decisis in the DE profile: every decision is labelled persuasive, and the
    rationale carries the profile's "To verify" note on § 31 BVerfGG (binding effect, force of law)."""
    reg = catalog.jurisdictions
    assert {"DE-BVERFG", "DE-BGH", "DE-BVERWG", "DE-BAG", "DE-BFH", "DE-BSG"} <= {c.id for c in reg.get("DE").courts}
    for court in ("DE-BVERFG", "DE-BGH"):
        authority = classify_judgment(
            reg, target="DE", judgment_jurisdiction="DE", court_id=court, forum_court=None, overruled_by=None
        )
        assert authority.category == "persuasive_precedent"
        assert "§ 31 Abs. 1 BVerfGG" in authority.rationale and "To verify" in authority.rationale
    foreign = classify_judgment(
        reg, target="CH", judgment_jurisdiction="DE", court_id="DE-BGH", forum_court=None, overruled_by=None
    )
    assert foreign.category == "foreign"

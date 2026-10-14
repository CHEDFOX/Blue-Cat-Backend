"""Jurisdiction profiles, source descriptors and the licence gate (no database needed)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ulis.catalog import Catalog
from ulis.jurisdictions.profile import JurisdictionProfile
from ulis.jurisdictions.registry import JurisdictionRegistry, ProfileError
from ulis.settings import REPO_ROOT
from ulis.sources.registry import LicenceNotApproved, licence_gate


def _profile(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = yaml.safe_load((REPO_ROOT / "config/jurisdictions/xa-sp.yaml").read_text())
    base.update(overrides)
    return base


def test_all_profiles_load_and_cross_validate(catalog: Catalog) -> None:
    assert set(catalog.jurisdictions.profiles) == {
        "INT",
        "XR",
        "XA",
        "XA-NP",
        "XA-SP",
        "XB",
        "IN",
        "US",
        "GB",
        "GB-EAW",
        "GB-ENG",
        "GB-WLS",
        "GB-SCT",
        "GB-NIR",
        "CA",
        "CH",
        "COE",
        "DE",
        "ES",
        "JP",
        "EU",
        "FI",
        "AU",
        "NL",
        "TW",
        "SE",
        "DK",
        "IT",
        "IE",
        "AT",
        "LV",
        "EE",
        "NZ",
        "PL",
        "AR",
        "LU",
        "PT",
        "UY",
    }
    assert all(p.fictional for p in catalog.jurisdictions.profiles.values() if p.id.startswith("X"))
    assert catalog.jurisdictions.get("IN").verification.status == "draft_unverified"
    assert catalog.jurisdictions.get("IN").coverage.corpus_status == "none"


def test_hierarchy_navigation(catalog: Catalog) -> None:
    reg = catalog.jurisdictions
    assert reg.chain("XA-NP") == ["XA-NP", "XA", "INT"]
    assert reg.sovereign("XA-NP") == "XA"
    assert reg.sovereign("XR") is None
    assert reg.descendants("XA") == ["XA-NP", "XA-SP"]
    # A federal Act extends to every province; a provincial Act only to its province.
    assert reg.extent_covers(["XA"], "XA-NP")
    assert reg.extent_covers(["XA-NP"], "XA-NP")
    assert not reg.extent_covers(["XA-NP"], "XA-SP")
    assert not reg.extent_covers(["XA-NP"], "XA")


def test_court_hierarchy(catalog: Catalog) -> None:
    reg = catalog.jurisdictions
    assert reg.courts_above("XA-NP-DC") == ["XA-NP-HC", "XA-SC"]
    assert reg.courts_below("XA-SC") == ["XA-NP-DC", "XA-NP-HC", "XA-SP-HC"]
    assert reg.court_jurisdiction("XB-CC") == "XB"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"fictional": False}, "fictional profiles"),
        ({"verification": {"status": "reviewed"}}, "reviewed_by"),
        ({"profile_version": "1.0"}, "MAJOR.MINOR.PATCH"),
        ({"id": "xa-sp"}, "invalid jurisdiction id"),
        ({"norm_hierarchy": ["act", "decree"]}, "undeclared instrument types"),
        ({"coverage": {"corpus_status": "broad"}}, "fictional_sample"),
    ],
)
def test_invalid_profiles_are_rejected(overrides: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        JurisdictionProfile.model_validate(_profile(**overrides))


def test_registry_rejects_unknown_parent_and_cycles(tmp_path: Path) -> None:
    (tmp_path / "a.yaml").write_text(yaml.safe_dump(_profile(id="XA-SP", parent="ZZ")))
    with pytest.raises(ProfileError, match="unknown parent"):
        JurisdictionRegistry.from_directory(tmp_path)
    (tmp_path / "a.yaml").write_text(yaml.safe_dump(_profile(id="XQ", parent="XZ", courts=[])))
    (tmp_path / "b.yaml").write_text(yaml.safe_dump(_profile(id="XZ", parent="XQ", courts=[])))
    with pytest.raises(ProfileError, match="cycle"):
        JurisdictionRegistry.from_directory(tmp_path)


def test_catalog_rejects_unknown_official_source(tmp_path: Path) -> None:
    for sub in ("jurisdictions", "sources", "concepts"):
        (tmp_path / sub).mkdir()
        for f in (REPO_ROOT / "config" / sub).glob("*.yaml"):
            (tmp_path / sub / f.name).write_text(f.read_text())
    profile = yaml.safe_load((tmp_path / "jurisdictions/xa.yaml").read_text())
    profile["publication"]["official_sources"] = ["no-such-source"]
    (tmp_path / "jurisdictions/xa.yaml").write_text(yaml.safe_dump(profile))
    with pytest.raises(ProfileError, match="unknown official source"):
        Catalog.load(tmp_path)


def test_profile_hash_is_stable(catalog: Catalog) -> None:
    profile = catalog.jurisdictions.get("XA")
    assert profile.sha256() == JurisdictionProfile.model_validate(profile.model_dump(mode="json")).sha256()


def test_licence_gate_allows_only_approved_sources(catalog: Catalog) -> None:
    approved = {
        "ulis-fictional-sample",
        "us-gpo-uslm-samples",
        "us-uscode-house",
        "us-ecfr",
        "gb-legislation-gov-uk",
        "ca-justice-laws-xml",
        "ch-fedlex",
        "ch-fedlex-treaties",
        "de-gesetze-im-internet",
        "es-boe-consolidada",
        "jp-egov-laws",
        "eu-eur-lex",
        "eu-cellar-case-law",
        "fi-finlex",
        "au-federal-register",
        "nl-bwb",
        "nl-rechtspraak",
        "tw-moj-laws",
        "se-riksdagen-sfs",
        "dk-retsinformation",
        "it-normattiva",
        "ie-irish-statute-book",
        "lv-likumi",
        "ee-riigi-teataja",
        "pl-eli-sejm",
        "ar-infoleg",
        "lu-legilux",
        "uy-impo",
    }
    for source_id in approved:
        licence_gate(catalog.sources.get(source_id))
    for source in catalog.sources:
        if source.id in approved:
            continue
        with pytest.raises(LicenceNotApproved) as exc:
            licence_gate(source)
        reasons = ("pending_review", "not_allowed", "status is 'planned'")
        assert any(r in str(exc.value) for r in reasons)


def test_find_case_law_records_computational_analysis_restriction(catalog: Catalog) -> None:
    source = catalog.sources.get("gb-find-case-law")
    assert source.licence.automated_processing == "not_allowed"
    with pytest.raises(LicenceNotApproved, match="automated_processing"):
        licence_gate(source)


def test_every_active_source_has_a_registered_connector_and_parser(catalog: Catalog) -> None:
    from ulis.ingestion.pipeline import CONNECTORS, PARSERS

    for source in catalog.sources:
        if source.status == "active":
            assert source.connector == "local_manifest" or source.connector in CONNECTORS, source.id
            assert source.parser in PARSERS, source.id

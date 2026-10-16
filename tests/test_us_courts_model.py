"""The US court list that CourtListener cases use: generated from its court table, consistent with the profile."""

from __future__ import annotations

import gzip
import importlib.util
import io
from pathlib import Path
from types import ModuleType

import pytest

from ulis.catalog import Catalog
from ulis.settings import REPO_ROOT
from ulis.sources.parsers.courtlistener_courts import COURTS

FIXTURE = REPO_ROOT / "tests" / "fixtures" / "courtlistener_bulk" / "courts-2026-09-30.csv.gz"


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("build_us_courts", REPO_ROOT / "scripts" / "build_us_courts.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def generated() -> list[dict[str, object]]:
    import csv

    script = _script()
    with gzip.open(FIXTURE, "rt", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(io.StringIO(handle.read(), newline="")))
    return script.build(rows, script.DEFAULT_GROUPS)  # type: ignore[no-any-return]


def test_the_committed_module_and_profile_are_what_the_script_generates(
    generated: list[dict[str, object]], catalog: Catalog
) -> None:
    assert {r["cl"]: (r["id"], r["name"], r["group"], r["citation"]) for r in generated} == {
        k: (c.ulis_id, c.name, c.group, c.citation) for k, c in COURTS.items()
    }
    profile = {c.id: c for c in catalog.jurisdictions.get("US").courts}
    for r in generated:
        if r["existing"]:
            continue
        court = profile[str(r["id"])]
        assert (court.name, court.rank, court.appeals_to) == (r["name"], r["rank"], r["appeals_to"])
    assert len(profile) == 14 + sum(1 for r in generated if not r["existing"])


def test_every_court_has_a_unique_id_a_profile_court_and_valid_appeals(catalog: Catalog) -> None:
    reg = catalog.jurisdictions
    ids = [c.ulis_id for c in COURTS.values()]
    assert len(ids) == len(set(ids))
    for court in COURTS.values():
        assert reg.court_jurisdiction(court.ulis_id) in ("US", "US-WA", "US-TX", "US-OR", "US-UT")
    assert [COURTS[k].ulis_id for k in ("scotus", "wash", "ca9")] == ["US-SC", "US-WA-SC", "US-CA9"]
    assert len(COURTS) > 500


def test_hierarchy_follows_the_law_where_it_can_be_derived(catalog: Catalog) -> None:
    reg = catalog.jurisdictions
    # district court -> circuit of its State (28 U.S.C. 41) -> Supreme Court
    assert reg.courts_above(COURTS["nysd"].ulis_id) == ["US-CA2", "US-SC"]
    first = {k: reg.courts_above(COURTS[k].ulis_id)[0] for k in ("cacd", "dcd", "nmid", "nmd")}
    assert first == {"cacd": "US-CA9", "dcd": "US-CADC", "nmid": "US-CA9", "nmd": "US-CA10"}
    # bankruptcy court -> district court of the same district
    assert reg.courts_above(COURTS["nysb"].ulis_id)[:2] == [COURTS["nysd"].ulis_id, "US-CA2"]
    # state intermediate courts -> their State's highest court -> Supreme Court (federal questions)
    assert reg.courts_above(COURTS["washctapp"].ulis_id) == ["US-WA-SC", "US-SC"]
    assert reg.courts_above(COURTS["calctapp"].ulis_id) == [COURTS["cal"].ulis_id, "US-SC"]
    assert set(reg.courts_above(COURTS["txctapp5"].ulis_id)) >= {"US-TX-SC", COURTS["texcrimapp"].ulis_id, "US-SC"}
    assert reg.courts_above(COURTS["uscfc"].ulis_id)[0] == "US-CAFC"
    # a trial-level court that CourtListener files under "appellate" gets no invented appeal
    assert COURTS["njch"].ulis_id in {c.id for c in reg.get("US").courts}
    assert reg.courts_above(COURTS["njch"].ulis_id) == []
    # historical circuit courts: no hierarchy is derived
    assert reg.courts_above(COURTS["circtdal"].ulis_id) == []


def test_state_apex_courts_keep_the_ids_the_state_profiles_already_define(catalog: Catalog) -> None:
    for cl_id, ulis_id in (("wash", "US-WA-SC"), ("tex", "US-TX-SC"), ("or", "US-OR-SC"), ("utah", "US-UT-SC")):
        assert COURTS[cl_id].ulis_id == ulis_id
        assert catalog.jurisdictions.court_jurisdiction(ulis_id) == ulis_id.rsplit("-", 1)[0]


def test_a_generated_court_id_fits_the_database_column() -> None:
    assert max(len(c.ulis_id) for c in COURTS.values()) <= 40  # courts.id is String(40)
    assert all(c.name for c in COURTS.values())


def test_the_script_is_deterministic(generated: list[dict[str, object]], tmp_path: Path) -> None:
    script = _script()
    target = tmp_path / "us.yaml"
    target.write_text("id: US\nprecedent:\n  doctrine: x\n", encoding="utf-8")
    script.write_profile(generated, target)
    once = target.read_text(encoding="utf-8")
    script.write_profile(generated, target)
    assert target.read_text(encoding="utf-8") == once and once.count("BEGIN GENERATED COURTS") == 1
    assert once.index("BEGIN GENERATED COURTS") < once.index("precedent:")

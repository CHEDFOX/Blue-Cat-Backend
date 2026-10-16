"""The assistant's tools and authority registry, plus retrieval smoke checks the tools depend on (PostgreSQL)."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Any

import pytest
from assistant_support import S12_1, S12_1_URI
from sqlalchemy.orm import Session, sessionmaker

from ulis.assistant import tools as tools_module
from ulis.assistant.registry import AuthorityRegistry
from ulis.assistant.tools import ToolBox, tool_specs
from ulis.assistant.verify import reread
from ulis.catalog import Catalog
from ulis.ingestion.pipeline import IngestionReport

pytestmark = pytest.mark.db
TODAY = date(2026, 10, 14)


@pytest.fixture
def box(session_factory: sessionmaker[Session], catalog: Catalog, ingested: IngestionReport) -> ToolBox:
    return ToolBox(session_factory, catalog, AuthorityRegistry(), today=TODAY, max_searches=5)


@pytest.fixture
def us_box(session_factory: sessionmaker[Session], catalog: Catalog, us_ingested: IngestionReport) -> ToolBox:
    return ToolBox(session_factory, catalog, AuthorityRegistry(), today=TODAY, max_searches=10)


def call(box: ToolBox, name: str, **args: Any) -> dict[str, Any]:
    out = box.execute(name, args)
    return json.loads(out.content)  # type: ignore[no-any-return]


def search(box: ToolBox, query: str, jurisdiction: str, as_of: str, **extra: Any) -> dict[str, Any]:
    return call(box, "search_law", query=query, jurisdiction=jurisdiction, as_of=as_of, **extra)


def citations(result: dict[str, Any]) -> list[str]:
    return [r["citation"] for r in result["results"]]


# --------------------------------------------------------------------------- definitions and registry


def test_tool_definitions_are_strict_closed_and_stable(catalog: Catalog) -> None:
    specs = tool_specs(catalog)
    assert [s["name"] for s in specs] == ["search_law", "get_provision", "list_jurisdictions", "coverage"]
    for spec in specs:
        schema = spec["input_schema"]
        assert spec["strict"] is True and schema["additionalProperties"] is False
        assert set(schema.get("required", [])) <= set(schema["properties"])
        assert spec["description"]
    assert specs[0]["input_schema"]["properties"]["jurisdiction"]["enum"] == sorted(catalog.jurisdictions.profiles)
    assert specs[0]["input_schema"]["required"] == ["query", "jurisdiction", "as_of"]
    assert json.dumps(specs, sort_keys=True) == json.dumps(tool_specs(catalog), sort_keys=True)


def test_authority_ids_are_stable_and_deduplicated(box: ToolBox) -> None:
    first = search(box, "retain personal data", "XA", "2024-01-01")
    again = search(box, "personal data retention period", "XA", "2024-01-01")
    by_citation = {r["citation"]: r["authority_id"] for r in first["results"]}
    assert by_citation[S12_1] == "A1"
    assert {r["citation"]: r["authority_id"] for r in again["results"]}[S12_1] == "A1"
    assert len(box.registry) == len(
        {r["authority_id"] for r in first["results"]} | {r["authority_id"] for r in again["results"]}
    )
    read = call(box, "get_provision", uri=S12_1_URI, as_of="2024-01-01")
    assert read["authority_id"] == "A1" and read["citation"] == S12_1  # same stored text, same id
    record = box.registry.get("A1")
    assert record is not None and record.text_sha256 == hashlib.sha256(record.text.encode()).hexdigest()
    assert record.source["name"] and record.source["url"] is None  # internal manifest locators are not exposed as links


def test_registry_keeps_exact_text_while_the_model_sees_a_clipped_copy(
    box: ToolBox, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tools_module, "SEARCH_TEXT_CHARS", 20)
    result = search(box, "retain personal data", "XA", "2024-01-01")
    hit = next(r for r in result["results"] if r["citation"] == S12_1)
    assert hit["text_truncated"] is True and len(hit["text"]) <= 20
    record = box.registry.get(hit["authority_id"])
    assert record is not None and record.text.startswith(hit["text"]) and len(record.text) > 20


def test_search_results_are_capped_and_carry_limitations(box: ToolBox) -> None:
    result = search(box, "personal data", "XA", "2024-01-01")
    assert 0 < len(result["results"]) <= tools_module.SEARCH_RESULT_LIMIT
    assert all(r["limitations"] and r["authority"] for r in result["results"])
    assert result["searches_left_for_this_issue"] == 4
    assert any("FICTIONAL_DATA" in n for n in result["search_notes"])


def test_point_in_time_provision_versions_are_distinct_authorities(box: ToolBox) -> None:
    old = call(box, "get_provision", uri=S12_1_URI, as_of="2020-06-01")
    new = call(box, "get_provision", uri=S12_1_URI, as_of="2024-01-01")
    assert "seven years" in old["text"] and "five years" in new["text"]
    assert old["authority_id"] != new["authority_id"] and old["valid_to"] == "2022-01-01"
    assert any(v["selected"] for v in old["other_versions"]) and len(old["other_versions"]) >= 2


def test_provision_with_no_version_on_the_date_yields_no_citable_authority(box: ToolBox) -> None:
    result = call(box, "get_provision", uri="/akn/xa/act/2021/4/~sec_2", as_of="2019-01-01")
    assert "authority_id" not in result and "no text to cite" in result["note"]
    assert any(lim["code"] == "NO_VERSION_ON_DATE" for lim in result["limitations"])
    assert len(box.registry) == 0


def test_jurisdiction_and_coverage_tools_state_what_is_indexed(box: ToolBox) -> None:
    listing = call(box, "list_jurisdictions")["jurisdictions"]
    by_id = {j["id"]: j for j in listing}
    assert by_id["XA"]["fictional"] is True and by_id["XA"]["corpus_status"] == "fictional_sample"
    assert by_id["DE"]["fictional"] is False and by_id["XA-NP"]["parent"] == "XA"
    xa = call(box, "coverage", jurisdiction="XA")
    assert xa["instruments"] > 0 and xa["passages"] > 0 and xa["known_gaps"]
    assert {s["id"] for s in xa["sub_jurisdictions"]} >= {"XA-NP", "XA-SP"}
    de = call(box, "coverage", jurisdiction="DE")
    assert de["instruments"] == 0 and de["judgments"] == 0
    assert set(box.coverage_seen) == {"XA", "DE"}


def test_a_bad_call_costs_nothing_and_unknown_things_are_refused(box: ToolBox) -> None:
    assert box.execute("search_law", {"query": "", "jurisdiction": "XA", "as_of": "2024-01-01"}).is_error
    assert box.execute("search_law", {"query": "x", "jurisdiction": "XA", "as_of": "2024-02-30"}).is_error
    assert box.execute(
        "search_law", {"query": "x", "jurisdiction": "XA", "as_of": "2024-01-01", "languages": ["??"]}
    ).is_error
    assert box.execute("search_law", "not an object").is_error  # type: ignore[arg-type]
    assert box.execute("shell", {"cmd": "id"}).is_error
    assert box.searches_used == 0
    box.begin_issue(1)
    assert not box.execute(
        "search_law", {"query": "personal data", "jurisdiction": "XA", "as_of": "2024-01-01"}
    ).is_error
    exhausted = box.execute("search_law", {"query": "personal data", "jurisdiction": "XA", "as_of": "2024-01-01"})
    assert exhausted.is_error and box.searches_used == 1


def test_judgment_paragraphs_register_with_the_paragraph_id_and_reread_clean(
    box: ToolBox, session_factory: sessionmaker[Session]
) -> None:
    result = search(box, "retain personal data", "XA", "2024-01-01")
    hit = next(r for r in result["results"] if r["kind"] == "judgment_paragraph")
    record = box.registry.get(hit["authority_id"])
    assert record is not None and record.kind == "judgment_paragraph" and record.uri == hit["uri"]
    with session_factory() as s:
        again = reread(s, record)
    assert again.verified and again.text == record.text and again.valid_from is None


# --------------------------------------------------------------------------- retrieval smoke checks (fixtures only)


def test_smoke_real_us_statute_is_found_with_its_exact_citation(us_box: ToolBox) -> None:
    result = search(us_box, "women STEM disciplines", "US", "2018-01-01")
    hit = next(r for r in result["results"] if r["uri"].startswith("/us/pl/115/6"))
    assert hit["citation"].startswith("Promoting Women in Entrepreneurship Act, Pub. L. No. 115-6")
    # a public law is held as enacted: in force at the source date, later amendments not applied
    assert hit["kind"] == "provision" and "applicability on the requested date unverified" in hit["authority"]


def test_smoke_statute_is_not_returned_before_it_existed(us_box: ToolBox) -> None:
    result = search(us_box, "women STEM disciplines", "US", "2016-01-01")
    assert all(not r["uri"].startswith("/us/pl/115/6") for r in result["results"])


def test_smoke_regulation_citation_and_currency_limitation(us_box: ToolBox) -> None:
    result = search(us_box, "licensed importer records of firearms", "US", "2017-06-01")
    hit = next(r for r in result["results"] if r["citation"].startswith("27 C.F.R. "))
    assert {"ATTESTED_ONLY_AT"} <= {lim["code"] for lim in hit["limitations"]}


def test_smoke_long_enacting_section_does_not_outrank_focused_text(us_box: ToolBox) -> None:
    result = search(us_box, "anadromous fish", "US", "2020-01-01")
    assert result["results"][0]["uri"].startswith("/us/pl/89/304")


def test_smoke_multilingual_query_reaches_french_and_german_text(box: ToolBox) -> None:
    fr = search(box, "données personnelles conservées", "XB", "2026-10-14", query_language="fr")
    assert "art. 28 al. 2 CC-XB" in citations(fr)
    de = search(box, "personenbezogene Daten aufbewahren", "XB", "2026-10-14", languages=["de"])
    assert all(r["jurisdiction"] == "XB" for r in de["results"])


def test_smoke_foreign_law_is_not_searched_for_the_home_jurisdiction(box: ToolBox) -> None:
    home = search(box, "données conservées", "XA", "2026-10-14")
    assert all(r["jurisdiction"] != "XB" for r in home["results"])


def test_smoke_overruled_precedent_is_flagged(box: ToolBox) -> None:
    result = search(box, "backup tapes retained", "XA-NP", "2026-10-14")
    flagged = [r for r in result["results"] if "Overruled" in r["authority"]]
    assert flagged and any(lim["code"] == "NEGATIVE_TREATMENT" for lim in flagged[0]["limitations"])


def test_smoke_jurisdiction_without_indexed_text_gives_an_empty_result_and_a_warning(box: ToolBox) -> None:
    result = search(box, "data protection", "DE", "2024-01-01")
    assert result["results"] == [] and "does not mean the law is silent" in result["note"]
    assert result["search_notes"]  # profile / coverage limitations are passed on to the model

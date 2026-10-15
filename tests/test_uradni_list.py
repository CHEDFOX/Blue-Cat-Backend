"""Uradni list RS (Slovenia): parser, connector, citations and search behaviour (real act pages)."""

from __future__ import annotations

import gzip
import json
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ulis.catalog import Catalog
from ulis.db import models as m
from ulis.ingestion.pipeline import IngestionReport, Ingestor, build_connector
from ulis.jurisdictions.modules import ProvisionCitationInput, get_module
from ulis.jurisdictions.modules.si import genitive
from ulis.retrieval.lookup import provision_at
from ulis.retrieval.search import SearchRequest, search
from ulis.settings import REPO_ROOT
from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.polite_http import FetchError, PoliteHttp
from ulis.sources.connectors.uradni_list import UradniListConnector
from ulis.sources.normalized import ParsedDocument, ParsedProvision
from ulis.sources.parsers.legal_html import HtmlParseError
from ulis.sources.parsers.uradni_list import abbreviation_of, parse_uradni_list
from ulis.sources.registry import licence_gate
from ulis.storage.object_store import LocalObjectStore
from ulis.verification.integrity import verify_source_document

FIX = REPO_ROOT / "tests" / "fixtures" / "uradni_list"
SOURCE = "si-uradni-list"
HOST = "www.uradni-list.si"
RETRIEVED = "2026-10-14T13:00:00+00:00"
TODAY = date(2026, 10, 14)
UA = "ULIS-test (+https://example.org)"
ROBOTS = "User-agent: *\nDisallow: /_pdf/*/*a/\n"

PAGES = {
    "ul-2025-01-3978-zjrm-2.html.gz": "2025-01-3978",
    "ul-2025-01-3979-zpug.html.gz": "2025-01-3979",
    "ul-2006-01-2788-uredba-pis.html.gz": "2006-01-2788",
}


def _page(name: str) -> bytes:
    return gzip.decompress((FIX / name).read_bytes())


def _doc(name: str) -> ParsedDocument:
    docs = parse_uradni_list(_page(name))
    assert len(docs) == 1
    return docs[0]


def _prov(doc: ParsedDocument, eid: str) -> ParsedProvision:
    return next(p for p in doc.provisions if p.eid == eid)


def _cite(doc: ParsedDocument, eid: str) -> str:
    p = _prov(doc, eid)
    return get_module("si").provision_citation(
        ProvisionCitationInput(
            instrument_title=doc.title,
            instrument_type=doc.instrument_type,
            jurisdiction_id="SI",
            top_kind=p.top_kind,
            num_path=p.num_path,
            language="sl",
            ext=doc.ext,
            eid=p.eid,
        )
    )


# --------------------------------------------------------------------------- parser


def test_law_document_facts() -> None:
    doc = _doc("ul-2025-01-3978-zjrm-2.html.gz")
    assert doc.work_uri == "/si/ul/2025-01-3978" and doc.expression_uri == "/si/ul/2025-01-3978/slv@2025-12-29"
    assert doc.title == "Zakon o varstvu javnega reda in miru (ZJRM-2)" and doc.short_title == "ZJRM-2"
    assert (doc.instrument_type, doc.language, doc.jurisdiction_id) == ("act", "sl", "SI")
    assert doc.number == "Uradni list RS, št. 112/2025" and doc.version_date == date(2025, 12, 29)
    assert doc.work_date is None and doc.currency_date is None
    assert doc.authoritative is False and doc.text_origin == "official_conversion"
    assert doc.consolidation_kind == "as_published" and doc.validity_basis == "source_periods"
    assert doc.ext["ul_id"] == "2025-01-3978" and doc.ext["issue"] == "112/2025" and doc.ext["abbreviation"] == "ZJRM-2"
    assert doc.ext["consolidated_text"] is False and doc.ext["act_number"] == 3978
    assert [e.event_type for e in doc.events] == ["publication"]
    # the page gives no entry-into-force date: no period is recorded, and later amendments are not applied
    assert all(p.valid_from is None and p.valid_to is None and p.status == "in_force" for p in doc.provisions)
    assert len(doc.outstanding_changes) == 1 and "does not consolidate" in doc.outstanding_changes[0]["description"]


def test_structure_headings_paragraphs_points_and_closing() -> None:
    doc = _doc("ul-2025-01-3978-zjrm-2.html.gz")
    tops = [p.eid for p in doc.provisions if p.parent_eid is None]
    assert tops == ["preamble", *[f"art-{n}" for n in range(1, 33)], "zav-1"]
    pre = _prov(doc, "preamble")
    assert pre.text.startswith(
        "Na podlagi druge alineje prvega odstavka 107. člena in prvega odstavka 91. člena Ustave"
    )
    assert "Razglašam Zakon o varstvu javnega reda in miru (ZJRM-2)" in pre.text  # the President's decree is kept
    one = _prov(doc, "art-1")
    assert one.heading == "vsebina in namen zakona" and one.context_heading == "I. SPLOŠNE DOLOČBE"
    par1 = _prov(doc, "art-1-par1")
    assert par1.kind == "paragraph" and par1.num_path == ["art:1", "par:1"] and par1.parent_eid == "art-1"
    assert par1.text.startswith("Ta zakon ureja varstvo javnega reda in miru ter določa ravnanja")
    assert not par1.text.startswith("(1)")  # the numbering belongs to the parent's composite text
    assert one.text.startswith("(1) Ta zakon ureja varstvo javnega reda in miru")
    two = _prov(doc, "art-2")
    assert two.heading == "pomen izrazov" and two.text.startswith(
        "Izrazi, uporabljeni v tem zakonu, pomenijo:\n1. javni red in mir"
    )
    point = _prov(doc, "art-2-pt1")
    assert point.kind == "point" and point.num_path == ["art:2", "pt:1"]
    assert point.text.startswith("javni red in mir je stanje, v katerem je zagotovljeno neovirano uresničevanje pravic")
    assert _prov(doc, "art-4-par2").context_heading.startswith("II. PREKRŠKI")
    closing = _prov(doc, "zav-1")
    assert closing.kind == "closing" and closing.text.startswith("Št. 212-03/25-5/48\nLjubljana, dne 17. decembra 2025")
    assert closing.text.endswith("predsednica")


def test_short_law_and_exact_text() -> None:
    doc = _doc("ul-2025-01-3979-zpug.html.gz")
    assert doc.title == "Zakon o potrjevanju učnih gradiv (ZPUG)" and doc.version_date == date(2025, 12, 29)
    assert [p.eid for p in doc.provisions if p.kind == "article"] == [f"art-{n}" for n in range(1, 10)]
    assert _prov(doc, "art-1").heading == "vsebina zakona"
    assert _prov(doc, "art-1").text.startswith("S tem zakonom se ureja potrjevanje učbenikov, delovnih učbenikov")
    assert _prov(doc, "art-2-pt1").text == "Učno gradivo po tem zakonu so učbeniki, delovni učbeniki in delovni zvezki."
    assert not any("\xa0" in p.text for p in doc.provisions)


def test_older_regulation_with_old_class_names() -> None:
    doc = _doc("ul-2006-01-2788-uredba-pis.html.gz")
    assert doc.instrument_type == "regulation" and doc.short_title is None and doc.ext.get("abbreviation") is None
    assert doc.work_uri == "/si/ul/2006-01-2788" and doc.version_date == date(2006, 6, 23)
    assert doc.ext["issue"] == "65/2006"
    assert [p.eid for p in doc.provisions if p.kind == "article"][:3] == ["art-1", "art-2", "art-3"]
    three = _prov(doc, "art-3")
    assert three.heading == "informativni značaj"
    assert _prov(doc, "art-3-par1").text.startswith(
        "Podatki, objavljeni v pravno-informacijskem sistemu, so informativnega značaja"
    )
    assert (
        "elektronske izdaje Uradnega lista Republike Slovenije, ki je uradna izdaja od 1. januarja 2006 dalje"
        in three.text
    )


def test_citations() -> None:
    law, decree = _doc("ul-2025-01-3978-zjrm-2.html.gz"), _doc("ul-2006-01-2788-uredba-pis.html.gz")
    assert _cite(law, "art-1") == "1. člen ZJRM-2"
    assert _cite(law, "art-1-par2") == "drugi odstavek 1. člena ZJRM-2"
    assert _cite(law, "art-2-pt1") == "1. točka 2. člena ZJRM-2"
    assert _cite(law, "preamble") == "uvodni del ZJRM-2" and _cite(law, "zav-1") == "sklepni del ZJRM-2"
    assert _cite(decree, "art-3") == "3. člen Uredbe o Pravno-informacijskem sistemu Republike Slovenije"
    assert (
        _cite(decree, "art-3-par1")
        == "prvi odstavek 3. člena Uredbe o Pravno-informacijskem sistemu Republike Slovenije"
    )
    module = get_module("si")
    assert module.validate_ext(law.ext)["ul_id"] == "2025-01-3978"
    with pytest.raises(ValueError):
        module.validate_ext({**law.ext, "unexpected": 1})


def test_citation_forms_for_deeper_units() -> None:
    module = get_module("si")
    base = {"instrument_type": "act", "jurisdiction_id": "SI", "top_kind": "article", "language": "sl"}

    def cite(path: list[str], **ext: Any) -> str:
        c = ProvisionCitationInput(
            instrument_title="Zakon o X (ZX)", num_path=path, ext={"abbreviation": "ZX", **ext}, **base
        )
        return module.provision_citation(c)

    assert cite(["art:3", "par:1", "pt:2", "sub:a"]) == "podtočka a) 2. točke prvega odstavka 3. člena ZX"
    assert cite(["art:3", "pt:2"]) == "2. točka 3. člena ZX"
    assert cite(["art:12a", "par:3"]) == "tretji odstavek 12.a člena ZX"
    assert cite(["art:5", "par:20"]) == "dvajseti odstavek 5. člena ZX"
    assert cite(["art:5", "par:21"]) == "21. odstavek 5. člena ZX"
    assert cite(["pril:1"]) == "priloga 1 ZX"
    assert (
        genitive("Zakon o delu") == "Zakona o delu"
        and genitive("Ustava Republike Slovenije") == "Ustave Republike Slovenije"
    )
    no_name = ProvisionCitationInput(instrument_title="Sklep o nečem", num_path=["art:2"], ext={}, **base)
    assert module.provision_citation(no_name) == "Sklep o nečem, 2. člen"


def test_abbreviation_comes_from_the_title() -> None:
    assert abbreviation_of("Zakon o varstvu javnega reda in miru (ZJRM-2)") == "ZJRM-2"
    assert abbreviation_of("Kazenski zakonik (KZ-1-UPB2) (uradno prečiščeno besedilo)") == "KZ-1-UPB2"
    assert abbreviation_of("Obligacijski zakonik (uradno prečiščeno besedilo) (OZ-UPB1)") == "OZ-UPB1"
    assert abbreviation_of("Zakon o X (uradno prečiščeno besedilo) (UPB1)") is None
    assert abbreviation_of("Ustava Republike Slovenije") is None
    assert abbreviation_of("Uredba o nečem (Uradni list RS, št. 1/2000)") is None


def test_parser_rejects_foreign_pages() -> None:
    with pytest.raises(HtmlParseError, match="not an Uradni list RS act page"):
        parse_uradni_list(b"<html><body><p>Hello</p></body></html>")
    page = (
        _page("ul-2025-01-3978-zjrm-2.html.gz")
        .decode("utf-8")
        .replace("3978. Zakon o varstvu", "3977. Zakon o varstvu")
    )
    with pytest.raises(HtmlParseError, match="another act"):
        parse_uradni_list(page.encode("utf-8"))
    page = _page("ul-2025-01-3978-zjrm-2.html.gz").decode("utf-8").replace('class="content-segments"', 'class="other"')
    with pytest.raises(HtmlParseError, match="no act text"):
        parse_uradni_list(page.encode("utf-8"))


# --------------------------------------------------------------------------- connector


def _write(http: PoliteHttp, url: str, data: bytes, status: int = 200) -> None:
    path = http.cache_path(url)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    meta = {
        "url": url,
        "final_url": url,
        "status": status,
        "content_type": "text/html; charset=utf-8",
        "retrieved_at": RETRIEVED,
    }
    path.with_name(path.name + ".meta.json").write_text(json.dumps(meta))


def _cache(tmp_path: Path) -> Path:
    cache = tmp_path / "cache"
    http = PoliteHttp(cache, UA, allowed_hosts={HOST}, offline=True)
    _write(http, f"https://{HOST}/robots.txt", ROBOTS.encode())
    for name, act_id in PAGES.items():
        _write(http, f"https://{HOST}/glasilo-uradni-list-rs/vsebina/{act_id}", _page(name))
    _write(http, f"https://{HOST}/glasilo-uradni-list-rs/vsebina/2020-01-1", b"", status=404)
    _write(
        http,
        f"https://{HOST}/glasilo-uradni-list-rs/vsebina/2020-01-2",
        "<html><body>Vzdrževanje</body></html>".encode(),
    )
    _write(http, f"https://{HOST}/glasilo-uradni-list-rs/vsebina/2020-01-3", _page("ul-2025-01-3979-zpug.html.gz"))
    return cache


def _manifest(tmp_path: Path, acts: list[dict[str, Any]], **extra: Any) -> Path:
    path = tmp_path / "manifest.yaml"
    data = {
        "schema_version": 1,
        "source_id": SOURCE,
        "base_url": f"https://{HOST}",
        "user_agent": UA,
        "acts": acts,
        **extra,
    }
    path.write_text(yaml.safe_dump(data, allow_unicode=True))
    return path


def test_connector_offline_yields_pages_exactly(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    acts = [
        {"id": "2025-01-3978", "title": "Zakon o varstvu javnega reda in miru (ZJRM-2)", "issue": "112/2025"},
        {"id": "2006-01-2788"},
    ]
    connector = UradniListConnector(_manifest(tmp_path, acts), SOURCE, cache, offline=True)
    docs = list(connector.fetch())
    assert [d.external_id for d in docs] == ["si-ul:2025-01-3978", "si-ul:2006-01-2788"]
    assert docs[0].content == _page("ul-2025-01-3978-zjrm-2.html.gz")  # exactly the bytes received
    assert (
        docs[0].uri == f"https://{HOST}/glasilo-uradni-list-rs/vsebina/2025-01-3978"
        and docs[0].media_type == "text/html"
    )
    assert docs[0].retrieved_at == datetime(2026, 10, 14, 13, 0, tzinfo=UTC)
    assert docs[0].metadata["ul_id"] == "2025-01-3978" and docs[0].metadata["issue"] == "112/2025"
    assert connector.http is not None and connector.http.requests_made == 0 and connector.fetch_errors == []


def test_connector_reports_missing_foreign_and_disallowed_pages(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    acts = [{"id": i} for i in ("2020-01-1", "2020-01-2", "2020-01-3", "2025-01-3979")]
    connector = UradniListConnector(_manifest(tmp_path, acts), SOURCE, cache, offline=True)
    assert [d.external_id for d in connector.fetch()] == ["si-ul:2025-01-3979"]
    errors = "\n".join(connector.fetch_errors)
    assert len(connector.fetch_errors) == 3
    assert "2020-01-1" in errors and "404" in errors
    assert "2020-01-2" in errors and "not the page of act 2020-01-2" in errors
    assert "2020-01-3" in errors and "not the page of act 2020-01-3" in errors  # another act's page under this address
    # a path the host's robots.txt disallows is never requested
    http = PoliteHttp(cache, UA, allowed_hosts={HOST}, offline=True)
    _write(
        http, f"https://{HOST}/robots.txt", b"User-agent: *\nDisallow: /glasilo-uradni-list-rs/vsebina/2025-01-3979\n"
    )
    connector = UradniListConnector(_manifest(tmp_path, [{"id": "2025-01-3979"}]), SOURCE, cache, offline=True)
    assert list(connector.fetch()) == [] and "disallowed by robots.txt" in connector.fetch_errors[0]


def test_connector_is_offline_and_refuses_bad_manifests(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    with pytest.raises(FetchError, match="offline and not cached"):
        list(UradniListConnector(_manifest(tmp_path, [{"id": "2024-01-1234"}]), SOURCE, cache, offline=True).fetch())
    good = [{"id": "2025-01-3978"}]
    for acts, extra, message in (
        ([{"id": "2025-01-3978/../x"}], {}, "invalid act id"),
        ([{"id": "2025-1-3978"}], {}, "invalid act id"),
        ([{"id": "2025-01-3978"}, {"id": "2025-01-3978"}], {}, "listed twice"),
        (good, {"base_url": "http://www.uradni-list.si"}, "base_url"),
        (good, {"user_agent": "python-urllib"}, "user_agent"),
        (good, {"crawl_delay_seconds": 2}, "crawl_delay_seconds"),
        ([], {}, "no documents"),
    ):
        with pytest.raises(ManifestError, match=message):
            list(UradniListConnector(_manifest(tmp_path, acts, **extra), SOURCE, tmp_path).fetch())
    with pytest.raises(ManifestError, match="mismatch"):
        list(UradniListConnector(_manifest(tmp_path, good, source_id="other"), SOURCE, tmp_path).fetch())


def test_real_manifest_and_registration() -> None:
    catalog = Catalog.load(REPO_ROOT / "config")
    source = catalog.sources.get(SOURCE)
    assert isinstance(build_connector(source), UradniListConnector)
    path = REPO_ROOT / source.access.endpoint
    manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
    ids = [a["id"] for a in manifest["acts"]]
    assert len(ids) == len(set(ids)) >= 100
    assert {"2025-01-3978", "2025-01-3979"} <= set(ids)
    assert all(a["id"][:4] >= "1991" for a in manifest["acts"])
    assert manifest["crawl_delay_seconds"] >= 5 and "ULIS-research-bot" in manifest["user_agent"]
    # the connector's own validation accepts the committed manifest
    assert UradniListConnector(path, SOURCE, Path("/nonexistent"))._manifest()["acts"] == manifest["acts"]
    licence_gate(source)
    assert source.licence.review_status == "approved" and source.authority == "official_non_authentic"
    profile = catalog.jurisdictions.get("SI")
    assert profile.verification.status == "draft_unverified" and profile.official_languages == ["sl"]
    assert profile.citation.module == "si" and profile.coverage.corpus_status == "pilot"


def test_fixtures_readme_names_the_source_and_licence() -> None:
    text = (FIX / "README.md").read_text(encoding="utf-8")
    assert "uradni-list.si" in text and "Omejitve uporabe gradiv ne veljajo" in text and "2026-10-14" in text
    for name in PAGES:
        assert name in text


# --------------------------------------------------------------------------- database


class _Fixtures:
    def fetch(self) -> Iterator[RawDocument]:
        for name, act_id in PAGES.items():
            yield RawDocument(
                f"si-ul:{act_id}",
                f"https://{HOST}/glasilo-uradni-list-rs/vsebina/{act_id}",
                "text/html",
                _page(name),
                datetime.fromisoformat(RETRIEVED),
                {"fixture": name},
            )


@pytest.fixture(scope="module")
def si_ingested(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, ingested: IngestionReport
) -> IngestionReport:
    with session_factory() as session:
        report = Ingestor(session, catalog, store).ingest_source(SOURCE, actor="pytest", connector=_Fixtures())
        session.commit()
    return report


def _search(session: Session, catalog: Catalog, **kw: Any) -> dict[str, Any]:
    return search(session, catalog, SearchRequest(jurisdiction="SI", **kw), today=TODAY)


@pytest.mark.db
def test_ingest_report_counts(si_ingested: IngestionReport) -> None:
    assert si_ingested.errors == [] and si_ingested.conflicts == []
    assert si_ingested.documents_new == 3 and si_ingested.instruments_created == 3
    assert si_ingested.provision_versions_created == sum(len(_doc(n).provisions) for n in PAGES)
    assert si_ingested.passages_indexed > 100


@pytest.mark.db
def test_search_finds_exact_text_with_citation_and_limitations(
    session: Session, catalog: Catalog, si_ingested: IngestionReport
) -> None:
    res = _search(session, catalog, q="potrjevanje učbenikov delovnih učbenikov in delovnih zvezkov")
    hit = res["results"][0]
    assert hit["citation"] == "1. člen ZPUG"
    assert hit["text"].startswith(
        "S tem zakonom se ureja potrjevanje učbenikov, delovnih učbenikov in delovnih zvezkov"
    )
    assert hit["jurisdiction"]["id"] == "SI" and all(h["jurisdiction"]["id"] == "SI" for h in res["results"])
    assert hit["authority"]["category"] == "applicability_unverified"  # no start date, no end date
    codes = {lim["code"] for lim in hit["limitations"]}
    assert {"SOURCE_NOT_AUTHENTIC", "PROFILE_UNVERIFIED", "EFFECTIVE_DATE_UNKNOWN", "CURRENCY_UNKNOWN"} <= codes
    assert "OUTSTANDING_CHANGES_INSTRUMENT" in codes  # amendments published later are not applied
    assert hit["source"]["attribution"].startswith("Vir: Uradni list Republike Slovenije")
    point = _search(
        session, catalog, q="javni red in mir je stanje v katerem je zagotovljeno neovirano uresničevanje pravic"
    )
    assert point["results"][0]["citation"] == "1. točka 2. člena ZJRM-2"


@pytest.mark.db
def test_no_validity_period_means_no_point_in_time_answer(
    session: Session, catalog: Catalog, si_ingested: IngestionReport
) -> None:
    got = provision_at(session, catalog, "/si/ul/2025-01-3979/~art-1", date(2026, 3, 1))
    assert got["provision"]["citation"] == "1. člen ZPUG"
    assert got["status_on_date"] == "in_force_date_unverified"
    assert got["version"]["valid_from"] is None and got["version"]["valid_to"] is None
    assert {"EFFECTIVE_DATE_UNKNOWN", "OUTSTANDING_CHANGES_INSTRUMENT"} <= {lim["code"] for lim in got["limitations"]}


@pytest.mark.db
def test_reingest_is_idempotent_and_sources_verify(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, si_ingested: IngestionReport
) -> None:
    with session_factory() as session:
        again = Ingestor(session, catalog, store).ingest_source(SOURCE, actor="pytest", connector=_Fixtures())
        assert again.documents_unchanged == 3 and again.provision_versions_created == 0
        ids = session.scalars(select(m.SourceDocument.id).where(m.SourceDocument.source_id == SOURCE)).all()
        results = [verify_source_document(session, catalog, store, i, checker="pytest") for i in ids]
        assert len(results) == 3 and all(r["result"] == "pass" for r in results)
        assert sum(r["checked_units"] for r in results) == si_ingested.provision_versions_created
        session.rollback()

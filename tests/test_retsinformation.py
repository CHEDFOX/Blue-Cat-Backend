"""Denmark: Retsinformation lovbekendtgørelser (Lex Dania XML) - parser, citations, connector, PostgreSQL."""

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
from ulis.retrieval.lookup import provision_at
from ulis.retrieval.search import SearchRequest, search
from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.polite_http import FetchError
from ulis.sources.connectors.retsinformation import RetsinformationConnector
from ulis.sources.normalized import ParsedDocument, ParsedProvision
from ulis.sources.parsers.retsinformation import RetsinformationParseError, parse_retsinformation
from ulis.storage.object_store import LocalObjectStore
from ulis.verification.integrity import verify_source_document

FIX = Path(__file__).parent / "fixtures" / "retsinformation"
SOURCE = "dk-retsinformation"
HOST = "www.retsinformation.dk"
FVL = "/dk/lov/forvaltningsloven"
# Forvaltningsloven: three consolidations, oldest first; Aftaleloven: the current one.
INGESTED = ["lbk-2007-1365", "lbk-2012-988", "lbk-2014-433", "lbk-2016-193"]


def _raw(name: str) -> bytes:
    return gzip.decompress((FIX / f"{name}.xml.gz").read_bytes())


def _doc(name: str) -> ParsedDocument:
    (doc,) = parse_retsinformation(_raw(name))
    return doc


def _p(doc: ParsedDocument, eid: str) -> ParsedProvision:
    return next(p for p in doc.provisions if p.eid == eid)


def _cite(doc: ParsedDocument, eid: str) -> str:
    p = _p(doc, eid)
    c = ProvisionCitationInput(str(doc.short_title), "act", "DK", p.top_kind, p.num_path, "da", ext=doc.ext)
    return get_module("dk").provision_citation(c)


# --------------------------------------------------------------------------- parser


@pytest.mark.parametrize(
    "content, message",
    [
        (b'<!DOCTYPE x [<!ENTITY e "x">]><Dokument/>', "unsafe"),
        (b"<html><body>app</body></html>", "unsupported root"),
        (  # a pre-2007 document (e.g. the Grundlov of 1953) has metadata only
            b"<Dokument><Meta><DocumentType>Lov</DocumentType></Meta></Dokument>",
            "only LBK H",
        ),
    ],
)
def test_unsafe_or_unsupported_input_is_rejected(content: bytes, message: str) -> None:
    with pytest.raises(RetsinformationParseError, match=message):
        parse_retsinformation(content)


def test_current_consolidation_structure_dates_and_status() -> None:
    doc = _doc("lbk-2014-433")
    assert (doc.work_uri, doc.expression_uri) == (FVL, f"{FVL}/dan@lbk-2014-433")
    assert (doc.title, doc.short_title, doc.language) == (
        "Bekendtgørelse af forvaltningsloven",
        "Forvaltningsloven",
        "da",
    )
    assert (doc.text_origin, doc.authoritative, doc.validity_basis) == (
        "official_consolidation",
        False,
        "revision_snapshot",
    )
    assert doc.version_date == doc.currency_date == date(2014, 4, 22)  # Meta/DiesSigni ("af 22/04/2014")
    assert doc.ext == {"popular_title": "Forvaltningsloven", "document_type": "LBK H"}
    par1 = _p(doc, "par-1")
    assert not par1.is_leaf and par1.text.startswith(
        "Loven gælder for alle dele af den offentlige forvaltning.\nStk. 2."
    )
    stk2 = _p(doc, "par-1-stk-2")
    assert stk2.has_own_content and stk2.text.startswith("Loven gælder desuden for al virksomhed, der udøves af\n1) ")
    nr1 = _p(doc, "par-1-stk-2-nr-1")
    assert nr1.is_leaf and nr1.num_path == ["p:1", "stk:2", "nr:1"]
    assert nr1.text == (
        "selvejende institutioner, foreninger, fonde m.v., der er oprettet ved lov eller i henhold til lov, og"
    )
    assert nr1.context_heading == "Kapitel 1 Lovens almindelige anvendelsesområde"
    assert all(p.valid_from == date(2014, 4, 22) and p.valid_to is None for p in doc.provisions)
    assert _p(doc, "par-9a").num_path == ["p:9 a"]
    # Commencement provisions of amending acts are reproduced; the source's footnote goes to the note.
    ikraft = _p(doc, "ikraft-lov-2013-638-par-3")
    assert ikraft.text.startswith("Stk. 1. Loven træder i kraft den 1. januar 2014.")
    assert ikraft.note is not None and "Lovændringen vedrører § 1, stk. 2 og 3" in ikraft.note
    assert "Lovændringen" not in ikraft.text
    # Later amending acts of the current consolidation are outstanding (instrument-wide) and events.
    assert [c["by_uri"] for c in doc.outstanding_changes] == [
        "https://www.retsinformation.dk/eli/accn/A20180050330",
        "https://www.retsinformation.dk/eli/accn/A20240065530",
    ]
    assert all(
        c["affects_eid"] is None and "LBK nr 433 af 22/04/2014" in c["description"] for c in doc.outstanding_changes
    )
    assert [(e.event_type, e.date) for e in doc.events] == [
        ("version_change", date(2014, 4, 22)),
        ("amendment", date(2018, 5, 23)),
        ("amendment", date(2024, 6, 11)),
    ]


def test_historic_consolidation_has_the_earlier_wording_and_no_outstanding_changes() -> None:
    doc = _doc("lbk-2012-988")
    assert doc.version_date == date(2012, 10, 9) and doc.outstanding_changes == []
    assert [e.event_type for e in doc.events] == ["version_change", "amendment", "amendment"]
    assert _p(doc, "par-1-stk-2").text.startswith("Vedkommende minister kan efter forhandling med justitsministeren")
    assert not any(p.eid == "par-9a" for p in doc.provisions)


def test_repealed_paragraphs() -> None:
    doc = _doc("lbk-2016-193")
    for eid in ("par-37", "par-38"):
        p = _p(doc, eid)
        assert (p.status, p.text, p.note) == ("repealed", "", "(Ophævet)")
    assert _p(doc, "par-36-stk-1").text == (
        "En aftale kan ændres eller tilsidesættes helt eller delvis, hvis det vil være urimeligt eller i strid med "
        "redelig handlemåde at gøre den gældende.\nDet samme gælder andre retshandler."
    )


def test_citations() -> None:
    fvl, afl = _doc("lbk-2014-433"), _doc("lbk-2016-193")
    assert _cite(fvl, "par-1-stk-2-nr-1") == "§ 1, stk. 2, nr. 1, i forvaltningsloven"
    assert _cite(fvl, "par-9a") == "§ 9 a i forvaltningsloven"
    assert _cite(fvl, "ikraft-lov-2013-638-par-3") == "§ 3 i lov nr. 638 af 12. juni 2013"
    assert _cite(afl, "par-36-stk-1") == "§ 36, stk. 1, i aftaleloven"
    module = get_module("dk")
    litra = ProvisionCitationInput("Straffeloven", "act", "DK", "paragraph", ["p:81", "nr:1", "litra:a"], "da")
    assert module.provision_citation(litra) == "§ 81, nr. 1, litra a, i straffeloven"
    span = ProvisionCitationInput("Straffeloven", "act", "DK", "paragraph", ["p:28-30"], "da")
    assert module.provision_citation(span) == "§§ 28-30 i straffeloven"


# --------------------------------------------------------------------------- connector


def _cache(dest: Path, names: dict[str, str]) -> Path:
    for eli, name in names.items():
        path = dest / HOST / "eli" / eli / "xml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_raw(name) if name else b"<!doctype html><html><div id='root'></div></html>")
        meta = {"url": f"https://{HOST}/eli/{eli}/xml", "status": 200, "retrieved_at": "2026-10-14T10:55:00+00:00"}
        path.with_name("xml.meta.json").write_text(json.dumps(meta))
    return dest


def _manifest(tmp_path: Path, works: list[dict[str, Any]], **extra: Any) -> Path:
    path = tmp_path / "manifest.yaml"
    data = {
        "schema_version": 1,
        "source_id": SOURCE,
        "user_agent": "ULIS-test (+https://example.org)",
        "works": works,
        **extra,
    }
    path.write_text(yaml.safe_dump(data, allow_unicode=True))
    return path


def test_connector_offline_from_cache(tmp_path: Path) -> None:
    cache = _cache(tmp_path / "cache", {"lta/2012/988": "lbk-2012-988", "lta/2014/433": "lbk-2014-433"})
    manifest = _manifest(tmp_path, [{"name": "Forvaltningsloven", "consolidations": ["lta/2012/988", "lta/2014/433"]}])
    docs = list(RetsinformationConnector(manifest, SOURCE, cache, offline=True).fetch())
    assert [d.external_id for d in docs] == ["retsinformation:lta/2012/988", "retsinformation:lta/2014/433"]
    assert docs[1].content == _raw("lbk-2014-433")  # exact bytes as received
    assert docs[1].uri == "https://www.retsinformation.dk/eli/lta/2014/433/xml"
    assert docs[1].metadata["eli"] == "https://www.retsinformation.dk/eli/lta/2014/433"
    with pytest.raises(FetchError, match="offline and not cached"):
        list(
            RetsinformationConnector(
                _manifest(tmp_path, [{"name": "X", "consolidations": ["lta/2016/193"]}]), SOURCE, cache, offline=True
            ).fetch()
        )


def test_connector_refuses_bad_manifests_and_html(tmp_path: Path) -> None:
    for works, extra, message in (
        ([{"name": "X", "consolidations": ["../etc"]}], {}, "invalid work"),
        ([{"name": "X", "consolidations": []}], {}, "invalid work"),
        ([{"name": "X", "consolidations": ["lta/2014/433"]}], {"crawl_delay_seconds": 1}, "at least 10"),
        (
            [{"name": "X", "consolidations": ["lta/2014/433"]}],
            {"base_url": "http://www.retsinformation.dk/eli"},
            "base_url",
        ),
        ([{"name": "X", "consolidations": ["lta/2014/433"]}], {"user_agent": "python-urllib"}, "user_agent"),
    ):
        with pytest.raises(ManifestError, match=message):
            list(RetsinformationConnector(_manifest(tmp_path, works, **extra), SOURCE, tmp_path).fetch())
    cache = _cache(tmp_path / "cache", {"lta/1999/1": ""})  # the website's HTML shell, served for unknown paths
    with pytest.raises(FetchError, match="not a Lex Dania XML document"):
        list(
            RetsinformationConnector(
                _manifest(tmp_path, [{"name": "X", "consolidations": ["lta/1999/1"]}]), SOURCE, cache, offline=True
            ).fetch()
        )


def test_source_uses_this_connector(catalog: Catalog) -> None:
    assert isinstance(build_connector(catalog.sources.get(SOURCE)), RetsinformationConnector)


# --------------------------------------------------------------------------- PostgreSQL


class _Fixtures:
    def fetch(self) -> Iterator[RawDocument]:
        for name in INGESTED:
            _, year, number = name.split("-")
            yield RawDocument(
                f"retsinformation:lta/{year}/{number}",
                f"https://{HOST}/eli/lta/{year}/{number}/xml",
                "application/xml",
                _raw(name),
                datetime(2026, 10, 14, 10, 55, tzinfo=UTC),
                {},
            )


@pytest.fixture(scope="module")
def dk_ingested(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, ingested: IngestionReport
) -> IngestionReport:
    with session_factory() as session:
        report = Ingestor(session, catalog, store).ingest_source(SOURCE, actor="pytest", connector=_Fixtures())
        session.commit()
    return report


def _search(session: Session, catalog: Catalog, **kw: Any) -> dict[str, Any]:
    return search(session, catalog, SearchRequest(jurisdiction="DK", **kw), today=date(2026, 10, 14))


@pytest.mark.db
def test_search_finds_exact_text_with_citation_and_limitations(
    session: Session, catalog: Catalog, dk_ingested: IngestionReport
) -> None:
    assert dk_ingested.errors == [] and dk_ingested.conflicts == []
    assert dk_ingested.instruments_created == 2 and dk_ingested.instrument_versions_created == 4
    res = _search(session, catalog, q="aftale tilsidesættes urimeligt redelig handlemåde", as_of=date(2026, 10, 1))
    hit = res["results"][0]
    assert hit["citation"] == "§ 36, stk. 1, i aftaleloven"
    assert hit["text"].startswith("En aftale kan ændres eller tilsidesættes helt eller delvis")
    codes = {lim["code"] for lim in hit["limitations"]}
    assert {
        "PROFILE_UNVERIFIED",
        "OFFICIAL_CONSOLIDATION_REPRODUCED",
        "SOURCE_NOT_AUTHENTIC",
        "SNAPSHOT_DATES",
        "OUTSTANDING_CHANGES_INSTRUMENT",
        "AS_OF_AFTER_CURRENCY",
    } <= codes
    assert hit["source"]["attribution"].startswith("Kilde: Retsinformation")
    fvl = _search(session, catalog, q="selvejende institutioner oprettet ved lov", as_of=date(2026, 10, 1))
    # The stykke (its own text plus its items) and the item itself are both found.
    assert {"§ 1, stk. 2, i forvaltningsloven", "§ 1, stk. 2, nr. 1, i forvaltningsloven"} <= {
        h["citation"] for h in fvl["results"][:3]
    }


@pytest.mark.db
def test_point_in_time_follows_the_consolidations(
    session: Session, catalog: Catalog, dk_ingested: IngestionReport
) -> None:
    stk2 = f"{FVL}/~par-1-stk-2"
    old = provision_at(session, catalog, stk2, date(2013, 6, 1))
    new = provision_at(session, catalog, stk2, date(2015, 1, 1))
    assert old["status_on_date"] == new["status_on_date"] == "in_force"
    assert old["version"]["text"].startswith("Vedkommende minister kan")
    assert new["version"]["text"].startswith("Loven gælder desuden for al virksomhed")
    # Dated by the consolidations: wording of LBK 1365/2007 continued in 988/2012, replaced by 433/2014.
    assert (old["version"]["valid_from"], old["version"]["valid_to"], old["version"]["valid_to_basis"]) == (
        "2007-12-07",
        "2014-04-22",
        "derived",
    )
    assert new["provision"]["citation"] == "§ 1, stk. 2, i forvaltningsloven"
    # A stykke absent from the next consolidation ends on its date; § 9 a first appears in 2014.
    gone = provision_at(session, catalog, f"{FVL}/~par-28-stk-6", date(2010, 1, 1))
    assert (gone["version"]["valid_to"], gone["version"]["valid_to_basis"]) == ("2012-10-09", "explicit")
    assert provision_at(session, catalog, f"{FVL}/~par-28-stk-6", date(2013, 1, 1))["version"] is None
    assert provision_at(session, catalog, f"{FVL}/~par-9a", date(2013, 1, 1))["status_on_date"] == "not_yet_in_force"
    assert provision_at(session, catalog, stk2, date(2000, 1, 1))["status_on_date"] == "history_not_indexed"
    repealed = provision_at(session, catalog, "/dk/lov/aftaleloven/~par-37", date(2026, 1, 1))
    assert repealed["status_on_date"] == "repealed"


@pytest.mark.db
def test_reingest_is_idempotent_and_sources_verify(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, dk_ingested: IngestionReport
) -> None:
    with session_factory() as session:
        again = Ingestor(session, catalog, store).ingest_source(SOURCE, actor="pytest", connector=_Fixtures())
        assert again.documents_unchanged == len(INGESTED) and again.provision_versions_created == 0
        ids = session.scalars(select(m.SourceDocument.id).where(m.SourceDocument.source_id == SOURCE)).all()
        results = [verify_source_document(session, catalog, store, i, checker="pytest") for i in ids]
        assert len(results) == len(INGESTED) and all(r["result"] == "pass" for r in results)

"""Federal Register of Legislation parser, connector and Australian search behaviour.

Real Commonwealth compilations (CC BY 4.0), retrieved from the Register's API on 2026-10-14.
"""

from __future__ import annotations

import base64
import gzip
import io
import json
import shutil
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ulis.catalog import Catalog
from ulis.db import models as m
from ulis.ingestion.pipeline import IngestionReport, Ingestor, resolve_uri
from ulis.jurisdictions.modules import ProvisionCitationInput, get_module
from ulis.retrieval.lookup import provision_at
from ulis.retrieval.search import SearchRequest, search
from ulis.settings import REPO_ROOT
from ulis.sources.connectors.au_federal_register import FederalRegisterConnector
from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.polite_http import FetchError
from ulis.sources.parsers.au_federal_register import (
    FrlParseError,
    label_key,
    parse_federal_register,
    parse_with_report,
    written_date,
)
from ulis.storage.object_store import LocalObjectStore
from ulis.verification.integrity import verify_source_document

FIX = REPO_ROOT / "tests" / "fixtures" / "au_federal_register" / "cache"
API = FIX / "api.prod.legislation.gov.au"
CONSTITUTION = API / "C2004Q00685"
AIA = API / "C1901A00002" / "2026-03-28_r0.json"
PRIVACY = API / "C2004A03712" / "2026-06-04_r0.json"


def _read(path: Path) -> bytes:
    """Fixtures are stored gzip-compressed (unmodified API responses)."""
    return gzip.decompress(path.with_name(path.name + ".gz").read_bytes())


def _doc(path: Path):  # type: ignore[no-untyped-def]
    docs = parse_federal_register(_read(path))
    assert len(docs) == 1
    return docs[0]


def _by_eid(doc) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    return {p.eid: p for p in doc.provisions}


def _cite(doc, eid: str) -> str:  # type: ignore[no-untyped-def]
    p = _by_eid(doc)[eid]
    return get_module("au").provision_citation(
        ProvisionCitationInput(
            doc.title, doc.instrument_type, "AU", p.top_kind, p.num_path, "en", ext=doc.ext, eid=p.eid
        )
    )


def test_current_compilation() -> None:
    doc = _doc(AIA)
    assert (doc.jurisdiction_id, doc.instrument_type, doc.work_uri) == ("AU", "act", "/au/act/C1901A00002")
    assert doc.expression_uri == "/au/act/C1901A00002/eng@2026-03-28"
    assert doc.text_origin == "official_consolidation" and doc.validity_basis == "revision_snapshot"
    assert (doc.version_date, doc.currency_date) == (date(2026, 3, 28), date(2026, 3, 28))
    assert doc.work_date == date(1901, 7, 12)  # assent, from the legislation history endnote
    assert doc.ext == {
        "title_id": "C1901A00002",
        "collection": "Act",
        "act_number": "No. 2, 1901",
        "code_title": None,
    }
    p = _by_eid(doc)
    # Every provision carries the compilation's period (the source does not date provisions).
    assert {(x.valid_from, x.valid_to, x.status) for x in doc.provisions} == {(date(2026, 3, 28), None, "in_force")}
    # An in-text note is part of the Act (s 13(1)) and stays with its subsection, labelled as a note.
    assert p["section-2-1"].text == (
        "This Act applies to all Acts (including this Act).\nNote: This Act also applies to legislative "
        "instruments, notifiable instruments and other instruments: see subsection 13(1) of the Legislation "
        "Act 2003 and subsection 46(1) of this Act."
    )
    assert p["section-2"].text.startswith("(1) This Act applies to all Acts") and not p["section-2"].is_leaf
    assert p["section-2B-def-administrative-arrangements-order-b-ii"].text == (
        "revokes a primary AAO and substitutes a new primary AAO."
    )
    # Unnumbered text of a section (chapeau and closing words) is the section's own text.
    assert p["section-15AC"].has_own_content and p["section-15AC"].text.startswith("Where:\n(a) an Act")
    # Nothing from the front matter or the endnotes is law text.
    assert doc.provisions[0].eid == "long-title" and doc.provisions[-1].eid == "section-51-b"
    assert not any("Endnote" in x.text or "About this compilation" in x.text for x in doc.provisions)


def test_citations_in_aglc_style() -> None:
    aia, privacy = _doc(AIA), _doc(PRIVACY)
    constitution = _doc(CONSTITUTION / "1977-07-29_r1.json")
    assert _cite(aia, "section-15AB-1") == "Acts Interpretation Act 1901 (Cth) s 15AB(1)"
    assert (
        _cite(aia, "section-2B-def-administrative-arrangements-order-b-ii")
        == "Acts Interpretation Act 1901 (Cth) s 2B (definition of ‘Administrative Arrangements Order’ para (b)(ii))"
    )
    assert _cite(privacy, "section-6-1-def-agency") == "Privacy Act 1988 (Cth) s 6(1) (definition of ‘agency’)"
    assert _cite(privacy, "schedule-1-clause-1-1.2-a") == "Privacy Act 1988 (Cth) sch 1 cl 1.2(a)"
    assert _cite(privacy, "schedule-2-clause-1") == "Privacy Act 1988 (Cth) sch 2 cl 1"
    assert _cite(privacy, "preamble") == "Privacy Act 1988 (Cth) Preamble"
    assert _cite(constitution, "section-51-xx") == "Australian Constitution s 51(xx)"
    assert _cite(constitution, "covering-clause-5") == "Commonwealth of Australia Constitution Act 1900 (Imp) s 5"
    assert _cite(constitution, "schedule") == "Australian Constitution sch"
    # A schedule enacted as a code (Criminal Code Act 1995) is cited by the code's own name.
    module = get_module("au")
    ext = {"title_id": "C2004A04868", "collection": "Act", "code_title": "Criminal Code"}

    def code(top_kind: str, path: list[str]) -> str:
        return module.provision_citation(
            ProvisionCitationInput("Criminal Code Act 1995", "act", "AU", top_kind, path, "en", ext=ext)
        )

    assert code("code_section", ["11.1", "(1)"]) == "Criminal Code (Cth) s 11.1(1)"
    assert code("code_dictionary", ["def:ABN"]) == "Criminal Code (Cth) Dictionary (definition of ‘ABN’)"
    assert code("section", ["3", "(2)"]) == "Criminal Code Act 1995 (Cth) s 3(2)"


def test_privacy_act_schedule_preamble_and_outstanding_version() -> None:
    doc = _doc(PRIVACY)
    p = _by_eid(doc)
    assert p["preamble"].text.startswith("WHEREAS Australia is a party to the International Covenant")
    assert p["schedule-1-clause-1-1.2"].heading == "Compliance with the Australian Privacy Principles etc."
    assert p["schedule-1-clause-1-1.2-a"].text.startswith("will ensure that the entity complies")
    # The Register lists a later version (from 2026-12-10) with no compilation: the period ends there.
    assert {x.valid_to for x in doc.provisions} == {date(2026, 12, 10)}
    assert [c["by_title"] for c in doc.outstanding_changes] == ["Version from 2026-12-10"]
    # A formula image is not reproduced; the provision says so in its note.
    assert "formula or image" in (p["section-6DA-2"].note or "")


def test_constitution_compilations() -> None:
    before = _by_eid(_doc(CONSTITUTION / "1946-12-19_r1.json"))
    after = _by_eid(_doc(CONSTITUTION / "1967-08-10_r1.json"))
    assert "other than the aboriginal race in any State" in before["section-51-xxvi"].text
    assert (
        after["section-51-xxvi"].text == "The people of any race for whom it is deemed necessary to make special laws:"
    )
    assert "section-127" in before and "section-127" not in after  # repealed provisions are omitted
    assert (before["section-51-xxvi"].valid_from, before["section-51-xxvi"].valid_to) == (
        date(1946, 12, 19),
        date(1967, 8, 10),
    )
    assert before["covering-clause-9"].top_kind == "covering_clause"
    assert before["section-51-xxvi"].num_path == ["51", "(xxvi.)"]


def test_older_reprint_format() -> None:
    """Reprints registered for 1997-2003 use h1-h6 headings and other paragraph style names."""
    docs, report = parse_with_report(_read(API / "C2004A02944" / "1998-07-01_r0.json"))
    doc = docs[0]
    p = _by_eid(doc)
    assert (doc.version_date, doc.work_date) == (date(1998, 7, 1), date(1984, 6, 25))
    assert {x.valid_to for x in doc.provisions} == {date(1999, 7, 1)}
    # "1  Short title [see Note 1]": the pointer to the endnotes is editorial, not part of the heading.
    assert p["section-1"].heading == "Short title"
    assert p["section-1"].text == "This Act may be cited as the Insurance Contracts Act 1984."
    assert (p["section-9-1-a"].kind, p["section-9-1-a"].text) == ("paragraph", "of reinsurance;")
    assert p["section-9-1-e-ii"].kind == "subparagraph"
    # Template placeholders under Part headings ("Do not delete : Division placeholder") are not text.
    assert report.blocks_outside_provisions == {"unstyled": 9}
    assert not any("placeholder" in x.text or "Table of Acts" in x.text for x in doc.provisions)
    assert doc.provisions[-1].eid == "section-78-c"


def test_rejects_unsupported_input() -> None:
    with pytest.raises(FrlParseError, match="JSON"):
        parse_federal_register(b"<html/>")
    data = json.loads(_read(AIA))
    with pytest.raises(FrlParseError, match="EPUB"):
        parse_federal_register(json.dumps({**data, "Extension": ".docx"}).encode())
    with pytest.raises(FrlParseError, match="as made"):
        parse_federal_register(json.dumps({**data, "CompilationNumber": "0"}).encode())
    with pytest.raises(FrlParseError, match="version metadata"):
        parse_federal_register(json.dumps({**data, "Version": None}).encode())
    # Any document type declaration other than XHTML 1.1's is refused (no DTD processing).
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr(
            "META-INF/container.xml",
            '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
            '<rootfile full-path="OEBPS/d.opf"/></rootfiles></container>',
        )
        z.writestr(
            "OEBPS/d.opf",
            '<package xmlns="http://www.idpf.org/2007/opf"><manifest><item id="a" href="a.html"/></manifest>'
            '<spine><itemref idref="a"/></spine></package>',
        )
        z.writestr("OEBPS/a.html", '<!DOCTYPE x [<!ENTITY e "boom">]><html xmlns="http://www.w3.org/1999/xhtml"/>')
    evil = {**data, "Bytes": base64.b64encode(buf.getvalue()).decode()}
    with pytest.raises(FrlParseError, match="unsafe XHTML"):
        parse_federal_register(json.dumps(evil).encode())


def test_helpers() -> None:
    assert written_date("12 July 1901") == date(1901, 7, 12)
    assert written_date("15 Mar 1995") == date(1995, 3, 15)
    assert written_date("1 Jan 1997 (s 2(1))") is None
    assert [label_key(x) for x in ("(1A)", "(xx.)", "1.2", "51.")] == ["1A", "xx", "1.2", "51"]
    assert resolve_uri("https://www.legislation.gov.au/C2004A03712/latest/text") == ("/au/act/C2004A03712", None)
    assert resolve_uri("https://www.legislation.gov.au/C2004Q00685") == ("/au/constitution/C2004Q00685", None)
    _, report = parse_with_report(_read(PRIVACY))
    assert report.images == 1 and sum(report.blocks_outside_provisions.values()) == 2


# --------------------------------------------------------------------------- connector


def _cache_copy(dest: Path) -> Path:
    """Copy the fixture cache and decompress it into the connector's cache layout."""
    shutil.copytree(FIX, dest)
    for gz in dest.rglob("*.json.gz"):
        gz.with_suffix("").write_bytes(gzip.decompress(gz.read_bytes()))
        gz.unlink()
    return dest


def _connector(cache: Path, tmp_path: Path, works: list[dict[str, Any]], **kw: Any) -> FederalRegisterConnector:
    manifest = {
        "schema_version": 1,
        "source_id": "au-federal-register",
        "api_base": "https://api.prod.legislation.gov.au/v1",
        "user_agent": "ULIS-test (+https://example.org)",
        "crawl_delay_seconds": 10,
        "works": works,
    }
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(manifest))
    return FederalRegisterConnector(path, "au-federal-register", cache, **kw)


CONSTITUTION_WORK = {"title_id": "C2004Q00685", "history": True, "history_since": "1946-12-19"}
PRIVACY_WORK = {"title_id": "C2004A03712"}
# Committed to the shared test database. The Privacy Act is ingested only inside a rolled-back
# transaction: its many "privacy" passages would crowd other jurisdictions out of other tests.
SHARED_WORKS = [CONSTITUTION_WORK, {"title_id": "C1901A00002"}]
WORKS = [*SHARED_WORKS, PRIVACY_WORK]


def test_connector_offline_from_cache(tmp_path: Path) -> None:
    conn = _connector(_cache_copy(tmp_path / "cache"), tmp_path, WORKS, offline=True)
    docs = list(conn.fetch())
    assert [d.external_id for d in docs] == [
        "frl:C2004Q00685@1946-12-19#r1",
        "frl:C2004Q00685@1967-08-10#r1",
        "frl:C2004Q00685@1977-07-29#r1",
        "frl:C1901A00002@2026-03-28#r0",
        "frl:C2004A03712@2026-06-04#r0",
    ]
    assert all(d.uri and d.uri.startswith("https://api.prod.legislation.gov.au/v1/documents(") for d in docs)
    assert docs[1].metadata["end"] == "1977-07-29" and docs[1].metadata["register_id"] == "C2004C00469"
    meta = json.loads(
        (tmp_path / "cache/api.prod.legislation.gov.au/C1901A00002/2026-03-28_r0.json.meta.json").read_text()
    )
    assert docs[3].retrieved_at.isoformat() == meta["retrieved_at"]


def test_connector_selection_and_manifest_checks(tmp_path: Path) -> None:
    conn = _connector(tmp_path, tmp_path, [{"title_id": "C1901A00002"}], offline=True)
    with pytest.raises(FetchError, match="offline and not cached"):
        list(conn.fetch())
    versions = [
        {"start": "1901-07-12T00:00:00", "registerId": "C1901A00002", "compilationNumber": "0"},
        {"start": "1950-01-01T00:00:00", "registerId": None, "compilationNumber": None},
        {"start": "1960-01-01T00:00:00", "registerId": "C2024C00001", "compilationNumber": "1"},
        {"start": "1970-01-01T00:00:00", "registerId": "C2024C00002", "compilationNumber": "2", "isLatest": True},
    ]
    documents = [
        {"start": "1960-01-01T00:00:00", "rectificationVersionNumber": 0, "volumeNumber": 0},
        {"start": "1960-01-01T00:00:00", "rectificationVersionNumber": 2, "volumeNumber": 0},
        {"start": "1970-01-01T00:00:00", "rectificationVersionNumber": 0, "volumeNumber": 1},
    ]
    chosen = conn.select("C1901A00002", versions, documents, history=True)
    assert [(v["start"][:10], d["rectificationVersionNumber"]) for v, d in chosen] == [("1960-01-01", 2)]
    assert [reason for _, _, reason in conn.skipped] == [
        "as made (not a compilation)",
        "no registered compilation for this version",
        "multi-volume compilation (not supported)",
    ]
    for bad in (
        {"user_agent": "python-urllib"},
        {"api_base": "https://example.org/v1"},
        {"works": [{"title_id": "x"}]},
    ):
        manifest = yaml.safe_load((tmp_path / "manifest.yaml").read_text()) | bad
        (tmp_path / "bad.yaml").write_text(yaml.safe_dump(manifest))
        with pytest.raises(ManifestError):
            list(FederalRegisterConnector(tmp_path / "bad.yaml", "au-federal-register", tmp_path).fetch())


# --------------------------------------------------------------------------- PostgreSQL


@pytest.fixture(scope="module")
def au_ingested(
    session_factory: sessionmaker[Session],
    catalog: Catalog,
    store: LocalObjectStore,
    ingested: IngestionReport,
    tmp_path_factory: pytest.TempPathFactory,
) -> IngestionReport:
    tmp = tmp_path_factory.mktemp("au")
    connector = _connector(_cache_copy(tmp / "cache"), tmp, SHARED_WORKS, offline=True)
    with session_factory() as session:
        report = Ingestor(session, catalog, store).ingest_source(
            "au-federal-register", actor="pytest", connector=connector
        )
        session.commit()
    return report


def _codes(limitations: list[dict[str, str]]) -> dict[str, str]:
    return {lim["code"]: lim["severity"] for lim in limitations}


@pytest.mark.db
def test_au_search_citation_and_limitations(session: Session, catalog: Catalog, au_ingested: IngestionReport) -> None:
    assert au_ingested.errors == [] and au_ingested.conflicts == []
    res = search(
        session,
        catalog,
        SearchRequest(
            q="capable of assisting in the ascertainment of the meaning", jurisdiction="AU", as_of=date(2026, 7, 1)
        ),
        today=date(2026, 10, 14),
    )
    hit = next(h for h in res["results"] if h["citation"] == "Acts Interpretation Act 1901 (Cth) s 15AB(1)")
    codes = _codes(hit["limitations"])
    assert {"OFFICIAL_CONSOLIDATION_REPRODUCED", "PROFILE_UNVERIFIED", "SOURCE_NOT_AUTHENTIC"} <= set(codes)
    # Only the 2026-03-28 compilation is indexed: earlier application is possible but not shown.
    assert codes["SNAPSHOT_DATES"] == "warning"
    assert "NOT_AUTHENTIC_TEXT" not in codes
    assert hit["source"]["attribution"].startswith("Based on content from the Federal Register of Legislation")


@pytest.mark.db
def test_privacy_act_period_ends_at_the_next_uncompiled_version(
    session_factory: sessionmaker[Session],
    catalog: Catalog,
    store: LocalObjectStore,
    au_ingested: IngestionReport,
    tmp_path: Path,
) -> None:
    connector = _connector(_cache_copy(tmp_path / "cache"), tmp_path, [PRIVACY_WORK], offline=True)
    with session_factory() as session:
        report = Ingestor(session, catalog, store).ingest_source(
            "au-federal-register", actor="pytest", connector=connector
        )
        assert report.errors == [] and report.conflicts == []

        def hits(as_of: date) -> list[dict[str, Any]]:
            res = search(
                session,
                catalog,
                SearchRequest(
                    q="open and transparent management of personal information", jurisdiction="AU", as_of=as_of
                ),
                today=date(2026, 10, 14),
            )
            return [h for h in res["results"] if h["document"]["uri"] == "/au/act/C2004A03712"]

        now = hits(date(2026, 10, 1))
        app = next(h for h in now if h["citation"] == "Privacy Act 1988 (Cth) sch 1 cl 1.1")
        assert app["text"].startswith("The object of this principle is to ensure that APP entities manage")
        assert "OUTSTANDING_CHANGES_INSTRUMENT" in _codes(app["limitations"])
        # The compilation ends on 2026-12-10, when a later version with no compilation starts.
        assert hits(date(2027, 1, 1)) == []
        session.rollback()


@pytest.mark.db
def test_constitution_history_joins_unchanged_wording(
    session: Session, catalog: Catalog, au_ingested: IngestionReport
) -> None:
    uri = "/au/constitution/C2004Q00685/~section-51-xxvi"
    old = provision_at(session, catalog, uri, date(1950, 1, 1))
    new = provision_at(session, catalog, uri, date(1990, 1, 1))
    assert old["provision"]["citation"] == "Australian Constitution s 51(xxvi)"
    assert old["status_on_date"] == new["status_on_date"] == "in_force"
    assert "other than the aboriginal race" in old["version"]["text"]
    assert (old["version"]["valid_from"], old["version"]["valid_to"]) == ("1946-12-19", "1967-08-10")
    assert (new["version"]["valid_from"], new["version"]["valid_to"]) == ("1967-08-10", None)
    assert _codes(new["limitations"])["SNAPSHOT_DATES"] == "info"  # the previous compilation differed
    assert _codes(old["limitations"])["SNAPSHOT_DATES"] == "warning"  # earliest indexed compilation
    # Unchanged across three compilations: one version, not three.
    same = provision_at(session, catalog, "/au/constitution/C2004Q00685/~section-51-xx", date(1950, 1, 1))
    assert [(t["valid_from"], t["valid_to"]) for t in same["timeline"]] == [("1946-12-19", None)]
    # Omitted from the 1967 compilation (repealed): no version after 1967-08-10, none claimed earlier.
    s127 = "/au/constitution/C2004Q00685/~section-127"
    assert provision_at(session, catalog, s127, date(1960, 1, 1))["status_on_date"] == "in_force"
    assert provision_at(session, catalog, s127, date(1970, 1, 1))["status_on_date"] == "no_version"
    assert provision_at(session, catalog, uri, date(1930, 1, 1))["status_on_date"] == "history_not_indexed"


@pytest.mark.db
def test_au_sources_verify_and_reingest_is_idempotent(
    session_factory: sessionmaker[Session],
    catalog: Catalog,
    store: LocalObjectStore,
    au_ingested: IngestionReport,
    tmp_path: Path,
) -> None:
    with session_factory() as session:
        ids = session.scalars(
            select(m.SourceDocument.id).where(m.SourceDocument.source_id == "au-federal-register")
        ).all()
        results = [verify_source_document(session, catalog, store, i) for i in ids]
        session.rollback()
    assert len(results) == 4 and all(r["result"] == "pass" for r in results)
    connector = _connector(_cache_copy(tmp_path / "cache"), tmp_path, SHARED_WORKS, offline=True)
    with session_factory() as session:
        again = Ingestor(session, catalog, store).ingest_source(
            "au-federal-register", actor="pytest", connector=connector
        )
        session.rollback()
    assert again.documents_unchanged == 4 and again.provision_versions_created == 0


def _relabelled(name: str, title_id: str, **version: Any) -> RawDocument:
    """A fixture compilation under another (unused) title id, to test ingestion orders in isolation."""
    data = json.loads(_read(CONSTITUTION / name))
    data["TitleId"] = title_id
    data["Version"] = {**data["Version"], "TitleId": title_id, **version}
    now = datetime(2026, 10, 14, tzinfo=UTC)
    return RawDocument(f"test:{title_id}:{name}", None, "application/json", json.dumps(data).encode(), now, {})


class _Docs:
    def __init__(self, docs: list[RawDocument]):
        self.docs = docs

    def fetch(self):  # type: ignore[no-untyped-def]
        yield from self.docs


@pytest.mark.db
def test_compilation_snapshots_order_gaps_and_refresh(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, ingested: IngestionReport
) -> None:
    """Compilations are whole-instrument snapshots with stated end dates (``ulis.ingestion.snapshots``)."""
    names = ["1946-12-19_r1.json", "1967-08-10_r1.json", "1977-07-29_r1.json"]
    # Newest first: older snapshots are refused (they could split a recorded version).
    backwards = [_relabelled(n, "C1900A99998") for n in reversed(names)]
    # A gap: the first compilation is said to end in 1960, seven years before the next one starts.
    gap = [_relabelled(names[0], "C1900A99997", End="1960-01-01T00:00:00"), _relabelled(names[1], "C1900A99997")]
    # A compilation fetched while it was the latest (no end date), then the next one.
    refreshed = [
        _relabelled(names[1], "C1900A99999", End=None, IsLatest=True),
        _relabelled(names[2], "C1900A99999"),
    ]
    with session_factory() as session:
        reports = [
            Ingestor(session, catalog, store).ingest_source("au-federal-register", actor="pytest", connector=_Docs(d))
            for d in (backwards, gap, refreshed)
        ]
        assert len(reports[0].errors) == 2 and all("oldest first" in e for e in reports[0].errors)
        assert reports[1].errors == reports[2].errors == [] and not any(r.conflicts for r in reports)

        def periods(title_id: str, eid: str) -> list[tuple[str | None, str | None, str]]:
            data = provision_at(session, catalog, f"/au/act/{title_id}/~{eid}", date(1980, 1, 1))
            return [(t["valid_from"], t["valid_to"], t["valid_to_basis"]) for t in data["timeline"]]

        # Unchanged wording does not continue across the gap: what applied in between is unknown.
        assert periods("C1900A99997", "section-51-xx") == [
            ("1946-12-19", "1960-01-01", "explicit"),
            ("1967-08-10", "1977-07-29", "explicit"),
        ]
        in_gap = provision_at(session, catalog, "/au/act/C1900A99997/~section-51-xx", date(1963, 1, 1))
        assert in_gap["status_on_date"] == "no_version"
        assert periods("C1900A99999", "section-51-xx") == [("1967-08-10", None, "open")]
        # s 15 changed in 1977: the open-ended 1967 wording now ends where the next one starts.
        assert periods("C1900A99999", "section-15") == [
            ("1967-08-10", "1977-07-29", "derived"),
            ("1977-07-29", None, "open"),
        ]
        session.rollback()

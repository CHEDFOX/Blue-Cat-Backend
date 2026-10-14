"""Gesetze im Internet: parser, connector and German search behaviour (real federal law, § 5 UrhG)."""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import zipfile
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
from ulis.ingestion.pipeline import resolve_uri as pipeline_resolve_uri
from ulis.jurisdictions.modules import ProvisionCitationInput, get_module
from ulis.retrieval.lookup import provision_at
from ulis.retrieval.search import SearchRequest, search
from ulis.settings import REPO_ROOT
from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.gesetze_im_internet import GesetzeImInternetConnector
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.polite_http import FetchError
from ulis.sources.normalized import ParsedDocument, ParsedProvision, ParseSkipped
from ulis.sources.parsers.gesetze_im_internet import (
    GiiParseError,
    gii_slug,
    parse_gesetze_im_internet,
    resolve_uri,
)
from ulis.storage.object_store import LocalObjectStore
from ulis.verification.integrity import verify_source_document

FIX = REPO_ROOT / "tests" / "fixtures" / "gesetze_im_internet"
CACHE = FIX / "cache"
LAWS = ("gg", "kschg", "stgb", "gewstg")
SOURCE = "de-gesetze-im-internet"


def _zip(abbr: str) -> Path:
    return CACHE / "www.gesetze-im-internet.de" / abbr / "xml.zip"


def _doc(abbr: str) -> ParsedDocument:
    docs = parse_gesetze_im_internet(_zip(abbr).read_bytes())
    assert len(docs) == 1
    return docs[0]


def _p(doc: ParsedDocument, eid: str) -> ParsedProvision:
    return {p.eid: p for p in doc.provisions}[eid]


def _cite(doc: ParsedDocument, eid: str) -> str:
    p = _p(doc, eid)
    return get_module("de").provision_citation(
        ProvisionCitationInput(
            doc.short_title or doc.title,
            doc.instrument_type,
            "DE",
            p.top_kind,
            p.num_path,
            "de",
            ext=doc.ext,
            eid=p.eid,
        )
    )


def _gii(body: str, stand: str = "", builddate: str = "20260101120000", doctype: str = "") -> bytes:
    """A minimal gii-norm document (synthetic; used only for edge cases absent from the fixtures)."""
    return (
        f'<?xml version="1.0" encoding="UTF-8" ?>{doctype}'
        f'<dokumente builddate="{builddate}" doknr="BJNRTEST"><norm doknr="BJNRTEST"><metadaten>'
        f"<jurabk>TestG</jurabk><langue>Testgesetz</langue>{stand}</metadaten></norm>{body}</dokumente>"
    ).encode()


# --------------------------------------------------------------------------- XML safety


def test_publisher_bare_doctype_is_accepted() -> None:
    raw = zipfile.ZipFile(_zip("kschg")).read("BJNR004990951.xml")
    assert b'<!DOCTYPE dokumente SYSTEM "http://www.gesetze-im-internet.de/dtd/1.01/gii-norm.dtd">' in raw[:300]
    assert parse_gesetze_im_internet(raw)[0].ext["jurabk"] == "KSchG"  # the XML itself, outside its zip


@pytest.mark.parametrize(
    "doctype",
    [
        # internal subset declaring an entity (the bare-DOCTYPE strip does not apply; DTDs are forbidden)
        '<!DOCTYPE dokumente SYSTEM "gii-norm.dtd" [<!ENTITY e "x">]>',
        # billion laughs
        '<!DOCTYPE dokumente [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;&a;"><!ENTITY c "&b;&b;&b;&b;">]>',
        # external entity
        '<!DOCTYPE dokumente [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>',
        # another document type
        '<!DOCTYPE other SYSTEM "http://www.gesetze-im-internet.de/dtd/1.01/gii-norm.dtd">',
    ],
)
def test_entity_bearing_and_other_doctypes_are_rejected(doctype: str) -> None:
    body = "<norm><metadaten><jurabk>X</jurabk><enbez>§ 1</enbez></metadaten></norm>"
    with pytest.raises(GiiParseError, match="unsafe XML"):
        parse_gesetze_im_internet(_gii(body, doctype=doctype))


def test_unexpected_input_is_rejected() -> None:
    with pytest.raises(GiiParseError, match="unsupported root"):
        parse_gesetze_im_internet(b"<akomaNtoso/>")
    two = io.BytesIO()
    with zipfile.ZipFile(two, "w") as z:
        z.writestr("a.xml", "<dokumente/>")
        z.writestr("b.xml", "<dokumente/>")
    with pytest.raises(GiiParseError, match="exactly one XML file"):
        parse_gesetze_im_internet(two.getvalue())
    with pytest.raises(GiiParseError, match="builddate"):
        parse_gesetze_im_internet(_gii("", builddate="unknown"))


# --------------------------------------------------------------------------- parser on real laws


def test_grundgesetz() -> None:
    doc = _doc("gg")
    assert (doc.jurisdiction_id, doc.instrument_type, doc.work_uri, doc.language) == (
        "DE",
        "constitution",
        "/de/bund/gg",
        "de",
    )
    assert doc.work_date == date(1949, 5, 23)  # ausfertigung-datum
    assert doc.version_date == doc.currency_date == date(2026, 5, 6)  # dokumente/@builddate
    assert doc.expression_uri == "/de/bund/gg/deu@2026-05-06"
    assert (doc.text_origin, doc.authoritative, doc.validity_basis) == (
        "official_revised",
        False,
        "attested_at_edition",
    )
    assert all(p.valid_from is None and p.valid_to is None for p in doc.provisions)  # no per-provision dates
    assert {"jurabk": "GG", "fundstelle": "BGBl 1949, 1"}.items() <= doc.ext.items()
    art1 = _p(doc, "art-1-abs-1")
    assert art1.text == (
        "Die Würde des Menschen ist unantastbar. "
        "Sie zu achten und zu schützen ist Verpflichtung aller staatlichen Gewalt."
    )
    assert _cite(doc, "art-1-abs-1") == "Art. 1 Abs. 1 GG"
    assert _p(doc, "art-1").text.startswith("(1) Die Würde des Menschen")  # the container keeps the labels
    assert _cite(doc, "art-73-abs-1-nr-5a") == "Art. 73 Abs. 1 Nr. 5a GG"  # one list continued over two <DL>s
    assert _cite(doc, "praeambel") == "Präambel GG"
    repealed = _p(doc, "art-49")
    assert (repealed.status, repealed.text, repealed.note) == ("repealed", "", "(weggefallen)")
    assert _p(doc, "arts-74a-und-75").status == "repealed"  # "(XXXX) Art 74a und 75"
    assert "Nichtamtlicher Hinweis" in (_p(doc, "art-140").note or "")  # WRV articles: an unofficial note only
    assert not any(p.eid.startswith(("anhang", "inhalts")) for p in doc.provisions)


def test_kuendigungsschutzgesetz_lists() -> None:
    doc = _doc("kschg")
    abs2 = _p(doc, "para-1-abs-2")
    assert abs2.has_own_content and not abs2.is_leaf  # chapeau + items + "Satz 2 gilt entsprechend ..."
    assert abs2.text.startswith("Sozial ungerechtfertigt ist die Kündigung")
    leaf = _p(doc, "para-1-abs-2-nr-1-buchst-a")
    assert leaf.text == "die Kündigung gegen eine Richtlinie nach § 95 des Betriebsverfassungsgesetzes verstößt,"
    assert _cite(doc, "para-1-abs-2-nr-1-buchst-a") == "§ 1 Abs. 2 Nr. 1 Buchst. a KSchG"
    assert _p(doc, "para-1").heading == "Sozial ungerechtfertigte Kündigungen"
    assert doc.outstanding_changes == []
    assert [s["typ"] for s in doc.ext["standangaben"]] == ["Neuf", "Stand"]


def test_strafgesetzbuch_notes_and_repealed_ranges() -> None:
    doc = _doc("stgb")
    assert _cite(doc, "para-242-abs-1") == "§ 242 Abs. 1 StGB"
    assert _p(doc, "para-242-abs-1").text.startswith("Wer eine fremde bewegliche Sache einem anderen")
    # § 217 was declared void by the Federal Constitutional Court; the source keeps the text and says so in a
    # footnote, which ULIS keeps as a note on the section and every subdivision (never as text).
    void = _p(doc, "para-217-abs-1")
    assert void.status == "in_force" and "nichtig gem. BVerfGE v. 26.2.2020" in (void.note or "")
    assert "nichtig" not in void.text
    rng = _p(doc, "paras-116-bis-119")
    assert rng.status == "repealed" and _cite(doc, "paras-116-bis-119") == "§§ 116 bis 119 StGB"
    # Official footnote (notification under Directive (EU) 2015/1535) is a note, its marker not text.
    assert "Notifiziert gemäß der Richtlinie (EU) 2015/1535" in (_p(doc, "para-184l").note or "")
    assert (
        _p(doc, "para-184l").heading
        == "Inverkehrbringen, Erwerb und Besitz von Sexpuppen mit kindlichem Erscheinungsbild"
    )


def test_gewerbesteuergesetz_numbered_sentences() -> None:
    doc = _doc("gewstg")
    s2 = _p(doc, "para-2-abs-1-satz-2")
    assert (
        s2.text
        == "Unter Gewerbebetrieb ist ein gewerbliches Unternehmen im Sinne des Einkommensteuergesetzes zu verstehen."
    )
    assert _cite(doc, "para-2-abs-1-satz-2") == "§ 2 Abs. 1 Satz 2 GewStG"
    # The superscript sentence numbers are structure, not text.
    assert _p(doc, "para-2-abs-1").text.startswith("Der Gewerbesteuer unterliegt jeder stehende Gewerbebetrieb")
    assert _p(doc, "para-2-abs-1-satz-1").text.startswith("Der Gewerbesteuer unterliegt")
    # Sentence 1 has no marker of its own when the first marker is "2".
    assert _p(doc, "para-8-nr-1-buchst-a-satz-1").text == "Entgelten für Schulden."
    assert _cite(doc, "para-8-nr-1-buchst-d-satz-2-buchst-aa") == "§ 8 Nr. 1 Buchst. d Satz 2 Doppelbuchst. aa GewStG"
    # Sentences spread over several <LA> blocks; each sentence keeps its own list.
    assert _cite(doc, "para-9-nr-1-satz-5-nr-1a-satz-2") == "§ 9 Nr. 1 Satz 5 Nr. 1a Satz 2 GewStG"
    assert _p(doc, "para-9-nr-1-satz-6").text.startswith("Eine Kürzung nach den Sätzen 2 und 3 ist ausgeschlossen")
    item = _p(doc, "para-8-nr-2")
    assert (item.status, item.text) == ("repealed", "")


def test_identifiers_follow_the_site() -> None:
    for abbr in LAWS:
        assert gii_slug(_doc(abbr).ext["jurabk"]) == abbr
    assert gii_slug("BDSG 2018") == "bdsg_2018"
    assert resolve_uri("https://www.gesetze-im-internet.de/bgb/__823.html") == ("/de/bund/bgb", "para-823")
    assert resolve_uri("http://www.gesetze-im-internet.de/gg/art_1.html") == ("/de/bund/gg", "art-1")
    assert resolve_uri("https://www.gesetze-im-internet.de/bdsg_2018/index.html") == ("/de/bund/bdsg_2018", None)
    assert resolve_uri("https://example.org/bgb/__823.html") is None
    assert pipeline_resolve_uri("https://www.gesetze-im-internet.de/stgb/__242.html") == ("/de/bund/stgb", "para-242")


def test_stand_hinweise_and_ambiguous_lists() -> None:
    stand = (
        "<standangabe><standtyp>Hinweis</standtyp><standkommentar>Änderung durch Art. 1 G v. 1.1.2026 I Nr. 1 "
        "textlich nachgewiesen, dokumentarisch noch nicht abschließend bearbeitet</standkommentar></standangabe>"
        "<standangabe><standtyp>Hinweis</standtyp><standkommentar>Änderung durch Art. 2 G v. 1.1.2026 I Nr. 1 "
        "noch nicht berücksichtigt</standkommentar></standangabe>"
    )
    two_lists = (
        "<norm><metadaten><jurabk>TestG</jurabk><enbez>§ 1</enbez></metadaten><textdaten><text><Content>"
        "<P>(1) Erster Satz: <DL><DT>1.</DT><DD><LA>a,</LA></DD><DT>2.</DT><DD><LA>b.</LA></DD></DL>"
        "Zweiter Satz: <DL><DT>1.</DT><DD><LA>c,</LA></DD><DT>2.</DT><DD><LA>d.</LA></DD></DL></P>"
        "<P>(2) (weggefallen)</P></Content></text></textdaten></norm>"
    )
    (doc,) = parse_gesetze_im_internet(_gii(two_lists, stand=stand))
    # Already in the text (Hinweise item 4): recorded, not outstanding. Anything else is outstanding.
    assert [c["description"] for c in doc.outstanding_changes] == [
        "Änderung durch Art. 2 G v. 1.1.2026 I Nr. 1 noch nicht berücksichtigt"
    ]
    abs1 = _p(doc, "para-1-abs-1")
    assert abs1.is_leaf and abs1.text.startswith("Erster Satz:\n1. a,")  # "Nr. 1" would be ambiguous: not split
    assert _p(doc, "para-1-abs-2").status == "repealed"


def test_citation_module() -> None:
    module = get_module("de")

    def cite(path: list[str], top: str = "section", ext: dict[str, Any] | None = None) -> str:
        return module.provision_citation(
            ProvisionCitationInput("x", "act", "DE", top, path, "de", ext=ext or {"jurabk": "BGB", "doknr": "X"})
        )

    assert cite(["§ 823", "(1)"]) == "§ 823 Abs. 1 BGB"
    assert cite(["§ 31a", "(1a)", "^2", "3.", "b)", "cc)", "ddd)"]) == (
        "§ 31a Abs. 1a Satz 2 Nr. 3 Buchst. b Doppelbuchst. cc Dreifachbuchst. ddd BGB"
    )
    assert cite(["Art 1", "(1)"], "article", {"jurabk": "GG", "doknr": "X"}) == "Art. 1 Abs. 1 GG"
    assert cite(["§ 1"], ext={"jurabk": "BDSG 2018", "amtabk": "BDSG", "doknr": "X"}) == "§ 1 BDSG"
    assert cite(["Anlage 1"], "annex", {"jurabk": "EStG", "doknr": "X"}) == "Anlage 1 zum EStG"
    assert cite(["Anlage 2"], "annex", {"jurabk": "ZPO", "doknr": "X"}) == "Anlage 2 ZPO"


# --------------------------------------------------------------------------- connector


def _manifest(tmp_path: Path, laws: list[dict[str, Any]], **extra: Any) -> Path:
    path = tmp_path / "manifest.yaml"
    data = {
        "schema_version": 1,
        "source_id": SOURCE,
        "base_url": "https://www.gesetze-im-internet.de",
        "user_agent": "ULIS-test (+https://example.org)",
        "laws": laws,
        **extra,
    }
    path.write_text(yaml.safe_dump(data, allow_unicode=True))
    return path


def test_connector_offline_from_cache(tmp_path: Path) -> None:
    cache = shutil.copytree(CACHE, tmp_path / "cache")
    pin = hashlib.sha256(_zip("kschg").read_bytes()).hexdigest()
    manifest = _manifest(tmp_path, [{"abbr": "gg"}, {"abbr": "kschg", "sha256": pin}])
    connector = GesetzeImInternetConnector(manifest, SOURCE, cache, offline=True)
    docs = list(connector.fetch())
    assert [d.external_id for d in docs] == ["gesetze-im-internet:gg", "gesetze-im-internet:kschg"]
    assert docs[1].content == _zip("kschg").read_bytes()  # exact bytes as received
    assert docs[1].uri == "https://www.gesetze-im-internet.de/kschg/xml.zip"
    assert docs[1].media_type == "application/zip" and docs[1].retrieved_at.year == 2026
    assert connector.http is not None and connector.http.requests_made == 0
    with pytest.raises(FetchError, match="offline and not cached"):
        list(GesetzeImInternetConnector(_manifest(tmp_path, [{"abbr": "bgb"}]), SOURCE, cache, offline=True).fetch())
    with pytest.raises(ManifestError, match="sha256"):
        bad_pin = _manifest(tmp_path, [{"abbr": "kschg", "sha256": "0" * 64}])
        list(GesetzeImInternetConnector(bad_pin, SOURCE, cache, offline=True).fetch())


def test_connector_refuses_bad_manifests_and_non_zip_responses(tmp_path: Path) -> None:
    for laws, extra, message in (
        ([{"abbr": "../etc"}], {}, "invalid law path"),
        ([{"abbr": "_ag/../x"}], {}, "invalid law path"),
        ([{"abbr": "Bgb"}], {}, "invalid law path"),
        ([{"abbr": "gg"}], {"base_url": "http://www.gesetze-im-internet.de"}, "base_url"),
        ([{"abbr": "gg"}], {"user_agent": "python-urllib"}, "user_agent"),
        ([], {}, "no laws"),
    ):
        with pytest.raises(ManifestError, match=message):
            list(GesetzeImInternetConnector(_manifest(tmp_path, laws, **extra), SOURCE, tmp_path).fetch())
    # A cached HTML page (e.g. an error page) is never passed on as a law.
    page = tmp_path / "c" / "www.gesetze-im-internet.de" / "gg" / "xml.zip"
    page.parent.mkdir(parents=True)
    page.write_bytes(b"<html>Fehler</html>")
    meta = {"url": "x", "status": 200, "content_type": "text/html", "retrieved_at": "2026-10-14T00:00:00+00:00"}
    page.with_name("xml.zip.meta.json").write_text(json.dumps(meta))
    connector = GesetzeImInternetConnector(_manifest(tmp_path, [{"abbr": "gg"}]), SOURCE, tmp_path / "c", offline=True)
    assert list(connector.fetch()) == []  # skipped, not aborted ...
    assert len(connector.fetch_errors) == 1 and "not a zip archive" in connector.fetch_errors[0]  # ... and reported


def test_source_uses_this_connector(catalog: Catalog) -> None:
    connector = build_connector(catalog.sources.get(SOURCE))
    assert isinstance(connector, GesetzeImInternetConnector)


# --------------------------------------------------------------------------- PostgreSQL


class _Fixtures:
    def fetch(self) -> Iterator[RawDocument]:
        for abbr in LAWS:
            meta = json.loads(_zip(abbr).with_name("xml.zip.meta.json").read_text())
            yield RawDocument(
                f"gesetze-im-internet:{abbr}",
                meta["final_url"],
                "application/zip",
                _zip(abbr).read_bytes(),
                datetime.fromisoformat(meta["retrieved_at"]).astimezone(UTC),
                {"abbr": abbr},
            )


@pytest.fixture(scope="module")
def de_ingested(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, ingested: IngestionReport
) -> IngestionReport:
    with session_factory() as session:
        report = Ingestor(session, catalog, store).ingest_source(SOURCE, actor="pytest", connector=_Fixtures())
        session.commit()
    return report


def _search(session: Session, catalog: Catalog, **kw: Any) -> dict[str, Any]:
    return search(session, catalog, SearchRequest(jurisdiction="DE", **kw), today=date(2026, 10, 14))


@pytest.mark.db
def test_search_finds_exact_text_with_citation_and_limitations(
    session: Session, catalog: Catalog, de_ingested: IngestionReport
) -> None:
    assert de_ingested.errors == [] and de_ingested.conflicts == []
    assert de_ingested.instruments_created == 4
    res = _search(session, catalog, q="Würde des Menschen unantastbar", as_of=date(2026, 10, 14))
    hit = next(h for h in res["results"] if h["citation"] == "Art. 1 Abs. 1 GG")
    # Germany is an EU member: EU law (e.g. the Charter, if ingested) is in scope, classified as such.
    assert all(
        h["authority"]["category"] in ("regional_law", "regional_directive")
        for h in res["results"]
        if h["jurisdiction"]["id"] == "EU"
    )
    assert hit["text"].startswith("Die Würde des Menschen ist unantastbar.")
    assert hit["authority"]["category"] == "applicability_unverified"
    codes = {lim["code"] for lim in hit["limitations"]}
    assert {"ATTESTED_ONLY_AT", "REVISED_TEXT", "SOURCE_NOT_AUTHENTIC", "PROFILE_UNVERIFIED"} <= codes
    assert "NOT_AUTHENTIC_TEXT" not in codes  # REVISED_TEXT already says it is not the authentic text
    assert hit["source"]["attribution"].startswith("Quelle: Gesetze im Internet")
    tax = _search(session, catalog, q="gewerbliches Unternehmen im Sinne des Einkommensteuergesetzes")
    assert tax["results"][0]["citation"] == "§ 2 Abs. 1 Satz 2 GewStG"
    void = _search(session, catalog, q="Selbsttötung geschäftsmäßig Gelegenheit")["results"][0]
    assert void["citation"] == "§ 217 Abs. 1 StGB"
    assert any(lim["code"] == "VERSION_NOTE" and "nichtig" in lim["message"] for lim in void["limitations"])


@pytest.mark.db
def test_earlier_dates_are_a_history_gap_not_absence(
    session: Session, catalog: Catalog, de_ingested: IngestionReport
) -> None:
    uri = "/de/bund/stgb/~para-242-abs-1"
    at_build = provision_at(session, catalog, uri, date(2026, 9, 28))
    assert at_build["status_on_date"] == "in_force_date_unverified"
    assert at_build["version"]["currency_date"] == "2026-09-28"
    earlier = provision_at(session, catalog, uri, date(2015, 1, 1))
    # Only the current wording is published: 2015 is not "not yet in force", it is unknown.
    assert earlier["status_on_date"] == "in_force_date_unverified"
    assert "ATTESTED_ONLY_AT" in {lim["code"] for lim in earlier["limitations"]}
    res = _search(session, catalog, q="fremde bewegliche Sache wegnimmt", as_of=date(2015, 1, 1))
    assert res["results"][0]["citation"] == "§ 242 Abs. 1 StGB"
    assert "HISTORY_INCOMPLETE" in {lim["code"] for lim in res["limitations"]}
    repealed = provision_at(session, catalog, "/de/bund/gg/~art-49", date(2026, 5, 6))
    assert [(t["status"], t["note"]) for t in repealed["timeline"]] == [("repealed", "(weggefallen)")]


@pytest.mark.db
def test_reingest_is_idempotent_and_sources_verify(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, de_ingested: IngestionReport
) -> None:
    with session_factory() as session:
        again = Ingestor(session, catalog, store).ingest_source(SOURCE, actor="pytest", connector=_Fixtures())
        assert again.documents_unchanged == len(LAWS) and again.provision_versions_created == 0
        ids = session.scalars(select(m.SourceDocument.id).where(m.SourceDocument.source_id == SOURCE)).all()
        results = [verify_source_document(session, catalog, store, i, checker="pytest") for i in ids]
        assert len(results) == len(LAWS) and all(r["result"] == "pass" for r in results)
        assert sum(r["checked_units"] for r in results) == de_ingested.provision_versions_created
        session.rollback()


class _Editions:
    """Two editions of a synthetic law: the newer one has a new wording of § 1 Abs. 1."""

    def fetch(self) -> Iterator[RawDocument]:
        for build, words in (("20260101120000", "alte Fassung"), ("20260601120000", "neue Fassung")):
            norm = (
                "<norm><metadaten><jurabk>EdG</jurabk><enbez>§ 1</enbez></metadaten><textdaten><text><Content>"
                f"<P>(1) Dies ist die {words}.</P><P>(2) Unverändert.</P></Content></text></textdaten></norm>"
            )
            content = _gii(norm, builddate=build).replace(b"BJNRTEST", b"BJNREDITIONS").replace(b"TestG", b"EdG")
            yield RawDocument(
                f"test:edg@{build}", "https://example.org/edg", "application/xml", content, datetime.now(UTC)
            )


@pytest.mark.db
def test_a_newer_edition_with_new_wording_is_a_new_version_not_a_conflict(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, ingested: IngestionReport
) -> None:
    with session_factory() as session:
        report = Ingestor(session, catalog, store).ingest_source(SOURCE, actor="pytest", connector=_Editions())
        session.commit()
    assert report.errors == [] and report.conflicts == [] and report.provision_versions_restated >= 1
    (doc,) = parse_gesetze_im_internet(next(iter(_Editions().fetch())).content)
    uri = f"{doc.work_uri}/~para-1-abs-1"
    with session_factory() as session:
        before = provision_at(session, catalog, uri, date(2026, 3, 1))
        after = provision_at(session, catalog, uri, date(2026, 7, 1))
        unchanged = provision_at(session, catalog, f"{doc.work_uri}/~para-1-abs-2", date(2026, 7, 1))
    assert before["status_on_date"] == "in_force_date_unverified" and "alte Fassung" in before["version"]["text"]
    assert after["status_on_date"] == "in_force" and "neue Fassung" in after["version"]["text"]
    assert after["version"]["valid_from"] == "2026-06-01" and after["version"]["valid_from_basis"] == "observed"
    assert unchanged["version"]["text"] == "Unverändert."  # same wording in both editions: one version


# --------------------------------------------------------------------------- laws without text are skipped, not errors


def _empty_law_zip() -> bytes:
    """A real kind of file: an amendment act whose articles have no text (synthetic, edge case only)."""
    body = (
        '<norm doknr="BJNRTEST1"><metadaten><jurabk>TestG</jurabk><gliederungseinheit>'
        "<gliederungskennzahl>010</gliederungskennzahl><gliederungsbez>Art 1</gliederungsbez></gliederungseinheit>"
        '</metadaten><textdaten><text format="XML"><Content><P/></Content></text></textdaten></norm>'
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("BJNRTEST.xml", _gii(body))
    return buf.getvalue()


def test_every_law_in_the_committed_manifest_passes_the_connector_validation() -> None:
    laws = yaml.safe_load((REPO_ROOT / "config/manifests/de-gesetze-im-internet.yaml").read_text())["laws"]
    connector = GesetzeImInternetConnector(
        REPO_ROOT / "config/manifests/de-gesetze-im-internet.yaml", SOURCE, Path("/nonexistent")
    )
    assert len(connector._manifest()["laws"]) == len(laws) > 1000  # includes the 35 laws whose path begins with "_"
    assert "_ag" in {law["abbr"] for law in laws}


def test_a_law_without_any_text_is_a_skip_not_a_parse_error() -> None:
    with pytest.raises(ParseSkipped) as caught:
        parse_gesetze_im_internet(_empty_law_zip())
    assert isinstance(caught.value, GiiParseError) and "no text" in str(caught.value)


@pytest.mark.db
def test_pipeline_counts_text_less_laws_as_skipped_and_still_ingests_the_rest(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, ingested: IngestionReport
) -> None:
    class _Mixed:
        fetch_errors = ["gesetze-im-internet:gone: https://example.invalid/gone/xml.zip: not found (404)"]

        def fetch(self) -> Iterator[RawDocument]:
            now = datetime(2026, 10, 14, tzinfo=UTC)
            yield RawDocument(
                "gesetze-im-internet:leer", "https://www.gesetze-im-internet.de/leer/xml.zip",
                "application/zip", _empty_law_zip(), now, {"abbr": "leer"},
            )  # fmt: skip
            yield from _Fixtures().fetch()

    with session_factory() as session:
        report = Ingestor(session, catalog, store).ingest_source(SOURCE, actor="pytest", connector=_Mixed())
        session.rollback()
    assert report.documents_skipped == 1 and report.skipped[0].startswith("gesetze-im-internet:leer")
    assert report.documents_new + report.documents_unchanged == len(LAWS)
    assert report.errors == _Mixed.fetch_errors  # a connector's skipped fetches are reported, parse skips are not

"""Luxembourg: Legilux consolidated legislation (Akoma Ntoso CSD13 XML, French), parser, connector and module.

Fixtures are real files from legilux.public.lu (see ``tests/fixtures/legilux/README.md``).
"""

from __future__ import annotations

import gzip
import json
import re
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ulis.catalog import Catalog
from ulis.db import models as m
from ulis.ingestion.pipeline import CONNECTORS, PARSERS, IngestionReport, Ingestor
from ulis.ingestion.pipeline import resolve_uri as pipeline_resolve
from ulis.jurisdictions.modules import ProvisionCitationInput, get_module
from ulis.retrieval.lookup import provision_at
from ulis.retrieval.search import SearchRequest, search
from ulis.settings import REPO_ROOT
from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.legilux import ROBOTS_URL, LegiluxConnector, bundle_bytes, file_url
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.polite_http import FetchError
from ulis.sources.parsers.legilux import LegiluxParseError, parse_legilux, resolve_uri, skipped_blocks
from ulis.sources.registry import licence_gate
from ulis.storage.object_store import LocalObjectStore

FIX = REPO_ROOT / "tests" / "fixtures" / "legilux"
CONSTITUTION = "constitution/1868/10/17/n1/consolide"
LOI = "loi/2018/08/01/a686/consolide"
CONSTITUTION_TITLE = "Constitution du Grand-Duché de Luxembourg"


def _read(name: str) -> bytes:
    return gzip.decompress((FIX / f"{name}.xml.gz").read_bytes())


def _bundle(path: str, day: str, name: str) -> bytes:
    return bundle_bytes(path, day, file_url(path, day), _read(name).decode("utf-8"))


def _doc(name: str):  # type: ignore[no-untyped-def]
    docs = parse_legilux(_read(name))
    assert len(docs) == 1
    return docs[0]


def _prov(doc, eid: str):  # type: ignore[no-untyped-def]
    return next(p for p in doc.provisions if p.eid == eid)


# --------------------------------------------------------------------------- parser


def test_constitution_is_a_snapshot_with_a_stated_end_and_no_struck_text() -> None:
    doc = _doc("constitution-20090101")
    assert (doc.instrument_type, doc.jurisdiction_id, doc.language) == ("constitution", "LU", "fr")
    assert doc.work_uri == "/lu/constitution/1868/10/17/n1"
    assert doc.expression_uri == "/lu/constitution/1868/10/17/n1/fr@2009-01-01"
    assert doc.title == CONSTITUTION_TITLE and doc.work_date == date(1868, 10, 17)
    assert doc.version_date == date(2009, 1, 1) and doc.validity_basis == "revision_snapshot"
    assert doc.authoritative is False and doc.text_origin == "official_consolidation"
    assert doc.ext["eli"] == "http://data.legilux.public.lu/eli/etat/leg/constitution/1868/10/17/n1"
    # The file states that the text applied until 2009-03-15; provision periods are half-open.
    assert {p.valid_to for p in doc.provisions if p.status == "in_force"} == {date(2009, 3, 16)}
    # Article 10 (naturalisation) is only present as struck-through wording of the earlier version.
    eids = {p.eid for p in doc.provisions}
    assert "art_10" not in eids and "art_10_20090101" not in eids and "art_10bis" in eids
    assert not any("naturalisation est accordée" in p.text for p in doc.provisions)
    assert [e.event_type for e in doc.events] == [
        "version_change",
        "enactment",
    ]  # 1989-04-18 is not an entry into force


def test_constitution_article_34_changes_on_2009_03_16() -> None:
    old, new = _doc("constitution-20090101"), _doc("constitution-20090316")
    assert _prov(old, "art_34").text == (
        "Le Grand-Duc sanctionne et promulgue les lois. Il fait connaître sa résolution dans les trois mois "
        "du vote de la Chambre."
    )
    assert _prov(new, "art_34").text == "Le Grand-Duc promulgue les lois dans les trois mois du vote de la Chambre."
    assert new.version_date == date(2009, 3, 16)
    changed = [p.eid for p in new.provisions if _prov(old, p.eid).text != p.text]
    assert changed == ["art_34"]


def test_code_civil_articles_paragraphs_lists_and_repeals() -> None:
    doc = _doc("code-civil-20251226-excerpt")
    assert doc.instrument_type == "code" and doc.work_uri == "/lu/code/civil" and doc.title == "Code civil"
    assert doc.ext["base_act"] == "http://data.legilux.public.lu/eli/etat/leg/loi/1804/03/21/n1"
    art = _prov(doc, "art_1382")
    assert art.num_path == ["1382"] and art.top_kind == "article" and art.status == "in_force"
    assert art.text == (
        "Tout fait quelconque de l'homme, qui cause à autrui un dommage, oblige celui par la faute duquel il est "
        "arrivé, à le réparer."
    )
    assert art.valid_from == date(2025, 12, 26) and art.note == "dateApplicability: 2018-04-01"
    assert _prov(doc, "art_1er").num_path == ["1er"]
    # Numbered paragraphs are child provisions; the article holds the whole text.
    para = _prov(doc, "art_63/para_2")
    assert (para.parent_eid, para.num_path, para.is_leaf) == ("art_63", ["63", "para:2"], True)
    assert _prov(doc, "art_63").is_leaf is False and para.text in _prov(doc, "art_63").text
    assert "(1)" not in para.text  # the paragraph number is not part of the text
    # List items carry the label given by the list's symbol (a), b), ...).
    lines = _prov(doc, "art_34").text.split("\n")
    assert lines[1].startswith("a) des parents") and lines[4].startswith("d) du décédé")
    # The amendment reference printed in the text stays in it, without the spaces of the XML line breaks.
    assert _prov(doc, "art_34").text.startswith("(L. 4 juillet 2014) Les actes de l’état civil")
    # A repealed article: empty text, status repealed, the wording kept as note; dated from the consolidation like
    # every provision (the repeal date is not stated).
    gone = _prov(doc, "art_12")
    assert (gone.status, gone.text, gone.valid_from) == ("repealed", "", date(2025, 12, 26))
    assert gone.note is not None and gone.note.startswith("Abrogé (L. 23 avril 1934)")
    assert [r.href for r in gone.refs] == ["http://data.legilux.public.lu/eli/etat/leg/loi/1934/04/23/n1/jo"]
    assert pipeline_resolve(gone.refs[0].href) == ("/lu/loi/1934/04/23/n1", None)


def test_wording_changes_between_consolidations_are_visible_and_spacing_is_not() -> None:
    old, new = _doc("code-civil-20250420-excerpt"), _doc("code-civil-20251226-excerpt")
    changed = sorted(p.eid for p in new.provisions if p.text != _prov(old, p.eid).text)
    assert changed == ["art_1165", "art_359", "art_59", "art_86"]
    assert "voyage en mer" in _prov(old, "art_86").text and "voyage de mer" in _prov(new, "art_86").text
    assert "(L. 19 décembre 2025) En cas" in _prov(new, "art_359").text
    assert "(L. 19 décembre 2025)" not in _prov(old, "art_359").text


def test_law_consolidation_and_code_du_travail_labels() -> None:
    law = _doc("loi-2018-08-01-a686-20251226")
    assert law.instrument_type == "law" and law.work_uri == "/lu/loi/2018/08/01/a686"
    assert law.work_date == date(2018, 8, 1) and law.version_date == date(2025, 12, 26)
    assert law.title.startswith("Loi du 1er août 2018 portant organisation de la Commission nationale")
    assert law.short_title is not None and law.short_title.endswith("régime général sur la protection des données")
    assert [e.event_type for e in law.events] == ["version_change", "enactment", "entry_into_force"]
    assert law.events[2].date == date(2018, 8, 20)
    assert {p.valid_to for p in law.provisions} == {None}  # the latest consolidation is open-ended
    work = _doc("code-travail-20260726-excerpt")
    art = _prov(work, "art_l_010-1")
    assert art.num_path == ["L. 010-1"]
    assert art.note is not None and art.note.startswith("(L. 24 juillet 2024)")  # amendment reference: note, not text
    assert not art.text.startswith("(")
    assert skipped_blocks(_read("code-travail-20260726-excerpt"))[0].startswith("annex")


def test_the_consolidation_date_of_the_bundle_overrides_stale_file_metadata() -> None:
    # The file served for the Code du travail consolidation of 2026-07-26 states dateApplicability 2026-07-01.
    bare = parse_legilux(_read("code-travail-20260726-excerpt"))[0]
    assert bare.version_date == date(2026, 7, 1) and bare.ext.get("metadata_date") is None
    doc = parse_legilux(_bundle("code/travail", "2026-07-26", "code-travail-20260726-excerpt"))[0]
    assert doc.version_date == date(2026, 7, 26) and doc.expression_uri == "/lu/code/travail/fr@2026-07-26"
    assert doc.ext["metadata_date"] == "2026-07-01"
    assert {p.valid_from for p in doc.provisions} == {date(2026, 7, 26)}
    assert [p.text for p in doc.provisions] == [p.text for p in bare.provisions]
    # A consistent file keeps its stated end date; with another date the stated end is not trusted.
    fine = parse_legilux(_bundle(CONSTITUTION, "2009-01-01", "constitution-20090101"))[0]
    assert fine.ext.get("metadata_date") is None and {p.valid_to for p in fine.provisions} == {date(2009, 3, 16)}
    stale = parse_legilux(_bundle(CONSTITUTION, "2009-01-02", "constitution-20090101"))[0]
    assert stale.ext["metadata_date"] == "2009-01-01" and {p.valid_to for p in stale.provisions} == {None}
    with pytest.raises(LegiluxParseError, match="unsupported bundle"):
        parse_legilux(b'{"schema": "other/1"}')
    with pytest.raises(LegiluxParseError, match="not a Legilux bundle"):
        parse_legilux(b'{"schema": "ulis-lu-legilux-bundle/1", "consolidation": "soon", "xml": "<a/>"}')


def test_unsupported_documents_are_rejected_loudly() -> None:
    good = _read("constitution-20090101")
    with pytest.raises(LegiluxParseError, match="not well-formed or unsafe"):
        parse_legilux(b'<!DOCTYPE x [<!ENTITY a "b">]><akomaNtoso xmlns="http://x"/>')
    with pytest.raises(LegiluxParseError, match="unsupported root"):
        parse_legilux(b"<html/>")
    with pytest.raises(LegiluxParseError, match="French is read"):
        parse_legilux(good.replace(b"language/FRA", b"language/DEU"))
    with pytest.raises(LegiluxParseError, match="missing FRBRWork"):
        parse_legilux(
            b"<akomaNtoso xmlns='http://docs.oasis-open.org/legaldocml/ns/akn/3.0/CSD13'><act>"
            b"<meta><identification/></meta><body/></act></akomaNtoso>"
        )
    with pytest.raises(LegiluxParseError, match="no articles"):
        parse_legilux(re.sub(rb"<body>.*</body>", b"<body/>", good, flags=re.S))


def test_resolve_uri() -> None:
    base = "http://data.legilux.public.lu/eli/etat/leg/"
    assert resolve_uri(base + "code/civil") == ("/lu/code/civil", None)
    assert resolve_uri(base + "code/civil/20251226/fr/xml") == ("/lu/code/civil", None)
    assert resolve_uri(base + "code/civil/art_1") == ("/lu/code/civil", None)
    assert resolve_uri(base + "loi/2018/08/01/a686/jo") == ("/lu/loi/2018/08/01/a686", None)
    assert resolve_uri("/eli/etat/leg/loi/2018/08/01/a686/consolide/20251226") == ("/lu/loi/2018/08/01/a686", None)
    assert resolve_uri("https://legilux.public.lu/eli/etat/leg/constitution/1868/10/17/n1/jo") == (
        "/lu/constitution/1868/10/17/n1",
        None,
    )
    assert resolve_uri("http://data.legilux.public.lu/eli/etat/adm/pa/2017/01/10/b159/jo") is None
    assert resolve_uri("https://example.org/eli/etat/leg/code/civil") is None


# --------------------------------------------------------------------------- module


def _cite(title: str, path: list[str], lang: str = "fr") -> str:
    module = get_module("lu")
    return module.provision_citation(ProvisionCitationInput(title, "code", "LU", "article", path, lang))


def test_citations_follow_luxembourg_practice() -> None:
    assert _cite("Code civil", ["1382"]) == "Art. 1382 du Code civil"
    assert _cite("Code civil", ["63", "para:2"]) == "Art. 63, paragraphe 2 du Code civil"
    assert _cite("Code civil", ["63", "para:1"]) == "Art. 63, paragraphe 1er du Code civil"
    assert _cite("Code civil", ["1er"]) == "Art. 1er du Code civil"
    assert _cite("Code du travail", ["L. 121-1"]) == "Art. L. 121-1 du Code du travail"
    assert _cite("Nouveau Code de procédure civile", ["1007-39"]) == "Art. 1007-39 du Nouveau Code de procédure civile"
    assert _cite(CONSTITUTION_TITLE, ["34"]) == "Art. 34 de la Constitution du Grand-Duché de Luxembourg"
    assert _cite("Loi du 1er août 2018 portant organisation de la CNPD", ["5"]) == (
        "Art. 5 de la loi du 1er août 2018 portant organisation de la CNPD"
    )
    assert _cite("Règlement grand-ducal du 1er août 2018 portant exécution", ["2"]).startswith(
        "Art. 2 du règlement grand-ducal du 1er août 2018"
    )
    assert _cite("Arrêté grand-ducal du 3 mai 2020 relatif", ["2"]).startswith(
        "Art. 2 de l'arrêté grand-ducal du 3 mai"
    )
    module = get_module("lu")
    assert module.validate_ext(
        {"eli": "http://data.legilux.public.lu/eli/etat/leg/code/civil", "act_type": "code", "base_act": None}
    ) == {"eli": "http://data.legilux.public.lu/eli/etat/leg/code/civil", "act_type": "code"}


# --------------------------------------------------------------------------- connector


def test_file_url_and_committed_manifest() -> None:
    assert file_url("code/civil", date(2025, 12, 26)) == (
        "https://legilux.public.lu/filestore/eli/etat/leg/code/civil/20251226/fr/xml/"
        "eli-etat-leg-code-civil-20251226-fr-xml.xml"
    )
    assert file_url(LOI, "2025-12-26").endswith(
        "/loi/2018/08/01/a686/consolide/20251226/fr/xml/eli-etat-leg-loi-2018-08-01-a686-consolide-20251226-fr-xml.xml"
    )
    manifest = REPO_ROOT / "config" / "manifests" / "lu-legilux.yaml"
    connector = LegiluxConnector(manifest, "lu-legilux", REPO_ROOT / "var" / "sources")
    data = connector._manifest()  # noqa: SLF001 - the connector's own validation of the committed manifest
    paths = {w["path"] for w in data["works"]}
    assert len(paths) > 1000 and {"code/civil", "code/penal", "code/travail", CONSTITUTION, LOI} <= paths
    assert not any(p.startswith(("recueil/", "adm/")) for p in paths)
    assert next(w for w in data["works"] if w["path"] == "code/civil")["consolidations"][0] == "2016-09-01"


def _put(cache: Path, url: str, data: bytes, content_type: str = "application/xml") -> None:
    """Write one response in PoliteHttp's cache layout (``<cache>/<host>/<path>`` and ``.meta.json``)."""
    target = cache / url.removeprefix("https://")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    meta = {
        "url": url,
        "final_url": url,
        "status": 200,
        "content_type": content_type,
        "retrieved_at": "2026-10-14T10:00:00+00:00",
    }
    target.with_name(target.name + ".meta.json").write_text(json.dumps(meta))


def _manifest(tmp_path: Path, works: list[dict[str, Any]], **overrides: Any) -> Path:
    manifest = {
        "schema_version": 1,
        "source_id": "lu-legilux",
        "user_agent": "ULIS-test (+https://example.org)",
        "crawl_delay_seconds": 5,
        "works": works,
        **overrides,
    }
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(manifest))
    return path


ROBOTS = b"User-agent: *\nDisallow: /search\nDisallow: /eli/etat/adm/\nDisallow: /*.docx\n"


def test_connector_reads_the_cache_offline_in_chronological_order(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    _put(cache, ROBOTS_URL, ROBOTS, "text/plain")
    for day, name in (("2009-01-01", "constitution-20090101"), ("2009-03-16", "constitution-20090316")):
        _put(cache, file_url(CONSTITUTION, day), _read(name))
    manifest = _manifest(
        tmp_path, [{"path": CONSTITUTION, "consolidations": ["2009-01-01", "2009-03-16", "2010-01-01"]}]
    )
    connector = LegiluxConnector(manifest, "lu-legilux", cache, offline=True)
    with pytest.raises(FetchError, match="offline and not cached"):  # the third file is not in the cache
        list(connector.fetch())
    connector = LegiluxConnector(
        _manifest(tmp_path, [{"path": CONSTITUTION, "consolidations": ["2009-01-01", "2009-03-16"]}]),
        "lu-legilux",
        cache,
        offline=True,
    )
    docs = list(connector.fetch())
    assert [d.metadata["consolidation"] for d in docs] == ["2009-01-01", "2009-03-16"]
    assert docs[0].external_id == f"legilux:{CONSTITUTION}@2009-01-01"
    # The stored raw document carries the manifest's consolidation date and the file's text exactly as received.
    assert docs[0].content == _bundle(CONSTITUTION, "2009-01-01", "constitution-20090101")
    assert json.loads(docs[0].content)["xml"].encode("utf-8") == _read("constitution-20090101")
    assert docs[0].uri is not None and docs[0].uri.startswith(
        "https://legilux.public.lu/filestore/"
    )  # never data.legilux
    assert docs[0].retrieved_at == datetime(2026, 10, 14, 10, 0, tzinfo=UTC) and connector.fetch_errors == []


def test_connector_refuses_robots_disallowed_urls_and_bad_answers(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    _put(cache, ROBOTS_URL, b"User-agent: *\nDisallow: /filestore/eli/etat/leg/code/\n", "text/plain")
    manifest = _manifest(tmp_path, [{"path": "code/civil", "consolidations": ["2025-12-26"]}])
    with pytest.raises(FetchError, match="disallowed by"):
        list(LegiluxConnector(manifest, "lu-legilux", cache, offline=True).fetch())
    # An HTML answer (the website's shell) is not ingested and is reported, not fatal.
    cache2 = tmp_path / "cache2"
    _put(cache2, ROBOTS_URL, ROBOTS, "text/plain")
    _put(cache2, file_url(CONSTITUTION, "2009-01-01"), b"<!DOCTYPE html><html/>", "text/html;charset=UTF-8")
    connector = LegiluxConnector(
        _manifest(tmp_path, [{"path": CONSTITUTION, "consolidations": ["2009-01-01"]}]),
        "lu-legilux",
        cache2,
        offline=True,
    )
    assert list(connector.fetch()) == [] and "not XML" in connector.fetch_errors[0]


def test_connector_validates_the_manifest(tmp_path: Path) -> None:
    def run(**kw: Any) -> None:
        works = kw.pop("works", [{"path": "code/civil", "consolidations": ["2025-12-26"]}])
        list(LegiluxConnector(_manifest(tmp_path, works, **kw), "lu-legilux", tmp_path, offline=True).fetch())

    with pytest.raises(ManifestError, match="user_agent"):
        run(user_agent="bot")
    with pytest.raises(ManifestError, match="source_id"):
        run(source_id="other")
    with pytest.raises(ManifestError, match="invalid work path"):
        run(works=[{"path": "../x", "consolidations": ["2025-12-26"]}])
    with pytest.raises(ManifestError, match="invalid work path"):
        run(works=[{"path": "adm/pa/2017", "consolidations": ["2025-12-26"]}])
    with pytest.raises(ManifestError, match="oldest first"):
        run(works=[{"path": "code/civil", "consolidations": ["2025-12-26", "2025-04-20"]}])
    with pytest.raises(ManifestError, match="listed twice"):
        run(
            works=[
                {"path": "code/civil", "consolidations": ["2025-04-20"]},
                {"path": "code/civil", "consolidations": ["2025-12-26"]},
            ]
        )


def test_source_is_registered_and_approved(catalog: Catalog) -> None:
    source = catalog.sources.get("lu-legilux")
    licence_gate(source)
    assert source.status == "active" and source.licence.id == "CC-BY-4.0" and source.languages == ["fr"]
    assert source.authority == "official_non_authentic" and source.licence.attribution_required is True
    assert CONNECTORS[source.connector or ""] is LegiluxConnector and source.parser in PARSERS
    assert "data.legilux.public.lu" in (source.rate_limits or "") and "/filestore/*" in (source.rate_limits or "")
    profile = catalog.jurisdictions.get("LU")
    assert profile.verification.status == "draft_unverified" and profile.coverage.corpus_status == "partial"
    assert profile.official_languages == ["lb", "fr", "de"] and profile.publication.official_sources == ["lu-legilux"]
    assert "Only consolidations with an XML file" in " ".join(profile.coverage.known_gaps)


# --------------------------------------------------------------------------- database


class _Docs:
    def __init__(self, docs: list[RawDocument]):
        self.docs = docs
        self.fetch_errors: list[str] = []

    def fetch(self):  # type: ignore[no-untyped-def]
        yield from self.docs


def _raw(path: str, day: str, name: str) -> RawDocument:
    return RawDocument(
        external_id=f"legilux:{path}@{day}",
        uri=file_url(path, day),
        media_type="application/json",
        content=_bundle(path, day, name),
        retrieved_at=datetime(2026, 10, 14, tzinfo=UTC),
        metadata={"consolidation": day, "language": "fr"},
    )


@pytest.fixture(scope="module")
def lu_ingested(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, ingested: IngestionReport
) -> IngestionReport:
    docs = [
        _raw(CONSTITUTION, "2009-01-01", "constitution-20090101"),
        _raw(CONSTITUTION, "2009-03-16", "constitution-20090316"),
        _raw("code/civil", "2025-04-20", "code-civil-20250420-excerpt"),
        _raw("code/civil", "2025-12-26", "code-civil-20251226-excerpt"),
        _raw(LOI, "2025-12-26", "loi-2018-08-01-a686-20251226"),
    ]
    with session_factory() as session:
        report = Ingestor(session, catalog, store).ingest_source("lu-legilux", actor="pytest", connector=_Docs(docs))
        session.commit()
    return report


def _search(session: Session, catalog: Catalog, **kw: Any) -> dict[str, Any]:
    return search(session, catalog, SearchRequest(jurisdiction="LU", **kw), today=date(2026, 10, 14))


@pytest.mark.db
def test_ingestion_continues_unchanged_wording(session: Session, lu_ingested: IngestionReport) -> None:
    assert lu_ingested.errors == [] and lu_ingested.conflicts == []
    assert lu_ingested.documents_new == 5 and lu_ingested.instruments_created == 3
    assert lu_ingested.provision_versions_corroborated > 150  # the Constitution's 170 unchanged articles
    versions = session.scalars(
        select(m.ProvisionVersion)
        .join(m.Provision)
        .where(m.Provision.uri == "/lu/code/civil/~art_1382", m.ProvisionVersion.language == "fr")
    ).all()
    assert [(v.valid_from, v.valid_to) for v in versions] == [(date(2025, 4, 20), None)]


@pytest.mark.db
def test_point_in_time_follows_the_consolidations(
    session: Session, catalog: Catalog, lu_ingested: IngestionReport
) -> None:
    uri = "/lu/constitution/1868/10/17/n1/~art_34"
    before = provision_at(session, catalog, uri, date(2009, 2, 1), language="fr")
    after = provision_at(session, catalog, uri, date(2009, 4, 1), language="fr")
    assert before["status_on_date"] == "in_force" and "sanctionne et promulgue" in before["version"]["text"]
    assert before["version"]["valid_from"] == "2009-01-01" and before["version"]["valid_to"] == "2009-03-16"
    assert after["status_on_date"] == "in_force" and after["version"]["valid_from"] == "2009-03-16"
    assert after["version"]["text"] == "Le Grand-Duc promulgue les lois dans les trois mois du vote de la Chambre."
    assert after["provision"]["citation"] == "Art. 34 de la Constitution du Grand-Duché de Luxembourg"
    before_art86 = provision_at(session, catalog, "/lu/code/civil/~art_86", date(2025, 6, 1), language="fr")
    after_art86 = provision_at(session, catalog, "/lu/code/civil/~art_86", date(2026, 1, 15), language="fr")
    assert "voyage en mer" in before_art86["version"]["text"] and "voyage de mer" in after_art86["version"]["text"]
    assert after_art86["version"]["valid_from"] == "2025-12-26"
    gap = provision_at(session, catalog, "/lu/code/civil/~art_86", date(2020, 1, 1), language="fr")
    assert gap["status_on_date"] == "history_not_indexed"  # the earliest ingested consolidation is 2025-04-20
    repealed = provision_at(session, catalog, "/lu/code/civil/~art_12", date(2025, 6, 1), language="fr")
    assert repealed["status_on_date"] == "repealed" and repealed["version"]["text"] == ""


@pytest.mark.db
def test_search_citations_and_limitations(session: Session, catalog: Catalog, lu_ingested: IngestionReport) -> None:
    res = _search(
        session,
        catalog,
        q="Tout fait quelconque de l homme qui cause à autrui un dommage oblige celui par la faute duquel",
        as_of=date(2026, 1, 15),
        query_language="fr",
    )
    hit = res["results"][0]
    assert hit["citation"] == "Art. 1382 du Code civil" and hit["text"].startswith("Tout fait quelconque de l'homme")
    codes = {lim["code"] for lim in hit["limitations"]}
    assert {"OFFICIAL_CONSOLIDATION_REPRODUCED", "SOURCE_NOT_AUTHENTIC", "PROFILE_UNVERIFIED"} <= codes
    assert hit["source"]["attribution"].startswith("Source : Legilux")
    law = _search(
        session,
        catalog,
        q="organisation de la Commission nationale pour la protection des données",
        as_of=date(2026, 1, 15),
        query_language="fr",
    )
    assert any(
        h["citation"].endswith(
            "de la loi du 1er août 2018 portant organisation de la Commission nationale "
            "pour la protection des données et du régime général sur la protection des données"
        )
        for h in law["results"]
    )
    old = _search(
        session, catalog, q="Le Grand-Duc sanctionne et promulgue les lois", as_of=date(2009, 2, 1), query_language="fr"
    )
    assert any(h["citation"] == "Art. 34 de la Constitution du Grand-Duché de Luxembourg" for h in old["results"])

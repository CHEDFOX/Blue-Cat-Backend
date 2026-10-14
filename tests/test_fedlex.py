"""Fedlex parser, connector and Swiss search behaviour (real SR consolidations, Federal Chancellery).

Fixtures are unmodified files from www.fedlex.admin.ch/filestore/ (see tests/fixtures/fedlex/README.md),
stored gzip-compressed with the fetcher's metadata.
"""

from __future__ import annotations

import gzip
import json
import re
import shutil
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ulis.catalog import Catalog
from ulis.db import models as m
from ulis.ingestion.pipeline import IngestionReport, Ingestor
from ulis.jurisdictions.modules import ProvisionCitationInput, get_module
from ulis.retrieval.lookup import provision_at
from ulis.retrieval.search import SearchRequest, search
from ulis.settings import REPO_ROOT
from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.fedlex import FedlexConnector, FetchError, public_url
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.parsers.fedlex import FedlexParseError, parse_fedlex, repeal_date, resolve_uri, skipped_blocks
from ulis.storage.object_store import LocalObjectStore

FIX = REPO_ROOT / "tests" / "fixtures" / "fedlex" / "cache"
STORE = "www.fedlex.admin.ch/filestore/fedlex.data.admin.ch/eli/cc"
DATA = "https://fedlex.data.admin.ch/filestore/fedlex.data.admin.ch/eli/cc"
FILES = {
    ("bv", "2024-03-03"): "1999/404/20240303/{l}/xml/fedlex-data-admin-ch-eli-cc-1999-404-20240303-{l}-xml-10.xml",
    ("dsg", "2023-09-01"): "2022/491/20230901/{l}/xml/fedlex-data-admin-ch-eli-cc-2022-491-20230901-{l}-xml-13.xml",
    ("dsg", "2025-04-01"): "2022/491/20250401/{l}/xml/fedlex-data-admin-ch-eli-cc-2022-491-20250401-{l}-xml-1.xml",
    ("vwvg", "2022-07-01"): "1969/737_757_755/20220701/{l}/xml/"
    "fedlex-data-admin-ch-eli-cc-1969-737_757_755-20220701-{l}-xml-15.xml",
    (
        "zgb",
        "2026-07-01",
    ): "24/233_245_233/20260701/{l}/xml/fedlex-data-admin-ch-eli-cc-24-233_245_233-20260701-{l}-xml-2.xml",
}
ELI = {"bv": "1999/404", "dsg": "2022/491", "vwvg": "1969/737_757_755"}


def _read(work: str, when: str, lang: str) -> bytes:
    path = FIX / STORE / FILES[(work, when)].format(l=lang)
    return gzip.decompress(path.with_name(path.name + ".gz").read_bytes())


def _doc(work: str, when: str, lang: str):  # type: ignore[no-untyped-def]
    docs = parse_fedlex(_read(work, when, lang))
    assert len(docs) == 1
    return docs[0]


def _by_eid(doc) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    return {p.eid: p for p in doc.provisions}


def _cite(doc, eid: str) -> str:  # type: ignore[no-untyped-def]
    p = _by_eid(doc)[eid]
    return get_module("ch").provision_citation(
        ProvisionCitationInput(
            doc.short_title, doc.instrument_type, "CH", p.top_kind, p.num_path, doc.language, ext=doc.ext, eid=p.eid
        )
    )


# --------------------------------------------------------------------------- parser


def test_constitution_in_three_equally_authentic_languages() -> None:
    de, fr, it = (_doc("bv", "2024-03-03", lang) for lang in ("de", "fr", "it"))
    assert (de.jurisdiction_id, de.instrument_type, de.work_uri) == ("CH", "constitution", "/ch/cc/1999/404")
    assert de.expression_uri == "/ch/cc/1999/404/de@2024-03-03" and fr.work_uri == it.work_uri == de.work_uri
    assert de.text_origin == "official_consolidation" and all(d.authoritative for d in (de, fr, it))
    assert de.validity_basis == "consolidation_series" and de.consolidation_kind == "point_in_time"
    assert (de.version_date, de.work_date, de.number) == (date(2024, 3, 3), date(1999, 4, 18), "101")
    assert de.ext == {
        "sr_number": "101",
        "eli": "https://fedlex.data.admin.ch/eli/cc/1999/404",
        "entry_into_force": "2000-01-01",
    }
    assert (de.short_title, fr.short_title, it.short_title) == ("BV", "Cst.", "Cost.")
    # Language-neutral identifiers: the three versions share their provisions.
    assert {p.eid for p in de.provisions} == {p.eid for p in fr.provisions} == {p.eid for p in it.provisions}
    assert _by_eid(de)["art_8/para_1"].text == "Alle Menschen sind vor dem Gesetz gleich."
    assert _by_eid(fr)["art_8/para_1"].text == "Tous les êtres humains sont égaux devant la loi."
    assert _by_eid(it)["art_8/para_1"].text == "Tutti sono uguali davanti alla legge."
    assert _cite(de, "art_8/para_1") == "Art. 8 Abs. 1 BV"
    assert _cite(fr, "art_8/para_1") == "art. 8 al. 1 Cst."
    assert _cite(it, "art_8/para_1") == "art. 8 cpv. 1 Cost."
    assert _cite(de, "art_10a") == "Art. 10a BV" and _by_eid(de)["art_10a"].heading.startswith("Verbot der Verhüllung")
    # Every in-force provision is dated from the consolidation.
    assert {p.valid_from for p in de.provisions if p.status == "in_force"} == {date(2024, 3, 3)}


def test_footnotes_are_notes_and_repeals_carry_the_stated_date() -> None:
    de = _by_eid(_doc("bv", "2024-03-03", "de"))
    art_10a = de["art_10a"]
    assert "Volksabstimmung vom 7. März 2021" in (art_10a.note or "")
    assert "Volksabstimmung" not in art_10a.text  # the footnote is not law text
    repealed = de["art_42/para_2"]
    assert repealed.status == "repealed" and repealed.text == ""
    assert repealed.valid_from == date(2008, 1, 1)  # "Aufgehoben ... mit Wirkung seit 1. Jan. 2008"
    assert "Aufgehoben in der Volksabstimmung vom 28. Nov. 2004" in (repealed.note or "")
    # The article itself stays in force; its composite text shows the repealed paragraph as printed.
    assert de["art_42"].status == "in_force" and "2 …" in de["art_42"].text


def test_unnumbered_subdivisions_and_subject_index() -> None:
    doc = _doc("bv", "2024-03-03", "de")
    art_197 = _by_eid(doc)["art_197"]
    assert art_197.is_leaf and "Beitritt der Schweiz zur UNO" in art_197.text  # one provision
    assert not any(p.eid.startswith("art_197/") for p in doc.provisions)
    skipped = skipped_blocks(_read("bv", "2024-03-03", "de"))
    assert any("Sachverzeichnis" in s for s in skipped)  # the subject index is not law
    assert any("Schlussbestimmungen" in s for s in skipped)


def test_letters_numbers_and_annexes_in_three_languages() -> None:
    de, fr, it = (_doc("dsg", "2025-04-01", lang) for lang in ("de", "fr", "it"))
    assert de.instrument_type == "federal_act" and de.number == "235.1"
    assert _cite(de, "art_5/lbl_c/lbl_1") == "Art. 5 lit. c Ziff. 1 DSG"
    assert _cite(fr, "art_5/lbl_c/lbl_1") == "art. 5 let. c ch. 1 LPD"
    assert _cite(it, "art_5/lbl_c/lbl_1") == "art. 5 lett. c n. 1 LPD"
    assert _cite(de, "art_24/para_5bis") == "Art. 24 Abs. 5bis DSG"
    assert _cite(fr, "annex_1") == "annexe 1 LPD" and _cite(it, "annex_1") == "allegato 1 LPD"
    c = _by_eid(de)["art_5/lbl_c"]
    assert c.text.startswith("besonders schützenswerte Personendaten:\n1. Daten über religiöse")
    assert not c.is_leaf and c.has_own_content  # indexed itself, and each number too
    assert _by_eid(de)["art_5"].text.startswith("In diesem Gesetz bedeuten:\na. Personendaten:")


def test_grouped_and_repealed_articles_share_keys_across_languages() -> None:
    docs = {lang: _by_eid(_doc("vwvg", "2022-07-01", lang)) for lang in ("de", "fr", "it")}
    for lang, provs in docs.items():
        # Printed "Art. 71a–71d", "Art. 71a et 71d", "Art. 71a a 71d": one key in every language.
        assert provs["art_71a-71d"].status == "repealed", lang
        assert provs["art_71a-71d"].valid_from == date(2007, 1, 1), lang
        assert provs["art_47a"].valid_from == date(2007, 1, 1), lang
    assert docs["it"]["art_47a"].note and "con effetto dal 1° gen. 2007" in docs["it"]["art_47a"].note


def test_civil_code_final_title_and_marginal_headings() -> None:
    doc = _doc("zgb", "2026-07-01", "de")
    p = _by_eid(doc)
    assert p["art_1"].heading == "A. Anwendung des Rechts"  # the marginal title (Randtitel)
    assert _cite(doc, "art_2/para_2") == "Art. 2 Abs. 2 ZGB"
    assert _cite(doc, "final_title/art_1") == "Art. 1 SchlT ZGB"
    assert p["final_title/art_1"].text.startswith("1 Die rechtlichen Wirkungen von Tatsachen")
    assert get_module("ch").provision_citation(
        ProvisionCitationInput("CC", "federal_act", "CH", "article", ["final_title", "1"], "fr")
    ) == ("art. 1 Tit. fin. CC")
    assert any("Wortlaut der früheren Bestimmungen" in s for s in skipped_blocks(_read("zgb", "2026-07-01", "de")))


def test_repeal_footnote_dates() -> None:
    assert repeal_date(
        "Aufgehoben durch Ziff. I des BG vom 26. Sept. 2014, mit Wirkung seit 1. Jan. 2016 (AS 2015 3977)."
    ) == date(2016, 1, 1)
    assert repeal_date("Abrogé par le ch. I de la LF du 26 sept. 2014, avec effet au 1er janv. 2016.") == date(
        2016, 1, 1
    )
    assert repeal_date("Abrogato dalla cifra I n. 1 della LF del 19 giu. 2015, con effetto 1° gen. 2018.") == date(
        2018, 1, 1
    )
    assert repeal_date(
        "Eingefügt durch ... (AS 2013 851). Aufgehoben durch ..., mit Wirkung seit seit 1. Jan. 2001."
    ) == date(2001, 1, 1)
    assert (
        repeal_date("Abrogati dalla cifra I n. 1 della LF, con effetto dal 1° gen. (RU 2012 6679).") is None
    )  # no year
    assert repeal_date("Fassung gemäss Ziff. I des BG vom 26. Sept. 2014, in Kraft seit 1. Jan. 2016.") is None


def test_unsupported_documents_are_rejected_loudly() -> None:
    with pytest.raises(FedlexParseError, match="unsafe"):
        parse_fedlex(b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e "x">]><x>&e;</x>')
    with pytest.raises(FedlexParseError, match="root"):
        parse_fedlex(b"<Statute/>")
    content = _read("dsg", "2025-04-01", "de")
    with pytest.raises(FedlexParseError, match="language"):
        parse_fedlex(content.replace(b'<FRBRlanguage language="de"/>', b'<FRBRlanguage language="rm"/>'))
    ordinance = content.replace(b"Die Bundesversammlung der Schweizerischen Eidgenossenschaft", b"Der Bundesrat")
    with pytest.raises(FedlexParseError, match="only the Federal Constitution and federal acts"):
        parse_fedlex(ordinance)


def test_resolve_uri_and_public_mapping() -> None:
    assert resolve_uri("https://fedlex.data.admin.ch/eli/cc/1999/404") == ("/ch/cc/1999/404", None)
    assert resolve_uri("https://www.fedlex.admin.ch/eli/cc/24/233_245_233/") == ("/ch/cc/24/233_245_233", None)
    assert resolve_uri("https://fedlex.data.admin.ch/eli/oc/2015/3977") is None
    url = f"{DATA}/2022/491/20250401/de/xml/fedlex-data-admin-ch-eli-cc-2022-491-20250401-de-xml-1.xml"
    assert public_url(url).startswith("https://www.fedlex.admin.ch/filestore/fedlex.data.admin.ch/eli/cc/2022/491/")
    with pytest.raises(ManifestError):
        public_url("https://example.org/filestore/x.xml")


# --------------------------------------------------------------------------- connector


def _cache_copy(dest: Path) -> Path:
    """Copy the fixture cache and decompress it into PoliteHttp's cache layout."""
    shutil.copytree(FIX, dest)
    for gz in dest.rglob("*.xml.gz"):
        gz.with_suffix("").write_bytes(gzip.decompress(gz.read_bytes()))
        gz.unlink()
    return dest


def _manifest(tmp_path: Path, works: list[tuple[str, list[str]]], **overrides: Any) -> Path:
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "source_id": "ch-fedlex",
        "user_agent": "ULIS-test (+https://example.org)",
        "crawl_delay_seconds": 5,
        "languages": ["de", "fr", "it"],
        "works": [
            {
                "sr": work,
                "eli": f"https://fedlex.data.admin.ch/eli/cc/{ELI[work]}",
                "consolidations": [
                    {
                        "date": when,
                        "files": {lang: f"{DATA}/" + FILES[(work, when)].format(l=lang) for lang in ("de", "fr", "it")},
                    }
                    for when in dates
                ],
            }
            for work, dates in works
        ],
        **overrides,
    }
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(manifest, allow_unicode=True))
    return path


def test_connector_reads_the_cache_offline_in_chronological_order(tmp_path: Path) -> None:
    cache = _cache_copy(tmp_path / "cache")
    manifest = _manifest(tmp_path, [("dsg", ["2023-09-01", "2025-04-01"])])
    docs = list(FedlexConnector(manifest, "ch-fedlex", cache, offline=True).fetch())
    assert [d.metadata["consolidation"] for d in docs] == ["2023-09-01"] * 3 + ["2025-04-01"] * 3
    assert [d.metadata["language"] for d in docs[:3]] == ["de", "fr", "it"]
    assert all(d.uri and d.uri.startswith("https://www.fedlex.admin.ch/filestore/") for d in docs)  # public mapping
    assert docs[0].external_id == "fedlex:cc/2022/491@2023-09-01:de"
    meta = json.loads(next((cache / STORE / "2022/491/20230901/de/xml").glob("*.meta.json")).read_text())
    assert docs[0].retrieved_at.isoformat() == meta["retrieved_at"]


def test_connector_requires_identification_order_and_cache(tmp_path: Path) -> None:
    with pytest.raises(FetchError, match="offline and not cached"):
        list(
            FedlexConnector(_manifest(tmp_path, [("dsg", ["2025-04-01"])]), "ch-fedlex", tmp_path, offline=True).fetch()
        )
    with pytest.raises(ManifestError, match="user_agent"):
        list(
            FedlexConnector(
                _manifest(tmp_path, [("dsg", ["2025-04-01"])], user_agent="python"), "ch-fedlex", tmp_path
            ).fetch()
        )
    with pytest.raises(ManifestError, match="oldest first"):
        list(
            FedlexConnector(_manifest(tmp_path, [("dsg", ["2025-04-01", "2023-09-01"])]), "ch-fedlex", tmp_path).fetch()
        )
    with pytest.raises(ManifestError, match="source_id"):
        list(FedlexConnector(_manifest(tmp_path, [("dsg", ["2025-04-01"])]), "other", tmp_path).fetch())


# --------------------------------------------------------------------------- PostgreSQL


@pytest.fixture(scope="module")
def ch_ingested(
    session_factory: sessionmaker[Session],
    catalog: Catalog,
    store: LocalObjectStore,
    ingested: IngestionReport,
    tmp_path_factory: pytest.TempPathFactory,
) -> IngestionReport:
    tmp = tmp_path_factory.mktemp("ch")
    cache = _cache_copy(tmp / "cache")
    manifest = _manifest(tmp, [("bv", ["2024-03-03"]), ("dsg", ["2023-09-01", "2025-04-01"]), ("vwvg", ["2022-07-01"])])
    with session_factory() as session:
        report = Ingestor(session, catalog, store).ingest_source(
            "ch-fedlex", actor="pytest", connector=FedlexConnector(manifest, "ch-fedlex", cache, offline=True)
        )
        session.commit()
    return report


def _search(session: Session, catalog: Catalog, **kw: Any) -> dict[str, Any]:
    return search(session, catalog, SearchRequest(jurisdiction="CH", **kw), today=date(2026, 10, 14))


@pytest.mark.db
def test_ingestion_continues_unchanged_wording(session: Session, ch_ingested: IngestionReport) -> None:
    assert ch_ingested.errors == [] and ch_ingested.conflicts == []
    assert ch_ingested.documents_new == 12 and ch_ingested.instruments_created == 3
    # The DSG consolidation of 2025-04-01 repeats every provision of 2023-09-01 except Art. 24.
    assert ch_ingested.provision_versions_corroborated > 1400
    versions = session.scalars(
        select(m.ProvisionVersion)
        .join(m.Provision)
        .where(m.Provision.uri == "/ch/cc/2022/491/~art_1", m.ProvisionVersion.language == "de")
    ).all()
    assert [(v.valid_from, v.valid_to) for v in versions] == [(date(2023, 9, 1), None)]


@pytest.mark.db
def test_point_in_time_follows_the_consolidations(
    session: Session, catalog: Catalog, ch_ingested: IngestionReport
) -> None:
    uri = "/ch/cc/2022/491/~art_24/para_5bis"
    before = provision_at(session, catalog, uri, date(2024, 6, 1), language="fr")
    after = provision_at(session, catalog, uri, date(2025, 5, 1), language="fr")
    assert before["status_on_date"] == "not_yet_in_force"  # inserted from the 2025-04-01 consolidation
    assert after["status_on_date"] == "in_force" and after["version"]["valid_from"] == "2025-04-01"
    assert after["provision"]["citation"] == "art. 24 al. 5bis LPD"
    art24_old = provision_at(session, catalog, "/ch/cc/2022/491/~art_24", date(2024, 6, 1), language="de")
    art24_new = provision_at(session, catalog, "/ch/cc/2022/491/~art_24", date(2025, 5, 1), language="de")
    assert "5bis" not in art24_old["version"]["text"] and "5bis" in art24_new["version"]["text"]
    assert art24_old["version"]["valid_to"] == "2025-04-01" and art24_old["version"]["valid_to_basis"] == "derived"
    # Before the earliest indexed BV consolidation, an earlier wording may have applied.
    gap = provision_at(session, catalog, "/ch/cc/1999/404/~art_8/para_1", date(2010, 1, 1), language="it")
    assert gap["status_on_date"] == "history_not_indexed"
    repealed = provision_at(session, catalog, "/ch/cc/1969/737_757_755/~art_47a", date(2020, 1, 1), language="de")
    assert repealed["status_on_date"] == "repealed" and repealed["version"]["valid_from"] == "2007-01-01"


@pytest.mark.db
def test_search_citations_and_limitations(session: Session, catalog: Catalog, ch_ingested: IngestionReport) -> None:
    res = _search(
        session, catalog, q="Alle Menschen sind vor dem Gesetz gleich", as_of=date(2026, 1, 1), query_language="de"
    )
    hit = res["results"][0]
    assert hit["citation"] == "Art. 8 Abs. 1 BV" and hit["text"] == "Alle Menschen sind vor dem Gesetz gleich."
    codes = {lim["code"] for lim in hit["limitations"]}
    assert {
        "OFFICIAL_CONSOLIDATION_REPRODUCED",
        "SOURCE_NOT_AUTHENTIC",
        "PROFILE_UNVERIFIED",
        "EQUALLY_AUTHENTIC_VERSIONS",
        "AS_OF_AFTER_CURRENCY",
    } <= codes
    assert "NOT_AUTHENTIC_TEXT" not in codes
    assert hit["source"]["attribution"].startswith("Dies ist keine amtliche Veröffentlichung.")
    fr = _search(session, catalog, q="données personnelles sensibles", as_of=date(2025, 5, 1), query_language="fr")
    assert any(h["citation"] == "art. 5 let. c LPD" and h["version"]["language"] == "fr" for h in fr["results"])
    it = _search(
        session, catalog, q="provvedimenti delle autorità nel singolo caso", as_of=date(2025, 5, 1), query_language="it"
    )
    assert any(re.fullmatch(r"art\. 5 cpv\. 1 PA", h["citation"]) for h in it["results"])


class _Docs:
    def __init__(self, docs: list[RawDocument]):
        self.docs = docs

    def fetch(self):  # type: ignore[no-untyped-def]
        yield from self.docs


@pytest.mark.db
def test_provision_absent_from_a_later_consolidation_ends_there(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, ingested: IngestionReport
) -> None:
    """A provision dropped from a later consolidation (renumbered or restructured) stops applying on
    that consolidation's date. Simulated on a copy of the DSG under another work identifier."""
    first = _read("dsg", "2025-04-01", "de").replace(b"eli/cc/2022/491", b"eli/cc/2022/9491")
    later = re.sub(rb'<paragraph eId="art_24/para_5_bis">.*?</paragraph>', b"", first, count=1, flags=re.S)
    later = later.replace(b"/20250401", b"/20250501").replace(b'date="2025-04-01"', b'date="2025-05-01"')
    assert later != first
    now = datetime(2026, 10, 14, tzinfo=UTC)
    docs = [
        RawDocument(f"test:dsg-9491@{d}", f"https://example.org/{d}", "application/akn+xml", c, now, {})
        for d, c in (("2025-04-01", first), ("2025-05-01", later))
    ]
    with session_factory() as session:
        report = Ingestor(session, catalog, store).ingest_source("ch-fedlex", actor="pytest", connector=_Docs(docs))
        session.commit()
    assert report.errors == [] and report.conflicts == []
    with session_factory() as session:
        uri = "/ch/cc/2022/9491/~art_24/para_5bis"
        during = provision_at(session, catalog, uri, date(2025, 4, 15), language="de")
        after = provision_at(session, catalog, uri, date(2025, 6, 1), language="de")
        kept = provision_at(session, catalog, "/ch/cc/2022/9491/~art_1", date(2025, 6, 1), language="de")
    assert during["status_on_date"] == "in_force" and during["version"]["valid_to"] == "2025-05-01"
    assert during["version"]["valid_to_basis"] == "explicit"
    assert after["status_on_date"] == "no_version"
    assert kept["status_on_date"] == "in_force" and kept["version"]["valid_from"] == "2025-04-01"

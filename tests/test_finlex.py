"""Finlex parser, fetcher and Finnish search behaviour (real Finlex open data responses, CC BY 4.0)."""

from __future__ import annotations

import gzip
import json
import shutil
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from defusedxml.ElementTree import fromstring
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
from ulis.sources.connectors.finlex import FetchError, FinlexConnector, history_version_ids, version_dates
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.parsers.finlex import (
    FinlexParseError,
    _stated_entry_into_force,
    parse_finlex,
    resolve_uri,
    version_id,
)
from ulis.storage.object_store import LocalObjectStore
from ulis.verification.integrity import verify_source_document

CACHE = REPO_ROOT / "tests" / "fixtures" / "finlex" / "cache"
ACTS = CACHE / "opendata.finlex.fi" / "finlex" / "avoindata" / "v1" / "akn" / "fi" / "act" / "statute-consolidated"
PL, TSL, RL = ACTS / "1999" / "731", ACTS / "2018" / "1050", ACTS / "1889" / "39-001"


def _read(path: Path) -> bytes:
    """Fixtures are the API responses as received (gzip-encoded)."""
    return gzip.decompress(path.read_bytes())


def _doc(path: Path):  # type: ignore[no-untyped-def]
    docs = parse_finlex(_read(path))
    assert len(docs) == 1
    return docs[0]


def _by_eid(doc) -> dict[str, list[Any]]:  # type: ignore[no-untyped-def]
    out: dict[str, list[Any]] = {}
    for p in doc.provisions:
        out.setdefault(p.eid, []).append(p)
    return out


def _cite(doc, eid: str) -> str:  # type: ignore[no-untyped-def]
    p = _by_eid(doc)[eid][0]
    return get_module("fi").provision_citation(
        ProvisionCitationInput(
            doc.short_title, "act", "FI", p.top_kind, p.num_path, doc.language, ext=doc.ext, eid=p.eid
        )
    )


@pytest.fixture(scope="module")
def pl_fi():  # type: ignore[no-untyped-def]
    return _doc(PL / "fin@latest")


@pytest.fixture(scope="module")
def pl_sv():  # type: ignore[no-untyped-def]
    return _doc(PL / "swe@latest")


@pytest.fixture(scope="module")
def rl_fi():  # type: ignore[no-untyped-def]
    return _doc(RL / "fin@latest")


@pytest.fixture(scope="module")
def rl_sv():  # type: ignore[no-untyped-def]
    return _doc(RL / "swe@latest")


def test_constitution_consolidation(pl_fi, pl_sv) -> None:  # type: ignore[no-untyped-def]
    assert (pl_fi.jurisdiction_id, pl_fi.work_uri, pl_fi.language) == ("FI", "/fi/act/1999/731", "fi")
    assert pl_fi.expression_uri == "/fi/act/1999/731/fin@20180817"
    # The source marks the consolidated work as not authoritative: revised text, not authentic.
    assert pl_fi.text_origin == "official_revised" and pl_fi.authoritative is False
    assert pl_fi.validity_basis == "source_periods" and pl_fi.consolidation_kind == "point_in_time"
    assert pl_fi.work_date == date(1999, 6, 11)  # dateIssued
    assert pl_fi.version_date == date(2018, 10, 15)  # entry into force of 817/2018, the version's statute
    assert pl_fi.currency_date == date(2018, 10, 5)  # dateConsolidated
    assert pl_fi.ext["statute_number"] == "731/1999" and pl_fi.ext["sections_per_chapter"] is False
    p = {e: v[0] for e, v in _by_eid(pl_fi).items()}
    assert p["chp_2__sec_6__subsec_1"].text == "Ihmiset ovat yhdenvertaisia lain edessä."
    # Original wording: from the Constitution's entry into force. Amended wording: from the amending
    # statute's entry into force, with the source's label kept as a note.
    assert p["chp_1__sec_1__subsec_1"].valid_from == date(2000, 3, 1)
    assert p["chp_1__sec_1__subsec_3"].valid_from == date(2012, 3, 1)
    assert p["chp_1__sec_1__subsec_3"].note == "Wording from 4.11.2011/1112."
    assert p["chp_1__sec_1"].valid_from == date(2012, 3, 1)  # a section changes with its subsections
    # The amendment marker printed in the heading is editorial: not part of the heading.
    assert p["chp_4__sec_53"].heading == "Kansanäänestys ja kansalaisaloite"
    assert p["chp_4__sec_53"].note == "Heading from 4.11.2011/1112."
    assert "(4.11.2011/1112)" not in p["chp_4__sec_53"].text
    assert _cite(pl_fi, "chp_2__sec_6__subsec_2") == "Suomen perustuslaki (731/1999) 6 § 2 mom."
    assert _cite(pl_sv, "chp_2__sec_6__subsec_2") == "Finlands grundlag (731/1999) 6 § 2 mom."
    assert _cite(pl_fi, "chp_2__sec_6") == "Suomen perustuslaki (731/1999) 6 §"
    assert {e.event_type for e in pl_fi.events} == {"entry_into_force", "amendment", "version_change"}


def test_language_versions_share_identifiers(pl_fi, pl_sv) -> None:  # type: ignore[no-untyped-def]
    assert pl_sv.language == "sv" and pl_sv.work_uri == pl_fi.work_uri
    assert pl_sv.expression_uri == "/fi/act/1999/731/swe@20180817"
    assert {(p.eid, p.valid_from) for p in pl_fi.provisions} == {(p.eid, p.valid_from) for p in pl_sv.provisions}
    sv = {p.eid: p for p in pl_sv.provisions}
    assert sv["chp_2__sec_6__subsec_1"].text == "Alla är lika inför lagen."
    fi_tsl, sv_tsl = _doc(TSL / "fin@latest"), _doc(TSL / "swe@latest")
    assert {p.eid for p in fi_tsl.provisions} == {p.eid for p in sv_tsl.provisions}


def test_repealed_subsection_and_earlier_temporal_version() -> None:
    latest, earlier = _doc(TSL / "fin@latest"), _doc(TSL / "fin@20250437")
    assert latest.expression_uri.endswith("/fin@20260380") and earlier.expression_uri.endswith("/fin@20250437")
    assert (earlier.version_date, latest.version_date) == (date(2026, 1, 1), date(2026, 9, 1))
    gone = _by_eid(latest)["chp_3__sec_14__subsec_4"][0]
    assert gone.status == "repealed" and gone.text == "" and gone.valid_from == date(2026, 9, 1)
    assert gone.note == "4 momentti on kumottu L:lla 22.5.2026/380."
    before = _by_eid(earlier)["chp_3__sec_14__subsec_4"][0]
    assert before.status == "in_force" and before.valid_from == date(2019, 1, 1)
    assert before.text.startswith("Tietosuojavaltuutettu akkreditoi")
    # The section's text changes on the repeal; the later version shows it from that date.
    assert [s.valid_from for s in _by_eid(latest)["chp_3__sec_14"]] == [date(2026, 9, 1)]
    assert "akkreditoi" not in _by_eid(latest)["chp_3__sec_14"][0].text
    sv_gone = _by_eid(_doc(TSL / "swe@latest"))["chp_3__sec_14__subsec_4"][0]
    assert sv_gone.status == "repealed" and sv_gone.note == "4 mom. har upphävts genom L 22.5.2026/380."


def test_pending_wording_is_recorded_with_the_wording_it_replaces(rl_fi, rl_sv) -> None:  # type: ignore[no-untyped-def]
    assert rl_fi.work_uri == "/fi/act/1889/39-001" and rl_fi.ext["statute_number"] == "39/1889"
    assert rl_fi.ext["sections_per_chapter"] is True
    # 2 a luku 7 §: amended by 638/2026 from 1 March 2028; the source prints the earlier wording too.
    later, earlier = _by_eid(rl_fi)["chp_2a__sec_7"]
    assert (later.valid_from, later.valid_to) == (date(2028, 3, 1), None)
    assert (earlier.valid_from, earlier.valid_to) == (date(1999, 10, 1), date(2028, 3, 1))
    assert "Aiempi sanamuoto kuuluu:" in (later.note or "")
    assert "päävelvoitetta on kokonaan tai osaksi noudatettu" in earlier.text
    # 6 luku 2 §: a subsection added from 2027 ("lisätty"); the section is recorded as it stands
    # now and from that date.
    assert [v.valid_from for v in _by_eid(rl_fi)["chp_6__sec_2"]] == [date(2018, 1, 1), date(2027, 1, 1)]
    assert _by_eid(rl_fi)["chp_6__sec_2__subsec_2"][0].valid_from == date(2027, 1, 1)
    assert _cite(rl_fi, "chp_21__sec_1") == "Rikoslaki (39/1889) 21 luku 1 §"
    assert _cite(rl_sv, "chp_21__sec_1__subsec_2") == "Strafflag (39/1889) 21 kap. 1 § 2 mom."
    assert _cite(rl_fi, "chp_2a__sec_7__subsec_1__para_1") == "Rikoslaki (39/1889) 2 a luku 7 § 1 mom. 1 kohta"
    assert (
        _cite(rl_fi, "chp_34__sec_4__subsec_1__para_4__subpara_a")
        == "Rikoslaki (39/1889) 34 luku 4 § 1 mom. 4 kohdan a alakohta"
    )
    assert (
        _cite(rl_sv, "chp_34__sec_4__subsec_1__para_4__subpara_a")
        == "Strafflag (39/1889) 34 kap. 4 § 1 mom. 4 punkten underpunkt a"
    )


def test_inconsistent_source_encodings_are_normalised(rl_fi, rl_sv) -> None:  # type: ignore[no-untyped-def]
    fi, sv = _by_eid(rl_fi), _by_eid(rl_sv)
    # 20 luku 3 §: the Finnish XML tags list item "3)" as a subsection; the Swedish one does not.
    assert "chp_20__sec_3__subsec_2__para_3" in fi and "chp_20__sec_3__subsec_4" not in fi
    assert fi["chp_20__sec_3__subsec_3"][0].text == "Yritys on rangaistava."
    assert sv["chp_20__sec_3__subsec_3"][0].text == "Försök är straffbart."
    # 21 luku 6 §: the Swedish XML tags the closing clause and the next subsection as unnumbered
    # "paragraphs"; both versions end up with two subsections.
    assert sv["chp_21__sec_6__subsec_2"][0].text == "Försök är straffbart."
    assert fi["chp_21__sec_6__subsec_1"][0].text.endswith("vähintään yhdeksi ja enintään kymmeneksi vuodeksi.")
    assert len({p.eid for p in rl_fi.provisions} ^ {p.eid for p in rl_sv.provisions}) <= 2


def test_rejects_unsupported_documents() -> None:
    original = _read(PL / "fin@latest").replace(
        b'<FRBRsubtype value="statute-consolidated"/>', b'<FRBRsubtype value="statute"/>', 1
    )
    with pytest.raises(FinlexParseError, match="statute-consolidated"):
        parse_finlex(original)
    with pytest.raises(FinlexParseError, match="unsafe"):
        parse_finlex(b'<!DOCTYPE x [<!ENTITY a "b">]><akomaNtoso/>')
    with pytest.raises(FinlexParseError, match="root"):
        parse_finlex(b"<law/>")


def test_identifiers_and_commencement_sentences() -> None:
    assert version_id(2026, "380") == "20260380" and version_id(1889, "39-001") == "18890039"
    assert resolve_uri("/akn/fi/act/statute-consolidated/1994/750#sec_8a") == ("/fi/act/1994/750", "sec_8a")
    assert resolve_uri("/akn/fi/act/statute-consolidated/1889/39-001#chp_48v19950578__sec_1") == (
        "/fi/act/1889/39-001",
        "chp_48__sec_1",
    )
    assert resolve_uri("/akn/fi/act/statute/2018/817") is None
    ids = history_version_ids(_read(TSL / "fin@latest"), 2018, "1050")
    assert ids[0] == "20181050" and ids[-1] == "20250437" and len(ids) == 8
    # Versions are recorded in the order they took effect: 239/2023 before 1112/2022.
    dates = version_dates(_read(TSL / "fin@latest"))
    assert dates["20230239"] == "2023-03-01" < dates["20221112"] == "2023-06-25"

    def block(text: str):  # type: ignore[no-untyped-def]
        akn = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"
        return fromstring(f'<content xmlns="{akn}"><p>{text}</p></content>')

    # Sentences as printed after the Inheritance Code and the Contracts Act (amending statutes whose
    # metadata lacks the date).
    assert _stated_entry_into_force(block("Tämä laki tulee voimaan 1 päivänä elokuuta 1989.")) == date(1989, 8, 1)
    assert _stated_entry_into_force(
        block("Denna lag träder i kraft den 1 Januari 1983. Den tillämpas även på rättshandlingar.")
    ) == date(1983, 1, 1)
    staged = "Tämä laki tulee voimaan 1 päivänä syyskuuta 1995 lukuun ottamatta 17 luvun 4 §:n 2 momenttia, joka tulee voimaan 1 päivänä lokakuuta 1995."  # noqa: E501
    assert _stated_entry_into_force(block(staged)) is None
    assert _stated_entry_into_force(block("Tämä laki tulee voimaan valtioneuvoston asetuksella.")) is None


# --------------------------------------------------------------------------- connector


def _connector(cache: Path, tmp_path: Path, works: list[dict[str, Any]], **kw: Any) -> FinlexConnector:
    manifest = {
        "schema_version": 1,
        "source_id": "fi-finlex",
        "base_url": "https://opendata.finlex.fi/finlex/avoindata/v1",
        "user_agent": "ULIS-test (+https://example.org)",
        "crawl_delay_seconds": 5,
        "languages": ["fin", "swe"],
        "works": works,
    }
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(manifest))
    return FinlexConnector(path, "fi-finlex", cache, **kw)


def test_connector_offline_history_and_missing_versions(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    shutil.copytree(CACHE, cache)
    tsl = cache / ACTS.relative_to(CACHE) / "2018" / "1050"
    for lang in ("fin", "swe"):  # a temporal version the API does not hold (cached 404)
        (tsl / f"{lang}@20240029").write_bytes(b"")
        (tsl / f"{lang}@20240029.meta.json").write_text(json.dumps({"url": "x", "status": 404}))
    works = [{"year": 2018, "number": "1050", "history": True, "history_versions": ["20240029", "20250437"]}]
    conn = _connector(cache, tmp_path, works, offline=True)
    docs = list(conn.fetch())
    assert [d.external_id for d in docs] == [
        "finlex:statute-consolidated/2018/1050/fin@20250437",
        "finlex:statute-consolidated/2018/1050/fin@20260380",
        "finlex:statute-consolidated/2018/1050/swe@20250437",
        "finlex:statute-consolidated/2018/1050/swe@20260380",
    ]
    assert conn.missing_versions == [
        "statute-consolidated/2018/1050/fin@20240029",
        "statute-consolidated/2018/1050/swe@20240029",
    ]
    latest = docs[1]
    assert (
        latest.uri
        == "https://opendata.finlex.fi/finlex/avoindata/v1/akn/fi/act/statute-consolidated/2018/1050/fin@20260380"
    )
    assert latest.metadata["fetched_url"].endswith("/fin@latest")
    assert latest.content.startswith(b"<akomaNtoso")  # gzip decoded
    meta = json.loads((tsl / "fin@latest.meta.json").read_text())
    assert latest.retrieved_at.isoformat() == meta["retrieved_at"]


def test_connector_requires_identification_and_cache_when_offline(tmp_path: Path) -> None:
    conn = _connector(tmp_path, tmp_path, [{"year": 2018, "number": "1050"}], offline=True)
    with pytest.raises(FetchError, match="offline and not cached"):
        list(conn.fetch())
    path = tmp_path / "manifest.yaml"
    manifest = yaml.safe_load(path.read_text())
    manifest["user_agent"] = "python-urllib"
    path.write_text(yaml.safe_dump(manifest))
    with pytest.raises(ManifestError, match="user_agent"):
        list(FinlexConnector(path, "fi-finlex", tmp_path).fetch())
    manifest["user_agent"] = "ULIS-test (+https://example.org)"
    manifest["works"] = [{"year": 2018, "number": "../x"}]
    path.write_text(yaml.safe_dump(manifest))
    with pytest.raises(ManifestError, match="invalid work"):
        list(FinlexConnector(path, "fi-finlex", tmp_path).fetch())


# --------------------------------------------------------------------------- PostgreSQL

FIXTURE_ORDER = [
    PL / "fin@latest",
    PL / "swe@latest",
    TSL / "fin@20250437",
    TSL / "fin@latest",
    TSL / "swe@20250437",
    TSL / "swe@latest",
    RL / "fin@latest",
    RL / "swe@latest",
]


class _Fixtures:
    def fetch(self) -> Iterator[RawDocument]:
        now = datetime(2026, 10, 14, tzinfo=UTC)
        for path in FIXTURE_ORDER:
            yield RawDocument(
                f"finlex:{path.relative_to(ACTS)}", f"file://{path.name}", "application/akn+xml", _read(path), now, {}
            )


@pytest.fixture(scope="module")
def fi_ingested(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, ingested: IngestionReport
) -> IngestionReport:
    with session_factory() as session:
        report = Ingestor(session, catalog, store).ingest_source("fi-finlex", actor="pytest", connector=_Fixtures())
        session.commit()
    return report


def _search(session: Session, catalog: Catalog, **kw: Any) -> dict[str, Any]:
    return search(session, catalog, SearchRequest(jurisdiction="FI", **kw), today=date(2026, 10, 14))


@pytest.mark.db
def test_bilingual_search_and_limitations(session: Session, catalog: Catalog, fi_ingested: IngestionReport) -> None:
    assert fi_ingested.errors == [] and fi_ingested.conflicts == []
    assert fi_ingested.documents_new == len(FIXTURE_ORDER)
    res = _search(session, catalog, q="yhdenvertaisia lain edessä", as_of=date(2026, 10, 1), query_language="fi")
    hit = res["results"][0]
    assert hit["citation"] == "Suomen perustuslaki (731/1999) 6 § 1 mom."
    assert hit["text"] == "Ihmiset ovat yhdenvertaisia lain edessä."
    codes = {lim["code"] for lim in hit["limitations"]}
    assert {"REVISED_TEXT", "PROFILE_UNVERIFIED", "SOURCE_NOT_AUTHENTIC", "AS_OF_AFTER_CURRENCY"} <= codes
    assert "NOT_AUTHENTIC_TEXT" not in codes  # REVISED_TEXT already says so
    assert hit["source"]["attribution"].startswith("Source: Finlex open data")
    sv = _search(session, catalog, q="lika inför lagen", as_of=date(2026, 10, 1), query_language="sv")
    first = sv["results"][0]
    assert first["citation"] == "Finlands grundlag (731/1999) 6 § 1 mom."
    assert first["version"]["language"] == "sv"


@pytest.mark.db
def test_point_in_time_follows_the_source(session: Session, catalog: Catalog, fi_ingested: IngestionReport) -> None:
    uri = "/fi/act/2018/1050/~chp_3__sec_14__subsec_4"
    before = provision_at(session, catalog, uri, date(2026, 6, 1), language="fi")
    after = provision_at(session, catalog, uri, date(2026, 10, 1), language="fi")
    assert before["status_on_date"] == "in_force" and before["version"]["text"].startswith("Tietosuojavaltuutettu")
    assert before["version"]["valid_to"] == "2026-09-01"
    assert after["status_on_date"] == "repealed"
    assert after["provision"]["citation"] == "Tietosuojalaki (1050/2018) 14 § 4 mom."
    # Criminal Code 2 a:7: the current wording until the amendment of 638/2026 takes effect.
    rl = "/fi/act/1889/39-001/~chp_2a__sec_7"
    now = provision_at(session, catalog, rl, date(2026, 10, 14), language="fi")
    then = provision_at(session, catalog, rl, date(2028, 6, 1), language="fi")
    assert "päävelvoitetta" in now["version"]["text"] and "päävelvoitetta" not in then["version"]["text"]
    assert now["provision"]["citation"] == "Rikoslaki (39/1889) 2 a luku 7 §"
    # Constitution 1 § 3 mom. took its current wording in 2012; the earlier wording is not indexed.
    gap = provision_at(session, catalog, "/fi/act/1999/731/~chp_1__sec_1__subsec_3", date(2010, 1, 1), language="fi")
    assert gap["status_on_date"] == "history_not_indexed"
    kept = provision_at(session, catalog, "/fi/act/1999/731/~chp_2__sec_6__subsec_1", date(2010, 1, 1), language="sv")
    assert kept["status_on_date"] == "in_force" and kept["version"]["text"] == "Alla är lika inför lagen."


@pytest.mark.db
def test_integrity_of_ingested_documents(
    session: Session, catalog: Catalog, store: LocalObjectStore, fi_ingested: IngestionReport
) -> None:
    docs = session.scalars(select(m.SourceDocument).where(m.SourceDocument.source_id == "fi-finlex")).all()
    assert len(docs) == len(FIXTURE_ORDER)
    results = [verify_source_document(session, catalog, store, doc.id) for doc in docs]
    assert all(r["result"] == "pass" for r in results), [r["problems"][:3] for r in results]
    assert all(r["checked_units"] > 0 for r in results)
    # Lifecycle events are recorded once per instrument, from the first document that reports them.
    assert sum(r["checked_events"] for r in results) > 0

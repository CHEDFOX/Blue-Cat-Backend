"""Finlex parser, fetcher and Finnish search behaviour (real Finlex open data responses, CC BY 4.0)."""

from __future__ import annotations

import gzip
import io
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
from ulis.sources.connectors.finlex import (
    API,
    PACKAGE_URL,
    FetchError,
    FinlexConnector,
    PackageChanged,
    history_version_ids,
    version_dates,
)
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.normalized import ParseSkipped
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
# From the widened manifest (2026-10-15): an empty shell, a repealed statute, a decision (not an Act or a decree)
SHELL, REPEALED, DECISION = ACTS / "1932" / "246", ACTS / "2020" / "1175", ACTS / "2024" / "718"


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


def test_shells_and_repealed_statutes_are_skipped_not_errors() -> None:
    # 1932/246 is in force and listed, but the source holds no text for it (a title and metadata, 3.7 KB).
    with pytest.raises(ParseSkipped, match="holds no text"):
        parse_finlex(_read(SHELL / "fin@latest"))
    with pytest.raises(ParseSkipped, match="no longer in force"):
        parse_finlex(_read(REPEALED / "fin@latest"))


def test_statute_types_other_than_acts_and_decrees_are_kept() -> None:
    fi, sv = _doc(DECISION / "fin@latest"), _doc(DECISION / "swe@latest")
    assert (fi.instrument_type, sv.instrument_type) == ("decision", "decision")  # Verohallinnon päätös 718/2024
    assert fi.work_uri == sv.work_uri == "/fi/act/2024/718" and fi.validity_basis == "source_periods"
    assert _cite(fi, "sec_1__subsec_2") == (
        "Verohallinnon päätös autoveroilmoituksella ilmoitettavista tiedoista (718/2024) 1 § 2 mom."
    )
    assert _cite(sv, "sec_1__subsec_2").startswith("Skatteförvaltningens beslut om uppgifter som ska lämnas")


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


def test_committed_manifest_lists_every_statute_in_force_that_has_text(tmp_path: Path) -> None:
    """config/manifests/fi-finlex.yaml is generated by scripts/build_finlex_manifest.py (API list + archive index)."""
    conn = FinlexConnector(REPO_ROOT / "config/manifests/fi-finlex.yaml", "fi-finlex", tmp_path)
    works = conn._manifest()["works"]  # also checks every work's shape and that none is listed twice
    assert len(works) > 5_000
    assert [(w["year"], str(w["number"]), w["history"]) for w in works[:7]] == [
        (1999, "731", False),
        (1889, "39-001", False),
        (1929, "228", False),
        (2018, "1050", True),
        (2003, "434", True),
        (2001, "55", False),
        (1965, "40", False),
    ]
    assert {(w["year"], str(w["number"])) for w in works if w["history"]} == {(2018, "1050"), (2003, "434")}
    assert all(w["languages"] in (["fin"], ["swe"]) for w in works if "languages" in w)
    listed = {f"{w['year']}/{w['number']}" for w in works}
    assert {"1932/244", "2024/718", "2019/416"} <= listed
    assert "1932/246" not in listed  # in force, but a shell without text


class _FakeHttp:
    """PoliteHttp stand-in: serves the cached 2018/1050 documents; other works answer as their number says."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def get(self, url: str, cache_key: str | None = None, headers: Any = None) -> tuple[bytes, dict[str, Any]] | None:
        meta = {"url": url, "retrieved_at": "2026-10-15T00:00:00+00:00", "status": 200}
        if "/2000/403/" in url:
            raise FetchError(f"{url}: HTTP 403 (not retried: a refusal is respected)")
        if "/2000/404/" in url or (url.endswith("/swe@latest") and "/2000/1/" in url):
            return None  # not held (in this language)
        return _read(TSL / "fin@latest"), meta


def test_connector_goes_on_after_a_missing_work_or_language_and_stops_on_a_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("ulis.sources.connectors.finlex.PoliteHttp", _FakeHttp)
    works = [
        {"year": 2000, "number": "404"},  # absent in both languages
        {"year": 2000, "number": "1"},  # Finnish only: the list endpoint did not show a Swedish version
        {"year": 2000, "number": "2", "languages": ["swe"]},  # the work's own languages replace the manifest's
    ]
    conn = _connector(tmp_path, tmp_path, works)
    docs = list(conn.fetch())
    assert [d.metadata["work"] + "/" + d.metadata["language"] for d in docs] == [
        "statute-consolidated/2000/1/fin",
        "statute-consolidated/2000/2/swe",
    ]
    assert conn.fetch_errors == [
        "finlex:statute-consolidated/2000/404/fin: not found (404); skipped",
        "finlex:statute-consolidated/2000/404/swe: not found (404); skipped",
        "finlex:statute-consolidated/2000/1/swe: not found (404); skipped",
    ]
    refused = _connector(tmp_path, tmp_path, [{"year": 2000, "number": "2"}, {"year": 2000, "number": "403"}])
    with pytest.raises(FetchError, match="HTTP 403"):
        list(refused.fetch())
    many = [{"year": 2001, "number": str(n)} for n in range(1, 30)]

    def unavailable(self: Any, url: str, cache_key: str | None = None, headers: Any = None) -> None:
        raise FetchError(f"{url}: HTTP 503 (not retried: a refusal is respected)")

    monkeypatch.setattr(_FakeHttp, "get", unavailable)
    failing = _connector(tmp_path, tmp_path, many)
    with pytest.raises(FetchError, match="20 works failed in a row"):
        list(failing.fetch())
    assert len(failing.fetch_errors) == 20


def test_manifest_rejects_duplicates_and_unknown_languages(tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match="listed twice"):
        _connector(tmp_path, tmp_path, [{"year": 2018, "number": "1050"}, {"year": 2018, "number": "1050"}])._manifest()
    with pytest.raises(ManifestError, match="unsupported language"):
        _connector(tmp_path, tmp_path, [{"year": 2018, "number": "1050", "languages": ["eng"]}])._manifest()


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


@pytest.fixture(scope="module")
def fi_decision_ingested(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, fi_ingested: IngestionReport
) -> IngestionReport:
    class Decision:
        def fetch(self) -> Iterator[RawDocument]:
            now = datetime(2026, 10, 15, tzinfo=UTC)
            for name in ("fin@latest", "swe@latest"):
                path = DECISION / name
                yield RawDocument(
                    f"finlex:{path.relative_to(ACTS)}",
                    f"file://{path.name}",
                    "application/akn+xml",
                    _read(path),
                    now,
                    {},
                )

    with session_factory() as session:
        report = Ingestor(session, catalog, store).ingest_source("fi-finlex", actor="pytest", connector=Decision())
        session.commit()
    return report


@pytest.mark.db
def test_a_decision_is_ingested_and_found_in_both_languages(
    session: Session, catalog: Catalog, fi_decision_ingested: IngestionReport
) -> None:
    assert fi_decision_ingested.errors == [] and fi_decision_ingested.conflicts == []
    assert (fi_decision_ingested.instruments_created, fi_decision_ingested.instrument_versions_created) == (1, 2)
    fi = _search(
        session, catalog, q="Veroilmoituksella ilmoitetaan verotukseen vaikuttavat seikat", query_language="fi"
    )
    assert fi["results"][0]["citation"] == (
        "Verohallinnon päätös autoveroilmoituksella ilmoitettavista tiedoista (718/2024) 1 § 1 mom."
    )
    codes = {lim["code"] for lim in fi["results"][0]["limitations"]}
    assert {"REVISED_TEXT", "SOURCE_NOT_AUTHENTIC"} <= codes
    sv = _search(
        session, catalog, q="I skattedeklarationen anges uppgifter som påverkar beskattningen", query_language="sv"
    )
    assert sv["results"][0]["citation"] == (
        "Skatteförvaltningens beslut om uppgifter som ska lämnas i bilskattedeklarationen (718/2024) 1 § 1 mom."
    )


# --------------------------------------------------------------------------- download package (mode: package)

SAMPLE_ZIP = REPO_ROOT / "tests" / "fixtures" / "finlex" / "package" / "statute-consolidated-sample.zip"
SAMPLE = "akn/fi/act/statute-consolidated"
LATEST_SAME_AS_API = [  # (work, language, package member version): the API's @latest is byte-identical
    ("2024/718", "fin", ""),
    ("2024/718", "swe", ""),
    ("1932/246", "fin", ""),
    ("2020/1175", "fin", "20220521"),
    ("2009/330", "fin", "20250869"),
    ("2009/330", "swe", "20250869"),
]


def _pin(path: Path) -> dict[str, Any]:
    import hashlib

    return {
        "url": PACKAGE_URL,
        "bytes": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "last_modified": "Sat, 17 Oct 2026 23:27:28 GMT",
        "retrieved_at": "2026-10-18T11:57:24.666915+00:00",
    }


def _package_connector(tmp_path: Path, works: list[dict[str, Any]], package: Path | None = SAMPLE_ZIP, **kw: Any):  # type: ignore[no-untyped-def]
    cache = tmp_path / "cache"
    if not cache.exists():
        shutil.copytree(CACHE, cache)
    conn = _connector(cache, tmp_path, works, package_file=package, **kw)
    manifest = yaml.safe_load(conn.manifest_path.read_text())
    manifest.update(mode="package", package=_pin(package or SAMPLE_ZIP))  # no local copy: the download is pinned
    conn.manifest_path.write_text(yaml.safe_dump(manifest))
    return conn


def test_package_route_reads_the_same_documents_as_the_api_route(tmp_path: Path) -> None:
    works = [
        {"year": 2024, "number": "718"},  # only the version as enacted ("fin@") in the package
        {"year": 1932, "number": "246", "languages": ["fin"]},  # a shell
        {"year": 2020, "number": "1175", "languages": ["fin"]},  # "fin@" and two later versions: the latest is 20220521
        {"year": 2009, "number": "330"},  # numbered versions only
    ]
    conn = _package_connector(tmp_path, works, offline=True)
    docs = list(conn.fetch())
    assert [d.external_id.removeprefix("finlex:statute-consolidated/") for d in docs] == [
        "2024/718/fin@20240718",
        "2024/718/swe@20240718",
        "1932/246/fin@19320246",
        "2020/1175/fin@20220521",
        "2009/330/fin@20250869",
        "2009/330/swe@20250869",
    ]
    for doc, (work, lang, version) in zip(docs, LATEST_SAME_AS_API, strict=True):
        member = f"{SAMPLE}/{work}/{lang}@{version}/main.xml"
        assert doc.uri == f"{PACKAGE_URL}#{member}" and doc.metadata["member"] == member
        assert doc.retrieved_at == datetime.fromisoformat("2026-10-18T11:57:24.666915+00:00")
        # The member's main.xml is the API's response for the same statute, byte for byte (fixtures fetched from
        # the API on 2026-10-14/15 and 2026-10-18), so the parser gives the same documents.
        api = _read(ACTS / work / f"{lang}@latest")
        assert doc.content == api
        try:
            expected = parse_finlex(api)
        except ParseSkipped as exc:  # the shell and the statute no longer in force: skipped on both routes
            with pytest.raises(ParseSkipped, match=str(exc).split("(")[0]):
                parse_finlex(doc.content)
        else:
            assert parse_finlex(doc.content) == expected
    assert conn.fetch_errors == [] and conn.package_fallbacks == [] and conn.http is not None
    assert conn.http.requests_made == 0  # nothing was asked of the API
    assert conn.package_meta is not None and conn.package_meta["sha256"] == _pin(SAMPLE_ZIP)["sha256"]


def test_package_route_history_and_what_it_takes_from_the_api(tmp_path: Path) -> None:
    works = [
        # history: the version as enacted is the member "fin@"; 20240029 is not in the sample package, so it is
        # asked of the API (here a cached 404, reported like on the API route)
        {
            "year": 2018,
            "number": "1050",
            "history": True,
            "history_versions": ["20181050", "20240029", "20250437"],
            "languages": ["fin"],
        },
        # the package's latest version is 20250006, but its members' metadata name 871/2026: the API's @latest
        {"year": 2023, "number": "64", "languages": ["fin"]},
        # not in the package at all: the API, as on the API route
        {"year": 1999, "number": "731", "languages": ["swe"]},
    ]
    conn = _package_connector(tmp_path, works, offline=True)
    tsl = tmp_path / "cache" / ACTS.relative_to(CACHE) / "2018" / "1050"
    (tsl / "fin@20240029").write_bytes(b"")
    (tsl / "fin@20240029.meta.json").write_text(json.dumps({"url": "x", "status": 404}))
    docs = list(conn.fetch())
    assert [d.external_id.removeprefix("finlex:statute-consolidated/") for d in docs] == [
        "2018/1050/fin@20181050",
        "2018/1050/fin@20250437",
        "2018/1050/fin@20260380",
        "2023/64/fin@20260871",
        "1999/731/swe@20180817",
    ]
    assert [d.uri.split("#")[-1] for d in docs[:3]] == [
        f"{SAMPLE}/2018/1050/fin@/main.xml",
        f"{SAMPLE}/2018/1050/fin@20250437/main.xml",
        f"{SAMPLE}/2018/1050/fin@20260380/main.xml",
    ]
    # The version as enacted in the package is the API's fin@20181050, byte for byte (fetched 2026-10-18).
    assert docs[0].content == _read(TSL / "fin@20181050")
    assert docs[3].uri.startswith(API) and docs[4].uri.startswith(API)
    assert conn.missing_versions == ["statute-consolidated/2018/1050/fin@20240029"]
    assert conn.package_fallbacks == [
        "finlex:statute-consolidated/2023/64/fin: the package's latest version is 20250006, but its metadata names "
        "20260871; fetched from the API",
        "finlex:statute-consolidated/1999/731/swe: not in the package; fetched from the API",
    ]


def test_package_route_loads_only_the_pinned_package(tmp_path: Path) -> None:
    conn = _package_connector(tmp_path, [{"year": 2024, "number": "718"}], offline=True)
    manifest = yaml.safe_load(conn.manifest_path.read_text())
    for change, message in [
        ({"sha256": "0" * 64}, "not the one the manifest pins"),
        ({"bytes": 1}, "not the one the manifest pins"),
    ]:
        conn.manifest_path.write_text(yaml.safe_dump({**manifest, "package": {**manifest["package"], **change}}))
        with pytest.raises(PackageChanged, match=message):
            list(conn.fetch())
    for change, message in [
        ({"url": "https://example.org/statute-consolidated.zip"}, "url is"),
        ({"sha256": "abc"}, "SHA-256"),
        ({"bytes": "4 GB"}, "bytes"),
        ({"retrieved_at": None}, "retrieved_at"),
    ]:
        conn.manifest_path.write_text(yaml.safe_dump({**manifest, "package": {**manifest["package"], **change}}))
        with pytest.raises(ManifestError, match=message):
            conn._manifest()
    conn.manifest_path.write_text(yaml.safe_dump({**manifest, "mode": "bulk"}))
    with pytest.raises(ManifestError, match="mode"):
        conn._manifest()
    # Offline without a cached package (and no local copy): nothing is downloaded.
    conn.manifest_path.write_text(yaml.safe_dump(manifest))
    no_copy = FinlexConnector(conn.manifest_path, "fi-finlex", tmp_path / "empty", offline=True)
    with pytest.raises(FetchError, match="offline and not cached"):
        list(no_copy.fetch())


def test_api_mode_overrides_the_manifest_for_single_works(tmp_path: Path) -> None:
    conn = _package_connector(tmp_path, [{"year": 2024, "number": "718"}], offline=True, mode="api")
    docs = list(conn.fetch())
    assert [d.uri for d in docs] == [
        f"{API}/akn/fi/act/statute-consolidated/2024/718/fin@20240718",
        f"{API}/akn/fi/act/statute-consolidated/2024/718/swe@20240718",
    ]
    assert conn.package_meta is None  # the package was not opened


def test_a_package_member_that_is_not_its_document_is_reported(tmp_path: Path) -> None:
    import zipfile

    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(SAMPLE_ZIP) as src, zipfile.ZipFile(bad, "w") as dst:
        dst.writestr(f"{SAMPLE}/2024/719/fin@/main.xml", src.read(f"{SAMPLE}/2024/718/fin@/main.xml"))
        dst.writestr(f"{SAMPLE}/2024/718/fin@/main.xml", src.read(f"{SAMPLE}/2024/718/fin@/main.xml"))
    works = [
        {"year": 2024, "number": "719", "languages": ["fin"]},
        {"year": 2024, "number": "718", "languages": ["fin"]},
    ]
    conn = _package_connector(tmp_path, works, package=bad, offline=True)
    docs = list(conn.fetch())
    assert [d.external_id for d in docs] == ["finlex:statute-consolidated/2024/718/fin@20240718"]
    assert len(conn.fetch_errors) == 1
    assert "is not /akn/fi/act/statute-consolidated/2024/719/fin@" in conn.fetch_errors[0]


def test_committed_manifest_loads_the_package_finlex_serves(tmp_path: Path, catalog: Catalog) -> None:
    from ulis.ops.loading import LoadOptions, PlanItem, Selection, format_plan
    from ulis.ops.sources import STATIC_PINS, manifest_info

    manifest = FinlexConnector(REPO_ROOT / "config/manifests/fi-finlex.yaml", "fi-finlex", tmp_path)._manifest()
    assert manifest["mode"] == "package"
    # A fresh server load works unattended: no pin to move, the package's size and SHA-256 are recorded per run.
    assert manifest["package"] == {"url": PACKAGE_URL, "pin": "latest", "approx_bytes": 4_397_198_140}
    # The load plan counts one download, not 11,000 requests; the weekly refresh runs it (one HEAD decides).
    info = manifest_info(catalog.sources.get("fi-finlex"), REPO_ROOT)
    assert info is not None and info.requests == 2 and info.download_bytes == 4_397_198_140
    assert info.weight == info.documents and info.documents is not None and info.documents > 11_000
    assert "www.finlex.fi" in info.data_hosts and "fi-finlex" not in STATIC_PINS
    plan = format_plan(Selection(selected=[PlanItem(catalog.sources.get("fi-finlex"), info)]), LoadOptions())
    assert ">= 2s + 4.1 GB" in plan and "fi-finlex 4.1 GB" in plan


def test_the_package_script_switches_between_latest_and_an_exact_pin(tmp_path: Path) -> None:
    """scripts/finlex_package.py --pin / --live replace the manifest's package block and nothing else."""
    from scripts.finlex_package import _BLOCK, live_block, pin_block

    text = (REPO_ROOT / "config/manifests/fi-finlex.yaml").read_text(encoding="utf-8")
    assert _BLOCK.sub(lambda _m: live_block(4_397_198_140), text, count=1) == text  # the committed block round-trips
    exact = {
        "bytes": 4_397_198_140,
        "sha256": "8c07c5b96f4d5063b841bb156eb204fb8f783c6e96d34c810555200aa7ce3e45",
        "etag": '"0d44183df8d844dd14a81c32ddfaa8e8-525"',
        "last_modified": "Sat, 17 Oct 2026 23:27:28 GMT",
        "retrieved_at": "2026-10-18T11:57:24.666915+00:00",
    }
    pinned_text = _BLOCK.sub(lambda _m: pin_block(exact), text, count=1)
    pinned = yaml.safe_load(pinned_text)
    assert pinned["package"] == {"url": PACKAGE_URL, "pin": "exact", **exact}
    assert pinned["works"] == yaml.safe_load(text)["works"]
    path = tmp_path / "pinned.yaml"
    path.write_text(pinned_text, encoding="utf-8")
    assert FinlexConnector(path, "fi-finlex", tmp_path)._manifest()["package"]["pin"] == "exact"
    assert _BLOCK.sub(lambda _m: live_block(4_397_198_140), pinned_text, count=1) == text


# --------------------------------------------------------------------------- download (fake server, no network)


class _Served(io.BytesIO):
    """A response of the fake server: ``cut`` bytes are sent, then the connection breaks."""

    def __init__(self, data: bytes, status: int, headers: dict[str, str], url: str, cut: int | None = None):
        super().__init__(data)
        self.status, self.headers, self._url, self._cut = status, headers, url, cut

    def read(self, n: int | None = -1) -> bytes:
        if self._cut is not None and self.tell() >= self._cut:
            raise ConnectionResetError("connection reset by peer")
        if self._cut is not None and n is not None and n > 0:
            n = min(n, self._cut - self.tell())
        return super().read(n)

    def geturl(self) -> str:
        return self._url

    def __enter__(self) -> _Served:
        return self

    def __exit__(self, *exc: Any) -> None:
        pass


class _Server:
    """www.finlex.fi as the connector sees it: robots.txt, then the package (with ETag and Range support)."""

    def __init__(self, payload: bytes, *, etag: str = '"v1"', cut: int | None = None, refuse: int | None = None):
        self.payload, self.etag, self.cut, self.refuse = payload, etag, cut, refuse
        self.last_modified = "Sat, 17 Oct 2026 23:27:28 GMT"
        self.rebuilt_after_cut: str | None = None
        self.head_failure: int | None = None  # HTTP status the HEAD request gets instead of the headers
        self.requests: list[dict[str, str]] = []
        self.robots = b"User-Agent: *\nDisallow: /_next/image\n"

    def __call__(self, request: Any, timeout: float = 0, **kw: Any) -> _Served:
        import urllib.error

        headers = {k.lower(): v for k, v in request.header_items()}
        self.requests.append({"url": request.full_url, "method": request.get_method(), **headers})
        if request.full_url.endswith("/robots.txt"):
            return _Served(self.robots, 200, {"Content-Type": "text/plain"}, request.full_url)
        if self.refuse:
            raise urllib.error.HTTPError(request.full_url, self.refuse, "refused", {}, None)  # type: ignore[arg-type]
        common = {"ETag": self.etag, "Last-Modified": self.last_modified}
        if request.get_method() == "HEAD":
            if self.head_failure:
                raise urllib.error.HTTPError(request.full_url, self.head_failure, "x", {}, None)  # type: ignore[arg-type]
            return _Served(b"", 200, {**common, "Content-Length": str(len(self.payload))}, request.full_url)
        start = int(headers["range"].removeprefix("bytes=").rstrip("-")) if "range" in headers else 0
        if start and headers.get("if-range") == self.etag:
            body = self.payload[start:]
            content_range = f"bytes {start}-{len(self.payload) - 1}/{len(self.payload)}"
            return _Served(body, 206, {**common, "Content-Range": content_range}, request.full_url)
        cut, self.cut = self.cut, None  # the connection breaks once
        if cut is not None and self.rebuilt_after_cut:
            self.etag = self.rebuilt_after_cut  # the publisher rebuilds the package while the transfer is broken
        return _Served(self.payload, 200, {**common, "Content-Length": str(len(self.payload))}, request.full_url, cut)


def _download(tmp_path: Path, server: _Server, monkeypatch: pytest.MonkeyPatch, live: bool = False):  # type: ignore[no-untyped-def]
    monkeypatch.setattr("urllib.request.urlopen", server)
    sleeps: list[float] = []
    conn = _package_connector(tmp_path, [{"year": 2024, "number": "718"}], package=None, sleep=sleeps.append)
    manifest = yaml.safe_load(conn.manifest_path.read_text())
    if live:
        manifest["package"] = {"url": PACKAGE_URL, "pin": "latest"}
        conn.manifest_path.write_text(yaml.safe_dump(manifest))
    return conn, manifest, sleeps


def test_download_resumes_an_interrupted_transfer_politely(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = SAMPLE_ZIP.read_bytes()
    server = _Server(payload, cut=30_000)
    conn, manifest, sleeps = _download(tmp_path, server, monkeypatch)
    path, meta = conn.download_package(manifest)
    assert path.read_bytes() == payload and path == conn.package_file()
    assert (meta["bytes"], meta["sha256"]) == (len(payload), manifest["package"]["sha256"])
    assert meta["requests"] == 3 and meta["interruptions"] == 1 and meta["etag"] == '"v1"'
    assert [r["url"].rsplit("/", 1)[-1] for r in server.requests] == [
        "robots.txt",
        "statute-consolidated.zip",
        "statute-consolidated.zip",
    ]
    resumed = server.requests[2]
    assert resumed["range"] == "bytes=30000-" and resumed["if-range"] == '"v1"'
    assert all(r["user-agent"].startswith("ULIS-test") for r in server.requests)
    assert sleeps and all(s > 0 for s in sleeps)  # the manifest's delay (5 s here) between request starts
    assert not path.with_name(path.name + ".part").exists()
    # Later runs use the cache: no request.
    server.requests.clear()
    assert conn.download_package(manifest)[0] == path and server.requests == []
    # The connector then reads the downloaded package (the API is not asked).
    docs = list(conn.fetch())
    assert [d.external_id for d in docs][:1] == ["finlex:statute-consolidated/2024/718/fin@20240718"]


def test_download_starts_again_when_the_package_changed_meanwhile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = SAMPLE_ZIP.read_bytes()
    server = _Server(payload, cut=30_000)
    conn, manifest, _ = _download(tmp_path, server, monkeypatch)
    server.rebuilt_after_cut = '"v2"'  # after the break If-Range ("v1") does not match: the whole file again
    path, meta = conn.download_package(manifest)
    assert path.read_bytes() == payload and meta["etag"] == '"v2"' and meta["requests"] == 3
    assert conn.download_events == [
        "interrupted at byte 30000 (connection reset by peer); resuming",
        "the package changed (ETag); starting again from byte 0",
    ]
    assert server.requests[2]["if-range"] == '"v1"'


@pytest.mark.parametrize("code", [401, 403, 429, 451])
def test_download_stops_at_the_first_refusal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: int) -> None:
    server = _Server(SAMPLE_ZIP.read_bytes(), refuse=code)
    conn, manifest, _ = _download(tmp_path, server, monkeypatch)
    with pytest.raises(FetchError, match=f"HTTP {code}"):
        conn.download_package(manifest)
    assert len(server.requests) == 2  # robots.txt and one GET: never retried


def test_download_refuses_a_newer_package_and_a_disallowed_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = SAMPLE_ZIP.read_bytes()
    server = _Server(payload + b"rebuilt")
    conn, manifest, _ = _download(tmp_path, server, monkeypatch)
    with pytest.raises(PackageChanged, match="replaced the package"):
        conn.download_package(manifest)
    assert not conn.package_file().with_name("statute-consolidated.zip.part").exists()
    # Unpinned (scripts/finlex_package.py --replace), the new package is downloaded.
    path, meta = conn.download_package(manifest, check_pin=False)
    assert meta["bytes"] == len(payload) + 7
    server.robots = b"User-agent: *\nDisallow: /files/\n"
    path.unlink()
    with pytest.raises(FetchError, match="disallowed by"):
        conn.download_package(manifest, check_pin=False)


# --------------------------------------------------------------------------- live package (package.pin: latest)


def _current_package(tmp_path: Path) -> bytes:
    """Another day's package, different from the sample: the sample's real members without those of 64/2023."""
    import zipfile

    path = tmp_path / "current.zip"
    with zipfile.ZipFile(SAMPLE_ZIP) as src, zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            if "/2023/64/" not in info.filename:
                dst.writestr(info, src.read(info))
    return path.read_bytes()


def _methods(server: _Server) -> list[str]:
    return [f"{r['method']} {r['url'].rsplit('/', 1)[-1]}" for r in server.requests]


def test_live_package_loads_what_finlex_serves_and_names_it_in_every_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import hashlib

    current = _current_package(tmp_path)
    assert current != SAMPLE_ZIP.read_bytes()
    server = _Server(current, etag='"day-2"')
    server.last_modified = "Sun, 18 Oct 2026 23:27:28 GMT"
    conn, manifest, _ = _download(tmp_path, server, monkeypatch, live=True)
    docs = list(conn.fetch())
    assert _methods(server) == ["GET robots.txt", "GET statute-consolidated.zip"]  # nothing cached: no HEAD
    sha = hashlib.sha256(current).hexdigest()
    assert [d.external_id for d in docs] == [
        "finlex:statute-consolidated/2024/718/fin@20240718",
        "finlex:statute-consolidated/2024/718/swe@20240718",
    ]
    for doc in docs:  # the exact package bytes the document came from
        assert {k: doc.metadata[k] for k in ("package_bytes", "package_sha256", "package_etag")} == {
            "package_bytes": len(current),
            "package_sha256": sha,
            "package_etag": '"day-2"',
        }
        assert doc.metadata["package_last_modified"] == "Sun, 18 Oct 2026 23:27:28 GMT"
        assert doc.metadata["package_retrieved_at"] == doc.retrieved_at.isoformat()
    meta = json.loads(conn.package_file().with_name("statute-consolidated.zip.meta.json").read_text())
    assert (meta["bytes"], meta["sha256"], meta["etag"]) == (len(current), sha, '"day-2"')
    # The same manifest pinned exactly to the sample package refuses the current one.
    conn.manifest_path.write_text(yaml.safe_dump({**manifest, "package": _pin(SAMPLE_ZIP)}))
    with pytest.raises(PackageChanged, match="not the one the manifest pins"):
        list(conn.fetch())
    for bad, message in [({"sha256": "a" * 64}, "takes no bytes or sha256"), ({"approx_bytes": "4 GB"}, "approx")]:
        conn.manifest_path.write_text(yaml.safe_dump({**manifest, "package": {**manifest["package"], **bad}}))
        with pytest.raises(ManifestError, match=message):
            conn._manifest()
    conn.manifest_path.write_text(yaml.safe_dump({**manifest, "package": {**manifest["package"], "pin": "today"}}))
    with pytest.raises(ManifestError, match="package.pin"):
        conn._manifest()


def test_live_package_is_reused_until_finlex_rebuilds_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import hashlib

    server = _Server(SAMPLE_ZIP.read_bytes(), etag='"day-1"')
    conn, _, _ = _download(tmp_path, server, monkeypatch, live=True)
    first = list(conn.fetch())
    server.requests.clear()
    second = list(conn.fetch())  # the same ETag: one HEAD, and the cached package is read again
    assert _methods(server) == ["GET robots.txt", "HEAD statute-consolidated.zip"]
    assert [(d.metadata["package_sha256"], d.retrieved_at) for d in second] == [
        (d.metadata["package_sha256"], d.retrieved_at) for d in first
    ]
    # Finlex rebuilds the package: the HEAD shows another ETag and the cached package is replaced (one on disk).
    server.payload, server.etag = _current_package(tmp_path), '"day-2"'
    server.requests.clear()
    third = list(conn.fetch())
    assert _methods(server) == ["GET robots.txt", "HEAD statute-consolidated.zip", "GET statute-consolidated.zip"]
    assert {d.metadata["package_sha256"] for d in third} == {hashlib.sha256(server.payload).hexdigest()}
    assert conn.download_events == [
        'the cached package (ETag "day-1") is not the one Finlex serves now (ETag "day-2", Last-Modified Sat, '
        "17 Oct 2026 23:27:28 GMT); downloading that one"
    ]
    assert conn.package_file().read_bytes() == server.payload
    assert sorted(p.name for p in conn.package_file().parent.iterdir()) == [
        "statute-consolidated.zip",
        "statute-consolidated.zip.meta.json",
    ]


def test_live_package_is_downloaded_again_after_it_was_pruned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = _Server(SAMPLE_ZIP.read_bytes())
    conn, _, _ = _download(tmp_path, server, monkeypatch, live=True)
    list(conn.fetch())
    package = conn.package_file()
    package.unlink()  # a cache pruner deleted the package and left its record
    server.requests.clear()
    assert len(list(conn.fetch())) == 2
    assert _methods(server) == ["GET robots.txt", "GET statute-consolidated.zip"]  # nothing left to compare: no HEAD
    assert package.read_bytes() == SAMPLE_ZIP.read_bytes()
    package.unlink()
    package.with_name(package.name + ".meta.json").unlink()  # both gone
    server.requests.clear()
    assert len(list(conn.fetch())) == 2 and len(server.requests) == 2
    # Offline, the cached package is read without a request; once it is gone, nothing is fetched.
    offline = FinlexConnector(conn.manifest_path, "fi-finlex", conn.cache_dir, offline=True)
    server.requests.clear()
    assert len(list(offline.fetch())) == 2 and server.requests == []
    package.unlink()
    with pytest.raises(FetchError, match="offline and not cached"):
        list(offline.fetch())


def test_live_package_when_finlex_cannot_be_asked_or_the_cache_was_altered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = _Server(SAMPLE_ZIP.read_bytes())
    conn, _, _ = _download(tmp_path, server, monkeypatch, live=True)
    list(conn.fetch())
    server.head_failure = 503  # Finlex cannot say what it serves: the cached package is used, and the run says so
    server.requests.clear()
    assert len(list(conn.fetch())) == 2
    assert _methods(server) == ["GET robots.txt"] + ["HEAD statute-consolidated.zip"] * 4
    assert conn.download_events == ["could not ask Finlex for its current package; using the cached one"]
    server.head_failure = 403  # a refusal stops the run
    with pytest.raises(FetchError, match="HTTP 403"):
        list(conn.fetch())
    # A cached file that no longer matches its record is not taken for Finlex's package: it is downloaded again.
    server.head_failure = None
    package = conn.package_file()
    package.write_bytes(package.read_bytes() + b"x")
    server.requests.clear()
    assert len(list(conn.fetch())) == 2
    assert _methods(server) == ["GET robots.txt", "HEAD statute-consolidated.zip", "GET statute-consolidated.zip"]
    assert package.read_bytes() == SAMPLE_ZIP.read_bytes()


@pytest.fixture(scope="module")
def fi_package_ingested(
    session_factory: sessionmaker[Session],
    catalog: Catalog,
    store: LocalObjectStore,
    fi_decision_ingested: IngestionReport,
    tmp_path_factory: pytest.TempPathFactory,
) -> IngestionReport:
    works = [
        {"year": 2024, "number": "718"},  # ingested above from the API's responses: the same bytes
        {"year": 2009, "number": "330"},
        {"year": 2020, "number": "1175", "languages": ["fin"]},  # no longer in force: skipped
        {"year": 1932, "number": "246", "languages": ["fin"]},  # a shell: skipped
    ]
    connector = _package_connector(tmp_path_factory.mktemp("finlex-package"), works, offline=True)
    with session_factory() as session:
        report = Ingestor(session, catalog, store).ingest_source("fi-finlex", actor="pytest", connector=connector)
        session.commit()
    return report


@pytest.mark.db
def test_the_package_route_ingests_through_the_pipeline(
    session: Session, catalog: Catalog, store: LocalObjectStore, fi_package_ingested: IngestionReport
) -> None:
    report = fi_package_ingested
    assert report.errors == [] and report.conflicts == []
    # 2024/718 was already stored from the API route: the package's bytes are the same, so nothing is duplicated.
    assert (report.documents_seen, report.documents_unchanged, report.documents_new, report.documents_skipped) == (
        6,
        2,
        2,
        2,
    )
    assert (report.instruments_created, report.instrument_versions_created) == (1, 2)
    docs = session.scalars(select(m.SourceDocument).where(m.SourceDocument.uri.like(f"{PACKAGE_URL}#%"))).all()
    assert sorted(d.external_id for d in docs) == [
        "finlex:statute-consolidated/2009/330/fin@20250869",
        "finlex:statute-consolidated/2009/330/swe@20250869",
    ]
    for doc in docs:
        assert verify_source_document(session, catalog, store, doc.id)["result"] == "pass"
        # The source-document record names the exact package the bytes came from.
        assert doc.metadata_["package_sha256"] == _pin(SAMPLE_ZIP)["sha256"]
        assert doc.metadata_["package_bytes"] == SAMPLE_ZIP.stat().st_size
    fi = _search(session, catalog, q="tuotantoeläinten hyvinvoinnin neuvottelukunta", query_language="fi")
    assert fi["results"][0]["citation"] == (
        "Valtioneuvoston asetus tuotantoeläinten hyvinvoinnin neuvottelukunnasta (330/2009) 1 § 1 mom."
    )
    sv = _search(session, catalog, q="delegation för animalieproduktionsdjurens välbefinnande", query_language="sv")
    assert sv["results"][0]["citation"].startswith(
        "Statsrådets förordning om delegationen för animalieproduktionsdjurens välbefinnande (330/2009) 1 §"
    )

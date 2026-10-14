"""Estonia: Riigi Teataja consolidated texts (oigusakt XML) - parser, citations, connector, PostgreSQL."""

from __future__ import annotations

import gzip
import json
import struct
import zipfile
from collections.abc import Iterator
from datetime import UTC, date, datetime
from io import BytesIO
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
from ulis.sources.connectors.polite_http import FetchError, PoliteHttp
from ulis.sources.connectors.riigi_teataja import (
    RiigiTeatajaConnector,
    RobotsRules,
    ZipEntry,
    locate_central_directory,
    read_central_directory,
    read_member,
)
from ulis.sources.normalized import ParsedDocument, ParsedProvision
from ulis.sources.parsers.riigi_teataja import RiigiTeatajaParseError, parse_riigi_teataja, resolve_uri
from ulis.storage.object_store import LocalObjectStore
from ulis.verification.integrity import verify_source_document

FIX = Path(__file__).parent / "fixtures" / "riigi_teataja"
SOURCE = "ee-riigi-teataja"
HOST = "www.riigiteataja.ee"
PS = "/ee/constitution/151381"
KARS = "/ee/act/162500"
TLS = "/ee/act/331584"
# Oldest first within each work (whole-act snapshots).
INGESTED = [
    "127042011002",
    "115052015002",
    "111042025002",
    "111042025003",
    "104012019011",
    "106032026010",
    "130062026052",
    "106072023031",
    "109072026028",
    "120062026018",
    "103072026034",
    "103072026035",
]


def _raw(name: str) -> bytes:
    return gzip.decompress((FIX / f"{name}.xml.gz").read_bytes())


def _doc(name: str) -> ParsedDocument:
    (doc,) = parse_riigi_teataja(_raw(name))
    return doc


def _p(doc: ParsedDocument, eid: str) -> ParsedProvision:
    return next(p for p in doc.provisions if p.eid == eid)


def _cite(doc: ParsedDocument, eid: str) -> str:
    p = _p(doc, eid)
    c = ProvisionCitationInput(
        str(doc.short_title), doc.instrument_type, "EE", p.top_kind, p.num_path, "et", ext=doc.ext
    )
    return get_module("ee").provision_citation(c)


# --------------------------------------------------------------------------- parser

MINIMAL = (
    '<?xml version="1.0" encoding="UTF-8"?><oigusakt xmlns="Juurakt"><metaandmed><dokumentLiik>seadus</dokumentLiik>'
    "<tekstiliik>{kind}</tekstiliik><globaalID>1</globaalID><terviktekstiGrupiID>2</terviktekstiGrupiID>"
    "<kehtivus><kehtivuseAlgus>2020-01-01</kehtivuseAlgus></kehtivus></metaandmed>"
    "<aktinimi><nimi><pealkiri>Näide</pealkiri></nimi></aktinimi><sisu>{body}</sisu></oigusakt>"
)


@pytest.mark.parametrize(
    "content, message",
    [
        (b'<!DOCTYPE x [<!ENTITY e "x">]><oigusakt/>', "unsafe"),
        (b"<html><body>app</body></html>", "unsupported root"),
        (MINIMAL.format(kind="algtekst", body="").encode(), "only consolidated texts"),
        (MINIMAL.format(kind="terviktekst", body="").encode(), "no provisions"),
        (MINIMAL.format(kind="terviktekst", body="<tabel/>").encode(), "unexpected <tabel>"),
    ],
)
def test_unsafe_or_unsupported_input_is_rejected(content: bytes, message: str) -> None:
    with pytest.raises(RiigiTeatajaParseError, match=message):
        parse_riigi_teataja(content)


def test_constitution_structure_dates_and_origin() -> None:
    doc = _doc("115052015002")
    assert (doc.work_uri, doc.expression_uri) == (PS, f"{PS}/est@115052015002")
    assert (doc.instrument_type, doc.language, doc.short_title, doc.work_date) == (
        "constitution",
        "et",
        "PS",
        date(1992, 6, 28),
    )
    assert doc.title == "Eesti Vabariigi põhiseadus" and doc.ext_module == "ee"
    assert (doc.text_origin, doc.authoritative, doc.validity_basis) == (
        "official_consolidation",
        False,
        "revision_snapshot",
    )
    assert doc.ext["original_publication"] == "RT 1992, 26, 349" and doc.ext["abbreviation"] == "PS"
    # kehtivuseAlgus 2015-08-13+03:00 .. kehtivuseLopp 2025-07-08: the calendar date as written, end exclusive.
    assert doc.version_date == date(2015, 8, 13)
    assert {(p.valid_from, p.valid_to) for p in doc.provisions} == {(date(2015, 8, 13), date(2025, 7, 9))}
    assert _p(doc, "preamble").text.startswith("Kõikumatus usus ja vankumatus tahtes kindlustada ja arendada riiki,\n")
    assert _p(doc, "preamble").note == "RT I 2007, 33, 210, jõust. 21.07.2007"
    # Numbers the XML leaves out are taken from the element ids; "para-10" holds the heading only.
    assert _p(doc, "para-1-lg-2").text == "Eesti iseseisvus ja sõltumatus on aegumatu ning võõrandamatu."
    assert [p.eid for p in doc.provisions if p.parent_eid == "para-10"] == ["para-10-lg-1"]
    assert doc.provisions[-1].eid.startswith("para-") and len({p.eid for p in doc.provisions}) == len(doc.provisions)
    current = _doc("111042025003")
    assert {p.valid_to for p in current.provisions} == {None} and current.version_date == date(2026, 3, 1)


def test_the_constitution_across_versions() -> None:
    texts = {n: _p(_doc(n), "para-156-lg-2") for n in ("127042011002", "115052015002", "111042025002", "111042025003")}
    assert "vähemalt kaheksateist aastat vanad" in texts["127042011002"].text
    assert texts["115052015002"].text.endswith("püsivalt elavad isikud, kes on vähemalt kuusteist aastat vanad.")
    assert "Eesti kodanikud ja kodakondsuseta isikud" in texts["111042025002"].text
    assert texts["111042025003"].text.endswith(
        "püsivalt elavad Eesti kodanikud, kes on vähemalt kuusteist aastat vanad."
    )
    assert texts["111042025003"].note == "RT I, 11.04.2025, 1, jõust. 01.03.2026"
    # Unnumbered subsections get their number from the paragraph even where the source repeats one id.
    assert [p.eid for p in _doc("111042025003").provisions[:4]] == ["preamble", "para-1", "para-1-lg-1", "para-1-lg-2"]


def test_repealed_provisions_and_superscript_numbers() -> None:
    kars = _doc("109072026028")
    gone = _p(kars, "para-287")
    assert (gone.status, gone.text, gone.heading) == ("repealed", "", None)
    assert gone.note == "Kehtetud - RT I, 12.07.2014, 1, jõust. 01.01.2015"
    assert [p.eid for p in _doc("106072023031").provisions if p.status == "repealed"] == [
        "para-24b1",
        "para-26-lg-2",
        "para-28-lg-3",
    ]
    iks = _doc("130062026052")
    inserted = _p(iks, "para-2b1")
    assert (
        inserted.num_path == ["sec:2¹"]
        and inserted.heading == "Andmekaitse Inspektsiooni pädevus kollektiivse esindushagi menetluses"
    )
    assert (
        "tarbijakaitseseaduse §-de 60² ja 66² tähenduses" in _p(iks, "para-2b1-lg-1").text
    )  # <sup> kept as superscripts
    assert _p(iks, "para-2b1-lg-1").note == "RT I, 31.12.2024, 5, jõust. 01.01.2025"
    # Line breaks inside a sentence are kept, text of different elements is joined in document order.
    assert (
        _p(kars, "para-113-lg-1").text
        == "Teise inimese tapmise eest –\nkaristatakse kuue- kuni viieteistaastase vangistusega."
    )


def test_dates_with_time_zone_offsets_and_lifecycle_events() -> None:
    old = _doc("104012019011")  # kehtivuseAlgus 2019-01-15+02:00, kehtivuseLopp 2023-10-31+02:00
    assert old.version_date == date(2019, 1, 15) and {p.valid_to for p in old.provisions} == {date(2023, 11, 1)}
    assert old.work_date == date(2018, 12, 12) and old.ext["publication"] == "RT I, 04.01.2019, 11"
    kars = _doc("109072026028")
    amendments = [e for e in kars.events if e.event_type == "amendment"]
    assert len(amendments) > 100 and all(e.affecting_uri and e.affecting_uri.startswith("https://") for e in amendments)
    assert [e.event_type for e in kars.events][0] == "version_change"


def test_citations() -> None:
    ps, kars, tls, iks = _doc("111042025003"), _doc("109072026028"), _doc("103072026035"), _doc("130062026052")
    assert _cite(ps, "para-10") == "PS § 10"
    assert _cite(ps, "para-156-lg-2") == "PS § 156 lg 2"
    assert _cite(ps, "preamble") == "PS preambul"
    assert _cite(kars, "para-113-lg-1") == "KarS § 113 lg 1"
    assert _cite(tls, "para-92-lg-1-p-6") == "TLS § 92 lg 1 p 6"
    assert _cite(iks, "para-2b1-lg-1") == "IKS § 2¹ lg 1"
    module = get_module("ee")
    nested = ProvisionCitationInput("HMS", "act", "EE", "section", ["sec:40", "sub:1", "pt:2", "subpt:a"], "et")
    assert module.provision_citation(nested) == "HMS § 40 lg 1 p 2 alap a"
    assert module.provision_citation(ProvisionCitationInput("Näide", "act", "EE", "section", [], "et")) == "Näide"
    assert resolve_uri("/ee/act/162500/~para-113-lg-1") == (KARS, "para-113-lg-1")
    assert resolve_uri("/nl/bwb/BWBR0001840") is None


# --------------------------------------------------------------------------- connector


def _zip(members: dict[str, bytes]) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in members.items():
            z.writestr(name, data)
    return buffer.getvalue()


def test_robots_rules_apply_wildcards_anchors_and_longest_match() -> None:
    rules = RobotsRules(
        "User-agent: Other\nDisallow: /\n\nUser-agent: *\nDisallow: */kohtulahendid/*\nDisallow: */akt/13310465/*\n"
        "Disallow: /private$\nAllow: /private/open\nDisallow: /private/\n# comment\n"
    )
    assert not rules.allowed("/et/kohtulahendid/123") and not rules.allowed("/akt/13310465/")
    assert rules.allowed("/akt/13310465") and rules.allowed("/avaandmed/ERT/xml.2026.zip")
    assert not rules.allowed("/private") and rules.allowed("/private/open") and not rules.allowed("/private/x")
    assert RobotsRules("User-agent: *\nDisallow:\n").allowed("/anything")


def test_zip_reading_with_crc_and_zip64_records() -> None:
    members = {"111.xml": b"<oigusakt>a</oigusakt>" * 20, "222.xml": b"<oigusakt>b</oigusakt>"}
    data = _zip(members)
    offset, size, entries = locate_central_directory(data[-65536:])
    assert entries == 2 and data[offset : offset + 4] == b"PK\x01\x02"
    found = read_central_directory(data[offset : offset + size], {"222.xml", "missing.xml"})
    assert set(found) == {"222.xml"}
    entry = found["222.xml"]
    assert read_member(data[entry.offset :], entry) == members["222.xml"]
    with pytest.raises(FetchError, match="CRC-32"):
        read_member(
            data[entry.offset :], ZipEntry(entry.name, entry.method, entry.compressed_size, entry.size, entry.offset, 1)
        )
    with pytest.raises(FetchError, match="no local file header"):
        read_member(b"x" * 100, entry)
    # zip64: sizes and offset in the extra field; counts in the zip64 end of central directory record
    name = b"333.xml"
    extra = struct.pack("<HHQQQ", 1, 24, 7_000_000_000, 5_000_000_000, 9_000_000_000)
    record = struct.pack(
        "<IHHHHHHIIIHHHHHII",
        0x02014B50,
        45,
        45,
        0,
        8,
        0,
        0,
        0xABCD,
        0xFFFFFFFF,
        0xFFFFFFFF,
        len(name),
        len(extra),
        0,
        0,
        0,
        0,
        0xFFFFFFFF,
    )
    directory = record + name + extra
    (entry64,) = read_central_directory(directory, {"333.xml"}).values()
    assert (entry64.size, entry64.compressed_size, entry64.offset, entry64.crc32) == (
        7_000_000_000,
        5_000_000_000,
        9_000_000_000,
        0xABCD,
    )
    eocd64 = struct.pack("<IQHHIIQQQQ", 0x06064B50, 44, 45, 45, 0, 0, 254454, 254454, 24766457, 43002773490)
    locator = struct.pack("<IIQI", 0x07064B50, 0, 43027000000, 1)
    eocd = struct.pack("<IHHHHIIH", 0x06054B50, 0xFFFF, 0xFFFF, 0xFFFF, 0xFFFF, 0xFFFFFFFF, 0xFFFFFFFF, 0)
    assert locate_central_directory(b"x" * 50 + eocd64 + locator + eocd) == (43002773490, 24766457, 254454)
    with pytest.raises(FetchError, match="no end of central directory"):
        locate_central_directory(b"not a zip")


ARCHIVE = "avaandmed/ERT/xml.2026.zip"


def _put(dest: Path, key: str, data: bytes, status: int = 200) -> None:
    path = dest / HOST / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    meta = {"url": f"https://{HOST}/{key}", "status": status, "retrieved_at": "2026-10-14T10:55:00+00:00"}
    path.with_name(path.name + ".meta.json").write_text(json.dumps(meta))


def _cache(dest: Path, ids: list[str], *, robots: str = "User-agent: *\nDisallow: */kohtulahendid/*\n") -> bytes:
    """What PoliteHttp would have cached for a small archive holding the fixtures ``ids``."""
    archive = _zip({f"{i}.xml": _raw(i) for i in ids})
    offset, size, _ = locate_central_directory(archive[-65536:])
    directory = archive[offset : offset + size]
    entries = read_central_directory(directory, {f"{i}.xml" for i in ids})
    _put(dest, "robots.txt", robots.encode())
    _put(dest, "avaandmed/ERT/LOEMIND.txt", b"Riigi Teataja avaandmed on genereeritud 2026-10-11 seisuga.\n")
    _put(dest, "xml.2026.zip.tail", archive[-65536:], 206)
    _put(dest, f"xml.2026.zip.central-directory@{offset}-{size}", directory, 206)
    for name, entry in entries.items():
        last = entry.offset + 30 + len(name) + 1024 + entry.compressed_size - 1
        _put(dest, f"{name}@{entry.offset}", archive[entry.offset : last + 1], 206)
    return archive


def _manifest(tmp_path: Path, works: list[dict[str, Any]], **extra: Any) -> Path:
    path = tmp_path / "manifest.yaml"
    data = {
        "schema_version": 1,
        "source_id": SOURCE,
        "base_url": f"https://{HOST}",
        "archive": ARCHIVE,
        "user_agent": "ULIS-test (+https://example.org)",
        "works": works,
        **extra,
    }
    path.write_text(yaml.safe_dump(data, allow_unicode=True))
    return path


PS_WORK = {"name": "PS", "abbreviation": "PS", "group_id": 151381, "versions": ["115052015002", "111042025002"]}


def test_connector_offline_from_cache(tmp_path: Path) -> None:
    _cache(tmp_path / "cache", ["115052015002", "111042025002"])
    docs = list(RiigiTeatajaConnector(_manifest(tmp_path, [PS_WORK]), SOURCE, tmp_path / "cache", offline=True).fetch())
    assert [d.external_id for d in docs] == ["ee-rt:115052015002", "ee-rt:111042025002"]
    assert docs[0].content == _raw("115052015002")  # exact bytes of the archive member
    assert docs[0].uri == f"https://{HOST}/{ARCHIVE}#115052015002.xml"
    md = docs[1].metadata
    assert (md["group_id"], md["abbreviation"], md["archive_generated"]) == ("151381", "PS", "2026-10-11")
    assert md["act_page"] == f"https://{HOST}/akt/111042025002" and md["member_size"] == len(_raw("111042025002"))
    assert parse_riigi_teataja(docs[1].content)[0].expression_uri == f"{PS}/est@111042025002"
    with pytest.raises(FetchError, match="is not in"):
        absent = {**PS_WORK, "versions": ["111042025003"]}
        list(RiigiTeatajaConnector(_manifest(tmp_path, [absent]), SOURCE, tmp_path / "cache", offline=True).fetch())


def test_connector_refuses_bad_manifests_robots_and_html(tmp_path: Path) -> None:
    ok = {"name": "X", "abbreviation": "X", "group_id": 1, "versions": ["115052015002"]}
    for works, extra, message in (
        ([{**ok, "versions": ["../etc"]}], {}, "invalid globaalID"),
        ([{**ok, "versions": []}], {}, "lists no versions"),
        ([{**ok, "versions": ["115052015002", "115052015002"]}], {}, "listed twice"),
        ([{**ok, "group_id": "x"}], {}, "group_id"),
        ([ok], {"crawl_delay_seconds": 1}, "at least 5"),
        ([ok], {"base_url": "http://www.riigiteataja.ee"}, "base_url"),
        ([ok], {"archive": "avaandmed/ERT/../../etc.zip"}, "archive must be"),
        ([ok], {"user_agent": "python-urllib"}, "user_agent"),
    ):
        with pytest.raises(ManifestError, match=message):
            list(RiigiTeatajaConnector(_manifest(tmp_path, works, **extra), SOURCE, tmp_path).fetch())
    # An act the publisher's robots.txt disallows is not ingested (rules are applied exactly).
    _cache(tmp_path / "blocked", ["115052015002"], robots="User-agent: *\nDisallow: */akt/115052015002/*\n")
    with pytest.raises(FetchError, match="robots.txt disallows /akt/115052015002"):
        list(RiigiTeatajaConnector(_manifest(tmp_path, [ok]), SOURCE, tmp_path / "blocked", offline=True).fetch())
    _cache(tmp_path / "closed", ["115052015002"], robots="User-agent: *\nDisallow: /avaandmed/\n")
    with pytest.raises(FetchError, match="robots.txt disallows /avaandmed"):
        list(RiigiTeatajaConnector(_manifest(tmp_path, [ok]), SOURCE, tmp_path / "closed", offline=True).fetch())
    # The web application's HTML shell, served for unknown paths, is not an act.
    shell = b"<!doctype html><html><div id='root'></div></html>"
    cache = tmp_path / "html"
    archive = _zip({"115052015002.xml": shell})
    offset, size, _ = locate_central_directory(archive[-65536:])
    entry = read_central_directory(archive[offset : offset + size], {"115052015002.xml"})["115052015002.xml"]
    _put(cache, "robots.txt", b"User-agent: *\n")
    _put(cache, "avaandmed/ERT/LOEMIND.txt", b"x")
    _put(cache, "xml.2026.zip.tail", archive[-65536:], 206)
    _put(cache, f"xml.2026.zip.central-directory@{offset}-{size}", archive[offset : offset + size], 206)
    _put(cache, f"115052015002.xml@{entry.offset}", archive, 206)
    with pytest.raises(FetchError, match="not a Riigi Teataja act XML"):
        list(RiigiTeatajaConnector(_manifest(tmp_path, [ok]), SOURCE, cache, offline=True).fetch())


def test_range_request_that_is_not_honoured_is_not_downloaded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A server that ignores Range would send the whole 43 GB archive: PoliteHttp stops before reading the body."""

    class Whole:
        status = 200
        headers: dict[str, str] = {}

        def __enter__(self) -> Whole:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def geturl(self) -> str:
            return "https://www.riigiteataja.ee/x"

        def read(self) -> bytes:
            raise AssertionError("the body must not be read")

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: Whole())
    http = PoliteHttp(tmp_path, "ULIS-test (+https://example.org)", allowed_hosts={HOST}, sleep=lambda s: None)
    with pytest.raises(FetchError, match="range request was not honoured"):
        http.get(f"https://{HOST}/x.zip", headers={"Range": "bytes=0-9"})


def test_source_uses_this_connector(catalog: Catalog) -> None:
    assert isinstance(build_connector(catalog.sources.get(SOURCE)), RiigiTeatajaConnector)
    manifest = yaml.safe_load((Path(__file__).parents[1] / "config/manifests/ee-riigi-teataja.yaml").read_text())
    assert [w["abbreviation"] for w in manifest["works"]] == ["PS", "KarS", "VÕS", "IKS", "TLS", "HMS"]


# --------------------------------------------------------------------------- PostgreSQL


class _Fixtures:
    def fetch(self) -> Iterator[RawDocument]:
        for name in INGESTED:
            yield RawDocument(
                f"ee-rt:{name}",
                f"https://{HOST}/{ARCHIVE}#{name}.xml",
                "application/xml",
                _raw(name),
                datetime(2026, 10, 14, 10, 55, tzinfo=UTC),
                {},
            )


@pytest.fixture(scope="module")
def ee_ingested(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, ingested: IngestionReport
) -> IngestionReport:
    with session_factory() as session:
        report = Ingestor(session, catalog, store).ingest_source(SOURCE, actor="pytest", connector=_Fixtures())
        session.commit()
    return report


def _search(session: Session, catalog: Catalog, **kw: Any) -> dict[str, Any]:
    return search(session, catalog, SearchRequest(jurisdiction="EE", **kw), today=date(2026, 10, 14))


@pytest.mark.db
def test_search_finds_exact_text_with_citation_and_limitations(
    session: Session, catalog: Catalog, ee_ingested: IngestionReport
) -> None:
    assert ee_ingested.errors == [] and ee_ingested.conflicts == []
    assert ee_ingested.instruments_created == 6 and ee_ingested.instrument_versions_created == len(INGESTED)
    res = _search(
        session,
        catalog,
        q="teise inimese tapmise eest kuue- kuni viieteistaastase vangistusega",
        as_of=date(2026, 10, 14),
    )
    hit = res["results"][0]
    assert hit["citation"] == "KarS § 113 lg 1"
    assert hit["text"] == "Teise inimese tapmise eest –\nkaristatakse kuue- kuni viieteistaastase vangistusega."
    codes = {lim["code"] for lim in hit["limitations"]}
    assert {
        "PROFILE_UNVERIFIED",
        "OFFICIAL_CONSOLIDATION_REPRODUCED",
        "SOURCE_NOT_AUTHENTIC",
        "SNAPSHOT_DATES",
    } <= codes
    assert hit["source"]["attribution"].startswith("Allikas: Riigi Teataja")
    tls = _search(
        session,
        catalog,
        q="reservteenistuses või mobilisatsioonikäsu alusel tegevteenistuses",
        as_of=date(2026, 10, 14),
    )
    assert tls["results"][0]["citation"] == "TLS § 92 lg 1 p 6"


@pytest.mark.db
def test_point_in_time_follows_the_consolidated_texts(
    session: Session, catalog: Catalog, ee_ingested: IngestionReport
) -> None:
    lg2 = f"{PS}/~para-156-lg-2"
    expected = {
        date(2012, 1, 1): ("kaheksateist aastat", "2011-07-22", "2015-08-13"),
        date(2020, 1, 1): ("püsivalt elavad isikud, kes on vähemalt kuusteist aastat", "2015-08-13", "2025-07-09"),
        date(2025, 8, 1): ("Eesti kodanikud ja kodakondsuseta isikud", "2025-07-09", "2026-03-01"),
        date(2026, 6, 1): ("elavad Eesti kodanikud, kes on vähemalt kuusteist", "2026-03-01", None),
    }
    for as_of, (words, start, end) in expected.items():
        got = provision_at(session, catalog, lg2, as_of)
        assert got["status_on_date"] == "in_force" and words in got["version"]["text"]
        assert (got["version"]["valid_from"], got["version"]["valid_to"]) == (start, end)
    assert got["provision"]["citation"] == "PS § 156 lg 2"
    assert provision_at(session, catalog, lg2, date(2000, 1, 1))["status_on_date"] == "history_not_indexed"
    # Unchanged wording continues across consolidated texts (one version from 2011 to 2026).
    one = provision_at(session, catalog, f"{PS}/~para-10-lg-1", date(2020, 1, 1))["version"]
    assert (one["valid_from"], one["valid_to"]) == ("2011-07-22", None)
    # An amendment that took effect on 2026-10-01 (RT I, 30.06.2026, 1) splits the wording of TLS § 92 lg 1 p 6.
    p6 = f"{TLS}/~para-92-lg-1-p-6"
    before, after = (
        provision_at(session, catalog, p6, date(2026, 9, 1)),
        provision_at(session, catalog, p6, date(2026, 10, 14)),
    )
    assert before["version"]["text"] == "töötaja on aja-, asendus- või reservteenistuses;"
    assert after["version"]["text"].endswith("või mobilisatsioonikäsu alusel tegevteenistuses;")
    assert after["version"]["valid_from"] == "2026-10-01"
    # A provision repealed in the consolidated text.
    assert provision_at(session, catalog, f"{KARS}/~para-287", date(2026, 10, 14))["status_on_date"] == "repealed"
    # IKS: the consolidated texts of 2023-11-01 - 2026-03-15 are not ingested, so that gap has no version.
    gap = provision_at(session, catalog, "/ee/act/1045568/~para-1-lg-1", date(2025, 1, 1))
    assert gap["version"] is None


@pytest.mark.db
def test_reingest_is_idempotent_and_sources_verify(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, ee_ingested: IngestionReport
) -> None:
    with session_factory() as session:
        again = Ingestor(session, catalog, store).ingest_source(SOURCE, actor="pytest", connector=_Fixtures())
        assert again.documents_unchanged == len(INGESTED) and again.provision_versions_created == 0
        ids = session.scalars(select(m.SourceDocument.id).where(m.SourceDocument.source_id == SOURCE)).all()
        results = [verify_source_document(session, catalog, store, i, checker="pytest") for i in ids]
        assert len(results) == len(INGESTED) and all(r["result"] == "pass" for r in results)

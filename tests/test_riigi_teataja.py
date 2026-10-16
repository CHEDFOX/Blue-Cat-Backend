"""Estonia: Riigi Teataja consolidated texts (oigusakt XML) - parser, citations, connector, PostgreSQL."""

from __future__ import annotations

import base64
import gzip
import json
import random
import re
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
    HEAD_COMPRESSED,
    RiigiTeatajaConnector,
    RobotsRules,
    ZipEntry,
    iter_central_directory,
    locate_central_directory,
    plan_chunks,
    read_central_directory,
    read_member,
)
from ulis.sources.normalized import ParsedDocument, ParsedProvision, ParseSkipped
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


def test_corrections_of_errors_are_notes_and_drafts_are_skipped() -> None:
    erratum = (
        "<veaparandus><veaparandusKuupaev>2019-04-18</veaparandusKuupaev><veaparandusTekst>Veaparandus - "
        "Kustutatud punkti 1 topelt tekst Riigi Teataja seaduse § 10 lg 3 alusel.</veaparandusTekst></veaparandus>"
    )
    body = (
        '<paragrahv id="para1"><paragrahvNr>1</paragrahvNr><loige id="para1lg1"><loigeNr>1</loigeNr>'
        "<sisuTekst><tavatekst>Tekst.</tavatekst></sisuTekst>"
        '<alampunkt id="para1lg1p1"><alampunktNr>1</alampunktNr><sisuTekst><tavatekst>Punkt.</tavatekst></sisuTekst>'
        f"{erratum}</alampunkt>{erratum}</loige>"
        # as in the Penal Code: the erratum inside the sisuTekst, after the text
        '<loige id="para1lg2"><loigeNr>2</loigeNr><sisuTekst><tavatekst>Teine.</tavatekst>'
        f"{erratum}</sisuTekst></loige></paragrahv>"
    )
    (doc,) = parse_riigi_teataja(MINIMAL.format(kind="terviktekst", body=body).encode())
    point, subsection, inside = _p(doc, "para-1-lg-1-p-1"), _p(doc, "para-1-lg-1"), _p(doc, "para-1-lg-2")
    assert (point.text, subsection.text, inside.text) == ("Punkt.", "Tekst.", "Teine.")
    assert point.note == subsection.note == inside.note
    assert (point.note or "").startswith("Veaparandus - Kustutatud punkti 1")
    draft = MINIMAL.format(kind="terviktekst", body=body).replace(
        "<metaandmed>", "<metaandmed><seisund>EELNÕU</seisund>"
    )
    with pytest.raises(ParseSkipped, match="a draft"):
        parse_riigi_teataja(draft.encode())


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


def _cite_by_title(doc: ParsedDocument, eid: str) -> str:
    """The citation as the retrieval layer builds it for an instrument without an abbreviation (its title)."""
    p = _p(doc, eid)
    c = ProvisionCitationInput(doc.title, doc.instrument_type, "EE", p.top_kind, p.num_path, "et", ext=doc.ext)
    return get_module("ee").provision_citation(c)


def test_regulations_have_a_preamble_and_are_cited_by_title_and_issuer() -> None:
    doc = _doc("418082026030")  # Viimsi Vallavalitsus, määrus (a local government)
    assert doc.instrument_type == "regulation" and doc.work_uri == "/ee/regulation/1064248"
    assert doc.ext["issuer"] == "Viimsi Vallavalitsus" and "abbreviation" not in doc.ext and doc.short_title is None
    preamble = _p(doc, "preamble")
    assert preamble.text.startswith("Määrus kehtestatakse põhikooli- ja gümnaasiumiseaduse § 71 lõike 8")
    assert preamble.status == "in_force" and preamble.num_path == ["preamble:preambul"]
    first = _p(doc, "para-1")
    assert first.heading == "Reguleerimisala" and first.valid_from == date(2026, 8, 21) and first.valid_to is None
    prefix = f"{doc.title} (Viimsi Vallavalitsus)"
    assert _cite_by_title(doc, "para-2-lg-2") == f"{prefix} § 2 lg 2"
    assert _cite_by_title(doc, "preamble") == f"{prefix} preambul"
    state = _doc("000013060572")  # Kaitseminister, määrus
    assert state.instrument_type == "regulation" and state.ext["issuer"] == "Kaitseminister"
    assert _cite_by_title(state, "para-1") == "Passiivse kaitse vahendite loetelu (Kaitseminister) § 1"


@pytest.mark.parametrize(
    "name, message",
    [
        ("408122017018", "is empty \\(repealed\\)"),  # <sisu/> and a "Kehtetu" mark: a repealed act
        ("000000745959", "unstructured HTML fragment"),
        ("326032013013", "document type 'korraldus'"),
    ],
)
def test_texts_with_nothing_to_ingest_are_skipped_not_errors(name: str, message: str) -> None:
    with pytest.raises(ParseSkipped, match=message):
        parse_riigi_teataja(_raw(name))
    with pytest.raises(ParseSkipped, match="is empty"):
        parse_riigi_teataja(MINIMAL.format(kind="terviktekst", body="").encode())


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


# --------------------------------------------------------------------------- the acts in force (select)


def _retag(xml: bytes, **fields: str) -> bytes:
    """A member with other metadata: validity (``start``, ``end``), ``group``, ``kind`` (tekstiliik)."""
    text = xml.decode()
    if "group" in fields:
        text = re.sub(
            r"<terviktekstiGrupiID>\d+</terviktekstiGrupiID>",
            f"<terviktekstiGrupiID>{fields['group']}</terviktekstiGrupiID>",
            text,
        )
    if "kind" in fields:
        text = re.sub(r"<tekstiliik>[^<]*</tekstiliik>", f"<tekstiliik>{fields['kind']}</tekstiliik>", text)
    if "start" in fields:
        end = f"<kehtivuseLopp>{fields['end']}</kehtivuseLopp>" if "end" in fields else ""
        text = re.sub(
            r"<kehtivus>.*?</kehtivus>",
            f"<kehtivus><kehtivuseAlgus>{fields['start']}</kehtivuseAlgus>{end}</kehtivus>",
            text,
            flags=re.S,
        )
    return text.encode()


def _padded(xml: bytes, size: int) -> bytes:
    """The same act with an incompressible comment, so that its member is large in the archive."""
    noise = base64.b64encode(random.Random(5).randbytes(size)).decode()
    return xml.replace(b"</oigusakt>", f"<!--{noise}--></oigusakt>".encode())


def _select_members() -> dict[str, bytes]:
    env, kait, viimsi = _raw("000013014802"), _raw("000013060572"), _raw("418082026030")
    return {
        # the text in force of two acts: the latest of its group up to the archive's date (2026-10-11)
        "000013014802.xml": env,
        "000013060572.xml": kait,
        # an older text of the first group WITHOUT an end date (Maksukorralduse seadus has such texts): superseded
        "000010000001.xml": _retag(env, start="2001-01-01"),
        # a text that starts in the future and a second text with the same start and group: the later name wins
        "000010000002.xml": _retag(kait, start="2030-01-01"),
        "000010000004.xml": kait,
        # in force but a local government's: left out by exclude_issuers
        "418082026030.xml": viimsi,
        # a repealed act: its newest text is empty, and its older real text (no end date) must not come back
        "408122017018.xml": _raw("408122017018"),
        "000010000005.xml": _retag(env, group="1035322", start="2010-01-01"),
        # an act whose newest text ended before the date
        "000010000006.xml": _retag(kait, group="999", start="2001-01-01", end="2002-01-01"),
        # the original text, not a consolidated one
        "000010000003.xml": _retag(kait, kind="algtekst", group="555"),
        # an old HTML fragment, a decision that is not an act
        "000000745959.xml": _raw("000000745959"),
        "326032013013.xml": _raw("326032013013"),
        # an act whose page robots.txt disallows (/akt/12345678)
        "000012345678.xml": _retag(kait, group="777"),
        # a large member (incompressible): its head is read first, the body only as it becomes a group's newest text
        "000070000001.xml": _padded(_retag(env, group="888"), 9000),
        # an act of the pinned works: not taken a second time
        "130062026052.xml": _raw("130062026052"),
    }


SELECT = {
    "document_types": ["seadus", "määrus"],
    "as_of": "archive",
    "exclude_issuers": "(Vallavolikogu|Vallavalitsus)$",
}
CHUNK = 20000


def _stream_cache(
    dest: Path, members: dict[str, bytes], robots: str = "User-agent: *\n", pinned: tuple[str, ...] = ("130062026052",)
) -> bytes:
    """What PoliteHttp would have cached for a small archive read by the stream mode (ranges of CHUNK bytes)."""
    archive = _zip(members)
    offset, size, _ = locate_central_directory(archive[-65536:])
    directory = archive[offset : offset + size]
    _put(dest, "robots.txt", robots.encode())
    _put(dest, "avaandmed/ERT/LOEMIND.txt", b"Riigi Teataja avaandmed on genereeritud 2026-10-11 seisuga.\n")
    _put(dest, "xml.2026.zip.tail", archive[-65536:], 206)
    _put(dest, f"xml.2026.zip.central-directory@{offset}-{size}", directory, 206)
    for name, entry in read_central_directory(directory, {f"{i}.xml" for i in pinned}).items():  # the pinned works
        last = entry.offset + 30 + len(name) + 1024 + entry.compressed_size - 1
        _put(dest, f"{name}@{entry.offset}", archive[entry.offset : last + 1], 206)
    for kind, start, end, _members in plan_chunks(list(iter_central_directory(directory)), offset, CHUNK, CHUNK // 8):
        if kind == "chunk":
            _put(dest, f"xml.2026.zip.chunk@{start}-{end}", archive[start : end + 1], 206)
        else:
            head = min(end, start + HEAD_COMPRESSED + 4096)
            _put(dest, f"xml.2026.zip.large@{start}-{end}", archive[start : head + 1], 206)
            _put(dest, f"xml.2026.zip.member@{start}-{end}", archive[start : end + 1], 206)
    return archive


def _select_manifest(tmp_path: Path, **select: Any) -> Path:
    works = [{"name": "IKS", "abbreviation": "IKS", "group_id": 1045568, "versions": ["130062026052"]}]
    return _manifest(tmp_path, works, select={**SELECT, "chunk_bytes": CHUNK, **select})


def test_plan_covers_every_member_once_in_whole_ranges() -> None:
    archive = _zip(_select_members())
    offset, size, _ = locate_central_directory(archive[-65536:])
    entries = list(iter_central_directory(archive[offset : offset + size]))
    plan = plan_chunks(entries, offset, CHUNK, CHUNK // 8)
    assert sorted(e.name for _, _, _, ms in plan for e in ms) == sorted(e.name for e in entries)
    assert {kind for kind, *_ in plan} == {"chunk", "large"}
    previous_end = -1
    for kind, start, end, ms in plan:
        assert start == previous_end + 1 and start == ms[0].offset  # ranges follow each other without a gap
        assert end - start + 1 <= CHUNK or kind == "large"
        assert (kind == "large") == (ms[0].compressed_size > CHUNK // 8)
        previous_end = end
    assert previous_end == offset - 1  # the last range ends where the central directory starts


def test_select_takes_the_acts_in_force_on_the_archive_date(tmp_path: Path) -> None:
    _stream_cache(tmp_path / "cache", _select_members(), robots="User-agent: *\nDisallow: */akt/12345678/*\n")
    cache = tmp_path / "cache"
    connector = RiigiTeatajaConnector(_select_manifest(tmp_path), SOURCE, cache, offline=True)
    docs = list(connector.fetch())
    ids = [d.external_id for d in docs]
    # the pinned work first (one range request of its own), then the winners of the archive in the order of their names
    assert ids == ["ee-rt:130062026052", "ee-rt:000013014802", "ee-rt:000013060572", "ee-rt:000070000001"]
    by_id = {d.external_id: d for d in docs}
    assert by_id["ee-rt:000013014802"].content == _raw("000013014802")  # exact bytes after the CRC-32 check
    stats = by_id["ee-rt:000013060572"].metadata
    assert stats["selected"] == "latest consolidated text of its group up to 2026-10-11, in force on that date"
    assert stats["abbreviation"] == ""
    assert stats["group_id"] == "327634" and stats["act_page"] == f"https://{HOST}/akt/000013060572"
    assert parse_riigi_teataja(by_id["ee-rt:000013060572"].content)[0].instrument_type == "regulation"
    # every member was read; 8 groups had a consolidated text of a wanted type up to the date; 3 are delivered
    assert connector.stream_stats["members"] == len(_select_members()) and connector.stream_stats["groups"] == 8
    assert connector.stream_stats["selected"] == 3
    assert not (cache / HOST / "spool").exists()  # the scratch files are gone
    # the act whose page robots.txt disallows is counted, not ingested, and said so in one line
    assert connector.robots_skipped == ["000012345678"]
    assert len(connector.fetch_errors) == 1 and "1 acts in force were not ingested" in connector.fetch_errors[0]
    # offline, the ranges stay in the cache; online, each is dropped once used (the archive is 43 GB)
    kept = sorted(p.name for p in (cache / HOST).glob("xml.2026.zip.*") if not p.name.endswith(".meta.json"))
    assert any(".chunk@" in n for n in kept) and any(".member@" in n for n in kept)
    online = RiigiTeatajaConnector(_select_manifest(tmp_path), SOURCE, cache, offline=False)
    assert len(list(online.fetch())) == 4  # every request is answered from the cache: no network
    left = sorted(p.name for p in (cache / HOST).glob("xml.2026.zip.*") if not p.name.endswith(".meta.json"))
    assert not any(".chunk@" in n or ".large@" in n for n in left)
    # the whole body of a large member is dropped as well once it has been read (the others never were)
    assert sum(".member@" in n for n in left) == sum(".member@" in n for n in kept) - 2
    assert "xml.2026.zip.tail" in left and any("central-directory" in n for n in left)


def test_select_without_the_local_governments_filter_and_with_a_size_limit(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    _stream_cache(cache, _select_members())
    everything = _select_manifest(tmp_path, exclude_issuers=None)
    ids = [d.external_id for d in RiigiTeatajaConnector(everything, SOURCE, cache, offline=True).fetch()]
    assert "ee-rt:418082026030" in ids and "ee-rt:000012345678" in ids  # no robots rule in this cache
    limited = RiigiTeatajaConnector(_select_manifest(tmp_path, max_member_bytes=7000), SOURCE, cache, offline=True)
    ids = [d.external_id for d in limited.fetch()]
    # the two acts of more than 7 000 bytes (the padded one too) are not delivered, and no older text takes their place
    assert "ee-rt:000013014802" not in ids and "ee-rt:000070000001" not in ids and "ee-rt:000013060572" in ids
    assert (
        len(limited.fetch_errors) == 1
        and "2 acts in force are larger than max_member_bytes (7000)" in limited.fetch_errors[0]
    )


def test_select_part_reads_a_share_of_the_ranges(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    archive = _stream_cache(cache, _select_members())
    offset, size, _ = locate_central_directory(archive[-65536:])
    ranges = len(plan_chunks(list(iter_central_directory(archive[offset : offset + size])), offset, CHUNK, CHUNK // 8))
    taken: list[str] = []
    for i in range(1, 4):
        c = RiigiTeatajaConnector(_select_manifest(tmp_path, part=f"{i}/3"), SOURCE, cache, offline=True)
        taken += [d.external_id for d in c.fetch() if d.external_id != "ee-rt:130062026052"]
        assert 1 <= c.stream_stats["requests"] <= ranges
    whole = [
        d.external_id for d in RiigiTeatajaConnector(_select_manifest(tmp_path), SOURCE, cache, offline=True).fetch()
    ]
    # for trials: the winner of a group is chosen among the texts the part holds (here every group is whole in one part)
    assert len(taken) == len(set(taken)) and set(taken) == {i for i in whole if i != "ee-rt:130062026052"}


def test_range_answered_with_the_whole_file_is_repeated(tmp_path: Path) -> None:
    connector = RiigiTeatajaConnector(_select_manifest(tmp_path), SOURCE, tmp_path, sleep=lambda s: None)
    calls: list[str] = []

    class Flaky:
        def get(self, url: str, **kwargs: Any) -> tuple[bytes, dict[str, Any]]:
            calls.append(kwargs["headers"]["Range"])
            if len(calls) < 3:
                raise FetchError(f"{url}: the range request was not honoured (HTTP 200)")
            return b"data", {"retrieved_at": "2026-10-15T10:00:00+00:00"}

    connector.http = Flaky()  # type: ignore[assignment]
    assert connector._range(f"https://{HOST}/{ARCHIVE}", 10, 19, "k")[0] == b"data"
    assert calls == ["bytes=10-19"] * 3

    class Refusing:
        def get(self, url: str, **kwargs: Any) -> tuple[bytes, dict[str, Any]]:
            raise FetchError(f"{url}: HTTP 429 (not retried: a refusal is respected)")

    connector.http = Refusing()  # type: ignore[assignment]
    with pytest.raises(FetchError, match="HTTP 429"):
        connector._range(f"https://{HOST}/{ARCHIVE}", 10, 19, "k")


def test_select_manifest_is_validated(tmp_path: Path) -> None:
    works = [{"name": "X", "abbreviation": "X", "group_id": 1, "versions": ["115052015002"]}]
    for sel, message in (
        ({}, "document_types"),
        ({"document_types": "seadus"}, "list of dokumentLiik"),
        ({"document_types": ["seadus"], "as_of": "yesterday"}, "as_of"),
        ({"document_types": ["seadus"], "part": "0/3"}, "part must be"),
        ({"document_types": ["seadus"], "part": "4/3"}, "part must be"),
        ({"document_types": ["seadus"], "exclude_issuers": "("}, "regular expression"),
        ({"document_types": ["seadus"], "max_member_bytes": 0}, "positive integer"),
    ):
        with pytest.raises(ManifestError, match=message):
            list(RiigiTeatajaConnector(_manifest(tmp_path, works, select=sel), SOURCE, tmp_path).fetch())
    path = tmp_path / "none.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "source_id": SOURCE,
                "base_url": f"https://{HOST}",
                "archive": ARCHIVE,
                "user_agent": "ULIS-test (+https://example.org)",
            }
        )
    )
    with pytest.raises(ManifestError, match="selects none"):
        list(RiigiTeatajaConnector(path, SOURCE, tmp_path).fetch())


def test_source_uses_this_connector(catalog: Catalog) -> None:
    assert isinstance(build_connector(catalog.sources.get(SOURCE)), RiigiTeatajaConnector)
    manifest = yaml.safe_load((Path(__file__).parents[1] / "config/manifests/ee-riigi-teataja.yaml").read_text())
    assert [w["abbreviation"] for w in manifest["works"]] == ["PS", "KarS", "VÕS", "IKS", "TLS", "HMS"]
    # the acts in force: the whole archive, the acts and regulations of the state (the local governments are left out)
    select = manifest["select"]
    assert select["document_types"] == ["seadus", "määrus"] and select["as_of"] == "archive" and "part" not in select
    assert select["exclude_issuers"] == "(Vallavolikogu|Vallavalitsus|Linnavolikogu|Linnavalitsus)$"
    # the connector's own validation accepts the committed manifest
    connector = build_connector(catalog.sources.get(SOURCE))
    assert isinstance(connector, RiigiTeatajaConnector)
    assert connector._manifest()["select"]["max_member_bytes"] == 50_000_000


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

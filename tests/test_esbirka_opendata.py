"""Czechia: e-Sbírka open-data dumps (JSON) - connector slice, parser, citations, PostgreSQL."""

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
from ulis.ingestion.pipeline import IngestionReport, Ingestor, build_connector, resolve_uri
from ulis.jurisdictions.modules import ProvisionCitationInput, get_module
from ulis.retrieval.lookup import provision_at
from ulis.retrieval.search import SearchRequest, search
from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.esbirka_opendata import (
    DATASETS,
    EsbirkaOpenDataConnector,
    iter_records,
    parse_selection,
)
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.polite_http import FetchError
from ulis.sources.normalized import ParsedDocument, ParsedProvision, ParseSkipped
from ulis.sources.parsers.esbirka_opendata import EsbirkaParseError, parse_esbirka
from ulis.storage.object_store import LocalObjectStore
from ulis.verification.integrity import verify_source_document

FIX = Path(__file__).parent / "fixtures" / "cz-esbirka-opendata"
REPO = Path(__file__).parents[1]
SOURCE = "cz-esbirka-opendata"
UA = "ULIS-research-bot/0.1 (+https://github.com/CHEDFOX/Blue-Cat-Backend)"
USTAVA = "/cz/sb/1993/1"
CL2_ODST3 = (
    "Státní moc slouží všem občanům a lze ji uplatňovat jen v případech, v mezích a způsoby, které stanoví zákon."
)
ZOU = "/cz/sb/2019/110"
# Oldest first within each work (whole-act snapshots).
INGESTED = [
    "ustava-1993-01-01",
    "ustava-2002-06-01",
    "ustava-2026-01-01",
    "zakon-110-2019-2019-04-24",
    "zakon-110-2019-2025-08-01",
    "zakonik-prace-2006-06-07",
]


def _raw(name: str) -> bytes:
    return gzip.decompress((FIX / f"{name}.json.gz").read_bytes())


def _doc(name: str) -> ParsedDocument:
    docs = parse_esbirka(_raw(name))
    assert len(docs) == 1
    return docs[0]


def _p(doc: ParsedDocument, eid: str) -> ParsedProvision:
    return next(p for p in doc.provisions if p.eid == eid)


def _cite(doc: ParsedDocument, eid: str) -> str:
    p = _p(doc, eid)
    return get_module("cz").provision_citation(
        ProvisionCitationInput(
            doc.title,
            doc.instrument_type,
            "CZ",
            p.top_kind,
            p.num_path,
            doc.language,
            doc.ext,
            version_date=doc.version_date,
        )
    )


# --------------------------------------------------------------------------- parser


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"not json", "not JSON"),
        (b'{"format": "other"}', "unsupported document format"),
    ],
)
def test_unsupported_input_is_rejected(content: bytes, message: str) -> None:
    with pytest.raises(EsbirkaParseError, match=message):
        parse_esbirka(content)


def test_constitution_structure_dates_and_origin() -> None:
    doc = _doc("ustava-2026-01-01")
    assert doc.instrument_type == "constitution" and doc.title == "Ústava České republiky"
    assert doc.number == "1/1993 Sb." and doc.work_date == date(1992, 12, 16)
    assert doc.work_uri == USTAVA and doc.expression_uri == f"{USTAVA}/ces@2026-01-01"
    assert doc.language == "cs" and doc.authoritative is False
    assert doc.text_origin == "official_consolidation" and doc.validity_basis == "revision_snapshot"
    assert doc.ext == {"year": 1993, "number": "1", "collection": "sb", "kind": "ZAKONUST"}
    assert _p(doc, "preamble").text.startswith(
        "Česká národní rada se usnesla na tomto ústavním zákoně:\nPREAMBULE\nMy, občané"
    )
    c2 = _p(doc, "cl_2.odst_3")
    assert c2.num_path == ["art:2", "sub:3"] and c2.parent_eid == "cl_2" and c2.is_leaf
    assert c2.text == CL2_ODST3
    assert _p(doc, "cl_2").is_leaf is False and _p(doc, "cl_2").text == ""
    # An article with one unnumbered paragraph carries that paragraph as its own text.
    assert (
        _p(doc, "cl_3").text == "Součástí ústavního pořádku České republiky je Listina základních práv a svobod."
        and _p(doc, "cl_3").is_leaf
    )
    assert (_p(doc, "cl_2.odst_3").context_heading or "").startswith("HLAVA PRVNÍ")
    assert all(
        p.valid_from == date(2026, 1, 1) and p.valid_to is None and p.status == "in_force" for p in doc.provisions
    )
    assert len({p.eid for p in doc.provisions}) == len(doc.provisions)
    # Document order is the order of the text, not of the dump.
    order = [p.eid for p in doc.provisions if p.eid.startswith("cl_2.")]
    assert order == ["cl_2.odst_1", "cl_2.odst_2", "cl_2.odst_3", "cl_2.odst_4"]


def test_the_constitution_across_versions() -> None:
    first, mid, now = _doc("ustava-1993-01-01"), _doc("ustava-2002-06-01"), _doc("ustava-2026-01-01")
    # A version that ended before the next one: the period runs to the day after its last day.
    assert (first.version_date, _p(first, "cl_10").valid_to) == (date(1993, 1, 1), date(2000, 1, 1))
    assert _p(first, "cl_10").text.startswith("Ratifikované a vyhlášené mezinárodní smlouvy o lidských právech")
    assert _p(mid, "cl_10").text.startswith("Vyhlášené mezinárodní smlouvy, k jejichž ratifikaci dal Parlament souhlas")
    assert (mid.version_date, _p(mid, "cl_10").valid_to) == (date(2002, 6, 1), date(2003, 3, 1))
    # Article 1 had one unnumbered paragraph in 1993; later it is split into numbered ones.
    assert _p(first, "cl_1").is_leaf and _p(first, "cl_1").text.startswith("Česká republika je svrchovaný")
    assert _p(mid, "cl_1").text == "" and _p(mid, "cl_1.odst_1").num_path == ["art:1", "sub:1"]
    assert _p(now, "cl_17.odst_1").text.startswith("Volby do Poslanecké sněmovny se konají")
    assert _p(first, "cl_17.odst_1").text.startswith("Volby do obou komor se konají")


def test_act_with_headings_footnotes_and_superscripts() -> None:
    doc = _doc("zakon-110-2019-2025-08-01")
    assert (
        doc.instrument_type == "act"
        and doc.title == "Zákon o zpracování osobních údajů"
        and doc.number == "110/2019 Sb."
    )
    assert doc.work_date == date(2019, 3, 12) and doc.version_date == date(2025, 8, 1)
    p1 = _p(doc, "par_1")
    assert p1.num_path == ["sec:1"] and p1.heading == "Předmět úpravy" and p1.is_leaf
    assert p1.text.startswith("Tento zákon zapracovává příslušné předpisy Evropské unie¹), zároveň navazuje")
    assert (p1.note or "").startswith("1) Směrnice Evropského parlamentu a Rady (EU) 2016/680")
    assert (_p(doc, "par_2.pism_a").note or "").startswith("2) Nařízení Evropského parlamentu a Rady (EU) 2016/679")
    assert _p(doc, "par_2.pism_a").num_path == ["sec:2", "pt:a"] and _p(doc, "par_2.pism_a").parent_eid == "par_2"
    assert _p(doc, "par_2").text == "Tento zákon upravuje" and _p(doc, "par_2").heading == "Působnost zákona"
    assert _p(doc, "par_5").heading.startswith("Oprávnění ke zpracování osobních údajů")  # type: ignore[union-attr]
    first = _doc("zakon-110-2019-2019-04-24")
    assert (_p(first, "par_1").valid_from, _p(first, "par_1").valid_to) == (date(2019, 4, 24), date(2025, 1, 1))


def test_fragment_with_a_later_effective_day_is_left_out_of_a_version_that_ends_before() -> None:
    # Labour Code, first version (2006-06-07 to 2006-12-30): 2 651 of its 2 652 fragments take effect on 2007-01-01.
    doc = _doc("zakonik-prace-2006-06-07")
    assert [p.eid for p in doc.provisions] == ["priloha_0"] and doc.provisions[0].valid_to == date(2006, 12, 31)
    content = json.loads(_raw("zakonik-prace-2006-06-07"))
    content["fragments"] = [r for r in content["fragments"] if "/prilohy/" not in r[content["columns"].index("eli")]]
    with pytest.raises(ParseSkipped, match="no provision"):
        parse_esbirka(json.dumps(content).encode())
    # The same fragments are kept in a version that is still open (prospective from their own day).
    content = json.loads(_raw("zakon-110-2019-2025-08-01"))
    rows = content["fragments"]
    i = content["columns"].index("differs_from")
    target = next(r for r in rows if r[content["columns"].index("eli")].endswith("/par_5"))
    target[i] = "2027-01-01"
    later = parse_esbirka(json.dumps(content).encode())[0]
    assert (_p(later, "par_5").status, _p(later, "par_5").valid_from) == ("prospective", date(2027, 1, 1))
    kids = [p for p in later.provisions if p.parent_eid == "par_5"]
    assert kids and all(k.valid_from == date(2027, 1, 1) and k.status == "prospective" for k in kids)


def test_citations() -> None:
    def cite(path: list[str], year: int, number: str, title: str, itype: str = "act") -> str:
        return get_module("cz").provision_citation(
            ProvisionCitationInput(
                title, itype, "CZ", "section", path, "cs", {"year": year, "number": number, "collection": "sb"}
            )
        )

    assert cite(["sec:2910"], 2012, "89", "Zákon občanský zákoník") == "§ 2910 občanského zákoníku"
    assert cite(["sec:40", "sub:1"], 2009, "40", "Zákon trestní zákoník") == "§ 40 odst. 1 trestního zákoníku"
    assert (
        cite(["sec:52", "sub:1", "pt:a"], 2006, "262", "Zákon zákoník práce") == "§ 52 odst. 1 písm. a) zákoníku práce"
    )
    assert (
        cite(["art:2", "sub:3"], 1993, "1", "Ústava České republiky", "constitution")
        == "čl. 2 odst. 3 Ústavy České republiky"
    )
    assert (
        cite(["sec:5", "sub:2"], 2019, "110", "Zákon o zpracování osobních údajů")
        == "§ 5 odst. 2 zákona č. 110/2019 Sb."
    )
    assert (
        cite(["sec:3", "sub:1", "pt:b", "item:2"], 2019, "110", "Zákon x")
        == "§ 3 odst. 1 písm. b) bod 2 zákona č. 110/2019 Sb."
    )
    assert cite(["sec:3"], 2020, "10", "Vyhláška o věcech", "regulation") == "§ 3 vyhlášky č. 10/2020 Sb."
    assert cite(["sec:3"], 2020, "11", "Nařízení vlády o věcech", "regulation") == "§ 3 nařízení vlády č. 11/2020 Sb."
    assert cite(["preamble:preambule"], 1993, "1", "Ústava", "constitution") == "preambule Ústavy České republiky"
    assert cite(["sec:1"], 1963, "99", "Občanský soudní řád") == "§ 1 občanského soudního řádu"
    assert _cite(_doc("ustava-2026-01-01"), "cl_2.odst_3") == "čl. 2 odst. 3 Ústavy České republiky"
    assert _cite(_doc("zakon-110-2019-2025-08-01"), "par_2.pism_a") == "§ 2 písm. a) zákona č. 110/2019 Sb."
    assert resolve_uri(f"{ZOU}/~par_2.pism_a") == (ZOU, "par_2.pism_a")


# --------------------------------------------------------------------------- connector


def _files() -> dict[str, Path]:
    return {name: FIX / "dumps" / filename for name, filename in DATASETS.items()}


def _manifest(tmp_path: Path, **selection: Any) -> Path:
    body = {
        "schema_version": 1,
        "source_id": SOURCE,
        "base_url": "https://opendata.eselpoint.gov.cz/datove-sady-esbirka/",
        "user_agent": UA,
        "crawl_delay_seconds": 5,
        "selection": {"as_of": "2026-10-14", "collections": ["sb"], **selection},
    }
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(body, allow_unicode=True))
    return path


def _fetch(tmp_path: Path, **selection: Any) -> list[RawDocument]:
    conn = EsbirkaOpenDataConnector(_manifest(tmp_path, **selection), SOURCE, tmp_path / "cache", files=_files())
    return list(conn.fetch())


def test_slice_selects_versions_and_orders_them_oldest_first(tmp_path: Path) -> None:
    docs = _fetch(tmp_path, full_history=["sb/2019/110", "sb/1918/8"])
    # The as-promulgated text of 8/1918 starts the same day as its first consolidated text, so only that is kept.
    assert [d.external_id for d in docs] == [
        "esbirka:sb/1918/8/1918-11-04",
        "esbirka:sb/1918/8/1919-11-28",
        "esbirka:sb/2019/110/2019-04-24",
        "esbirka:sb/2019/110/2025-01-01",
        "esbirka:sb/2019/110/2025-07-01",
        "esbirka:sb/2019/110/2025-08-01",
    ]
    last = docs[-1]
    assert last.uri == "https://e-sbirka.gov.cz/sb/2019/110/2025-08-01" and last.media_type == "application/json"
    body = json.loads(last.content)
    assert body["format"] == "esbirka-opendata-slice/1" and body["as_of"] == "2026-10-14"
    assert (
        body["version"]["znění-datum-účinnosti-od"] == "2025-08-01"
        and body["metadata"]["metadata-citace"] == "110/2019 Sb."
    )
    assert set(body["datasets"]) == set(DATASETS) and len(body["fragments"]) == last.metadata["fragments"] == 650
    row = dict(zip(body["columns"], body["fragments"][0], strict=True))
    assert row["type"] == "Virtual_Document" and row["eli"].endswith("/2025-08-01/dokument")
    # Fragment text is copied unchanged from the dump (markup included).
    texts = [dict(zip(body["columns"], r, strict=True))["text"] or "" for r in body["fragments"]]
    assert any(t.startswith("<var>§ 1</var>") for t in texts)
    # Re-reading gives the same bytes (the slice is reused).
    again = _fetch(tmp_path, full_history=["sb/2019/110", "sb/1918/8"])
    assert [d.content for d in again] == [d.content for d in docs]


def test_current_as_of_in_force_years_and_skip(tmp_path: Path) -> None:
    current = _fetch(tmp_path / "a", current=["sb/2019/110", "sb/1918/8"])
    assert [d.external_id for d in current] == ["esbirka:sb/1918/8/1919-11-28", "esbirka:sb/2019/110/2025-08-01"]
    earlier = _fetch(tmp_path / "b", full_history=["sb/2019/110"], as_of="2025-03-01")
    assert [d.external_id for d in earlier] == ["esbirka:sb/2019/110/2019-04-24", "esbirka:sb/2019/110/2025-01-01"]
    years = _fetch(tmp_path / "c", in_force=True, in_force_years=[2019, 2019])
    assert [d.external_id for d in years] == ["esbirka:sb/2019/110/2025-08-01"]
    rest = _fetch(tmp_path / "d", in_force=True, skip=["sb/2019/110"])
    assert [d.external_id for d in rest] == ["esbirka:sb/1918/8/1919-11-28"]
    assert _fetch(tmp_path / "e", current=["sb/2000/999"]) == []


def test_connector_refuses_bad_manifests_and_uncached_downloads(tmp_path: Path) -> None:
    ok: dict[str, Any] = {"full_history": ["sb/2019/110"]}
    for bad in (
        {"full_history": ["2019/110"]},
        {"full_history": ["sb/2019/110/2025-01-01"]},
        {"collections": ["sm"]},
        {"as_of": "tomorrow"},
        {"full_history": [], "current": []},
        {"in_force": True, "in_force_years": [2020, 2019]},
        {"in_force": True, "in_force_years": [2019]},
    ):
        with pytest.raises(ManifestError):
            _fetch(tmp_path / "x", **{**ok, **bad})
    manifest = _manifest(tmp_path, **ok)
    body = yaml.safe_load(manifest.read_text())
    for key, value in (
        ("schema_version", 2),
        ("source_id", "other"),
        ("base_url", "https://evil.example/"),
        ("user_agent", "bot"),
        ("crawl_delay_seconds", 1),
    ):
        changed = {**body, key: value}
        manifest.write_text(yaml.safe_dump(changed))
        with pytest.raises(ManifestError):
            list(EsbirkaOpenDataConnector(manifest, SOURCE, tmp_path / "cache", files=_files()).fetch())
    manifest.write_text(yaml.safe_dump(body))
    with pytest.raises(FetchError, match="offline and not cached"):
        list(EsbirkaOpenDataConnector(manifest, SOURCE, tmp_path / "empty-cache", offline=True).fetch())
    with pytest.raises(ManifestError):
        parse_selection({})


def test_dump_reader_streams_items_and_detects_truncation() -> None:
    dump = gzip.decompress((FIX / "dumps" / DATASETS["metadata"]).read_bytes())
    items = list(iter_records([dump[i : i + 700] for i in range(0, len(dump), 700)]))
    assert len(items) == 2 and all(json.loads(i)["typ"] == "právní-akt-metadata" for i in items)
    with pytest.raises(FetchError, match="truncated"):
        list(iter_records([dump[: len(dump) // 2]]))
    with pytest.raises(FetchError, match="not an e-Sbírka dump"):
        list(iter_records([b"<html></html>"]))


def test_committed_manifest_loads_and_source_uses_this_connector(catalog: Catalog, tmp_path: Path) -> None:
    path = REPO / "config/manifests/cz-esbirka-opendata.yaml"
    connector = EsbirkaOpenDataConnector(path, SOURCE, tmp_path, offline=True)
    manifest = connector._manifest()  # noqa: SLF001
    selection = parse_selection(manifest)
    assert selection.in_force is True and selection.collections == ("sb",)
    assert {
        "sb/1993/1",
        "sb/2012/89",
        "sb/2009/40",
        "sb/2006/262",
        "sb/2019/110",
        "sb/1963/99",
        "sb/1961/141",
    } <= selection.full_history
    assert len(selection.full_history) >= 25
    assert isinstance(build_connector(catalog.sources.get(SOURCE)), EsbirkaOpenDataConnector)
    source = catalog.sources.get(SOURCE)
    assert source.authority == "official_non_authentic" and source.licence.review_status == "approved"


# --------------------------------------------------------------------------- PostgreSQL


class _Fixtures:
    def fetch(self) -> Iterator[RawDocument]:
        for name in INGESTED:
            yield RawDocument(
                f"esbirka:{name}",
                f"https://e-sbirka.gov.cz/{name}",
                "application/json",
                _raw(name),
                datetime(2026, 10, 14, 18, 45, tzinfo=UTC),
                {},
            )


@pytest.fixture(scope="module")
def cz_ingested(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, ingested: IngestionReport
) -> IngestionReport:
    with session_factory() as session:
        report = Ingestor(session, catalog, store).ingest_source(SOURCE, actor="pytest", connector=_Fixtures())
        session.commit()
    return report


def _search(session: Session, catalog: Catalog, **kw: Any) -> dict[str, Any]:
    return search(session, catalog, SearchRequest(jurisdiction="CZ", **kw), today=date(2026, 10, 14))


@pytest.mark.db
def test_search_finds_exact_text_with_citation_and_limitations(
    session: Session, catalog: Catalog, cz_ingested: IngestionReport
) -> None:
    assert cz_ingested.errors == [] and cz_ingested.conflicts == []
    assert cz_ingested.instruments_created == 3 and cz_ingested.instrument_versions_created == len(INGESTED)
    res = _search(session, catalog, q="Státní moc slouží všem občanům a lze ji uplatňovat", as_of=date(2026, 10, 14))
    hit = res["results"][0]
    assert hit["citation"] == "čl. 2 odst. 3 Ústavy České republiky"
    assert hit["text"] == CL2_ODST3
    codes = {lim["code"] for lim in hit["limitations"]}
    assert {
        "PROFILE_UNVERIFIED",
        "OFFICIAL_CONSOLIDATION_REPRODUCED",
        "SOURCE_NOT_AUTHENTIC",
        "SNAPSHOT_DATES",
    } <= codes
    assert hit["source"]["attribution"].startswith("Zdroj: e-Sbírka")
    zou = _search(session, catalog, q="Subjektem údajů se rozumí fyzická osoba")
    assert zou["results"][0]["citation"] == "§ 3 zákona č. 110/2019 Sb."


@pytest.mark.db
def test_point_in_time_follows_the_time_versions(
    session: Session, catalog: Catalog, cz_ingested: IngestionReport
) -> None:
    cl10 = f"{USTAVA}/~cl_10"
    old = provision_at(session, catalog, cl10, date(1995, 1, 1))
    assert old["status_on_date"] == "in_force" and old["version"]["text"].startswith(
        "Ratifikované a vyhlášené mezinárodní smlouvy"
    )
    assert (old["version"]["valid_from"], old["version"]["valid_to"]) == ("1993-01-01", "2000-01-01")
    mid = provision_at(session, catalog, cl10, date(2002, 7, 1))
    assert mid["version"]["text"].startswith(
        "Vyhlášené mezinárodní smlouvy, k jejichž ratifikaci dal Parlament souhlas"
    )
    now = provision_at(session, catalog, cl10, date(2026, 10, 14))
    assert now["version"]["valid_from"] == "2026-01-01" and now["version"]["valid_to"] is None
    assert now["provision"]["citation"] == "čl. 10 Ústavy České republiky"
    # Versions between 2000 and 2002 and after 2003 up to 2025 were not ingested, so those dates have no version.
    assert provision_at(session, catalog, cl10, date(2001, 1, 1))["version"] is None
    # 110/2019: the first version ends on 2024-12-31 and the next one is not ingested.
    par1 = provision_at(session, catalog, f"{ZOU}/~par_1", date(2020, 1, 1))["version"]
    assert (par1["valid_from"], par1["valid_to"]) == ("2019-04-24", "2025-01-01")
    assert provision_at(session, catalog, f"{ZOU}/~par_1", date(2025, 3, 1))["version"] is None
    assert provision_at(session, catalog, f"{ZOU}/~par_1", date(2026, 10, 14))["version"]["valid_from"] == "2025-08-01"
    # Labour Code: the annex is the one part of the first version (2006-06-07 to 2006-12-30) that applied.
    annex = provision_at(session, catalog, "/cz/sb/2006/262/~priloha_0", date(2006, 8, 1))
    assert annex["version"]["valid_to"] == "2006-12-31"


@pytest.mark.db
def test_reingest_is_idempotent_and_sources_verify(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, cz_ingested: IngestionReport
) -> None:
    with session_factory() as session:
        again = Ingestor(session, catalog, store).ingest_source(SOURCE, actor="pytest", connector=_Fixtures())
        assert again.documents_unchanged == len(INGESTED) and again.provision_versions_created == 0
        ids = session.scalars(select(m.SourceDocument.id).where(m.SourceDocument.source_id == SOURCE)).all()
        results = [verify_source_document(session, catalog, store, i, checker="pytest") for i in ids]
        assert len(results) == len(INGESTED) and all(r["result"] == "pass" for r in results)

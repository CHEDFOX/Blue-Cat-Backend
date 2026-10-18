"""BOE consolidated legislation: parser, connector and Spanish search behaviour (real State law).

Fixtures are unmodified BOE open-data API responses (AEBOE reuse conditions); see
tests/fixtures/boe_consolidada/README.md.
"""

from __future__ import annotations

import gzip
import re
import shutil
from collections.abc import Iterator
from datetime import UTC, date, datetime
from functools import cache
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
from ulis.sources.connectors.boe_consolidada import BoeConsolidadaConnector, robots_rules_by_id
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.polite_http import FetchError
from ulis.sources.normalized import ParsedDocument, ParsedProvision, ParseSkipped
from ulis.sources.parsers.boe_consolidada import (
    RANGOS,
    BoeParseError,
    _Node,
    ordinal_number,
    parse_boe_consolidada,
    split_units,
)
from ulis.storage.object_store import LocalObjectStore
from ulis.verification.integrity import verify_source_document

FIX = REPO_ROOT / "tests" / "fixtures" / "boe_consolidada" / "cache"
FILES = FIX / "www.boe.es" / "legislacion-consolidada"
CE, LOPDGDD, CP = "BOE-A-1978-31229", "BOE-A-2018-16673", "BOE-A-1995-25444"
# Lower-ranking State norms added when the manifest was widened to the whole list: a Real Decreto repealed in full on
# 2017-04-01, an Orden, an un-numbered Resolución and an Acuerdo Internacional.
RD_REPEALED, ORDEN, RESOLUCION, ACUERDO = "BOE-A-2001-17164", "BOE-A-2007-8105", "BOE-A-2016-5431", "BOE-A-2020-4408"


@cache
def _raw(boe_id: str) -> bytes:
    return gzip.decompress((FILES / f"{boe_id}.xml.gz").read_bytes())


@cache
def _docs(boe_id: str) -> list[ParsedDocument]:
    return parse_boe_consolidada(_raw(boe_id))


def _versions(boe_id: str, eid: str) -> list[tuple[ParsedDocument, ParsedProvision]]:
    rows = [(d, p) for d in _docs(boe_id) for p in d.provisions if p.eid == eid]
    return sorted(rows, key=lambda r: (r[1].valid_from or date.max, r[0].expression_uri))


def _cite(boe_id: str, eid: str) -> str:
    doc, prov = _versions(boe_id, eid)[-1]
    return get_module("es").provision_citation(
        ProvisionCitationInput(
            doc.short_title or doc.title, doc.instrument_type, "ES", prov.top_kind, prov.num_path, "es", ext=doc.ext
        )
    )


# --------------------------------------------------------------------------- parser


def test_constitution_versions_are_dated_by_the_source() -> None:
    docs = _docs(CE)
    assert [d.version_date for d in docs] == [
        date(1978, 12, 29),
        date(1992, 8, 28),
        date(2011, 9, 27),
        date(2024, 2, 17),
        date(2026, 5, 20),
    ]
    first = docs[0]
    assert (first.jurisdiction_id, first.instrument_type, first.work_uri, first.language) == (
        "ES",
        "constitution",
        "/es/c/BOE-A-1978-31229",
        "es",
    )
    assert first.text_origin == "official_revised" and not first.authoritative
    assert first.validity_basis == "source_periods" and first.consolidation_kind == "version_delta"
    assert first.work_date == date(1978, 12, 27) and first.currency_date == date(2026, 5, 20)
    assert first.ext["boe_id"] == CE and first.ext["rango"] == "Constitución"
    assert first.expression_uri == "/es/c/BOE-A-1978-31229/spa@1978-12-29;actualizacion=20260520T074424Z"
    assert {e.event_type for e in first.events} >= {"publication", "entry_into_force"}

    # Art. 13.2 was amended in 1992 ("activo" -> "activo y pasivo"); 13.1 kept its words.
    v13_2 = _versions(CE, "art-13-2")
    assert [p.valid_from for _, p in v13_2] == [date(1978, 12, 29), date(1992, 8, 28)]
    assert "sufragio activo en las elecciones municipales" in v13_2[0][1].text
    assert "sufragio activo y pasivo en las elecciones municipales" in v13_2[1][1].text
    assert v13_2[0][1].valid_to is None  # derived later from the next version
    assert "Reforma de 27 de agosto de 1992" in (v13_2[1][1].note or "")
    assert "Ref. BOE-A-1992-20403" not in v13_2[1][1].text  # notes are never law text
    assert [p.valid_from for _, p in _versions(CE, "art-13-1")] == [date(1978, 12, 29)]
    article = _versions(CE, "art-13")
    assert len(article) == 2 and article[0][1].heading == "Artículo 13" and not article[0][1].is_leaf
    # An unnumbered paragraph after apartado 2 belongs to it.
    art24_2 = _versions(CE, "art-24-2")[0][1]
    assert art24_2.text.startswith("2. Asimismo, todos tienen derecho al Juez ordinario")
    assert art24_2.text.endswith("no se estará obligado a declarar sobre hechos presuntamente delictivos.")
    assert art24_2.parent_eid == "art-24" and art24_2.num_path == ["24", "2"]


def test_citations_in_spanish_style() -> None:
    assert _cite(CE, "art-24-1") == "art. 24.1 CE"
    assert _cite(CE, "da-1") == "DA 1.ª CE"
    assert _cite(CE, "dd") == "DD CE"
    assert _cite(CE, "dd-3") == "DD, apdo. 3 CE"
    assert _cite(CE, "preambulo") == "CE, preámbulo"
    assert _cite(LOPDGDD, "art-2-2-a") == "art. 2.2.a) LOPDGDD"
    assert _cite(LOPDGDD, "dt-1") == "DT 1.ª LOPDGDD"
    assert _cite(CP, "art-138-1") == "art. 138.1 CP"
    assert _cite(CP, "art-22-1o") == "art. 22.1.ª CP"
    assert _cite(CP, "art-183ter") == "art. 183 ter CP"
    assert _cite(CP, "arts-617-622") == "arts. 617 a 622 CP"
    module = get_module("es")
    other = {"boe_id": "BOE-A-2022-1", "rango": "Ley", "numero_oficial": "1/2022"}
    assert (
        module.provision_citation(ProvisionCitationInput("Ley 1/2022", "law", "ES", "article", ["3", "1"], "es", other))
        == "art. 3.1 de la Ley 1/2022"
    )
    decree = {"boe_id": "BOE-A-2015-11430", "rango": "Real Decreto Legislativo", "numero_oficial": "2/2015"}
    cite = module.provision_citation(
        ProvisionCitationInput("ET", "royal_legislative_decree", "ES", "decree_article", ["único"], "es", decree)
    )
    assert cite == "art. único del Real Decreto Legislativo 2/2015"
    assert module.validate_ext(_docs(CE)[0].ext)["boe_id"] == CE


def test_penal_code_repeals_ranges_and_expired_blocks() -> None:
    # Art. 309 has its own "(Derogado)" version.
    last = _versions(CP, "art-309")[-1][1]
    assert (last.status, last.valid_from, last.text) == ("repealed", date(2013, 1, 17), "")
    assert (last.note or "").startswith("(Derogado) Se deroga por la disposición derogatoria única.1")
    # Arts. 617-622 are repealed by a range block "Artículos 617 a 622. (Derogados)" from 2015-07-01.
    for eid in ("art-617", "art-617-1", "art-622"):
        doc, prov = _versions(CP, eid)[-1]
        assert (prov.status, prov.valid_from, doc.version_date) == ("repealed", date(2015, 7, 1), date(2015, 3, 31))
        assert (prov.note or "").startswith("Artículos 617 a 622: (Derogados)")
    # Art. 183 ter stops being shown from 2022-09-07 without a recorded repeal: explicit end + note.
    ter = _versions(CP, "art-183ter")[-1][1]
    assert (ter.status, ter.valid_to) == ("in_force", date(2022, 9, 7))
    assert "fecha_caducidad" in (ter.note or "")
    # Art. 15 lost its numbered apartados in 2015; the apartados end when the new wording starts.
    assert [(p.valid_from, p.valid_to) for _, p in _versions(CP, "art-15-1")] == [(date(1996, 5, 24), date(2015, 7, 1))]
    # Art. 138: one paragraph until 2015, then apartados and letters.
    art138 = _versions(CP, "art-138")
    assert art138[0][1].is_leaf and not art138[-1][1].is_leaf and art138[-1][1].valid_from == date(2015, 7, 1)
    assert _versions(CP, "art-138-2-a")[0][1].text.startswith("a) cuando concurra en su comisión")
    # The source lists two different texts for art. 274 with the same dates: the second listing is
    # kept apart so that ingestion reports the discrepancy.
    listings = [d for d, _ in _versions(CP, "art-274") if "/listado-2;" in d.expression_uri]
    assert len(listings) == 1 and listings[0].version_date == date(2003, 11, 26)
    # The original text is published 1995-11-24 and enters into force 1996-05-24.
    assert _docs(CP)[0].version_date == date(1995, 11, 24)
    assert _versions(CP, "art-1")[0][1].valid_from == date(1996, 5, 24)


def test_parsed_documents_are_consistent() -> None:
    for boe_id in (CE, LOPDGDD, CP):
        docs = _docs(boe_id)
        keys = [(d.expression_uri, p.eid, p.status, p.valid_from) for d in docs for p in d.provisions]
        assert len(keys) == len(set(keys))  # one version per provision, status and start in each document
        for d in docs:
            for p in d.provisions:
                assert p.status != "repealed" or p.text == ""
                assert p.valid_to is None or p.valid_from is None or p.valid_to > p.valid_from


def test_splitting_follows_the_printed_numbering() -> None:
    root = _Node(key="", kind="article", label="")
    paras = [
        ("1. Uno.", "parrafo"),
        ("2. Dos:", "parrafo"),
        ("a) letra a.", "parrafo"),
        ("b) letra b.", "parrafo_2"),
        ("Párrafo sin número.", "parrafo"),
        ("1. No sigue la secuencia.", "parrafo"),
        ("3. Tres, con cita: «Artículo 5.", "parrafo"),
        ("1. Texto citado.»", "parrafo"),
        ("4. Cuatro.", "parrafo"),
        ("5. Sangrado.", "sangrado"),
        ("1.ª Primera.", "parrafo"),
    ]
    split_units(paras, root)
    assert [c.key for c in root.children] == ["1", "2", "3", "4"]
    two = root.children[1]
    assert [c.key for c in two.children] == ["a", "b"]
    assert two.children[1].lines == ["b) letra b.", "Párrafo sin número.", "1. No sigue la secuencia."]
    assert root.children[2].lines == ["3. Tres, con cita: «Artículo 5.", "1. Texto citado.»"]
    four = root.children[3]
    assert four.lines == ["4. Cuatro.", "5. Sangrado."] and [c.label for c in four.children] == ["1.ª"]
    assert root.text() == "\n".join(t for t, _ in paras)


def test_ordinal_words() -> None:
    assert ordinal_number("primera") == 1
    assert ordinal_number("Décimotercera") == 13
    assert ordinal_number("decimoctava") == 18
    assert ordinal_number("undécima") == 11
    assert ordinal_number("vigesimoprimera") == 21
    assert ordinal_number("vigésima primera") == 21
    assert ordinal_number("única") is None


def test_lower_ranking_rangos_are_read() -> None:
    expected = {
        ORDEN: ("ministerial_order", "/es/o/BOE-A-2007-8105", "CUL/1014/2007"),
        RESOLUCION: ("resolution", "/es/res/BOE-A-2016-5431", None),
        ACUERDO: ("international_agreement", "/es/ai/BOE-A-2020-4408", None),
        RD_REPEALED: ("royal_decree", "/es/rd/BOE-A-2001-17164", "996/2001"),
    }
    for boe_id, (kind, uri, number) in expected.items():
        first = _docs(boe_id)[0]
        assert (first.instrument_type, first.work_uri, first.number) == (kind, uri, number)
        assert first.jurisdiction_id == "ES" and first.language == "es" and first.authoritative is False
        assert first.validity_basis == "source_periods" and first.provisions
    # Every instrument type the parser can produce is declared by the Spanish profile, with an ELI code each.
    profile = Catalog.load(REPO_ROOT / "config").jurisdictions.get("ES")
    assert {kind for kind, _ in RANGOS.values()} <= set(profile.instrument_types)
    assert len({code for _, code in RANGOS.values()}) == len(RANGOS)


def test_citations_of_lower_ranking_norms() -> None:
    assert _cite(ORDEN, "art-1") == "art. 1 de la Orden CUL/1014/2007"
    assert _cite(ORDEN, "art-1-1") == "art. 1.1 de la Orden CUL/1014/2007"
    assert _cite(RD_REPEALED, "rd-art-1") == "art. 1 del Real Decreto 996/2001"
    # No official number: the identifying head of the title, without the clause that says what the norm does.
    assert _cite(RESOLUCION, "bloque-primero") == (
        "Resolución de 31 de mayo de 2016, de la Dirección General de Seguros y Fondos de Pensiones, Primero"
    )
    module = get_module("es")
    assert module.validate_ext(_docs(RD_REPEALED)[0].ext)["fecha_derogacion"] == "2017-04-01"
    cite = module.provision_citation(
        ProvisionCitationInput(
            "Acuerdo " + "muy largo " * 40,
            "agreement",
            "ES",
            "article",
            ["2"],
            "es",
            {"boe_id": "BOE-A-2020-1", "rango": "Acuerdo"},
        )
    )
    assert cite.endswith(" [...], art. 2") and len(cite) < 200


def test_a_norm_repealed_in_full_ends_on_the_date_the_source_gives() -> None:
    first = _docs(RD_REPEALED)[0]
    assert first.ext["fecha_derogacion"] == "2017-04-01" and first.ext["vigencia_agotada"] is True
    repeal = [e for e in first.events if e.event_type == "repeal"]
    assert [(e.date, e.locator) for e in repeal] == [(date(2017, 4, 1), "metadatos/fecha_derogacion")]
    description = repeal[0].description or ""
    assert "BOE-A-2017-3550" in description and "Real Decreto 316/2017" in description
    # The consolidated text still shows the last wording, but no provision of the norm is open-ended.
    versions = [p for d in _docs(RD_REPEALED) for p in d.provisions if p.status == "in_force"]
    assert versions and all(p.valid_to == date(2017, 4, 1) for p in versions)
    assert all("repealed in full on 2017-04-01" in (p.note or "") for p in versions)
    # A norm in force is untouched.
    assert all(p.valid_to is None for p in _docs(ORDEN)[-1].provisions if p.eid == "art-1")
    assert "fecha_derogacion" not in _docs(ORDEN)[0].ext


def test_a_text_without_provisions_is_skipped_not_an_error() -> None:
    empty = re.sub(rb"<bloque\b.*?</bloque>", b"", _raw(ORDEN), flags=re.S)
    with pytest.raises(ParseSkipped, match="holds no provision"):
        parse_boe_consolidada(empty)


def test_rejects_unexpected_documents() -> None:
    with pytest.raises(BoeParseError, match="API status"):
        parse_boe_consolidada(b"<response><status><code>404</code><text>no</text></status><data/></response>")
    regional = _raw(CE).replace(
        b'<ambito codigo="1">Estatal</ambito>', '<ambito codigo="2">Autonómico</ambito>'.encode()
    )
    with pytest.raises(BoeParseError, match="State"):
        parse_boe_consolidada(regional)
    with pytest.raises(BoeParseError, match="unsafe"):
        parse_boe_consolidada(b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY e "x">]><response>&e;</response>')


# --------------------------------------------------------------------------- connector


def _cache_copy(dest: Path) -> Path:
    shutil.copytree(FIX, dest)
    for gz in dest.rglob("*.xml.gz"):
        gz.with_suffix("").write_bytes(gzip.decompress(gz.read_bytes()))
        gz.unlink()
    return dest


def _manifest(tmp_path: Path, ids: list[str], **overrides: Any) -> Path:
    manifest = {
        "schema_version": 1,
        "source_id": "es-boe-consolidada",
        "api_base": "https://www.boe.es/datosabiertos/api/legislacion-consolidada",
        "user_agent": "ULIS-test (+https://example.org)",
        "crawl_delay_seconds": 5,
        "documents": [{"id": i} for i in ids],
        **overrides,
    }
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(manifest))
    return path


def test_connector_reads_the_cache_offline(tmp_path: Path) -> None:
    cache_dir = _cache_copy(tmp_path / "cache")
    conn = BoeConsolidadaConnector(_manifest(tmp_path, [CE]), "es-boe-consolidada", cache_dir, offline=True)
    docs = list(conn.fetch())
    assert len(docs) == 1 and conn.requests_made == 0
    raw = docs[0]
    assert raw.content == _raw(CE) and raw.external_id == f"boe-consolidada:{CE}"
    assert raw.uri == f"https://www.boe.es/datosabiertos/api/legislacion-consolidada/id/{CE}"
    assert raw.retrieved_at == datetime.fromisoformat("2026-10-14T07:51:47.523186+00:00")
    missing = BoeConsolidadaConnector(
        _manifest(tmp_path, ["BOE-A-2000-323"]), "es-boe-consolidada", cache_dir, offline=True
    )
    with pytest.raises(FetchError, match="offline and not cached"):
        list(missing.fetch())


def test_connector_validates_the_manifest(tmp_path: Path) -> None:
    for overrides, message in [
        ({"user_agent": "python-urllib"}, "user_agent"),
        ({"api_base": "https://mirror.example.org/api"}, "api_base"),
        ({"documents": [{"id": "../../etc/passwd"}]}, "invalid or repeated BOE identifier"),
        ({"documents": [{"id": CE}, {"id": CE}]}, "invalid or repeated BOE identifier"),
        ({"crawl_delay_seconds": 0.5}, "crawl_delay_seconds"),
        ({"source_id": "other"}, "source_id"),
    ]:
        conn = BoeConsolidadaConnector(_manifest(tmp_path, [CE], **overrides), "es-boe-consolidada", tmp_path)
        with pytest.raises(ManifestError, match=message):
            list(conn.fetch())


def test_connector_skips_a_missing_norm_and_a_disallowed_one(tmp_path: Path) -> None:
    cache_dir = _cache_copy(tmp_path / "cache")
    robots = (
        "User-agent: *\nDisallow: /datos/pdfs/\nDisallow: /*BOE-A-2007-8105$\n"
        "Disallow: /buscar/act.php?id=BOE-A-1978-31229\n"
    )
    (cache_dir / "www.boe.es" / "robots.txt").write_text(robots)
    gone = cache_dir / "www.boe.es" / "legislacion-consolidada" / "BOE-A-2000-323.xml"
    gone.write_bytes(b"")
    gone.with_name(gone.name + ".meta.json").write_text('{"url": "x", "status": 404}')
    ids = ["BOE-A-2000-323", ORDEN, CE, RESOLUCION]
    conn = BoeConsolidadaConnector(_manifest(tmp_path, ids), "es-boe-consolidada", cache_dir, offline=True)
    docs = list(conn.fetch())
    # Only the Constitution and the Resolución are returned; the run did not stop.
    assert [d.external_id for d in docs] == [f"boe-consolidada:{CE}", f"boe-consolidada:{RESOLUCION}"]
    assert len(conn.fetch_errors) == 2
    assert "BOE-A-2000-323: not found (404)" in conn.fetch_errors[0]
    assert "BOE-A-2007-8105" in conn.fetch_errors[1] and "disallowed by robots.txt" in conn.fetch_errors[1]


def test_robots_rules_are_indexed_by_the_identifier_they_name() -> None:
    by_id, generic = robots_rules_by_id(
        "User-agent: *\nDisallow: /datos/pdfs/\nDisallow: /*BOE-A-2013-11511$\n"
        "Disallow: /buscar/doc.php?id=BOE-A-2013-11511\n"
    )
    assert len(by_id["BOE-A-2013-11511"]) == 2 and len(generic) == 1
    path = "/datosabiertos/api/legislacion-consolidada/id/BOE-A-2013-11511"
    assert any(rule.match(path) for rule in by_id["BOE-A-2013-11511"])
    assert not any(rule.match(path.replace("11511", "11512")) for rule in (*by_id["BOE-A-2013-11511"], *generic))


def test_the_committed_manifest_covers_the_state_list_and_passes_validation(tmp_path: Path) -> None:
    manifest_path = REPO_ROOT / "config" / "manifests" / "es-boe-consolidada.yaml"
    conn = BoeConsolidadaConnector(manifest_path, "es-boe-consolidada", tmp_path)
    manifest = conn._manifest()  # noqa: SLF001 - the connector's own validation (identifiers, no repeats, delay)
    ids = [d["id"] for d in manifest["documents"]]
    # The six pilot norms come first; the whole State list is far larger than the pilot.
    assert ids[:6] == [CE, "BOE-A-1889-4763", CP, LOPDGDD, "BOE-A-2015-10565", "BOE-A-2015-11430"]
    assert len(ids) > 6000 and all(i.startswith("BOE-A-") for i in ids)
    assert all(d.get("title") for d in manifest["documents"])
    assert manifest["crawl_delay_seconds"] == 1  # no Crawl-delay or published rate: the project minimum


# --------------------------------------------------------------------------- PostgreSQL


class _Fixtures:
    def __init__(self, ids: list[str]):
        self.ids = ids

    def fetch(self) -> Iterator[RawDocument]:
        now = datetime(2026, 10, 14, tzinfo=UTC)
        for boe_id in self.ids:
            url = f"https://www.boe.es/datosabiertos/api/legislacion-consolidada/id/{boe_id}"
            yield RawDocument(f"boe-consolidada:{boe_id}", url, "application/xml", _raw(boe_id), now, {})


@pytest.fixture(scope="module")
def es_ingested(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, ingested: IngestionReport
) -> IngestionReport:
    with session_factory() as session:
        report = Ingestor(session, catalog, store).ingest_source(
            "es-boe-consolidada", actor="pytest", connector=_Fixtures([CE, LOPDGDD])
        )
        session.commit()
    return report


def _search(session: Session, catalog: Catalog, q: str, as_of: date) -> dict[str, Any]:
    return search(
        session,
        catalog,
        SearchRequest(q=q, jurisdiction="ES", as_of=as_of, query_language="es"),
        today=date(2026, 10, 14),
    )


@pytest.mark.db
def test_search_cites_the_constitution_with_limitations(
    session: Session, catalog: Catalog, es_ingested: IngestionReport
) -> None:
    assert es_ingested.errors == [] and es_ingested.conflicts == []
    assert es_ingested.instrument_versions_created == len(_docs(CE)) + len(_docs(LOPDGDD))
    res = _search(session, catalog, "tutela efectiva de los jueces y tribunales", date(2026, 7, 1))
    hit = res["results"][0]
    assert hit["citation"] == "art. 24.1 CE"
    assert hit["text"].startswith("1. Todas las personas tienen derecho a obtener la tutela efectiva")
    assert hit["version"]["language"] == "es"
    codes = {lim["code"] for lim in hit["limitations"]}
    assert {"REVISED_TEXT", "SOURCE_NOT_AUTHENTIC", "PROFILE_UNVERIFIED", "CURRENCY", "AS_OF_AFTER_CURRENCY"} <= codes
    assert "NOT_AUTHENTIC_TEXT" not in codes and "HISTORY_INCOMPLETE" not in {lim["code"] for lim in res["limitations"]}
    assert hit["source"]["attribution"].startswith("Basado en datos de la Agencia Estatal Boletín Oficial del Estado")
    assert "meramente informativo" in hit["source"]["attribution"]
    # Spanish stemming: "consentimientos" finds "consentimiento".
    res = _search(session, catalog, "consentimientos del afectado pluralidad de finalidades", date(2026, 7, 1))
    assert res["results"][0]["citation"] == "art. 6.2 LOPDGDD"


@pytest.mark.db
def test_point_in_time_follows_the_source_versions(
    session: Session, catalog: Catalog, es_ingested: IngestionReport
) -> None:
    uri = "/es/c/BOE-A-1978-31229/~art-13-2"
    before = provision_at(session, catalog, uri, date(1990, 1, 1))
    on_day = provision_at(session, catalog, uri, date(1992, 8, 28))
    assert before["status_on_date"] == on_day["status_on_date"] == "in_force"
    assert "sufragio activo en las elecciones" in before["version"]["text"]
    assert (before["version"]["valid_to"], before["version"]["valid_to_basis"]) == ("1992-08-28", "derived")
    assert "sufragio activo y pasivo" in on_day["version"]["text"]
    assert before["provision"]["citation"] == "art. 13.2 CE"
    assert "LATER_VERSION_EXISTS" in {lim["code"] for lim in before["limitations"]}
    # 13.1 kept its words: the same version answers in 1990 and in 2000.
    one = "/es/c/BOE-A-1978-31229/~art-13-1"
    a, b = provision_at(session, catalog, one, date(1990, 1, 1)), provision_at(session, catalog, one, date(2000, 1, 1))
    assert a["version"]["id"] == b["version"]["id"]
    # The indexed history starts with the original publication: an apartado added in 2021 was
    # "not yet in force" in 2020, not a gap in the history.
    added = provision_at(session, catalog, "/es/lo/BOE-A-2018-16673/~art-2-5", date(2020, 1, 1))
    assert added["status_on_date"] == "not_yet_in_force"
    assert provision_at(session, catalog, "/es/lo/BOE-A-2018-16673/~art-2-5", date(2021, 6, 16))["status_on_date"] == (
        "in_force"
    )
    # Art. 24 LOPDGDD was rewritten in 2023 without its numbered apartados: they end explicitly.
    gone = provision_at(session, catalog, "/es/lo/BOE-A-2018-16673/~art-24-1", date(2024, 1, 1))
    assert gone["status_on_date"] == "no_version"
    assert provision_at(session, catalog, "/es/lo/BOE-A-2018-16673/~art-24", date(2024, 1, 1))["status_on_date"] == (
        "in_force"
    )


@pytest.mark.db
def test_stored_texts_verify_against_the_raw_documents(
    session: Session, catalog: Catalog, store: LocalObjectStore, es_ingested: IngestionReport
) -> None:
    ids = [f"boe-consolidada:{CE}", f"boe-consolidada:{LOPDGDD}"]
    docs = session.scalars(select(m.SourceDocument).where(m.SourceDocument.external_id.in_(ids))).all()
    assert len(docs) == 2
    for doc in docs:
        result = verify_source_document(session, catalog, store, doc.id, checker="pytest")
        assert result["result"] == "pass" and result["checked_units"] > 600, result["problems"][:3]


@pytest.mark.db
def test_penal_code_history_and_source_discrepancies(
    session: Session, catalog: Catalog, store: LocalObjectStore, ingested: IngestionReport
) -> None:
    # Ingested in the test's own session, which is rolled back afterwards, so the conflicts recorded
    # here do not leak into other tests.
    report = Ingestor(session, catalog, store).ingest_source(
        "es-boe-consolidada", actor="pytest", connector=_Fixtures([CP])
    )
    assert report.errors == []
    # BOE lists two different texts for the same version of arts. 274, 515, 611 and 631: the first
    # listing is recorded and the second is reported as a conflict for review.
    assert sorted(c.split(" ")[0].rsplit("~", 1)[1] for c in report.conflicts) == [
        "art-274",
        "art-515",
        "art-611",
        "art-631",
    ]
    repealed_by_range = "/es/lo/BOE-A-1995-25444/~art-617-1"
    before = provision_at(session, catalog, repealed_by_range, date(2015, 6, 30))
    after = provision_at(session, catalog, repealed_by_range, date(2015, 7, 1))
    assert before["status_on_date"] == "in_force" and before["provision"]["citation"] == "art. 617.1 CP"
    assert after["status_on_date"] == "repealed"
    assert "REPEALED" in {lim["code"] for lim in after["limitations"]}
    expired = "/es/lo/BOE-A-1995-25444/~art-183ter"
    assert provision_at(session, catalog, expired, date(2022, 9, 6))["status_on_date"] == "in_force"
    assert provision_at(session, catalog, expired, date(2023, 1, 1))["status_on_date"] == "no_version"
    hit = search(
        session,
        catalog,
        SearchRequest(q="matare a otro reo de homicidio", jurisdiction="ES", as_of=date(2010, 1, 1)),
        today=date(2026, 10, 14),
    )["results"][0]
    assert hit["citation"] == "art. 138 CP" and hit["version"]["valid_to"] == "2015-07-01"
    source = session.scalar(select(m.SourceDocument).where(m.SourceDocument.external_id == f"boe-consolidada:{CP}"))
    assert source is not None
    assert verify_source_document(session, catalog, store, source.id, checker="pytest")["result"] == "pass"


@pytest.mark.db
def test_repealed_and_ministerial_norms_search_and_resolve_by_date(
    session: Session, catalog: Catalog, store: LocalObjectStore, ingested: IngestionReport
) -> None:
    report = Ingestor(session, catalog, store).ingest_source(
        "es-boe-consolidada", actor="pytest", connector=_Fixtures([RD_REPEALED, ORDEN, RESOLUCION, ACUERDO])
    )
    assert report.errors == [] and report.conflicts == []
    # An Orden is found and cited with its own designation.
    hit = _search(
        session, catalog, "Comisión Española sobre la digitalización y la accesibilidad en línea", date(2026, 10, 14)
    )["results"][0]
    assert hit["citation"].startswith("art. 1") and hit["citation"].endswith(" de la Orden CUL/1014/2007")
    # A Real Decreto repealed in full on 2017-04-01 is found for a date before, not for today.
    query = "procedimiento de examen previo aplicable artículos del Real Decreto 812/2000 alimentación"
    before = _search(session, catalog, query, date(2010, 1, 1))["results"]
    assert before and before[0]["citation"] == "art. 2 del Real Decreto 996/2001"
    assert before[0]["version"]["valid_to"] == "2017-04-01"
    assert not [
        r for r in _search(session, catalog, query, date(2026, 10, 14))["results"] if "996/2001" in r["citation"]
    ]
    uri = "/es/rd/BOE-A-2001-17164/~rd-art-2"
    assert provision_at(session, catalog, uri, date(2017, 3, 31))["status_on_date"] == "in_force"
    after = provision_at(session, catalog, uri, date(2017, 4, 1))
    assert after["status_on_date"] == "no_version"
    events = session.scalars(
        select(m.LifecycleEvent)
        .join(m.Instrument, m.LifecycleEvent.instrument_id == m.Instrument.id)
        .where(m.Instrument.uri == "/es/rd/BOE-A-2001-17164")
    ).all()
    assert ("repeal", date(2017, 4, 1)) in {(e.event_type, e.event_date) for e in events}
    # Stored texts of the new ranks re-derive from their raw documents.
    for boe_id in (RD_REPEALED, ORDEN, RESOLUCION, ACUERDO):
        doc = session.scalar(
            select(m.SourceDocument).where(m.SourceDocument.external_id == f"boe-consolidada:{boe_id}")
        )
        assert doc is not None
        assert verify_source_document(session, catalog, store, doc.id, checker="pytest")["result"] == "pass"

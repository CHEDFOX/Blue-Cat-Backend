"""InfoLEG (Argentina): parser, connector, citations and search behaviour (excerpts of the real pages)."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError
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
from ulis.sources.connectors.infoleg_ar import InfolegArConnector, robots_crawl_delay
from ulis.sources.connectors.likumi_lv import robots_disallowed
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.polite_http import FetchError
from ulis.sources.normalized import ParsedDocument, ParsedProvision, ParseSkipped
from ulis.sources.parsers.infoleg_ar import (
    BUNDLE_FORMAT,
    BUNDLE_FORMAT_V2,
    InfolegParseError,
    parse_infoleg_ar,
    resolve_uri,
)
from ulis.sources.registry import LicenceNotApproved, licence_gate
from ulis.storage.object_store import LocalObjectStore
from ulis.verification.integrity import verify_source_document

FIX = REPO_ROOT / "tests" / "fixtures" / "infoleg_ar"
META = json.loads((FIX / "meta.json").read_text(encoding="utf-8"))
SOURCE = "ar-infoleg"
NAMES = ("constitucion", "cccn", "codigo-penal", "ley-25326", "lct", "ley-19549")
TODAY = date(2026, 10, 14)


def _bundle(name: str, **changes: Any) -> bytes:
    """The raw document the connector yields for a fixture (summary and text page, exactly as stored)."""
    info = META[name]
    pages = {}
    for key, file in (("summary", f"{name}.summary.html"), ("text", f"{name}.text.html")):
        pages[key] = {
            "url": info[key]["final_url"],
            "retrieved_at": info[key]["retrieved_at"],
            "content_type": info[key]["content_type"],
            "html": (FIX / file).read_text(encoding="utf-8"),
        }
    bundle: dict[str, Any] = {
        "format": BUNDLE_FORMAT,
        "infoleg_id": info["infoleg_id"],
        "text_kind": info["text_kind"],
        "pages": pages,
        **changes,
    }
    return json.dumps(bundle, ensure_ascii=False, sort_keys=True).encode("utf-8")


def _doc(name: str) -> ParsedDocument:
    docs = parse_infoleg_ar(_bundle(name))
    assert len(docs) == 1
    return docs[0]


def _prov(doc: ParsedDocument, eid: str) -> ParsedProvision:
    return next(p for p in doc.provisions if p.eid == eid)


def _eids(doc: ParsedDocument) -> list[str]:
    return [p.eid for p in doc.provisions]


def _citation(doc: ParsedDocument, eid: str) -> str:
    p = _prov(doc, eid)
    return get_module("ar").provision_citation(
        ProvisionCitationInput(
            instrument_title=doc.title,
            instrument_type=doc.instrument_type,
            jurisdiction_id="AR",
            top_kind=p.top_kind,
            num_path=p.num_path,
            language="es",
            ext=doc.ext,
            eid=eid,
        )
    )


# --------------------------------------------------------------------------- parser


def test_constitution_is_the_official_text_from_preamble_to_the_last_transitory_clause() -> None:
    doc = _doc("constitucion")
    assert doc.work_uri == "/ar/constitucion/nacional" and doc.instrument_type == "constitution"
    assert doc.language == "es" and doc.jurisdiction_id == "AR" and doc.work_date is None
    assert doc.authoritative is False and doc.text_origin == "official_conversion"
    assert doc.consolidation_kind == "as_made" and doc.validity_basis == "attested_at_edition"
    assert doc.version_date == date(2026, 10, 14) and doc.currency_date == doc.version_date
    assert doc.ext["infoleg_id"] == 804 and doc.ext["numero"] == "24430" and doc.ext["texto"] == "original"
    assert [(e.event_type, e.date.isoformat()) for e in doc.events] == [
        ("enactment", "1994-12-14"),
        ("publication", "1995-01-10"),
        ("version_change", "2026-10-14"),
    ]
    eids = _eids(doc)
    assert eids[0] == "preamble" and eids[1] == "art-1" and eids[-1] == "dt-17" and len(eids) == 148
    # articles 1 to 129 plus 14 bis, the preamble and seventeen transitory clauses; nothing of Ley 24.430 around it
    assert len([e for e in eids if e.startswith("art-")]) == 130 and len(set(eids)) == len(eids)
    assert not any("Ordénase la publicación" in p.text for p in doc.provisions)
    assert not any("Publíquese en el Boletín Oficial" in p.text for p in doc.provisions)
    assert _prov(doc, "preamble").text.startswith("Nos los representantes del pueblo de la Nación Argentina")
    bis = _prov(doc, "art-14-bis")
    assert bis.num_path == ["14 bis"] and bis.text.count("\n") == 2  # three paragraphs
    assert bis.text.startswith("El trabajo en sus diversas formas gozará de la protección de las leyes")
    assert "el derecho de huelga" in bis.text
    assert bis.context_heading == "PRIMERA PARTE – Capítulo Primero: Declaraciones, derechos y garantías"
    art75 = _prov(doc, "art-75")
    assert art75.text.startswith("Corresponde al Congreso:\n1. Legislar en materia aduanera.")
    assert art75.context_heading and art75.context_heading.endswith("CAPITULO CUARTO: Atribuciones del Congreso")
    clause = _prov(doc, "dt-2")
    assert clause.kind == "transitional_provision" and clause.num_path == ["segunda"]
    assert clause.text.startswith("Las acciones positivas a que alude el Artículo 37")
    assert clause.note == "(Corresponde al Artículo 37)" and "Corresponde" not in clause.text


def test_civil_and_commercial_code_is_annex_i_only() -> None:
    doc = _doc("cccn")
    assert doc.work_uri == "/ar/ley/26994" and doc.instrument_type == "law" and doc.number == "26994"
    assert doc.text_origin == "official_revised" and doc.consolidation_kind == "current_consolidation"
    assert doc.work_date == date(2014, 10, 1) and doc.authoritative is False
    eids = _eids(doc)
    assert eids[0] == "art-1" and len(set(eids)) == len(eids)
    assert "art-762" in eids and "art-1204-bis" in eids  # "ARTICULO 762,-" is printed with a comma
    assert not any("Apruébase el Código Civil y Comercial" in p.text for p in doc.provisions)  # Ley 26.994 art. 1
    assert not any("MODIFICACIONES A LA LEY" in p.text for p in doc.provisions)  # Anexo II
    assert eids[-1] == "art-1205"
    one = _prov(doc, "art-1")
    # the rubric is printed inside the first sentence and stays in the text
    assert one.text.startswith("Fuentes y aplicación. Los casos que este Código rige deben ser resueltos")
    assert one.heading is None and one.context_heading == "TITULO PRELIMINAR – CAPITULO 1: Derecho"
    assert _prov(doc, "art-7").text.count("\n") == 2
    list_article = _prov(doc, "art-14").text.split("\n")
    assert list_article[1:3] == ["a) derechos individuales;", "b) derechos de incidencia colectiva."]
    assert list_article[3].startswith("La ley no ampara el ejercicio abusivo")
    assert _prov(doc, "art-762").text.startswith("Individualización. La obligación de dar es de género")
    ctx = _prov(doc, "art-201").context_heading or ""
    assert ctx.endswith(" – Gobierno y administración")  # the unnumbered <seccion> sub-heading before art. 201
    assert (
        _prov(doc, "art-1109").context_heading
        == "TITULO III: Contratos de consumo – CAPITULO 3: Modalidades especiales"
    )
    # "(Artículo sustituido ...) ARTICULO 1199.- ..." on one line: the note closes art. 1198, art. 1199 starts
    assert _prov(doc, "art-1198").note == "(Artículo sustituido por art. 256 del Decreto N° 70/2023 B.O. 21/12/2023)"
    assert _prov(doc, "art-1199").text.startswith("Moneda de pago y actualización.")
    assert "B.O." not in "".join(p.text for p in doc.provisions)
    for eid in ("art-1202", "art-1204", "art-1204-bis"):
        p = _prov(doc, eid)
        assert p.status == "repealed" and p.text == "" and "Artículo derogado por" in (p.note or "")


def test_penal_code_skips_the_index_and_keeps_editorial_notes_out_of_the_text() -> None:
    doc = _doc("codigo-penal")
    assert doc.work_uri == "/ar/ley/11179" and doc.short_title == "Código Penal"
    eids = _eids(doc)
    assert eids[:4] == ["art-1", "art-2", "art-3", "art-4"] and eids[-1] == "art-316"
    assert not any("Arts." in p.text for p in doc.provisions)  # the "Indice Temático" is not text
    one = _prov(doc, "art-1")
    assert one.text.startswith("Este Código se aplicará:\n1) Por delitos cometidos o cuyos efectos")
    assert one.note and one.note.startswith("(Artículo sustituido por art. 29 de la Ley N° 27.401 B.O. 1/12/2017")
    assert one.context_heading == "LIBRO PRIMERO: DISPOSICIONES GENERALES – TITULO I: APLICACION DE LA LEY PENAL"
    art79 = _prov(doc, "art-79")
    assert art79.text == (
        "Se aplicará reclusión o prisión de ocho a veinticinco años, al que matare a otro siempre que en este "
        "código no se estableciere otra pena."
    )
    assert art79.context_heading and art79.context_heading.endswith("Capítulo I: Delitos contra la vida")
    art80 = _prov(doc, "art-80")
    assert "B.O." not in art80.text and art80.text.startswith("Se impondrá reclusión perpetua o prisión perpetua")
    # trailing "(inciso sustituido por ...)" belongs to one item: its marker stays with the note
    assert (art80.note or "").startswith("1º (inciso sustituido por art. 1° de la Ley N° 26.791 B.O. 14/12/2012)")
    assert "8° (Inciso incorporado por art. 1° de la Ley N° 25.601" in (art80.note or "")
    for eid, note in (("art-36", "Derogado."), ("art-78-bis", "(Artículo derogado por art. 14 de la Ley N° 26.388")):
        p = _prov(doc, eid)
        assert p.status == "repealed" and p.text == "" and (p.note or "").startswith(note)
    # a note under a heading goes with the next article, naming the heading
    assert (_prov(doc, "art-77").note or "").startswith("[TITULO XIII] (Numeración del capítulo sustituida por art. 2°")
    for eid in ("art-144-quinto", "art-167-quinque"):  # suffixes as printed: "144 quinto", "167 quinque"
        assert eid in eids
    assert _prov(doc, "art-144-quinto").num_path == ["144 quinto"]
    assert (_prov(doc, "art-167-ter").context_heading or "").endswith("Capítulo 2 bis: Abigeato")
    assert (_prov(doc, "art-213-quater").context_heading or "").endswith(
        "Capítulo VI: Asociaciones ilícitas terroristas y financiación del terrorismo"
    )
    assert "Antecedentes normativos" not in "".join(p.text for p in doc.provisions)


def test_data_protection_law_headings_vetoes_and_end_of_text() -> None:
    doc = _doc("ley-25326")
    assert doc.work_uri == "/ar/ley/25326" and doc.title == "Ley de Protección de los Datos Personales"
    assert len(doc.provisions) == 48 and _eids(doc)[-1] == "art-48"
    assert (_prov(doc, "art-1").heading, _prov(doc, "art-5").heading) == ("Objeto", "Consentimiento")
    assert _prov(doc, "art-47").heading is None
    five = _prov(doc, "art-5").text
    assert five.startswith("1. El tratamiento de datos personales es ilícito") and "\na) Los datos se obtengan" in five
    assert _prov(doc, "art-5").context_heading == "Capítulo II: Principios generales relativos a la protección de datos"
    # a vetoed point is printed by InfoLEG as "2. (Punto vetado ...)": nothing but a number remains of it
    assert "2. (Punto vetado por art. 1° del Decreto N° 995/2000 B.O. 2/11/2000)" in _prov(doc, "art-29").text
    assert (_prov(doc, "art-46").note or "").startswith("(Nota Infoleg: por art. 2° del Decreto N° 1558/2001")
    assert _prov(doc, "art-47").note == "(Artículo incorporado por art. 1° de la Ley N° 26.343 B.O. 9/1/2008)"
    last = _prov(doc, "art-48")
    assert last.text == "Comuníquese al Poder Ejecutivo." and last.heading is None
    assert not any("REGISTRADO" in p.text or "DADA EN LA SALA" in p.text for p in doc.provisions)
    assert doc.ext["boletin_numero"] == "29517" and doc.ext["fecha_boletin"] == "2000-11-02"


def test_labour_contract_law_headings_bis_suffixes_and_repeals() -> None:
    doc = _doc("lct")
    assert doc.work_uri == "/ar/ley/20744" and doc.short_title is None
    assert _prov(doc, "art-2").heading == "Ambito de aplicación"
    assert _prov(doc, "art-2").text.startswith("La vigencia de esta ley quedará condicionada")
    # a long article line is the text itself, not a heading
    seventeen = _prov(doc, "art-17-bis")
    assert seventeen.heading is None and seventeen.text.startswith("Las desigualdades que creara esta ley")
    ninety_four = _prov(doc, "art-94")
    assert ninety_four.heading is None and ninety_four.text.startswith(
        "Deber de preavisar - Conversión del contrato. —"
    )
    bis = _prov(doc, "art-105-bis")  # printed "Art. 105 BIS."
    assert bis.num_path == ["105 bis"] and bis.status == "repealed" and bis.heading.startswith("Cajas de Asistencia")
    assert _prov(doc, "art-132-bis").heading is None and _prov(doc, "art-132-bis").text.startswith("Si el empleador")
    for eid in ("art-28", "art-173", "art-174", "art-175"):
        p = _prov(doc, eid)
        assert p.status == "repealed" and p.text == "" and "Artículo derogado por" in (p.note or "")
    assert _prov(doc, "art-173").heading == "Trabajo nocturno. Espectáculos públicos"
    assert (_prov(doc, "art-89").note or "").startswith("(Capítulo VIII derogado por art. 26 de la Ley Nº 27.802")
    assert _prov(doc, "art-278").text.startswith("Remisión de antecedentes judiciales.")


def test_administrative_procedure_law_unnumbered_section_titles_are_not_text() -> None:
    doc = _doc("ley-19549")
    assert doc.work_uri == "/ar/ley/19549" and doc.ext["organismo"] == "PODER EJECUTIVO NACIONAL (P.E.N.)"
    eids = _eids(doc)
    assert eids[:3] == ["art-1", "art-1-bis", "art-2"] and eids[-1] == "art-34" and len(eids) == 37
    assert _prov(doc, "art-1").text.startswith("Ámbito de aplicación.\na) Las disposiciones de esta ley")
    fourteen = _prov(doc, "art-14")
    assert fourteen.text.endswith("siempre que el interesado a quien el acto beneficiaba no hubiere incurrido en dolo.")
    assert "Anulabilidad" not in fourteen.text  # a centred section title between two articles
    assert fourteen.context_heading == "Título III: Requisitos esenciales del acto administrativo – Nulidad"
    assert (_prov(doc, "art-15").context_heading or "").endswith("Anulabilidad")
    assert (_prov(doc, "art-22").note or "").startswith("[Prescripción] (Nombre de la sección sustituido")
    assert "Antecedentes normativos" not in "".join(p.text for p in doc.provisions)
    assert not any("LANUSSE" in p.text and p.eid != "art-34" for p in doc.provisions)


def test_parser_rejects_unexpected_input() -> None:
    good = json.loads(_bundle("ley-25326"))
    with pytest.raises(InfolegParseError, match="not an InfoLEG bundle"):
        parse_infoleg_ar(b"<html>captcha</html>")
    with pytest.raises(InfolegParseError, match="unsupported bundle"):
        parse_infoleg_ar(json.dumps({**good, "format": "other/1"}).encode())
    with pytest.raises(InfolegParseError, match="not one of the instruments"):
        parse_infoleg_ar(json.dumps({**good, "infoleg_id": 1}).encode())
    with pytest.raises(InfolegParseError, match="unknown text kind"):
        parse_infoleg_ar(json.dumps({**good, "text_kind": "vigente"}).encode())
    with pytest.raises(InfolegParseError, match="mode 'original'"):  # an updated text delivered as the original
        parse_infoleg_ar(json.dumps({**good, "text_kind": "original"}).encode())
    with pytest.raises(InfolegParseError, match="incomplete bundle"):
        parse_infoleg_ar(json.dumps({**good, "pages": {"summary": good["pages"]["summary"]}}).encode())
    wrong_summary = {
        **good,
        "pages": {**good["pages"], "summary": good["pages"]["summary"] | {"html": "<html></html>"}},
    }
    with pytest.raises(InfolegParseError, match="not that of InfoLEG norm 64790"):
        parse_infoleg_ar(json.dumps(wrong_summary).encode())
    text = good["pages"]["text"]["html"]
    sup = {
        **good,
        "pages": {**good["pages"], "text": good["pages"]["text"] | {"html": text.replace("Objeto", "O<sup>1</sup>")}},
    }
    with pytest.raises(InfolegParseError, match="unsupported markup"):
        parse_infoleg_ar(json.dumps(sup).encode())
    image = text.replace(
        "Comuníquese al Poder Ejecutivo.", 'Comuníquese <img src="x.png" alt="firma"> al Poder Ejecutivo.'
    )
    with_image = {**good, "pages": {**good["pages"], "text": good["pages"]["text"] | {"html": image}}}
    with pytest.raises(InfolegParseError, match="an image inside the text of art-48"):
        parse_infoleg_ar(json.dumps(with_image).encode())
    cccn = json.loads(_bundle("cccn"))
    no_annex = cccn["pages"]["text"]["html"].replace("ANEXO I<br>", "ANEXO UNO<br>")
    broken = {**cccn, "pages": {**cccn["pages"], "text": cccn["pages"]["text"] | {"html": no_annex}}}
    with pytest.raises(InfolegParseError, match="start marker"):
        parse_infoleg_ar(json.dumps(broken).encode())


def test_resolve_uri() -> None:
    cases = {
        "https://www.argentina.gob.ar/normativa/nacional/norma-804": "/ar/constitucion/nacional",
        "https://www.argentina.gob.ar/normativa/nacional/ley-25326-64790": "/ar/ley/25326",
        "https://www.argentina.gob.ar/normativa/nacional/ley-26994-235975/actualizacion": "/ar/ley/26994",
        "https://www.argentina.gob.ar/normativa/nacional/norma-16546/texto": "/ar/ley/11179",
        "https://servicios.infoleg.gob.ar/infolegInternet/verNorma.do?id=25552": "/ar/ley/20744",
    }
    for uri, work in cases.items():
        assert resolve_uri(uri) == (work, None)
        assert pipeline_resolve_uri(uri) == (work, None)
    assert resolve_uri("https://www.argentina.gob.ar/normativa/nacional/norma-999999") is None
    assert resolve_uri("https://example.org/normativa/nacional/norma-804") is None


# --------------------------------------------------------------------------- citations


def test_citation_styles() -> None:
    assert _citation(_doc("cccn"), "art-1109") == "art. 1109 CCCN"
    assert _citation(_doc("codigo-penal"), "art-79") == "art. 79 CP"
    assert _citation(_doc("codigo-penal"), "art-144-quinto") == "art. 144 quinto CP"
    assert _citation(_doc("constitucion"), "art-14-bis") == "art. 14 bis CN"
    assert _citation(_doc("constitucion"), "dt-1") == "disp. trans. primera CN"
    assert _citation(_doc("constitucion"), "preamble") == "CN, preámbulo"
    assert _citation(_doc("lct"), "art-105-bis") == "art. 105 bis LCT"
    assert _citation(_doc("ley-25326"), "art-5") == "art. 5, Ley 25.326"
    assert _citation(_doc("ley-19549"), "art-14") == "art. 14, Ley 19.549"
    assert _citation(_doc("ley-19549"), "art-1-bis") == "art. 1 bis, Ley 19.549"


def test_citation_of_items_and_numbers() -> None:
    module = get_module("ar")
    base = {"instrument_title": "Ley de Protección de los Datos Personales", "instrument_type": "law"}
    ext = {"infoleg_id": 64790, "tipo": "Ley", "numero": "25326", "texto": "actualizado"}

    def cite(*num_path: str, ext_: dict[str, Any] = ext, top_kind: str = "article") -> str:
        c = ProvisionCitationInput(
            jurisdiction_id="AR", top_kind=top_kind, num_path=list(num_path), language="es", ext=ext_, **base
        )
        return module.provision_citation(c)

    assert cite("4", "a") == "art. 4 inc. a), Ley 25.326"
    assert cite("5", "2", "e") == "art. 5 inc. 2 ap. e), Ley 25.326"
    assert cite("80", "1", ext_={**ext, "infoleg_id": 16546, "numero": "11179"}) == "art. 80 inc. 1 CP"
    assert cite("1", ext_={**ext, "infoleg_id": 7, "numero": "7"}) == "art. 1, Ley 7"
    assert cite("1", ext_={}) == "art. 1, Ley de Protección de los Datos Personales"
    assert module.id == "ar" and module.version


def test_ext_is_validated() -> None:
    module = get_module("ar")
    ok = {"infoleg_id": 64790, "tipo": "Ley", "numero": "25326", "texto": "actualizado", "fecha_sancion": "2000-10-04"}
    assert module.validate_ext(ok) == ok
    for bad in (
        {**ok, "numero": "25.326"},
        {**ok, "texto": "vigente"},
        {**ok, "other": 1},
        {**ok, "fecha_sancion": "4/10/2000"},
    ):
        with pytest.raises(ValidationError):
            module.validate_ext(bad)


# --------------------------------------------------------------------------- connector


def _cache(tmp_path: Path, names: tuple[str, ...] = NAMES) -> Path:
    """A PoliteHttp cache holding the fixtures where the connector looks for them."""
    host = tmp_path / "www.argentina.gob.ar"
    host.mkdir(parents=True)
    (host / "robots.txt").write_bytes((FIX / "robots.txt").read_bytes())
    (host / "robots.txt.meta.json").write_text(
        json.dumps({"url": "https://www.argentina.gob.ar/robots.txt", "status": 200})
    )
    for name in names:
        info = META[name]
        folder = host / "normativa" / "nacional" / f"norma-{info['infoleg_id']}"
        folder.mkdir(parents=True)
        text_file = "actualizacion.html" if info["text_kind"] == "actualizado" else "texto.html"
        for file, key, source in (
            ("resumen.html", "summary", f"{name}.summary.html"),
            (text_file, "text", f"{name}.text.html"),
        ):
            (folder / file).write_bytes((FIX / source).read_bytes())
            (folder / f"{file}.meta.json").write_text(json.dumps(info[key]))
    return tmp_path


def _manifest(tmp_path: Path, laws: list[dict[str, Any]], **extra: Any) -> Path:
    path = tmp_path / "manifest.yaml"
    data = {
        "schema_version": 1,
        "source_id": SOURCE,
        "base_url": "https://www.argentina.gob.ar",
        "user_agent": "ULIS-test (+https://example.org)",
        "crawl_delay_seconds": 10,
        "laws": laws,
        **extra,
    }
    path.write_text(yaml.safe_dump(data, allow_unicode=True))
    return path


def test_robots_rules_of_the_site() -> None:
    robots = (FIX / "robots.txt").read_text(encoding="utf-8")
    assert robots_crawl_delay(robots) == 10.0
    rules = robots_disallowed(robots)

    def blocked(path: str) -> bool:
        return any(rule.match(path) for rule in rules)

    assert blocked("/sites/default/files/infoleg/anexos/1/norma.pdf") and blocked("/normativa/x/ley.pdf")
    assert blocked("/normativa/nacional/norma-804?tca=1") and blocked("/buscar") and blocked("/search/x")
    for path in (
        "/normativa/nacional/norma-804",
        "/normativa/nacional/norma-804/texto",
        "/normativa/nacional/norma-235975/actualizacion",
    ):
        assert not blocked(path)
    assert robots_crawl_delay("User-agent: bot\nCrawl-delay: 1\n") is None
    assert robots_crawl_delay("User-agent: *\nCrawl-delay: x\n") is None


def test_connector_offline_from_cache(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    laws = [
        {"id": META[n]["infoleg_id"], "text": META[n]["text_kind"], "title": n} for n in ("constitucion", "ley-25326")
    ]
    connector = InfolegArConnector(_manifest(tmp_path, laws), SOURCE, cache, offline=True)
    docs = list(connector.fetch())
    assert [d.external_id for d in docs] == ["infoleg-ar:804:original", "infoleg-ar:64790:actualizado"]
    assert docs[0].content == _bundle("constitucion") and docs[1].content == _bundle("ley-25326")  # exact bytes stored
    assert docs[0].uri == "https://www.argentina.gob.ar/normativa/nacional/norma-804/texto"
    assert docs[1].media_type == "application/json" and docs[1].retrieved_at.year == 2026
    assert docs[1].metadata == {"infoleg_id": 64790, "text_kind": "actualizado", "title": "ley-25326"}
    assert connector.http is not None and connector.http.requests_made == 0
    assert connector.http.crawl_delay == 10.0  # the larger of the manifest's and robots.txt's Crawl-delay
    slow = _manifest(tmp_path, laws[:1], crawl_delay_seconds=30)
    again = InfolegArConnector(slow, SOURCE, cache, offline=True)
    list(again.fetch())
    assert again.http is not None and again.http.crawl_delay == 30.0
    fresh = tmp_path / "empty"
    with pytest.raises(FetchError, match="offline and not cached"):
        list(InfolegArConnector(_manifest(tmp_path, laws), SOURCE, fresh, offline=True).fetch())


def test_connector_refuses_what_robots_or_the_pages_do_not_allow(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    laws = [{"id": 804, "text": "original"}]
    robots_file = cache / "www.argentina.gob.ar" / "robots.txt"
    robots_file.write_text("User-agent: *\nDisallow: /normativa/nacional/\nCrawl-delay: 10\n")
    with pytest.raises(FetchError, match="disallowed by robots.txt"):
        list(InfolegArConnector(_manifest(tmp_path, laws), SOURCE, cache, offline=True).fetch())
    robots_file.write_bytes((FIX / "robots.txt").read_bytes())
    # the site offers no updated text for the Constitution: the manifest must say original
    with pytest.raises(FetchError, match="does not link to /normativa/nacional/norma-804/actualizacion"):
        list(
            InfolegArConnector(
                _manifest(tmp_path, [{"id": 804, "text": "actualizado"}]), SOURCE, cache, offline=True
            ).fetch()
        )
    summary = cache / "www.argentina.gob.ar" / "normativa" / "nacional" / "norma-804" / "resumen.html"
    original = summary.read_bytes()
    summary.write_text("<html>captcha</html>")
    with pytest.raises(FetchError, match="not an InfoLEG summary page for norm 804"):
        list(InfolegArConnector(_manifest(tmp_path, laws), SOURCE, cache, offline=True).fetch())
    summary.write_bytes(original)
    text = summary.with_name("texto.html")
    text.write_text("<html>no text here</html>")
    with pytest.raises(FetchError, match="no original text found"):
        list(InfolegArConnector(_manifest(tmp_path, laws), SOURCE, cache, offline=True).fetch())
    text.write_bytes(b"\xff\xfe not utf-8")
    with pytest.raises(FetchError, match="not UTF-8"):
        list(InfolegArConnector(_manifest(tmp_path, laws), SOURCE, cache, offline=True).fetch())


def test_connector_refuses_bad_manifests(tmp_path: Path) -> None:
    good = [{"id": 804, "text": "original"}]
    for laws, extra, message in (
        ([{"id": "804/../x", "text": "original"}], {}, "invalid or repeated document id"),
        ([{"id": 0, "text": "original"}], {}, "invalid or repeated document id"),
        ([{"id": 804}], {}, "text must be one of"),
        ([{"id": 804, "text": "vigente"}], {}, "text must be one of"),
        (good, {"base_url": "http://www.argentina.gob.ar"}, "base_url"),
        (good, {"user_agent": "python-urllib"}, "user_agent"),
        ([], {}, "no documents"),
    ):
        with pytest.raises(ManifestError, match=message):
            list(InfolegArConnector(_manifest(tmp_path, laws, **extra), SOURCE, tmp_path).fetch())
    with pytest.raises(ManifestError, match="mismatch"):
        list(InfolegArConnector(_manifest(tmp_path, good, source_id="other"), SOURCE, tmp_path).fetch())


def test_real_manifest_registration_and_profile() -> None:
    catalog = Catalog.load(REPO_ROOT / "config")
    source = catalog.sources.get(SOURCE)
    assert isinstance(build_connector(source), InfolegArConnector)
    manifest = yaml.safe_load((REPO_ROOT / source.access.endpoint).read_text(encoding="utf-8"))
    # The six pilot instruments come first, unchanged; every national law with a text follows (open-data CSV).
    assert [(law["id"], law["text"]) for law in manifest["laws"][:6]] == [
        (804, "original"),
        (235975, "actualizado"),
        (16546, "actualizado"),
        (64790, "actualizado"),
        (25552, "actualizado"),
        (22363, "actualizado"),
    ]
    assert not any("dataset_row" in law for law in manifest["laws"][:6])
    widened = manifest["laws"][6:]
    assert len(widened) > 8000 and all(law["dataset_row"]["tipo_norma"] == "Ley" for law in widened)
    assert {law["text"] for law in widened} == {"original", "actualizado"}
    assert (
        manifest["dataset"]["record"].startswith("https://datos.gob.ar/dataset/")
        and len(manifest["dataset"]["sha256"]) == 64
    )
    InfolegArConnector(REPO_ROOT / source.access.endpoint, SOURCE, Path("/nonexistent"))._manifest()  # noqa: SLF001
    assert manifest["crawl_delay_seconds"] >= 10 and "ULIS-research-bot" in manifest["user_agent"]
    licence_gate(source)
    assert source.licence.review_status == "approved" and source.authority == "official_non_authentic"
    assert source.licence.id == "CC-BY-4.0" and source.licence.attribution_required
    assert source.licence.url == "https://creativecommons.org/licenses/by/4.0/deed.es"
    profile = catalog.jurisdictions.get("AR")
    assert profile.verification.status == "draft_unverified" and profile.official_languages == ["es"]
    assert profile.legal_system.structure == "federal" and profile.citation.module == "ar"
    assert profile.coverage.corpus_status == "partial" and profile.memberships == []
    assert [c.id for c in profile.courts][0] == "AR-CSJN"
    assert catalog.module_for("AR").id == "ar"


def test_other_sources_remain_gated() -> None:
    catalog = Catalog.load(REPO_ROOT / "config")
    with pytest.raises(LicenceNotApproved):
        licence_gate(catalog.sources.get("in-india-code"))


def test_fixtures_readme_names_the_source_and_licence() -> None:
    text = (FIX / "README.md").read_text(encoding="utf-8")
    assert "argentina.gob.ar/normativa" in text and "Creative Commons Atribución 4.0" in text and "2026-10-14" in text
    for name, info in META.items():
        assert info["summary"]["url"].startswith("https://www.argentina.gob.ar/normativa/nacional/norma-")
        assert info["text"]["retrieved_at"].startswith("2026-10-14") and name in text


# --------------------------------------------------------------------------- database


class _Fixtures:
    def fetch(self) -> Iterator[RawDocument]:
        for name in NAMES:
            info = META[name]
            yield RawDocument(
                f"infoleg-ar:fixture/{name}",
                info["text"]["final_url"],
                "application/json",
                _bundle(name),
                datetime.fromisoformat(info["text"]["retrieved_at"]).astimezone(UTC),
                {"fixture": name},
            )


@pytest.fixture(scope="module")
def ar_ingested(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, ingested: IngestionReport
) -> IngestionReport:
    with session_factory() as session:
        report = Ingestor(session, catalog, store).ingest_source(SOURCE, actor="pytest", connector=_Fixtures())
        session.commit()
    return report


def _search(session: Session, catalog: Catalog, **kw: Any) -> dict[str, Any]:
    return search(session, catalog, SearchRequest(jurisdiction="AR", **kw), today=TODAY)


@pytest.mark.db
def test_ingest_report_counts(ar_ingested: IngestionReport) -> None:
    assert ar_ingested.errors == [] and ar_ingested.conflicts == []
    assert ar_ingested.documents_new == len(NAMES) and ar_ingested.instruments_created == len(NAMES)
    assert ar_ingested.provision_versions_created == sum(len(_doc(n).provisions) for n in NAMES)
    repealed = sum(1 for n in NAMES for p in _doc(n).provisions if p.status == "repealed")
    assert repealed > 5 and ar_ingested.passages_indexed == ar_ingested.provision_versions_created - repealed


@pytest.mark.db
def test_search_finds_exact_text_with_citation_and_limitations(
    session: Session, catalog: Catalog, ar_ingested: IngestionReport
) -> None:
    res = _search(session, catalog, q="al que matare a otro")
    hit = res["results"][0]
    assert hit["citation"] == "art. 79 CP"
    assert hit["text"] == (
        "Se aplicará reclusión o prisión de ocho a veinticinco años, al que matare a otro siempre que en este "
        "código no se estableciere otra pena."
    )
    assert all(h["jurisdiction"]["id"] == "AR" for h in res["results"])
    assert hit["authority"]["category"] == "applicability_unverified"
    codes = {lim["code"] for lim in hit["limitations"]}
    assert {"ATTESTED_ONLY_AT", "REVISED_TEXT", "SOURCE_NOT_AUTHENTIC", "PROFILE_UNVERIFIED"} <= codes
    assert hit["source"]["attribution"].startswith("Fuente: InfoLEG")
    constitution = _search(session, catalog, q="forma representativa republicana federal")["results"][0]
    assert constitution["citation"] == "art. 1 CN"
    assert constitution["text"].startswith("La Nación Argentina adopta para su gobierno la forma representativa")
    work = _search(session, catalog, q="derecho de huelga gremios convenios colectivos de trabajo")["results"][0]
    assert work["citation"] == "art. 14 bis CN"
    consumer = _search(
        session, catalog, q="contratos celebrados fuera de los establecimientos comerciales lugar de cumplimiento"
    )
    assert consumer["results"][0]["citation"] == "art. 1109 CCCN"
    data = _search(session, catalog, q="consentimiento libre, expreso e informado del titular de los datos")
    assert data["results"][0]["citation"] == "art. 5, Ley 25.326"
    procedure = _search(session, catalog, q="acto administrativo nulidad absoluta e insanable error esencial")
    assert procedure["results"][0]["citation"] == "art. 14, Ley 19.549"
    labour = _search(
        session, catalog, q="vigencia de esta ley condicionada compatible naturaleza modalidades de la actividad"
    )
    assert labour["results"][0]["citation"] == "art. 2 LCT"
    assert labour["results"][0]["text"].startswith("La vigencia de esta ley quedará condicionada")


@pytest.mark.db
def test_attested_text_is_a_history_gap_and_repeals_are_reported(
    session: Session, catalog: Catalog, ar_ingested: IngestionReport
) -> None:
    uri = "/ar/ley/11179/~art-79"
    now = provision_at(session, catalog, uri, date(2026, 10, 14))
    assert now["status_on_date"] == "in_force_date_unverified" and now["version"]["currency_date"] == "2026-10-14"
    assert now["provision"]["citation"] == "art. 79 CP"
    earlier = provision_at(session, catalog, uri, date(1990, 1, 1))
    # only the current wording is published: 1990 is not "not yet in force", it is unknown
    assert earlier["status_on_date"] == "in_force_date_unverified"
    assert "ATTESTED_ONLY_AT" in {lim["code"] for lim in earlier["limitations"]}
    repealed = provision_at(session, catalog, "/ar/ley/11179/~art-78-bis", date(2026, 10, 14))
    assert [(t["status"], t["note"][:40]) for t in repealed["timeline"]] == [
        ("repealed", "(Artículo derogado por art. 14 de la Ley")
    ]
    res = _search(session, catalog, q="al que matare a otro", as_of=date(1990, 1, 1))
    assert res["results"][0]["citation"] == "art. 79 CP" and "HISTORY_INCOMPLETE" in {
        lim["code"] for lim in res["limitations"]
    }


@pytest.mark.db
def test_reingest_is_idempotent_and_sources_verify(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, ar_ingested: IngestionReport
) -> None:
    with session_factory() as session:
        again = Ingestor(session, catalog, store).ingest_source(SOURCE, actor="pytest", connector=_Fixtures())
        assert again.documents_unchanged == len(NAMES) and again.provision_versions_created == 0
        ids = session.scalars(select(m.SourceDocument.id).where(m.SourceDocument.source_id == SOURCE)).all()
        results = [verify_source_document(session, catalog, store, i, checker="pytest") for i in ids]
        assert len(results) == len(NAMES) and all(r["result"] == "pass" for r in results)
        assert sum(r["checked_units"] for r in results) == ar_ingested.provision_versions_created
        session.rollback()


def test_fixture_bundle_pattern_matches_the_connector_format() -> None:
    bundle = json.loads(_bundle("lct"))
    assert bundle["format"] == BUNDLE_FORMAT and set(bundle["pages"]) == {"summary", "text"}
    assert re.fullmatch(r"20\d\d-\d\d-\d\dT.*", bundle["pages"]["text"]["retrieved_at"])


# --------------------------------------------------------------------------- any national law (open-data CSV row)
# Fixtures in tests/fixtures/infoleg_ar/generic/: the text body of real pages of laws taken from the whole-collection
# manifest, with the CSV row the manifest carries for each (generic.json).

GENERIC = FIX / "generic"
GENERIC_META = json.loads((GENERIC / "generic.json").read_text(encoding="utf-8"))
DATASET = json.loads((GENERIC / "dataset.json").read_text(encoding="utf-8"))
LEY_25344, LEY_274, LEY_346, LEY_26453, LEY_645_IMAGE, LEY_5, LEY_2268 = (
    "65053",
    "280985",
    "48854",
    "148387",
    "281640",
    "280913",
    "283850",
)


def _generic_bundle(nid: str, html: str | None = None, **changes: Any) -> bytes:
    info = GENERIC_META[nid]
    bundle: dict[str, Any] = {
        "format": BUNDLE_FORMAT_V2,
        "infoleg_id": info["infoleg_id"],
        "text_kind": info["text_kind"],
        "number_ambiguous": info["number_ambiguous"],
        "dataset": DATASET,
        "dataset_row": info["dataset_row"],
        "pages": {
            "text": {
                "url": info["url"],
                "retrieved_at": info["retrieved_at"],
                "content_type": "text/html; charset=utf-8",
                "html": html if html is not None else (GENERIC / f"{nid}.text.html").read_text(encoding="utf-8"),
            }
        },
        **changes,
    }
    return json.dumps(bundle, ensure_ascii=False, sort_keys=True).encode("utf-8")


def _generic(nid: str) -> ParsedDocument:
    return parse_infoleg_ar(_generic_bundle(nid))[0]


def test_any_national_law_is_read_from_its_text_page_and_the_dataset_row() -> None:
    doc = _generic(LEY_25344)
    assert (doc.instrument_type, doc.work_uri, doc.number) == ("law", "/ar/ley/25344", "25344")
    assert doc.title.startswith("Ley 25.344 - ") and doc.title.split(" - ", 1)[1] == doc.ext["titulo_resumido"]
    assert doc.text_origin == "official_revised" and doc.consolidation_kind == "current_consolidation"
    assert doc.validity_basis == "attested_at_edition" and doc.authoritative is False
    assert doc.work_date == date(2000, 10, 19) and doc.ext["fecha_sancion"] == "2000-10-19"
    assert doc.ext["organismo"] == "HONORABLE CONGRESO DE LA NACION ARGENTINA"
    assert doc.ext["modificada_por"] >= 1 and doc.ext["texto"] == "actualizado"
    assert [p.eid for p in doc.provisions][:3] == ["art-1", "art-2", "art-3"]
    assert _citation(doc, "art-1") == "art. 1, Ley 25.344"
    assert get_module("ar").validate_ext(doc.ext)["numero"] == "25344"
    assert doc.outstanding_changes == []  # an updated text, and the editors say nothing about abrogation


def test_nineteenth_century_laws_with_an_ordinal_sign_and_no_stop_are_read() -> None:
    doc = _generic(LEY_274)
    assert doc.work_uri == "/ar/ley/274-1861"  # the number was issued twice: the year of sanction tells them apart
    assert _eids(doc) == ["art-1", "art-2"]
    assert _prov(doc, "art-1").text.startswith("Abrese un crédito suplementario")
    assert _citation(doc, "art-2") == "art. 2, Ley 274"
    other = _generic(LEY_5)
    assert other.work_uri == "/ar/ley/5-1854" and other.work_date == date(1854, 11, 21)


def test_what_the_editors_say_about_abrogation_and_missing_updates_is_a_warning() -> None:
    abrogated = _generic(LEY_346)
    assert "ABROGADA POR ART. 30 DE LA LEY N° 21.795" in abrogated.ext["observaciones"]
    descriptions = [c["description"] for c in abrogated.outstanding_changes]
    assert descriptions == ["InfoLEG's observation on this law reads: " + abrogated.ext["observaciones"]]
    modified = _generic(LEY_26453)
    assert modified.ext["texto"] == "original" and modified.ext["modificada_por"] >= 1
    assert len(modified.outstanding_changes) == 1
    assert modified.outstanding_changes[0]["description"].startswith("InfoLEG lists 1 norm(s) that modify this law")
    assert "publishes no updated text" in modified.outstanding_changes[0]["description"]
    both = _generic(LEY_2268)
    assert len(both.outstanding_changes) == 2 and both.text_origin == "official_conversion"


def test_pages_with_nothing_readable_are_skipped_not_errors() -> None:
    with pytest.raises(ParseSkipped, match="an image inside"):
        parse_infoleg_ar(_generic_bundle(LEY_645_IMAGE))
    body = (GENERIC / f"{LEY_25344}.text.html").read_text(encoding="utf-8")
    with pytest.raises(ParseSkipped, match="unsupported markup"):
        parse_infoleg_ar(_generic_bundle(LEY_25344, html=body.replace("Ley 25.344", "Ley 25.<sup>3</sup>44", 1)))
    with pytest.raises(ParseSkipped, match="no article to read"):
        parse_infoleg_ar(_generic_bundle(LEY_25344, html=re.sub(r"(?i)art[ií]culo|art\.", "Disposicion", body)))
    # the six pilot instruments keep their loud failures
    with pytest.raises(InfolegParseError, match="no provisions found"):
        parse_infoleg_ar(_bundle("ley-25326", pages=_bundle_pages_without_articles()))


def _bundle_pages_without_articles() -> dict[str, Any]:
    pages = json.loads(_bundle("ley-25326"))["pages"]
    pages["text"]["html"] = '<div class="infoleg-norma-body infoleg-mode-actualizado"><p>Sin articulos</p></div>'
    return pages


def test_dataset_rows_must_describe_a_numbered_law() -> None:
    row = GENERIC_META[LEY_25344]["dataset_row"]
    for bad in ({**row, "tipo_norma": "Decreto"}, {**row, "numero_norma": "S/N"}):
        raw = json.loads(_generic_bundle(LEY_25344))
        raw["dataset_row"] = bad
        with pytest.raises(InfolegParseError, match="only numbered laws"):
            parse_infoleg_ar(json.dumps(raw).encode())
    with pytest.raises(InfolegParseError, match="unsupported bundle"):
        parse_infoleg_ar(json.dumps({"format": "ulis-infoleg-bundle/3"}).encode())


def _generic_cache(tmp_path: Path, ids: tuple[str, ...]) -> Path:
    """A cache with the robots.txt and only the text pages: a dataset-row entry never asks for the summary page."""
    host = tmp_path / "www.argentina.gob.ar"
    host.mkdir(parents=True)
    (host / "robots.txt").write_bytes((FIX / "robots.txt").read_bytes())
    (host / "robots.txt.meta.json").write_text(
        json.dumps({"url": "https://www.argentina.gob.ar/robots.txt", "status": 200})
    )
    for nid in ids:
        info = GENERIC_META[nid]
        folder = host / "normativa" / "nacional" / f"norma-{nid}"
        folder.mkdir(parents=True)
        name = "actualizacion.html" if info["text_kind"] == "actualizado" else "texto.html"
        (folder / name).write_bytes((GENERIC / f"{nid}.text.html").read_bytes())
        (folder / f"{name}.meta.json").write_text(
            json.dumps(
                {
                    "url": info["url"],
                    "final_url": info["url"],
                    "status": 200,
                    "content_type": "text/html; charset=utf-8",
                    "retrieved_at": info["retrieved_at"],
                }
            )
        )
    return tmp_path


def _entry(nid: str) -> dict[str, Any]:
    info = GENERIC_META[nid]
    return {
        "id": info["infoleg_id"],
        "text": info["text_kind"],
        "title": "x",
        "dataset_row": info["dataset_row"],
        **({"number_ambiguous": True} if info["number_ambiguous"] else {}),
    }


def test_connector_makes_one_request_per_law_and_skips_what_it_cannot_read(tmp_path: Path) -> None:
    cache = _generic_cache(tmp_path, (LEY_25344, LEY_274))
    gone = cache / "www.argentina.gob.ar" / "normativa" / "nacional" / "norma-1" / "texto.html"
    gone.parent.mkdir(parents=True)
    gone.write_bytes(b"")
    gone.with_name("texto.html.meta.json").write_text('{"url": "x", "status": 404}')
    empty = cache / "www.argentina.gob.ar" / "normativa" / "nacional" / "norma-2" / "texto.html"
    empty.parent.mkdir(parents=True)
    empty.write_text("<html>no text</html>")
    empty.with_name("texto.html.meta.json").write_text(
        '{"url": "https://www.argentina.gob.ar/x", "status": 200, "retrieved_at": "2026-10-15T00:00:00+00:00"}'
    )
    row = GENERIC_META[LEY_25344]["dataset_row"]
    laws = [
        _entry(LEY_25344),
        {"id": 1, "text": "original", "dataset_row": {**row, "numero_norma": "1"}},
        {"id": 2, "text": "original", "dataset_row": {**row, "numero_norma": "2"}},
        _entry(LEY_274),
    ]
    connector = InfolegArConnector(_manifest(tmp_path, laws, dataset=DATASET), SOURCE, cache, offline=True)
    docs = list(connector.fetch())
    assert [d.external_id for d in docs] == [f"infoleg-ar:{LEY_25344}:actualizado", f"infoleg-ar:{LEY_274}:original"]
    assert docs[0].content == _generic_bundle(LEY_25344) and docs[1].content == _generic_bundle(LEY_274)
    assert connector.http is not None and connector.http.requests_made == 0
    assert len(connector.fetch_errors) == 2
    assert "norma-1/texto not found (404)" in connector.fetch_errors[0]
    assert "norma-2/texto holds no original text" in connector.fetch_errors[1]
    bundle = json.loads(docs[0].content)
    assert bundle["format"] == BUNDLE_FORMAT_V2 and "summary" not in bundle["pages"]
    assert bundle["dataset"]["sha256"] == DATASET["sha256"]


def test_connector_validates_dataset_entries(tmp_path: Path) -> None:
    good = _entry(LEY_25344)
    for laws, extra, message in (
        (
            [{**good, "dataset_row": {**good["dataset_row"], "tipo_norma": "Decreto"}}],
            {"dataset": DATASET},
            "numbered Ley",
        ),
        (
            [{**good, "dataset_row": {**good["dataset_row"], "numero_norma": "S/N"}}],
            {"dataset": DATASET},
            "numbered Ley",
        ),
        ([good], {}, "dataset block"),
        ([good, good], {"dataset": DATASET}, "invalid or repeated document id"),
        ([good], {"dataset": DATASET, "crawl_delay_seconds": 5}, "crawl_delay_seconds"),
    ):
        with pytest.raises(ManifestError, match=message):
            list(InfolegArConnector(_manifest(tmp_path, laws, **extra), SOURCE, tmp_path, offline=True).fetch())


def test_manifest_script_drops_control_characters_from_metadata() -> None:
    from scripts.build_infoleg_manifest import _clean

    assert _clean("CANCELACI\x96N DE\x80 DEUDAS \n") == "CANCELACIN DE DEUDAS"
    assert _clean("  Ley 25.344 ") == "Ley 25.344"


@pytest.mark.db
def test_generic_laws_ingest_search_and_carry_their_warnings(
    session: Session, catalog: Catalog, store: LocalObjectStore, ingested: IngestionReport
) -> None:
    class _Docs:
        def __init__(self, ids: tuple[str, ...]):
            self.ids = ids
            self.fetch_errors: list[str] = []

        def fetch(self) -> Iterator[RawDocument]:
            for nid in self.ids:
                info = GENERIC_META[nid]
                yield RawDocument(
                    f"infoleg-ar:{nid}:{info['text_kind']}",
                    info["url"],
                    "application/json",
                    _generic_bundle(nid),
                    datetime.fromisoformat(info["retrieved_at"]),
                    {},
                )

    ids = (LEY_25344, LEY_274, LEY_346, LEY_26453, LEY_5, LEY_2268)
    report = Ingestor(session, catalog, store).ingest_source("ar-infoleg", actor="pytest", connector=_Docs(ids))
    assert report.errors == [] and report.conflicts == [] and report.documents_new == 6
    uris = set(session.scalars(select(m.Instrument.uri).where(m.Instrument.jurisdiction_id == "AR")))
    assert {"/ar/ley/25344", "/ar/ley/274-1861", "/ar/ley/5-1854", "/ar/ley/346"} <= uris
    first = _generic(LEY_25344).provisions[0]
    phrase = " ".join(first.text.split()[:4])
    res = search(
        session,
        catalog,
        SearchRequest(q=phrase, jurisdiction="AR", as_of=TODAY, query_language="es"),
        today=TODAY,
    )
    assert [r["citation"] for r in res["results"]][:5] == ["art. 1, Ley 25.344"], res["results"][:1]
    hit = next(r for r in res["results"] if r["citation"] == "art. 1, Ley 25.344")
    codes = {lim["code"] for lim in hit["limitations"]}
    assert {"ATTESTED_ONLY_AT", "PROFILE_UNVERIFIED", "REVISED_TEXT"} <= codes
    assert hit["source"]["attribution"].startswith("Fuente: InfoLEG")
    old = search(
        session,
        catalog,
        SearchRequest(q="Abrese un crédito suplementario", jurisdiction="AR", as_of=TODAY, query_language="es"),
        today=TODAY,
    )
    assert old["results"][0]["citation"] == "art. 1, Ley 274"
    warned = search(
        session,
        catalog,
        SearchRequest(q=_generic(LEY_346).provisions[0].text[:80], jurisdiction="AR", as_of=TODAY, query_language="es"),
        today=TODAY,
    )["results"][0]
    assert any(
        "ABROGADA" in lim["message"] for lim in warned["limitations"] if lim["code"] == "OUTSTANDING_CHANGES_INSTRUMENT"
    )

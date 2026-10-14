"""IMPO (Uruguay): parser, connector, citations and search behaviour (excerpts of the real answers)."""

from __future__ import annotations

import json
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
from ulis.ingestion.pipeline import CONNECTORS, PARSERS, IngestionReport, Ingestor, build_connector
from ulis.ingestion.pipeline import resolve_uri as pipeline_resolve_uri
from ulis.jurisdictions.modules import ProvisionCitationInput, get_module
from ulis.retrieval.lookup import provision_at
from ulis.retrieval.search import SearchRequest, search
from ulis.settings import REPO_ROOT
from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.impo_uy import ImpoUyConnector
from ulis.sources.connectors.infoleg_ar import robots_crawl_delay
from ulis.sources.connectors.likumi_lv import robots_disallowed
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.polite_http import FetchError, PoliteHttp
from ulis.sources.normalized import ParsedDocument, ParsedProvision
from ulis.sources.parsers import impo_uy
from ulis.sources.parsers.impo_uy import BUNDLE_FORMAT, ImpoParseError, clean_article_text, parse_impo_uy, resolve_uri
from ulis.sources.registry import licence_gate
from ulis.storage.object_store import LocalObjectStore
from ulis.verification.integrity import verify_source_document

FIX = REPO_ROOT / "tests" / "fixtures" / "impo_uy"
META = json.loads((FIX / "meta.json").read_text(encoding="utf-8"))
SOURCE = "uy-impo"
NAMES = tuple(META)
TODAY = date(2026, 10, 14)


def _body(name: str) -> str:
    return (FIX / f"{name}.json").read_bytes().decode("latin-1")


def _bundle(name: str, **changes: Any) -> bytes:
    """The raw document the connector yields for a fixture (IMPO's answer as a Latin-1 string, URL, retrieval time)."""
    info = META[name]
    bundle: dict[str, Any] = {
        "format": BUNDLE_FORMAT,
        "path": info["path"],
        "url": info["url"],
        "retrieved_at": info["retrieved_at"],
        "content_type": info["content_type"],
        "body": _body(name),
        **changes,
    }
    return json.dumps(bundle, ensure_ascii=False, sort_keys=True).encode("utf-8")


def _doc(name: str) -> ParsedDocument:
    docs = parse_impo_uy(_bundle(name))
    assert len(docs) == 1
    return docs[0]


def _prov(doc: ParsedDocument, eid: str) -> ParsedProvision:
    return next(p for p in doc.provisions if p.eid == eid)


def _citation(doc: ParsedDocument, eid: str) -> str:
    p = _prov(doc, eid)
    return get_module("uy").provision_citation(
        ProvisionCitationInput(
            instrument_title=doc.title,
            instrument_type=doc.instrument_type,
            jurisdiction_id="UY",
            top_kind=p.top_kind,
            num_path=p.num_path,
            language="es",
            ext=doc.ext,
            eid=eid,
        )
    )


# --------------------------------------------------------------------------- parser


def test_constitution_articles_headings_and_dates() -> None:
    doc = _doc("constitucion")
    assert doc.work_uri == "/uy/constitucion/1967-1967" and doc.instrument_type == "constitution"
    assert doc.expression_uri == "/uy/constitucion/1967-1967/spa@2026-10-14" and doc.language == "es"
    assert doc.title == "CONSTITUCION DE LA REPUBLICA" and doc.number is None and doc.work_date is None
    assert doc.authoritative is False and doc.text_origin == "official_consolidation"
    assert doc.consolidation_kind == "current_consolidation" and doc.validity_basis == "attested_at_edition"
    assert doc.version_date == date(2026, 10, 14) and doc.currency_date == date(2026, 10, 14)
    assert [p.eid for p in doc.provisions] == ["art-1", "art-2", "art-72", "art-332"]
    art1 = _prov(doc, "art-1")
    assert art1.text == (
        "La República Oriental del Uruguay es la asociación política de todos los habitantes comprendidos "
        "dentro de su territorio."
    )
    assert art1.context_heading == "SECCION I - DE LA NACION Y SU SOBERANIA / CAPITULO I"
    assert art1.note == "Ver en esta norma, artículo: 80."
    assert [r.href for r in art1.refs] == ["https://www.impo.com.uy/bases/constitucion/1967-1967/80"]
    assert all(p.valid_from is None and p.valid_to is None and p.status == "in_force" for p in doc.provisions)
    # the multi-line section title of a heading is joined; the transitional-clause link stays in the note
    assert _prov(doc, "art-332").context_heading.startswith(
        "SECCION XIX - DE LA OBSERVANCIA DE LAS LEYES ANTERIORES DEL"
    )  # type: ignore[union-attr]
    assert _prov(doc, "art-332").note == "DISPOSICIONES TRANSITORIAS Y ESPECIALES"
    types = {(e.event_type, e.date) for e in doc.events}
    assert ("publication", date(1967, 2, 2)) in types and ("version_change", date(2026, 10, 14)) in types


def test_civil_code_repeals_ranges_and_inline_articles() -> None:
    doc = _doc("codigo-civil")
    assert doc.work_uri == "/uy/codigo-civil/16603-1994" and doc.title == "Código Civil" and doc.short_title is None
    assert doc.number == "16603" and doc.work_date == date(1994, 10, 19) and doc.instrument_type == "law"
    assert doc.ext["nombre"] == "APROBADO POR LEY Nº 16.603" and doc.ext["fecha_publicacion"] == "1994-11-21"
    assert [p.eid for p in doc.provisions] == [
        "art-1",
        "art-4",
        "art-105",
        "art-131-144",
        "art-242",
        "art-242-1",
        "art-1205",
        "art-1308",
        "art-1319",
    ]
    # hard-wrapped lines are re-joined; paragraphs are kept, the (*) marker is not text
    assert _prov(doc, "art-1").text.split("\n") == [
        "Las leyes sólo son obligatorias en virtud de su promulgación por el Poder Ejecutivo.",
        "El Poder Ejecutivo determinará la forma de la promulgación; y desde que ésta pueda saberse, las leyes "
        "serán ejecutadas en todo el territorio de la República.",
        "La promulgación se reputará sabida diez días después de verificada en la Capital.",
    ]
    # "DEROGADO por ..." is the whole text: repealed, empty text, the statement kept in the note
    art4 = _prov(doc, "art-4")
    assert art4.status == "repealed" and art4.text == "" and not art4.indexable
    assert art4.note is not None and art4.note.startswith("DEROGADO por Ley No. 10.084 del 3.12.41")
    # an empty article with a "Derogado/s por:" note is repealed too
    art105 = _prov(doc, "art-105")
    assert art105.status == "repealed" and art105.text == ""
    assert art105.note is not None and art105.note.startswith("Derogado/s por: Ley Nº 20.443 de 12/12/2025 artículo 3.")
    assert _prov(doc, "art-131-144").status == "repealed" and _prov(doc, "art-131-144").num_path == ["131-144"]
    # IMPO keeps 242 and 242-1 in one entry, separated by an inline label: two provisions
    assert _prov(doc, "art-242").text.endswith("La partida de nacimiento o bautismo no hace por sí sola prueba alguna.")
    assert _prov(doc, "art-242-1").text.startswith("El padre o madre naturales declarados judicialmente tales")
    assert "Se admite" not in _prov(doc, "art-242-1").text and _prov(doc, "art-242-1").num_path == ["242-1"]
    assert _prov(doc, "art-1319").text.startswith(
        "Todo hecho ilícito del hombre que causa a otro un daño, impone a aquel por cuyo dolo, culpa o negligencia "
        "ha sucedido, la obligación de repararlo.\n"
    )
    assert "(*)" not in "".join(p.text for p in doc.provisions)
    repeal = next(e for e in doc.events if e.affecting_uri == "/uy/leyes/20443-2025")
    assert (repeal.event_type, repeal.date, repeal.locator) == ("repeal", date(2025, 12, 12), "art-105")
    assert "date of that norm" in (repeal.description or "")
    promulgation = next(e for e in doc.events if e.event_type == "promulgation")
    assert promulgation.date == date(1994, 10, 19) and "Código Civil" in (promulgation.description or "")


def test_penal_code_bis_articles_and_amendment_notes() -> None:
    doc = _doc("codigo-penal")
    bis = _prov(doc, "art-149-bis")
    assert bis.num_path == ["149-BIS"] and bis.text.startswith("(Incitación al odio, desprecio o violencia")
    assert bis.note is not None and "Redacción dada por: Ley Nº 17.677 de 29/07/2003 artículo 1." in bis.note
    types = {(e.event_type, e.affecting_uri, e.date) for e in doc.events if e.affecting_uri}
    assert ("amendment", "/uy/leyes/17677-2003", date(2003, 7, 29)) in types
    assert ("amendment", "/uy/leyes/16048-1989", date(1989, 6, 16)) in types
    assert ("repeal", "/uy/leyes/16274-1992", date(1992, 7, 6)) in types
    assert ("repeal", "/uy/leyes/14068-1972", date(1972, 7, 10)) in types  # "Pena de destierro suprimido/s por:"
    assert _prov(doc, "art-74").status == "repealed"


def test_law_reflow_items_notes_and_references() -> None:
    doc = _doc("ley-18331")
    assert doc.work_uri == "/uy/leyes/18331-2008" and doc.title == "LEY DE PROTECCION DE DATOS PERSONALES"
    assert doc.short_title == "Ley Nº 18.331" and doc.number == "18331" and doc.work_date == date(2008, 8, 11)
    assert doc.ext == {
        "impo_path": "leyes/18331-2008",
        "kind": "leyes",
        "tipo": "Ley",
        "numero": "18331",
        "anio": 2008,
        "nombre": "LEY DE PROTECCION DE DATOS PERSONALES",
        "fecha_promulgacion": "2008-08-11",
        "fecha_publicacion": "2008-08-18",
    }
    art3 = _prov(doc, "art-3")
    assert art3.text.split("\n")[:3] == [
        "Ambito objetivo.- El régimen de la presente ley será de aplicación a los datos personales registrados en "
        "cualquier soporte que los haga susceptibles de tratamiento, y a toda modalidad de uso posterior de estos "
        "datos por los ámbitos público o privado.",
        "No será de aplicación a las siguientes bases de datos:",
        "A) A las mantenidas por personas físicas en el ejercicio de actividades exclusivamente personales o "
        "domésticas.",
    ]
    art9 = _prov(doc, "art-9")
    lines = art9.text.split("\n")
    assert lines[2] == "No será necesario el previo consentimiento cuando:" and lines[3].startswith(
        "A) Los datos provengan"
    )
    assert "(*)" not in art9.text and art9.note is not None
    assert art9.note.startswith("Inciso 2º) redacción dada por: Ley Nº 18.719 de 27/12/2010 artículo 152.")
    hrefs = [r.href for r in art9.refs]
    assert "https://www.impo.com.uy/bases/leyes/18331-2008/18_BIS" in hrefs  # IMPO's own address form
    assert all(r.relation == "cites" and r.extraction_method == "source_markup" for r in art9.refs)
    assert not any("originales" in h for h in hrefs)  # links to the original text of an article are not references
    assert _prov(doc, "art-9-bis").num_path == ["9-BIS"] and _prov(doc, "art-18-bis").eid == "art-18-bis"
    amended = {(e.affecting_uri, e.date) for e in doc.events if e.event_type == "amendment"}
    assert ("/uy/leyes/19670-2018", date(2018, 10, 15)) in amended and (
        "/uy/leyes/19924-2020",
        date(2020, 12, 18),
    ) in amended
    assert len({(e.event_type, e.date, e.affecting_uri) for e in doc.events}) == len(doc.events)


def test_articles_an_amending_law_leaves_without_text() -> None:
    doc = _doc("ley-17016")
    first = _prov(doc, "art-1")
    assert first.text == "" and first.status == "in_force" and not first.indexable
    assert first.note == "Este artículo dio nueva redacción a: Decreto Ley Nº 14.294 de 31/10/1974 artículo 3."
    assert _prov(doc, "art-7").text.startswith("El Poder Ejecutivo establecerá en el decreto reglamentario")
    assert _prov(doc, "art-7").indexable


def test_decree_and_decree_law() -> None:
    decree = _doc("decreto-414-2009")
    assert decree.work_uri == "/uy/decretos/414-2009" and decree.instrument_type == "decree"
    assert decree.short_title == "Decreto Nº 414/009" and decree.number == "414"
    assert _prov(decree, "art-7").status == "repealed" and _prov(decree, "art-18").status == "repealed"
    assert (
        _prov(decree, "art-1").context_heading == "TITULO I. DISPOSICIONES GENERALES / CAPITULO I. AMBITO DE APLICACION"
    )
    assert any(e.affecting_uri == "/uy/decretos/64-2020" and e.event_type == "repeal" for e in decree.events)
    law = _doc("decreto-ley-14653")
    assert law.work_uri == "/uy/decretos-ley/14653-1977" and law.instrument_type == "law"
    assert law.short_title == "Decreto Ley Nº 14.653" and law.work_date == date(1977, 5, 25)


def test_text_cleaning() -> None:
    wrapped = "     Los preceptos de la presente ley, así como los\r\nque la reglamentan, se aplican. (*)\r\n "
    assert (
        clean_article_text(wrapped) == "Los preceptos de la presente ley, así como los que la reglamentan, se aplican."
    )
    # a break after a sentence-ending mark before an upper-case or numbered line is kept; so are items
    assert clean_article_text("Primero.\r\n   Segundo párrafo\r\ncontinúa.\r\nA) uno;\r\nB) dos,\r\n   tres.") == (
        "Primero.\nSegundo párrafo continúa.\nA) uno;\nB) dos, tres."
    )
    # abbreviations do not end a sentence; markup and entities go, words stay
    assert clean_article_text("Ley No.\r\n10.084 del <i>año</i> 1941 &amp; otras<br>Fin.") == (
        "Ley No. 10.084 del año 1941 & otras\nFin."
    )
    assert clean_article_text("<pre>\r\n  (*) \r\n</pre>") == ""
    # table rows (dot leaders, aligned columns) keep their own lines
    table = "Escuela Primaria..........   198:000.000\r\n  Liceo Secundario .........   128:000.000\r\nTotal"
    assert (
        clean_article_text(table)
        == "Escuela Primaria..........   198:000.000\nLiceo Secundario .........   128:000.000\nTotal"
    )
    assert impo_uy.decode_body("Obligaci\xf3n".encode("latin-1").decode("latin-1")) == "Obligación"
    assert impo_uy.decode_body("Obligación".encode().decode("latin-1")) == "Obligación"


def test_parser_reads_the_lenient_json_impo_sends() -> None:
    # IMPO leaves a bare CR in strings (before <br>); strict JSON refuses it
    body = _body("ley-17016").replace('"textoArticulo": "', '"textoArticulo": "\r', 1)
    with pytest.raises(ValueError, match="control character"):
        json.loads(body)
    doc = parse_impo_uy(_bundle("ley-17016", body=body))[0]
    assert len(doc.provisions) == 7


def test_parser_rejects_unexpected_input() -> None:
    good = json.loads(_bundle("ley-18331"))
    with pytest.raises(ImpoParseError, match="not an IMPO bundle"):
        parse_impo_uy(b"<html>login</html>")
    with pytest.raises(ImpoParseError, match="unsupported bundle"):
        parse_impo_uy(json.dumps({**good, "format": "other/1"}).encode())
    with pytest.raises(ImpoParseError, match="invalid norm path"):
        parse_impo_uy(json.dumps({**good, "path": "leyes/18331"}).encode())
    with pytest.raises(ImpoParseError, match="not one ULIS knows"):
        parse_impo_uy(json.dumps({**good, "path": "avisos/18331-2008"}).encode())
    with pytest.raises(ImpoParseError, match="incomplete bundle"):
        parse_impo_uy(json.dumps({k: v for k, v in good.items() if k != "retrieved_at"}).encode())
    with pytest.raises(ImpoParseError, match="incomplete bundle"):
        parse_impo_uy(json.dumps({**good, "body": "<html>no json</html>"}).encode())
    with pytest.raises(ImpoParseError, match="no articles"):
        parse_impo_uy(json.dumps({**good, "body": '{"tipoNorma": "Ley", "articulos": []}'}).encode())
    twice = json.loads(_body("ley-18331"))
    twice["articulos"].append(twice["articulos"][0])
    with pytest.raises(ImpoParseError, match="appears twice"):
        parse_impo_uy(_bundle("ley-18331", body=json.dumps(twice, ensure_ascii=False)))
    odd = json.loads(_body("ley-18331"))
    odd["articulos"][0]["tituloArticulo"] = "Artículo 1 (modificado)"
    with pytest.raises(ImpoParseError, match="unexpected article label"):
        parse_impo_uy(_bundle("ley-18331", body=json.dumps(odd, ensure_ascii=False)))


def test_resolve_uri() -> None:
    law = "/uy/leyes/18331-2008"
    assert resolve_uri("https://www.impo.com.uy/bases/leyes/18331-2008") == (law, None)
    assert resolve_uri("http://www.impo.com.uy/bases/leyes/18331-2008/18_BIS") == (law, "art-18-bis")
    assert resolve_uri("https://www.impo.com.uy/bases/codigo-civil/16603-1994/131_144") == (
        "/uy/codigo-civil/16603-1994",
        "art-131-144",
    )
    assert resolve_uri("https://www.impo.com.uy/bases/leyes-originales/18331-2008/9") is None
    assert resolve_uri("https://www.impo.com.uy/bases/avisos/123-2020") is None
    assert resolve_uri("https://www.impo.com.uy/bases/leyes/18331") is None
    assert resolve_uri("https://example.org/bases/leyes/18331-2008") is None
    assert pipeline_resolve_uri("https://www.impo.com.uy/bases/decretos/414-2009/7") == (
        "/uy/decretos/414-2009",
        "art-7",
    )
    assert pipeline_resolve_uri("/uy/decretos/414-2009/~art-7") == ("/uy/decretos/414-2009", "art-7")


# --------------------------------------------------------------------------- citations


def test_citation_styles() -> None:
    assert _citation(_doc("constitucion"), "art-72") == "artículo 72 de la Constitución de la República"
    civil = _doc("codigo-civil")
    assert _citation(civil, "art-1308") == "artículo 1308 del Código Civil"
    assert _citation(civil, "art-131-144") == "artículos 131 a 144 del Código Civil"
    assert _citation(civil, "art-242-1") == "artículo 242-1 del Código Civil"
    assert _citation(_doc("codigo-penal"), "art-149-bis") == "artículo 149 bis del Código Penal"
    assert _citation(_doc("ley-18331"), "art-9") == "artículo 9 de la Ley Nº 18.331"
    assert _citation(_doc("ley-18331"), "art-18-bis") == "artículo 18 bis de la Ley Nº 18.331"
    assert _citation(_doc("decreto-414-2009"), "art-7") == "artículo 7 del Decreto Nº 414/009"
    assert _citation(_doc("decreto-ley-14653"), "art-1") == "artículo 1 del Decreto Ley Nº 14.653"
    module = get_module("uy")
    base = {
        "instrument_title": "X",
        "instrument_type": "law",
        "jurisdiction_id": "UY",
        "top_kind": "article",
        "language": "es",
    }
    whole = ProvisionCitationInput(num_path=[], ext={"kind": "leyes", "numero": "18331"}, **base)  # type: ignore[arg-type]
    assert module.provision_citation(whole) == "Ley Nº 18.331"
    unknown = ProvisionCitationInput(num_path=["5"], ext={"kind": "otro"}, **base)  # type: ignore[arg-type]
    assert module.provision_citation(unknown) == "artículo 5 de X"
    old = ProvisionCitationInput(num_path=["3"], ext={"kind": "decretos", "numero": "123", "anio": 1991}, **base)  # type: ignore[arg-type]
    assert module.provision_citation(old) == "artículo 3 del Decreto Nº 123/991"


def test_ext_is_validated() -> None:
    module = get_module("uy")
    good = _doc("ley-18331").ext
    assert module.validate_ext(good) == good
    for bad in (
        {**good, "impo_path": "leyes/18331"},
        {**good, "numero": "18.331"},
        {**good, "fecha_promulgacion": "11/08/2008"},
        {**good, "extra": 1},
        {k: v for k, v in good.items() if k != "kind"},
    ):
        with pytest.raises(ValidationError):
            module.validate_ext(bad)


# --------------------------------------------------------------------------- connector


def _meta(name: str) -> dict[str, Any]:
    info = META[name]
    return {
        "url": info["url"],
        "final_url": info["url"],
        "status": 200,
        "content_type": info["content_type"],
        "retrieved_at": info["retrieved_at"],
    }


def _cache(tmp_path: Path, names: tuple[str, ...] = NAMES) -> Path:
    """A PoliteHttp cache holding the fixtures where the connector looks for them."""
    host = tmp_path / "www.impo.com.uy"
    host.mkdir(parents=True)
    (host / "robots.txt").write_bytes((FIX / "robots.txt").read_bytes())
    (host / "robots.txt.meta.json").write_text(json.dumps({"url": "https://www.impo.com.uy/robots.txt", "status": 200}))
    for name in names:
        target = host / "bases" / (META[name]["path"] + ".json")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((FIX / f"{name}.json").read_bytes())
        target.with_name(target.name + ".meta.json").write_text(json.dumps(_meta(name)))
    return tmp_path


def _manifest(tmp_path: Path, norms: list[dict[str, Any]], **extra: Any) -> Path:
    path = tmp_path / "manifest.yaml"
    data = {
        "schema_version": 1,
        "source_id": SOURCE,
        "base_url": "https://www.impo.com.uy",
        "user_agent": "ULIS-test (+https://example.org)",
        "crawl_delay_seconds": 10,
        "norms": norms,
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

    assert blocked("/cgi-bin/suscripcion/loginUsuario.cgi") and blocked("/descargas/x.pdf")
    assert blocked("/bases/avisos/54239-2006") and blocked("/bases/actas-ord-asse-nd/350-2015?json=true")
    for path in (
        "/bases/leyes/18331-2008?json=true",
        "/bases/codigo-civil/16603-1994?json=true",
        "/bases/constitucion/1967-1967?json=true",
        "/bases/decretos/414-2009?json=true",
    ):
        assert not blocked(path)


def test_connector_offline_from_cache(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    norms = [{"path": META[n]["path"], "title": n} for n in NAMES]
    connector = ImpoUyConnector(_manifest(tmp_path, norms), SOURCE, cache, offline=True)
    docs = list(connector.fetch())
    assert [d.external_id for d in docs] == [f"impo-uy:{META[n]['path']}" for n in NAMES]
    assert (
        docs[0].uri == "https://www.impo.com.uy/bases/constitucion/1967-1967"
        and docs[0].media_type == "application/json"
    )
    assert docs[3].content == _bundle("ley-18331")  # the exact answer is stored, bytes carried as Latin-1
    assert json.loads(docs[3].content)["body"].encode("latin-1") == (FIX / "ley-18331.json").read_bytes()
    assert docs[3].retrieved_at == datetime.fromisoformat(META["ley-18331"]["retrieved_at"]).astimezone(UTC)
    assert docs[3].metadata == {"impo_path": "leyes/18331-2008", "title": "ley-18331"}
    assert connector.http is not None and connector.http.requests_made == 0 and connector.fetch_errors == []
    assert connector.http.crawl_delay == 10.0  # the larger of the manifest's and robots.txt's Crawl-Delay
    assert sum(len(parse_impo_uy(d.content)[0].provisions) for d in docs) == sum(len(_doc(n).provisions) for n in NAMES)
    fresh = tmp_path / "empty"
    with pytest.raises(FetchError, match="offline and not cached"):
        list(ImpoUyConnector(_manifest(tmp_path, norms), SOURCE, fresh, offline=True).fetch())


def _fake_http(monkeypatch: pytest.MonkeyPatch, answers: dict[str, Any]) -> list[str]:
    asked: list[str] = []

    def get(self: PoliteHttp, url: str, cache_key: str | None = None, headers: Any = None) -> Any:
        asked.append(url)
        answer = answers[url]
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(PoliteHttp, "get", get)
    return asked


def test_connector_skips_what_is_not_a_norm_and_stops_on_refusals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    robots = (FIX / "robots.txt").read_bytes()
    base = "https://www.impo.com.uy"
    ok_path = META["ley-17016"]["path"]

    def own(i: int) -> dict[str, Any]:
        return {**_meta("ley-17016"), "final_url": f"{base}/bases/leyes/{i}-2000?json=true"}

    answers: dict[str, Any] = {
        f"{base}/robots.txt": (robots, {"status": 200}),
        f"{base}/bases/{ok_path}?json=true": ((FIX / "ley-17016.json").read_bytes(), _meta("ley-17016")),
        f"{base}/bases/leyes/1-2000?json=true": None,
        f"{base}/bases/leyes/2-2000?json=true": FetchError(
            f"{base}/bases/leyes/2-2000?json=true: HTTP 500 (not retried)"
        ),
        f"{base}/bases/leyes/3-2000?json=true": (b"<html>login</html>", own(3)),
        f"{base}/bases/leyes/4-2000?json=true": (
            (FIX / "ley-17016.json").read_bytes(),
            {**_meta("ley-17016"), "final_url": f"{base}/cgi-bin/suscripcion/loginUsuario.cgi"},
        ),
        f"{base}/bases/leyes/5-2000?json=true": (b'{"tipoNorma": "Ley"}', own(5)),
    }
    asked = _fake_http(monkeypatch, answers)
    norms = [{"path": ok_path}] + [{"path": f"leyes/{i}-2000"} for i in range(1, 6)] + [{"path": "avisos/54239-2006"}]
    norms.insert(3, {"path": "actas-ord-asse-nd/350-2015"})
    connector = ImpoUyConnector(_manifest(tmp_path, norms), SOURCE, tmp_path / "cache", sleep=lambda s: None)
    docs = list(connector.fetch())
    assert [d.external_id for d in docs] == [f"impo-uy:{ok_path}"]
    errors = "\n".join(connector.fetch_errors)
    for expected in ("not found (404)", "HTTP 500", "not a JSON norm", "loginUsuario.cgi", "disallowed by robots.txt"):
        assert expected in errors
    assert len(connector.fetch_errors) == 7  # 404, 500, two non-norms, a redirect, two paths robots.txt disallows
    assert not any("/cgi-bin" in url or "actas-ord" in url or "avisos" in url for url in asked)
    # a refusal stops the run
    answers[f"{base}/bases/leyes/1-2000?json=true"] = FetchError(
        f"{base}/x: HTTP 403 (not retried: a refusal is respected)"
    )
    with pytest.raises(FetchError, match="HTTP 403"):
        list(ImpoUyConnector(_manifest(tmp_path, norms), SOURCE, tmp_path / "cache", sleep=lambda s: None).fetch())
    # so do five failures in a row
    for i in range(1, 6):
        answers[f"{base}/bases/leyes/{i}-2000?json=true"] = FetchError(f"{base}/x: HTTP 500 (not retried)")
    with pytest.raises(FetchError, match="5 documents in a row"):
        list(ImpoUyConnector(_manifest(tmp_path, norms), SOURCE, tmp_path / "cache", sleep=lambda s: None).fetch())
    # robots.txt must be readable
    answers[f"{base}/robots.txt"] = None
    with pytest.raises(FetchError, match="refusing to fetch without reading it"):
        list(ImpoUyConnector(_manifest(tmp_path, norms), SOURCE, tmp_path / "cache", sleep=lambda s: None).fetch())


def test_connector_refuses_bad_manifests(tmp_path: Path) -> None:
    good = [{"path": "leyes/18331-2008"}]
    for norms, extra, message in (
        ([{"path": "leyes/18331-2008/../x-2000"}], {}, "invalid document path"),
        ([{"path": "leyes/18331"}], {}, "invalid document path"),
        ([{"path": "Leyes/18331-2008"}], {}, "invalid document path"),
        ([{"path": "leyes/0-2008"}], {}, "invalid document path"),
        ([{}], {}, "invalid document path"),
        (good + good, {}, "listed twice"),
        (good, {"base_url": "http://www.impo.com.uy"}, "base_url"),
        (good, {"user_agent": "python-urllib"}, "user_agent"),
        ([], {}, "no documents"),
    ):
        with pytest.raises(ManifestError, match=message):
            list(ImpoUyConnector(_manifest(tmp_path, norms, **extra), SOURCE, tmp_path).fetch())
    with pytest.raises(ManifestError, match="mismatch"):
        list(ImpoUyConnector(_manifest(tmp_path, good, source_id="other"), SOURCE, tmp_path).fetch())


def test_real_manifest_registration_and_profile() -> None:
    catalog = Catalog.load(REPO_ROOT / "config")
    source = catalog.sources.get(SOURCE)
    assert isinstance(build_connector(source), ImpoUyConnector)
    assert source.connector in CONNECTORS and source.parser in PARSERS
    manifest_path = REPO_ROOT / source.access.endpoint  # type: ignore[operator]
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    paths = [n["path"] for n in manifest["norms"]]
    assert len(paths) == len(set(paths)) >= 200
    assert {"constitucion/1967-1967", "codigo-civil/16603-1994", "codigo-penal/9155-1933", "leyes/18331-2008"} <= set(
        paths
    )
    assert {p.split("/")[0] for p in paths} >= {"constitucion", "leyes", "decretos", "decretos-ley", "codigo-civil"}
    assert manifest["crawl_delay_seconds"] >= 10 and "ULIS-research-bot" in manifest["user_agent"]
    # the committed manifest passes the connector's own validation
    ImpoUyConnector(manifest_path, SOURCE, Path("/nonexistent"))._manifest()
    licence_gate(source)
    assert source.licence.review_status == "approved" and source.authority == "official_non_authentic"
    assert source.licence.attribution_required and source.licence.url is not None
    assert source.licence.url.endswith("licencia-de-datos-abiertos.pdf")
    profile = catalog.jurisdictions.get("UY")
    assert profile.verification.status == "draft_unverified" and profile.official_languages == ["es"]
    assert profile.citation.module == "uy" and profile.coverage.corpus_status == "partial" and profile.memberships == []
    assert catalog.module_for("UY").id == "uy"


def test_fixtures_readme_names_the_source_and_licence() -> None:
    text = (FIX / "README.md").read_text(encoding="utf-8")
    assert (
        "impo.com.uy/datos-abiertos" in text and "Licencia de Datos Abiertos - Uruguay" in text and "2026-10-14" in text
    )
    for name, info in META.items():
        assert info["url"].startswith("https://www.impo.com.uy/bases/") and info["url"].endswith("?json=true")
        assert info["retrieved_at"].startswith("2026-10-14") and name in text


# --------------------------------------------------------------------------- database


class _Fixtures:
    def fetch(self) -> Iterator[RawDocument]:
        for name in NAMES:
            info = META[name]
            yield RawDocument(
                f"impo-uy:fixture/{name}",
                info["url"],
                "application/json",
                _bundle(name),
                datetime.fromisoformat(info["retrieved_at"]).astimezone(UTC),
                {"fixture": name},
            )


@pytest.fixture(scope="module")
def uy_ingested(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, ingested: IngestionReport
) -> IngestionReport:
    with session_factory() as session:
        report = Ingestor(session, catalog, store).ingest_source(SOURCE, actor="pytest", connector=_Fixtures())
        session.commit()
    return report


def _search(session: Session, catalog: Catalog, **kw: Any) -> dict[str, Any]:
    return search(session, catalog, SearchRequest(jurisdiction="UY", **kw), today=TODAY)


@pytest.mark.db
def test_ingest_report_counts(uy_ingested: IngestionReport) -> None:
    assert uy_ingested.errors == [] and uy_ingested.conflicts == []
    assert uy_ingested.documents_new == len(NAMES) and uy_ingested.instruments_created == len(NAMES)
    assert uy_ingested.provision_versions_created == sum(len(_doc(n).provisions) for n in NAMES)
    repealed = sum(1 for n in NAMES for p in _doc(n).provisions if p.status == "repealed")
    empty = sum(1 for n in NAMES for p in _doc(n).provisions if p.status != "repealed" and not p.text)
    assert repealed >= 8 and empty >= 6
    assert uy_ingested.passages_indexed == uy_ingested.provision_versions_created - repealed - empty


@pytest.mark.db
def test_search_finds_exact_text_with_citation_and_limitations(
    session: Session, catalog: Catalog, uy_ingested: IngestionReport
) -> None:
    res = _search(session, catalog, q="hecho ilícito causa a otro un daño obligación de repararlo")
    hit = res["results"][0]
    assert hit["citation"] == "artículo 1319 del Código Civil"
    assert hit["text"].startswith(
        "Todo hecho ilícito del hombre que causa a otro un daño, impone a aquel por cuyo dolo, culpa o negligencia "
        "ha sucedido, la obligación de repararlo."
    )
    assert all(h["jurisdiction"]["id"] == "UY" for h in res["results"])
    assert hit["authority"]["category"] == "applicability_unverified"
    codes = {lim["code"] for lim in hit["limitations"]}
    assert {"ATTESTED_ONLY_AT", "SOURCE_NOT_AUTHENTIC", "PROFILE_UNVERIFIED"} <= codes
    assert hit["source"]["attribution"].startswith("Fuente: IMPO")
    constitution = _search(
        session, catalog, q="enumeración de derechos, deberes y garantías inherentes a la personalidad humana"
    )
    assert constitution["results"][0]["citation"] == "artículo 72 de la Constitución de la República"
    data = _search(
        session, catalog, q="tratamiento de datos personales previo consentimiento libre expreso e informado"
    )
    assert data["results"][0]["citation"] == "artículo 9 de la Ley Nº 18.331"
    bis = _search(session, catalog, q="incitare al odio, al desprecio, o a cualquier forma de violencia moral o física")
    assert bis["results"][0]["citation"] == "artículo 149 bis del Código Penal"
    biometric = _search(
        session, catalog, q="datos biométricos evaluación de impacto en la protección de datos personales"
    )
    assert biometric["results"][0]["citation"] == "artículo 18 bis de la Ley Nº 18.331"
    paternity = _search(
        session, catalog, q="investigación de la maternidad mujer casada testimonios fehacientes del parto"
    )
    assert paternity["results"][0]["citation"] == "artículo 242 del Código Civil"
    # a repealed article is not a search result
    repealed = _search(session, catalog, q="DEROGADO por Ley No. 10.084 del 3.12.41")
    assert all(h["citation"] != "artículo 4 del Código Civil" for h in repealed["results"])


@pytest.mark.db
def test_attested_text_is_a_history_gap_and_repeals_are_reported(
    session: Session, catalog: Catalog, uy_ingested: IngestionReport
) -> None:
    uri = "/uy/codigo-civil/16603-1994/~art-1319"
    now = provision_at(session, catalog, uri, date(2026, 10, 14))
    assert now["status_on_date"] == "in_force_date_unverified" and now["version"]["currency_date"] == "2026-10-14"
    assert now["provision"]["citation"] == "artículo 1319 del Código Civil"
    earlier = provision_at(session, catalog, uri, date(1990, 1, 1))
    # only the current wording is published: 1990 is not "not yet in force", it is unknown
    assert earlier["status_on_date"] == "in_force_date_unverified"
    assert "ATTESTED_ONLY_AT" in {lim["code"] for lim in earlier["limitations"]}
    repealed = provision_at(session, catalog, "/uy/codigo-civil/16603-1994/~art-105", date(2026, 10, 14))
    assert [(t["status"], t["note"][:43]) for t in repealed["timeline"]] == [
        ("repealed", "Derogado/s por: Ley Nº 20.443 de 12/12/2025")
    ]
    res = _search(session, catalog, q="hecho ilícito causa a otro un daño", as_of=date(1990, 1, 1))
    assert res["results"][0]["citation"] == "artículo 1319 del Código Civil"
    assert "HISTORY_INCOMPLETE" in {lim["code"] for lim in res["limitations"]}


@pytest.mark.db
def test_lifecycle_events_are_recorded(session: Session, uy_ingested: IngestionReport) -> None:
    law = session.scalar(select(m.Instrument).where(m.Instrument.uri == "/uy/leyes/18331-2008"))
    assert law is not None
    events = session.scalars(select(m.LifecycleEvent).where(m.LifecycleEvent.instrument_id == law.id)).all()
    amendments = {(e.affecting_uri, e.event_date) for e in events if e.event_type == "amendment"}
    assert ("/uy/leyes/19670-2018", date(2018, 10, 15)) in amendments
    assert {e.event_type for e in events} >= {"promulgation", "publication", "amendment", "version_change"}


@pytest.mark.db
def test_reingest_is_idempotent_and_sources_verify(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, uy_ingested: IngestionReport
) -> None:
    with session_factory() as session:
        again = Ingestor(session, catalog, store).ingest_source(SOURCE, actor="pytest", connector=_Fixtures())
        assert again.documents_unchanged == len(NAMES) and again.provision_versions_created == 0
        ids = session.scalars(select(m.SourceDocument.id).where(m.SourceDocument.source_id == SOURCE)).all()
        results = [verify_source_document(session, catalog, store, i, checker="pytest") for i in ids]
        assert len(results) == len(NAMES) and all(r["result"] == "pass" for r in results)
        assert sum(r["checked_units"] for r in results) == uy_ingested.provision_versions_created
        session.rollback()

"""Italy: Normattiva Akoma Ntoso export - parser, citations, connector, ingestion and point-in-time lookups.

Fixtures are real exports (``tests/fixtures/it-normattiva/``, see its README) reduced to a few articles.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, date, datetime
from email.message import Message
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ulis.catalog import Catalog
from ulis.db import models as m
from ulis.ingestion.pipeline import IngestionReport, Ingestor, resolve_uri
from ulis.jurisdictions.modules import ProvisionCitationInput, get_module
from ulis.retrieval.lookup import provision_at
from ulis.retrieval.search import SearchRequest, search
from ulis.sources.connectors import normattiva as connector_module
from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.normattiva import FetchError, NormattivaConnector, akn_url, page_url, version_label
from ulis.sources.connectors.polite_http import PoliteHttp
from ulis.sources.parsers import normattiva as parser_module
from ulis.sources.parsers.normattiva import NormattivaParseError, parse_normattiva, skipped_attachments
from ulis.storage.object_store import LocalObjectStore

FIX = Path(__file__).parent / "fixtures" / "it-normattiva"
UA = "ULIS-test (+https://example.org)"

COST = "19471227_047U0001_{}.xml"
COST_VERSIONS = {
    "ORIGINAL": "ORIGINALE",
    "20011108": "VIGENZA_20011108",
    "20120508": "VIGENZA_20120508",
    "20211104": "VIGENZA_20211104",
    "20220309": "VIGENZA_20220309",
    "20231022": "VIGENZA_20231022",
}
OTHERS = {
    "cc": ("19420404", "042U0262", "VIGENZA_20260812", "urn:nir:stato:regio.decreto:1942-03-16;262"),
    "cp": ("19301026", "030U1398", "VIGENZA_20260930", "urn:nir:stato:regio.decreto:1930-10-19;1398"),
    "privacy": ("20030729", "003G0218", "VIGENZA_20260220", "urn:nir:stato:decreto.legislativo:2003-06-30;196"),
    "statuto": ("19700527", "070U0300", "VIGENZA_20251106", "urn:nir:stato:legge:1970-05-20;300"),
    "l241": ("19900818", "090G0294", "VIGENZA_20260421", "urn:nir:stato:legge:1990-08-07;241"),
}


def _cost(version: str) -> bytes:
    return (FIX / COST.format(COST_VERSIONS[version])).read_bytes()


def _other(key: str) -> bytes:
    day, code, version, _ = OTHERS[key]
    return (FIX / f"{day}_{code}_{version}.xml").read_bytes()


def _doc(content: bytes):  # type: ignore[no-untyped-def]
    docs = parse_normattiva(content)
    assert len(docs) == 1
    return docs[0]


def _by_eid(doc) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    return {p.eid: p for p in doc.provisions}


def _cite(doc, eid: str) -> str:  # type: ignore[no-untyped-def]
    p = _by_eid(doc)[eid]
    return get_module("it").provision_citation(
        ProvisionCitationInput(
            doc.short_title or doc.title,
            doc.instrument_type,
            "IT",
            p.top_kind,
            p.num_path,
            "it",
            ext=doc.ext,
            eid=p.eid,
        )
    )


# --------------------------------------------------------------------------- parser


def test_constitution_as_first_published_and_as_amended() -> None:
    old, new = _doc(_cost("ORIGINAL")), _doc(_cost("20231022"))
    assert old.work_uri == new.work_uri == "/it/costituzione/1947-12-27"
    assert old.expression_uri == "/it/costituzione/1947-12-27/it@1948-01-01"
    assert new.expression_uri == "/it/costituzione/1947-12-27/it@2023-10-22"
    assert (old.consolidation_kind, new.consolidation_kind) == ("as_made", "point_in_time")
    assert old.instrument_type == "constitution" and old.language == "it" and old.extent == ["IT"]
    assert old.work_date == date(1947, 12, 27) and old.ext["gazzetta_ufficiale_number"] == "298"
    assert old.ext["entry_into_force"] == "1948-01-01" and old.number is None
    # Not authentic, not official: Normattiva's own legal notice.
    assert not new.authoritative and not old.authoritative
    assert (old.text_origin, new.text_origin) == ("official_conversion", "official_revised")
    assert new.validity_basis == "revision_snapshot" and new.currency_date is None
    o, n = _by_eid(old), _by_eid(new)
    assert "ambiente" not in o["art_9"].text and "Tutela l'ambiente" in n["art_9"].text
    assert (
        o["art_9"].text.splitlines()[0]
        == "La Repubblica promuove lo sviluppo della cultura e la ricerca scientifica e tecnica."
    )
    assert n["art_9"].text.splitlines()[2].startswith("((Tutela l'ambiente, la biodiversita'")  # amendment markers kept
    assert o["art_9"].valid_from == date(1948, 1, 1) and n["art_9"].valid_from == date(2023, 10, 22)


def test_footnote_anchors_are_not_text_and_notes_are_notes() -> None:
    art10 = _by_eid(_doc(_cost("20231022")))["art_10"]
    assert "((5))" not in art10.text and not art10.text.rstrip().endswith("(5)")
    assert art10.text.splitlines()[-1] == "Non e' ammessa l'estradizione dello straniero per reati politici."
    assert art10.note is not None and art10.note.startswith("AGGIORNAMENTO (5) | La L. costituzionale 21 giugno 1967")
    assert "AGGIORNAMENTO" not in art10.text


def test_repealed_and_partly_repealed_provisions() -> None:
    new = _by_eid(_doc(_cost("20231022")))
    old = _by_eid(_doc(_cost("ORIGINAL")))
    repealed = new["art_115"]
    assert repealed.status == "repealed" and repealed.text == "" and repealed.valid_from == date(2023, 10, 22)
    assert repealed.note == "ARTICOLO ABROGATO DALLA L. COSTITUZIONALE 18 OTTOBRE 2001, N. 3"
    assert not repealed.indexable and old["art_115"].status == "in_force" and old["art_115"].text
    # A comma repealed inside a live article stays in the text as Normattiva prints it.
    assert new["art_125"].status == "in_force"
    assert new["art_125"].text.startswith("((COMMA ABROGATO DALLA L. COSTITUZIONALE 18 OTTOBRE 2001, N. 3)).")


def test_transitional_and_final_provisions_of_the_constitution() -> None:
    new = _by_eid(_doc(_cost("20231022")))
    assert {"trans_I", "trans_XIII"} <= set(new)
    p = new["trans_XIII"]
    assert (p.kind, p.top_kind, p.num_path) == ("transitional", "transitional", ["trans:XIII"])
    assert p.text.startswith("I membri e i discendenti di Casa Savoia non sono elettori")
    assert new["trans_I"].text.startswith("Con l'entrata in vigore della Costituzione il Capo provvisorio dello Stato")
    assert "DISPOSIZIONI TRANSITORIE" not in new["trans_I"].text


def test_structured_act_commi_lettere_and_inline_numbers() -> None:
    doc = _doc(_other("l241"))
    p = _by_eid(doc)
    assert doc.work_uri == "/it/legge/1990/241" and doc.number == "241" and doc.instrument_type == "act"
    assert doc.work_date == date(1990, 8, 7) and doc.version_date == date(2026, 4, 21)
    assert doc.title.startswith("Legge 7 agosto 1990, n. 241 - Nuove norme in materia di procedimento amministrativo")
    assert doc.ext["entry_into_force"] == "1990-09-02" and doc.ext["citation_label"] == "L. 241/1990"
    art6 = p["art_6"]
    assert art6.heading == "Compiti del responsabile del procedimento"  # parentheses are the rubrica's, not text
    assert not art6.is_leaf and not art6.indexable and art6.top_kind == "article"
    para = p["art_6/para_1"]
    assert para.has_own_content and para.text.startswith("Il responsabile del procedimento:\na) valuta")
    point = p["art_6/para_1/let_a"]
    assert (point.kind, point.num_path, point.parent_eid) == ("point", ["6", "comma:1", "lett:a"], "art_6/para_1")
    assert point.text.startswith("valuta, ai fini istruttori") and point.context_heading == art6.heading
    # A comma inserted by an amendment is numbered inside its text.
    assert p["art_1/para_2-bis"].num_path == ["1", "comma:2-bis"]
    assert p["art_1/para_2-bis"].text.startswith("((2-bis. I rapporti tra il cittadino")
    assert p["art_1"].text.startswith("1. L'attivita' amministrativa persegue")
    # Footnote anchor "(14)" removed from the text, the note kept.
    art2_2 = p["art_2/para_2"]
    assert art2_2.text.endswith("entro il termine di trenta giorni.")
    assert any("AGGIORAMENTO (14)" in (c.note or "") for c in p.values() if c.eid.startswith("art_2/"))
    assert p["art_31"].status == "repealed" and p["art_31"].note == "ARTICOLO ABROGATO DALLA L. 11 FEBBRAIO 2005, N. 15"
    assert (
        p["art_2-bis"].heading == "Conseguenze per il ritardo dell'amministrazione nella conclusione del procedimento"
    )


def test_commi_the_source_does_not_number_stay_in_their_article() -> None:
    p = _by_eid(_doc(_other("statuto")))
    art18 = p["art_18"]
    assert (
        art18.is_leaf
        and art18.kind == "article"
        and art18.heading == "Tutela del lavoratore in caso di licenziamento illegittimo"
    )
    assert len(art18.text.splitlines()) >= 10 and art18.text.startswith(
        "Il giudice, con la sentenza con la quale dichiara la nullita'"
    )
    assert not [e for e in p if e.startswith("art_18/")]
    assert p["art_33"].status == "repealed" and p["art_1"].heading == "Liberta' di opinione"


def test_privacy_code_and_the_annexes_that_are_not_read() -> None:
    content = _other("privacy")
    doc = _doc(content)
    p = _by_eid(doc)
    assert doc.instrument_type == "legislative_decree" and doc.work_uri == "/it/decreto.legislativo/2003/196"
    assert doc.ext["citation_label"] == "d.lgs. 196/2003" and doc.ext["entry_into_force"] == "2004-01-01"
    assert p["art_4"].status == "repealed" and "D.LGS. 10 AGOSTO 2018, N. 101" in (p["art_4"].note or "")
    quindecies = p["art_2-quindecies"]  # the source repeats "Art. 2-quinquiesdecies" before the marker
    assert quindecies.status == "repealed" and quindecies.text == ""
    assert p["art_167/para_1"].text.startswith("Salvo che il fatto costituisca piu' grave reato, chiunque")
    assert p["art_167"].text.startswith("1. Salvo che il fatto")
    assert skipped_attachments(content) == ["Allegato B", "Allegato C"]


def test_civil_code_articles_preliminary_provisions_and_source_quirks() -> None:
    doc = _doc(_other("cc"))
    p = _by_eid(doc)
    assert (
        doc.work_uri == "/it/codice.civile/1942/262" and doc.instrument_type == "code" and doc.title == "Codice civile"
    )
    assert doc.ext["approved_by"] == "Regio decreto 16 marzo 1942, n. 262" and doc.ext["citation_label"] == "c.c."
    a = p["art_2043"]
    assert a.heading == "Risarcimento per fatto illecito" and a.num_path == ["2043"] and a.is_leaf
    assert a.text == (
        "Qualunque fatto doloso o colposo, che cagiona ad altri un danno ingiusto, "
        "obbliga colui che ha commesso il fatto a risarcire il danno."
    )
    # The two articles of the approving decree are not the code's art. 1 and 2.
    assert p["art_1"].heading == "Capacita' giuridica" and p["art_1"].text.startswith(
        "La capacita' giuridica si acquista"
    )
    assert p["art_2"].heading == "Maggiore eta'. Capacita' di agire"  # rubrica inside amendment markers
    assert p["prel_art_12"].num_path == ["prel:12"] and p["prel_art_12"].heading == "Interpretazione della legge"
    assert p["prel_art_19"].status == "repealed"
    assert p["art_314_2"].num_path == ["314/2"] and p["art_314_2"].status == "repealed"
    # The article line says 1159 and the attachment 1159 bis (a slip in the source): the attachment wins.
    assert "art_1159-bis" in p and "art_1159" not in p
    # The attachment says "novies", the article line "nonies": the printed number is kept.
    assert "art_1519-nonies" in p and "art_1519-novies" not in p
    assert [e for e in p if e.startswith("prel_")] == ["prel_art_1", "prel_art_12", "prel_art_19"]


def test_penal_code() -> None:
    doc = _doc(_other("cp"))
    p = _by_eid(doc)
    assert doc.work_uri == "/it/codice.penale/1930/1398" and doc.ext["citation_label"] == "c.p."
    assert p["art_575"].heading == "Omicidio" and p["art_575"].text == (
        "Chiunque cagiona la morte di un uomo e' punito con la reclusione non inferiore ad anni ventuno."
    )
    assert p["art_575"].note is not None and p["art_575"].note.startswith("AGGIORNAMENTO (96)")
    assert p["art_270-bis"].num_path == ["270-bis"] and p["art_270-bis"].text.count("\n") >= 3
    assert p["art_519"].status == "repealed" and p["art_519"].text == ""  # "Codice Penale-art. 519" echo dropped
    assert p["art_640"].text.splitlines()[1].startswith("La pena e' della reclusione da uno a cinque anni")


def test_references_become_ulis_uris() -> None:
    p = _by_eid(_doc(_other("l241")))
    assert "/it/legge/1988/400/~art_17" in [r.href for r in p["art_2-bis/para_1-bis"].refs]
    ref = parser_module._ref_uri
    assert ref("/akn/it/act/costituzione/stato/1947-12-27/const/!main#art_117-com2-letm") == (
        "/it/costituzione/1947-12-27/~art_117"
    )
    assert (
        ref("/akn/it/act/decretoLegislativo/stato/2006-04-11/198/!main#art_35")
        == "/it/decreto.legislativo/2006/198/~art_35"
    )
    assert ref("/akn/it/act/legge/stato/2009/69/!main") == "/it/legge/2009/69"
    assert ref("/akn/it/act/regioDecreto/stato/1930-10-19/1398/!main") == "/it/codice.penale/1930/1398"
    assert (
        ref("/akn/it/act/codice.civile/stato/1942-03-16/262/!main#art_2-bis") == "/it/codice.civile/1942/262/~art_2-bis"
    )
    assert ref("/akn/it/act/direttivaUe/eu/1995/46/!main") is None and ref("#x") is None


def test_resolve_uri() -> None:
    assert resolve_uri("urn:nir:stato:legge:1990-08-07;241~art2") == ("/it/legge/1990/241", "art_2")
    assert resolve_uri("https://www.normattiva.it/uri-res/N2Ls?urn:nir:stato:legge:1990-08-07;241") == (
        "/it/legge/1990/241",
        None,
    )
    assert resolve_uri("urn:nir:stato:costituzione:1947-12-27")[0] == "/it/costituzione/1947-12-27"
    assert resolve_uri("/it/legge/1990/241/~art_2") == ("/it/legge/1990/241", "art_2")
    assert resolve_uri("https://example.org/x") == ("https://example.org/x", None)


def test_unsupported_documents_are_rejected_loudly() -> None:
    good = _other("statuto")
    with pytest.raises(NormattivaParseError, match="unsupported root"):
        parse_normattiva(b'<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"/>')
    with pytest.raises(NormattivaParseError, match="unexpected ELI"):
        parse_normattiva(good.replace(b"eli/id/1970/05/27/070U0300/", b"eli/x/"))
    with pytest.raises(NormattivaParseError, match="unsupported kind of act"):
        parse_normattiva(good.replace(b"urn:nir:stato:legge:", b"urn:nir:stato:decreto.ministeriale:"))
    with pytest.raises(NormattivaParseError, match="no provisions"):
        parse_normattiva(re.sub(rb"<article .*</article>", b"", good, flags=re.S))
    with pytest.raises(Exception, match="(?i)entit|dtd|forbidden"):
        parse_normattiva(b'<?xml version="1.0"?><!DOCTYPE a [<!ENTITY x "y">]><akomaNtoso xmlns="x"/>')


def test_ext_is_validated_and_citations_follow_the_italian_convention() -> None:
    module = get_module("it")
    for key in ("cc", "cp", "privacy", "statuto", "l241"):
        module.validate_ext(_doc(_other(key)).ext)
    module.validate_ext(_doc(_cost("ORIGINAL")).ext)
    with pytest.raises(PydanticValidationError):
        module.validate_ext({**_doc(_other("cc")).ext, "unexpected": 1})
    cost, cc, cp = _doc(_cost("20231022")), _doc(_other("cc")), _doc(_other("cp"))
    assert _cite(cost, "art_9") == "art. 9 Cost."
    assert _cite(cost, "trans_XIII") == "XIII disp. trans. fin. Cost."
    assert _cite(cc, "art_2043") == "art. 2043 c.c."
    assert _cite(cc, "prel_art_12") == "art. 12 disp. prel. c.c."
    assert _cite(cc, "art_314_2") == "art. 314/2 c.c."
    assert _cite(cp, "art_270-bis") == "art. 270-bis c.p."
    assert _cite(_doc(_other("l241")), "art_6/para_1/let_a") == "art. 6, comma 1, lett. a), L. 241/1990"
    assert _cite(_doc(_other("privacy")), "art_167/para_1") == "art. 167, comma 1, d.lgs. 196/2003"
    assert _cite(_doc(_other("statuto")), "art_18") == "art. 18 L. 300/1970"

    def cite(path: list[str], label: str) -> str:
        return module.provision_citation(
            ProvisionCitationInput("x", "act", "IT", "article", path, "it", ext={"citation_label": label})
        )

    assert cite(["3", "comma:1"], "Cost.") == "art. 3, comma 1, Cost."
    assert cite(["2", "comma:1", "lett:a"], "d.lgs. 196/2003") == "art. 2, comma 1, lett. a), d.lgs. 196/2003"
    assert cite(["640", "comma:2", "num:1"], "c.p.") == "art. 640, comma 2, n. 1, c.p."
    assert cite([], "c.c.") == "x"


# --------------------------------------------------------------------------- connector


def _put(cache: Path, url: str, content: bytes) -> None:
    http = PoliteHttp(cache, UA, allowed_hosts={connector_module.HOST}, offline=True)
    path = http.cache_path(url)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    path.with_name(path.name + ".meta.json").write_text(
        json.dumps(
            {
                "url": url,
                "final_url": url,
                "status": 200,
                "content_type": "text/xml",
                "retrieved_at": "2026-10-14T12:00:00+00:00",
            }
        )
    )


def _manifest(tmp_path: Path, vigenza: list[str], **overrides: Any) -> Path:
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "source_id": "it-normattiva",
        "user_agent": UA,
        "crawl_delay_seconds": 6,
        "works": [
            {
                "code": "047U0001",
                "gazzetta_date": "1947-12-27",
                "urn": "urn:nir:stato:costituzione:1947-12-27",
                "vigenza": vigenza,
            }
        ],
        **overrides,
    }
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(manifest, allow_unicode=True))
    return path


def _cost_url(when: str) -> str:
    return akn_url(date(1947, 12, 27), "047U0001", date.fromisoformat(when))


def test_version_label_and_urls() -> None:
    assert version_label(_cost("ORIGINAL")) == "ORIGINAL" and version_label(_cost("20011108")) == "20011108"
    assert version_label(b"<html>Errore nel caricamento delle informazioni</html>") is None
    assert _cost_url("1948-01-01") == (
        "https://www.normattiva.it/do/atto/caricaAKN?dataGU=19471227&codiceRedaz=047U0001&dataVigenza=19480101"
    )
    assert page_url("urn:nir:stato:legge:1990-08-07;241") == (
        "https://www.normattiva.it/uri-res/N2Ls?urn:nir:stato:legge:1990-08-07;241"
    )


def test_connector_reads_the_cache_offline_oldest_first_and_yields_each_version_once(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    for when, version in [
        ("1948-01-01", "ORIGINAL"),
        ("2001-12-31", "20011108"),
        ("2002-06-01", "20011108"),  # the same version again
        ("2026-10-14", "20231022"),
    ]:
        _put(cache, _cost_url(when), _cost(version))
    manifest = _manifest(tmp_path, ["1948-01-01", "2001-12-31", "2002-06-01", "2026-10-14"])
    docs = list(NormattivaConnector(manifest, "it-normattiva", cache, offline=True).fetch())
    assert [d.external_id for d in docs] == [
        "normattiva:047U0001@ORIGINAL",
        "normattiva:047U0001@20011108",
        "normattiva:047U0001@20231022",
    ]
    assert [d.metadata["requested_vigenza"] for d in docs] == ["1948-01-01", "2001-12-31", "2026-10-14"]
    assert docs[1].uri == _cost_url("2001-12-31") and docs[1].media_type == "application/akn+xml"
    assert docs[0].retrieved_at == datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
    assert docs[2].content == _cost("20231022") and docs[2].metadata["urn"] == "urn:nir:stato:costituzione:1947-12-27"


def test_connector_requires_identification_order_cache_and_politeness(tmp_path: Path) -> None:
    with pytest.raises(FetchError, match="offline and not cached"):
        list(NormattivaConnector(_manifest(tmp_path, ["2026-10-14"]), "it-normattiva", tmp_path, offline=True).fetch())
    for overrides, message in [
        ({"user_agent": "python"}, "user_agent"),
        ({"crawl_delay_seconds": 1}, "crawl_delay_seconds"),
    ]:
        with pytest.raises(ManifestError, match=message):
            list(
                NormattivaConnector(_manifest(tmp_path, ["2026-10-14"], **overrides), "it-normattiva", tmp_path).fetch()
            )
    with pytest.raises(ManifestError, match="oldest first"):
        list(NormattivaConnector(_manifest(tmp_path, ["2026-10-14", "1948-01-01"]), "it-normattiva", tmp_path).fetch())
    with pytest.raises(ManifestError, match="source_id"):
        list(NormattivaConnector(_manifest(tmp_path, ["2026-10-14"]), "other", tmp_path).fetch())
    bad = _manifest(tmp_path, ["2026-10-14"])
    data = yaml.safe_load(bad.read_text())
    data["works"][0]["urn"] = "https://example.org/x"
    bad.write_text(yaml.safe_dump(data))
    with pytest.raises(ManifestError, match="invalid URN"):
        list(NormattivaConnector(bad, "it-normattiva", tmp_path).fetch())
    with pytest.raises(FetchError, match="only https to"):
        PoliteHttp(tmp_path, UA, allowed_hosts={connector_module.HOST}).get("https://dati.normattiva.it/")


class _Response:
    """A minimal urlopen() result with a Set-Cookie header."""

    status = 200

    def __init__(self, url: str, cookie: str | None):
        self._url = url
        self.headers = Message()
        self.headers["Content-Type"] = "text/html"
        if cookie:
            self.headers["Set-Cookie"] = cookie

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self) -> bytes:
        return b"ok"

    def geturl(self) -> str:
        return self._url

    def info(self) -> Message:
        return self.headers


def test_polite_http_keeps_session_cookies_only_when_asked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[str | None] = []

    def fake_urlopen(request: Any, **kwargs: Any) -> _Response:
        sent.append(request.get_header("Cookie"))
        return _Response(request.full_url, "JSESSIONID=abc123; path=/; Secure; HttpOnly" if len(sent) == 1 else None)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    for cookies, expected in ((True, [None, "JSESSIONID=abc123"]), (False, [None, None])):
        sent.clear()
        http = PoliteHttp(
            tmp_path / str(cookies), UA, allowed_hosts={"www.normattiva.it"}, cookies=cookies, sleep=lambda _: None
        )
        http.get("https://www.normattiva.it/page")
        http.get("https://www.normattiva.it/other")
        assert sent == expected
        assert (http.cookie_jar is not None) is cookies


class _FakeHttp:
    """Stands in for PoliteHttp: records requests, keeps a cookie flag, caches like the real one."""

    script: list[bytes] = []
    log: list[tuple[str, str | None]] = []
    last: _FakeHttp | None = None

    def __init__(self, cache_dir: Path, user_agent: str, **kwargs: Any) -> None:
        self.cache_dir, self.kwargs = cache_dir, kwargs
        _FakeHttp.last = self

    def cache_path(self, url: str, cache_key: str | None = None) -> Path:
        return self.cache_dir / hashlib.sha256(url.encode()).hexdigest()[:16]

    def get(self, url: str, cache_key: str | None = None, headers: dict[str, str] | None = None):  # type: ignore[no-untyped-def]
        path = self.cache_path(url)
        meta_path = path.with_name(path.name + ".meta.json")
        if path.exists() and meta_path.exists():
            return path.read_bytes(), json.loads(meta_path.read_text())
        _FakeHttp.log.append((url, (headers or {}).get("Referer")))
        content = b"<html>page</html>" if "uri-res" in url else _FakeHttp.script.pop(0)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        meta = {"url": url, "final_url": url, "status": 200, "retrieved_at": "2026-10-14T12:00:00+00:00"}
        path.with_name(path.name + ".meta.json").write_text(json.dumps(meta))
        return content, meta


def test_connector_starts_the_session_sends_the_page_as_referer_and_never_keeps_an_error_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(connector_module, "PoliteHttp", _FakeHttp)
    manifest = _manifest(tmp_path, ["2026-10-14"])
    page = page_url("urn:nir:stato:costituzione:1947-12-27")
    # The first answer is an error page (session refused): the connector starts the session once more.
    _FakeHttp.script, _FakeHttp.log = [b"<html>Errore nel caricamento delle informazioni</html>", _cost("20231022")], []
    docs = list(NormattivaConnector(manifest, "it-normattiva", tmp_path / "c").fetch())
    assert [d.external_id for d in docs] == ["normattiva:047U0001@20231022"]
    assert _FakeHttp.log == [
        (page, None),
        (_cost_url("2026-10-14"), page),
        (page, None),
        (_cost_url("2026-10-14"), page),
    ]
    assert _FakeHttp.last is not None and _FakeHttp.last.kwargs["cookies"] is True
    assert _FakeHttp.last.kwargs["allowed_hosts"] == {"www.normattiva.it"} and _FakeHttp.last.kwargs["crawl_delay"] == 6
    assert not _FakeHttp.last.cache_path(page).exists()  # the page is read for the cookie only
    # Refused twice: an error, and nothing cached for that URL.
    _FakeHttp.script, _FakeHttp.log = [b"<html>Errore</html>", b"<html>Errore</html>"], []
    with pytest.raises(FetchError, match="did not accept the session"):
        list(NormattivaConnector(manifest, "it-normattiva", tmp_path / "d").fetch())
    assert not _FakeHttp.last.cache_path(_cost_url("2026-10-14")).exists()
    # Everything cached: no session is started and nothing is requested.
    _FakeHttp.script, _FakeHttp.log = [_cost("20231022")], []
    cache = tmp_path / "e"
    assert len(list(NormattivaConnector(manifest, "it-normattiva", cache).fetch())) == 1  # fills the cache
    _FakeHttp.log = []
    assert len(list(NormattivaConnector(manifest, "it-normattiva", cache).fetch())) == 1
    assert _FakeHttp.log == []


# --------------------------------------------------------------------------- PostgreSQL


class _Docs:
    def __init__(self, docs: list[RawDocument]):
        self.docs = docs

    def fetch(self):  # type: ignore[no-untyped-def]
        yield from self.docs


def _raw(code: str, label: str, content: bytes, urn: str) -> RawDocument:
    return RawDocument(
        f"normattiva:{code}@{label}",
        f"https://www.normattiva.it/do/atto/caricaAKN?codiceRedaz={code}&version={label}",
        "application/akn+xml",
        content,
        datetime(2026, 10, 14, 12, 0, tzinfo=UTC),
        {"urn": urn},
    )


@pytest.fixture(scope="module")
def it_ingested(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, ingested: IngestionReport
) -> IngestionReport:
    docs = [_raw("047U0001", v, _cost(v), "urn:nir:stato:costituzione:1947-12-27") for v in COST_VERSIONS]
    docs += [_raw(code, version, _other(key), urn) for key, (_, code, version, urn) in OTHERS.items()]
    with session_factory() as session:
        report = Ingestor(session, catalog, store).ingest_source("it-normattiva", actor="pytest", connector=_Docs(docs))
        session.commit()
    return report


def _search(session: Session, catalog: Catalog, **kw: Any) -> dict[str, Any]:
    kw.setdefault("as_of", date(2026, 10, 14))
    return search(session, catalog, SearchRequest(jurisdiction="IT", **kw), today=date(2026, 10, 14))


def _versions(session: Session, uri: str) -> list[tuple[date | None, date | None, str]]:
    rows = session.scalars(
        select(m.ProvisionVersion)
        .join(m.Provision)
        .where(m.Provision.uri == uri)
        .order_by(m.ProvisionVersion.valid_from)
    ).all()
    return [(v.valid_from, v.valid_to, v.status) for v in rows]


@pytest.mark.db
def test_ingestion_report_and_stored_instruments(session: Session, it_ingested: IngestionReport) -> None:
    assert it_ingested.errors == [] and it_ingested.conflicts == []
    assert it_ingested.documents_new == 11 and it_ingested.instruments_created == 6
    assert it_ingested.provision_versions_corroborated > 50  # unchanged wording continues across versions
    uris = set(session.scalars(select(m.Instrument.uri).where(m.Instrument.jurisdiction_id == "IT")))
    assert uris == {
        "/it/costituzione/1947-12-27",
        "/it/codice.civile/1942/262",
        "/it/codice.penale/1930/1398",
        "/it/decreto.legislativo/2003/196",
        "/it/legge/1970/300",
        "/it/legge/1990/241",
    }
    cost = session.scalar(select(m.Instrument).where(m.Instrument.uri == "/it/costituzione/1947-12-27"))
    assert cost is not None and cost.instrument_type == "constitution" and cost.ext["citation_label"] == "Cost."
    origins = {
        (v.version_date.isoformat(), v.text_origin, v.consolidation_kind, v.authoritative)
        for v in session.scalars(select(m.InstrumentVersion).where(m.InstrumentVersion.instrument_id == cost.id))
    }
    assert ("1948-01-01", "official_conversion", "as_made", False) in origins
    assert ("2023-10-22", "official_revised", "point_in_time", False) in origins


@pytest.mark.db
def test_unchanged_wording_continues_and_amendments_start_new_versions(
    session: Session, it_ingested: IngestionReport
) -> None:
    base = "/it/costituzione/1947-12-27/~"
    # Art. 1 never changed: one version from the text as first published.
    assert _versions(session, base + "art_1") == [(date(1948, 1, 1), None, "in_force")]
    # Art. 9 changed once (L. cost. 1/2022, in force 2022-03-09); the earlier wording ends there.
    assert _versions(session, base + "art_9") == [
        (date(1948, 1, 1), date(2022, 3, 9), "in_force"),
        (date(2022, 3, 9), None, "in_force"),
    ]
    # Art. 117 changed in 2001 and 2012; art. 115 was repealed in 2001.
    assert [v[0] for v in _versions(session, base + "art_117")] == [
        date(1948, 1, 1),
        date(2001, 11, 8),
        date(2012, 5, 8),
    ]
    assert _versions(session, base + "art_115") == [
        (date(1948, 1, 1), date(2001, 11, 8), "in_force"),
        (date(2001, 11, 8), None, "repealed"),
    ]


@pytest.mark.db
def test_point_in_time_follows_the_versions_the_source_states(
    session: Session, catalog: Catalog, it_ingested: IngestionReport
) -> None:
    def at(eid: str, when: date) -> dict[str, Any]:
        return provision_at(session, catalog, f"/it/costituzione/1947-12-27/~{eid}", when, language="it")

    before, after = at("art_9", date(2022, 3, 8)), at("art_9", date(2022, 3, 9))
    assert "ambiente" not in before["version"]["text"] and "Tutela l'ambiente" in after["version"]["text"]
    assert before["version"]["valid_to"] == "2022-03-09" and after["version"]["valid_from"] == "2022-03-09"
    assert after["provision"]["citation"] == "art. 9 Cost."
    # Art. 81: the text before and after the 2012 amendment (in force 2012-05-08).
    assert at("art_81", date(2012, 5, 7))["version"]["text"].startswith("Le Camere approvano ogni anno i bilanci")
    assert at("art_81", date(2012, 5, 8))["version"]["text"].startswith("((Lo Stato assicura l'equilibrio")
    # Art. 115 was in force until the 2001 reform and is repealed since.
    assert at("art_115", date(2000, 1, 1))["status_on_date"] == "in_force"
    repealed = at("art_115", date(2005, 1, 1))
    assert repealed["status_on_date"] == "repealed" and repealed["version"]["valid_from"] == "2001-11-08"
    # The Constitution entered into force on 1948-01-01; nothing is indexed before.
    assert at("art_1", date(1947, 12, 31))["status_on_date"] in ("history_not_indexed", "not_yet_in_force")
    assert at("art_1", date(2026, 10, 14))["status_on_date"] == "in_force"
    # An instrument with a single version: earlier dates are a history gap, not a guess.
    cc = "/it/codice.civile/1942/262/~"
    gap = provision_at(session, catalog, cc + "art_2043", date(1990, 1, 1), language="it")
    assert gap["status_on_date"] == "history_not_indexed"
    now = provision_at(session, catalog, cc + "art_2043", date(2026, 10, 14), language="it")
    assert now["status_on_date"] == "in_force" and now["provision"]["citation"] == "art. 2043 c.c."
    old_repeal = provision_at(session, catalog, cc + "prel_art_19", date(2026, 10, 14), language="it")
    assert old_repeal["status_on_date"] == "repealed"


@pytest.mark.db
def test_search_finds_known_provisions_with_italian_citations_and_limitations(
    session: Session, catalog: Catalog, it_ingested: IngestionReport
) -> None:
    res = _search(session, catalog, q="Qualunque fatto doloso o colposo che cagiona ad altri un danno ingiusto")
    hit = res["results"][0]
    assert hit["citation"] == "art. 2043 c.c." and hit["unit"]["heading"] == "Risarcimento per fatto illecito"
    assert hit["text"].startswith("Qualunque fatto doloso o colposo, che cagiona ad altri un danno ingiusto")
    codes = {lim["code"] for lim in hit["limitations"]}
    assert {"PROFILE_UNVERIFIED", "SNAPSHOT_DATES", "CURRENCY_UNKNOWN", "SOURCE_NOT_AUTHENTIC"} <= codes
    assert "COMPILATION_NOT_OFFICIAL" not in codes  # that wording is the United States Code's
    assert hit["source"]["attribution"].startswith("Fonte: Normattiva")
    assert hit["version"]["language"] == "it"

    cost = _search(session, catalog, q="La sovranita' appartiene al popolo")
    assert cost["results"][0]["citation"] == "art. 1 Cost."
    old = _search(session, catalog, q="Il giudice, con la sentenza con la quale dichiara la nullita' del licenziamento")
    assert old["results"][0]["citation"] == "art. 18 L. 300/1970"
    privacy = _search(session, catalog, q="salvo che il fatto costituisca piu' grave reato chiunque trarre profitto")
    assert any(h["citation"] == "art. 167, comma 1, d.lgs. 196/2003" for h in privacy["results"])
    lettera = _search(session, catalog, q="valuta, ai fini istruttori, le condizioni di ammissibilita'")
    assert lettera["results"][0]["citation"] == "art. 6, comma 1, lett. a), L. 241/1990"
    penal = _search(session, catalog, q="Chiunque cagiona la morte di un uomo e' punito con la reclusione")
    assert penal["results"][0]["citation"] == "art. 575 c.p."


@pytest.mark.db
def test_repealed_provisions_are_not_indexed_and_references_are_stored(
    session: Session, catalog: Catalog, it_ingested: IngestionReport
) -> None:
    res = _search(session, catalog, q="ARTICOLO ABROGATO DALLA L. COSTITUZIONALE 18 OTTOBRE 2001")
    assert not [h for h in res["results"] if h["citation"] == "art. 115 Cost."]
    links = set(
        session.scalars(select(m.CitationLink.to_uri).where(m.CitationLink.from_work_uri == "/it/legge/1990/241"))
    )
    assert "/it/legge/1988/400/~art_17" in links


# --------------------------------------------------------------------------- the codes page and the other codes
# (the manifest was widened to the acts of Normattiva's "Costituzione e Codici" page; see docs/jurisdictions/it.md)

DISP_ATT = ("19411224", "041U1368", "VIGENZA_20241126")  # disp. att. c.p.c. (5 of 307 attachments kept)
MILITARY = ("19410506", "041U0303", "VIGENZA_20221230")  # the two military penal codes (5 of 738 kept)
GIUSTIZIA_CONTABILE = ("20160907", "16G00187", "VIGENZA_20230101")  # three sets of attachments (12 of 251 kept)
AMBIENTE = ("20060414", "006G0171", "VIGENZA_20261007")  # two different art. 298-bis (3 articles kept)


def _fixture(parts: tuple[str, str, str]) -> bytes:
    return (FIX / f"{parts[0]}_{parts[1]}_{parts[2]}.xml").read_bytes()


def test_a_code_exported_as_attachments_is_read_article_by_article() -> None:
    doc = _doc(_fixture(DISP_ATT))
    assert (doc.instrument_type, doc.work_uri) == ("royal_decree", "/it/regio.decreto/1941/1368")
    assert doc.ext["citation_label"] == "disp. att. c.p.c." and doc.ext["approved_by"].endswith("n. 1368")
    assert [p.eid for p in doc.provisions] == ["art_1", "art_2", "art_3", "art_118", "art_230"]
    assert _cite(doc, "art_118") == "art. 118 disp. att. c.p.c."
    assert _by_eid(doc)["art_1"].text.startswith("In ogni stato e grado del processo il pubblico ministero")
    assert skipped_attachments(_fixture(DISP_ATT)) == []


def test_two_codes_in_one_decree_are_cited_each_by_its_own_abbreviation() -> None:
    content = _fixture(MILITARY)
    doc = _doc(content)
    assert doc.instrument_type == "code" and doc.work_uri == "/it/regio.decreto/1941/303"
    assert [p.eid for p in doc.provisions] == ["c-p-m-p_art_1", "c-p-m-p_art_2", "c-p-m-g_art_1", "c-p-m-g_art_33"]
    assert _cite(doc, "c-p-m-p_art_1") == "art. 1 c.p.m.p." and _cite(doc, "c-p-m-g_art_1") == "art. 1 c.p.m.g."
    # The source has two attachments named "art. 33": one is art. 22 under the wrong name and is not read.
    assert _by_eid(doc)["c-p-m-g_art_33"].text.startswith("Per i condannati a una pena detentiva")
    assert skipped_attachments(content) == ["Codice penale militare di guerra - art. 33"]


def test_attachment_sets_that_number_their_articles_alike_are_told_apart() -> None:
    doc = _doc(_fixture(GIUSTIZIA_CONTABILE))
    by_eid = _by_eid(doc)
    # The most frequent set is "art. N"; the others are cited by their name.
    assert {"art_1", "art_10", "art_100", "art_101"} <= set(by_eid)
    allegato_2 = [e for e in by_eid if e.startswith("allegato-2-norme-di-attuazione")]
    assert len(allegato_2) == 4 and all(len(e) < 100 for e in by_eid)
    assert _cite(doc, "art_1") == "art. 1 d.lgs. 174/2016"
    assert _cite(doc, allegato_2[0]) == "art. 1 Allegato 2 Norme di attuazione del Codice della Giustizia contabile"
    assert by_eid["art_1"].text != by_eid[allegato_2[0]].text


def test_an_act_with_annexed_codes_but_its_own_articles_is_not_a_flat_act() -> None:
    privacy = _doc(_other("privacy"))
    assert privacy.ext["act_type"] == "decreto.legislativo" and "approved_by" not in privacy.ext
    assert "art_167" in _by_eid(privacy)


def test_two_different_articles_with_the_same_number_are_both_kept() -> None:
    doc = _doc(_fixture(AMBIENTE))
    by_eid = _by_eid(doc)
    assert {"art_298", "art_298-bis", "art_298-bis--2"} <= set(by_eid)
    assert by_eid["art_298-bis"].text.startswith("1. Sono vietati, con riferimento alle sostanze relative")
    assert "disciplina della parte sesta" in by_eid["art_298-bis--2"].text
    assert _cite(doc, "art_298-bis--2") == "art. 298-bis d.lgs. 152/2006"


def test_redactional_codes_of_both_formats_are_accepted(tmp_path: Path) -> None:
    for code in ("047U0001", "003G0218", "18G00011", "25G00192"):
        assert connector_module._CODE.match(code)  # noqa: SLF001
    assert not connector_module._CODE.match("18G0001")  # noqa: SLF001
    assert version_label(b"<x>eli/id/2018/01/22/18G00011/CONSOLIDATED/20260812</x>") == "20260812"
    assert parser_module._ELI.match("eli/id/2018/01/22/18G00011/ORIGINAL")  # noqa: SLF001


def test_the_committed_manifest_lists_the_codes_page_and_passes_validation(tmp_path: Path) -> None:
    path = Path(__file__).parents[1] / "config" / "manifests" / "it-normattiva.yaml"
    manifest = NormattivaConnector(path, "it-normattiva", tmp_path)._manifest()  # noqa: SLF001
    works = manifest["works"]
    codes = [w["code"] for w in works]
    assert len(codes) == len(set(codes)) and len(works) >= 40
    # The Constitution, the pilot acts and the codes of the page, in both redactional-code formats.
    assert {"047U0001", "042U0262", "030U1398", "040U1443", "088G0492", "18G00011", "25G00192"} <= set(codes)
    assert works[0]["code"] == "047U0001" and len(works[0]["vigenza"]) > 20
    assert all(w["urn"].startswith("urn:nir:stato:") for w in works)  # ministerial decrees are not on the list


def test_the_listing_of_the_codes_page_is_read_from_its_links() -> None:
    from scripts.build_normattiva_manifest import describe, listing

    page = (
        '<a href="/uri-res/N2Ls?urn:nir:stato:regio.decreto:1942-03-16;262" class="x">\n  Codice civile </a>'
        '<a href="https://www.normattiva.it/uri-res/N2Ls?urn:nir:stato:decreto.legislativo:2003-06-30;196">'
        "<span>Codice in materia di protezione dei dati personali</span></a>"
        '<a href="/uri-res/N2Ls?urn:nir:stato:regio.decreto:1942-03-16;262">again</a>'
    )
    acts = listing(page)
    assert [u for u, _ in acts] == [
        "urn:nir:stato:regio.decreto:1942-03-16;262",
        "urn:nir:stato:decreto.legislativo:2003-06-30;196",
    ]
    assert describe(*acts[0]) == "Codice civile (R.D. 16 marzo 1942, n. 262)"
    assert describe(*acts[1]).endswith("(d.lgs. 30 giugno 2003, n. 196)")


@pytest.mark.db
def test_flat_codes_ingest_search_and_cite(
    session: Session, catalog: Catalog, store: LocalObjectStore, it_ingested: IngestionReport
) -> None:
    # Ingested in the test's own session, which is rolled back afterwards.
    docs = [
        _raw(code, version, _fixture(parts), f"urn:nir:stato:regio.decreto:{code}")
        for parts in (DISP_ATT, MILITARY, GIUSTIZIA_CONTABILE, AMBIENTE)
        for _, code, version in [parts]
    ]
    report = Ingestor(session, catalog, store).ingest_source("it-normattiva", actor="pytest", connector=_Docs(docs))
    assert report.errors == [] and report.conflicts == [] and report.instruments_created == 4
    hit = _search(session, catalog, q="Il giudice regolarmente adito secondo le leggi anteriori")["results"][0]
    assert hit["citation"] == "art. 230 disp. att. c.p.c."
    hit = _search(session, catalog, q="Per i condannati a una pena detentiva il tempo trascorso in speciali reparti")[
        "results"
    ][0]
    assert hit["citation"] == "art. 33 c.p.m.g."
    hit = _search(session, catalog, q="Terminata l'udienza di discussione la decisione e' deliberata")["results"][0]
    assert hit["citation"].endswith("d.lgs. 174/2016")
    ambiente = session.scalars(
        select(m.Provision.uri).where(m.Provision.uri.like("/it/decreto.legislativo/2006/152/~art_298-bis%"))
    ).all()
    assert any(u.endswith("~art_298-bis--2") for u in ambiente)

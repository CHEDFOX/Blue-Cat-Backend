"""Constitution documents: connector and parser ``constitution_pages_b``, module ``constitution_b``, gates."""

from __future__ import annotations

import gzip
import json
import re
from collections.abc import Iterator
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
import yaml
from sqlalchemy.orm import Session, sessionmaker

from ulis.catalog import Catalog
from ulis.ingestion.pipeline import CONNECTORS, PARSERS, IngestionReport, Ingestor, build_connector
from ulis.jurisdictions.modules import ProvisionCitationInput, get_module
from ulis.retrieval.lookup import provision_at
from ulis.retrieval.search import SearchRequest, search
from ulis.settings import REPO_ROOT
from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.constitution_pages_b import (
    ConstitutionPagesBConnector,
    RobotsPolicy,
    load_manifest,
)
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.polite_http import PoliteHttp
from ulis.sources.normalized import ParsedDocument, ParsedProvision, ParseSkipped
from ulis.sources.parsers.constitution_pages_b import (
    BUNDLE_SCHEMA,
    ConstitutionParseError,
    article_heading,
    bundle_bytes,
    html_source,
    parse_constitution_pages_b,
    read_bundle,
    text_source,
)
from ulis.sources.registry import LicenceNotApproved, licence_gate
from ulis.storage.object_store import LocalObjectStore
from ulis.verification.integrity import verify_source_document

FIX = REPO_ROOT / "tests" / "fixtures" / "constitution_pages_b"
MANIFESTS = REPO_ROOT / "config" / "manifests"
RETRIEVED = "2026-10-15T09:00:00+00:00"
TODAY = date(2026, 10, 15)

# (source id, manifest, document id, fixture file, format, content type)
CASES = {
    "cr": ("cr-tse-constitution", "cr-tse-constitution-es", "cr-tse-constitucion-politica.pages.pdf", "pdf"),
    "by": ("by-president-constitution", "by-president-constitution-ru", "by-president-constitution.html.gz", "html"),
    "mx": ("mx-diputados-constitution", "mx-diputados-constitution-es", "mx-diputados-cpeum.pages.txt", "text"),
    "befr": ("be-dekamer-constitution", "be-dekamer-constitution-fr", "be-dekamer-constitution-fr.pages.txt", "text"),
    "benl": ("be-dekamer-constitution", "be-dekamer-constitution-nl", "be-dekamer-constitution-nl.pages.txt", "text"),
}


def _manifest_doc(source_id: str, doc_id: str) -> dict[str, Any]:
    manifest = yaml.safe_load((MANIFESTS / f"{source_id}.yaml").read_text(encoding="utf-8"))
    return next(d for d in manifest["documents"] if d["id"] == doc_id)


def _fixture_bytes(name: str) -> bytes:
    data = (FIX / name).read_bytes()
    return gzip.decompress(data) if name.endswith(".gz") else data


def _bundle(key: str) -> bytes:
    """The raw document the connector would yield for a fixture, with the hints of the committed manifest."""
    source_id, doc_id, fixture, fmt = CASES[key]
    doc = _manifest_doc(source_id, doc_id)
    return bundle_bytes(
        document_id=doc_id,
        country=doc["country"],
        language=doc["language"],
        title=doc["title"],
        url=doc["url"],
        fmt=fmt,
        retrieved_on="2026-10-15",
        content_type=None,
        data=_fixture_bytes(fixture),
        status_note=doc.get("status_note"),
        hints=doc.get("parser_hints"),
    )


_PARSED: dict[str, ParsedDocument] = {}


def _doc(key: str) -> ParsedDocument:
    if key not in _PARSED:
        docs = parse_constitution_pages_b(_bundle(key))
        assert len(docs) == 1
        _PARSED[key] = docs[0]
    return _PARSED[key]


def _prov(doc: ParsedDocument, eid: str) -> ParsedProvision:
    return next(p for p in doc.provisions if p.eid == eid)


def _cite(doc: ParsedDocument, eid: str) -> str:
    p = _prov(doc, eid)
    module = get_module(doc.ext_module or "default")
    return module.provision_citation(
        ProvisionCitationInput(
            instrument_title=doc.title,
            instrument_type=doc.instrument_type,
            jurisdiction_id=doc.jurisdiction_id,
            top_kind=p.top_kind,
            num_path=p.num_path,
            language=doc.language,
            ext=doc.ext,
            eid=p.eid,
        )
    )


# --------------------------------------------------------------------------- robots policy


def test_robots_policy_longest_match_and_allow() -> None:
    policy = RobotsPolicy("User-agent: *\nDisallow: /docs/\nAllow: /docs/public/\nDisallow: /*.tmp$\n")
    assert policy.allowed("/index.html")
    assert not policy.allowed("/docs/a.pdf")
    assert policy.allowed("/docs/public/a.pdf")  # the longer Allow wins
    assert not policy.allowed("/x/y.tmp")
    assert policy.allowed("/x/y.tmp2")  # "$" anchors the end
    assert not policy.closes_site


def test_robots_policy_picks_our_group_before_the_wildcard_group() -> None:
    text = "User-agent: ulis-research-bot\nDisallow: /private\nCrawl-delay: 12\n\nUser-agent: *\nDisallow: /\n"
    policy = RobotsPolicy(text)
    assert policy.allowed("/public") and not policy.allowed("/private/x")
    assert policy.crawl_delay == 12 and not policy.closes_site
    assert RobotsPolicy("User-agent: *\nDisallow: /\n").closes_site
    assert RobotsPolicy("User-agent: *\nDisallow:\n").allowed("/anything")  # an empty Disallow allows everything
    assert RobotsPolicy("").allowed("/anything")


def test_robots_policy_of_the_real_hosts() -> None:
    cr = RobotsPolicy(
        "User-agent: *\nDisallow: /cgi-bin/ ejemplos de bloqueados.\nDisallow: /images/\n"
        "Disallow: /tmp/\nDisallow: /private/\n"
    )
    assert cr.allowed("/pdf/normativa/constitucion.pdf") and not cr.allowed("/images/logo.png")
    by = RobotsPolicy("User-agent: *\nAllow: /\nDisallow: /*/search*\nDisallow: /*?openVideo=true\n")
    assert by.allowed("/ru/gosudarstvo/constitution") and not by.allowed("/ru/search?q=x")
    cy = RobotsPolicy("User-agent: *\nDisallow: /images/media/redirectfile/\nDisallow: /cgi-bin/\n")
    assert not cy.allowed("/images/media/redirectfile/2-SYNTAGMA.pdf")


# --------------------------------------------------------------------------- bundle


def test_bundle_round_trip_and_foreign_input() -> None:
    raw = bundle_bytes(
        document_id="xx-test-doc", country="XX", language="en", title="T", url="https://x.example/c", fmt="text",
        retrieved_on="2026-10-15", content_type="text/plain", data=b"Article 1\nText.",
    )  # fmt: skip
    bundle = read_bundle(raw)
    assert bundle["schema"] == BUNDLE_SCHEMA and bundle["data"] == b"Article 1\nText."
    with pytest.raises(ConstitutionParseError, match="not a constitution bundle"):
        read_bundle(b"<html></html>")
    with pytest.raises(ConstitutionParseError, match="unexpected schema"):
        read_bundle(json.dumps({"schema": "other/1"}).encode())
    with pytest.raises(ConstitutionParseError, match="unknown parser hints"):
        read_bundle(json.dumps({**json.loads(raw), "hints": {"nonsense": 1}}).encode())
    with pytest.raises(ConstitutionParseError, match="not base64"):
        read_bundle(json.dumps({**json.loads(raw), "data_b64": "***"}).encode())
    with pytest.raises(ConstitutionParseError, match="pdftotext failed|no text layer"):
        parse_constitution_pages_b(
            bundle_bytes(
                document_id="xx-test-doc",
                country="XX",
                language="en",
                title="T",
                url="https://x.example/c",
                fmt="pdf",
                retrieved_on="2026-10-15",
                content_type=None,
                data=b"%PDF-1.4\n%%EOF",
            )  # fmt: skip
        )


# --------------------------------------------------------------------------- headings


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("Artículo 1- Costa Rica es una República democrática.", ("1", "Costa Rica es una República democrática.")),
        ("ARTICULO 12. Honduras es un Estado", ("12", "Honduras es un Estado")),
        (
            "Artículo 1o. En los Estados Unidos Mexicanos todas las personas",
            ("1", "En los Estados Unidos Mexicanos todas las personas"),
        ),
        ("Article 1er", ("1", "")),
        ("Art. 11bis", ("11bis", "")),
        ("Art. 7 bis", ("7 bis", "")),
        ("Artikel 198", ("198", "")),
        ("Статья 116¹.", ("116¹", "")),
        ("Артыкул 17.", ("17", "")),
        ("NENI 12", ("12", "")),
        ("1 straipsnis", ("1", "")),
        ("12-modda. O‘zbekiston — suveren davlat.", ("12", "O‘zbekiston — suveren davlat.")),
        ("Member 12", None),
        ("Artículo 12 de esta Constitución establece", None),  # a reference at the start of a wrapped line
        ("artículo 36. Esta suspensión durará un año", None),  # lower case: a reference
        ("article 77 (6 janvier 2014 – MB 31 janvier 2014)", None),
        ("§ 1er. L'enseignement est libre", None),  # a paragraph sign is not an article heading by default
    ],
)
def test_article_heading_rules(line: str, expected: tuple[str, str] | None) -> None:
    assert article_heading(line, None) == expected


def test_html_text_keeps_superscripts_and_skips_scripts() -> None:
    html = (
        b"<html><head><title>x</title><script>var a=1;</script></head>"
        b"<body><p>Article 116<sup>1</sup>.</p><p>Text</p></body></html>"
    )
    assert html_source(html, "text/html; charset=utf-8").lines == ["Article 116¹.", "Text"]
    assert html_source("<p>caf\xe9</p>".encode("latin-1"), "text/html; charset=iso-8859-1").lines == ["caf\xe9"]


def test_text_with_form_feeds_drops_running_headers_and_page_numbers() -> None:
    pages = [
        "HEADER\nArt. 1\nText one.\n1",
        "HEADER\nArt. 2\nText two.\n2",
        "HEADER\nArt. 3\nText three.\n3",
        "HEADER\nArt. 4\nText four.\n4",
    ]
    source = text_source("\f".join(pages).encode(), None)
    assert "HEADER" not in source.lines and "1" not in source.lines and source.furniture_removed >= 8


# ----------------------------------------------------------------------- Costa Rica: PDF, notes, transitional series


def test_costa_rica_pdf_fixture_structure_and_exact_text() -> None:
    doc = _doc("cr")
    assert doc.jurisdiction_id == "CR" and doc.language == "es" and doc.instrument_type == "constitution"
    assert doc.work_uri == "/cr/constitution" and doc.expression_uri == "/cr/constitution/es@2026-10-15"
    assert doc.validity_basis == "attested_at_edition" and doc.authoritative is False
    assert doc.work_date == date(1949, 11, 7) and doc.ext["numbering"] in ("articles", "mixed")
    eids = [p.eid for p in doc.provisions]
    assert eids[:5] == ["art-8", "art-9", "art-10", "art-11", "art-12"]
    assert "art-197" in eids and "art-t10" in eids and "art-t50-xx" in eids
    art11 = _prov(doc, "art-11")
    assert art11.text.startswith("Los funcionarios públicos son simples depositarios de la autoridad.")
    assert art11.note and art11.note.startswith("Nota: Reformado el artículo 11")
    everything = "\n".join(p.text for p in doc.provisions)
    assert (
        "Nota:" not in everything and "Notas:" not in everything and "www.tse.go.cr" not in everything
    )  # notes and running header
    assert "\nNormativa\n" not in everything and "Normativa\n" not in everything[:20]
    assert _prov(doc, "art-9").note and _prov(doc, "art-9").note.startswith(
        "Notas: Adicionado el último párrafo del artículo 9"
    )
    art197 = _prov(doc, "art-197")
    assert art197.text == (
        "Esta Constitución entrará en plena vigencia el ocho de noviembre de 1949, y deroga las anteriores. "
        "Se mantiene en vigor el ordenamiento jurídico existente, mientras no sea modificado o derogado por los "
        "órganos competentes del Poder Público, o no quede derogado expresa o implícitamente "
        "por la presente Constitución."
    )
    assert art197.context_heading and art197.context_heading.startswith("TÍTULO XVIII")
    transitional = _prov(doc, "art-t10")
    assert transitional.num_path == ["T10"] and transitional.text.startswith("La Sala que se crea en el artículo 10")
    assert _cite(doc, "art-197") == "Costa Rica Constitution art. 197"
    assert _cite(doc, "art-t10") == "Costa Rica Constitution art. T10"


# ------------------------------------------------------------------- Belarus: HTML, two scripts, superscript numbers


def test_belarus_html_fixture_preamble_articles_and_superscripts() -> None:
    doc = _doc("by")
    assert doc.language == "ru" and len(doc.provisions) == 159 and doc.ext["numbering"] == "articles"
    assert doc.provisions[0].eid == "preamble" and doc.provisions[0].text.startswith(
        "Мы, народ Республики Беларусь (Беларуси),"
    )
    assert (
        _prov(doc, "art-17").text
        == "Государственными языками в Республике Беларусь являются белорусский и русский языки."
    )
    assert _prov(doc, "art-116-1").num_path == ["116¹"]  # the page prints "Статья 116" with a superscript 1
    assert (
        _prov(doc, "art-101").text == "Исключена."
    )  # a repealed article keeps its number and the portal's placeholder
    assert not any("©" in p.text or "Обычная версия" in p.text for p in doc.provisions)  # footer and navigation are cut
    assert "27 февраля 2022 года состоялся" in doc.ext["edition_note"]
    assert _cite(doc, "art-116-1") == "Belarus Constitution art. 116¹"
    assert _cite(doc, "preamble") == "Belarus Constitution preamble"


# --------------------------------------------------------------------------- Mexico: annotations are notes, not text


def test_mexico_annotations_and_ordinals() -> None:
    doc = _doc("mx")
    assert [p.eid for p in doc.provisions][:2] == ["art-1", "art-2"]  # "Artículo 1o." is article 1
    assert "art-136" in {p.eid for p in doc.provisions}
    art1 = _prov(doc, "art-1")
    assert art1.text.startswith("En los Estados Unidos Mexicanos todas las personas gozarán de los derechos humanos")
    assert "DOF" not in art1.text and art1.note and "Párrafo reformado DOF 10-06-2011" in art1.note
    assert art1.context_heading == "Título Primero › Capítulo I — De los Derechos Humanos y sus Garantías"
    assert doc.ext["edition_note"].startswith("Última reforma publicada DOF")
    assert doc.work_date == date(1917, 2, 5)
    # nothing after the "Artículos Transitorios" heading is ingested
    assert all("Primer Jefe" not in p.text for p in doc.provisions)
    assert _cite(doc, "art-1") == "Mexico Constitution art. 1"


# --------------------------------------------------------------------------- Belgium: French and Dutch, same labels


def test_belgium_french_and_dutch_share_labels() -> None:
    fr, nl = _doc("befr"), _doc("benl")
    assert fr.language == "fr" and nl.language == "nl" and fr.work_uri == nl.work_uri == "/be/constitution"
    fr_art = {p.eid: p for p in fr.provisions if p.kind == "article"}
    nl_art = {p.eid: p for p in nl.provisions if p.kind == "article"}
    assert {"art-1", "art-2", "art-3", "art-198"} <= set(fr_art) and set(fr_art) == set(nl_art)
    assert fr_art["art-1"].text == "La Belgique est un État fédéral qui se compose des communautés et des régions."
    assert nl_art["art-1"].text.startswith("België is een federale Staat")
    assert fr_art["art-1"].context_heading and fr_art["art-1"].context_heading.startswith("TITRE PREMIER")
    assert fr.ext["edition_note"] == "mars 2021" and nl.ext["edition_note"] == "maart 2021"
    # the unnumbered transitional provisions after Titre IX are stored as blocks, not glued to article 198
    assert "ENTRÉE EN VIGUEUR" in " ".join(p.heading or "" for p in fr.provisions if p.kind == "block")
    assert "TITRE IX" not in fr_art["art-198"].text
    assert _cite(fr, "art-198") == "Belgium Constitution art. 198"


# --------------------------------------------------------------------------- blocks fallback


def test_text_without_recognisable_articles_becomes_blocks() -> None:
    text = "\n".join(f"Paragraph {i} of an unstructured text that has no numbered headings at all." for i in range(60))
    raw = bundle_bytes(
        document_id="xx-blocks-doc", country="XX", language="en", title="Basic Law", url="https://x.example/law",
        fmt="text", retrieved_on="2026-10-15", content_type="text/plain", data=text.encode(),
        hints={"country_name": "Xland"},
    )  # fmt: skip
    doc = parse_constitution_pages_b(raw)[0]
    assert doc.ext["numbering"] == "blocks" and len(doc.provisions) >= 2
    assert all(p.kind == "block" and p.top_kind == "block" for p in doc.provisions)
    assert "Paragraph 59" in doc.provisions[-1].text
    assert _cite(doc, doc.provisions[0].eid) == "Xland Constitution text block 1"
    with pytest.raises(ParseSkipped):
        parse_constitution_pages_b(
            bundle_bytes(
                document_id="xx-empty-doc",
                country="XX",
                language="en",
                title="T",
                url="https://x.example/c",
                fmt="text",
                retrieved_on="2026-10-15",
                content_type=None,
                data=b"\n\n",
                hints={},
            )  # fmt: skip
        )


# --------------------------------------------------------------------------- connector (offline, from a cache)


def _write(client: PoliteHttp, url: str, data: bytes, status: int = 200, cache_key: str | None = None) -> None:
    path = client.cache_path(url, cache_key)
    meta = {
        "url": url,
        "final_url": url,
        "status": status,
        "content_type": "application/pdf",
        "retrieved_at": RETRIEVED,
    }
    PoliteHttp._store(path, path.with_name(path.name + ".meta.json"), data, meta)


def _manifest(tmp_path: Path, documents: list[dict[str, Any]], hosts: list[str] | None = None) -> Path:
    path = tmp_path / "manifest.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "source_id": "xx-test",
                "user_agent": "ULIS-research-bot/0.1 (+https://github.com/CHEDFOX/Blue-Cat-Backend)",
                "crawl_delay_seconds": 5,
                "hosts": hosts or ["www.example.org"],
                "documents": documents,
            }
        ),
        encoding="utf-8",
    )
    return path


def _doc_entry(doc_id: str = "xx-const-en", path: str = "/c.txt", fmt: str = "text") -> dict[str, Any]:
    return {
        "id": doc_id, "country": "XX", "language": "en", "url": f"https://www.example.org{path}", "format": fmt,
        "title": "Constitution of Xland", "parser_hints": {"country_name": "Xland"},
    }  # fmt: skip


def _client(tmp_path: Path) -> PoliteHttp:
    return PoliteHttp(
        tmp_path / "cache",
        "ULIS-research-bot/0.1 (+https://x.example)",
        allowed_hosts={"www.example.org"},
        offline=True,
    )


def test_connector_offline_yields_a_bundle_the_parser_reads(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _write(client, "https://www.example.org/robots.txt", b"User-agent: *\nDisallow: /private/\n")
    body = (
        b"Article 1\nXland is a republic.\nArticle 2\nPower belongs to the people.\nArticle 3\nThe capital is Xcity.\n"
    )
    _write(client, "https://www.example.org/c.txt", body, cache_key="doc/xx-const-en")
    connector = ConstitutionPagesBConnector(
        _manifest(tmp_path, [_doc_entry()]), "xx-test", tmp_path / "cache", offline=True
    )
    raws = list(connector.fetch())
    assert connector.fetch_errors == [] and [r.external_id for r in raws] == ["xx-const-en"]
    assert raws[0].media_type == "application/json" and raws[0].retrieved_at == datetime.fromisoformat(RETRIEVED)
    bundle = read_bundle(raws[0].content)
    assert bundle["data"] == body and bundle["retrieved_on"] == "2026-10-15" and bundle["country"] == "XX"
    doc = parse_constitution_pages_b(raws[0].content)[0]
    assert [p.eid for p in doc.provisions] == ["art-1", "art-2", "art-3"]
    assert _prov(doc, "art-2").text == "Power belongs to the people."


def test_connector_reports_disallowed_missing_and_non_pdf_documents_and_goes_on(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _write(client, "https://www.example.org/robots.txt", b"User-agent: *\nDisallow: /private/\n")
    _write(
        client,
        "https://www.example.org/ok.txt",
        b"Article 1\nA.\nArticle 2\nB.\nArticle 3\nC.\n",
        cache_key="doc/xx-ok-doc",
    )
    _write(client, "https://www.example.org/missing.txt", b"", status=404, cache_key="doc/xx-missing")
    _write(client, "https://www.example.org/fake.pdf", b"<html>not a pdf</html>", cache_key="doc/xx-fake-pdf")
    entries = [
        _doc_entry("xx-private-doc", "/private/p.txt"),
        _doc_entry("xx-missing", "/missing.txt"),
        _doc_entry("xx-fake-pdf", "/fake.pdf", "pdf"),
        _doc_entry("xx-ok-doc", "/ok.txt"),
    ]
    connector = ConstitutionPagesBConnector(_manifest(tmp_path, entries), "xx-test", tmp_path / "cache", offline=True)
    raws = list(connector.fetch())
    assert [r.external_id for r in raws] == ["xx-ok-doc"]
    errors = " | ".join(connector.fetch_errors)
    assert "disallowed by robots.txt" in errors and "not found (404)" in errors and "not a PDF file" in errors


def test_connector_never_fetches_from_a_host_whose_robots_close_it(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _write(client, "https://www.example.org/robots.txt", b"User-agent: *\nDisallow: /\n")
    connector = ConstitutionPagesBConnector(
        _manifest(tmp_path, [_doc_entry()]), "xx-test", tmp_path / "cache", offline=True
    )
    assert list(connector.fetch()) == []
    assert connector.fetch_errors and "disallows this crawler" in connector.fetch_errors[0]
    assert connector.requests_made == 0


def test_connector_treats_an_unreadable_robots_file_as_a_refusal(tmp_path: Path) -> None:
    connector = ConstitutionPagesBConnector(
        _manifest(tmp_path, [_doc_entry()]), "xx-test", tmp_path / "cache", offline=True
    )
    assert (
        list(connector.fetch()) == []
    )  # offline and robots.txt not cached: the rules cannot be read, nothing is fetched
    assert connector.fetch_errors and "could not be read" in connector.fetch_errors[0]


def test_connector_refuses_bad_manifests(tmp_path: Path) -> None:
    good = _doc_entry()
    cases = [
        ([{**good, "url": "http://www.example.org/c.txt"}], "https"),
        ([{**good, "url": "https://evil.example.net/c.txt"}], "https"),
        ([{**good, "format": "docx"}], "format"),
        ([{**good, "country": "Xland"}], "country"),
        ([{**good, "id": "X"}], "invalid document id"),
        ([{**good, "title": ""}], "title"),
        ([{**good, "extra": 1}], "unknown keys"),
        ([{**good, "parser_hints": {"nonsense": 1}}], "unknown parser hints"),
        ([good, good], "listed twice"),
        ([], "no documents"),
    ]
    for entries, message in cases:
        with pytest.raises(ManifestError, match=message):
            load_manifest(_manifest(tmp_path, entries), "xx-test")
    path = _manifest(tmp_path, [good])
    with pytest.raises(ManifestError, match="mismatch"):
        load_manifest(path, "other-source")
    data = yaml.safe_load(path.read_text())
    data["crawl_delay_seconds"] = 1
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(ManifestError, match="crawl_delay_seconds"):
        load_manifest(path, "xx-test")


# --------------------------------------------------------------------------- committed manifests, sources, gates


def _constitution_b_sources(catalog: Catalog) -> list[Any]:
    return [s for s in catalog.sources if s.connector == "constitution_pages_b"]


def test_committed_manifests_load_and_name_only_their_own_hosts(catalog: Catalog) -> None:
    sources = _constitution_b_sources(catalog)
    assert {s.id for s in sources} == {"cr-tse-constitution", "mx-diputados-constitution", "by-president-constitution"}
    for source in sources:
        assert source.parser == "constitution_pages_b" and source.status == "active"
        assert source.licence.review_status == "approved" and source.licence.reviewed_on == date(2026, 10, 15)
        manifest = load_manifest(REPO_ROOT / source.access.endpoint, source.id)
        assert manifest["crawl_delay_seconds"] >= 5 and "ULIS-research-bot" in manifest["user_agent"]
        for doc in manifest["documents"]:
            assert urlsplit(doc["url"]).netloc in manifest["hosts"] and doc["country"] in source.jurisdictions
            assert doc["language"] in source.languages
        assert isinstance(build_connector(source, REPO_ROOT, Path("/nonexistent/cache")), ConstitutionPagesBConnector)
    # the manifest prepared for the planned Belgian source loads too
    be = load_manifest(MANIFESTS / "be-dekamer-constitution.yaml", "be-dekamer-constitution")
    assert [d["language"] for d in be["documents"]] == ["fr", "nl"]


def test_registrations() -> None:
    assert "constitution_pages_b" in CONNECTORS and "constitution_pages_b" in PARSERS


def _findings_ids() -> list[str]:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "findings_b", REPO_ROOT / "scripts" / "constitution_findings_b_data.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return [e["id"] for e in module.PLANNED]


def test_every_other_publisher_of_the_sweep_is_recorded_and_refused_by_the_gate(catalog: Catalog) -> None:
    ids = _findings_ids()
    assert len(ids) == len(set(ids)) >= 60
    statuses = {"planned": 0, "rejected": 0}
    for source_id in ids:
        source = catalog.sources.get(source_id)
        if source.status == "active":  # promoted by the deep sweep (docs/constitutions-europe-me-deep.md)
            continue
        statuses[source.status] += 1
        assert source.connector is None and source.parser is None
        assert source.licence.reviewed_on == date(2026, 10, 15) and "robots" in (source.licence.reviewed_by or "")
        assert source.document_kinds == ["constitution"]
        with pytest.raises(LicenceNotApproved) as exc:
            licence_gate(source)
        assert f"status is {source.status!r}" in str(exc.value)
    assert statuses["rejected"] == 2 and statuses["planned"] >= 20
    for hostile in ("dm-government-constitution", "pt-tribunal-constitucional-constitution"):
        assert catalog.sources.get(hostile).status == "rejected"
        assert catalog.sources.get(hostile).licence.bulk_download == "not_allowed"


def test_the_findings_document_lists_every_source_and_country() -> None:
    text = (REPO_ROOT / "docs" / "constitutions-americas-europe.md").read_text(encoding="utf-8")
    for source_id in _findings_ids() + [
        "cr-tse-constitution",
        "mx-diputados-constitution",
        "by-president-constitution",
    ]:
        assert f"`{source_id}`" in text
    for country in (
        "Costa Rica",
        "Mexico",
        "Belarus",
        "Norway",
        "Kazakhstan",
        "Austria",
        "Belgium",
        "Uzbekistan",
        "Peru",
        "Cuba",
    ):
        assert f"| {country} |" in text
    assert text.count("\n| ") > 70


def test_fixtures_readme_names_every_fixture() -> None:
    readme = (FIX / "README.md").read_text(encoding="utf-8")
    for path in FIX.iterdir():
        if path.name != "README.md":
            assert path.name in readme
    assert "licence" in readme.lower()


def test_active_constitution_profiles_use_the_module_and_the_sources() -> None:
    catalog = Catalog.load(REPO_ROOT / "config")
    for cc, source in (
        ("CR", "cr-tse-constitution"),
        ("MX", "mx-diputados-constitution"),
        ("BY", "by-president-constitution"),
    ):
        profile = catalog.jurisdictions.get(cc)
        assert profile.citation.module == "constitution_b" and profile.publication.official_sources == [source]
        assert profile.verification.status == "draft_unverified" and profile.coverage.corpus_status == "pilot"
        assert profile.instrument_types == ["constitution"]
        assert re.search(r"Constitution art\. ", " ".join(profile.citation.examples))


# --------------------------------------------------------------------------- database


class _Fixtures:
    """The documents of one source as the connector would yield them (the fixture bundles)."""

    def __init__(self, *keys: str):
        self.keys = keys
        self.fetch_errors: list[str] = []

    def fetch(self) -> Iterator[RawDocument]:
        for key in self.keys:
            _, doc_id, _, _ = CASES[key]
            doc = _manifest_doc(CASES[key][0], doc_id)
            yield RawDocument(
                doc_id,
                doc["url"],
                "application/json",
                _bundle(key),
                datetime.fromisoformat(RETRIEVED),
                {"fixture": key},
            )


@pytest.fixture(scope="module")
def constitutions_ingested(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, ingested: IngestionReport
) -> dict[str, IngestionReport]:
    reports: dict[str, IngestionReport] = {}
    with session_factory() as session:
        for source_id, keys in (
            ("cr-tse-constitution", ("cr",)),
            ("mx-diputados-constitution", ("mx",)),
            ("by-president-constitution", ("by",)),
        ):
            reports[source_id] = Ingestor(session, catalog, store).ingest_source(
                source_id, actor="pytest", connector=_Fixtures(*keys)
            )
        session.commit()
    return reports


def _search(session: Session, catalog: Catalog, jurisdiction: str, **kw: Any) -> dict[str, Any]:
    return search(session, catalog, SearchRequest(jurisdiction=jurisdiction, **kw), today=TODAY)


@pytest.mark.db
def test_ingest_reports(constitutions_ingested: dict[str, IngestionReport]) -> None:
    for source_id, report in constitutions_ingested.items():
        assert report.errors == [] and report.conflicts == [], source_id
        assert report.documents_new == 1 and report.instruments_created == 1 and report.instrument_versions_created == 1
    assert constitutions_ingested["cr-tse-constitution"].provision_versions_created == len(_doc("cr").provisions)
    assert constitutions_ingested["by-president-constitution"].provision_versions_created == 159


@pytest.mark.db
def test_search_finds_exact_text_with_citation_and_limitations(
    session: Session, catalog: Catalog, constitutions_ingested: dict[str, IngestionReport]
) -> None:
    res = _search(
        session, catalog, "CR", q="Tribunal Supremo de Elecciones rango e independencia de los Poderes del Estado"
    )
    assert res["results"][0]["citation"] == "Costa Rica Constitution art. 9"
    res = _search(session, catalog, "CR", q="Esta Constitución entrará en plena vigencia el ocho de noviembre de 1949")
    hit = res["results"][0]
    assert hit["citation"] == "Costa Rica Constitution art. 197"
    assert hit["text"].startswith(
        "Esta Constitución entrará en plena vigencia el ocho de noviembre de 1949, y deroga las anteriores."
    )
    assert hit["jurisdiction"]["id"] == "CR" and all(h["jurisdiction"]["id"] == "CR" for h in res["results"])
    codes = {lim["code"] for lim in hit["limitations"]}
    assert {
        "PROFILE_UNVERIFIED",
        "ATTESTED_ONLY_AT",
        "SOURCE_NOT_AUTHENTIC",
        "OFFICIAL_CONSOLIDATION_REPRODUCED",
    } <= codes
    assert hit["source"]["attribution"].startswith("Fuente: Tribunal Supremo de Elecciones de Costa Rica")
    assert "{retrieved_date}" not in hit["source"]["attribution"]
    mx = _search(session, catalog, "MX", q="serán la Ley Suprema de toda la Unión tratados")
    assert mx["results"][0]["citation"] == "Mexico Constitution art. 133"
    by = _search(session, catalog, "BY", q="Государственными языками являются белорусский и русский языки")
    assert by["results"][0]["citation"] == "Belarus Constitution art. 17"
    assert by["results"][0]["source"]["attribution"].startswith("Источник: Официальный интернет-портал Президента")


@pytest.mark.db
def test_point_in_time_is_attested_only_at_the_retrieval_date(
    session: Session, catalog: Catalog, constitutions_ingested: dict[str, IngestionReport]
) -> None:
    now = provision_at(session, catalog, "/cr/constitution/~art-197", TODAY)
    assert now["status_on_date"] == "in_force_date_unverified" and now["version"]["currency_date"] == "2026-10-15"
    assert now["provision"]["citation"] == "Costa Rica Constitution art. 197"
    earlier = provision_at(session, catalog, "/cr/constitution/~art-197", date(1990, 1, 1))
    assert (
        earlier["status_on_date"] == "in_force_date_unverified"
    )  # only today's wording is published: not "unknown", not "in force"
    assert "ATTESTED_ONLY_AT" in {lim["code"] for lim in earlier["limitations"]}


@pytest.mark.db
def test_raw_documents_verify_against_the_stored_text(
    session: Session, catalog: Catalog, store: LocalObjectStore, constitutions_ingested: dict[str, IngestionReport]
) -> None:
    from sqlalchemy import select

    from ulis.db import models as m

    ids = session.scalars(select(m.SourceDocument.id).where(m.SourceDocument.source_id == "cr-tse-constitution")).all()
    assert ids
    for document_id in ids:
        result = verify_source_document(session, catalog, store, document_id, checker="pytest")
        assert result["result"] == "pass", result

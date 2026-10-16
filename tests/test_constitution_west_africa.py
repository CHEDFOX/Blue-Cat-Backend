"""Constitutions of North, West and Central Africa (deep sweep): the committed sources, manifests and profiles, the
parser additions made for them (French ``Article 1er``, PDF ``columns``), real fixtures per publisher, and the records of
the publishers that were refused.

Fixtures are real: the text lines of pages of the publishers' PDFs and HTML pages (``pdftotext`` / the page's rendered
text), written back into small PDFs made by ``tests/constitution_pdf.py`` (the built-in font has no glyph for a bullet or a
Unicode hyphen: those two characters in the page furniture were replaced by ``-``). The manifest's own structure hints are
the ones the tests use, so a hint that stops working fails here.
"""

# ruff: noqa: E501 - fixtures and quoted publisher words are long
from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from constitution_pdf import make_pdf
from sqlalchemy.orm import Session, sessionmaker
from test_constitution_text import bundle, parse, prov, text_bundle

from ulis.catalog import Catalog
from ulis.ingestion.pipeline import CONNECTORS, PARSERS, IngestionReport, Ingestor, build_connector
from ulis.jurisdictions.modules import ProvisionCitationInput, get_module
from ulis.retrieval.lookup import provision_at
from ulis.retrieval.search import SearchRequest, search
from ulis.settings import REPO_ROOT
from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.constitution_docs import ConstitutionDocsConnector
from ulis.sources.parsers.constitution_text import document_lines
from ulis.sources.parsers.pdf_text import pdf_pages
from ulis.sources.registry import LicenceNotApproved, licence_gate
from ulis.storage.object_store import LocalObjectStore
from ulis.verification.integrity import verify_source_document

FIX = REPO_ROOT / "tests" / "fixtures" / "constitution_west_africa"
RETRIEVED = datetime(2026, 10, 16, 9, 0, tzinfo=UTC)
TODAY = date(2026, 10, 16)

ACTIVE = {
    "bj-courconst-constitution": "BJ",
    "ci-cc-constitution": "CI",
    "mr-primature-constitution": "MR",
    "cv-parlamento-constitution": "CV",
    "dz-joradp-constitution": "DZ",
    "ma-adala-constitution": "MA",
    "ne-gouv-constitution": "NE",
    "ga-gouvernement-constitution": "GA",
    "bf-presidence-constitution": "BF",
    "tg-jo-constitution": "TG",
    "gw-anp-constitution": "GW",
    "cd-medd-constitution": "CD",
    "cm-assnat-constitution": "CM",
    "ml-koulouba-constitution": "ML",
    "cg-gouvernement-constitution": "CG",
    "gq-gobierno-constitution": "GQ",
    "lr-mod-constitution": "LR",
}
REJECTED = {
    "cg-sgg-constitution": "est interdite et constituerait une contrefaçon",
    "cg-presidence-constitution": "Toute exploitation non autorisée du site",
    "ml-sgg-constitution": "est interdite et constituerait une contrefaçon",
    "gn-sgg-jo-constitution": "est interdite et constituerait une contrefaçon",
    "gm-assembly-constitution": "Collect data without permission",
}
PLANNED = {
    "td-presidence-constitution": "HTTP 403",
    "eg-sis-constitution": "certificate verification failed",
    "tn-legislation-constitution": "connection reset",
    "ly-sld-constitution": "damaged Arabic text layer",
    "ng-justice-constitution": "no text layer",
    "gn-coursupreme-constitution": "no text layer",
    "gh-judicial-constitution": "without the article numbers",
    "sl-parliament-constitution": "self-signed certificate",
    "cd-presidence-constitution": "Toute utilisation à des fins commerciales ou publicitaires est formellement interdite",
}


def _manifest(catalog: Catalog, source_id: str) -> dict[str, Any]:
    source = catalog.sources.get(source_id)
    return yaml.safe_load((REPO_ROOT / source.access.endpoint).read_text(encoding="utf-8"))


def _doc(catalog: Catalog, source_id: str, slug: str) -> dict[str, Any]:
    return next(d for d in _manifest(catalog, source_id)["documents"] if d["slug"] == slug)


def _pages(name: str) -> list[list[str]]:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def _pdf_bundle(catalog: Catalog, source_id: str, slug: str, body: bytes) -> bytes:
    d = _doc(catalog, source_id, slug)
    return bundle(
        body,
        "pdf",
        cc=d["jurisdiction"],
        language=d["language"],
        structure=d["structure"],
        title=d["title"],
        citation_name=d["citation_name"],
        adopted_on=d.get("adopted_on"),
        events=d.get("events", []),
    )


def _html_bundle(catalog: Catalog, source_id: str, slug: str, lines: list[str]) -> bytes:
    d = _doc(catalog, source_id, slug)
    return text_bundle(
        lines,
        cc=d["jurisdiction"],
        language=d["language"],
        structure=d["structure"],
        title=d["title"],
        citation_name=d["citation_name"],
        events=d.get("events", []),
    )


def _two_columns(pages: list[dict[str, list[str]]]) -> bytes:
    """A PDF with the left strip of each page printed at x = 40 and the right strip at x = 320, line by line from the top.

    The lines of the two strips are written line by line (left line 1, right line 1, left line 2 ...) so that a reader
    that follows the content stream or the baselines would interleave them; a reader that cuts the page in strips does not."""
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    catalog = add(b"")
    pages_obj = add(b"")
    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    kids = []
    for page in pages:
        content = ["BT", "/F1 8 Tf"]
        left, right = page["left"], page["right"]
        for row in range(max(len(left), len(right))):
            y = 800 - 10 * row
            for x, strip in ((40, left), (320, right)):
                if row < len(strip) and strip[row].strip():
                    text = strip[row].replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
                    content.append(f"1 0 0 1 {x} {y} Tm ({text}) Tj")
        content.append("ET")
        stream = "\n".join(content).encode("cp1252", errors="replace")
        data = add(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        kids.append(
            add(
                f"<< /Type /Page /Parent {pages_obj} 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 {font} 0 R >> >> "
                f"/Contents {data} 0 R >>".encode()
            )
        )
    objects[catalog - 1] = f"<< /Type /Catalog /Pages {pages_obj} 0 R >>".encode()
    objects[pages_obj - 1] = (
        "<< /Type /Pages /Kids [" + " ".join(f"{k} 0 R" for k in kids) + f"] /Count {len(kids)} >>"
    ).encode()
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root {catalog} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


# --------------------------------------------------------------------------- committed sources, manifests, profiles


@pytest.mark.parametrize("source_id", sorted(ACTIVE))
def test_committed_manifest_loads_and_matches_its_source(catalog: Catalog, source_id: str) -> None:
    source = catalog.sources.get(source_id)
    assert (
        source.status == "active" and source.connector == "constitution_docs" and source.parser == "constitution_text"
    )
    assert source.document_kinds == ["constitution"] and source.licence.review_status == "approved"
    licence_gate(source, commercial=False)
    with pytest.raises(LicenceNotApproved, match="commercial"):
        licence_gate(source, commercial=True)
    assert source.licence.commercial_use == "unknown" and source.licence.reviewed_on == date(2026, 10, 16)
    assert source.licence.attribution_required and "{retrieved_date}" in (source.licence.attribution_text or "")
    assert "not counsel" in (source.licence.reviewed_by or "") and "robots.txt" in (source.licence.reviewed_by or "")
    assert isinstance(build_connector(source), ConstitutionDocsConnector)
    manifest = ConstitutionDocsConnector(
        REPO_ROOT / source.access.endpoint, source_id, Path("/nonexistent")
    )._manifest()
    assert "ULIS-research-bot" in manifest["user_agent"] and manifest["crawl_delay_seconds"] >= 5
    assert {d["jurisdiction"] for d in manifest["documents"]} == set(source.jurisdictions) == {ACTIVE[source_id]}
    assert {d["language"] for d in manifest["documents"]} == set(source.languages)
    for d in manifest["documents"]:
        assert d["authoritative"] is False and d["url"].startswith("https://")
        for event in d.get("events", []):
            date.fromisoformat(str(event["date"]))
    profile = catalog.jurisdictions.get(ACTIVE[source_id])
    assert profile.verification.status == "draft_unverified" and profile.instrument_types == ["constitution"]
    assert profile.citation.module == "constitution" and source_id in profile.publication.official_sources
    assert "Only the constitution is loaded" in profile.coverage.known_gaps[0]
    assert profile.parent == "INT" and set(profile.official_languages) >= set(source.languages)


@pytest.mark.parametrize("source_id", sorted(REJECTED) + sorted(PLANNED))
def test_refused_publishers_are_recorded_and_the_licence_gate_refuses_them(catalog: Catalog, source_id: str) -> None:
    source = catalog.sources.get(source_id)
    assert source.status == ("rejected" if source_id in REJECTED else "planned")
    assert source.licence.review_status in ("pending_review", "rejected")
    assert source.licence.reviewed_on == date(2026, 10, 16)
    assert "robots.txt" in (source.licence.reviewed_by or "")
    phrase = REJECTED.get(source_id) or PLANNED[source_id]
    assert phrase in (source.licence.reviewed_by or ""), source_id
    assert source.document_kinds == ["constitution"] and source.connector is None and source.parser is None
    assert source.licence.attribution_text is None
    with pytest.raises(LicenceNotApproved):
        licence_gate(source)
    assert source_id not in CONNECTORS and not any(source_id in name for name in PARSERS)
    if source_id in REJECTED:
        assert source.licence.automated_processing == "not_allowed" and source.licence.bulk_download == "not_allowed"


# --------------------------------------------------------------------------- parser additions


def test_french_ordinal_in_a_heading_is_article_one(catalog: Catalog) -> None:
    doc = parse(_pdf_bundle(catalog, "bj-courconst-constitution", "bj-fr", make_pdf(_pages("bj-fr-pages.json"))))
    ids = [p.eid for p in doc.provisions]
    assert ids[:4] == ["preamble", "art-1", "art-2", "art-3"], ids
    art1 = prov(doc, "art-1")
    assert art1.text.startswith("L'Etat du Bénin est une République indépendante et souveraine")
    assert art1.context_heading == "TITRE PREMIER : DE L'ETAT ET DE LA SOUVERAINETE"
    assert "Article 5 (nouveau)" not in prov(doc, "art-5").text  # the heading line is the heading, not text


def test_portuguese_feminine_ordinal_and_html_pages() -> None:
    lines = [
        "Constituição de República da Guiné-Bissau",
        "O PAIGC, fundado em 19 de Setembro de 1956, cumpriu exemplarmente o seu Programa mínimo.",
        "TÍTULO I",
        "ARTIGO 1.º",
        "A Guiné-Bissau é uma República soberana, democrática, laica e unitária.",
        "ARTIGO 2ª",
        "A soberania nacional reside no povo.",
        "ARTIGO 3.º",
        "O Estado respeita a dignidade da pessoa humana.",
        "ARTIGO 4.º",
        "Todos os cidadãos são iguais perante a lei.",
        "ARTIGO 5ª",
        "O Estado promove a justiça.",
    ]
    doc = parse(
        text_bundle(
            lines, cc="GW", language="pt", structure={"preamble_regex": "^Constituição de República da Guiné-Bissau$"}
        )
    )
    assert [p.eid for p in doc.provisions] == ["preamble", "art-1", "art-2", "art-3", "art-4", "art-5"]
    assert prov(doc, "art-2").text == "A soberania nacional reside no povo."


def test_columns_hint_reads_a_gazette_page_strip_by_strip(catalog: Catalog) -> None:
    pdf = _two_columns(json.loads((FIX / "tg-fr-columns.json").read_text(encoding="utf-8")))
    plain = "\n".join(pdf_pages(pdf))
    columns = "\n".join(pdf_pages(pdf, columns=2))
    # the first line of the right strip of the first page follows the whole left strip, not the first left line
    data = json.loads((FIX / "tg-fr-columns.json").read_text(encoding="utf-8"))
    last_left = [x for x in data[0]["left"] if x.strip()][-1]
    first_right = next(x for x in data[0]["right"] if x.strip())
    assert columns.index(last_left) < columns.index(first_right)
    assert plain.index(first_right) < plain.index(last_left)  # without the hint the strips are read row by row
    doc = parse(_pdf_bundle(catalog, "tg-jo-constitution", "tg-fr", pdf))
    ids = [p.eid for p in doc.provisions if p.parent_eid is None]
    assert ids[0] in ("preamble", "art-1") and "art-1" in ids and "art-2" in ids
    assert prov(doc, "art-1").text.startswith("La République togolaise est un Etat de")
    assert "columns" in _doc(catalog, "tg-jo-constitution", "tg-fr")["structure"]
    lines = document_lines(
        json.loads(_pdf_bundle(catalog, "tg-jo-constitution", "tg-fr", pdf).decode("utf-8")),
    )
    assert any("Article premier" in x for x in lines)


# --------------------------------------------------------------------------- fixtures of the real texts


def test_morocco_algeria_mauritania_pdf_furniture_is_dropped_and_dates_are_the_documents(catalog: Catalog) -> None:
    ma = parse(_pdf_bundle(catalog, "ma-adala-constitution", "ma-fr", make_pdf(_pages("ma-fr-pages.json"))))
    assert prov(ma, "preamble").text.startswith("Fidèle à son choix irréversible de construire un Etat de droit")
    assert "-2-" not in prov(ma, "preamble").text and prov(ma, "art-1").text.startswith("Le Maroc est une monarchie")
    dz = parse(_pdf_bundle(catalog, "dz-joradp-constitution", "dz-fr", make_pdf(_pages("dz-fr-pages.json"))))
    assert prov(dz, "preamble").text.startswith("Le peuple algérien est un peuple libre")
    assert prov(dz, "art-2").text == "L'Islam est la religion de l'Etat"
    assert all("JOURNAL OFFICIEL DE LA REPUBLIQUE ALGERIENNE" not in p.text for p in dz.provisions)
    mr = parse(_pdf_bundle(catalog, "mr-primature-constitution", "mr-fr", make_pdf(_pages("mr-fr-pages.json"))))
    assert prov(mr, "art-1").text.startswith("La Mauritanie est une République Islamique")
    assert prov(mr, "art-2").text.startswith("(nouveau)\nLe peuple est la source de tout pouvoir.")
    assert all("Page " not in p.text.split("\n")[0] for p in mr.provisions)
    assert [e.date for e in ma.events] == [date(2011, 7, 1), date(2011, 7, 29)]
    assert [e.event_type for e in ma.events] == ["adoption", "promulgation"]
    assert dz.validity_basis == "attested_at_edition" and dz.instrument_type == "constitution"


def test_spanish_misprinted_heading_and_english_parts(catalog: Catalog) -> None:
    gq = parse(_pdf_bundle(catalog, "gq-gobierno-constitution", "gq-es", make_pdf(_pages("gq-es-pages.json"))))
    ids = [p.eid for p in gq.provisions if p.parent_eid is None]
    assert "art-8" in ids, ids  # 'Articulo 8º' (no accent) in the PDF is still article 8
    assert "‐" not in "".join(p.text for p in gq.provisions)
    cm = parse(_pdf_bundle(catalog, "cm-assnat-constitution", "cm-en", make_pdf(_pages("cm-en-pages.json"))))
    assert prov(cm, "art-1").context_heading == "PART I The State and Sovereignty"
    assert prov(cm, "art-1").text.startswith("(1) : The United Republic of Cameroon shall")


def test_portuguese_html_pages_keep_the_preamble_and_the_titles(catalog: Catalog) -> None:
    cv = parse(
        _html_bundle(
            catalog,
            "cv-parlamento-constitution",
            "cv-pt",
            json.loads((FIX / "cv-pt-lines.json").read_text(encoding="utf-8")),
        )
    )
    assert prov(cv, "preamble").text.startswith("A proclamação da Independência Nacional")
    assert prov(cv, "art-1").context_heading == "Título I Da República"
    gw = parse(
        _html_bundle(
            catalog, "gw-anp-constitution", "gw-pt", json.loads((FIX / "gw-pt-lines.json").read_text(encoding="utf-8"))
        )
    )
    assert prov(gw, "preamble").heading == "Constituição de República da Guiné-Bissau"
    assert prov(gw, "art-1").text == "A Guiné-Bissau é uma República soberana, democrática, laica e unitária."


def test_citations_in_french_portuguese_spanish_and_english() -> None:
    module = get_module("constitution")

    def cite(label: str, eid: str, path: list[str], cc: str, language: str) -> str:
        return module.provision_citation(
            ProvisionCitationInput(label, "constitution", cc, "article", path, language, eid=eid)
        )

    assert cite("Benin Constitution", "art-1", ["art:1"], "BJ", "fr") == "Benin Constitution art. 1"
    assert cite("Benin Constitution", "art-157_1", ["art:157_1"], "BJ", "fr") == "Benin Constitution art. 157-1"
    assert cite("Cabo Verde Constitution", "art-162", ["art:162"], "CV", "pt") == "Cabo Verde Constitution art. 162"
    assert (
        cite("Equatorial Guinea Constitution", "art-8", ["art:8"], "GQ", "es")
        == "Equatorial Guinea Constitution art. 8"
    )
    assert (
        cite("Cameroon Constitution", "art-9-par2", ["art:9", "par:2"], "CM", "en") == "Cameroon Constitution art. 9(2)"
    )


# --------------------------------------------------------------------------- stored through the real pipeline


class _Documents:
    def __init__(self, source_id: str, documents: list[tuple[str, bytes]]):
        self.source_id = source_id
        self.documents = documents

    def fetch(self) -> Any:
        for slug, content in self.documents:
            yield RawDocument(
                f"constitution:{slug}", f"https://example.org/{slug}", "application/json", content, RETRIEVED, {}
            )


def _sources(catalog: Catalog) -> dict[str, list[tuple[str, bytes]]]:
    return {
        "bj-courconst-constitution": [
            ("bj-fr", _pdf_bundle(catalog, "bj-courconst-constitution", "bj-fr", make_pdf(_pages("bj-fr-pages.json"))))
        ],
        "ma-adala-constitution": [
            ("ma-fr", _pdf_bundle(catalog, "ma-adala-constitution", "ma-fr", make_pdf(_pages("ma-fr-pages.json"))))
        ],
        "cv-parlamento-constitution": [
            (
                "cv-pt",
                _html_bundle(
                    catalog,
                    "cv-parlamento-constitution",
                    "cv-pt",
                    json.loads((FIX / "cv-pt-lines.json").read_text(encoding="utf-8")),
                ),
            )
        ],
        "gq-gobierno-constitution": [
            ("gq-es", _pdf_bundle(catalog, "gq-gobierno-constitution", "gq-es", make_pdf(_pages("gq-es-pages.json"))))
        ],
    }


@pytest.fixture(scope="module")
def west_africa_ingested(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, ingested: IngestionReport
) -> dict[str, IngestionReport]:
    reports = {}
    with session_factory() as session:
        for source_id, documents in _sources(catalog).items():
            reports[source_id] = Ingestor(session, catalog, store).ingest_source(
                source_id, actor="pytest", connector=_Documents(source_id, documents)
            )
        session.commit()
    return reports


@pytest.mark.db
def test_the_fixtures_are_ingested_without_errors(west_africa_ingested: dict[str, IngestionReport]) -> None:
    for source_id, report in west_africa_ingested.items():
        assert report.errors == [] and report.conflicts == [], source_id
        assert report.documents_new == 1 and report.instruments_created == 1, source_id
        assert report.passages_indexed >= 6, source_id


@pytest.mark.db
def test_search_finds_an_article_with_its_citation_and_the_limitations(
    session: Session, catalog: Catalog, west_africa_ingested: dict[str, IngestionReport]
) -> None:
    res = search(
        session,
        catalog,
        SearchRequest(jurisdiction="BJ", q="La capitale de la République du Bénin est Porto-Novo"),
        today=TODAY,
    )
    hit = res["results"][0]
    assert hit["citation"].startswith("Benin Constitution art. 1")
    assert all(h["jurisdiction"]["id"] == "BJ" for h in res["results"])
    codes = {lim["code"] for lim in hit["limitations"]}
    assert {"ATTESTED_ONLY_AT", "PROFILE_UNVERIFIED", "NOT_AUTHENTIC_TEXT"} <= codes
    assert "Constitution of Benin" in hit["source"]["attribution"]
    ma = search(
        session,
        catalog,
        SearchRequest(
            jurisdiction="MA", q="Le Maroc est une monarchie constitutionnelle, démocratique, parlementaire et sociale"
        ),
        today=TODAY,
    )
    assert ma["results"][0]["citation"].startswith("Morocco Constitution art. 1")
    cv = search(
        session,
        catalog,
        SearchRequest(jurisdiction="CV", q="Cabo Verde é uma República soberana, unitária e democrática"),
        today=TODAY,
    )
    assert any(h["citation"].startswith("Cabo Verde Constitution art. 1") for h in cv["results"])
    gq = search(
        session,
        catalog,
        SearchRequest(jurisdiction="GQ", q="El Estado ecuatoguineano acata los principios del Derecho Internacional"),
        today=TODAY,
    )
    assert any(h["citation"].startswith("Equatorial Guinea Constitution art. 8") for h in gq["results"])


@pytest.mark.db
def test_lookup_by_uri_is_attested_at_the_edition_and_has_no_history(
    session: Session, catalog: Catalog, west_africa_ingested: dict[str, IngestionReport]
) -> None:
    got = provision_at(session, catalog, "/bj/constitution/~art-1", TODAY)
    assert got["provision"]["citation"] == "Benin Constitution art. 1"
    assert got["version"]["text"].startswith("L'Etat du Bénin est une République indépendante et souveraine")
    assert got["status_on_date"] == "in_force_date_unverified"


@pytest.mark.db
def test_reingest_is_idempotent_and_stored_texts_verify(
    session_factory: sessionmaker[Session],
    catalog: Catalog,
    store: LocalObjectStore,
    west_africa_ingested: dict[str, IngestionReport],
) -> None:
    from sqlalchemy import select

    from ulis.db import models as m

    sources = _sources(catalog)
    with session_factory() as session:
        for source_id, documents in sources.items():
            again = Ingestor(session, catalog, store).ingest_source(
                source_id, actor="pytest", connector=_Documents(source_id, documents)
            )
            assert again.documents_unchanged == 1 and again.provision_versions_created == 0, source_id
            assert again.errors == [] and again.conflicts == [], source_id
        ids = session.scalars(select(m.SourceDocument.id).where(m.SourceDocument.source_id.in_(list(sources)))).all()
        results = [verify_source_document(session, catalog, store, i, checker="pytest") for i in ids]
        assert len(results) == len(sources) and all(r["result"] == "pass" for r in results)
        session.rollback()

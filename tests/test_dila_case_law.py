"""France: DILA case law (CONSTIT, CASS, INCA, JADE): connector, parser, citations, ingestion with links to the codes.

Real decisions from the DILA stocks and daily archives (Licence Ouverte 2.0), see tests/fixtures/dila_juri/README.md.
"""

from __future__ import annotations

import io
import json
import tarfile
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from tests.test_dila_legi import (  # noqa: F401 - the legislation fixtures and the cache helpers
    CIVIL,
    UA,
    fr_ingested,
    seed_cache,
)
from ulis.catalog import Catalog
from ulis.db import models as m
from ulis.ingestion.pipeline import IngestionReport, Ingestor
from ulis.jurisdictions.modules.base import JudgmentCitationInput
from ulis.jurisdictions.modules.fr import FrModule, format_docket
from ulis.retrieval.search import SearchRequest, search
from ulis.settings import REPO_ROOT
from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.dila_case_law import DilaCaseLawConnector
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.polite_http import FetchError
from ulis.sources.normalized import ParsedDocument, ParseSkipped
from ulis.sources.parsers import dila_juri, fr_texts
from ulis.storage.object_store import LocalObjectStore

FIX = REPO_ROOT / "tests" / "fixtures" / "dila_juri"
QPC, DC71, AN93 = "CONSTEXT000045243083", "CONSTEXT000017665602", "CONSTEXT000017667265"
AVIS, CASS85, CASS24 = "JURITEXT000051744508", "JURITEXT000007015983", "JURITEXT000050221552"
CE, TC, CAA = "CETATEXT000051408975", "CETATEXT000051764351", "CETATEXT000051885343"
CONSTIT_STOCK = "Freemium_constit_global_20250713-140000.tar.gz"
CASS_STOCK = "Freemium_cass_global_20250713-140000.tar.gz"


def raw(ident: str) -> bytes:
    return (FIX / f"{ident}.xml").read_bytes()


def parse(ident: str) -> ParsedDocument:
    (doc,) = dila_juri.parse_dila_juri(raw(ident))
    return doc


def para(doc: ParsedDocument, eid: str) -> Any:
    return next(p for p in doc.paragraphs if p.eid == eid)


# ------------------------------------------------------------------------------------------------------ parser
def test_constitutional_decision() -> None:
    doc = parse(QPC)
    assert doc.work_uri == f"/fr/juri/{QPC}" and doc.expression_uri == f"/fr/juri/{QPC}/fra@2022-01-07"
    j = doc.judgment
    assert j is not None
    assert (j.court_id, j.decision_date, j.docket, j.neutral_citation) == (
        "FR-CC",
        date(2022, 1, 7),
        "2021-960 QPC",
        "ECLI:FR:CC:2022:2021.960.QPC",
    )
    assert j.case_name.startswith("Association française des producteurs de cannabinoïdes [Définition")
    assert (doc.doc_kind, doc.language, doc.authoritative, doc.text_origin) == ("judgment", "fr", False, "official")
    assert doc.ext["nature"] == "QPC" and doc.ext["solution"] == "Conformité" and doc.ext["nor"] == "CSCX2200705S"
    numbered = [p for p in doc.paragraphs if p.part == "considerations"]
    assert [p.num for p in numbered] == [str(i) for i in range(1, 22)]
    assert numbered[2].text.startswith("L'article L. 5132-7 du code de la santé publique, dans sa rédaction")
    assert doc.paragraphs[0].part == "header" and doc.paragraphs[0].text.startswith(
        "LE CONSEIL CONSTITUTIONNEL A ÉTÉ SAISI"
    )
    # the headings "- Sur le fond :" are units of their own, not the end of the paragraph before them
    assert [p.text for p in doc.paragraphs if p.part == "heading"] == [
        "- Sur l'admission des interventions :",
        "- Sur le fond :",
    ]
    assert not para(doc, "para-7").text.endswith(":") and para(doc, "decision-1").text.startswith("Jugé par le Conseil")
    assert para(doc, "para-1").text.startswith("La question prioritaire de constitutionnalité doit être considérée")


def test_decision_of_the_constitutional_council_with_the_operative_part() -> None:
    doc = parse(AN93)
    assert doc.judgment is not None and doc.judgment.docket == "93-1615 AN"
    assert [p.part for p in doc.paragraphs] == ["header", "considerations", "considerations", "decision"]
    decision = doc.paragraphs[-1]
    assert decision.text.startswith("Décide :\nArticle premier :\nM. Patrick Renzi est déclaré inéligible")
    assert "Délibéré par le Conseil constitutionnel dans sa séance du 4 novembre 1993" in decision.text
    assert para(doc, "para-2").text.startswith("Considérant qu'en vertu du deuxième alinéa de l'article L.O. 128")


def test_old_published_decision_has_headnotes_and_blocks() -> None:
    doc = parse(CASS85)
    assert doc.judgment is not None
    assert (doc.judgment.court_id, doc.judgment.docket, doc.judgment.neutral_citation) == ("FR-CCASS", "83-11960", None)
    assert doc.ext["formation"] == "ASSEMBLEE_PLENIERE" and doc.ext["published_in_bulletin"] is True
    parts = [p.part for p in doc.paragraphs]
    assert parts[:3] == ["headnote", "headnote", "references"] and "considerations" not in parts
    assert parts.count("text") >= 5 and parts[-1] == "decision"
    assert doc.paragraphs[0].text.startswith("SECURITE SOCIALE - Cotisations")
    assert all(p.num is None for p in doc.paragraphs)


def test_recent_decision_of_the_cour_de_cassation() -> None:
    doc = parse(CASS24)
    assert doc.judgment is not None and doc.judgment.neutral_citation == "ECLI:FR:CCASS:2024:CO00516"
    assert [p.num for p in doc.paragraphs if p.num] == [str(i) for i in range(1, 13)]
    refs = {(p.eid, r.href.rpartition("~")[2], r.extraction_method) for p in doc.paragraphs for r in p.refs}
    assert refs >= {
        ("para-5", "art-1353", "pattern"),
        ("para-5", "art-2313", "pattern"),
        ("para-5", "art-L643-1", "pattern"),
        ("header-1", "art-R431-5", "pattern"),
    }
    commerce = "LEGITEXT000005634379"  # "articles L. 643-1 ... du code de commerce"
    assert f"/fr/legi/{commerce}/~art-L643-1" in {r.href for p in doc.paragraphs for r in p.refs}


def test_the_courts_list_of_texts_applied_is_source_markup() -> None:
    doc = parse(AVIS)
    assert doc.ext["nature"] == "AVIS" and doc.ext["formation"] == "AVIS"
    first = doc.paragraphs[0]
    assert first.part == "headnote"
    marked = [(r.href, r.extraction_method) for r in first.refs]
    assert marked == [(f"/fr/legi/{CIVIL}/~art-{n}", "source_markup") for n in (1569, 1570, 1571, 1574)]
    assert first.refs[0].text == "Articles 1569, 1570, 1571 et 1574 du code civil"
    # the body of the avis names article 1569 and (in the operative part) article R. 431-5 of the Code de l'organisation
    # judiciaire: found by the strict pattern, not by the court's markup
    body = {(p.eid, r.href.rpartition("~")[2], r.extraction_method) for p in doc.paragraphs[1:] for r in p.refs}
    assert body == {("para-3", "art-1569", "pattern"), ("decision-1", "art-R431-5", "pattern")}


def test_administrative_decisions() -> None:
    ce, caa, tc = parse(CE), parse(CAA), parse(TC)
    assert ce.judgment is not None and caa.judgment is not None and tc.judgment is not None
    assert (ce.judgment.court_id, ce.judgment.docket, ce.judgment.neutral_citation) == (
        "FR-CE",
        "493033",
        "ECLI:FR:CECHS:2025:493033.20250331",
    )
    assert ce.ext["formation"] == "7ème chambre" and ce.ext["appeal_type"] == "Excès de pouvoir"
    assert (caa.judgment.court_id, caa.judgment.docket, caa.ext["place"]) == ("FR-CAA", "22PA02724", "PARIS")
    assert tc.judgment.court_id == "FR-TC" and tc.paragraphs[0].part == "headnote"
    # the publisher's headnote is stored as published, including its wrongly encoded characters
    assert any("rÃ©sulte" in p.text for p in tc.paragraphs if p.part == "headnote")
    assert [p.num for p in ce.paragraphs if p.num] == ["1", "2", "3", "4", "5", "6"]
    assert "1°) d'annuler cet arrêt" in ce.paragraphs[0].text  # "1°)" is not a paragraph number


def test_no_name_of_a_judge_lawyer_or_party_is_kept_outside_the_text() -> None:
    for ident in (AVIS, CASS24, CE, CAA, QPC):
        doc = parse(ident)
        dumped = json.dumps(doc.ext, ensure_ascii=False)
        assert doc.judgment is not None
        for secret in ("Champalaune", "LUBEN", "Luben", "Bréchot", "OHL", "VEXLIARD"):
            assert secret not in dumped


def test_unusable_documents_are_refused() -> None:
    with pytest.raises(ValueError, match="unexpected root"):
        dila_juri.parse_dila_juri(b"<TEXTE_VERSION/>")
    with pytest.raises(Exception, match="DTD|entit"):
        dila_juri.parse_dila_juri(b'<!DOCTYPE x [<!ENTITY a "b">]><TEXTE_JURI_ADMIN/>')
    text = raw(CE).decode("utf-8")
    with pytest.raises(ValueError, match="unknown court"):
        dila_juri.parse_dila_juri(text.replace("<JURIDICTION>Conseil d'État<", "<JURIDICTION>Cour de Mars<").encode())
    with pytest.raises(ValueError, match="invalid decision date"):
        dila_juri.parse_dila_juri(text.replace("<DATE_DEC>2025-03-31<", "<DATE_DEC>2025-13-31<").encode())
    empty = text[: text.index("<BLOC_TEXTUEL>")] + "<BLOC_TEXTUEL><CONTENU/></BLOC_TEXTUEL></TEXTE></TEXTE_JURI_ADMIN>"
    with pytest.raises(ParseSkipped, match="no text"):
        dila_juri.parse_dila_juri(empty.encode())
    # a published decision whose file holds only the headnote is kept with it
    only_headnote = raw(CASS85).decode("utf-8")
    start, end = only_headnote.index("<BLOC_TEXTUEL>"), only_headnote.index("</BLOC_TEXTUEL>") + len("</BLOC_TEXTUEL>")
    kept = dila_juri.parse_dila_juri(
        (only_headnote[:start] + "<BLOC_TEXTUEL><CONTENU/></BLOC_TEXTUEL>" + only_headnote[end:]).encode()
    )
    assert [p.part for p in kept[0].paragraphs] == ["headnote", "headnote", "references"]


def test_courts_of_the_publisher_names() -> None:
    assert dila_juri.court_for("Conseil constitutionnel") == ("FR-CC", None)
    assert dila_juri.court_for("Conseil d'Etat") == dila_juri.court_for("Conseil d’État") == ("FR-CE", None)
    assert dila_juri.court_for("Tribunal des Conflits") == ("FR-TC", None)
    assert dila_juri.court_for("CAA de MARSEILLE") == ("FR-CAA", "MARSEILLE")
    assert dila_juri.court_for("TA de Lyon") == ("FR-TA", "Lyon")
    with pytest.raises(ValueError):
        dila_juri.court_for("Cour d'appel de Paris")


def test_numbered_paragraphs_must_run_in_sequence() -> None:
    lines = [
        "Vu la loi.",
        "",
        "1. Premier.",
        "suite du premier",
        "5. mai est un mois.",
        "2. Deuxième.",
        "- Sur le fond :",
        "Décide :",
        "Article 1er : rejet",
    ]
    units = dila_juri._units(lines)
    assert units == [
        ("header", None, "Vu la loi."),
        ("considerations", "1", "Premier.\nsuite du premier\n5. mai est un mois."),
        ("considerations", "2", "Deuxième."),
        ("heading", None, "- Sur le fond :"),
        ("decision", None, "Décide :\nArticle 1er : rejet"),
    ]


# ------------------------------------------------------------------------------------------------------ references
def test_references_are_strict() -> None:
    civil = lambda text, on=date(2020, 1, 1): [r.href.split("/~")[1] for r in fr_texts.statute_refs(text, on)]  # noqa: E731
    assert civil("vu les articles 1569, 1570 et 1574 du code civil") == ["art-1569", "art-1570", "art-1574"]
    assert civil("l'article 1er du Code Civil") == ["art-1"]
    assert civil("l'article 61-1 de la Constitution") == ["art-61-1"]
    assert civil("l'article 1240 du code civil, alinéa 2, et") == ["art-1240"]
    # several texts in one phrase, "même code", unknown codes: no link
    assert civil("l'article 12 du code de la mer") == [] and civil("l'article 12 du même code") == []
    # another country's code, an earlier Constitution
    assert civil("l'article 12 du code civil suisse") == [] and civil("l'article 12 du code civil du Québec") == []
    assert (
        civil("l'article 3 de la Constitution de 1946") == []
        and civil("l'article 6 de la Constitution du 27 octobre 1946") == []
    )
    assert civil("l'article 3 de la Constitution du 4 octobre 1958") == ["art-3"]
    assert civil("l'article 3 de la Constitution, qui") == ["art-3"] and civil("l'article 3 du code civil.") == [
        "art-3"
    ]
    assert civil("les articles L. 441-1 et suivants du code civil") == []
    assert fr_texts.statute_refs("article 1240 du code civil", None)[0].href.endswith("/~art-1240")
    # the same words from 1989 are about the earlier Code pénal
    penal = "l'article 121-3 du code pénal"
    assert fr_texts.statute_refs(penal, date(2000, 1, 1)) and not fr_texts.statute_refs(penal, date(1989, 1, 1))
    assert not fr_texts.statute_refs(penal, None)


def test_laws_are_found_by_number() -> None:
    refs = fr_texts.statute_refs("l'article 22 de la loi n° 78-17 du 6 janvier 1978", date(2020, 1, 1))
    assert [r.href for r in refs] == ["/fr/legi/JORFTEXT000000886460/~art-22"]
    assert fr_texts.statute_refs("l'article 22 de la loi n° 99-99999 du 6 janvier 1978", date(2020, 1, 1)) == []


# ------------------------------------------------------------------------------------------------------ citations
def _cite(module: FrModule, ident: str, paragraph: str | None = None) -> str:
    doc = parse(ident)
    j = doc.judgment
    assert j is not None
    return module.judgment_citation(
        JudgmentCitationInput(
            case_name=j.case_name,
            neutral_citation=j.neutral_citation,
            decision_date=j.decision_date,
            docket=j.docket,
            court_id=j.court_id,
            court_name=j.court_id,
            paragraph=paragraph,
            language="fr",
            jurisdiction_id="FR",
            ext=doc.ext,
        )
    )


def test_decisions_are_cited_in_french_style() -> None:
    module = FrModule()
    assert _cite(module, QPC, "5") == "Cons. const., 7 janv. 2022, n° 2021-960 QPC, § 5"
    assert _cite(module, AN93) == "Cons. const., 4 nov. 1993, n° 93-1615 AN"
    assert _cite(module, CASS24) == "Cass. ass. plén., 11 sept. 2024, n° 23-12.695"
    assert _cite(module, AVIS) == "Cass. avis, 12 juin 2025, n° 25-70.009"
    assert _cite(module, CASS85) == "Cass. ass. plén., 25 oct. 1985, n° 83-11.960"
    assert _cite(module, CE, "3") == "CE, 31 mars 2025, n° 493033, § 3"
    assert _cite(module, CAA) == "CAA Paris, 11 juill. 2025, n° 22PA02724"
    assert _cite(module, TC) == "T. confl., 2 juin 2025, n° C4338"
    assert format_docket("FR-CCASS", "11-17738; 21-25554") == "11-17.738; 21-25.554"
    assert format_docket("FR-CE", "493033") == "493033" and format_docket("FR-CE", None) is None


# ------------------------------------------------------------------------------------------------------ archives
def dump_name(ident: str, database: str) -> str:
    tail = "/".join([ident[8:20][i : i + 2] for i in range(0, 10, 2)])
    if ident.startswith("CONS"):
        return f"constit/global/CONS/TEXT/{tail}/{ident}.xml"
    if ident.startswith("JURI"):
        return f"juri/{database.lower()}/global/JURI/TEXT/{tail}/{ident}.xml"
    return f"jade/global/publie/CETA/TEXT/{tail}/{ident}.xml"


def build_archive(path: Path, files: dict[str, bytes], prefix: str = "", extra: dict[str, bytes] | None = None) -> None:
    with tarfile.open(path, "w:gz") as tar:
        for name, data in {
            **{prefix + k: v for k, v in files.items()},
            **{prefix + k: v for k, v in (extra or {}).items()},
        }.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))


def manifest_file(tmp_path: Path, database: str, stock_file: str, size: int, **overrides: Any) -> Path:
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "source_id": f"fr-dila-{database.lower()}",
        "database": database,
        "user_agent": UA,
        "crawl_delay_seconds": 5,
        "stock": {"file": stock_file, "date": "2025-07-13", "bytes": size},
        "incrementals": {"enabled": False},
        "select": {},
    }
    manifest.update(overrides)
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(manifest))
    return path


def cass_cache(tmp_path: Path, **kwargs: Any) -> tuple[Path, int]:
    """A cache holding a CASS stock (the 1985 and 2024 decisions) and, if asked, increments."""
    cache = tmp_path / "cache"
    seed_cache(cache)
    directory = cache / "echanges.dila.gouv.fr" / "OPENDATA" / "CASS"
    directory.mkdir(parents=True)
    stock = directory / CASS_STOCK
    build_archive(stock, {dump_name(i, "cass"): raw(i) for i in (CASS85, CASS24)})
    return cache, stock.stat().st_size


def run(manifest: Path, cache: Path) -> tuple[DilaCaseLawConnector, list[RawDocument]]:
    connector = DilaCaseLawConnector(manifest, manifest_source(manifest), cache, offline=True, sleep=lambda _s: None)
    return connector, list(connector.fetch())


def manifest_source(manifest: Path) -> str:
    return str(yaml.safe_load(manifest.read_text())["source_id"])


def test_connector_yields_the_decisions_as_published(tmp_path: Path) -> None:
    cache, size = cass_cache(tmp_path)
    connector, docs = run(manifest_file(tmp_path, "CASS", CASS_STOCK, size), cache)
    assert sorted(d.external_id for d in docs) == sorted([CASS85, CASS24])
    assert {d.content for d in docs} == {raw(CASS85), raw(CASS24)}  # byte for byte
    first = docs[0]
    assert first.media_type == "application/xml" and first.metadata["database"] == "CASS"
    assert first.uri.endswith(f"{CASS_STOCK}#{first.external_id}") and first.metadata["archive"]["name"] == CASS_STOCK
    assert connector.report["selected"] == 2 and connector.fetch_errors == []


def test_selection_by_court_year_nature_and_count(tmp_path: Path) -> None:
    cache, size = cass_cache(tmp_path)

    def ids(**select: Any) -> list[str]:
        _, docs = run(manifest_file(tmp_path / "m", "CASS", CASS_STOCK, size, select=select), cache)
        return sorted(d.external_id for d in docs)

    assert ids(year_from=2000) == [CASS24] and ids(year_to=1999) == [CASS85] and ids(year_from=2025) == []
    assert (
        ids(juridictions=["Cour de cassation"]) == sorted([CASS85, CASS24])
        and ids(juridictions=["Conseil d'Etat"]) == []
    )
    assert ids(juridictions=["Cour de*"]) == sorted([CASS85, CASS24]) and ids(natures=["AVIS"]) == []
    assert ids(natures=["ARRET"]) == sorted([CASS85, CASS24]) and len(ids(max_documents=1)) == 1


def test_newest_archive_wins_and_deletions_are_honoured(tmp_path: Path) -> None:
    cache, size = cass_cache(tmp_path)
    directory = cache / "echanges.dila.gouv.fr" / "OPENDATA" / "CASS"
    seed_cache(
        cache,
        ["Freemium_cass_global_20250713-140000.tar.gz", "CASS_20250714-210000.tar.gz", "CASS_20250715-210000.tar.gz"],
        database="CASS",
    )
    changed = raw(CASS24).replace(
        b"</TEXTE_JURI_JUDI>", b"</TEXTE_JURI_JUDI>\n"
    )  # synthetic change, only for this mechanism test
    build_archive(
        directory / "CASS_20250714-210000.tar.gz",
        {dump_name(CASS24, "cass"): changed, dump_name(AVIS, "cass"): raw(AVIS)},
        "20250714-210000/",
    )
    deletion = dump_name(CASS85, "cass").removesuffix(".xml").split("/")[-1]
    build_archive(
        directory / "CASS_20250715-210000.tar.gz",
        {},
        "20250715-210000/",
        {"liste_suppression_cass.dat": (deletion + "\n").encode()},
    )
    manifest = manifest_file(tmp_path / "m", "CASS", CASS_STOCK, size, incrementals={"enabled": True})
    connector, docs = run(manifest, cache)
    by_id = {d.external_id: d for d in docs}
    assert sorted(by_id) == sorted([CASS24, AVIS])  # CASS85 was deleted by the newest archive
    assert (
        by_id[CASS24].content == changed and by_id[CASS24].metadata["archive"]["name"] == "CASS_20250714-210000.tar.gz"
    )
    assert connector.report["superseded"] == 2 and connector.report["deleted_ids"] == 1
    # without increments, only the stock
    _, stock_only = run(manifest_file(tmp_path / "n", "CASS", CASS_STOCK, size), cache)
    assert sorted(d.external_id for d in stock_only) == sorted([CASS85, CASS24])


def test_manifests_are_validated(tmp_path: Path) -> None:
    cache, size = cass_cache(tmp_path)

    def check(**kw: Any) -> None:
        DilaCaseLawConnector(
            manifest_file(tmp_path / "v", "CASS", CASS_STOCK, size, **kw), "fr-dila-cass", cache
        ).manifest()

    check()
    with pytest.raises(ManifestError, match="user_agent"):
        check(user_agent="bot (x)")
    with pytest.raises(ManifestError):
        check(crawl_delay_seconds=1)
    with pytest.raises(ManifestError, match="year_from"):
        check(select={"year_from": 2020, "year_to": 2010})
    with pytest.raises(ManifestError, match="invalid nature"):
        check(select={"natures": ["a b"]})
    with pytest.raises(ManifestError, match="unexpected stock file name"):
        check(stock={"file": "Freemium_jade_global_20250713-140000.tar.gz", "date": "2025-07-13", "bytes": 10})
    with pytest.raises(ManifestError, match="source_id"):
        DilaCaseLawConnector(manifest_file(tmp_path / "w", "CASS", CASS_STOCK, size), "other", cache).manifest()


@pytest.mark.parametrize("source_id", ["fr-dila-constit", "fr-dila-cass", "fr-dila-inca", "fr-dila-jade"])
def test_committed_manifests_are_valid(catalog: Catalog, source_id: str) -> None:
    source = catalog.sources.get(source_id)
    assert (source.connector, source.parser, source.status) == ("dila_case_law", "dila_juri_xml", "active")
    manifest = DilaCaseLawConnector(
        REPO_ROOT / (source.access.endpoint or ""), source_id, Path("/nonexistent")
    ).manifest()
    assert manifest.incrementals.enabled and manifest.database == source_id.split("-")[-1].upper()
    assert source.licence.id == "Etalab-Licence-Ouverte-2.0" and "pseudonymis" in (
        source.licence.attribution_text or ""
    )
    assert source.licence.review_status == "approved"


def test_offline_connector_needs_its_cache(tmp_path: Path) -> None:
    manifest = manifest_file(tmp_path, "CASS", CASS_STOCK, 10)
    with pytest.raises(FetchError, match="offline and not cached"):
        list(DilaCaseLawConnector(manifest, "fr-dila-cass", tmp_path / "empty", offline=True).fetch())


# ------------------------------------------------------------------------------------------------------ PostgreSQL
class _Decisions:
    def __init__(self, idents: list[str]):
        self.idents = idents

    def fetch(self) -> Iterator[RawDocument]:
        now = datetime(2026, 10, 14, tzinfo=UTC)
        for ident in self.idents:
            yield RawDocument(
                ident,
                f"https://echanges.dila.gouv.fr/OPENDATA/X/y.tar.gz#{ident}",
                "application/xml",
                raw(ident),
                now,
                {},
            )


@pytest.fixture(scope="module")
def fr_case_law(
    session_factory: sessionmaker[Session],
    catalog: Catalog,
    store: LocalObjectStore,
    fr_ingested: IngestionReport,  # noqa: F811 - the Code civil fixture (tests/test_dila_legi.py) is in the database
) -> dict[str, IngestionReport]:
    reports = {}
    with session_factory() as session:
        ingestor = Ingestor(session, catalog, store)
        reports["constit"] = ingestor.ingest_source(
            "fr-dila-constit", actor="pytest", connector=_Decisions([QPC, DC71, AN93])
        )
        reports["cass"] = ingestor.ingest_source(
            "fr-dila-cass", actor="pytest", connector=_Decisions([AVIS, CASS85, CASS24])
        )
        reports["jade"] = ingestor.ingest_source("fr-dila-jade", actor="pytest", connector=_Decisions([CE, TC, CAA]))
        session.commit()
    return reports


@pytest.mark.db
def test_decisions_are_ingested_and_linked_to_the_code(
    session: Session, catalog: Catalog, fr_case_law: dict[str, IngestionReport]
) -> None:
    for name, report in fr_case_law.items():
        assert report.errors == [] and report.conflicts == [], name
    assert fr_case_law["constit"].judgments_created == 3 and fr_case_law["cass"].judgments_created == 3
    assert fr_case_law["jade"].judgments_created == 3
    avis = session.scalar(select(m.Judgment).where(m.Judgment.uri == f"/fr/juri/{AVIS}"))
    assert avis is not None and avis.court_id == "FR-CCASS" and avis.docket == "25-70009"
    links = list(
        session.scalars(select(m.CitationLink).where(m.CitationLink.to_uri.like(f"/fr/legi/{CIVIL}/~art-15%")))
    )
    assert {link.to_uri.rpartition("~")[2] for link in links} >= {"art-1569", "art-1570", "art-1571"}
    marked = [link for link in links if link.extraction_method == "source_markup"]
    assert marked and all(link.to_provision_id is not None and link.to_instrument_id is not None for link in marked)


@pytest.mark.db
def test_search_finds_a_decision_with_its_citation(
    session: Session, catalog: Catalog, fr_case_law: dict[str, IngestionReport]
) -> None:
    res = search(
        session,
        catalog,
        SearchRequest(
            q="cannabinoïdes stupéfiant substances vénéneuses",
            jurisdiction="FR",
            as_of=date(2026, 6, 1),
            query_language="fr",
        ),
        today=date(2026, 10, 14),
    )
    hits = [h for h in res["results"] if h["citation"].startswith("Cons. const.")]
    assert hits, [h["citation"] for h in res["results"]]
    top = hits[0]
    assert top["citation"].startswith("Cons. const., 7 janv. 2022, n° 2021-960 QPC, § ")
    assert top["source"]["attribution"].startswith(
        "Source: Direction de l'information légale et administrative (DILA), base CONSTIT"
    )
    codes = {lim["code"] for lim in top["limitations"]}
    assert "PROFILE_UNVERIFIED" in codes

"""EU case law: judgments of the Court of Justice and the General Court from Cellar.

The fixtures are unmodified Cellar responses (gzip-compressed) for five judgments in English and
French, replayed offline by the connector: Schrems (C-362/14) and Digital Rights Ireland
(C-293/12), converted from Formex; Simmenthal (106/77) and Van Gend & Loos (26/62), digitised from
the printed Reports; Kadi v Council and Commission (T-315/01, General Court).
"""

from __future__ import annotations

import copy
import gzip
import json
import shutil
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ulis.catalog import Catalog
from ulis.db import models as m
from ulis.ingestion.pipeline import IngestionReport, Ingestor
from ulis.ingestion.pipeline import resolve_uri as pipeline_resolve_uri
from ulis.jurisdictions.modules import JudgmentCitationInput, get_module
from ulis.retrieval.authority import classify_judgment
from ulis.retrieval.lookup import judgment_detail
from ulis.retrieval.search import SearchRequest, search
from ulis.settings import REPO_ROOT
from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.eu_cellar import CellarConnector
from ulis.sources.connectors.eu_cellar_case_law import (
    CellarCaseLawConnector,
    choose_items,
    query_case_items,
    query_case_metadata,
)
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.polite_http import FetchError
from ulis.sources.normalized import ParsedDocument
from ulis.sources.parsers.eu_cellar import EuParseError
from ulis.sources.parsers.eu_cellar_case_law import (
    legislation_refs,
    numbered_lines,
    parse_case_metadata,
    parse_eu_case_law,
    resolve_uri,
)
from ulis.storage.object_store import LocalObjectStore

FIX = REPO_ROOT / "tests" / "fixtures" / "eu_cellar_case_law"
LEGISLATION_FIX = REPO_ROOT / "tests" / "fixtures" / "eu_cellar"
SCHREMS, DRI, SIMMENTHAL, VAN_GEND, KADI_GC = "62014CJ0362", "62012CJ0293", "61977CJ0106", "61962CJ0026", "62001TJ0315"
CHARTER_ART_7 = "/eu/treaty/char_2016/~art_7"
QUERY_EN = "public authorities access on a generalised basis to the content of electronic communications"
QUERY_FR = "autorités publiques accéder de manière généralisée au contenu des communications électroniques"


def _decompressed_copy(source: Path, dest: Path) -> Path:
    shutil.copytree(source, dest)
    for gz in dest.rglob("*.gz"):
        gz.with_suffix("").write_bytes(gzip.decompress(gz.read_bytes()))
        gz.unlink()
    return dest


@pytest.fixture(scope="module")
def bundles(tmp_path_factory: pytest.TempPathFactory) -> dict[str, RawDocument]:
    cache = _decompressed_copy(FIX / "cache", tmp_path_factory.mktemp("case-law") / "cache")
    connector = CellarCaseLawConnector(FIX / "manifest.yaml", "eu-cellar-case-law", cache, offline=True)
    docs = {raw.external_id: raw for raw in connector.fetch()}
    assert connector.http is not None and connector.http.requests_made == 0
    return docs


def _doc(bundles: dict[str, RawDocument], celex: str, lang: str) -> ParsedDocument:
    (doc,) = parse_eu_case_law(bundles[f"cellar-case-law:{celex}:{lang}"].content)
    return doc


def _para(doc: ParsedDocument, eid: str) -> Any:
    return {p.eid: p for p in doc.paragraphs}[eid]


def _cite(doc: ParsedDocument, eid: str) -> str:
    p = _para(doc, eid)
    assert doc.judgment is not None
    return get_module("eu").judgment_citation(
        JudgmentCitationInput(
            doc.judgment.case_name,
            doc.judgment.neutral_citation,
            doc.judgment.decision_date,
            doc.judgment.docket,
            doc.judgment.court_id,
            "court",
            p.num,
            doc.language,
            "EU",
            ext=doc.ext,
        )
    )


# --------------------------------------------------------------------------- connector


def test_connector_replays_cache_offline_and_bundles_every_response(bundles: dict[str, RawDocument]) -> None:
    assert sorted(bundles) == sorted(
        f"cellar-case-law:{c}:{lang}" for c in (SCHREMS, DRI, SIMMENTHAL, VAN_GEND, KADI_GC) for lang in ("en", "fr")
    )
    raw = bundles[f"cellar-case-law:{SCHREMS}:fr"]
    bundle = json.loads(raw.content)
    assert [p["role"] for p in bundle["parts"]] == ["sparql:metadata", "sparql:items:xhtml", "item:xhtml"]
    assert bundle["kind"] == "case_law" and bundle["short_name"] == "Schrems"
    assert all(p["url"].startswith("https://publications.europa.eu/") for p in bundle["parts"])
    assert raw.uri is not None and raw.uri.startswith("https://publications.europa.eu/resource/cellar/")


def test_connector_refuses_bad_manifests_and_uncached_requests(tmp_path: Path) -> None:
    manifest = yaml.safe_load((FIX / "manifest.yaml").read_text())

    def run(works: list[dict[str, str]]) -> None:
        path = tmp_path / "m.yaml"
        path.write_text(yaml.safe_dump(dict(manifest, works=works)))
        list(CellarCaseLawConnector(path, "eu-cellar-case-law", tmp_path / "cache", offline=True).fetch())

    with pytest.raises(ManifestError, match="judgment"):
        run([{"celex": "32016R0679"}])  # legislation, not a judgment
    with pytest.raises(ManifestError, match="invalid CELEX"):
        run([{"celex": '62014CJ0362" . ?s ?p ?o #'}])
    with pytest.raises(FetchError, match="offline and not cached"):
        run([{"celex": "62014CJ0363"}])
    with pytest.raises(ManifestError, match="source_id"):
        CellarCaseLawConnector(FIX / "manifest.yaml", "other", tmp_path, offline=True).fetch().__next__()


def test_queries_embed_only_validated_celex() -> None:
    assert '"62014CJ0362"^^xsd:string' in query_case_metadata("62014CJ0362")
    assert "cdm:case-law_ecli" in query_case_metadata("62014CJ0362")
    assert "cdm:expression_case-law_parties" in query_case_items("62014CJ0362", ["en", "fr"])


def _row(work: str, lang: str, item: str) -> dict[str, dict[str, str]]:
    base = "http://publications.europa.eu/resource/"
    return {
        "work": {"value": base + "cellar/" + work},
        "lang": {"value": base + "authority/language/" + lang},
        "item": {"value": f"{base}cellar/{item}/DOC_1"},
    }


def test_choose_items_takes_every_language_from_one_cellar_work() -> None:
    a, b = "aaaaaaaa-0000-0000-0000-000000000000", "bbbbbbbb-0000-0000-0000-000000000000"
    rows = [_row(a, "ENG", f"{a}.0006.01"), _row(a, "FRA", f"{a}.0010.01"), _row(b, "ENG", f"{b}.0001.05")]
    chosen = choose_items(rows, ["en", "fr"])
    assert chosen == {
        "en": f"http://publications.europa.eu/resource/cellar/{a}.0006.01/DOC_1",
        "fr": f"http://publications.europa.eu/resource/cellar/{a}.0010.01/DOC_1",
    }
    # two files of the same work and language: nothing is guessed
    with pytest.raises(FetchError, match="expected one XHTML file"):
        choose_items([*rows, _row(a, "ENG", f"{a}.0006.02")], ["en", "fr"])
    with pytest.raises(FetchError, match="expected one XHTML file"):
        choose_items(rows[:1], ["en", "fr"])  # no French file at all


# --------------------------------------------------------------------------- parser: converted from Formex


def test_schrems_metadata_and_numbered_points(bundles: dict[str, RawDocument]) -> None:
    doc = _doc(bundles, SCHREMS, "en")
    assert doc.doc_kind == "judgment" and doc.jurisdiction_id == "EU" and doc.ext_module == "eu"
    j = doc.judgment
    assert j is not None
    assert (j.court_id, j.neutral_citation, j.decision_date, j.docket) == (
        "EU-CJEU",
        "ECLI:EU:C:2015:650",
        date(2015, 10, 6),
        "C-362/14",
    )
    assert j.case_name == "Maximillian Schrems v Data Protection Commissioner"
    assert doc.work_uri == "/eu/case/ECLI:EU:C:2015:650"
    assert doc.expression_uri == "/eu/case/ECLI:EU:C:2015:650/eng@2015-10-06"
    assert doc.ext["designations"] == {"en": "Case C-362/14", "fr": "Affaire C-362/14"}
    # English is the language of the case; the French version is a translation
    assert doc.authoritative and not _doc(bundles, SCHREMS, "fr").authoritative
    nums = [p.num for p in doc.paragraphs if p.part == "motivation" and p.num]
    assert nums == [str(n) for n in range(1, 108)]
    assert _para(doc, "para_94").text == (
        "In particular, legislation permitting the public authorities to have access on a generalised basis to the "
        "content of electronic communications must be regarded as compromising the essence of the fundamental "
        "right to respect for private life, as guaranteed by Article 7 of the Charter (see, to this effect, "
        "judgment in Digital Rights Ireland and Others, C-293/12 and C-594/12, EU:C:2014:238, paragraph 39)."
    )
    assert "Language of the case: English." in _para(doc, "header").text
    assert _para(doc, "index").text.startswith("‛Reference for a preliminary ruling")


def test_operative_part_is_kept_as_separate_paragraphs(bundles: dict[str, RawDocument]) -> None:
    doc = _doc(bundles, SCHREMS, "en")
    decision = [p for p in doc.paragraphs if p.part == "decision"]
    assert [(p.eid, p.num) for p in decision] == [("opu_1", None), ("op_1", "op-1"), ("op_2", "op-2")]
    assert decision[0].text == "On those grounds, the Court (Grand Chamber) hereby rules:"
    assert decision[2].text == "Decision 2000/520 is invalid."
    assert decision[1].text.startswith("Article 25(6) of Directive 95/46/EC of the European Parliament")
    fr = _doc(bundles, SCHREMS, "fr")
    assert [p.eid for p in fr.paragraphs if p.part == "decision"] == ["opu_1", "op_1", "op_2"]
    assert _para(fr, "op_2").text == "La décision 2000/520 est invalide."


def test_language_versions_share_identifiers(bundles: dict[str, RawDocument]) -> None:
    en, fr = _doc(bundles, SCHREMS, "en"), _doc(bundles, SCHREMS, "fr")
    assert en.work_uri == fr.work_uri and en.judgment == fr.judgment
    numbered = lambda d: [(p.eid, p.num) for p in d.paragraphs if p.num]  # noqa: E731
    assert numbered(en) == numbered(fr)
    assert _para(fr, "para_94").text.startswith("En particulier, une réglementation permettant aux autorités")


def test_references_are_source_links_and_exact_patterns_only(bundles: dict[str, RawDocument]) -> None:
    doc = _doc(bundles, SCHREMS, "en")
    refs = {p.eid: p.refs for p in doc.paragraphs}
    # a link the source marks, with the point it pins
    (link,) = [r for r in refs["para_94"] if r.extraction_method == "source_markup"]  # the point replaces the case link
    assert link.href == "http://publications.europa.eu/resource/ecli/ECLI%3AEU%3AC%3A2014%3A238#point39"
    assert resolve_uri(link.href) == ("/eu/case/ECLI:EU:C:2014:238", "para_39")
    # "Article 7 of the Charter" names the article and the instrument; the link is to the article
    patterns = {r.href: r.extraction_method for r in refs["para_94"]}
    assert patterns[CHARTER_ART_7] == "pattern"
    all_hrefs = {r.href for rs in refs.values() for r in rs}
    assert "/eu/treaty/tfeu_2016/~art_16" in all_hrefs  # "Article 16(2) TFEU"
    assert "/eu/treaty/char_2016/~art_47" in all_hrefs  # "Articles 7, 8 and 47 of the Charter ..."
    # Directive 95/46/EC is not among the ingested instruments, and OJ references are not links
    assert not any("95" in h for h in all_hrefs) and not any("/oj/" in h for h in all_hrefs)


def test_pattern_rules_never_guess() -> None:
    def hrefs(text: str, lang: str = "en", charter: str = "short") -> set[str]:
        return {r.href for r in legislation_refs(text, lang, charter)}

    assert hrefs("Articles 101 and 102 TFEU") == {"/eu/treaty/tfeu_2016/~art_101", "/eu/treaty/tfeu_2016/~art_102"}
    assert hrefs("Article 6(1)(a) of Regulation (EU) 2016/679") == {"/eu/reg/2016/679/~art_6"}
    assert hrefs("Article 4 of Regulation (EC) No 593/2008") == {"/eu/reg/2008/593/~art_4"}
    assert hrefs("Article 25(6) of Directive 95/46/EC") == set()  # not ingested
    assert hrefs("Article 234 EC") == set()  # pre-Lisbon numbering is not mapped
    assert hrefs("Articles 7 to 9 of the Charter") == set()  # ranges are not expanded
    assert hrefs("Article 25 of the Charter of the United Nations") == set()
    assert hrefs("Article 8 of the Charter", charter="none") == set()
    # where another Charter is named too, only the full name counts
    assert hrefs("Article 8 of the Charter", charter="full") == set()
    assert hrefs("Article 8 of the Charter of Fundamental Rights of the European Union", charter="full") == {
        "/eu/treaty/char_2016/~art_8"
    }
    assert hrefs("l’article 101 TFUE", "fr") == {"/eu/treaty/tfeu_2016/~art_101"}
    assert hrefs("les articles 7 et 8 de la Charte", "fr") == {
        "/eu/treaty/char_2016/~art_7",
        "/eu/treaty/char_2016/~art_8",
    }
    assert hrefs("l’article 6, paragraphe 1, sous a), du règlement 2016/679", "fr") == {"/eu/reg/2016/679/~art_6"}


# --------------------------------------------------------------------------- parser: digitised Reports


def test_simplified_layout_numbers_points_in_sequence(bundles: dict[str, RawDocument]) -> None:
    doc = _doc(bundles, SIMMENTHAL, "en")
    assert doc.judgment is not None and doc.judgment.docket == "106/77"
    assert doc.judgment.neutral_citation == "ECLI:EU:C:1978:49"
    assert not doc.authoritative  # language of the case: Italian
    assert [p.num for p in doc.paragraphs if p.part == "motivation" and p.num] == [str(n) for n in range(1, 30)]
    assert _para(doc, "para_21").text.startswith("IT FOLLOWS FROM THE FOREGOING THAT EVERY NATIONAL COURT MUST")
    assert _para(doc, "header").text.startswith("IN CASE 106/77")
    assert _para(doc, "subject").text.startswith("ON THE INTERPRETATION OF ARTICLE 189 OF THE EEC TREATY")
    assert [p.part for p in doc.paragraphs if p.part == "decision"] != []
    assert doc.paragraphs[-1].text.startswith("A NATIONAL COURT WHICH IS CALLED UPON")


def test_judgment_without_numbered_points(bundles: dict[str, RawDocument]) -> None:
    doc = _doc(bundles, VAN_GEND, "en")
    assert doc.judgment is not None and doc.judgment.docket == "26-62"
    assert [p for p in doc.paragraphs if p.num] == []
    assert _para(doc, "u_1").part == "motivation"
    assert {p.part for p in doc.paragraphs} == {"header", "motivation", "decision"}
    assert "ARTICLE 12 OF THE TREATY ESTABLISHING THE EUROPEAN ECONOMIC COMMUNITY PRODUCES DIRECT EFFECTS" in " ".join(
        p.text for p in doc.paragraphs if p.part == "decision"
    )


def test_point_numbers_are_the_longest_run_not_quoted_lists() -> None:
    lines = [
        "1. First point.",
        "2. Second point.",
        "Questions:",
        "1. Is a quoted question numbered like a point?",
        "2. May it be so?",
        "3. Third point.",
        "A quotation: 8. Not a point",
        "8. Article 22(8) of the quoted directive",
        "4. Fourth point.",
        "6. Sixth point (the source lost the number 5).",
        "7 Seventh point.",
    ]
    picked = {i: n for i, (n, _) in numbered_lines(lines).items()}
    assert picked == {0: "1", 1: "2", 5: "3", 8: "4", 9: "6", 10: "7"}
    assert numbered_lines(["No numbers here."]) == {}


def test_general_court_judgment(bundles: dict[str, RawDocument]) -> None:
    doc = _doc(bundles, KADI_GC, "en")
    assert doc.judgment is not None
    assert (doc.judgment.court_id, doc.judgment.docket, doc.judgment.neutral_citation) == (
        "EU-GC",
        "T-315/01",
        "ECLI:EU:T:2005:332",
    )
    assert doc.authoritative  # language of the case: English
    assert doc.ext["short_name"] == "Kadi v Council and Commission"
    assert _cite(doc, "para_100") == "Case T-315/01 Kadi v Council and Commission (EU:T:2005:332), para 100"


def test_bad_bundles_are_refused(bundles: dict[str, RawDocument]) -> None:
    raw = bundles[f"cellar-case-law:{SCHREMS}:en"]
    bundle = json.loads(raw.content)
    tampered = copy.deepcopy(bundle)
    tampered["parts"][2]["content"] = tampered["parts"][2]["content"].replace("Schrems", "Schrems!", 1)
    with pytest.raises(EuParseError, match="SHA-256"):
        parse_eu_case_law(json.dumps(tampered).encode())
    legislation = dict(bundle, kind="legislation")
    with pytest.raises(EuParseError, match="not a Cellar case-law bundle"):
        parse_eu_case_law(json.dumps(legislation).encode())
    with pytest.raises(EuParseError, match="not a ULIS Cellar bundle"):
        parse_eu_case_law(b'{"format": "other"}')


def test_court_and_kind_come_from_the_metadata() -> None:
    def rows(**override: str) -> list[dict[str, str]]:
        base = {
            "resource_legal_id_celex": "62014CJ0362",
            "work_has_resource-type": "http://publications.europa.eu/resource/authority/resource-type/JUDG",
            "work_created_by_agent": "http://publications.europa.eu/resource/authority/corporate-body/GCEU",
            "work_date_document": "2015-10-06",
            "case-law_ecli": "ECLI:EU:T:2015:1",
            "case-law_uses_procedure_language": "http://publications.europa.eu/resource/authority/language/ENG",
        }
        base.update(override)
        cdm = "http://publications.europa.eu/ontology/cdm#"
        return [{"work": "w", "p": cdm + k, "o": v} for k, v in base.items()]

    meta = parse_case_metadata(rows(), "62014CJ0362")
    assert (meta.court_id, meta.procedure_language) == ("EU-GC", "en")
    with pytest.raises(EuParseError, match="not a judgment"):
        parse_case_metadata(rows(**{"work_has_resource-type": "http://x/resource-type/ORDER"}), "62014CJ0362")
    with pytest.raises(EuParseError, match="unsupported court"):
        parse_case_metadata(rows(work_created_by_agent="http://x/corporate-body/CST"), "62014CJ0362")


# --------------------------------------------------------------------------- citations


def test_citations_in_english_and_french(bundles: dict[str, RawDocument]) -> None:
    assert _cite(_doc(bundles, SCHREMS, "en"), "para_94") == "Case C-362/14 Schrems (EU:C:2015:650), para 94"
    assert _cite(_doc(bundles, SCHREMS, "fr"), "para_94") == "Affaire C-362/14 Schrems (EU:C:2015:650), point 94"
    assert _cite(_doc(bundles, SCHREMS, "en"), "op_2") == (
        "Case C-362/14 Schrems (EU:C:2015:650), operative part, point 2"
    )
    assert _cite(_doc(bundles, SCHREMS, "fr"), "op_1").endswith(", dispositif, point 1")
    assert _cite(_doc(bundles, DRI, "en"), "para_39") == (
        "Joined Cases C-293/12 and C-594/12 Digital Rights Ireland and Others (EU:C:2014:238), para 39"
    )
    assert _cite(_doc(bundles, DRI, "fr"), "para_39").startswith("Affaires jointes C-293/12 et C-594/12 Digital Rights")
    assert _cite(_doc(bundles, SIMMENTHAL, "en"), "para_21") == "Case 106/77 Simmenthal (EU:C:1978:49), para 21"
    # no point number: the case only
    assert _cite(_doc(bundles, VAN_GEND, "en"), "u_3") == "Case 26-62 Van Gend en Loos (EU:C:1963:1)"


def test_citation_falls_back_to_the_parties_without_a_label() -> None:
    module = get_module("eu")
    c = JudgmentCitationInput(
        "Gerhard Köbler v Republik Österreich.",
        "ECLI:EU:C:2003:513",
        date(2003, 9, 30),
        "C-224/01",
        "EU-CJEU",
        "Court of Justice",
        "127",
        "de",
        "EU",
        ext={"designations": {"en": "Case C-224/01"}},
    )
    assert (
        module.judgment_citation(c)
        == "Rechtssache C-224/01 Gerhard Köbler v Republik Österreich. (EU:C:2003:513), Rn. 127"
    )
    bare = JudgmentCitationInput("X v Y", None, date(2000, 1, 2), None, "EU-CJEU", "Court", None, "en", "EU")
    assert module.judgment_citation(bare) == "X v Y (2000-01-02)"


def test_pipeline_resolves_case_links_and_instrument_links() -> None:
    uri = "http://publications.europa.eu/resource/ecli/ECLI%3AEU%3AC%3A2014%3A238#point39"
    assert pipeline_resolve_uri(uri) == ("/eu/case/ECLI:EU:C:2014:238", "para_39")
    assert pipeline_resolve_uri("http://publications.europa.eu/resource/ecli/ECLI%3AEU%3AC%3A2014%3A238") == (
        "/eu/case/ECLI:EU:C:2014:238",
        None,
    )
    assert pipeline_resolve_uri("/eu/treaty/char_2016/~art_7") == ("/eu/treaty/char_2016", "art_7")
    assert resolve_uri("http://publications.europa.eu/resource/oj/JOL_1995_281_R_TOC") is None


# --------------------------------------------------------------------------- authority


def _authority(catalog: Catalog, target: str, court: str = "EU-CJEU", as_of: date = date(2026, 10, 14)) -> Any:
    return classify_judgment(
        catalog.jurisdictions,
        target=target,
        judgment_jurisdiction="EU",
        court_id=court,
        forum_court=None,
        overruled_by=None,
        as_of=as_of,
    )


def test_cjeu_decisions_are_case_law_of_a_court_of_which_the_state_is_a_member(catalog: Catalog) -> None:
    a = _authority(catalog, "DE")
    assert a.category == "regional_case_law" and a.weight == 1.05
    assert "Case law of the court of a regional organisation" in a.label
    # honest: the weight comes from the EU profile (settled case law, not stare decisis), to verify
    for expected in ("Court of Justice", "settled case law", "not stare decisis", "to verify", "TFEU art 267"):
        assert expected in a.rationale, expected
    assert "binding precedent" in a.rationale  # only to say the weight is not that
    assert not a.category.startswith("binding")
    # the General Court's decisions can be appealed
    gc = _authority(catalog, "DE", "EU-GC")
    assert gc.category == "regional_case_law" and "may be appealed to Court of Justice" in gc.rationale


def test_authority_follows_membership_and_the_organisations_own_system(catalog: Catalog) -> None:
    assert _authority(catalog, "CA").category == "foreign"
    assert _authority(catalog, "GB-ENG", as_of=date(2022, 1, 1)).category == "foreign"  # no longer a member
    assert _authority(catalog, "GB-ENG", as_of=date(2015, 1, 1)).category == "regional_case_law"
    own = _authority(catalog, "EU")
    assert own.category == "organisation_case_law" and own.weight == 1.05
    assert "not stare decisis" in own.rationale and "Persuasive" not in own.label


# --------------------------------------------------------------------------- PostgreSQL


class _Fixtures:
    def __init__(self, docs: dict[str, RawDocument]):
        self.docs = docs

    def fetch(self) -> Iterator[RawDocument]:
        yield from self.docs.values()


@pytest.fixture(scope="module")
def legislation(tmp_path_factory: pytest.TempPathFactory) -> dict[str, RawDocument]:
    cache = _decompressed_copy(LEGISLATION_FIX / "cache", tmp_path_factory.mktemp("legislation") / "cache")
    connector = CellarConnector(LEGISLATION_FIX / "manifest.yaml", "eu-eur-lex", cache, offline=True)
    return {raw.external_id: raw for raw in connector.fetch()}


@pytest.fixture(scope="module")
def case_law_ingested(
    session_factory: sessionmaker[Session],
    catalog: Catalog,
    store: LocalObjectStore,
    ingested: IngestionReport,
    legislation: dict[str, RawDocument],
    bundles: dict[str, RawDocument],
) -> IngestionReport:
    with session_factory() as session:
        # the Charter (and the other fixtures of the EU legislation source) first
        Ingestor(session, catalog, store).ingest_source("eu-eur-lex", actor="pytest", connector=_Fixtures(legislation))
        report = Ingestor(session, catalog, store).ingest_source(
            "eu-cellar-case-law", actor="pytest", connector=_Fixtures(bundles)
        )
        session.commit()
    return report


def _search(session: Session, catalog: Catalog, **kw: Any) -> dict[str, Any]:
    return search(session, catalog, SearchRequest(**kw), today=date(2026, 10, 14))


@pytest.mark.db
def test_ingestion_report(case_law_ingested: IngestionReport) -> None:
    r = case_law_ingested
    assert r.errors == [] and r.conflicts == []
    assert r.documents_seen == 10 and r.documents_new == 10
    assert r.judgments_created == 5
    assert r.paragraphs_created > 600
    assert r.citation_links_created > 100


@pytest.mark.db
def test_search_returns_the_judgment_paragraph_with_citation_and_authority(
    session: Session, catalog: Catalog, case_law_ingested: IngestionReport
) -> None:
    q = QUERY_EN
    res = _search(session, catalog, q=q, jurisdiction="EU", query_language="en", languages=["en"])
    hit = next(h for h in res["results"] if h["kind"] == "judgment_paragraph" and h["unit"]["eid"] == "para_94")
    assert hit["citation"] == "Case C-362/14 Schrems (EU:C:2015:650), para 94"
    assert hit["document"]["court_id"] == "EU-CJEU"
    assert hit["document"]["neutral_citation"] == "ECLI:EU:C:2015:650"
    assert hit["version"]["language"] == "en" and hit["version"]["authoritative"] is True
    assert hit["authority"]["category"] == "organisation_case_law"
    assert hit["text"].startswith("In particular, legislation permitting the public authorities")
    codes = {lim["code"] for lim in hit["limitations"]}
    assert {"AUTHORITY", "CITATOR_COVERAGE", "SOURCE_NOT_AUTHENTIC"} <= codes
    assert any(c["uri"].endswith("ECLI%3AEU%3AC%3A2014%3A238#point39") for c in hit["cites"])


@pytest.mark.db
def test_member_state_search_labels_cjeu_case_law_and_non_members_do_not_get_it(
    session: Session, catalog: Catalog, case_law_ingested: IngestionReport
) -> None:
    q = QUERY_EN

    def schrems(jurisdiction: str, when: date) -> dict[str, Any] | None:
        res = _search(session, catalog, q=q, jurisdiction=jurisdiction, as_of=when, query_language="en")
        return next(
            (h for h in res["results"] if h["unit"]["eid"] == "para_94" and h["kind"] == "judgment_paragraph"), None
        )

    in_germany = schrems("DE", date(2020, 1, 1))
    assert in_germany is not None
    assert in_germany["authority"]["category"] == "regional_case_law"
    assert in_germany["citation"] == "Case C-362/14 Schrems (EU:C:2015:650), para 94"
    assert "to verify" in in_germany["authority"]["rationale"]
    assert schrems("GB-ENG", date(2015, 6, 1)) is not None  # a member then
    assert schrems("GB-ENG", date(2022, 6, 1)) is None  # no longer
    assert schrems("CA", date(2020, 1, 1)) is None  # not a member


@pytest.mark.db
def test_french_search_gives_the_french_text_and_citation(
    session: Session, catalog: Catalog, case_law_ingested: IngestionReport
) -> None:
    res = _search(
        session,
        catalog,
        q=QUERY_FR,
        jurisdiction="EU",
        query_language="fr",
        languages=["fr"],
    )
    hit = next(
        h
        for h in res["results"]
        if h["unit"]["eid"] == "para_94" and h["document"]["neutral_citation"] == "ECLI:EU:C:2015:650"
    )
    assert hit["citation"] == "Affaire C-362/14 Schrems (EU:C:2015:650), point 94"
    assert hit["version"]["language"] == "fr" and hit["version"]["authoritative"] is False
    assert hit["text"].startswith("En particulier, une réglementation permettant aux autorités publiques")


@pytest.mark.db
def test_resolved_and_unresolved_references(
    session: Session, catalog: Catalog, case_law_ingested: IngestionReport
) -> None:
    schrems = session.scalar(select(m.Judgment).where(m.Judgment.uri == "/eu/case/ECLI:EU:C:2015:650"))
    dri = session.scalar(select(m.Judgment).where(m.Judgment.uri == "/eu/case/ECLI:EU:C:2014:238"))
    assert schrems is not None and dri is not None
    links = list(session.scalars(select(m.CitationLink).where(m.CitationLink.from_work_uri == schrems.uri)))

    def target(to_uri_part: str) -> list[m.CitationLink]:
        return [link for link in links if to_uri_part in link.to_uri]

    # a case ingested here: resolved to the judgment
    dri_links = target("ECLI%3AEU%3AC%3A2014%3A238")
    assert dri_links and all(link.to_judgment_id == dri.id and link.to_instrument_id is None for link in dri_links)
    assert {link.extraction_method for link in dri_links} == {"source_markup"}
    # a case not ingested here (Osterreichischer Rundfunk): the link is kept, unresolved
    other = target("ECLI%3AEU%3AC%3A2003%3A294")
    assert other and all(link.to_judgment_id is None and link.to_instrument_id is None for link in other)
    # the Charter is ingested: resolved to the provision, by a pattern in the text
    charter = [link for link in target("/eu/treaty/char_2016/~art_8") if link.extraction_method == "pattern"]
    assert charter
    for link in charter:
        assert link.to_instrument_id is not None and link.to_provision_id is not None
        provision = session.get(m.Provision, link.to_provision_id)
        assert provision is not None and provision.eid == "art_8"
    # the TFEU is not ingested in these fixtures: unresolved
    tfeu = target("/eu/treaty/tfeu_2016/~art_16")
    assert tfeu and all(link.to_instrument_id is None and link.to_provision_id is None for link in tfeu)
    assert {link.relation for link in links} == {"cites"}


@pytest.mark.db
def test_cited_judgment_shows_who_cites_it(
    session: Session, catalog: Catalog, case_law_ingested: IngestionReport
) -> None:
    res = _search(
        session,
        catalog,
        q="retention of data traffic data location data invalid directive 2006/24",
        jurisdiction="EU",
        query_language="en",
        languages=["en"],
    )
    hits = [h for h in res["results"] if h["document"].get("neutral_citation") == "ECLI:EU:C:2014:238"]
    assert hits
    citing = " ".join(lim["message"] for h in hits for lim in h["limitations"] if lim["code"] == "SUBSEQUENT_TREATMENT")
    assert "Case C-362/14 Schrems (EU:C:2015:650)" in citing


@pytest.mark.db
def test_judgment_detail_lists_both_language_versions(
    session: Session, catalog: Catalog, case_law_ingested: IngestionReport
) -> None:
    detail = judgment_detail(session, catalog, "/eu/case/ECLI:EU:C:2015:650")
    assert detail["citation"] == "Case C-362/14 Schrems (EU:C:2015:650)"
    assert detail["court"]["id"] == "EU-CJEU" and detail["decision_date"] == "2015-10-06"
    versions = {v["language"]: v for v in detail["versions"]}
    assert set(versions) == {"en", "fr"}
    assert (versions["en"]["authoritative"], versions["fr"]["authoritative"]) == (True, False)
    pins = {lang: next(p["citation"] for p in v["paragraphs"] if p["eid"] == "para_94") for lang, v in versions.items()}
    assert pins == {
        "en": "Case C-362/14 Schrems (EU:C:2015:650), para 94",
        "fr": "Affaire C-362/14 Schrems (EU:C:2015:650), point 94",
    }
    assert [p["eid"] for p in versions["en"]["paragraphs"]][:3] == ["header", "index", "para_1"]

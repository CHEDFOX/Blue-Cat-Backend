"""CourtListener bulk: fourteen real cases (tests/fixtures/courtlistener_bulk/, see its README), parser and pipeline.

Unit tests check what the parser makes of each case (units, citations, courts, dates). PostgreSQL tests ingest them
through the licence-gated pipeline with an in-memory connector, search for known passages, and check the exact citation,
the unit label, the authority and the limitations; a re-run is unchanged and every stored unit verifies against its raw
bundle.
"""

from __future__ import annotations

import gzip
import html
import json
import re
from collections.abc import Iterator
from datetime import UTC, date, datetime
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ulis.catalog import Catalog
from ulis.db import models as m
from ulis.ingestion.pipeline import IngestionReport, Ingestor
from ulis.retrieval.search import SearchRequest, search
from ulis.settings import REPO_ROOT
from ulis.sources.connectors.base import RawDocument
from ulis.sources.parsers import courtlistener_bulk as p
from ulis.storage.object_store import LocalObjectStore
from ulis.verification.integrity import verify_source_document

SOURCE = "us-courtlistener-bulk-s3"
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "courtlistener_bulk" / "bundles.json.gz"
MILLER, FEX, WALLACE, ALTFELIX, WATER, MOORE, WYATT, BENJAMIN, HAWAII, MARKS, ARK, BANKS, TEN_BARRELS, KIOWA = (
    8195937,
    112819,
    7297270,
    8379209,
    4911766,
    7550104,
    2334896,
    3388412,
    4387452,
    1232642,
    4382808,
    4041673,
    8703738,
    7471795,
)


def load() -> dict[int, dict[str, Any]]:
    with gzip.open(FIXTURE, "rt", encoding="utf-8") as handle:
        return {b["cluster"]["id"]: b for b in json.load(handle)}


BUNDLES = load()


def parse(cluster_id: int, **changes: Any) -> Any:
    bundle = json.loads(json.dumps(BUNDLES[cluster_id]))
    bundle.update(changes)
    return p.parse(p.bundle_bytes(bundle))[0]


# --------------------------------------------------------------------------- parser, case by case


def test_supreme_court_case_has_one_unit_per_printed_page_and_the_court_s_citation() -> None:
    doc = parse(MILLER)
    j = doc.judgment
    assert j is not None
    assert (j.court_id, j.neutral_citation, j.case_name, j.decision_date, j.docket) == (
        "US-SC",
        "317 U.S. 601",
        "Miller v. United States",
        date(1942, 12, 14),
        "No. 76",
    )
    assert [(x.num, x.part) for x in doc.paragraphs] == [("601", "Lead opinion"), ("602", "Lead opinion")]
    assert doc.paragraphs[0].text.startswith(
        "It is ordered that the opinion filed December 7, 1942, be amended as follows:"
    )
    assert doc.paragraphs[1].text.endswith("Opinion reported as amended, ante, p. 192.")
    assert doc.work_uri == f"/us/case/cl/{MILLER}" and doc.expression_uri == f"/us/case/cl/{MILLER}/eng@1942-12-14"
    assert doc.ext["unit_scheme"] == "page" and doc.ext["page_citation"] == "317 U.S. 601" and doc.ext["ocr"] is True
    assert doc.ext["courtlistener_court"] == "scotus" and doc.ext["text_columns"] == ["xml_harvard"]


def test_parallel_citations_pages_of_another_reporter_and_two_opinions() -> None:
    doc = parse(KIOWA)
    j = doc.judgment
    assert j is not None and j.neutral_citation == "523 U.S. 751"  # the official reports first
    assert doc.ext["citations"] == ["118 S. Ct. 1700", "523 U.S. 751"]
    assert doc.ext["page_citation"] == "118 S. Ct. 1700"  # the text paginates the Supreme Court Reporter
    parts = [x.part for x in doc.paragraphs]
    assert parts[0] == "Lead opinion (Kennedy)" and parts[-1].startswith("Dissenting opinion")
    assert [x.num for x in doc.paragraphs][:4] == ["1702", "1703", "1704", "1705"]
    assert doc.ext["opinion_ids"] == [7393423, 7393424]
    assert "tribal immunity from suit" in " ".join(x.text for x in doc.paragraphs)


def test_a_court_of_appeals_order_and_a_district_court_opinion() -> None:
    wallace = parse(WALLACE)
    assert wallace.judgment is not None
    assert (wallace.judgment.court_id, wallace.judgment.neutral_citation) == ("US-CAFC", "55 F. App'x 548")
    assert wallace.paragraphs[1].text == "for failure to prosecute in accordance with the rules."
    barrels = parse(TEN_BARRELS)
    assert barrels.judgment is not None and barrels.judgment.court_id == "US-CL-FLND"
    assert barrels.ext["court_name"] == "District Court, N.D. Florida" and barrels.ext["court_citation"] == "N.D. Fla."
    assert barrels.paragraphs[0].text.startswith("[Nowhere reported. The records of the court")


def test_state_courts_use_the_existing_apex_ids_and_keep_the_reporter_the_text_paginates() -> None:
    water = parse(WATER)
    assert water.judgment is not None and water.judgment.court_id == "US-WA-SC"
    assert water.judgment.neutral_citation == "65 Wash. 2d 392" and water.ext["citations"][1] == "397 P.2d 845"
    assert water.paragraphs[0].text.startswith("Per Curiam.")
    wyatt = parse(WYATT)
    assert wyatt.judgment is not None and wyatt.judgment.neutral_citation == "597 Pa. 56"
    assert wyatt.ext["page_citation"] == "950 A.2d 263"  # Lawbox star pages of the regional reporter
    assert [x.num for x in wyatt.paragraphs] == ["263", "264"]
    altfelix = parse(ALTFELIX)
    assert altfelix.judgment is not None and altfelix.judgment.court_id == "US-CL-OHIO"
    # the printed word "Judgment" is split by the page break in the source; the text is kept as the source gives it
    assert [x.text[:6] for x in altfelix.paragraphs] == ["Judg", "ment a"]


def test_neutral_citations_docket_only_cases_and_the_syllabus() -> None:
    marks, ark, banks = parse(MARKS), parse(ARK), parse(BANKS)
    assert [d.ext["citation_kind"] for d in (marks, ark, banks)] == ["neutral"] * 3
    assert [d.judgment.neutral_citation for d in (marks, ark, banks) if d.judgment] == [
        "2006 WI 108",
        "2017 Ark. 132",
        "1910 OK 513",
    ]
    assert marks.judgment is not None and marks.judgment.docket == "2005AP2329"
    hawaii = parse(HAWAII)
    assert hawaii.judgment is not None and hawaii.judgment.neutral_citation is None
    assert (
        hawaii.ext["citation_kind"] == "docket"
        and hawaii.judgment.docket == "CAAP-15-0000466"
        and hawaii.number == "CAAP-15-0000466"
    )
    assert [x.part for x in banks.paragraphs] == ["Syllabus", "Lead opinion (Williams)"]
    assert "(Syllabus by the Court.)" in banks.paragraphs[0].text and banks.ext["disposition"] == "Dismissed."


def test_two_opinions_of_one_case_are_labelled_parts_in_the_publishers_order() -> None:
    doc = parse(MOORE)
    assert [(x.num, x.part) for x in doc.paragraphs] == [("1", "Lead opinion"), ("2", "Concurring opinion (Tate)")]
    assert doc.paragraphs[0].text.endswith("Application denied for the reasons stated by the trial judge.")
    assert doc.ext["judges"] == "Tate" and doc.ext["opinion_ids"] == sorted(doc.ext["opinion_ids"])


def squash(markup: str) -> str:
    """The characters of a text without mark-up and white space: a unit must be found in the source in this form."""
    markup = re.sub(r"<(footnotemark|page-number|headnotes)\b[^>]*>.*?</\1>", "", markup, flags=re.S)  # not text
    return re.sub(r"\s+", "", html.unescape(re.sub(r"<[^>]*>", "", markup)))


@pytest.mark.parametrize("cluster_id", sorted(BUNDLES))
def test_nothing_is_invented_every_paragraph_of_every_unit_is_in_the_source(cluster_id: int) -> None:
    bundle = BUNDLES[cluster_id]
    doc = parse(cluster_id)
    source = "".join(squash(op["text"]) for op in bundle["opinions"]) + squash(bundle["cluster"]["syllabus"])
    for x in doc.paragraphs:
        assert x.text.strip() == x.text and x.text and len(x.part) <= 32 and len(x.num or "") <= 20
        for piece in x.text.split("\n\n"):
            # a footnote starts with its number, which the parser takes from the footnote's label attribute
            assert squash(piece) in source or squash(re.sub(r"^\d+\.?\s*", "", piece)) in source, (x.eid, piece[:80])
    assert [x.sort_order for x in doc.paragraphs] == list(range(1, len(doc.paragraphs) + 1))
    assert doc.ext["courtlistener_cluster"] == cluster_id and doc.authoritative is False
    # a page scheme leaves no star-page mark of the source in any unit
    if doc.ext["unit_scheme"] == "page":
        marks = set(re.findall(r"\*\d+", " ".join(op["text"] for op in bundle["opinions"])))
        assert not any(mark in x.text for mark in marks for x in doc.paragraphs)


# --------------------------------------------------------------------------- pipeline and search


class Bundles:
    """An in-memory connector over the fixture bundles (plus optional extra bundles)."""

    fetch_errors: list[str] = []

    def __init__(self, extra: list[dict[str, Any]] | None = None) -> None:
        self.extra = extra or []

    def fetch(self) -> Iterator[RawDocument]:
        for bundle in [*BUNDLES.values(), *self.extra]:
            cluster = bundle["cluster"]["id"]
            suffix = f":{bundle['opinions'][0]['id']}" if bundle.get("continuation") else ""
            yield RawDocument(
                f"cl:cluster:{cluster}{suffix}",
                f"https://com-courtlistener-storage.s3-us-west-2.amazonaws.com/bulk-data/opinions-2026-09-30.csv.bz2#cluster-{cluster}",
                "application/json",
                p.bundle_bytes(bundle),
                datetime(2026, 10, 16, 8, 0, tzinfo=UTC),
                {"cluster_id": cluster},
            )


SPLIT_ID = 999_000_001


def split_kiowa() -> list[dict[str, Any]]:
    """Kiowa under another cluster id as the connector hands it over when the dissent row arrives late: the lead
    opinion first, the dissent in a second bundle of the same case with the continuation flag (and no syllabus)."""
    first = json.loads(json.dumps(BUNDLES[KIOWA]))
    first["cluster"]["id"] = SPLIT_ID
    second = json.loads(json.dumps(first))
    first["opinions"] = [o for o in first["opinions"] if o["type"] == "020lead"]
    second["opinions"] = [o for o in second["opinions"] if o["type"] == "040dissent"]
    second["continuation"] = True
    second["cluster"]["syllabus"] = ""
    return [first, second]


@pytest.fixture(scope="module")
def cases(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, ingested: IngestionReport
) -> IngestionReport:
    with session_factory() as session:
        report = Ingestor(session, catalog, store).ingest_source(SOURCE, actor="pytest", connector=Bundles())
        session.commit()
    return report


def find(session: Session, catalog: Catalog, q: str, **kw: Any) -> dict[str, Any]:
    result = search(session, catalog, SearchRequest(jurisdiction="US", q=q, **kw), today=date(2026, 10, 16))
    return result["results"][0]  # type: ignore[no-any-return]


@pytest.mark.db
def test_ingest_reports_every_case_and_every_unit(cases: IngestionReport) -> None:
    assert cases.errors == [] and cases.conflicts == [] and cases.documents_skipped == 0
    assert cases.documents_seen == cases.documents_new == cases.judgments_created == len(BUNDLES)
    units = sum(len(parse(c).paragraphs) for c in BUNDLES)
    assert cases.paragraphs_created == cases.passages_indexed == units


@pytest.mark.db
def test_search_finds_a_supreme_court_page_with_the_exact_citation_and_pinpoint(
    session: Session, catalog: Catalog, cases: IngestionReport
) -> None:
    hit = find(session, catalog, "insert a period after the word transcribed")
    assert (
        hit["kind"] == "judgment_paragraph" and hit["citation"] == "Miller v. United States, 317 U.S. 601, 601 (1942)"
    )
    assert hit["unit"]["label"] == "p. 601" and hit["document"]["court_id"] == "US-SC"
    assert hit["document"]["neutral_citation"] == "317 U.S. 601" and hit["document"]["decision_date"] == "1942-12-14"
    assert hit["version"]["authoritative"] is False and hit["authority"]["category"] == "precedent_vertical"
    assert "CITATOR_COVERAGE" in {lim["code"] for lim in hit["limitations"]}
    bound = find(session, catalog, "insert a period after the word transcribed", forum_court="US-CA9")
    assert bound["authority"]["category"] == "binding_precedent"


@pytest.mark.db
def test_parallel_citation_carries_the_pinpoint_of_the_reporter_whose_pages_the_text_has(
    session: Session, catalog: Catalog, cases: IngestionReport
) -> None:
    hit = find(session, catalog, "tribal immunity from suit without drawing a distinction based on where the tribe")
    assert hit["citation"] == (
        "Kiowa Tribe of Oklahoma v. Manufacturing Technologies, Inc., 523 U.S. 751, 118 S. Ct. 1700, 1703 (1998)"
    )
    assert hit["unit"]["label"] == "p. 1703" and hit["unit"]["heading"] == "Lead opinion (Kennedy)"


@pytest.mark.db
def test_state_court_case_is_cited_with_its_court_and_year_and_ranks_by_the_state_hierarchy(
    session: Session, catalog: Catalog, cases: IngestionReport
) -> None:
    hit = find(
        session, catalog, "Petition for Writ of Mandamus and/or Extraordinary Relief is denied Process is granted"
    )
    assert hit["citation"] == "Wyatt v. Court of Common Pleas, 597 Pa. 56, 950 A.2d 263, 264 (Pa. 2008)"
    assert hit["unit"]["label"] == "p. 264" and hit["document"]["court_id"] == "US-CL-PA"
    assert hit["authority"]["category"] == "precedent_vertical"  # the State's intermediate courts appeal to it
    below = find(session, catalog, "Process is granted Petition for Writ of Mandamus", forum_court="US-CL-PASUPERCT")
    other = find(session, catalog, "Process is granted Petition for Writ of Mandamus", forum_court="US-CA9")
    assert below["authority"]["category"] == "binding_precedent"
    assert other["authority"]["category"] == "persuasive_precedent"


@pytest.mark.db
def test_neutral_citation_syllabus_and_block_units(session: Session, catalog: Catalog, cases: IngestionReport) -> None:
    hit = find(session, catalog, "APPEAL AND ERROR Dismissal Failure to File Brief Syllabus by the Court")
    assert hit["citation"] == "Banks v. Clark, 1910 OK 513" and hit["document"]["court_id"] == "US-CL-OKLA"
    assert hit["unit"]["label"] == "Syllabus, text block 1" and hit["unit"]["heading"] == "Syllabus"
    docket = find(session, catalog, "Electronically Filed Intermediate Court of Appeals CAAP-15-0000466")
    assert docket["citation"] == (
        "The Trustees of the Estate of Bernice Pauahi Bishop v. Au, No. CAAP-15-0000466 (Haw. App. Apr. 28, 2017)"
    )


@pytest.mark.db
def test_decision_after_the_requested_date_is_flagged(
    session: Session, catalog: Catalog, cases: IngestionReport
) -> None:
    hit = find(
        session, catalog, "insert a period after the word transcribed", as_of=date(1900, 1, 1), include_historical=True
    )
    assert "DECIDED_AFTER_DATE" in {lim["code"] for lim in hit["limitations"]}


@pytest.mark.db
def test_stored_units_verify_against_their_raw_bundles_and_a_rerun_is_unchanged(
    session: Session,
    session_factory: sessionmaker[Session],
    catalog: Catalog,
    store: LocalObjectStore,
    cases: IngestionReport,
) -> None:
    ids = list(session.scalars(select(m.SourceDocument.id).where(m.SourceDocument.source_id == SOURCE)))
    assert len(ids) == len(BUNDLES)
    for doc_id in ids:
        result = verify_source_document(session, catalog, store, doc_id, checker="pytest")
        assert result["result"] == "pass" and result["problems"] == [], result
    with session_factory() as other:
        again = Ingestor(other, catalog, store).ingest_source(SOURCE, actor="pytest", connector=Bundles())
        other.rollback()
    assert again.documents_unchanged == len(BUNDLES) and again.documents_new == 0 and again.errors == []


@pytest.mark.db
def test_a_late_opinion_of_a_case_is_a_second_version_of_the_same_judgment(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, cases: IngestionReport
) -> None:
    with session_factory() as session:
        report = Ingestor(session, catalog, store).ingest_source(
            SOURCE, actor="pytest", connector=Bundles(split_kiowa())
        )
        assert report.errors == [] and report.documents_new == 2 and report.judgments_created == 1
        judgment = session.scalars(select(m.Judgment).where(m.Judgment.uri == f"/us/case/cl/{SPLIT_ID}")).one()
        versions = session.scalars(
            select(m.JudgmentVersion)
            .where(m.JudgmentVersion.judgment_id == judgment.id)
            .order_by(m.JudgmentVersion.uri)
        ).all()
        assert [v.uri for v in versions] == [
            f"/us/case/cl/{SPLIT_ID}/eng@1998-05-26",
            f"/us/case/cl/{SPLIT_ID}/eng@1998-05-26+7393424",
        ]
        parts = session.scalars(
            select(m.JudgmentParagraph.part).join(m.JudgmentVersion).where(m.JudgmentVersion.judgment_id == judgment.id)
        ).all()
        assert sorted(set(parts)) == ["Dissenting opinion (Stevens)", "Lead opinion (Kennedy)"]
        session.rollback()

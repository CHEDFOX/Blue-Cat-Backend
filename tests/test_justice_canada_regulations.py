"""Consolidated federal regulations of Canada (Justice Laws XML): parser, citations, search (real files, OGL)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

from ulis.catalog import Catalog
from ulis.ingestion.pipeline import IngestionReport, Ingestor
from ulis.jurisdictions.modules import ProvisionCitationInput, get_module
from ulis.retrieval.lookup import provision_at
from ulis.retrieval.search import SearchRequest, search
from ulis.settings import REPO_ROOT
from ulis.sources.connectors.base import RawDocument
from ulis.sources.normalized import ParseSkipped
from ulis.sources.parsers.justice_canada import (
    parse_justice_canada,
    regulation_number,
    regulation_uri,
    resolve_uri,
)

FIX = REPO_ROOT / "tests" / "fixtures" / "justice_canada"
EN = FIX / "eng" / "regulations"
FR = FIX / "fra" / "reglements"
LEAMY_EN, LEAMY_FR = EN / "SOR-98-547.xml", FR / "DORS-98-547.xml"
POSTAL_EN, POSTAL_FR = EN / "C.R.C.,_c._1274.xml", FR / "C.R.C.,_ch._1274.xml"
WAIVER_EN, WAIVER_FR = EN / "SI-2000-82.xml", FR / "TR-2000-82.xml"
STUB_EN, STUB_FR = EN / "SOR-78-148.xml", FR / "DORS-78-148.xml"
ACT_STUB_EN, ACT_STUB_FR = FIX / "eng" / "acts" / "F-12.xml", FIX / "fra" / "lois" / "F-12.xml"


def _doc(path: Path):  # type: ignore[no-untyped-def]
    docs = parse_justice_canada(path.read_bytes())
    assert len(docs) == 1
    return docs[0]


def _cite(doc, eid: str) -> str:  # type: ignore[no-untyped-def]
    p = {x.eid: x for x in doc.provisions}[eid]
    return get_module("ca").provision_citation(
        ProvisionCitationInput(
            doc.title, doc.instrument_type, "CA", p.top_kind, p.num_path, doc.language, ext=doc.ext, eid=p.eid
        )
    )


def test_sor_regulation_english() -> None:
    doc = _doc(LEAMY_EN)
    assert (doc.instrument_type, doc.work_uri, doc.language) == ("regulation", "/ca/regulation/sor-98-547", "en")
    assert doc.expression_uri == "/ca/regulation/sor-98-547/eng@2007-05-31"
    assert doc.title == "Leamy Lake Navigation Channel Regulations" and doc.number == "SOR/98-547"
    assert doc.text_origin == "official_consolidation" and doc.authoritative
    # Dates come from the source: registration (work), point in time (version), "current to" (currency).
    assert (doc.work_date, doc.version_date, doc.currency_date) == (
        date(1998, 10, 29),
        date(2007, 5, 31),
        date(2025, 7, 24),
    )
    assert doc.ext == {
        "instrument_number": "SOR/98-547",
        "instrument_series": "SOR",
        "registration_date": "1998-10-29",
        "enabling_acts": ["N-4"],
        "order_number": "1998-1930",
        "order_date": "1998-10-29",
    }
    p = {x.eid: x for x in doc.provisions}
    # The making order is one unnumbered provision; it links the regulation to the Act it is made under.
    order = doc.provisions[0]
    assert order.eid == "order" and order.kind == "order" and order.num_path == [] and order.is_leaf
    assert order.text.startswith(
        "His Excellency the Governor General in Council, on the recommendation of the Minister"
    )
    assert [(r.relation, r.href) for r in order.refs][:2] == [
        ("made_under", "https://laws-lois.justice.gc.ca/eng/acts/N-4/"),
        ("cites", "https://laws-lois.justice.gc.ca/eng/acts/N-4/"),
    ]
    assert p["section-2"].text.startswith("These Regulations do not apply to peace officers or Commission employees")
    assert p["section-2"].valid_from == date(2006, 3, 22) and p["section-2"].status == "in_force"
    assert p["section-4"].context_heading == "Prohibitions and Obligations"
    # A repealed section and a repealed definition keep only the repeal notice, as a note.
    assert p["section-3"].status == "repealed" and p["section-3"].text == ""
    assert p["section-3"].note == "[Repealed, SOR/2007-113, s. 2]" and p["section-3"].valid_from == date(2007, 5, 31)
    repealed_def = p["section-1-def-authorized-vessel"]
    assert repealed_def.status == "repealed" and repealed_def.text == ""
    assert repealed_def.note == "[Repealed, SOR/2007-113, s. 1]"
    assert p["section-1-def-peace-officer-a"].text.startswith("a member of the Royal Canadian Mounted Police")
    assert p["schedule-1"].heading == "Leamy Lake Navigation Channel" and p["schedule-1"].text.startswith(
        "That portion of Leamy Lake, in the City of Gatineau"
    )
    assert [e.event_type for e in doc.events] == ["version_change"]
    assert _cite(doc, "section-2") == "Leamy Lake Navigation Channel Regulations, SOR/98-547, s 2"
    assert _cite(doc, "section-1-def-peace-officer-a") == (
        "Leamy Lake Navigation Channel Regulations, SOR/98-547, s 1 “peace officer”(a)"
    )
    assert _cite(doc, "schedule-1") == "Leamy Lake Navigation Channel Regulations, SOR/98-547, Sch 1"
    assert _cite(doc, "order") == "Leamy Lake Navigation Channel Regulations, SOR/98-547"


def test_french_version_shares_identifiers_and_cites_dors() -> None:
    en, fr = _doc(LEAMY_EN), _doc(LEAMY_FR)
    assert fr.language == "fr" and fr.work_uri == en.work_uri and fr.number == "SOR/98-547"
    assert fr.expression_uri == "/ca/regulation/sor-98-547/fra@2007-05-31" and fr.ext == en.ext
    assert fr.title == "Règlement sur le chenal de navigation du lac Leamy"
    en_ids, fr_ids = {p.eid for p in en.provisions}, {p.eid for p in fr.provisions}
    assert {
        "order",
        "section-2",
        "section-3",
        "section-1-def-peace-officer-a",
        "schedule-1",
        "schedule-2",
    } <= en_ids & fr_ids
    # The repealed definition lost its paired term, so each language keeps its own identifier for it.
    assert en_ids - fr_ids == {"section-1-def-authorized-vessel"}
    assert fr_ids - en_ids == {"section-1-def-fr-embarcation-autorisee"}
    q = {x.eid: x for x in fr.provisions}
    assert q["section-3"].status == "repealed" and q["section-3"].note == "[Abrogé, DORS/2007-113, art. 2]"
    assert _cite(fr, "section-2") == "Règlement sur le chenal de navigation du lac Leamy, DORS/98-547, art 2"
    assert _cite(fr, "section-1-def-peace-officer-a").endswith("DORS/98-547, art 1 « agent de la paix » a)")
    assert _cite(fr, "schedule-1") == "Règlement sur le chenal de navigation du lac Leamy, DORS/98-547, ann 1"


def test_consolidated_regulations_of_canada_1978() -> None:
    en, fr = _doc(POSTAL_EN), _doc(POSTAL_FR)
    assert en.work_uri == fr.work_uri == "/ca/regulation/crc-c-1274"
    assert en.number == fr.number == "C.R.C., c. 1274" and en.ext["instrument_series"] == "CRC"
    assert en.work_date is None and "registration_date" not in en.ext  # the C.R.C. gives no registration date
    assert en.ext["enabling_acts"] == ["C-10"] and en.title == "Armed Forces Postal Regulations"
    p = {x.eid: x for x in en.provisions}
    assert en.provisions[0].eid == "section-1" and en.provisions[0].refs[0].relation == "made_under"
    assert p["section-4.1"].status == "repealed" and p["section-4.1"].note == "[Repealed, SOR/87-685, s. 2]"
    # A schedule that is a table keeps its rows; a repealed schedule keeps only the notice.
    assert p["schedule"].text.startswith("Item | Column I | Column II")
    assert p["schedule-i"].status == "repealed" and p["schedule-i"].text == ""
    assert _cite(en, "section-4") == "Armed Forces Postal Regulations, CRC, c 1274, s 4"
    assert _cite(fr, "section-4") == "Règlement des postes pour les forces armées, CRC, c 1274, art 4"
    assert _cite(en, "schedule-i") == "Armed Forces Postal Regulations, CRC, c 1274, Sch I"


def test_statutory_instrument_without_body() -> None:
    en, fr = _doc(WAIVER_EN), _doc(WAIVER_FR)
    assert en.work_uri == fr.work_uri == "/ca/regulation/si-2000-82" and en.ext["instrument_series"] == "SI"
    assert [p.eid for p in en.provisions] == [p.eid for p in fr.provisions] == ["order", "schedule"]
    assert en.provisions[0].text.startswith("Her Excellency the Governor General in Council")
    assert en.provisions[0].valid_from == date(2006, 3, 22) and en.work_date == date(2000, 9, 13)
    assert en.provisions[1].text.startswith("The whole of Lot 13, Block 12B")
    assert _cite(en, "order") == "Reservation to the Crown Waiver Order (Tuktoyaktuk, N.W.T.), SI/2000-82"
    assert _cite(fr, "schedule") == (
        "Décret sur la renonciation aux terres réservées à la Couronne (Tuktoyaktuk, T.N.-O.), TR/2000-82, ann"
    )


def test_wholly_repealed_instruments_hold_nothing_to_ingest() -> None:
    with pytest.raises(ParseSkipped, match=r"repeal notice \[Repealed, 2018, c\. 31, s\. 398\]"):
        parse_justice_canada(STUB_EN.read_bytes())
    with pytest.raises(ParseSkipped, match="Abrogé"):
        parse_justice_canada(STUB_FR.read_bytes())
    # The same for the 18 Acts of the repository that hold only a repeal notice.
    with pytest.raises(ParseSkipped, match=r"\[Repealed, 2012, c\. 24, s\. 76\]"):
        parse_justice_canada(ACT_STUB_EN.read_bytes())
    with pytest.raises(ParseSkipped):
        parse_justice_canada(ACT_STUB_FR.read_bytes())


def test_instrument_numbers_are_language_neutral() -> None:
    assert regulation_number("DORS/2001-227") == "SOR/2001-227" and regulation_number("TR/2000-100") == "SI/2000-100"
    assert regulation_number("C.R.C., ch. 870") == "C.R.C., c. 870" and regulation_number("DORS-81-33a") == "SOR-81-33a"
    assert regulation_number("2010, ch. 12, art. 91") == "2010, c. 12, s. 91"
    assert regulation_number("SI/2006–16") == "SI/2006-16" and regulation_number("C.P. 1945-7421") == "P.C. 1945-7421"
    assert regulation_uri("SOR/2001-227") == regulation_uri("DORS-2001-227") == "/ca/regulation/sor-2001-227"
    assert regulation_uri("C.R.C., c. 870") == regulation_uri("C.R.C.,_ch._870") == "/ca/regulation/crc-c-870"
    assert regulation_uri("2010, ch. 12, art. 91") == "/ca/regulation/2010-c-12-s-91"
    assert resolve_uri("https://laws-lois.justice.gc.ca/eng/regulations/SOR-2001-227/") == (
        "/ca/regulation/sor-2001-227",
        None,
    )
    assert resolve_uri("https://laws-lois.justice.gc.ca/fra/reglements/DORS-2001-227/index.html") == (
        "/ca/regulation/sor-2001-227",
        None,
    )
    assert resolve_uri("https://laws-lois.justice.gc.ca/fra/reglements/C.R.C.,_ch._870/") == (
        "/ca/regulation/crc-c-870",
        None,
    )
    assert resolve_uri("https://laws-lois.justice.gc.ca/eng/acts/N-4/") == ("/ca/act/n-4", None)


# --------------------------------------------------------------------------- PostgreSQL


class _Fixtures:
    FILES = (
        ("eng", LEAMY_EN),
        ("fra", LEAMY_FR),
        ("eng", POSTAL_EN),
        ("fra", POSTAL_FR),
        ("eng", WAIVER_EN),
        ("fra", WAIVER_FR),
        ("eng", STUB_EN),
        ("fra", STUB_FR),
        ("eng", ACT_STUB_EN),
        ("fra", ACT_STUB_FR),
    )

    def fetch(self) -> Iterator[RawDocument]:
        now = datetime(2026, 10, 18, tzinfo=UTC)
        for lang, path in self.FILES:
            yield RawDocument(
                f"justice-canada:{lang}:{path.stem}",
                f"file://{path.name}",
                "application/xml",
                path.read_bytes(),
                now,
                {},
            )


@pytest.fixture(scope="module")
def ca_regulations(
    session_factory: sessionmaker[Session], catalog: Catalog, store: Any, ingested: IngestionReport
) -> IngestionReport:
    with session_factory() as session:
        report = Ingestor(session, catalog, store).ingest_source(
            "ca-justice-laws-xml", actor="pytest", connector=_Fixtures()
        )
        session.commit()
    return report


def _search(session: Session, catalog: Catalog, **kw: Any) -> dict[str, Any]:
    return search(session, catalog, SearchRequest(jurisdiction="CA", **kw), today=date(2026, 10, 18))


@pytest.mark.db
def test_regulations_ingest_and_search(session: Session, catalog: Catalog, ca_regulations: IngestionReport) -> None:
    report = ca_regulations
    assert report.errors == [] and report.conflicts == []
    assert (report.instruments_created, report.instrument_versions_created) == (3, 6)
    # The four stubs (a repealed regulation and a repealed Act, both languages) are skipped, not errors.
    assert report.documents_skipped == 4 and all("repeal notice" in s for s in report.skipped)
    assert report.citation_links_created >= 3  # made_under links to the enabling Acts, at least
    res = _search(
        session,
        catalog,
        q="peace officers or Commission employees acting in the course of their employment",
        as_of=date(2025, 1, 1),
        query_language="en",
    )
    hit = next(h for h in res["results"] if h["citation"].endswith(", s 2"))
    assert hit["citation"] == "Leamy Lake Navigation Channel Regulations, SOR/98-547, s 2"
    assert hit["document"]["type"] == "regulation"
    codes = {lim["code"] for lim in hit["limitations"]}
    assert {"OFFICIAL_CONSOLIDATION_REPRODUCED", "PROFILE_UNVERIFIED", "EQUALLY_AUTHENTIC_VERSIONS"} <= codes
    fr = _search(
        session,
        catalog,
        q="les agents de la paix et les employés de la Commission",
        as_of=date(2025, 1, 1),
        query_language="fr",
    )
    first = next(h for h in fr["results"] if h["citation"].endswith(", art 2"))
    assert first["citation"] == "Règlement sur le chenal de navigation du lac Leamy, DORS/98-547, art 2"
    assert first["version"]["language"] == "fr"
    crc = _search(session, catalog, q="Armed Forces Postal Regulations", as_of=date(2025, 1, 1), query_language="en")
    assert any(h["citation"] == "Armed Forces Postal Regulations, CRC, c 1274, s 1" for h in crc["results"])


@pytest.mark.db
def test_repealed_regulation_provision(session: Session, catalog: Catalog, ca_regulations: IngestionReport) -> None:
    gone = provision_at(session, catalog, "/ca/regulation/sor-98-547/~section-3", date(2025, 1, 1), language="en")
    assert gone["status_on_date"] == "repealed" and gone["provision"]["citation"].endswith("SOR/98-547, s 3")
    live = provision_at(session, catalog, "/ca/regulation/sor-98-547/~section-2", date(2025, 1, 1), language="fr")
    assert live["status_on_date"] == "in_force"
    assert live["provision"]["citation"] == "Règlement sur le chenal de navigation du lac Leamy, DORS/98-547, art 2"
    assert live["instrument"]["type"] == "regulation"

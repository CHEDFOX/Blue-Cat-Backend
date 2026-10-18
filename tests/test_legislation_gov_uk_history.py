"""legislation.gov.uk: the base date of the revised text, Acts without sections, enacted and made versions, derived
validity.

Real documents (Open Government Licence v3.0, retrieved 2026-10-16 and stored gzip-compressed): the Gaming (Amendment)
Act 1990
(repealed; its first version is the base date 1991-02-01, the second the repeal on 2007-09-01), an Act of the Old
Scottish
Parliament of 1487 (the whole Act is one paragraph of text), the Northern Ireland Assembly's Act 2020 c. 4 as enacted,
and a
Northern Ireland Order in Council of 1989 as made.
"""

from __future__ import annotations

import gzip
from collections.abc import Iterator
from datetime import UTC, date, datetime

import pytest
from sqlalchemy.orm import Session, sessionmaker

from ulis.catalog import Catalog
from ulis.ingestion.pipeline import IngestionReport, Ingestor
from ulis.retrieval.lookup import provision_at
from ulis.settings import REPO_ROOT
from ulis.sources.connectors.base import RawDocument
from ulis.sources.parsers.legislation_gov_uk import parse_legislation_gov_uk
from ulis.storage.object_store import LocalObjectStore
from ulis.temporal.validity import Span, derive_valid_to

FIX = REPO_ROOT / "tests" / "fixtures" / "legislation_gov_uk" / "versions"
SITE = "https://www.legislation.gov.uk"
GAMING_1991, GAMING_2007 = "ukpga_1990_26_1991-02-01", "ukpga_1990_26_2007-09-01"


def _fixture(name: str) -> bytes:
    return gzip.decompress((FIX / f"{name}.akn.gz").read_bytes())


def test_wordings_that_ended_soon_after_the_base_date_start_on_the_base_date() -> None:
    """The site's revised text of an Act enacted before 1991-02-01 starts on that date, which is a lifecycle date
    only if
    something changed on it: "period-to-1991-04-01" wordings used to start (and end) on 1991-04-01 and were lost."""
    first = parse_legislation_gov_uk(_fixture(GAMING_1991))[0]
    assert first.work_date == date(1990, 7, 13) and first.version_date == date(1991, 2, 1)
    in_force = [p for p in first.provisions if p.status == "in_force" and p.valid_to is not None]
    assert in_force and {p.valid_from for p in in_force} == {date(1991, 2, 1)}
    assert {p.valid_to for p in in_force} == {date(1991, 4, 1), date(1997, 1, 31), date(2007, 9, 1)}
    assert all(p.record_version for p in in_force)  # none is dropped as "never applied"
    last = parse_legislation_gov_uk(_fixture(GAMING_2007))[0]
    assert last.version_date == date(2007, 9, 1)
    assert last.provisions and all(
        p.status == "repealed" and p.text == "" for p in last.provisions if p.eid == "section-1"
    )


def test_an_old_act_without_sections_is_one_unit_of_text() -> None:
    doc = parse_legislation_gov_uk(_fixture("aosp_1487_17_current"))[0]
    assert doc.work_uri == "/gb/aosp/1487/17" and doc.jurisdiction_id == "GB-SCT" and doc.instrument_type == "act"
    assert [p.eid for p in doc.provisions] == ["body"]
    assert doc.provisions[0].text.startswith("Alsa it is statut and ordanit be the hale thre estatis")
    assert doc.provisions[0].num_path == [] and doc.provisions[0].valid_from is None  # the site states no period


def test_enacted_and_made_documents_are_as_made_with_no_start_dates() -> None:
    enacted = parse_legislation_gov_uk(_fixture("nia_2020_4_enacted"))[0]
    made = parse_legislation_gov_uk(_fixture("nisi_1989_1336_made"))[0]
    for doc in (enacted, made):
        assert (doc.consolidation_kind, doc.text_origin, doc.jurisdiction_id, doc.instrument_type) == (
            "as_made",
            "official_conversion",
            "GB-NIR",
            "act",
        )
        assert doc.provisions and all(p.valid_from is None and p.valid_to is None for p in doc.provisions)
    assert enacted.work_uri == "/gb/nia/2020/4" and enacted.version_date == date(2020, 8, 25)
    assert made.work_uri == "/gb/nisi/1989/1336" and made.work_date == date(1989, 8, 2)


def test_a_version_starting_the_same_day_does_not_end_a_version_on_its_own_start_date() -> None:
    """Licensing (Scotland) Act 2005 s 105(5) has an in-force wording and a repealed one from 2009-09-01: deriving the
    end of the first from the start of the second gave a zero-length interval, which the database refuses."""
    d = date
    spans = [
        Span("a", "in_force", d(2005, 12, 21), d(2009, 9, 1), True),
        Span("c", "in_force", d(2009, 9, 1), None, False),
        Span("r", "repealed", d(2009, 9, 1), None, False),
        Span("later", "in_force", d(2013, 4, 1), None, False),
    ]
    derived = derive_valid_to(spans)
    assert derived["a"] == (d(2009, 9, 1), "explicit")
    assert derived["c"] == (d(2013, 4, 1), "derived")  # not 2009-09-01, its own start
    assert derived["r"] == (d(2013, 4, 1), "derived")
    assert derived["later"] == (None, "open")


class _Docs:
    def fetch(self) -> Iterator[RawDocument]:
        for name, path, version in (
            (GAMING_1991, "ukpga/1990/26", "1991-02-01"),
            (GAMING_2007, "ukpga/1990/26", "2007-09-01"),
            ("aosp_1487_17_current", "aosp/1487/17", "1991-02-01"),
            ("nia_2020_4_enacted", "nia/2020/4", "2020-08-25"),
            ("nisi_1989_1336_made", "nisi/1989/1336", "1989-08-02"),
        ):
            yield RawDocument(
                f"legislation.gov.uk:{path}@{version}",
                f"{SITE}/{path}/{version}",
                "application/akn+xml",
                _fixture(name),
                datetime(2026, 10, 16, 8, 0, tzinfo=UTC),
                {"path": path, "point_in_time": version},
            )


@pytest.fixture(scope="module")
def history_ingested(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore
) -> IngestionReport:
    with session_factory() as session:
        report = Ingestor(session, catalog, store).ingest_source(
            "gb-legislation-gov-uk", actor="pytest", connector=_Docs()
        )
        session.commit()
    return report


@pytest.mark.db
def test_repealed_act_keeps_its_text_before_the_repeal(
    session: Session, catalog: Catalog, history_ingested: IngestionReport
) -> None:
    assert history_ingested.errors == [] and history_ingested.conflicts == []
    # Five documents of four works. The test database is shared by the session: test_legislation_gov_uk.py may
    # already have ingested the Gaming (Amendment) Act 1990, so only the other three instruments are new then.
    assert history_ingested.documents_seen == 5 and history_ingested.instruments_created in (3, 4)
    assert history_ingested.instrument_versions_created in (3, 4, 5)
    during = provision_at(session, catalog, "/gb/ukpga/1990/26/~section-2", date(2000, 1, 1))
    assert during["provision"]["citation"] == "Gaming (Amendment) Act 1990 (repealed), s 2"
    assert during["version"]["text"].startswith("(1) This Act may be cited as the Gaming")
    assert (during["version"]["valid_from"], during["version"]["valid_to"]) == ("1991-02-01", "2007-09-01")
    after = provision_at(session, catalog, "/gb/ukpga/1990/26/~section-2", date(2010, 1, 1))
    assert after["version"]["status"] == "repealed" and after["version"]["valid_from"] == "2007-09-01"
    # A wording repealed within two months of the base date was in force from the base date to the repeal.
    early = provision_at(session, catalog, "/gb/ukpga/1990/26/~schedule-paragraph-2", date(1991, 3, 1))
    assert early["version"]["status"] == "in_force" and early["version"]["valid_to"] == "1997-01-31"


@pytest.mark.db
def test_old_scottish_act_enacted_act_and_order_in_council_are_found(
    session: Session, catalog: Catalog, history_ingested: IngestionReport
) -> None:
    old = provision_at(session, catalog, "/gb/aosp/1487/17/~body", date(2026, 10, 1))
    assert old["version"]["text"].startswith("Alsa it is statut and ordanit be the hale thre estatis")
    enacted = provision_at(session, catalog, "/gb/nia/2020/4/~section-1", date(2026, 10, 1))
    assert enacted["version"]["valid_from"] is None and enacted["status_on_date"] == "in_force_date_unverified"
    made = provision_at(session, catalog, "/gb/nisi/1989/1336/~article-1", date(2026, 10, 1))
    assert made["provision"]["citation"].endswith(", SI 1989/1336, art 1")

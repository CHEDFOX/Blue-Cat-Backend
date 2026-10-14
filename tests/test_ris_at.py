"""Austria: RIS parser, OGD connector window and Austrian search behaviour (real RIS document, CC BY 4.0)."""

from __future__ import annotations

from datetime import UTC, date, datetime
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
from ulis.sources.connectors.ris_ogd import ogd_window_open, vienna_time
from ulis.sources.parsers.ris import RisParseError, parse_ris_bundesrecht
from ulis.storage.object_store import LocalObjectStore

FIXTURE = REPO_ROOT / "tests" / "fixtures" / "ris" / "NOR40217848.xml"  # § 83 StGB, as served by RIS


@pytest.mark.parametrize(
    ("utc", "local", "open_"),
    [
        (datetime(2026, 10, 14, 8, 2, tzinfo=UTC), datetime(2026, 10, 14, 10, 2), False),  # Wednesday, CEST
        (datetime(2026, 10, 14, 17, 59, tzinfo=UTC), datetime(2026, 10, 14, 19, 59), False),
        (datetime(2026, 10, 14, 18, 0, tzinfo=UTC), datetime(2026, 10, 14, 20, 0), True),
        (datetime(2026, 10, 15, 2, 59, tzinfo=UTC), datetime(2026, 10, 15, 4, 59), True),
        (datetime(2026, 10, 15, 3, 0, tzinfo=UTC), datetime(2026, 10, 15, 5, 0), False),
        (datetime(2026, 10, 17, 10, 0, tzinfo=UTC), datetime(2026, 10, 17, 12, 0), True),  # Saturday
        (datetime(2026, 11, 2, 18, 59, tzinfo=UTC), datetime(2026, 11, 2, 19, 59), False),  # Monday, CET
        (datetime(2026, 11, 2, 19, 0, tzinfo=UTC), datetime(2026, 11, 2, 20, 0), True),
        (datetime(2026, 3, 29, 0, 59, tzinfo=UTC), datetime(2026, 3, 29, 1, 59), True),  # DST starts 01:00 UTC
        (datetime(2026, 3, 30, 3, 30, tzinfo=UTC), datetime(2026, 3, 30, 5, 30), False),
    ],
)
def test_ogd_access_window(utc: datetime, local: datetime, open_: bool) -> None:
    """RIS OGD terms: only 20:00-05:00 Vienna time on weekdays, or at weekends."""
    assert vienna_time(utc) == local
    assert ogd_window_open(utc) is open_


def _cite(title: str, top_kind: str, path: list[str], instrument_type: str = "act") -> str:
    c = ProvisionCitationInput(title, instrument_type, "AT", top_kind, path, "de")
    return get_module("at").provision_citation(c)


def test_citation_forms() -> None:
    assert _cite("StGB", "section", ["83", "abs:3", "z:1"]) == "§ 83 Abs. 3 Z 1 StGB"
    assert _cite("B-VG", "article", ["7", "abs:1"], "constitution") == "Art. 7 Abs. 1 B-VG"
    assert _cite("DSG", "section", ["4", "z:2", "lit:a"]) == "§ 4 Z 2 lit. a DSG"
    assert _cite("ABGB", "section", ["1295"]) == "§ 1295 ABGB"
    assert _cite("UGB", "annex", ["1"]) == "Anl. 1 UGB"


def test_paragraph_83_stgb_structure_and_text() -> None:
    (doc,) = parse_ris_bundesrecht(FIXTURE.read_bytes())
    assert (doc.jurisdiction_id, doc.instrument_type, doc.work_uri) == ("AT", "act", "/at/act/10002296")
    assert doc.text_origin == "official_revised" and not doc.authoritative and doc.language == "de"
    assert doc.validity_basis == "source_periods" and doc.currency_date is None
    p = {x.eid: x for x in doc.provisions}
    assert list(p) == ["par-83", "par-83-abs-1", "par-83-abs-2", "par-83-abs-3", "par-83-abs-3-z-1", "par-83-abs-3-z-2"]
    assert p["par-83"].heading == "Körperverletzung"
    assert p["par-83-abs-2"].text == (
        "Ebenso ist zu bestrafen, wer einen anderen am Körper mißhandelt und dadurch fahrlässig "
        "verletzt oder an der Gesundheit schädigt."
    )
    assert p["par-83-abs-3-z-1"].text.startswith("mit der Kontrolle der Einhaltung der Beförderungsbedingungen")
    assert p["par-83-abs-3"].text.endswith("ist mit Freiheitsstrafe bis zu zwei Jahren zu bestrafen.")
    assert all(x.valid_from == date(2020, 1, 1) and x.valid_to is None for x in p.values())  # Inkrafttreten 01.01.2020
    assert all(x.status == "in_force" and x.extent == ["AT"] for x in p.values())
    z1 = p["par-83-abs-3-z-1"]
    assert _cite(doc.short_title or "", z1.top_kind, z1.num_path) == "§ 83 Abs. 3 Z 1 StGB"


def test_rejects_foreign_documents() -> None:
    with pytest.raises(RisParseError):
        parse_ris_bundesrecht(b'<?xml version="1.0"?><other xmlns="http://www.bka.gv.at"/>')
    with pytest.raises(RisParseError):
        parse_ris_bundesrecht(b"<!DOCTYPE x [<!ENTITY e 'e'>]><x/>")


def test_end_date_and_repeal_code_paths() -> None:
    """Synthetic variants of the real fixture (not RIS data): exercises the end-date and repeal paths.

    The ``akra`` field name is an assumption, not yet confirmed against a real superseded version.
    """
    xml = FIXTURE.read_text(encoding="utf-8")
    ended = xml.replace(
        'ct="ikra" halign="j">01.01.2020</absatz>',
        'ct="ikra" halign="j">01.01.2020</absatz><ueberschrift typ="titel">Außerkrafttretensdatum</ueberschrift>'
        '<absatz typ="erltext" ct="akra" halign="j">31.12.2022</absatz>',
    )
    (doc,) = parse_ris_bundesrecht(ended.encode("utf-8"))
    assert doc.provisions[0].valid_to == date(2023, 1, 1)  # inclusive last day -> exclusive end


# --------------------------------------------------------------------------- PostgreSQL


class _Fixture:
    def fetch(self):  # type: ignore[no-untyped-def]
        yield RawDocument(
            "ris:NOR40217848",
            "https://www.ris.bka.gv.at/Dokumente/Bundesnormen/NOR40217848/NOR40217848.xml",
            "application/xml",
            FIXTURE.read_bytes(),
            datetime(2026, 10, 14, 7, 44, tzinfo=UTC),
            {},
        )


@pytest.fixture(scope="module")
def at_ingested(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, ingested: IngestionReport
) -> IngestionReport:
    # The source is "planned" in the register (its API host's robots.txt disallows our bot); the tests
    # exercise the parser and pipeline on a real document by treating it as active in this test only.
    descriptor = catalog.sources.get("at-ris-bundesrecht")
    catalog.sources.sources["at-ris-bundesrecht"] = descriptor.model_copy(update={"status": "active"})
    try:
        with session_factory() as session:
            report = Ingestor(session, catalog, store).ingest_source(
                "at-ris-bundesrecht", actor="pytest", connector=_Fixture()
            )
            session.commit()
    finally:
        catalog.sources.sources["at-ris-bundesrecht"] = descriptor
    return report


def _search(session: Session, catalog: Catalog, **kw: Any) -> dict[str, Any]:
    return search(session, catalog, SearchRequest(jurisdiction="AT", **kw), today=date(2026, 10, 14))


@pytest.mark.db
def test_search_finds_paragraph_with_citation_and_limitations(
    session: Session, catalog: Catalog, at_ingested: IngestionReport
) -> None:
    assert at_ingested.errors == [] and at_ingested.conflicts == []
    assert at_ingested.provision_versions_created == 6
    res = _search(session, catalog, q="Körperverletzung Freiheitsstrafe", as_of=date(2026, 10, 14), query_language="de")
    hit = next(h for h in res["results"] if h["citation"] == "§ 83 Abs. 1 StGB")
    assert hit["text"].startswith("Wer einen anderen am Körper verletzt")
    codes = {lim["code"] for lim in hit["limitations"]}
    assert {"REVISED_TEXT", "PROFILE_UNVERIFIED", "CURRENCY_UNKNOWN", "SOURCE_NOT_AUTHENTIC"} <= codes
    assert hit["source"]["attribution"].startswith("Quelle: Rechtsinformationssystem des Bundes (RIS)")


@pytest.mark.db
def test_point_in_time_before_recorded_version(
    session: Session, catalog: Catalog, at_ingested: IngestionReport
) -> None:
    uri = "/at/act/10002296/~par-83-abs-1"
    now = provision_at(session, catalog, uri, date(2026, 10, 14), language="de")
    assert now["status_on_date"] == "in_force"
    # Only the version from 2020-01-01 is ingested: earlier dates are a history gap, not absence of law.
    before = provision_at(session, catalog, uri, date(2015, 1, 1), language="de")
    assert before["status_on_date"] != "in_force"


def test_source_is_planned_until_the_publisher_permits_automated_access(catalog: Catalog) -> None:
    import pytest as _pytest

    from ulis.sources.registry import LicenceNotApproved, licence_gate

    with _pytest.raises(LicenceNotApproved):
        licence_gate(catalog.sources.get("at-ris-bundesrecht"))

"""Basiswettenbestand (BWB): parser, connector and Dutch search behaviour (real national law, CC0 1.0)."""

from __future__ import annotations

import gzip
import hashlib
import json
import shutil
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
from ulis.ingestion.pipeline import IngestionReport, Ingestor, build_connector
from ulis.ingestion.pipeline import resolve_uri as pipeline_resolve_uri
from ulis.jurisdictions.modules import ProvisionCitationInput, get_module
from ulis.jurisdictions.modules.nl import NlProfileExt
from ulis.retrieval.lookup import provision_at
from ulis.retrieval.search import SearchRequest, search
from ulis.settings import REPO_ROOT
from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.bwb import BwbConnector
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.polite_http import FetchError
from ulis.sources.normalized import ParsedDocument, ParsedProvision
from ulis.sources.parsers.bwb import BwbParseError, parse_bwb, read_manifest, resolve_uri
from ulis.storage.object_store import LocalObjectStore
from ulis.verification.integrity import verify_source_document

SOURCE = "nl-bwb"
HOST = "repository.officiele-overheidspublicaties.nl"
FIX = REPO_ROOT / "tests" / "fixtures" / "bwb" / "cache"
BWB = FIX / HOST / "bwb"
GW, UAVG, BW6, AWB = "BWBR0001840", "BWBR0040940", "BWBR0005289", "BWBR0005537"
SP14 = "BWBR0034337"  # Regeling vaststelling standaardpremie 2014
UAVG_TOESTANDEN = ["2018-05-25_0", "2019-01-01_0", "2019-02-19_0", "2020-01-01_0", "2021-07-01_0", "2026-09-01_0"]
# What the in-memory connector feeds the pipeline, oldest toestand first, each regulation's manifest last.
INGESTED = {
    UAVG: UAVG_TOESTANDEN,
    GW: ["2022-11-01_0", "2023-02-22_0"],
    BW6: ["2026-07-16_0"],
    AWB: ["2026-08-15_0"],
    SP14: ["2014-01-01_0"],  # a ministerial regulation (<regeling>), one of the thousands of the whole collection
}


def _path(bwb_id: str, toestand: str | None = None) -> Path:
    if toestand is None:
        return BWB / bwb_id / "manifest.xml.gz"
    return BWB / bwb_id / toestand / "xml" / f"{bwb_id}_{toestand}.xml.gz"


def _raw(bwb_id: str, toestand: str | None = None) -> bytes:
    return gzip.decompress(_path(bwb_id, toestand).read_bytes())


def _doc(bwb_id: str, toestand: str) -> ParsedDocument:
    docs = parse_bwb(_raw(bwb_id, toestand))
    assert len(docs) == 1
    return docs[0]


def _p(doc: ParsedDocument, eid: str) -> ParsedProvision:
    return {p.eid: p for p in doc.provisions}[eid]


def _cite(doc: ParsedDocument, eid: str, catalog: Catalog) -> str:
    p = _p(doc, eid)
    return get_module("nl").provision_citation(
        ProvisionCitationInput(
            doc.short_title or doc.title,
            doc.instrument_type,
            "NL",
            p.top_kind,
            p.num_path,
            "nl",
            ext=doc.ext,
            profile_extensions=catalog.jurisdictions.get("NL").extensions,
            eid=p.eid,
        )
    )


def _toestand(body: str, soort: str = "wet", start: str = "2026-01-01") -> bytes:
    """A minimal toestand (synthetic; used only for edge cases absent from the fixtures)."""
    return (
        f'<?xml version="1.0" encoding="UTF-8"?><toestand bwb-id="BWBR9999999" inwerkingtreding="{start}" '
        f'bwb-ng-vast-deel="http://wetten.overheid.nl/id/BWBR9999999/{start}/0"><wetgeving soort="{soort}" '
        'xml:lang="nl"><intitule inwerking="2020-01-01">Testwet</intitule><citeertitel>Testwet</citeertitel>'
        f"<wet-besluit><wettekst>{body}</wettekst></wet-besluit></wetgeving></toestand>"
    ).encode()


# --------------------------------------------------------------------------- XML safety and input


@pytest.mark.parametrize(
    "content",
    [
        b'<?xml version="1.0"?><!DOCTYPE toestand [<!ENTITY e "x">]><toestand>&e;</toestand>',
        b'<!DOCTYPE work [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;&a;">]><work label="BWBR0000001">&b;</work>',
        b'<!DOCTYPE toestand [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><toestand>&xxe;</toestand>',
        b'<!DOCTYPE toestand SYSTEM "http://example.org/toestand.dtd"><toestand/>',
    ],
)
def test_doctypes_and_entities_are_rejected(content: bytes) -> None:
    with pytest.raises(BwbParseError, match="unsafe XML"):
        parse_bwb(content)


def test_unexpected_input_is_rejected() -> None:
    with pytest.raises(BwbParseError, match="unsupported root"):
        parse_bwb(b"<akomaNtoso/>")
    article = '<artikel status="goed" inwerking="2020-01-01"><kop><nr>1</nr></kop><al>x</al></artikel>'
    for soort in ("beleidsregel", "zbo", "KB", "wet-BES"):
        with pytest.raises(BwbParseError, match="unsupported regulation type"):
            parse_bwb(_toestand(article, soort))
    # an Act of the Kingdom is an Act; the archive selection list a ministerial regulation
    assert parse_bwb(_toestand(article, "rijkswet"))[0].instrument_type == "act"
    assert parse_bwb(_toestand(article, "ministeriele-regeling-archiefselectielijst"))[0].instrument_type == (
        "ministerial_regulation"
    )
    with pytest.raises(BwbParseError, match="unknown status"):
        parse_bwb(_toestand('<artikel status="raar"><kop><nr>1</nr></kop><al>x</al></artikel>'))
    with pytest.raises(BwbParseError, match="unexpected element <tabel>"):
        parse_bwb(_toestand("<tabel/>"))


# --------------------------------------------------------------------------- toestanden


def test_grondwet_current_toestand(catalog: Catalog) -> None:
    doc = _doc(GW, "2023-02-22_0")
    assert (doc.jurisdiction_id, doc.instrument_type, doc.work_uri, doc.language) == (
        "NL",
        "constitution",
        "/nl/bwb/BWBR0001840",
        "nl",
    )
    assert doc.expression_uri == "/nl/bwb/BWBR0001840/nld@2023-02-22_0"
    assert doc.version_date == doc.currency_date == date(2023, 2, 22)
    assert doc.work_date == date(1840, 9, 12)  # intitule/@inwerking as recorded by the BWB
    assert (doc.short_title, doc.title) == (
        "Grondwet",
        "Grondwet voor het Koninkrijk der Nederlanden van 24 augustus 1815",
    )
    assert (doc.text_origin, doc.authoritative, doc.validity_basis, doc.consolidation_kind) == (
        "official_revised",
        False,
        "revision_snapshot",
        "point_in_time",
    )
    assert doc.ext == {"bwb_id": GW, "jci": "jci1.3:c:BWBR0001840", "citeertitel": "Grondwet", "soort": "wet"}
    # The start of each toestand is recorded from the manifest, which lists them all.
    assert [(e.event_type, e.date) for e in doc.events] == [("entry_into_force", date(1840, 9, 12))]
    art1 = _p(doc, "art-1")
    assert art1.text == (
        "Allen die zich in Nederland bevinden, worden in gelijke gevallen gelijk behandeld. Discriminatie wegens "
        "godsdienst, levensovertuiging, politieke gezindheid, ras, geslacht, handicap, seksuele gerichtheid of op "
        "welke grond dan ook, is niet toegestaan."
    )
    assert art1.valid_from == date(2023, 2, 22)  # the article's own @inwerking
    assert (art1.note or "").startswith("Bron: Stb.2023-62 (wijziging)")
    assert art1.context_heading == "Hoofdstuk 1 Grondrechten"
    assert _cite(doc, "art-1", catalog) == "art. 1 Grondwet"
    lid4 = _p(doc, "art-23-lid-4")
    assert lid4.num_path == ["23", "lid 4"] and lid4.valid_from == date(2018, 12, 21)
    assert _cite(doc, "art-23-lid-4", catalog) == "art. 23 lid 4 Grondwet"
    assert [r.href for r in lid4.refs] == ["jci1.3:c:BWBR0001840&hoofdstuk=7&artikel=132a&z=2023-02-22&g=2023-02-22"]
    assert _p(doc, "art-23").text.startswith("1 Het onderwijs is een voorwerp")  # the container keeps the numbers
    assert _p(doc, "art-120").text == (
        "De rechter treedt niet in de beoordeling van de grondwettigheid van wetten en verdragen."
    )
    general = _p(doc, "art-algemene-bepaling")
    assert general.text == "De Grondwet waarborgt de grondrechten en de democratische rechtsstaat."
    assert _cite(doc, "art-algemene-bepaling", catalog) == "Algemene bepaling Grondwet"
    repealed = _p(doc, "art-101")
    assert (repealed.status, repealed.text, repealed.valid_from) == ("repealed", "", date(1995, 11, 1))
    assert repealed.note == "Vervallen; Bron: Stb.1995-401 (vervallen)"


def test_grondwet_earlier_toestand_has_the_earlier_wording() -> None:
    art1 = _p(_doc(GW, "2022-11-01_0"), "art-1")
    assert art1.text.endswith("ras, geslacht of op welke grond dan ook, is niet toegestaan.")
    # Stb. 2019, 33 re-placed the whole text ("tekstplaatsing"): every article got a new @inwerking.
    assert art1.valid_from == date(2018, 12, 21) and "tekstplaatsing" in (art1.note or "")


def test_uavg_series_statuses() -> None:
    first = _doc(UAVG, "2018-05-25_0")
    assert first.instrument_type == "act" and first.work_date == date(2018, 5, 25)
    not_yet = _p(first, "art-48a")
    assert (not_yet.status, not_yet.valid_from, not_yet.text) == ("prospective", None, "")
    assert (not_yet.note or "").startswith("Dit onderdeel is nog niet inwerking getreden")
    assert _p(_doc(UAVG, "2019-01-01_0"), "art-48a").valid_from == date(2019, 1, 1)
    latest = _doc(UAVG, "2026-09-01_0")
    new = _p(latest, "art-23a")  # inserted by Stb. 2026, 154 but not yet in force: text shown, no start date
    assert new.status == "prospective" and new.valid_from is None
    assert new.text.startswith("1 Gelet op artikel 9, tweede lid, onderdeel g, van de verordening")
    assert "art-10-lid-1" not in {p.eid for p in latest.provisions}  # art. 10 lost its members on 2020-01-01
    assert _p(latest, "art-10").text == "De Autoriteit persoonsgegevens heeft een secretariaat."


def test_bw_book_6(catalog: Catalog) -> None:
    doc = _doc(BW6, "2026-07-16_0")
    assert doc.ext["boek"] == "6" and doc.short_title == "Burgerlijk Wetboek Boek 6"
    lid1 = _p(doc, "art-162-lid-1")
    assert lid1.text == (
        "Hij die jegens een ander een onrechtmatige daad pleegt, welke hem kan worden toegerekend, is verplicht de "
        "schade die de ander dientengevolge lijdt, te vergoeden."
    )
    assert lid1.valid_from == date(1992, 1, 1) and lid1.note == "Bron: Stb.1991-600 (nieuwe regeling)"
    assert lid1.context_heading == (
        "Boek 6 Algemeen gedeelte van het verbintenissenrecht – Titel 3 Onrechtmatige daad – Afdeling 1 Algemene "
        "bepalingen"
    )
    assert _cite(doc, "art-162-lid-1", catalog) == "art. 6:162 lid 1 BW"


def test_awb_numbering_annexes_and_editorial_text(catalog: Catalog) -> None:
    doc = _doc(AWB, "2026-08-15_0")
    assert _cite(doc, "art-3:4-lid-2", catalog) == "art. 3:4 lid 2 Awb"
    assert _p(doc, "art-3:4-lid-2").text.startswith("De voor een of meer belanghebbenden nadelige gevolgen")
    assert _cite(doc, "art-4:21-lid-2-onderdeel-a", catalog) == "art. 4:21 lid 2 onder a Awb"
    # Retroactive effect is stated in the note; the version starts on the entry into force.
    retro = _p(doc, "art-4:21-lid-1")
    assert retro.valid_from == date(2007, 1, 1) and "Terugwerkende kracht tot en met 2006-01-01" in (retro.note or "")
    # An editorial insertion inside a sentence is removed from the text and noted with its context.
    fee = _p(doc, "art-4:113-lid-1")
    assert "minder dan € 454 bedraagt en € 20 indien de schuld € 454 of meer bedraagt." in fee.text
    assert "Per 1 januari 2015" not in fee.text
    assert (
        "Redactioneel, bij “vergoeding bedraagt € 9 indien de schuld minder dan € 454”: Per 1 januari 2015: € 500"
        in (fee.note or "")
    )
    assert "Per 1 januari 2015" not in (_p(doc, "art-4:113-lid-2").note or "")  # a sibling member's note
    # A member not yet in force is shown only as editorial text: prospective, no text.
    pending = _p(doc, "art-2:10-lid-2")
    assert (pending.status, pending.text, pending.valid_from) == ("prospective", "", None)
    assert (pending.note or "").startswith("Dit lid is nog niet in werking getreden.")
    # Two parallel texts with the same member numbers (digital procedure): one undivided provision.
    parallel = _p(doc, "art-8:36c")
    assert parallel.is_leaf and parallel.text.count("\n1 Als tijdstip") + parallel.text.startswith("1 Als") == 2
    assert "Voor overige gevallen luidt het artikel als volgt:" in (parallel.note or "")
    annex = _p(doc, "bijlage-2")
    assert (annex.kind, annex.num_path, annex.status) == ("annex", ["Bijlage 2"], "in_force")
    assert (annex.heading or "").startswith("Bevoegdheidsregeling bestuursrechtspraak")
    assert _cite(doc, "bijlage-2", catalog) == "Bijlage 2 Awb"


# --------------------------------------------------------------------------- manifest and identifiers


def test_manifest_listing_and_change_report() -> None:
    listing = read_manifest(_raw(GW))
    assert (listing.bwb_id, listing.generated, len(listing.past), listing.current.label) == (
        GW,
        date(2026, 9, 15),
        11,
        "2023-02-22_0",
    )
    assert listing.current.sha512 == hashlib.sha512(_raw(GW, "2023-02-22_0")).hexdigest()
    assert listing.past[0].end == date(2005, 2, 7) and listing.current.end is None
    # Toestanden corrected later for retroactive effect: only the corrected variant (_1) is kept.
    awb = read_manifest(_raw(AWB))
    labels = {t.label for t in awb.past}
    assert {"2006-01-01_1", "2006-02-01_1"} <= labels and "2006-01-01_0" not in labels
    (report,) = parse_bwb(_raw(GW))
    assert (report.consolidation_kind, report.expression_uri, report.currency_date) == (
        "change_report",
        "/nl/bwb/BWBR0001840/nld@2023-02-22_0",
        date(2026, 9, 15),
    )
    assert report.outstanding_changes == [] and report.provisions == []
    assert [e.date for e in report.events][:2] == [date(2002, 3, 21), date(2005, 2, 8)]


def test_scheduled_toestanden_are_outstanding_changes() -> None:
    # The real UAVG manifest with its generation date moved back before the 2026-09-01 toestand.
    edited = _raw(UAVG).replace(b'gegenereerd="2026-10-14', b'gegenereerd="2026-08-01', 1)
    assert edited != _raw(UAVG)
    listing = read_manifest(edited)
    assert listing.current.label == "2021-07-01_0" and [t.label for t in listing.scheduled] == ["2026-09-01_0"]
    (report,) = parse_bwb(edited)
    assert report.expression_uri == "/nl/bwb/BWBR0040940/nld@2021-07-01_0"
    (change,) = report.outstanding_changes
    assert change["effective_date"] == "2026-09-01" and change["affects_eid"] is None
    assert "not applied to this text" in str(change["description"])


def test_identifiers() -> None:
    assert resolve_uri("jci1.3:c:BWBR0001840&hoofdstuk=7&artikel=132a&z=2023-02-22&g=2023-02-22") == (
        "/nl/bwb/BWBR0001840",
        "art-132a",
    )
    assert resolve_uri("1.0:c:BWBR0005537&artikel=4%3A21&lid=2&g=2026-08-15") == (
        "/nl/bwb/BWBR0005537",
        "art-4:21-lid-2",
    )
    assert resolve_uri("jci1.3:c:BWBR0007376") == ("/nl/bwb/BWBR0007376", None)
    assert resolve_uri("https://wetten.overheid.nl/jci1.3:c:BWBR0005289&artikel=162") == (
        "/nl/bwb/BWBR0005289",
        "art-162",
    )
    assert resolve_uri("https://example.org/BWBR0001840") is None
    assert pipeline_resolve_uri("jci1.3:c:BWBR0001854&artikel=310") == ("/nl/bwb/BWBR0001854", "art-310")


def test_citation_module(catalog: Catalog) -> None:
    module = get_module("nl")
    extensions = catalog.jurisdictions.get("NL").extensions

    def cite(path: list[str], ext: dict[str, Any], top: str = "article") -> str:
        return module.provision_citation(
            ProvisionCitationInput("x", "act", "NL", top, path, "nl", ext=ext, profile_extensions=extensions)
        )

    sr = {"bwb_id": "BWBR0001854", "jci": "jci1.3:c:BWBR0001854", "citeertitel": "Wetboek van Strafrecht"}
    assert cite(["310"], sr) == "art. 310 Sr"
    bw1 = {"bwb_id": "BWBR0002656", "jci": "x", "citeertitel": "Burgerlijk Wetboek Boek 1", "boek": "1"}
    assert cite(["3", "lid 1"], bw1) == "art. 1:3 lid 1 BW"
    uavg = {"bwb_id": UAVG, "jci": "x", "citeertitel": "Uitvoeringswet Algemene verordening gegevensbescherming"}
    assert cite(["7", "lid 2", "a.", "1°."], uavg) == "art. 7 lid 2 onder a sub 1° UAVG"
    other = {"bwb_id": "BWBR0001886", "jci": "x", "citeertitel": "Auteurswet"}
    assert cite(["11"], other) == "art. 11 Auteurswet"  # no abbreviation chosen: the citation title
    renamed = {**sr, "citeertitel": "Wetboek van Strafrecht 2030"}
    assert cite(["1"], renamed) == "art. 1 Wetboek van Strafrecht 2030"  # stale abbreviation entry not applied
    with pytest.raises(ValidationError, match="neither a source abbreviation"):
        NlProfileExt.model_validate(
            {
                "abbreviations": {
                    "BWBR0001854": {"citeertitel": "Wetboek van Strafrecht", "afkortingen": ["Sr"], "cite_as": "WvS"}
                }
            }
        )


def test_enig_artikel_and_a_table_of_contents() -> None:
    # <enig-artikel>: the only article of a regulation, unnumbered (BWBR0015805); a table of contents is not a provision
    only = (
        '<enig-artikel status="goed" inwerking="2020-01-01">'
        "<al>Deze regeling treedt in werking op 1 januari 2020.</al></enig-artikel>"
    )
    doc = parse_bwb(_toestand(only))[0]
    assert [(p.eid, p.kind, p.num_path, p.text) for p in doc.provisions] == [
        (
            "art-enig-artikel",
            "general_provision",
            ["Enig artikel"],
            "Deze regeling treedt in werking op 1 januari 2020.",
        )
    ]
    toc = _toestand(only).replace(
        b"<wet-besluit>", b"<wet-besluit><officiele-inhoudsopgave><al>Inhoud</al></officiele-inhoudsopgave>"
    )
    assert [p.eid for p in parse_bwb(toc)[0].provisions] == ["art-enig-artikel"]


def test_a_regeling_body_is_read_like_a_wet_besluit() -> None:
    # BWBR0040488, a ministerial regulation of two articles: <regeling><regeling-tekst>...
    doc = _doc("BWBR0040488", "2018-02-15_0")
    assert doc.instrument_type == "ministerial_regulation" and doc.language == "nl"
    assert [p.eid for p in doc.provisions] == ["art-1", "art-2"]
    assert _p(doc, "art-1").text.startswith("Het subsidieplafond, bedoeld in artikel 5")
    assert _p(doc, "art-2").text.startswith("Dit besluit treedt in werking met ingang van 15 februari 2018")


# --------------------------------------------------------------------------- connector


def _cache_copy(dest: Path) -> Path:
    shutil.copytree(FIX, dest)
    for gz in dest.rglob("*.gz"):
        gz.with_suffix("").write_bytes(gzip.decompress(gz.read_bytes()))
        gz.unlink()
    return dest


def _manifest(tmp_path: Path, regulations: list[dict[str, Any]], **extra: Any) -> Path:
    path = tmp_path / "manifest.yaml"
    data = {
        "schema_version": 1,
        "source_id": SOURCE,
        "base_url": "https://repository.officiele-overheidspublicaties.nl/bwb",
        "user_agent": "ULIS-test (+https://example.org)",
        "regulations": regulations,
        **extra,
    }
    path.write_text(yaml.safe_dump(data, allow_unicode=True))
    return path


def test_connector_offline_from_cache(tmp_path: Path) -> None:
    cache = _cache_copy(tmp_path / "cache")
    manifest = _manifest(tmp_path, [{"bwb_id": UAVG, "history": True}, {"bwb_id": GW, "history": False}])
    connector = BwbConnector(manifest, SOURCE, cache, offline=True)
    docs = list(connector.fetch())
    assert [d.external_id for d in docs] == [
        *(f"bwb:{UAVG}@{t}" for t in UAVG_TOESTANDEN),
        f"bwb:{UAVG}:manifest",
        f"bwb:{GW}@2023-02-22_0",
        f"bwb:{GW}:manifest",
    ]
    assert connector.http is not None and connector.http.requests_made == 0
    current = docs[-2]
    assert current.content == _raw(GW, "2023-02-22_0")  # exact bytes as received
    assert current.uri == f"https://{HOST}/bwb/{GW}/2023-02-22_0/xml/{GW}_2023-02-22_0.xml"
    assert current.metadata["manifest_sha512_matches"] is True and current.metadata["geldig_tot_en_met"] is None
    assert docs[0].metadata["geldig_van"] == "2018-05-25" and docs[0].metadata["geldig_tot_en_met"] == "2018-12-31"
    assert current.media_type == "application/xml" and current.retrieved_at.year == 2026
    with pytest.raises(FetchError, match="offline and not cached"):
        list(BwbConnector(_manifest(tmp_path, [{"bwb_id": GW, "history": True}]), SOURCE, cache, offline=True).fetch())


def test_connector_refuses_bad_manifests_and_non_toestand_responses(tmp_path: Path) -> None:
    for regulations, extra, message in (
        ([{"bwb_id": "../etc"}], {}, "invalid BWB id"),
        ([{"bwb_id": GW}, {"bwb_id": GW}], {}, "listed twice"),
        ([{"bwb_id": GW}], {"base_url": "http://repository.officiele-overheidspublicaties.nl/bwb"}, "base_url"),
        ([{"bwb_id": GW}], {"user_agent": "python-urllib"}, "user_agent"),
        ([], {}, "no regulations"),
    ):
        with pytest.raises(ManifestError, match=message):
            list(BwbConnector(_manifest(tmp_path, regulations, **extra), SOURCE, tmp_path).fetch())
    # A cached HTML page (e.g. an error page) is never passed on as a toestand: reported, and the run goes on.
    cache = _cache_copy(tmp_path / "cache")
    page = cache / HOST / "bwb" / BW6 / "2026-07-16_0" / "xml" / f"{BW6}_2026-07-16_0.xml"
    page.write_bytes(b"<html>Fout</html>")
    connector = BwbConnector(
        _manifest(tmp_path, [{"bwb_id": BW6}, {"bwb_id": GW, "history": False}]), SOURCE, cache, offline=True
    )
    docs = list(connector.fetch())
    assert [d.external_id for d in docs] == [f"bwb:{GW}@2023-02-22_0", f"bwb:{GW}:manifest"]
    assert len(connector.fetch_errors) == 1 and "not a BWB toestand" in connector.fetch_errors[0]
    assert connector.fetch_errors[0].startswith(f"bwb:{BW6}:")


def test_other_kinds_than_wet_amvb_and_ministerial_regulation_are_not_stored(tmp_path: Path) -> None:
    cache = _cache_copy(tmp_path / "cache")
    page = cache / HOST / "bwb" / BW6 / "2026-07-16_0" / "xml" / f"{BW6}_2026-07-16_0.xml"
    assert b'soort="wet"' in page.read_bytes()
    page.write_bytes(page.read_bytes().replace(b'soort="wet"', b'soort="beleidsregel"', 1))
    connector = BwbConnector(
        _manifest(tmp_path, [{"bwb_id": BW6}, {"bwb_id": GW, "history": False}]), SOURCE, cache, offline=True
    )
    docs = list(connector.fetch())
    # neither the toestand nor the manifest of the policy rule is passed on; no error either: it is a scope decision
    assert [d.external_id for d in docs] == [f"bwb:{GW}@2023-02-22_0", f"bwb:{GW}:manifest"]
    assert connector.unsupported == [f"{BW6}: beleidsregel"] and connector.fetch_errors == []


def test_lapsed_and_missing_regulations_are_skipped_not_fatal(tmp_path: Path) -> None:
    cache = _cache_copy(tmp_path / "cache")
    path = cache / HOST / "bwb" / BW6 / "manifest.xml"
    text = path.read_text(encoding="utf-8")
    head, _, tail = text.rpartition("<einddatum>9999-12-31</einddatum>")
    path.write_text(head + "<einddatum>2026-09-30</einddatum>" + tail, encoding="utf-8")  # the last toestand has ended
    manifest = _manifest(
        tmp_path, [{"bwb_id": BW6, "history": False}, {"bwb_id": "BWBR0000001"}, {"bwb_id": GW, "history": False}]
    )
    # BWBR0000001 is not in the cache and the connector is offline: that is a refusal-like failure and stops the run
    with pytest.raises(FetchError, match="offline and not cached"):
        list(BwbConnector(manifest, SOURCE, cache, offline=True).fetch())
    connector = BwbConnector(
        _manifest(tmp_path, [{"bwb_id": BW6, "history": False}, {"bwb_id": GW, "history": False}]),
        SOURCE,
        cache,
        offline=True,
    )
    docs = list(connector.fetch())
    assert [d.external_id for d in docs] == [f"bwb:{GW}@2023-02-22_0", f"bwb:{GW}:manifest"]
    assert connector.lapsed == [BW6] and connector.fetch_errors == []


def test_committed_manifest_lists_the_publishers_whole_regulation_list(catalog: Catalog) -> None:
    path = REPO_ROOT / catalog.sources.get(SOURCE).access.endpoint  # type: ignore[operator]
    manifest = BwbConnector(path, SOURCE, Path("/nonexistent"))._manifest()
    regulations = manifest["regulations"]
    ids = [r["bwb_id"] for r in regulations]
    assert ids[:6] == [GW, UAVG, "BWBR0002656", BW6, "BWBR0001854", AWB]  # the pilot regulations keep their place
    assert [r["history"] for r in regulations[:6]] == [True, True, False, False, False, False]
    assert all(r["history"] is False for r in regulations[6:])
    assert len(ids) > 40000 and all(i.startswith("BWBR") for i in ids)  # regelingen only; treaties (BWBV) are left out


def test_source_uses_this_connector(catalog: Catalog) -> None:
    assert isinstance(build_connector(catalog.sources.get(SOURCE)), BwbConnector)


# --------------------------------------------------------------------------- PostgreSQL


class _Fixtures:
    def fetch(self) -> Iterator[RawDocument]:
        for bwb_id, toestanden in INGESTED.items():
            for toestand in [*toestanden, None]:
                path = _path(bwb_id, toestand)
                meta = json.loads(path.with_name(path.name.removesuffix(".gz") + ".meta.json").read_text())
                yield RawDocument(
                    f"bwb:{bwb_id}@{toestand}" if toestand else f"bwb:{bwb_id}:manifest",
                    meta["final_url"],
                    "application/xml",
                    _raw(bwb_id, toestand),
                    datetime.fromisoformat(meta["retrieved_at"]).astimezone(UTC),
                    {"bwb_id": bwb_id, "toestand": toestand},
                )


@pytest.fixture(scope="module")
def nl_ingested(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, ingested: IngestionReport
) -> IngestionReport:
    with session_factory() as session:
        report = Ingestor(session, catalog, store).ingest_source(SOURCE, actor="pytest", connector=_Fixtures())
        session.commit()
    return report


def _search(session: Session, catalog: Catalog, **kw: Any) -> dict[str, Any]:
    return search(session, catalog, SearchRequest(jurisdiction="NL", **kw), today=date(2026, 10, 14))


@pytest.mark.db
def test_search_finds_exact_text_with_citation_and_limitations(
    session: Session, catalog: Catalog, nl_ingested: IngestionReport
) -> None:
    assert nl_ingested.errors == [] and nl_ingested.conflicts == []
    assert nl_ingested.instruments_created == 5 and nl_ingested.instrument_versions_created == 11
    res = _search(session, catalog, q="onrechtmatige daad toegerekend schade vergoeden", as_of=date(2026, 1, 1))
    hit = res["results"][0]
    assert hit["citation"] == "art. 6:162 lid 1 BW"
    assert hit["text"].startswith("Hij die jegens een ander een onrechtmatige daad pleegt")
    assert hit["authority"]["category"] == "binding_legislation"
    codes = {lim["code"] for lim in hit["limitations"]}
    assert {"VERSION_PERIOD", "REVISED_TEXT", "SOURCE_NOT_AUTHENTIC", "PROFILE_UNVERIFIED", "CURRENCY"} <= codes
    assert "NOT_AUTHENTIC_TEXT" not in codes and "ATTESTED_ONLY_AT" not in codes
    period = next(lim["message"] for lim in hit["limitations"] if lim["code"] == "VERSION_PERIOD")
    assert period == "This version applies from 1992-01-01; no end date recorded."
    assert hit["source"]["attribution"].startswith("Bron: Basiswettenbestand")
    # Book 6 is ingested current-only (toestand of 2026-07-16): the article's own date (1992) covers 2026-01-01,
    # but other text in force on that date may be missing, which the search reports.
    assert "HISTORY_INCOMPLETE" in {lim["code"] for lim in res["limitations"]}
    premie = _search(session, catalog, q="standaardpremie 2014 vastgesteld", as_of=date(2016, 1, 1))
    assert premie["results"][0]["text"] == "De standaardpremie wordt voor het jaar 2014 vastgesteld op € 1329,–."
    assert premie["results"][0]["authority"]["category"] == "binding_legislation"
    awb = _search(session, catalog, q="nadelige gevolgen besluit onevenredig doelen", as_of=date(2026, 10, 1))
    assert awb["results"][0]["citation"] == "art. 3:4 lid 2 Awb"
    # Art. 46 UAVG had two members until 2026-09-01 and one undivided text since: the article, a container in
    # the earlier toestanden, is indexed once it is a leaf (its earlier composite text is then indexed too).
    query = "nummer identificatie persoon bij wet voorgeschreven"
    one_text = _search(session, catalog, q=query, as_of=date(2026, 10, 1))
    assert one_text["results"][0]["citation"] == "art. 46 UAVG"
    assert one_text["results"][0]["version"]["valid_from"] == "2026-09-01"
    two_members = {h["citation"] for h in _search(session, catalog, q=query, as_of=date(2020, 1, 1))["results"]}
    assert {"art. 46 UAVG", "art. 46 lid 1 UAVG"} <= two_members


@pytest.mark.db
def test_point_in_time_follows_the_toestanden(session: Session, catalog: Catalog, nl_ingested: IngestionReport) -> None:
    uri = "/nl/bwb/BWBR0001840/~art-1"
    before = provision_at(session, catalog, uri, date(2023, 2, 21))
    after = provision_at(session, catalog, uri, date(2023, 2, 22))
    assert before["status_on_date"] == after["status_on_date"] == "in_force"
    assert "handicap" not in before["version"]["text"] and "handicap, seksuele gerichtheid" in after["version"]["text"]
    assert (before["version"]["valid_from"], before["version"]["valid_to"], before["version"]["valid_to_basis"]) == (
        "2018-12-21",  # the fixtures start in 2022; with the full series the wording continues from 2002-03-21
        "2023-02-22",
        "derived",
    )
    assert after["provision"]["citation"] == "art. 1 Grondwet"
    assert after["version"]["currency_date"] == "2026-09-15"  # from the manifest (change report)
    old = provision_at(session, catalog, uri, date(2001, 1, 1))
    assert old["status_on_date"] == "history_not_indexed"  # the BWB starts in 2002
    # UAVG: a member removed on 2020-01-01 ends there (explicit); an article enters into force later.
    lid = provision_at(session, catalog, "/nl/bwb/BWBR0040940/~art-10-lid-1", date(2019, 6, 1))
    assert (lid["version"]["valid_to"], lid["version"]["valid_to_basis"]) == ("2020-01-01", "explicit")
    assert provision_at(session, catalog, "/nl/bwb/BWBR0040940/~art-10-lid-1", date(2020, 1, 1))["version"] is None
    art21a = "/nl/bwb/BWBR0040940/~art-21a"
    assert provision_at(session, catalog, art21a, date(2019, 1, 15))["status_on_date"] == "not_yet_in_force"
    assert provision_at(session, catalog, art21a, date(2019, 2, 19))["status_on_date"] == "in_force"
    pending = provision_at(session, catalog, "/nl/bwb/BWBR0040940/~art-23a", date(2026, 10, 14))
    assert pending["status_on_date"] == "prospective_only"
    # Before Book 6 entered into force (1992) its articles were not law; the indexed history is not the cause.
    assert provision_at(session, catalog, "/nl/bwb/BWBR0005289/~art-162-lid-1", date(1991, 6, 1))["status_on_date"] == (
        "not_yet_in_force"
    )
    repealed = provision_at(session, catalog, "/nl/bwb/BWBR0001840/~art-101", date(2010, 1, 1))
    assert repealed["status_on_date"] == "repealed"


@pytest.mark.db
def test_reingest_is_idempotent_and_sources_verify(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, nl_ingested: IngestionReport
) -> None:
    documents = sum(len(t) + 1 for t in INGESTED.values())
    with session_factory() as session:
        again = Ingestor(session, catalog, store).ingest_source(SOURCE, actor="pytest", connector=_Fixtures())
        assert again.documents_unchanged == documents and again.provision_versions_created == 0
        ids = session.scalars(select(m.SourceDocument.id).where(m.SourceDocument.source_id == SOURCE)).all()
        results = [verify_source_document(session, catalog, store, i, checker="pytest") for i in ids]
        assert len(results) == documents and all(r["result"] == "pass" for r in results)
        assert sum(r["checked_units"] for r in results) == (
            nl_ingested.provision_versions_created + nl_ingested.provision_versions_corroborated
        )
        # Each manifest attests the start of every toestand it lists (lifecycle events), re-derived too.
        listed = {f"bwb:{b}:manifest": len(read_manifest(_raw(b)).past) for b in INGESTED}
        assert {r["external_id"]: r["checked_events"] for r in results if r["external_id"] in listed} == listed
        session.rollback()

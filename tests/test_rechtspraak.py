"""Dutch case law from the Open Data van de Rechtspraak: parser, citations, connector and search behaviour.

The fixtures in ``tests/fixtures/rechtspraak`` are real responses of
``https://data.rechtspraak.nl/uitspraken/content?id=<ECLI>`` (gzip-compressed, retrieved 2026-10-14).
"""

from __future__ import annotations

import gzip
import json
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
from ulis.jurisdictions.modules import JudgmentCitationInput, get_module
from ulis.retrieval.search import SearchRequest, search
from ulis.settings import REPO_ROOT
from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.polite_http import FetchError, PoliteHttp
from ulis.sources.connectors.rechtspraak import ROBOTS_URL, RechtspraakConnector, cache_key, content_url
from ulis.sources.normalized import ParsedDocument, ParsedJudgmentMeta
from ulis.sources.parsers.rechtspraak import (
    RechtspraakParseError,
    _ecli_refs,
    _statute_refs,
    parse_rechtspraak,
    work_uri_for,
)
from ulis.sources.registry import licence_gate
from ulis.storage.object_store import LocalObjectStore
from ulis.verification.integrity import verify_source_document

SOURCE = "nl-rechtspraak"
HOST = "data.rechtspraak.nl"
UA = "ULIS-research-bot/0.1 (+https://github.com/CHEDFOX/Blue-Cat-Backend)"
FIX = REPO_ROOT / "tests" / "fixtures" / "rechtspraak"
BWB_FIX = REPO_ROOT / "tests" / "fixtures" / "bwb" / "cache" / "repository.officiele-overheidspublicaties.nl" / "bwb"
URGENDA = "ECLI:NL:HR:2019:2006"
KELDERLUIK = "ECLI:NL:HR:1965:AB7079"
LINDENBAUM = "ECLI:NL:HR:1919:AG1776"
BUREN = "ECLI:NL:HR:2020:591"
KRAKEN = "ECLI:NL:HR:2021:203"
ABRVS = "ECLI:NL:RVS:2020:2660"
ALL = [LINDENBAUM, KELDERLUIK, URGENDA, BUREN, KRAKEN, ABRVS]
BW6, AWB = "BWBR0005289", "BWBR0005537"


def _raw(ecli: str) -> bytes:
    return gzip.decompress((FIX / (ecli.replace(":", "_") + ".xml.gz")).read_bytes())


def _doc(ecli: str) -> ParsedDocument:
    docs = parse_rechtspraak(_raw(ecli))
    assert len(docs) == 1
    return docs[0]


def _para(doc: ParsedDocument, eid: str) -> Any:
    return {p.eid: p for p in doc.paragraphs}[eid]


def _citation(catalog: Catalog, doc: ParsedDocument, eid: str | None, court_name: str) -> str:
    assert doc.judgment is not None
    p = _para(doc, eid) if eid else None
    return get_module("nl").judgment_citation(
        JudgmentCitationInput(
            doc.judgment.case_name,
            doc.judgment.neutral_citation,
            doc.judgment.decision_date,
            doc.judgment.docket,
            doc.judgment.court_id,
            court_name,
            p.num if p else None,
            "nl",
            "NL",
        )
    )


# --------------------------------------------------------------------------- metadata, headnote, considerations


def test_metadata_headnote_and_single_consideration() -> None:
    doc = _doc(BUREN)
    assert doc.doc_kind == "judgment" and doc.language == "nl" and doc.jurisdiction_id == "NL"
    assert doc.judgment == ParsedJudgmentMeta(
        court_id="NL-HR",
        case_name=BUREN,  # the data has no case name, so the ECLI stands in
        neutral_citation=BUREN,
        decision_date=date(2020, 4, 3),
        docket="19/00998",
    )
    assert doc.work_uri == work_uri_for(BUREN) == "/nl/rechtspraak/ECLI:NL:HR:2020:591"
    assert doc.expression_uri == f"{doc.work_uri}/nld@2025-03-21" and doc.version_date == date(2025, 3, 21)
    assert doc.authoritative is False and doc.text_origin == "official" and doc.extent == ["NL"]
    assert doc.ext["procedure"] == ["Artikel 81 RO-zaken", "Cassatie"] and doc.ext["rechtsgebied"] == ["Civiel recht"]
    assert doc.ext["vindplaatsen"] == ["Rechtspraak.nl", "RvdW 2020/501"] and doc.ext["issued"] == "2020-04-02"

    assert [(p.part, p.num) for p in doc.paragraphs] == [
        ("headnote", None),
        ("text", None),
        ("procedure", None),
        ("considerations", "2"),
        ("decision", None),
    ]
    head = doc.paragraphs[0]
    assert head.text == (
        "Art. 81 lid 1 RO. Burenrecht. Onrechtmatige daad; art. 6:162 BW. Verankering uitbouw woning in muur "
        "buurhuis. Erfdienstbaarheid tot inankering? Uitleg akten. Bij herbouw tot woning buren in acht te nemen "
        "afstand van perceelsgrens; art. 5:56 BW; betekenis bestemmingsplan en omgevingsvergunning."
    )
    assert _para(doc, "rov-2").text == (
        "De Hoge Raad heeft de klachten over het arrest van het hof beoordeeld. De uitkomst hiervan is dat deze "
        "klachten niet kunnen leiden tot vernietiging van dat arrest. De Hoge Raad hoeft niet te motiveren waarom hij "
        "tot dit oordeel is gekomen. Bij de beoordeling van deze klachten is het namelijk niet nodig om antwoord te "
        "geven op vragen die van belang zijn voor de eenheid of de ontwikkeling van het recht (zie artikel 81 lid 1 "
        "van de Wet op de rechterlijke organisatie)."
    )
    assert _para(doc, "pv-1").text.startswith("Procesverloop\nVoor het verloop van het geding in feitelijke instanties")
    assert "a. de vonnissen in de zaak C/16/373612/ HL ZA 14-217" in _para(doc, "pv-1").text  # list marker shown
    assert _para(doc, "dec-3").text.startswith(
        "De Hoge Raad:\n- verwerpt het beroep;\n- veroordeelt [eiser] in de kosten"
    )


def test_references_are_source_markup_or_unambiguous_patterns() -> None:
    refs = {r.href: r for r in _doc(BUREN).paragraphs[0].refs}
    # The publisher's formal relations (earlier instance, advisory opinion) are markup; unresolved targets are fine.
    assert refs["/nl/rechtspraak/ECLI:NL:GHARL:2018:10306"].relation == "earlier_instance"
    assert refs["/nl/rechtspraak/ECLI:NL:PHR:2020:57"].relation == "opinion"
    assert {r.extraction_method for r in (refs["/nl/rechtspraak/ECLI:NL:PHR:2020:57"],)} == {"source_markup"}
    art = refs["jci1.31:c:BWBR0005289&artikel=162"]
    assert (art.text, art.relation, art.extraction_method) == ("art. 6:162 BW", "cites", "pattern")
    assert len(refs) == 3  # "art. 5:56 BW" is Book 5, which is not ingested: no reference is made up for it


def test_numbered_paragroups_and_procedure_section() -> None:
    doc = _doc(KRAKEN)
    assert doc.judgment is not None and doc.judgment.docket == "19/01813" and doc.judgment.court_id == "NL-HR"
    assert [(p.eid, p.part, p.num) for p in doc.paragraphs[1:]] == [
        ("txt-u2", "text", None),
        ("pv-1", "procedure", None),  # section 1 "Procesverloop": numbered, but not a consideration
        ("rov-2", "considerations", "2"),
        ("rov-3.1", "considerations", "3.1"),
        ("rov-3.2", "considerations", "3.2"),
        ("dec-4", "decision", None),
    ]
    assert _para(doc, "rov-3.2").text == (
        "Het cassatiemiddel is gegrond. In het licht van de opgelegde taakstraf van acht uren, subsidiair vier dagen "
        "hechtenis is er geen aanleiding om aan het oordeel dat de redelijke termijn is overschreden enig ander "
        "rechtsgevolg te verbinden en zal de Hoge Raad met dat oordeel volstaan."
    )
    sr = [r for r in doc.paragraphs[0].refs if r.extraction_method == "pattern"]
    assert [(r.href, r.text) for r in sr] == [("jci1.31:c:BWBR0001854&artikel=138a", "art. 138a Sr")]


def test_numbering_in_the_text_of_flat_decisions() -> None:
    doc = _doc(ABRVS)
    assert doc.judgment is not None
    assert (doc.judgment.court_id, doc.judgment.docket) == ("NL-ABRVS", "201906618/2/A3")
    numbered = [(p.num, p.part) for p in doc.paragraphs if p.num]
    assert numbered == [(str(n), "considerations") for n in range(1, 7)]
    assert _para(doc, "rov-3").text == (
        "Gelet op artikel 8:29, derde lid, van de Awb beslist de Afdeling of de beperking van de kennisneming van een "
        "stuk gerechtvaardigd is. Deze beslissing vergt een afweging van belangen. Enerzijds speelt hierbij het belang "
        "dat partijen gelijkelijk beschikken over de voor het (hoger) beroep relevante informatie. Daartegenover staat "
        "dat de kennisneming door partijen van bepaalde gegevens het algemeen belang, het belang van één of meer "
        "partijen en/of het belang van derden onevenredig kan schaden."
    )
    # Headings between the numbered considerations stay unnumbered units; the standard headings switch the part.
    assert [(p.part, p.num) for p in doc.paragraphs if p.text in ("Inleiding", "Beoordeling", "Overwegingen")] == [
        ("considerations", None)
    ] * 3
    assert [p.part for p in doc.paragraphs if p.part in ("procedure", "decision")] == ["procedure", "decision"]
    assert _para(doc, "dec-u16").text.startswith("Beslissing\nDe Afdeling bestuursrechtspraak van de Raad van State:")


def test_numbers_in_the_header_are_not_considerations_and_line_breaks_are_kept() -> None:
    # A numbered list of parties before the body ("1. [eiser]") must not become "r.o. 1" (seen in other decisions).
    doc = parse_rechtspraak(_tweak(BUREN, "[eiser],<?linebreak?>wonende", "1.\t[eiser],<?linebreak?>wonende"))[0]
    assert [p.num for p in doc.paragraphs if p.num] == ["2"]
    header = next(p for p in doc.paragraphs if p.part == "text")
    assert "\n1. [eiser],\nwonende te [woonplaats],\n" in header.text  # <?linebreak?> becomes a line break
    assert _para(doc, "pv-1").text.startswith("Procesverloop\n")


def test_old_decision_without_structure_keeps_all_text_unnumbered() -> None:
    doc = _doc(LINDENBAUM)
    assert doc.judgment is not None and doc.judgment.decision_date == date(1919, 1, 31)
    assert all(p.num is None for p in doc.paragraphs)
    assert [p.part for p in doc.paragraphs] == ["headnote", "text", "text", "text", "footnote"]
    assert _para(doc, "fn-1").text.startswith("In de publicatie in de Nederlandse Jurisprudentie (NJ)")
    # The publisher's own "Wetsverwijzing" markup is kept as given (relation "references", not "cites").
    refs = [(r.href, r.text, r.relation, r.extraction_method) for r in doc.paragraphs[0].refs]
    assert refs == [
        ("jci1.31:c:BWBR0005289", "Burgerlijk Wetboek Boek 6", "references", "source_markup"),
        ("jci1.31:c:BWBR0005289&artikel=162", "Burgerlijk Wetboek Boek 6 162", "references", "source_markup"),
    ]


def test_long_decision_footnotes_markers_and_links() -> None:
    doc = _doc(URGENDA)
    footnotes = [p for p in doc.paragraphs if p.part == "footnote"]
    assert len(footnotes) == 51 and all(p.num is None for p in footnotes)
    assert _para(doc, "rov-2.1").text.startswith("In deze zaak kan, blijkens rov. 2 van het arrest van het hof")
    assert "van haar vonnis,[1] alsmede van de feiten" in _para(doc, "rov-2.1").text  # footnote reference shown as [n]
    assert _para(doc, "rov-5.7.1").num == "5.7.1" and _para(doc, "rov-5.7.1").part == "considerations"
    assert _para(doc, "fn-6").text == "HR 5 november 1965, ECLI:NL:HR:1965:AB7079."
    assert [(r.href, r.extraction_method) for r in _para(doc, "fn-6").refs] == [
        (f"/nl/rechtspraak/{KELDERLUIK}", "pattern")
    ]
    assert _para(doc, "dec-9").text.startswith(
        "De Hoge Raad:\n- verwerpt het beroep;\n- veroordeelt de Staat in de kosten"
    )
    own = [r for p in doc.paragraphs for r in p.refs if r.href == work_uri_for(URGENDA)]
    assert own == []  # a decision does not cite itself
    statutes = {r.text for p in doc.paragraphs for r in p.refs if r.href.startswith("jci")}
    assert {"art. 6:162 lid 2 BW", "art. 6:163 BW"} <= statutes
    assert len({p.eid for p in doc.paragraphs}) == len(doc.paragraphs)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("art. 6:162 BW", ["jci1.31:c:BWBR0005289&artikel=162"]),
        ("artikel 6:162 lid 2 BW", ["jci1.31:c:BWBR0005289&artikel=162&lid=2"]),
        ("art. 6:162 lid 2 onder a BW", ["jci1.31:c:BWBR0005289&artikel=162&lid=2"]),
        ("art. 1:3 BW", ["jci1.31:c:BWBR0002656&artikel=3"]),
        ("art. 3:4 lid 2 Awb", ["jci1.31:c:BWBR0005537&artikel=3:4&lid=2"]),
        ("artikel 8:36c Awb", ["jci1.31:c:BWBR0005537&artikel=8:36c"]),
        ("art. 294.2 Sr", ["jci1.31:c:BWBR0001854&artikel=294&lid=2"]),
        ("artikel 138a Sr", ["jci1.31:c:BWBR0001854&artikel=138a"]),
        (
            "art. 120 Grondwet en art. 22 lid 2 UAVG",
            ["jci1.31:c:BWBR0001840&artikel=120", "jci1.31:c:BWBR0040940&artikel=22&lid=2"],
        ),
        # Not unambiguous: no reference is made.
        ("art. 5:37 BW", []),  # Book 5 is not ingested
        ("art. 6:162 en 6:163 BW", []),  # lists of articles are not interpreted
        ("art. 1401 oud BW", []),
        ("art. 81 lid 1 RO", []),
        ("de artikelen 2 en 8 EVRM", []),
        ("art. 6:162 BW (oud)", ["jci1.31:c:BWBR0005289&artikel=162"]),  # "(oud)" is not used with colon numbering
    ],
)
def test_statute_patterns(text: str, expected: list[str]) -> None:
    refs = _statute_refs(text)
    assert [r.href for r in refs] == expected
    assert {r.extraction_method for r in refs} <= {"pattern"}


def test_ecli_pattern_skips_own_and_foreign_identifiers() -> None:
    text = "Zie ECLI:NL:HR:2019:2006, ECLI:NL:GHDHA:2018:2591. Vgl. ECLI:EU:C:2014:2 en ECLI:NL:HR:2020:591."
    assert [r.href for r in _ecli_refs(text, "ECLI:NL:HR:2020:591")] == [
        "/nl/rechtspraak/ECLI:NL:HR:2019:2006",
        "/nl/rechtspraak/ECLI:NL:GHDHA:2018:2591",
    ]


# --------------------------------------------------------------------------- refusals


def _tweak(ecli: str, old: str, new: str) -> bytes:
    text = _raw(ecli).decode("utf-8")
    assert old in text
    return text.replace(old, new, 1).encode("utf-8")


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"<akomaNtoso/>", "not open-rechtspraak"),
        (b"<!DOCTYPE x [<!ENTITY a 'b'>]><open-rechtspraak/>", "unsafe"),
        (b"<open-rechtspraak></open-rechtspraak>", "no rdf:Description"),
        (_raw(BUREN).replace(b"uitspraak", b"conclusie"), "not a decision with text"),
        (_tweak(BUREN, ">19/00998<", ">19/00998 (Engels)<"), "English translation"),
        (_tweak(BUREN, "Hoge_Raad_der_Nederlanden", "Hoge_Raad_Onbekend"), "not mapped to a ULIS court"),
        (
            _tweak(BUREN, "ECLI:NL:HR:2020:591</dcterms:identifier>", "ECLI:NL:RVS:2020:591</dcterms:identifier>"),
            "does not match",
        ),
        (_tweak(BUREN, "<dcterms:language>nl</dcterms:language>", "<dcterms:language>en</dcterms:language>"), "not nl"),
        (_tweak(BUREN, "2020-04-03</dcterms:date>", "gisteren</dcterms:date>"), "invalid dcterms:date"),
    ],
    ids=["root", "doctype", "no-metadata", "conclusie", "translation", "court", "ecli-court", "language", "date"],
)
def test_parser_refuses_what_it_cannot_read_safely(content: bytes, message: str) -> None:
    with pytest.raises(RechtspraakParseError, match=message):
        parse_rechtspraak(content)


# --------------------------------------------------------------------------- citations, profile, source


def test_judgment_citations(catalog: Catalog) -> None:
    assert _citation(catalog, _doc(BUREN), "rov-2", "Hoge Raad") == "HR 3 april 2020, ECLI:NL:HR:2020:591, r.o. 2"
    assert _citation(catalog, _doc(BUREN), None, "Hoge Raad") == "HR 3 april 2020, ECLI:NL:HR:2020:591"
    assert _citation(catalog, _doc(BUREN), "dec-3", "Hoge Raad") == "HR 3 april 2020, ECLI:NL:HR:2020:591"
    assert (
        _citation(catalog, _doc(ABRVS), "rov-3", "Raad van State")
        == "ABRvS 6 november 2020, ECLI:NL:RVS:2020:2660, r.o. 3"
    )
    assert _citation(catalog, _doc(URGENDA), "rov-5.7.1", "Hoge Raad").endswith(
        "20 december 2019, ECLI:NL:HR:2019:2006, r.o. 5.7.1"
    )
    nl = get_module("nl")
    base = {"case_name": "x", "decision_date": date(2018, 10, 9), "docket": "200.178.245/01", "language": "nl"}
    for court_id, name, shown in (
        ("NL-CRVB", "Centrale Raad van Beroep", "CRvB"),
        ("NL-CBB", "College van Beroep voor het bedrijfsleven", "CBb"),
        ("NL-GHDHA", "Gerechtshof Den Haag", "Hof Den Haag"),
        ("NL-RBDHA", "Rechtbank Den Haag", "Rb. Den Haag"),
        ("NL-GH", "Gerechtshoven (courts of appeal)", "Gerechtshoven (courts of appeal)"),
    ):
        cite = nl.judgment_citation(
            JudgmentCitationInput(
                neutral_citation="ECLI:NL:X:2018:1",
                court_id=court_id,
                court_name=name,
                paragraph="12",
                jurisdiction_id="NL",
                **base,
            )
        )
        assert cite == f"{shown} 9 oktober 2018, ECLI:NL:X:2018:1, r.o. 12"
    without_ecli = nl.judgment_citation(
        JudgmentCitationInput(
            "x", None, date(2018, 1, 2), "200.1/01", "NL-GHDHA", "Gerechtshof Den Haag", None, "nl", "NL"
        )
    )
    assert without_ecli == "Hof Den Haag 2 januari 2018, zaaknr. 200.1/01"
    # Legislation citations are unchanged.
    assert nl.validate_ext({"ecli": "ECLI:NL:HR:2020:591"})["ecli"] == "ECLI:NL:HR:2020:591"
    with pytest.raises(ValidationError):
        nl.validate_ext({"ecli": "x", "unknown": 1})
    with pytest.raises(ValidationError):
        nl.validate_ext({"citeertitel": "Burgerlijk Wetboek Boek 6"})  # legislation still needs its BWB id


def test_profile_has_the_courts_of_the_decisions_and_no_formal_precedent(catalog: Catalog) -> None:
    reg = catalog.jurisdictions
    profile = reg.get("NL")
    assert profile.profile_version == "0.2.0" and profile.precedent.doctrine == "no_formal_precedent"
    courts = reg.courts()
    for court_id in ("NL-HR", "NL-ABRVS", "NL-CRVB", "NL-CBB", "NL-GHDHA", "NL-RBDHA"):
        assert reg.court_jurisdiction(court_id) == "NL"
    assert courts["NL-GHDHA"].appeals_to == ["NL-HR"] and courts["NL-GHDHA"].rank == 2
    assert reg.courts_above("NL-RBDHA")[:1] == ["NL-GHDHA"] and "NL-HR" in reg.courts_above("NL-RBDHA")
    assert "nl-rechtspraak" in profile.publication.official_sources
    # Legislation settings are untouched.
    assert profile.extensions["nl"]["abbreviations"]["BWBR0005289"]["cite_as"] == "BW"


def test_source_is_approved_and_uses_this_connector(catalog: Catalog) -> None:
    source = catalog.sources.get(SOURCE)
    licence_gate(source)
    assert source.licence.attribution_required is True and "Rechtspraak.nl" in (source.licence.attribution_text or "")
    assert source.languages == ["nl"] and source.document_kinds == ["judgment"]
    assert isinstance(build_connector(source), RechtspraakConnector)
    manifest = yaml.safe_load((REPO_ROOT / source.access.endpoint).read_text())
    eclis = [d["ecli"] for d in manifest["decisions"]]
    assert len(eclis) == len(set(eclis)) == 30 and all(e.startswith("ECLI:NL:") for e in eclis)
    assert manifest["crawl_delay_seconds"] >= 5 and "(" in manifest["user_agent"]


# --------------------------------------------------------------------------- connector


def _write_cache(cache: Path, eclis: list[str], robots: str | None = None) -> None:
    http = PoliteHttp(cache, UA, allowed_hosts={HOST})
    meta = {"status": 200, "content_type": "application/xml", "retrieved_at": "2026-10-14T16:35:32+00:00"}
    for ecli in eclis:
        path = http.cache_path(content_url(ecli), cache_key(ecli))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_raw(ecli))
        url = content_url(ecli)
        path.with_name(path.name + ".meta.json").write_text(json.dumps({**meta, "url": url, "final_url": url}))
    robots_path = http.cache_path(ROBOTS_URL, "robots.txt")
    robots_path.parent.mkdir(parents=True, exist_ok=True)
    robots_path.write_bytes((robots or "").encode())
    status = {"status": 404} if robots is None else {**meta, "status": 200, "content_type": "text/plain"}
    robots_path.with_name(robots_path.name + ".meta.json").write_text(json.dumps({"url": ROBOTS_URL, **status}))


def _manifest(tmp_path: Path, eclis: list[str], **extra: Any) -> Path:
    body = {
        "schema_version": 1,
        "source_id": SOURCE,
        "base_url": f"https://{HOST}",
        "user_agent": UA,
        "crawl_delay_seconds": 5,
        "decisions": [{"ecli": e} for e in eclis],
        **extra,
    }
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(body))
    return path


def test_connector_offline_from_cache(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    _write_cache(cache, [BUREN, KRAKEN])
    docs = list(RechtspraakConnector(_manifest(tmp_path, [BUREN, KRAKEN]), SOURCE, cache, offline=True).fetch())
    assert [d.external_id for d in docs] == [f"rechtspraak:{BUREN}", f"rechtspraak:{KRAKEN}"]
    assert docs[0].content == _raw(BUREN)  # exact bytes as received
    assert docs[0].uri == f"https://{HOST}/uitspraken/content?id={BUREN}" and docs[0].media_type == "application/xml"
    assert docs[0].metadata["ecli"] == BUREN and docs[0].retrieved_at.year == 2026
    with pytest.raises(FetchError, match="offline and not cached"):
        list(RechtspraakConnector(_manifest(tmp_path, [BUREN, ABRVS]), SOURCE, cache, offline=True).fetch())


def test_connector_refuses_bad_manifests(tmp_path: Path) -> None:
    for eclis, extra, message in (
        (["ECLI:NL:HR:2020:591/../x"], {}, "invalid ECLI"),
        (["ECLI:BE:CASS:2020:1"], {}, "invalid ECLI"),
        ([BUREN, BUREN], {}, "listed twice"),
        ([BUREN], {"base_url": "http://data.rechtspraak.nl"}, "base_url"),
        ([BUREN], {"user_agent": "python-urllib"}, "user_agent"),
        ([BUREN], {"crawl_delay_seconds": 1}, "at least 5"),
        ([], {}, "no decisions"),
    ):
        with pytest.raises(ManifestError, match=message):
            list(RechtspraakConnector(_manifest(tmp_path, eclis, **extra), SOURCE, tmp_path).fetch())
    with pytest.raises(ManifestError, match="source_id"):
        list(RechtspraakConnector(_manifest(tmp_path, [BUREN], source_id="other"), SOURCE, tmp_path).fetch())


def test_connector_obeys_robots_txt_and_never_passes_on_other_documents(tmp_path: Path) -> None:
    blocked = tmp_path / "blocked"
    _write_cache(blocked, [BUREN], robots="User-agent: *\nDisallow: /uitspraken/\n")
    with pytest.raises(FetchError, match="disallowed by"):
        list(RechtspraakConnector(_manifest(tmp_path, [BUREN]), SOURCE, blocked, offline=True).fetch())
    # An allowing robots.txt that names other paths does not block the service.
    allowed = tmp_path / "allowed"
    _write_cache(allowed, [BUREN], robots="User-agent: *\nDisallow: /registers/\n")
    assert len(list(RechtspraakConnector(_manifest(tmp_path, [BUREN]), SOURCE, allowed, offline=True).fetch())) == 1
    # A cached error page, or the document of another ECLI, is not ingested as the requested decision.
    cache = tmp_path / "cache"
    _write_cache(cache, [BUREN])
    page = PoliteHttp(cache, UA, allowed_hosts={HOST}).cache_path(content_url(BUREN), cache_key(BUREN))
    page.write_bytes(b"<html>Fout</html>")
    with pytest.raises(FetchError, match="not well-formed XML|not the open-data document"):
        list(RechtspraakConnector(_manifest(tmp_path, [BUREN]), SOURCE, cache, offline=True).fetch())
    page.write_bytes(_raw(KRAKEN))
    with pytest.raises(FetchError, match="not the open-data document of ECLI:NL:HR:2020:591"):
        list(RechtspraakConnector(_manifest(tmp_path, [BUREN]), SOURCE, cache, offline=True).fetch())


# --------------------------------------------------------------------------- PostgreSQL


class _Legislation:
    """The Dutch legislation the decisions link to: Burgerlijk Wetboek Boek 6 and the Awb (the nl-bwb fixtures).

    The bytes are those of tests/test_bwb.py, which runs first (alphabetical order) and ingests the whole nl-bwb
    fixture set; here the same documents are then "unchanged". Run alone, this module ingests just these two.
    """

    def fetch(self) -> Iterator[RawDocument]:
        for bwb_id, toestand in ((BW6, "2026-07-16_0"), (AWB, "2026-08-15_0")):
            path = BWB_FIX / bwb_id / toestand / "xml" / f"{bwb_id}_{toestand}.xml"
            meta = json.loads(path.with_name(path.name + ".meta.json").read_text())
            yield RawDocument(
                f"bwb:{bwb_id}@{toestand}",
                meta["final_url"],
                "application/xml",
                gzip.decompress(path.with_name(path.name + ".gz").read_bytes()),
                datetime.fromisoformat(meta["retrieved_at"]).astimezone(UTC),
                {"bwb_id": bwb_id, "toestand": toestand},
            )


class _Decisions:
    def fetch(self) -> Iterator[RawDocument]:
        for ecli in ALL:
            yield RawDocument(
                f"rechtspraak:{ecli}",
                content_url(ecli),
                "application/xml",
                _raw(ecli),
                datetime(2026, 10, 14, 16, 0, tzinfo=UTC),
                {"ecli": ecli},
            )


@pytest.fixture(scope="module")
def case_law(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, ingested: IngestionReport
) -> IngestionReport:
    with session_factory() as session:
        legislation = Ingestor(session, catalog, store).ingest_source(
            "nl-bwb", actor="pytest", connector=_Legislation()
        )
        assert legislation.errors == []
        report = Ingestor(session, catalog, store).ingest_source(SOURCE, actor="pytest", connector=_Decisions())
        session.commit()
    return report


def _search(session: Session, catalog: Catalog, **kw: Any) -> dict[str, Any]:
    return search(session, catalog, SearchRequest(jurisdiction="NL", **kw), today=date(2026, 10, 14))


@pytest.mark.db
def test_ingest_report(case_law: IngestionReport) -> None:
    assert case_law.errors == [] and case_law.conflicts == []
    assert case_law.documents_new == 6 and case_law.judgments_created == 6
    assert case_law.paragraphs_created == case_law.passages_indexed == 268
    assert case_law.instruments_created == 0 and case_law.citation_links_created == 40


@pytest.mark.db
def test_search_returns_the_paragraph_with_citation_and_persuasive_authority(
    session: Session, catalog: Catalog, case_law: IngestionReport
) -> None:
    res = _search(session, catalog, q="taakstraf acht uren redelijke termijn overschreden rechtsgevolg")
    hit = res["results"][0]
    assert hit["kind"] == "judgment_paragraph"
    assert hit["citation"] == "HR 16 februari 2021, ECLI:NL:HR:2021:203, r.o. 3.2"
    assert hit["text"].startswith("Het cassatiemiddel is gegrond. In het licht van de opgelegde taakstraf")
    assert hit["authority"]["category"] == "persuasive_precedent" and hit["authority"]["label"] == "Persuasive only"
    assert "no formal precedent" in hit["authority"]["rationale"]
    codes = {lim["code"] for lim in hit["limitations"]}
    assert {"AUTHORITY", "SOURCE_NOT_AUTHENTIC", "PROFILE_UNVERIFIED"} <= codes
    assert hit["source"]["attribution"].startswith("Bron: Rechtspraak.nl")
    assert hit["unit"]["eid"] == "rov-3.2" and hit["unit"]["heading"] == "considerations"
    assert (hit["document"]["court_id"], hit["document"]["court"]) == ("NL-HR", "Hoge Raad der Nederlanden")
    assert hit["document"]["decision_date"] == "2021-02-16" and hit["version"]["authoritative"] is False

    abrvs = _search(session, catalog, q="beperking kennisneming stuk gerechtvaardigd afweging belangen")["results"][0]
    assert abrvs["citation"] == "ABRvS 6 november 2020, ECLI:NL:RVS:2020:2660, r.o. 3"
    assert abrvs["authority"]["category"] == "persuasive_precedent"

    headnote = _search(session, catalog, q="onrechtmatige daad uitbouw woning muur buurhuis")["results"][0]
    assert headnote["citation"] == "HR 3 april 2020, ECLI:NL:HR:2020:591"  # the headnote has no consideration number
    assert headnote["text"].startswith("Art. 81 lid 1 RO. Burenrecht. Onrechtmatige daad; art. 6:162 BW.")


@pytest.mark.db
def test_hoge_raad_label_is_persuasive_even_for_a_forum_court_below_it(
    session: Session, catalog: Catalog, case_law: IngestionReport
) -> None:
    res = _search(session, catalog, q="kelderluik gevaarzetting onrechtmatige daad", forum_court="NL-RBDHA")
    hit = res["results"][0]
    assert hit["citation"] == "HR 5 november 1965, ECLI:NL:HR:1965:AB7079"
    assert hit["authority"]["category"] == "persuasive_precedent" and hit["authority"]["label"] == "Persuasive only"
    assert hit["authority"]["rationale"].startswith(
        "NL follows no formal precedent: decisions are not formally binding."
    )
    assert "very persuasive in practice" in hit["authority"]["rationale"]


@pytest.mark.db
def test_statute_and_decision_links_are_resolved_where_the_target_is_ingested(
    session: Session, case_law: IngestionReport
) -> None:
    instruments = {i.uri: i for i in session.scalars(select(m.Instrument))}
    bw6 = instruments["/nl/bwb/BWBR0005289"]
    art_162 = session.scalar(
        select(m.Provision).where(m.Provision.instrument_id == bw6.id, m.Provision.eid == "art-162")
    )
    assert art_162 is not None

    def links(ecli: str, **where: Any) -> list[m.CitationLink]:
        return list(
            session.scalars(
                select(m.CitationLink).where(m.CitationLink.from_work_uri == work_uri_for(ecli)).filter_by(**where)
            )
        )

    pattern = [lk for lk in links(BUREN, extraction_method="pattern") if lk.to_uri.endswith("artikel=162")]
    assert len(pattern) == 1 and pattern[0].citation_text == "art. 6:162 BW" and pattern[0].relation == "cites"
    assert pattern[0].to_instrument_id == bw6.id and pattern[0].to_provision_id == art_162.id

    # The publisher's Wetsverwijzing markup resolves too, to the instrument and to the article.
    markup = {lk.to_uri: lk for lk in links(LINDENBAUM, extraction_method="source_markup")}
    assert (
        markup["jci1.31:c:BWBR0005289"].to_provision_id is None
        and markup["jci1.31:c:BWBR0005289"].to_instrument_id == bw6.id
    )
    assert markup["jci1.31:c:BWBR0005289&artikel=162"].to_provision_id == art_162.id
    assert markup["jci1.31:c:BWBR0005289&artikel=162"].relation == "references"

    # Urgenda cites the Kelderluik judgment (ingested) and the Rechtbank's (not ingested: stays unresolved).
    judgments = {j.neutral_citation: j for j in session.scalars(select(m.Judgment))}
    to_kelderluik = [lk for lk in links(URGENDA) if lk.to_uri == work_uri_for(KELDERLUIK)]
    assert to_kelderluik and all(lk.to_judgment_id == judgments[KELDERLUIK].id for lk in to_kelderluik)
    to_rechtbank = [lk for lk in links(URGENDA) if lk.to_uri == "/nl/rechtspraak/ECLI:NL:RBDHA:2015:7145"]
    assert to_rechtbank and all(lk.to_judgment_id is None and lk.to_instrument_id is None for lk in to_rechtbank)
    # A statute article of a regulation that is not ingested (Sr is not in this test's legislation) stays unresolved.
    sr = [lk for lk in links(KRAKEN, extraction_method="pattern") if "BWBR0001854" in lk.to_uri]
    assert sr and all(lk.to_instrument_id is None for lk in sr)


@pytest.mark.db
def test_judgments_are_stored_with_court_date_docket_and_version(session: Session, case_law: IngestionReport) -> None:
    rows = {
        j.neutral_citation: j for j in session.scalars(select(m.Judgment).where(m.Judgment.jurisdiction_id == "NL"))
    }
    assert set(ALL) <= set(rows)
    buren = rows[BUREN]
    assert (buren.court_id, buren.decision_date, buren.docket, buren.case_name) == (
        "NL-HR",
        date(2020, 4, 3),
        "19/00998",
        BUREN,
    )
    assert buren.ext["rechtsgebied"] == ["Civiel recht"] and buren.ext["ecli"] == BUREN
    version = session.scalar(select(m.JudgmentVersion).where(m.JudgmentVersion.judgment_id == buren.id))
    assert version is not None and version.language == "nl" and version.authoritative is False
    assert version.uri == f"{work_uri_for(BUREN)}/nld@2025-03-21"
    texts = {
        p.eid: p.text
        for p in session.scalars(
            select(m.JudgmentParagraph).where(m.JudgmentParagraph.judgment_version_id == version.id)
        )
    }
    assert texts["rov-2"] == _para(_doc(BUREN), "rov-2").text


@pytest.mark.db
def test_reingest_is_idempotent_and_sources_verify(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, case_law: IngestionReport
) -> None:
    with session_factory() as session:
        again = Ingestor(session, catalog, store).ingest_source(SOURCE, actor="pytest", connector=_Decisions())
        assert again.documents_unchanged == 6 and again.judgments_created == 0 and again.paragraphs_created == 0
        ids = session.scalars(select(m.SourceDocument.id).where(m.SourceDocument.source_id == SOURCE)).all()
        results = [verify_source_document(session, catalog, store, i, checker="pytest") for i in ids]
        assert len(results) == 6 and all(r["result"] == "pass" for r in results)
        assert sum(r["checked_units"] for r in results) == case_law.paragraphs_created
        session.rollback()

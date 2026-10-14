"""Sejm ELI API (Poland): parser, connector, citations and search behaviour (real excerpts)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ulis.catalog import Catalog
from ulis.db import models as m
from ulis.ingestion.pipeline import IngestionReport, Ingestor, build_connector
from ulis.ingestion.pipeline import resolve_uri as pipeline_resolve_uri
from ulis.jurisdictions.modules import ProvisionCitationInput, get_module
from ulis.retrieval.lookup import instrument_detail, provision_at
from ulis.retrieval.search import SearchRequest, search
from ulis.settings import REPO_ROOT
from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.eli_sejm import EliSejmConnector
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.polite_http import FetchError
from ulis.sources.normalized import ParsedDocument, ParsedProvision
from ulis.sources.parsers.eli_sejm import EliParseError, parse_eli_sejm, resolve_uri
from ulis.sources.registry import LicenceNotApproved, licence_gate
from ulis.storage.object_store import LocalObjectStore
from ulis.verification.integrity import verify_source_document

FIX = REPO_ROOT / "tests" / "fixtures" / "eli_sejm"
META = json.loads((FIX / "meta.json").read_text(encoding="utf-8"))
SOURCE = "pl-eli-sejm"
TODAY = date(2026, 10, 14)
# The order in which the connector yields the documents: for each act its consolidated texts (oldest first), then
# the change report on the act's record.
ORDER = (
    "kc_2023_1610",
    "kc_2024_1061",
    "kc_references",
    "kk_2024_17",
    "uodo_2019_1781",
    "uodo_references",
    "kp_2023_1465",
    "kpa_2023_775",
    "kpa_2024_572",
    "kpa_references",
    "kro_2023_2809",
)
TEXTS = [n for n in ORDER if not n.endswith("_references")]


def _bytes(name: str) -> bytes:
    return (FIX / f"{name}.json").read_bytes()


def _doc(name: str) -> ParsedDocument:
    docs = parse_eli_sejm(_bytes(name))
    assert len(docs) == 1
    return docs[0]


def _prov(doc: ParsedDocument, eid: str) -> ParsedProvision:
    return next(p for p in doc.provisions if p.eid == eid)


def _cite(name: str, eid: str) -> str:
    doc = _doc(name)
    p = _prov(doc, eid)
    return get_module("pl").provision_citation(
        ProvisionCitationInput(
            instrument_title=doc.title,
            instrument_type=doc.instrument_type,
            jurisdiction_id="PL",
            top_kind=p.top_kind,
            num_path=p.num_path,
            language="pl",
            ext=doc.ext,
            eid=p.eid,
        )
    )


# --------------------------------------------------------------------------- parser


def test_kodeks_cywilny_document_facts() -> None:
    doc = _doc("kc_2024_1061")
    assert doc.work_uri == "/pl/du/1964/93" and doc.expression_uri == "/pl/du/1964/93/pol@2024-06-19"
    assert doc.work_title == "Ustawa z dnia 23 kwietnia 1964 r. - Kodeks cywilny."
    assert doc.title == "Ustawa z dnia 23 kwietnia 1964 r. - Kodeks cywilny (tekst jednolity: Dz.U. 2024 poz. 1061)"
    assert doc.short_title == "Kodeks cywilny" and doc.number == "Dz.U. 1964 nr 16 poz. 93"
    assert doc.language == "pl" and doc.instrument_type == "act" and doc.jurisdiction_id == "PL"
    assert doc.work_date == date(1964, 4, 23)
    # the legal-status date ("stan prawny") of the announcement is the date the text is up to date to
    assert doc.version_date == date(2024, 6, 19) and doc.currency_date == date(2024, 6, 19)
    assert doc.validity_basis == "revision_snapshot" and doc.consolidation_kind == "point_in_time"
    assert doc.text_origin == "official_consolidation" and doc.authoritative is False
    assert doc.ext["dz_u"] == "Dz.U. 1964 nr 16 poz. 93" and doc.ext["eli"] == "https://eli.gov.pl/eli/DU/1964/93/ogl"
    assert doc.ext["cite_as"] == "k.c." and doc.outstanding_changes == []
    kinds = {e.event_type: e.date for e in doc.events}
    assert kinds == {
        "enactment": date(1964, 4, 23),
        "entry_into_force": date(1965, 1, 1),
        "version_change": date(2024, 6, 19),
        "consolidation_published": date(2024, 7, 17),
    }
    assert all(p.valid_from == date(2024, 6, 19) and p.valid_to is None and p.extent == ["PL"] for p in doc.provisions)


def test_kodeks_cywilny_provisions_structure_and_exact_text() -> None:
    doc = _doc("kc_2024_1061")
    assert [p.eid for p in doc.provisions if p.parent_eid is None] == [
        "art-2",
        "art-22_1",
        "art-24",
        "art-172",
        "art-263",
        "art-415",
        "art-417",
    ]
    art = _prov(doc, "art-415")
    assert art.text == "Kto z winy swej wyrządził drugiemu szkodę, obowiązany jest do jej naprawienia."
    assert art.kind == "article" and art.num_path == ["art:415"] and art.is_leaf and art.status == "in_force"
    assert art.context_heading == "Księga TRZECIA - ZOBOWIĄZANIA › Tytuł VI - Czyny niedozwolone"
    # an article with paragraphs: composite text on the article, the paragraphs' own text on each
    art417 = _prov(doc, "art-417")
    assert not art417.is_leaf and not art417.has_own_content
    assert art417.text.startswith("§ 1. Za szkodę wyrządzoną przez niezgodne z prawem działanie lub zaniechanie")
    assert art417.text.splitlines()[1].startswith("§ 2. Jeżeli wykonywanie zadań z zakresu władzy publicznej zlecono")
    par1 = _prov(doc, "art-417-par1")
    assert par1.parent_eid == "art-417" and par1.num_path == ["art:417", "par:1"] and par1.kind == "paragraph"
    assert par1.text == (
        "Za szkodę wyrządzoną przez niezgodne z prawem działanie lub zaniechanie przy wykonywaniu władzy publicznej "
        "ponosi odpowiedzialność Skarb Państwa lub jednostka samorządu terytorialnego lub inna osoba prawna "
        "wykonująca tę władzę z mocy prawa."
    )
    # superscripts: art. 22(1)
    sup = _prov(doc, "art-22_1")
    assert sup.num_path == ["art:22_1"] and sup.text.startswith("Za konsumenta uważa się osobę fizyczną")
    assert sup.context_heading and sup.context_heading.endswith(
        "Rozdział I - Zdolność prawna i zdolność do czynności prawnych"
    )


def test_repealed_provisions_keep_the_sources_words_in_a_note() -> None:
    doc = _doc("kc_2024_1061")
    art2 = _prov(doc, "art-2")
    assert art2.status == "repealed" and art2.text == "" and art2.is_leaf
    assert art2.note and art2.note.startswith("(uchylony)") and "Repeal date not stated by the source" in art2.note
    old = _prov(_doc("kc_2023_1610"), "art-172-par3")
    assert old.status == "in_force" and old.text.startswith("Nabyć nieruchomość rolną w rozumieniu przepisów ustawy")
    new = _prov(doc, "art-172-par3")
    assert new.status == "repealed" and new.text == "" and new.note and new.note.startswith("(uchylony)")
    assert _prov(doc, "art-172").text.endswith("§ 3. (uchylony)")  # the printed composite keeps the word


def test_unnumbered_paragraph_is_numbered_from_its_bookmark_and_says_so() -> None:
    doc = _doc("kc_2024_1061")
    par1 = _prov(doc, "art-263-par1")
    assert par1.num_path == ["art:263", "par:1"] and par1.text.startswith(
        "Roszczenie właściciela przeciwko użytkownikowi"
    )
    assert par1.note and "prints this paragraph without its number" in par1.note and "_a263_p1" in par1.note
    assert _prov(doc, "art-263").text.startswith("§ Roszczenie")  # the composite text shows the label as printed
    assert _prov(doc, "art-263-par2").status == "repealed"


def test_kodeks_karny_points_and_footnotes() -> None:
    doc = _doc("kk_2024_17")
    assert doc.version_date == date(2023, 12, 5) and doc.ext["cite_as"] == "k.k."
    par2 = _prov(doc, "art-148-par2")
    assert not par2.is_leaf and par2.has_own_content  # a chapeau, the points and the closing words after them
    assert par2.text.splitlines()[0] == "Kto zabija człowieka:"
    assert par2.text.splitlines()[1] == "1) ze szczególnym okrucieństwem,"
    assert par2.text.splitlines()[-1].startswith("podlega karze pozbawienia wolności na czas nie krótszy od lat 15")
    pkt = _prov(doc, "art-148-par2-pkt1")
    assert pkt.text == "ze szczególnym okrucieństwem," and pkt.num_path == ["art:148", "par:2", "pkt:1"]
    par1 = _prov(doc, "art-148-par1")
    assert par1.text.startswith("Kto zabija człowieka,") and "podlega karze pozbawienia wolności" in par1.text
    # the publisher's footnote is a note, never text
    assert par1.note == "101) Ze zmianą wprowadzoną przez art. 1 pkt 55 lit. a ustawy, o której mowa w odnośniku 2."
    assert "101)" not in par1.text
    assert par1.context_heading == "Część SZCZEGÓLNA › Rozdział XIX - Przestępstwa przeciwko życiu i zdrowiu"


def test_ustawa_o_ochronie_danych_osobowych_ustepy_quotation_and_act_notes() -> None:
    doc = _doc("uodo_2019_1781")
    assert (
        doc.short_title == "ustawa o ochronie danych osobowych"
        and doc.ext["cite_as"] == "ustawy o ochronie danych osobowych"
    )
    ust = _prov(doc, "art-35-ust1")
    assert ust.kind == "subsection" and ust.num_path == ["art:35", "ust:1"]
    assert ust.text.splitlines()[0] == (
        "Przed przystąpieniem do wykonywania obowiązków Prezes Urzędu składa przed Sejmem Rzeczypospolitej Polskiej "
        "ślubowanie o następującej treści:"
    )
    assert ust.text.splitlines()[1].startswith("„Obejmując stanowisko Prezesa Urzędu Ochrony Danych Osobowych")
    assert ust.text.endswith("sumiennie i bezstronnie.”.")
    # the footnotes on the act's title are the act's own notes
    notes = doc.ext["notes"]
    assert notes[0].startswith("1) Niniejsza ustawa służy stosowaniu rozporządzenia") and len(notes) == 2
    assert "dyrektywę Parlamentu Europejskiego i Rady (UE) 2016/680" in notes[1]


def test_kodeks_pracy_preamble_ranges_stray_divisions_and_superscripts() -> None:
    doc = _doc("kp_2023_1465")
    pre = _prov(doc, "preamble")
    assert pre.status == "repealed" and pre.text == "" and pre.note and pre.note.startswith("Preambuła (uchylona)")
    sup = _prov(doc, "art-18_3a")
    assert (
        sup.num_path == ["art:18_3a"]
        and sup.context_heading == "Dział PIERWSZY - Przepisy ogólne › Rozdział IIa - Równe traktowanie w zatrudnieniu"
    )
    rng = _prov(doc, "art-266-280")
    assert (
        rng.status == "repealed"
        and rng.text == ""
        and rng.note
        and "dział dwunasty nie obejmuje art. 266-280" in rng.note
    )
    # a division the source nests inside art. 283 § 2 pkt 9 is hoisted out of the article
    art283 = _prov(doc, "art-283")
    assert "Rozdział" not in art283.text
    stray = _prov(doc, "div-bran_TRZYNASTY-arti_283-para_2-pint_9-chpt_II")
    assert stray.kind == "division" and stray.status == "repealed" and stray.parent_eid is None
    assert stray.note and stray.note.startswith("(zawierający art. 284-290¹ - uchylony)")
    note_division = _prov(doc, "div-bran_CZTERNASTY_a")
    assert note_division.note and note_division.note.startswith("(zawierający art. 295¹ i art. 295²- uchylony)")
    assert [p.eid for p in doc.provisions].index("art-283") < [p.eid for p in doc.provisions].index(stray.eid)
    assert _prov(doc, "art-52-par1").text.startswith("Pracodawca może rozwiązać umowę o pracę bez wypowiedzenia z winy")


def test_publication_addresses_in_the_text_are_text_and_footnotes_are_notes() -> None:
    doc = _doc("kpa_2024_572")
    p = _prov(doc, "art-2a-par1")
    assert "(Dz. Urz. UE L 119 z 04.05.2016, str. 1, z późn. zm.), zwanego dalej „rozporządzeniem 2016/679”," in p.text
    assert p.note == "1) Zmiana wymienionego rozporządzenia została ogłoszona w Dz. Urz. UE L 127 z 23.05.2018, str. 2."
    rng = _prov(doc, "art-164-179")
    assert (
        rng.status == "repealed" and rng.num_path == ["art:164-179"] and rng.note and rng.note.startswith("(uchylone)")
    )


def test_change_report_lists_later_amendments_and_texts_as_outstanding() -> None:
    doc = _doc("kc_references")
    assert doc.consolidation_kind == "change_report" and doc.provisions == []
    assert doc.expression_uri == "/pl/du/1964/93/pol@2024-06-19"  # the text it attaches to
    assert doc.currency_date == date(2024, 6, 19)  # not extended: the text is up to date to its legal status
    assert [c["by_title"] for c in doc.outstanding_changes] == [
        "Dz.U. 2024 poz. 1237",
        "Dz.U. 2025 poz. 769",
        "Dz.U. 2025 poz. 1172",
        "Dz.U. 2025 poz. 1508",
        "Dz.U. 2026 poz. 184",
        "Dz.U. 2026 poz. 507",
        "Tekst jednolity Dz.U. 2025 poz. 1071",
        "Tekst jednolity Dz.U. 2026 poz. 795",
    ]
    first = doc.outstanding_changes[0]
    assert first["effective_date"] == "2024-08-29" and first["by_uri"] == "https://eli.gov.pl/eli/DU/2024/1237/ogl"
    assert first["affects_eid"] is None and "in force from 2024-08-29" in first["description"]
    assert doc.outstanding_changes[5]["effective_date"] == "2028-11-01"  # enacted, not yet in force
    # every amending act with a date is a lifecycle event, including those already part of the text
    amendments = [e for e in doc.events if e.event_type == "amendment"]
    assert len(amendments) == 113 and amendments[0].date == date(1971, 11, 4)
    assert any(e.affecting_uri == "https://eli.gov.pl/eli/DU/2024/1237/ogl" for e in amendments)
    assert [c["by_title"] for c in _doc("uodo_references").outstanding_changes] == [
        "Dz.U. 2026 poz. 252",
        "Dz.U. 2026 poz. 548",
        "Dz.U. 2026 poz. 1003",
    ]
    assert [c["by_title"] for c in _doc("kpa_references").outstanding_changes] == [
        "Dz.U. 2025 poz. 769",
        "Tekst jednolity Dz.U. 2025 poz. 1691",
    ]


def test_parser_rejects_unexpected_input() -> None:
    good = json.loads(_bytes("kk_2024_17"))

    def parse(bundle: dict[str, Any]) -> list[ParsedDocument]:
        return parse_eli_sejm(json.dumps(bundle).encode())

    with pytest.raises(EliParseError, match="not an ELI bundle"):
        parse_eli_sejm(b"<html>captcha</html>")
    with pytest.raises(EliParseError, match="schema"):
        parse({**good, "schema": "other/1"})
    with pytest.raises(EliParseError, match="unsupported bundle kind"):
        parse({**good, "kind": "pdf"})
    with pytest.raises(EliParseError, match="no legal-status date"):
        parse({**good, "tj_record": {**good["tj_record"], "legalStatusDate": None}})
    with pytest.raises(EliParseError, match="exactly one annex"):
        parse({**good, "text_html": good["text_html"].replace("Załącznik", "Dodatek")})
    with pytest.raises(EliParseError, match="does not match its id"):
        parse({**good, "text_html": good["text_html"].replace('data-id="arti_148"', 'data-id="arti_149"')})
    with pytest.raises(EliParseError, match="unknown unit kind"):
        parse(
            {**good, "text_html": good["text_html"].replace("unit_arti pro-text false", "unit_tire pro-text false", 1)}
        )
    with pytest.raises(EliParseError, match="unexpected"):
        parse({**good, "text_html": good["text_html"].replace('data-template="xText"', 'data-template="table"', 1)})
    with pytest.raises(EliParseError, match="a change report needs"):
        parse({**json.loads(_bytes("kc_references")), "attach_to": {"tj_id": "DU/2024/1061", "legal_status_date": ""}})


def test_citation_styles() -> None:
    assert _cite("kc_2024_1061", "art-415") == "art. 415 k.c."
    assert _cite("kc_2024_1061", "art-417-par2") == "art. 417 § 2 k.c."
    assert _cite("kc_2024_1061", "art-22_1") == "art. 22¹ k.c."
    assert _cite("kk_2024_17", "art-148-par1") == "art. 148 § 1 k.k."
    assert _cite("kk_2024_17", "art-148-par2-pkt1") == "art. 148 § 2 pkt 1 k.k."
    assert _cite("kp_2023_1465", "art-18_3a-par1") == "art. 18³ᵃ § 1 k.p."
    assert _cite("kp_2023_1465", "preamble") == "preambuła k.p."
    assert _cite("kpa_2024_572", "art-164-179") == "art. 164-179 k.p.a."
    assert _cite("kro_2023_2809", "art-99_2a") == "art. 99²ᵃ k.r.o."
    assert _cite("uodo_2019_1781", "art-35-ust1") == "art. 35 ust. 1 ustawy o ochronie danych osobowych"
    module = get_module("pl")
    letter = ProvisionCitationInput(
        instrument_title="x",
        instrument_type="act",
        jurisdiction_id="PL",
        top_kind="article",
        num_path=["art:6", "ust:1", "pkt:2", "lit:a"],
        language="pl",
        ext={"cite_as": "ustawy o ochronie danych osobowych"},
    )
    assert module.provision_citation(letter) == "art. 6 ust. 1 pkt 2 lit. a ustawy o ochronie danych osobowych"


def test_citation_without_an_abbreviation_and_unrepresentable_superscripts() -> None:
    module = get_module("pl")
    base = {"instrument_type": "act", "jurisdiction_id": "PL", "top_kind": "article", "language": "pl"}
    plain = ProvisionCitationInput(instrument_title="Ustawa z dnia 1 stycznia 2000 r. o x", num_path=["art:5"], **base)
    assert module.provision_citation(plain) == "art. 5 (Ustawa z dnia 1 stycznia 2000 r. o x)"
    odd = ProvisionCitationInput(instrument_title="T", num_path=["art:5_3q"], ext={"cite_as": "k.c."}, **base)
    assert module.provision_citation(odd) == "art. 5(3q) k.c."
    assert module.validate_ext(
        {"dz_u": "Dz.U. 1964 nr 16 poz. 93", "eli": "https://eli.gov.pl/eli/DU/1964/93/ogl", "short_name": "KC"}
    )
    with pytest.raises(ValueError):
        module.validate_ext({"dz_u": "x", "eli": "y", "short_name": "z", "unknown": 1})


def test_references_resolve_to_work_uris() -> None:
    assert resolve_uri("https://eli.gov.pl/eli/DU/1964/93/ogl") == ("/pl/du/1964/93", None)
    assert resolve_uri("https://api.sejm.gov.pl/eli/acts/DU/1997/553/text.html") == ("/pl/du/1997/553", None)
    assert resolve_uri("https://api.sejm.gov.pl/eli/acts/DU/1964/93/text.html/artykul%3D415") == (
        "/pl/du/1964/93",
        "art-415",
    )
    assert resolve_uri("https://eli.gov.pl/eli/MP/2020/1/ogl") is None
    assert resolve_uri("https://likumi.lv/ta/id/57980") is None
    assert pipeline_resolve_uri("https://eli.gov.pl/eli/DU/2018/1000/ogl") == ("/pl/du/2018/1000", None)


# --------------------------------------------------------------------------- connector


def _write(cache: Path, key: str, data: bytes, content_type: str, retrieved_at: str, status: int = 200) -> None:
    target = cache / "api.sejm.gov.pl" / key
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    target.with_name(target.name + ".meta.json").write_text(
        json.dumps(
            {
                "url": f"https://api.sejm.gov.pl/{key}",
                "final_url": f"https://api.sejm.gov.pl/{key}",
                "status": status,
                "content_type": content_type,
                "retrieved_at": retrieved_at,
            }
        )
    )


def _cache(tmp_path: Path, robots: str | None = None, html_flag: bool = True) -> Path:
    cache = tmp_path / "cache"
    stamp = META["kc_2024_1061"]["retrieved_at"]
    if robots is None:
        _write(cache, "robots.txt", b"", "text/html", stamp, status=404)
    else:
        _write(cache, "robots.txt", robots.encode(), "text/plain", stamp)
    for name in ("kc_2023_1610", "kc_2024_1061", "uodo_2019_1781"):
        bundle = json.loads(_bytes(name))
        tj_id = bundle["tj_id"]
        record = {**bundle["tj_record"], "textHTML": html_flag}
        _write(
            cache,
            f"eli/acts/{tj_id}.json",
            json.dumps(record).encode(),
            "application/json",
            META[name]["retrieved_at"],
        )
        _write(
            cache,
            f"eli/acts/{tj_id}.text.html",
            bundle["text_html"].encode(),
            "text/html;charset=utf-8",
            META[name]["retrieved_at"],
        )
    for name in ("kc_references", "uodo_references"):
        bundle = json.loads(_bytes(name))
        _write(
            cache,
            f"eli/acts/{bundle['act_id']}.json",
            json.dumps(bundle["act_record"]).encode(),
            "application/json",
            META[name]["retrieved_at"],
        )
    return cache


def _manifest(tmp_path: Path, instruments: list[dict[str, Any]] | None = None, **extra: Any) -> Path:
    path = tmp_path / "manifest.yaml"
    data = {
        "schema_version": 1,
        "source_id": SOURCE,
        "base_url": "https://api.sejm.gov.pl/eli",
        "user_agent": "ULIS-test (+https://example.org)",
        "crawl_delay_seconds": 5,
        "instruments": instruments
        if instruments is not None
        else [
            {"key": "kc", "act": "DU/1964/93", "consolidated": ["DU/2023/1610", "DU/2024/1061"]},
            {"key": "uodo", "act": "DU/2018/1000", "consolidated": ["DU/2019/1781"]},
        ],
        **extra,
    }
    path.write_text(yaml.safe_dump(data, allow_unicode=True))
    return path


def test_connector_offline_from_cache(tmp_path: Path) -> None:
    connector = EliSejmConnector(_manifest(tmp_path), SOURCE, _cache(tmp_path), offline=True)
    docs = list(connector.fetch())
    assert [d.external_id for d in docs] == [
        "pl-eli:DU/2023/1610",
        "pl-eli:DU/2024/1061",
        "pl-eli:DU/1964/93:references",
        "pl-eli:DU/2019/1781",
        "pl-eli:DU/2018/1000:references",
    ]
    assert connector.http is not None and connector.http.requests_made == 0
    first = docs[0]
    assert (
        first.uri == "https://api.sejm.gov.pl/eli/acts/DU/2023/1610/text.html"
        and first.media_type == "application/json"
    )
    bundle = json.loads(first.content)
    fixture = json.loads(_bytes("kc_2023_1610"))
    assert (
        bundle["kind"] == "consolidated_text" and bundle["act_id"] == "DU/1964/93" and bundle["tj_id"] == "DU/2023/1610"
    )
    assert bundle["text_html"] == fixture["text_html"] and bundle["tj_record"] == fixture["tj_record"]  # verbatim
    assert (
        bundle["act"]["displayAddress"] == "Dz.U. 1964 nr 16 poz. 93"
        and bundle["act"]["announcementDate"] == "1964-04-23"
    )
    assert first.retrieved_at.year == 2026 and first.metadata["legal_status_date"] == "2023-07-28"
    report = json.loads(docs[2].content)
    assert report["kind"] == "change_report" and report["attach_to"] == {
        "tj_id": "DU/2024/1061",
        "legal_status_date": "2024-06-19",
    }
    # what the connector delivers parses to the same documents as the fixtures
    parsed = parse_eli_sejm(docs[1].content)[0]
    assert parsed.expression_uri == "/pl/du/1964/93/pol@2024-06-19"
    assert [p.eid for p in parsed.provisions] == [p.eid for p in _doc("kc_2024_1061").provisions]
    assert parse_eli_sejm(docs[2].content)[0].outstanding_changes == _doc("kc_references").outstanding_changes
    with pytest.raises(FetchError, match="offline and not cached"):
        list(
            EliSejmConnector(
                _manifest(tmp_path, [{"act": "DU/1997/553", "consolidated": ["DU/2024/17"]}]),
                SOURCE,
                _cache(tmp_path),
                offline=True,
            ).fetch()
        )


def test_connector_refuses_pdf_only_texts_and_wrong_pages(tmp_path: Path) -> None:
    with pytest.raises(FetchError, match="no HTML text"):
        list(EliSejmConnector(_manifest(tmp_path), SOURCE, _cache(tmp_path, html_flag=False), offline=True).fetch())
    cache = _cache(tmp_path)
    page = cache / "api.sejm.gov.pl" / "eli" / "acts" / "DU" / "2024" / "1061.text.html"
    page.write_text("<html>captcha</html>")
    with pytest.raises(FetchError, match="not an ELI act text"):
        list(EliSejmConnector(_manifest(tmp_path), SOURCE, cache, offline=True).fetch())
    other = _cache(tmp_path / "other")
    record = other / "api.sejm.gov.pl" / "eli" / "acts" / "DU" / "2024" / "1061.json"
    record.write_text(json.dumps({**json.loads(record.read_text()), "pos": 1062}))
    with pytest.raises(FetchError, match="not that of DU/2024/1061"):
        list(EliSejmConnector(_manifest(tmp_path), SOURCE, other, offline=True).fetch())
    wrong = _cache(tmp_path / "wrong")
    record = wrong / "api.sejm.gov.pl" / "eli" / "acts" / "DU" / "2024" / "1061.json"
    data = json.loads(record.read_text())
    data["references"] = {"Tekst jednolity dla aktu": [{"id": "DU/1997/553"}]}
    record.write_text(json.dumps(data))
    with pytest.raises(FetchError, match="not recorded as a consolidated text"):
        list(EliSejmConnector(_manifest(tmp_path), SOURCE, wrong, offline=True).fetch())


def test_connector_requires_increasing_legal_status_dates(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    record = cache / "api.sejm.gov.pl" / "eli" / "acts" / "DU" / "2024" / "1061.json"
    record.write_text(json.dumps({**json.loads(record.read_text()), "legalStatusDate": "2023-07-28"}))
    with pytest.raises(FetchError, match="is not after 2023-07-28"):
        list(EliSejmConnector(_manifest(tmp_path), SOURCE, cache, offline=True).fetch())


def test_connector_honours_a_robots_txt_if_one_appears(tmp_path: Path) -> None:
    ok = _cache(tmp_path, robots="User-agent: *\nDisallow:\n")
    assert len(list(EliSejmConnector(_manifest(tmp_path), SOURCE, ok, offline=True).fetch())) == 5
    blocked = _cache(tmp_path / "b", robots="User-agent: *\nDisallow: /eli/\n")
    with pytest.raises(FetchError, match="disallowed by https://api.sejm.gov.pl/robots.txt"):
        list(EliSejmConnector(_manifest(tmp_path), SOURCE, blocked, offline=True).fetch())


def test_connector_refuses_bad_manifests(tmp_path: Path) -> None:
    kc = {"key": "kc", "act": "DU/1964/93", "consolidated": ["DU/2023/1610", "DU/2024/1061"]}
    for instruments, extra, message in (
        ([{**kc, "act": "DU/1964/93/../x"}], {}, "invalid Dziennik Ustaw address"),
        ([{**kc, "consolidated": ["MP/2024/1061"]}], {}, "invalid Dziennik Ustaw address"),
        ([{**kc, "consolidated": []}], {}, "lists no consolidated text"),
        ([{**kc, "consolidated": ["DU/2024/1061", "DU/2023/1610"]}], {}, "oldest first"),
        ([kc, {**kc, "key": "again"}], {}, "listed twice"),
        ([kc], {"base_url": "http://api.sejm.gov.pl/eli"}, "base_url"),
        ([kc], {"base_url": "https://isap.sejm.gov.pl/eli"}, "base_url"),
        ([kc], {"user_agent": "python-urllib"}, "user_agent"),
        ([kc], {"crawl_delay_seconds": 1}, "at least 5"),
        ([], {}, "no instruments"),
    ):
        with pytest.raises(ManifestError, match=message):
            list(EliSejmConnector(_manifest(tmp_path, instruments, **extra), SOURCE, tmp_path, offline=True).fetch())
    with pytest.raises(ManifestError, match="mismatch"):
        list(EliSejmConnector(_manifest(tmp_path, source_id="other"), SOURCE, tmp_path, offline=True).fetch())


def test_real_manifest_and_registration() -> None:
    catalog = Catalog.load(REPO_ROOT / "config")
    source = catalog.sources.get(SOURCE)
    assert isinstance(build_connector(source), EliSejmConnector)
    manifest = yaml.safe_load((REPO_ROOT / source.access.endpoint).read_text(encoding="utf-8"))
    acts = {i["key"]: (i["act"], i["consolidated"]) for i in manifest["instruments"]}
    assert acts["kc"] == ("DU/1964/93", ["DU/2023/1610", "DU/2024/1061"])
    assert {k: v[0] for k, v in acts.items()} == {
        "kc": "DU/1964/93",
        "kk": "DU/1997/553",
        "uodo": "DU/2018/1000",
        "kp": "DU/1974/141",
        "kpa": "DU/1960/168",
        "kro": "DU/1964/59",
    }
    assert sum(len(v[1]) for v in acts.values()) == 11
    assert manifest["base_url"] == "https://api.sejm.gov.pl/eli" and manifest["crawl_delay_seconds"] >= 5
    assert "ULIS-research-bot" in manifest["user_agent"]
    # DU/1997/483 (the Constitution) is PDF only and is not listed
    assert all("DU/1997/483" not in [v[0], *v[1]] for v in acts.values())
    licence_gate(source)
    assert source.licence.review_status == "approved" and source.authority == "official_non_authentic"
    assert source.languages == ["pl"] and "isap.sejm.gov.pl" not in manifest["base_url"]
    profile = catalog.jurisdictions.get("PL")
    assert profile.verification.status == "draft_unverified" and profile.official_languages == ["pl"]
    assert profile.citation.module == "pl" and [m.organisation for m in profile.memberships] == ["EU"]
    assert profile.precedent.doctrine == "no_formal_precedent" and profile.legal_system.families == ["civil_law"]
    assert {c.id for c in profile.courts} >= {"PL-TK", "PL-SN", "PL-NSA"}
    assert profile.publication.official_sources == [SOURCE]


def test_other_sources_remain_gated() -> None:
    catalog = Catalog.load(REPO_ROOT / "config")
    with pytest.raises(LicenceNotApproved):
        licence_gate(catalog.sources.get("in-india-code"))


def test_fixtures_readme_names_the_source_and_licence() -> None:
    text = (FIX / "README.md").read_text(encoding="utf-8")
    assert "api.sejm.gov.pl" in text and "Prawo autorskie art. 4" in text and "2026-10-14" in text
    for name in ORDER:
        info = META[name]
        assert info["url"].startswith("https://api.sejm.gov.pl/eli/acts/DU/") and info["retrieved_at"].startswith(
            "2026-10-14"
        )
        assert (FIX / f"{name}.json").stat().st_size < 100_000


# --------------------------------------------------------------------------- database


class _Fixtures:
    def fetch(self) -> Iterator[RawDocument]:
        for name in ORDER:
            info = META[name]
            yield RawDocument(
                f"pl-eli:fixture/{name}",
                info["url"],
                "application/json",
                _bytes(name),
                datetime.fromisoformat(info["retrieved_at"]).astimezone(UTC),
                {"fixture": name},
            )


@pytest.fixture(scope="module")
def pl_ingested(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, ingested: IngestionReport
) -> IngestionReport:
    with session_factory() as session:
        report = Ingestor(session, catalog, store).ingest_source(SOURCE, actor="pytest", connector=_Fixtures())
        session.commit()
    return report


def _search(session: Session, catalog: Catalog, **kw: Any) -> dict[str, Any]:
    return search(session, catalog, SearchRequest(jurisdiction="PL", **kw), today=TODAY)


@pytest.mark.db
def test_ingest_report_counts(pl_ingested: IngestionReport) -> None:
    assert pl_ingested.errors == [] and pl_ingested.conflicts == []
    assert pl_ingested.documents_new == len(ORDER) and pl_ingested.instruments_created == 6
    assert pl_ingested.instrument_versions_created == len(TEXTS)
    # The second Kodeks cywilny text continues 13 of the 17 provisions of the first (art. 24 § 1 and art. 172 § 3
    # changed, with their articles) and the second k.p.a. text all 6 of the first.
    assert pl_ingested.provision_versions_corroborated == 13 + 6
    expected = sum(len(_doc(n).provisions) for n in TEXTS) - pl_ingested.provision_versions_corroborated
    assert pl_ingested.provision_versions_created == expected
    assert pl_ingested.passages_indexed > 50


@pytest.mark.db
def test_search_finds_exact_text_with_citation_and_limitations(
    session: Session, catalog: Catalog, pl_ingested: IngestionReport
) -> None:
    res = _search(session, catalog, q="Kto z winy swej wyrządził drugiemu szkodę obowiązany jest do jej naprawienia")
    hit = res["results"][0]
    assert hit["citation"] == "art. 415 k.c."
    assert hit["text"] == "Kto z winy swej wyrządził drugiemu szkodę, obowiązany jest do jej naprawienia."
    assert hit["jurisdiction"]["id"] == "PL" and all(h["jurisdiction"]["id"] == "PL" for h in res["results"])
    assert hit["authority"]["category"] == "binding_legislation"
    codes = {lim["code"] for lim in hit["limitations"]}
    assert {"PROFILE_UNVERIFIED", "NOT_AUTHENTIC_TEXT", "SNAPSHOT_DATES", "AS_OF_AFTER_CURRENCY"} <= codes
    assert "OUTSTANDING_CHANGES_INSTRUMENT" in codes  # six amending acts and two newer texts since 2024-06-19
    snapshot = next(lim for lim in hit["limitations"] if lim["code"] == "SNAPSHOT_DATES")
    assert "2023-07-28" in snapshot["message"]  # the earliest indexed text that has this wording
    assert hit["source"]["attribution"].startswith("Źródło: Dziennik Ustaw Rzeczypospolitej Polskiej")
    penal = _search(
        session, catalog, q="Kto zabija człowieka podlega karze pozbawienia wolności na czas nie krótszy od lat 10"
    )
    assert penal["results"][0]["citation"] == "art. 148 § 1 k.k."
    oath = _search(session, catalog, q="Prezes Urzędu składa przed Sejmem ślubowanie o następującej treści")
    assert oath["results"][0]["citation"] == "art. 35 ust. 1 ustawy o ochronie danych osobowych"
    labour = _search(
        session, catalog, q="Pracownicy powinni być równo traktowani w zakresie nawiązania i rozwiązania stosunku pracy"
    )
    assert labour["results"][0]["citation"] == "art. 18³ᵃ § 1 k.p."
    admin = _search(session, catalog, q="organy administracji publicznej stoją na straży praworządności")
    assert admin["results"][0]["citation"] == "art. 7 k.p.a."
    family = _search(session, catalog, q="Małżonkowie mają równe prawa i obowiązki w małżeństwie")
    assert family["results"][0]["citation"] == "art. 23 k.r.o."
    # a repealed provision is not indexed as text
    assert not any(h["citation"] == "art. 2 k.c." for h in _search(session, catalog, q="uchylony")["results"])


@pytest.mark.db
def test_point_in_time_follows_the_snapshots_the_source_states(
    session: Session, catalog: Catalog, pl_ingested: IngestionReport
) -> None:
    uri = "/pl/du/1964/93/~art-24-par1"
    first = provision_at(session, catalog, uri, date(2023, 8, 1))
    assert first["status_on_date"] == "in_force" and first["version"]["valid_from"] == "2023-07-28"
    assert (
        first["version"]["valid_to"] == "2024-06-19"
        and "zadośćuczynienia pieniężnego lub zapłaty" in first["version"]["text"]
    )
    assert first["provision"]["citation"] == "art. 24 § 1 k.c."
    last_day = provision_at(session, catalog, uri, date(2024, 6, 18))
    assert last_day["version"]["valid_from"] == "2023-07-28"
    second = provision_at(session, catalog, uri, date(2024, 6, 19))
    assert second["status_on_date"] == "in_force" and second["version"]["valid_from"] == "2024-06-19"
    assert (
        second["version"]["valid_to"] is None
        and "zadośćuczynienia pieniężnego albo zapłaty" in second["version"]["text"]
    )
    assert {"SNAPSHOT_DATES", "LATER_VERSION_EXISTS"} <= {lim["code"] for lim in first["limitations"]}
    # the provision the second text shows as repealed
    gone = "/pl/du/1964/93/~art-172-par3"
    assert provision_at(session, catalog, gone, date(2024, 1, 1))["status_on_date"] == "in_force"
    repealed = provision_at(session, catalog, gone, date(2024, 7, 1))
    assert repealed["status_on_date"] == "repealed"
    note = next(lim for lim in repealed["limitations"] if lim["code"] == "REPEALED")["message"]
    assert "Repealed with effect from 2024-06-19" in note and "Repeal date not stated by the source" in note
    # before the earliest text held: a history gap, not "not yet in force"
    earlier = provision_at(session, catalog, uri, date(2010, 1, 1))
    assert earlier["status_on_date"] == "history_not_indexed" and earlier["version"] is None
    # an unchanged provision is continued across both texts and is open-ended
    unchanged = provision_at(session, catalog, "/pl/du/1964/93/~art-415", date(2024, 12, 1))
    assert unchanged["status_on_date"] == "in_force" and unchanged["version"]["valid_from"] == "2023-07-28"
    assert unchanged["version"]["valid_to"] is None
    assert "OUTSTANDING_CHANGES_INSTRUMENT" in {lim["code"] for lim in unchanged["limitations"]}


@pytest.mark.db
def test_instrument_detail_lists_texts_events_and_outstanding_changes(
    session: Session, catalog: Catalog, pl_ingested: IngestionReport
) -> None:
    detail = instrument_detail(session, catalog, "/pl/du/1964/93")
    assert detail["number"] == "Dz.U. 1964 nr 16 poz. 93" and detail["work_date"] == "1964-04-23"
    assert detail["ext"]["cite_as"] == "k.c." and detail["type"] == "act"
    versions = detail["versions"]
    assert [v["version_date"] for v in versions] == ["2023-07-28", "2024-06-19"]
    assert all(v["language"] == "pl" and v["authoritative"] is False for v in versions)
    assert all(v["text_origin"] == "official_consolidation" for v in versions)
    assert versions[0]["outstanding_changes"] == [] and versions[1]["currency_date"] == "2024-06-19"
    assert [c["by_title"] for c in versions[1]["outstanding_changes"]][-1] == "Tekst jednolity Dz.U. 2026 poz. 795"
    amendments = [e for e in detail["events"] if e["type"] == "amendment"]
    assert len(amendments) == 113
    assert {"enactment", "entry_into_force", "version_change"} <= {e["type"] for e in detail["events"]}
    assert [p["eid"] for p in detail["provisions"]][:3] == ["art-2", "art-22_1", "art-24"]
    uodo = instrument_detail(session, catalog, "/pl/du/2018/1000")
    assert [v["version_date"] for v in uodo["versions"]] == ["2019-08-29"] and len(
        uodo["versions"][0]["outstanding_changes"]
    ) == 3


@pytest.mark.db
def test_reingest_is_idempotent_and_sources_verify(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, pl_ingested: IngestionReport
) -> None:
    with session_factory() as session:
        again = Ingestor(session, catalog, store).ingest_source(SOURCE, actor="pytest", connector=_Fixtures())
        assert again.documents_unchanged == len(ORDER) and again.provision_versions_created == 0
        ids = session.scalars(select(m.SourceDocument.id).where(m.SourceDocument.source_id == SOURCE)).all()
        results = [verify_source_document(session, catalog, store, i, checker="pytest") for i in ids]
        assert len(results) == len(ORDER) and all(r["result"] == "pass" for r in results)
        attested = sum(r["checked_units"] for r in results)
        assert attested == pl_ingested.provision_versions_created + pl_ingested.provision_versions_corroborated
        assert sum(r["checked_events"] for r in results) > 200  # the amending acts of the change reports
        session.rollback()

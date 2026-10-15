"""OECD Compendium of Legal Instruments: connector and parser ``oecd_legal_instruments``, module ``oecd``, gates."""

from __future__ import annotations

import base64
import gzip
import importlib.util
import json
from collections.abc import Iterator
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from sqlalchemy.orm import Session, sessionmaker

from ulis.catalog import Catalog
from ulis.ingestion.pipeline import CONNECTORS, PARSER_VERSIONS, PARSERS, IngestionReport, Ingestor, build_connector
from ulis.jurisdictions.modules import MODULES, ProvisionCitationInput, get_module
from ulis.retrieval.lookup import provision_at
from ulis.retrieval.search import SearchRequest, search
from ulis.settings import REPO_ROOT
from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.oecd_legal_instruments import (
    BUNDLE_SCHEMA,
    MAX_INSTRUMENTS,
    OecdLegalInstrumentsConnector,
    bundle_bytes,
    load_manifest,
)
from ulis.sources.connectors.polite_http import PoliteHttp
from ulis.sources.normalized import ParsedDocument, ParsedProvision
from ulis.sources.parsers.constitution_pages_b import html_source
from ulis.sources.parsers.oecd_legal_instruments import (
    OecdParseError,
    instrument_type,
    parse_oecd_legal_instruments,
    read_bundle,
)
from ulis.sources.registry import LicenceNotApproved, licence_gate
from ulis.storage.object_store import LocalObjectStore
from ulis.verification.integrity import verify_source_document

FIX = REPO_ROOT / "tests" / "fixtures" / "oecd_legal_instruments"
MANIFEST = REPO_ROOT / "config" / "manifests" / "int-oecd-legal-instruments.yaml"
SOURCE_ID = "int-oecd-legal-instruments"
TODAY = date(2026, 10, 15)
FIXTURES = {
    "0162en": "OECD-LEGAL-0162.en.json.gz",
    "0162fr": "OECD-LEGAL-0162.fr.json.gz",
    "0293en": "OECD-LEGAL-0293.en.json.gz",
    "0383fr": "OECD-LEGAL-0383.fr.json.gz",
    "0307fr": "OECD-LEGAL-0307.fr.json.gz",
    "0449en": "OECD-LEGAL-0449.en.json.gz",
    "0413en": "OECD-LEGAL-0413.en.json.gz",
}


def _bytes(key: str) -> bytes:
    return gzip.decompress((FIX / FIXTURES[key]).read_bytes())


_PARSED: dict[str, ParsedDocument] = {}


def _doc(key: str) -> ParsedDocument:
    if key not in _PARSED:
        docs = parse_oecd_legal_instruments(_bytes(key))
        assert len(docs) == 1
        _PARSED[key] = docs[0]
    return _PARSED[key]


def _prov(doc: ParsedDocument, eid: str) -> ParsedProvision:
    return next(p for p in doc.provisions if p.eid == eid)


def _cite(doc: ParsedDocument, eid: str) -> str:
    p = _prov(doc, eid)
    return get_module(doc.ext_module or "default").provision_citation(
        ProvisionCitationInput(
            instrument_title=doc.title,
            instrument_type=doc.instrument_type,
            jurisdiction_id=doc.jurisdiction_id,
            top_kind=p.top_kind,
            num_path=p.num_path,
            language=doc.language,
            ext=doc.ext,
            eid=p.eid,
        )
    )


# --------------------------------------------------------------------------- bundle and types


def test_bundle_is_read_and_foreign_input_is_refused() -> None:
    bundle = read_bundle(_bytes("0293en"))
    assert bundle["schema"] == BUNDLE_SCHEMA and bundle["key"] == "OECD/LEGAL/0293" and bundle["language"] == "en"
    assert bundle["html"].lstrip().startswith(b"<div class=\"WordSection1\"")
    assert "lastPublishedBy" not in bundle["record"]["statusSummary"]  # a staff member's name and e-mail are not kept
    with pytest.raises(OecdParseError):
        read_bundle(b"not json")
    with pytest.raises(OecdParseError):
        read_bundle(json.dumps({"schema": "other/1"}).encode())
    broken = {**json.loads(_bytes("0293en")), "html_b64": "!!!"}
    with pytest.raises(OecdParseError):
        read_bundle(json.dumps(broken).encode())
    missing = {k: v for k, v in json.loads(_bytes("0293en")).items() if k != "record"}
    with pytest.raises(OecdParseError, match="record"):
        read_bundle(json.dumps(missing).encode())


@pytest.mark.parametrize(
    ("title", "kind"),
    [
        ("Recommendation of the Council on Artificial Intelligence", "recommendation"),
        ("Decision of the Council on the Guidelines for Multinational Enterprises on Responsible Business Conduct", "decision"),
        ("Declaration on Propriety, Integrity and Transparency", "declaration"),
        ("Ministerial Declaration on the Protection of Privacy on Global Networks", "declaration"),
        ("Convention on Combating Bribery of Foreign Public Officials", "treaty"),
        ("Protocol amending the Convention on Mutual Administrative Assistance in Tax Matters", "treaty"),
        ("Multilateral Convention to Implement Tax Treaty Related Measures", "treaty"),
        ("Arrangement on Officially Supported Export Credits", "arrangement"),
        ("Revised Recommendation of the Council on Chemical Accidents", "recommendation"),
        ("Principles of Corporate Governance", "other"),
    ],
)
def test_instrument_type_from_the_title(title: str, kind: str) -> None:
    assert instrument_type(title) == kind


# --------------------------------------------------------------------------- parser on real instruments


def test_convention_is_split_into_articles_with_the_record_dates() -> None:
    doc = _doc("0293en")
    assert doc.jurisdiction_id == "INT-OECD" and doc.ext_module == "oecd" and doc.instrument_type == "treaty"
    assert doc.work_uri == "/int/oecd/0293" and doc.number == "OECD/LEGAL/0293" and doc.language == "en"
    assert doc.authoritative and doc.text_origin == "official_authentic" and doc.validity_basis == "source_periods"
    assert doc.work_date == date(1997, 11, 21) and doc.ext["key"] == "OECD/LEGAL/0293" and doc.ext["numbering"] == "articles"
    assert [p.eid for p in doc.provisions] == ["preamble", *[f"art-{n}" for n in range(1, 18)]]
    art = _prov(doc, "art-5")
    assert art.text.startswith("Enforcement\nInvestigation and prosecution of the bribery of a foreign public official")
    assert art.valid_from == date(1999, 2, 15) and art.valid_to is None and art.status == "in_force"
    assert [e.event_type for e in doc.events] == ["enactment", "entry_into_force", "version_change"]
    assert _cite(doc, "art-5") == "OECD/LEGAL/0293 art. 5" and _cite(doc, "preamble") == "OECD/LEGAL/0293 preamble"
    assert doc.version_date == date(2026, 9, 16) and doc.currency_date == date(2026, 10, 15)


def test_abrogated_instrument_is_valid_until_its_abrogation_date_in_both_languages() -> None:
    en, fr = _doc("0162en"), _doc("0162fr")
    assert [p.eid for p in en.provisions] == [p.eid for p in fr.provisions] == ["preamble", "para-i", "para-ii"]
    for doc in (en, fr):
        assert {(p.valid_from, p.valid_to) for p in doc.provisions} == {(date(1978, 4, 26), date(2023, 6, 8))}
        assert [e.event_type for e in doc.events] == ["enactment", "repeal", "version_change"]
        assert doc.ext["abrogated_on"] == "2023-06-08" and doc.authoritative
    assert _prov(en, "para-ii").text.startswith("II. INSTRUCTS the Committee of Experts")
    assert _prov(fr, "para-ii").text.startswith("II. CHARGE le Comité d'experts")
    assert en.expression_uri != fr.expression_uri and en.work_uri == fr.work_uri


def test_declaration_numbered_paragraphs_and_notes_after_the_rule() -> None:
    doc = _doc("0383fr")
    assert [p.eid for p in doc.provisions] == ["preamble", *[f"para-{n}" for n in range(1, 11)], "blk-0001"]
    assert _prov(doc, "para-1").text.startswith("1) Probité, intégrité et transparence constituent la pierre angulaire")
    assert doc.instrument_type == "declaration" and doc.language == "fr"
    block = _prov(doc, "blk-0001")
    assert block.text.startswith("N.B. : La présente Déclaration est ouverte à l’adhésion de pays non membres") and block.kind == "block"
    assert _cite(doc, "para-1") == "OECD/LEGAL/0383 para. 1" and _cite(doc, "blk-0001") == "OECD/LEGAL/0383 text block 1"


def test_decision_with_attached_procedures_numbers_the_second_set_by_its_heading() -> None:
    doc = _doc("0307fr")
    assert doc.instrument_type == "decision"
    para = _prov(doc, "para-iii-2")
    assert para.num_path == ["III.2"] and para.parent_eid == "para-iii" and para.valid_from == date(2023, 6, 8)
    assert para.text.startswith("2. La présente Décision sera réexaminée périodiquement.")
    proc = _prov(doc, "procedures-para-i")
    assert proc.num_path == ["Procédures", "I"] and proc.heading == "Procédures" and not proc.is_leaf
    assert _prov(doc, "procedures-para-i-1").text == "1. visibles ;"
    assert _cite(doc, "para-iii-2") == "OECD/LEGAL/0307 para. III.2"
    assert _cite(doc, "procedures-para-i-1") == "OECD/LEGAL/0307 procédures para. I.1"
    assert [e.event_type for e in doc.events] == ["enactment", "amendment", "version_change"]


def test_recommendation_sections_and_decimal_principles() -> None:
    doc = _doc("0449en")
    assert doc.ext["numbering"] == "paragraphs" and doc.instrument_type == "recommendation"
    assert [p.eid for p in doc.provisions if p.kind == "section"] == ["sec-1", "sec-2"]
    principle = _prov(doc, "para-1-2")
    assert principle.text.startswith("1.2. Respect for the rule of law, human rights and democratic values")
    assert principle.context_heading == "Section 1: Principles for responsible stewardship of trustworthy AI"
    assert principle.valid_from == date(2024, 5, 3)  # the last amendment dates the current wording
    assert _cite(doc, "para-1-2") == "OECD/LEGAL/0449 para. 1.2" and _cite(doc, "sec-2") == "OECD/LEGAL/0449 section 2"
    assert _cite(doc, "para-ii") == "OECD/LEGAL/0449 para. II"
    assert {"para-1-1", "para-1-5", "para-2-1", "para-2-5", "para-viii"} <= {p.eid for p in doc.provisions}


def test_appendix_units_are_cited_with_the_appendix_and_lettered_principles_nest() -> None:
    doc = _doc("0413en")
    assert _prov(doc, "appendix").kind == "annex" and _prov(doc, "appendix").text.startswith("APPENDIX\nPRINCIPLES OF CORPORATE GOVERNANCE")
    roman = _prov(doc, "appendix-para-i")
    letter = _prov(doc, "appendix-para-i-a")
    assert roman.num_path == ["Appendix", "I"] and not roman.is_leaf and roman.has_own_content
    assert letter.num_path == ["Appendix", "I.A"] and letter.parent_eid == "appendix-para-i" and letter.is_leaf
    assert letter.text.startswith("I.A. The corporate governance framework should be developed with a view to its impact")
    assert _cite(doc, "appendix-para-i-a") == "OECD/LEGAL/0413 appendix para. I.A"
    assert _cite(doc, "appendix") == "OECD/LEGAL/0413 appendix"
    # the body of the Recommendation (before the appendix) keeps its own numbering
    assert [p.eid for p in doc.provisions[:6]] == ["preamble", "para-i", "para-ii", "para-iii", "para-iv", "appendix"]
    assert len({p.eid for p in doc.provisions}) == len(doc.provisions)


@pytest.mark.parametrize("key", sorted(FIXTURES))
def test_no_line_of_the_source_is_lost_or_changed(key: str) -> None:
    """Every line of the HTML text is in a provision's text (or is a heading kept beside it): the words are the OECD's."""
    bundle = read_bundle(_bytes(key))
    lines = [ln for ln in html_source(bundle["html"], bundle.get("content_type")).lines if ln]
    doc = _doc(key)
    body = "\n".join(p.text for p in doc.provisions)
    headings = " ".join(p.heading or "" for p in doc.provisions)
    missing = [ln for ln in lines if ln not in body and ln not in headings]
    assert missing == []
    assert all(p.text for p in doc.provisions) and [p.sort_order for p in doc.provisions] == list(range(len(doc.provisions)))
    order = [body.index(ln) for ln in lines if ln in body]
    assert order == sorted(order)  # reading order is kept


# --------------------------------------------------------------------------- module


def test_module_is_registered_and_validates_its_ext() -> None:
    module = get_module("oecd")
    assert module.id == "oecd" and "oecd" in MODULES
    doc = _doc("0293en")
    assert module.validate_ext(doc.ext)["key"] == "OECD/LEGAL/0293"
    with pytest.raises(ValueError):
        module.validate_ext({**doc.ext, "unexpected": 1})
    with pytest.raises(ValueError):
        module.validate_ext({**doc.ext, "key": "OECD/LEGAL/12"})


def test_registrations() -> None:
    assert CONNECTORS["oecd_legal_instruments"] is OecdLegalInstrumentsConnector
    assert PARSERS["oecd_legal_instruments"] is parse_oecd_legal_instruments
    assert PARSER_VERSIONS["oecd_legal_instruments"] == "1.0.0"


# --------------------------------------------------------------------------- connector


HOST = "legalinstruments.oecd.org"
UA = "ULIS-research-bot/0.1 (+https://github.com/CHEDFOX/Blue-Cat-Backend)"
HTML = b"<html><body><p>THE COUNCIL,</p><p>I. AGREES that this is a test.</p><p>II. INVITES the test to pass.</p></body></html>"


def _write(client: PoliteHttp, url: str, body: bytes, *, status: int = 200, cache_key: str | None = None) -> None:
    path = client.cache_path(url, cache_key)  # the cache layout is PoliteHttp's; offline reads it back
    meta = {
        "url": url,
        "final_url": url,
        "status": status,
        "content_type": "text/html" if url.endswith("html") else "application/json",
        "retrieved_at": "2026-10-15T09:00:00+00:00",
    }
    PoliteHttp._store(path, path.with_name(path.name + ".meta.json"), body, meta)


def _offline(tmp_path: Path) -> PoliteHttp:
    return PoliteHttp(tmp_path / "cache", UA, allowed_hosts={HOST}, offline=True)


def _record(ident: int = 7, key: str = "OECD/LEGAL/0007", langs: tuple[str, ...] = ("en", "fr")) -> dict[str, Any]:
    return {
        "id": ident,
        "key": key,
        "title": {"name": [{"lang": "en", "value": "Recommendation of the Council on Tests"}, {"lang": "fr", "value": "Recommandation du Conseil sur les essais"}]},
        "statusSummary": {
            "adoptionDate": "2020-01-02",
            "inForceDate": "2020-01-02",
            "lastUpdateDate": "2020-01-02",
            "lastPublishDate": "2021-03-04T10:00:00+01:00",
            "lastPublishedBy": "Some.Person@oecd.org",
        },
        "bodyText": {"ref": [{"format": "html", "lang": lang, "uri": f"/public/doc/{ident}/{lang}-text.html"} for lang in langs]},
    }


def _manifest(tmp_path: Path, instruments: list[dict[str, Any]] | None = None, **overrides: Any) -> Path:
    body: dict[str, Any] = {
        "schema_version": 1,
        "source_id": "xx-oecd",
        "host": HOST,
        "user_agent": UA,
        "crawl_delay_seconds": 5,
        "languages": ["en", "fr"],
        "instruments": instruments if instruments is not None else [{"id": 7, "key": "OECD/LEGAL/0007", "title": "Tests"}],
        **overrides,
    }
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(body), encoding="utf-8")
    return path


def _seed(tmp_path: Path, record: dict[str, Any], robots: bytes = b"User-agent: *\nDisallow:\n") -> None:
    client = _offline(tmp_path)
    _write(client, f"https://{HOST}/robots.txt", robots)
    _write(client, f"https://{HOST}/api/instruments/{record['id']}", json.dumps(record).encode(), cache_key=f"api/instruments/{record['id']}.json")
    for ref in record["bodyText"]["ref"]:
        _write(client, f"https://{HOST}{ref['uri']}", HTML, cache_key=f"doc/{record['id']}.{ref['lang']}.html")


def test_connector_yields_one_bundle_per_language_and_drops_the_staff_name(tmp_path: Path) -> None:
    _seed(tmp_path, _record())
    connector = OecdLegalInstrumentsConnector(_manifest(tmp_path), "xx-oecd", tmp_path / "cache", offline=True)
    raws = list(connector.fetch())
    assert connector.fetch_errors == [] and [r.external_id for r in raws] == ["OECD/LEGAL/0007/en", "OECD/LEGAL/0007/fr"]
    assert raws[0].media_type == "application/json" and raws[0].uri == f"https://{HOST}/public/doc/7/en-text.html"
    bundle = read_bundle(raws[0].content)
    assert bundle["html"] == HTML and bundle["retrieved_on"] == "2026-10-15" and bundle["record_url"] == f"https://{HOST}/api/instruments/7"
    assert "lastPublishedBy" not in bundle["record"]["statusSummary"] and "Some.Person" not in raws[0].content.decode()
    doc = parse_oecd_legal_instruments(raws[1].content)[0]
    assert doc.language == "fr" and doc.title == "Recommandation du Conseil sur les essais" and doc.work_title.startswith("Recommendation")
    assert [p.eid for p in doc.provisions] == ["preamble", "para-i", "para-ii"] and doc.version_date == date(2021, 3, 4)
    assert _prov(doc, "para-i").valid_from == date(2020, 1, 2)


def test_connector_reports_missing_wrong_and_textless_instruments_and_goes_on(tmp_path: Path) -> None:
    client = _offline(tmp_path)
    _seed(tmp_path, _record(7, "OECD/LEGAL/0007"))
    _write(client, f"https://{HOST}/api/instruments/8", b"", status=404, cache_key="api/instruments/8.json")
    _write(client, f"https://{HOST}/api/instruments/9", json.dumps(_record(9, "OECD/LEGAL/0010")).encode(), cache_key="api/instruments/9.json")
    only_en = _record(10, "OECD/LEGAL/0011", langs=("en",))
    _seed(tmp_path, only_en)
    entries = [
        {"id": 8, "key": "OECD/LEGAL/0008", "title": "Gone"},
        {"id": 9, "key": "OECD/LEGAL/0009", "title": "Wrong key"},
        {"id": 10, "key": "OECD/LEGAL/0011", "title": "English only"},
        {"id": 7, "key": "OECD/LEGAL/0007", "title": "Fine"},
    ]
    connector = OecdLegalInstrumentsConnector(_manifest(tmp_path, entries), "xx-oecd", tmp_path / "cache", offline=True)
    ids = [r.external_id for r in connector.fetch()]
    assert ids == ["OECD/LEGAL/0011/en", "OECD/LEGAL/0007/en", "OECD/LEGAL/0007/fr"]
    errors = " | ".join(connector.fetch_errors)
    assert "not found (404)" in errors and "OECD/LEGAL/0010" in errors and "no HTML text in fr" in errors


def test_connector_obeys_a_robots_file_that_closes_the_site_or_a_path(tmp_path: Path) -> None:
    _seed(tmp_path, _record(), robots=b"User-agent: *\nDisallow: /\n")
    connector = OecdLegalInstrumentsConnector(_manifest(tmp_path), "xx-oecd", tmp_path / "cache", offline=True)
    assert list(connector.fetch()) == [] and "disallows this crawler" in connector.fetch_errors[0]
    other = tmp_path / "second"
    other.mkdir()
    _seed(other, _record(), robots=b"User-agent: *\nDisallow: /public/\n")
    connector = OecdLegalInstrumentsConnector(_manifest(other), "xx-oecd", other / "cache", offline=True)
    assert list(connector.fetch()) == []
    assert connector.fetch_errors and all("disallowed by robots.txt" in e for e in connector.fetch_errors)


def test_connector_treats_an_unreadable_robots_file_as_a_refusal(tmp_path: Path) -> None:
    connector = OecdLegalInstrumentsConnector(_manifest(tmp_path), "xx-oecd", tmp_path / "cache", offline=True)
    assert list(connector.fetch()) == [] and "nothing is fetched" in connector.fetch_errors[0]


def test_manifest_refusals(tmp_path: Path) -> None:
    ok = {"id": 7, "key": "OECD/LEGAL/0007", "title": "Tests"}
    assert load_manifest(_manifest(tmp_path), "xx-oecd")["instruments"] == [ok]
    bad: list[tuple[dict[str, Any], str]] = [
        ({"source_id": "other"}, "mismatch"),
        ({"host": "www.example.org"}, "host must be"),
        ({"user_agent": "bot"}, "user_agent"),
        ({"crawl_delay_seconds": 1}, "at least 5"),
        ({"languages": ["en", "de"]}, "languages"),
        ({"languages": []}, "languages"),
        ({"instruments": []}, "no instruments"),
        ({"instruments": [{"id": "7", "key": "OECD/LEGAL/0007"}]}, "invalid instrument"),
        ({"instruments": [{"id": 7, "key": "OECD/LEGAL/7"}]}, "invalid key"),
        ({"instruments": [ok, {"id": 8, "key": "OECD/LEGAL/0007"}]}, "listed twice"),
        ({"instruments": [ok, {"id": 7, "key": "OECD/LEGAL/0008"}]}, "listed twice"),
    ]
    for overrides, message in bad:
        with pytest.raises(ManifestError, match=message):
            load_manifest(_manifest(tmp_path, **overrides), "xx-oecd")
    too_many = [{"id": n, "key": f"OECD/LEGAL/{n:04d}", "title": "t"} for n in range(1, MAX_INSTRUMENTS + 2)]
    with pytest.raises(ManifestError, match="whole or significant part"):
        load_manifest(_manifest(tmp_path, too_many), "xx-oecd")
    assert len(load_manifest(_manifest(tmp_path, too_many[:MAX_INSTRUMENTS]), "xx-oecd")["instruments"]) == MAX_INSTRUMENTS


def test_bundle_bytes_is_deterministic() -> None:
    assert bundle_bytes({"b": 1, "a": "é"}) == bundle_bytes({"a": "é", "b": 1}) == '{"a":"é","b":1}'.encode()
    assert base64.b64decode(base64.b64encode(HTML)) == HTML


# --------------------------------------------------------------------------- catalogue, manifest and gates


def test_committed_manifest_is_a_short_selection_of_individual_instruments(catalog: Catalog) -> None:
    manifest = load_manifest(MANIFEST, SOURCE_ID)
    entries = manifest["instruments"]
    assert 10 <= len(entries) <= MAX_INSTRUMENTS and len(entries) < 504 * 0.05
    assert [e["key"] for e in entries] == sorted(e["key"] for e in entries)
    assert manifest["languages"] == ["en", "fr"] and manifest["crawl_delay_seconds"] >= 5 and "ULIS-research-bot" in manifest["user_agent"]
    spec = importlib.util.spec_from_file_location("build_oecd_manifest", REPO_ROOT / "scripts" / "build_oecd_manifest.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert sorted(module.SELECTION) == sorted(e["id"] for e in entries)  # the script and the manifest agree
    source = catalog.sources.get(SOURCE_ID)
    assert source.connector == source.parser == "oecd_legal_instruments" and source.access.endpoint == "config/manifests/int-oecd-legal-instruments.yaml"
    assert isinstance(build_connector(source, REPO_ROOT, Path("/nonexistent/cache")), OecdLegalInstrumentsConnector)


def test_oecd_source_quotes_both_notices_and_is_refused_in_a_commercial_deployment(catalog: Catalog) -> None:
    source = catalog.sources.get(SOURCE_ID)
    licence_gate(source, commercial=False)
    with pytest.raises(LicenceNotApproved, match="commercial"):
        licence_gate(source, commercial=True)
    text = source.licence.name + " " + (source.licence.reviewed_by or "")
    assert "This document is provided free of charge" in text and "as long as it is not altered in any way" in text
    assert "It may not be sold" in text and "shall not be reproduced in whole or significant part" in text
    assert "HTTP 403" in (source.licence.reviewed_by or "") and source.licence.review_status == "approved"
    assert source.licence.reviewed_on == TODAY and source.licence.commercial_use == "unknown"
    assert source.jurisdictions == ["INT-OECD"] and source.languages == ["en", "fr"] and source.authority == "official_authentic"


# source id -> (status, strings the finding must contain)
BLOCKED = {
    "wipo-lex": ("rejected", ["perform automated queries", "web scraping", "academic, research and non-commercial purposes", "/edocs/lexdocs/"]),
    "uncitral-texts": ("rejected", ["permission is needed to reproduce UNCITRAL texts"]),
    "ilo-normlex": ("planned", ["HTTP 403", "do not automatically benefit from a Creative Commons licence", "3 May 2023"]),
    "wto-legal-texts": ("planned", ["User-agent: * / Disallow: /", "Disallow: *", "not named"]),
    "unidroit-instruments": ("planned", ["except for commercial purposes", "provided the source is duly acknowledged"]),
    "hcch-conventions": ("planned", ["except for commercial purposes", "Crawl-delay: 10"]),
}


def test_every_other_organisation_is_recorded_and_refused_by_the_gate(catalog: Catalog) -> None:
    for source_id, (status, needles) in BLOCKED.items():
        source = catalog.sources.get(source_id)
        assert source.status == status and source.jurisdictions == ["INT"]
        assert source.licence.review_status in ("rejected", "pending_review")
        assert source.licence.reviewed_on == TODAY and source.connector is None and source.parser is None
        text = " ".join([source.access.endpoint or "", source.rate_limits or "", source.licence.name, source.licence.reviewed_by or ""])
        for needle in needles:
            assert needle in text, (source_id, needle)
        assert "ask the publisher" not in text.lower() and "contact the" not in (source.licence.notes or "").lower()
        with pytest.raises(LicenceNotApproved):
            licence_gate(source, commercial=False)


def test_profile_uses_the_module_and_the_source() -> None:
    catalog = Catalog.load(REPO_ROOT / "config")
    profile = catalog.jurisdictions.get("INT-OECD")
    assert profile.parent == "INT" and profile.citation.module == "oecd" and profile.publication.official_sources == [SOURCE_ID]
    assert profile.verification.status == "draft_unverified" and profile.coverage.corpus_status == "pilot"
    assert profile.official_languages == ["en", "fr"] and profile.memberships == []
    for example in profile.citation.examples:
        assert example.startswith("OECD/LEGAL/")
    for doc_key in FIXTURES:
        assert _doc(doc_key).instrument_type in profile.instrument_types


# --------------------------------------------------------------------------- database


class _Fixtures:
    """The fixture bundles as the connector would yield them."""

    def __init__(self, *keys: str):
        self.keys = keys
        self.fetch_errors: list[str] = []

    def fetch(self) -> Iterator[RawDocument]:
        for key in self.keys:
            bundle = read_bundle(_bytes(key))
            yield RawDocument(
                f"{bundle['key']}/{bundle['language']}",
                bundle["url"],
                "application/json",
                _bytes(key),
                datetime.fromisoformat("2026-10-15T09:00:00+00:00"),
                {"fixture": key},
            )


@pytest.fixture(scope="module")
def oecd_ingested(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, ingested: IngestionReport
) -> IngestionReport:
    with session_factory() as session:
        report = Ingestor(session, catalog, store).ingest_source(SOURCE_ID, actor="pytest", connector=_Fixtures(*FIXTURES))
        session.commit()
    return report


def _search(session: Session, catalog: Catalog, **kw: Any) -> dict[str, Any]:
    return search(session, catalog, SearchRequest(jurisdiction="INT-OECD", **kw), today=TODAY)


@pytest.mark.db
def test_ingest_report(oecd_ingested: IngestionReport) -> None:
    assert oecd_ingested.errors == [] and oecd_ingested.conflicts == [] and oecd_ingested.skipped == []
    assert oecd_ingested.documents_new == len(FIXTURES)
    assert oecd_ingested.instruments_created == 5  # 0162 (two languages), 0293, 0383, 0307, 0449, 0413 -> six works, one shared
    assert oecd_ingested.provision_versions_created == sum(len(_doc(k).provisions) for k in FIXTURES)


@pytest.mark.db
def test_search_finds_exact_text_with_the_oecd_citation(
    session: Session, catalog: Catalog, oecd_ingested: IngestionReport
) -> None:
    res = _search(session, catalog, q="Investigation and prosecution of the bribery of a foreign public official")
    hit = res["results"][0]
    assert hit["citation"] == "OECD/LEGAL/0293 art. 5"
    assert hit["text"].startswith("Enforcement\nInvestigation and prosecution of the bribery of a foreign public official")
    assert hit["jurisdiction"]["id"] == "INT-OECD"
    codes = {lim["code"] for lim in hit["limitations"]}
    assert "PROFILE_UNVERIFIED" in codes and "SOURCE_NOT_AUTHENTIC" not in codes
    assert "OECD" in hit["source"]["attribution"] and "{retrieved_date}" not in hit["source"]["attribution"]
    ai = _search(session, catalog, q="Respect for the rule of law, human rights and democratic values, including fairness and privacy")
    assert ai["results"][0]["citation"] == "OECD/LEGAL/0449 para. 1.2"
    fr = _search(session, catalog, q="La présente Décision sera réexaminée périodiquement")
    assert fr["results"][0]["citation"] == "OECD/LEGAL/0307 para. III.2"
    appendix = _search(session, catalog, q="The corporate governance framework should be developed with a view to its impact on corporate access to finance")
    assert appendix["results"][0]["citation"] == "OECD/LEGAL/0413 appendix para. I.A"


@pytest.mark.db
def test_abrogated_instrument_is_in_force_until_its_abrogation_date(
    session: Session, catalog: Catalog, oecd_ingested: IngestionReport
) -> None:
    during = provision_at(session, catalog, "/int/oecd/0162/~para-ii", date(2010, 1, 1))
    assert during["status_on_date"] == "in_force" and during["provision"]["citation"] == "OECD/LEGAL/0162 para. II"
    after = provision_at(session, catalog, "/int/oecd/0162/~para-ii", date(2024, 1, 1))
    assert after["status_on_date"] != "in_force"
    amended = provision_at(session, catalog, "/int/oecd/0449/~para-ii", date(2020, 1, 1))
    assert amended["status_on_date"] in ("history_not_indexed", "not_yet_in_force")  # only the 2024 wording is published
    current = provision_at(session, catalog, "/int/oecd/0449/~para-ii", TODAY)
    assert current["status_on_date"] == "in_force" and current["version"]["currency_date"] == "2026-10-15"


@pytest.mark.db
def test_raw_documents_verify_against_the_stored_text(
    session: Session, catalog: Catalog, store: LocalObjectStore, oecd_ingested: IngestionReport
) -> None:
    from sqlalchemy import select

    from ulis.db import models as m

    ids = session.scalars(select(m.SourceDocument.id).where(m.SourceDocument.source_id == SOURCE_ID)).all()
    assert len(ids) == len(FIXTURES)
    for document_id in ids:
        result = verify_source_document(session, catalog, store, document_id, checker="pytest")
        assert result["result"] == "pass", result

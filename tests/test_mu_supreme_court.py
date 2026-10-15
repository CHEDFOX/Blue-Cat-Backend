"""Mauritius: Judgment Search of the Supreme Court website (planned: robots.txt disallows all robots).

The connector and parser are tested on real fixtures; the source stays ``planned``, the licence gate refuses it and
the connector refuses the live site's robots.txt. Poppler's ``pdftotext`` is needed for the tests that read a PDF.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import yaml

from ulis.catalog import Catalog
from ulis.ingestion.pipeline import CONNECTORS, PARSER_VERSIONS, PARSERS
from ulis.jurisdictions.modules import JudgmentCitationInput, get_module
from ulis.settings import REPO_ROOT
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.mu_supreme_court import (
    BASE_URL,
    HOST,
    ROBOTS_URL,
    MuSupremeCourtConnector,
    listing_url,
    parse_listing,
)
from ulis.sources.connectors.polite_http import FetchError, PoliteHttp
from ulis.sources.normalized import ParsedDocument, ParseSkipped
from ulis.sources.parsers.mu_judgments import (
    BUNDLE_SCHEMA,
    MuParseError,
    classify_court,
    detect_language,
    parse_mu_judgment,
    split_paragraphs,
    work_uri_for,
)
from ulis.sources.registry import LicenceNotApproved, licence_gate

FIX = REPO_ROOT / "tests" / "fixtures" / "mu_supreme_court"
SOURCE = "mu-supreme-court-judgments"
UA = "ULIS-research-bot/0.1 (+https://github.com/CHEDFOX/Blue-Cat-Backend)"
NOORBUX, TURQUOISE, SPICE, GOVIND = "19490869", "19490555", "19493399", "19480656"
needs_pdftotext = pytest.mark.skipif(shutil.which("pdftotext") is None, reason="poppler's pdftotext is not installed")


def _bundle(nid: str) -> dict[str, Any]:
    return json.loads((FIX / f"bundle-{nid}.json").read_bytes())


def _doc(nid: str) -> ParsedDocument:
    return parse_mu_judgment((FIX / f"bundle-{nid}.json").read_bytes())[0]


def _para(doc: ParsedDocument, eid: str) -> Any:
    return next(p for p in doc.paragraphs if p.eid == eid)


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text)


# ----------------------------------------------------------------------------------------------- parser


def test_numbered_ruling_with_a_quotation_and_the_courts_own_numbering_glitch() -> None:
    doc = _doc(TURQUOISE)
    assert doc.doc_kind == "judgment" and doc.jurisdiction_id == "MU" and doc.ext_module == "mu"
    assert doc.work_uri == "/mu/judgment/2026-int-287"
    assert doc.expression_uri == "/mu/judgment/2026-int-287/eng@2026-10-14"
    assert doc.title == "Turquoise Properties Ltd v Zoobair and Osman Properties Ltd"
    assert (doc.language, doc.authoritative, doc.text_origin) == ("en", False, "official_conversion")
    meta = doc.judgment
    assert meta is not None
    assert (meta.court_id, meta.neutral_citation, meta.decision_date) == ("MU-IC", "2026 INT 287", date(2026, 10, 14))
    assert doc.version_date == date(2026, 10, 14) and doc.number == "2026 INT 287"
    header = _para(doc, "hdr")
    assert header.part == "header" and header.text.splitlines()[:2] == [doc.title, "2026 INT 287"]
    assert "THE INTERMEDIATE COURT OF MAURITIUS" in header.text and header.text.endswith("RULING")
    assert _para(doc, "para-1").text.startswith("The Plaintiff is suing the Defendant for having fraudulently")
    # the quoted numbered pleas "1. ... 2. ..." stay inside paragraph 2
    assert "2. In the alternative the plaint is time barred" in _para(doc, "para-2").text
    # the court printed 3, 4, 3, 5: kept as printed, with distinct eids
    nums = [p.num for p in doc.paragraphs if p.part == "text"]
    assert nums[:6] == ["1", "2", "3", "4", "3", "5"]
    assert _para(doc, "para-3-2").text.startswith("It is trite law that only interested parties")
    assert doc.ext["document_number"] == "2026 INT 287" and doc.ext["court_basis"] == "listing_delivered_by"
    assert doc.ext["delivered_by"] == "Mrs Y Nathire Beebeejaun, Magistrate Intermediate Court"


def test_number_alone_on_its_line_and_a_sublist() -> None:
    doc = _doc(NOORBUX)
    assert doc.judgment is not None and doc.judgment.court_id == "MU-DC"
    assert [p.num for p in doc.paragraphs if p.part == "text"][:4] == ["1", "2", "3", "4"]
    assert _para(doc, "para-1").text.startswith(
        "The Applicant stands provisionally charged with one Count of drug dealing"
    )
    two = _para(doc, "para-2").text
    assert "(a)\nrisk of absconding; and\n(b)\nrisk of re-offending." in two
    assert not any(re.search(r"Page \d+ of \d+", p.text) for p in doc.paragraphs)  # printed page numbers are dropped


def test_square_bracket_markers() -> None:
    doc = _doc(GOVIND)
    assert [p.num for p in doc.paragraphs if p.part == "text"][:3] == ["1", "2", "3"]
    assert _para(doc, "para-1").text.startswith("The Applicant stands provisionally charged before the District Court")
    assert doc.work_uri == "/mu/judgment/2026-bmb-59" and doc.title == "Yogajee Govind v. Police (1)"


def test_unnumbered_judgment_is_split_at_blank_lines_and_page_footers_are_dropped() -> None:
    doc = _doc(SPICE)
    assert doc.judgment is not None and doc.judgment.court_id == "MU-DC"
    assert "IN THE DISTRICT COURT OF PORT LOUIS (DIVISION III)" in _para(doc, "hdr").text
    body = [p for p in doc.paragraphs if p.part == "text"]
    assert all(p.num is None for p in body) and [p.eid for p in body][:3] == ["u-1", "u-2", "u-3"]
    assert body[0].text.startswith("The accused company stands charged with the offence of « Failing to comply")
    # "a Notice to Owner was served ..." continues the sentence after a page break: one paragraph
    assert "on whom a Notice to Owner was served on the 29th May 2019" in body[0].text
    assert not any(re.search(r"(^|\s)Page \d+(\s|$)", p.text) for p in doc.paragraphs)


def _without_footers(text: str) -> str:
    pages = []
    for page in text.split("\f"):
        lines = page.rstrip().split("\n")
        if lines and re.fullmatch(r"(Page )?\d{1,3}( of \d{1,3})?", lines[-1].strip()):
            lines.pop()
        pages.append("\n".join(lines))
    return "\n".join(pages)


@pytest.mark.parametrize("nid", [NOORBUX, TURQUOISE, SPICE, GOVIND])
def test_paragraph_text_is_exactly_the_extracted_text(nid: str) -> None:
    """Every paragraph is a stretch of the source text (white space aside); only page numbers and the dots of
    the numbered markers are not in the paragraphs."""
    source = _squash(_without_footers(_bundle(nid)["text"]))
    doc = _doc(nid)
    kept = ""
    position = 0
    for p in doc.paragraphs:
        flat = _squash(p.text)
        found = source.find(flat, position)
        assert found >= 0, p.eid  # in order, without gaps beyond the markers
        assert found - position <= 6, (p.eid, source[position:found])
        position = found + len(flat)
        kept += flat
    assert len(source) - len(kept) <= 6 * len(doc.paragraphs)


def test_marker_rules_quote_repeat_and_sequence() -> None:
    text = (
        "Title\n2026 INT 1\nJUDGMENT\n"
        "1. First paragraph\ncontinues here.\n"
        "2. The rule reads:- “1. A quoted item\n2. Another quoted item that is long\nend of quote.”\n"
        "3. Third, after the quotation.\n"
        "19. A stray large number does not start a paragraph.\n"
        "4. Fourth.\n"
    )
    header, units = split_paragraphs(text)
    assert header == ["Title", "2026 INT 1", "JUDGMENT"]
    assert [n for n, _ in units] == ["1", "2", "3", "4"]
    assert units[0][1] == "First paragraph continues here."
    assert "2. Another quoted item that is long end of quote.”" in units[1][1]
    assert "19. A stray large number" in units[2][1]


def test_wrapped_lines_hyphen_join_and_long_paragraphs_split_at_sentence_ends() -> None:
    _, units = split_paragraphs("RULING\n1. The non-\njoinder of parties is not fatal.\n")
    assert units == [("1", "The non-joinder of parties is not fatal.")]
    sentence = "This is one more sentence of the reasoning. "
    _, long_units = split_paragraphs("RULING\n1. " + sentence * 300 + "\n")
    assert len(long_units) > 2 and long_units[0][0] == "1" and all(n is None for n, _ in long_units[1:])
    assert all(len(t) <= 4000 for _, t in long_units)
    assert " ".join(t for _, t in long_units) == (sentence * 300).strip()


def test_page_number_footers_are_only_taken_from_the_end_of_a_page() -> None:
    _, units = split_paragraphs("RULING\n1. First line of text\n3\n\f2. Second paragraph\nPage 2 of 2\n")
    assert units == [("1", "First line of text"), ("2", "Second paragraph")]


@pytest.mark.parametrize(
    ("role", "court"),
    [
        ("Mrs S D Bonomally, Acting President Industrial Court", "MU-IND"),
        ("Mr M Armoogum, Magistrate Industrial Court", "MU-IND"),
        ("Ms N Seebaluck , Magistrate Intermediate Court", "MU-IC"),
        ("Mrs R K  Gowry-Bhurrut , President Children's Court", "MU-CC"),
        ("Mr Arun Rohamally, Senior District Magistrate", "MU-DC"),
        ("Ms A Dhunnoo, District Magistrate", "MU-DC"),
        ("The Hon. Chief Justice", "MU-SC"),
    ],
)
def test_court_from_the_officers_title(role: str, court: str) -> None:
    assert classify_court(role, []) == (court, "listing_delivered_by")


def test_court_from_the_header_and_never_a_guess() -> None:
    assert classify_court(None, ["x", "THE INTERMEDIATE COURT OF MAURITIUS"]) == ("MU-IC", "header_text")
    assert classify_court("Mr X", ["BEFORE THE BAIL AND REMAND COURT"]) == ("MU-DC", "header_text")
    with pytest.raises(MuParseError, match="cannot be told"):
        classify_court("Mr X, Registrar", ["Something else"])


def test_work_uri_and_language() -> None:
    assert work_uri_for("2021 SCJ 394", "7") == "/mu/judgment/2021-scj-394"
    assert work_uri_for(None, "7") == "/mu/judgment/node-7"
    assert work_uri_for("no. 5", "7") == "/mu/judgment/node-7"
    assert (
        detect_language("the court held that the accused was not in the vehicle and that it is for the State to prove")
        == "en"
    )
    fr = (
        "Le tribunal a jugé que la requête de la défenderesse est fondée et que les frais sont "
        "à la charge de la partie qui " * 4
    )
    assert detect_language(fr) == "fr"


def _tweak(nid: str, **changes: Any) -> bytes:
    bundle = _bundle(nid)
    for key, value in changes.items():
        target = bundle["listing"] if key in bundle["listing"] else bundle
        target[key] = value
    return json.dumps(bundle).encode()


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"not json", "not a JSON bundle"),
        (json.dumps({"schema": "other/1"}).encode(), "unexpected bundle schema"),
        (_tweak(NOORBUX, delivered_on="31-12-2026"), "unreadable date"),
        (_tweak(NOORBUX, title="  "), "no title"),
        (
            _tweak(NOORBUX, delivered_by="Mr X, Registrar", text="Plain text only, nothing about a court.\n"),
            "cannot be told",
        ),
    ],
)
def test_parser_refuses_what_it_cannot_read_safely(content: bytes, message: str) -> None:
    with pytest.raises(MuParseError, match=message):
        parse_mu_judgment(content)


def test_a_pdf_without_a_text_layer_is_skipped_not_an_error() -> None:
    with pytest.raises(ParseSkipped, match="no text layer"):
        parse_mu_judgment(_tweak(NOORBUX, text="\f\n\f"))
    assert _bundle(NOORBUX)["schema"] == BUNDLE_SCHEMA


# ----------------------------------------------------------------------------------------- citations, profile


def test_judgment_citations(catalog: Catalog) -> None:
    module = get_module("mu")
    base = JudgmentCitationInput(
        case_name="Turquoise Properties Ltd v Zoobair and Osman Properties Ltd",
        neutral_citation="2026 INT 287",
        decision_date=date(2026, 10, 14),
        docket=None,
        court_id="MU-IC",
        court_name="Intermediate Court",
        paragraph=None,
        language="en",
        jurisdiction_id="MU",
    )
    assert (
        module.judgment_citation(base) == "Turquoise Properties Ltd v Zoobair and Osman Properties Ltd [2026 INT 287]"
    )
    cited = module.judgment_citation(JudgmentCitationInput(**{**base.__dict__, "paragraph": "5"}))
    assert cited == "Turquoise Properties Ltd v Zoobair and Osman Properties Ltd [2026 INT 287], para 5"
    bare = module.judgment_citation(JudgmentCitationInput(**{**base.__dict__, "neutral_citation": None}))
    assert bare.endswith("(Intermediate Court, 14 October 2026)")
    # ext is validated strictly
    with pytest.raises(ValueError, match="extra"):
        module.validate_ext({**_doc(TURQUOISE).ext, "invented": 1})
    assert module.validate_ext(_doc(TURQUOISE).ext)["node_id"] == TURQUOISE


def test_profile_has_the_courts_and_is_a_documented_stub(catalog: Catalog) -> None:
    profile = catalog.jurisdictions.get("MU")
    assert profile.verification.status == "draft_unverified" and profile.coverage.corpus_status == "none"
    assert [c.id for c in profile.courts] == ["MU-SC", "MU-IC", "MU-IND", "MU-DC", "MU-CC"]
    assert profile.court("MU-DC") is not None and profile.court("MU-DC").appeals_to == ["MU-SC"]  # type: ignore[union-attr]
    assert profile.citation.module == "mu" and profile.publication.official_sources == [SOURCE]
    assert not profile.memberships
    gaps = " ".join(profile.coverage.known_gaps)
    assert "robots.txt disallows all robots" in gaps and "No Mauritian legislation" in gaps


# ------------------------------------------------------------------------------ source, gate and connector


def test_source_is_planned_because_robots_txt_disallows_all_robots(catalog: Catalog) -> None:
    source = catalog.sources.get(SOURCE)
    assert source.status == "planned" and source.licence.review_status == "pending_review"
    assert source.licence.automated_processing == "not_allowed" and source.licence.bulk_download == "not_allowed"
    assert (
        "Disallow: /" in (source.licence.reviewed_by or "") or "robots.txt disallows all robots" in source.licence.name
    )
    with pytest.raises(LicenceNotApproved, match="status is 'planned'"):
        licence_gate(source)
    # the code is ready and registered, so that flipping the status after written permission is all that is left
    assert source.connector == "mu_supreme_court" and CONNECTORS["mu_supreme_court"] is MuSupremeCourtConnector
    assert source.parser == "mu_supreme_court_bundle" and PARSERS["mu_supreme_court_bundle"] is parse_mu_judgment
    assert PARSER_VERSIONS["mu_supreme_court_bundle"] == "1.0.0"


def test_listing_rows_are_read_exactly_as_printed() -> None:
    rows, last = parse_listing((FIX / "listing-2026-0000.html").read_text(encoding="utf-8"))
    assert last == 0 and [r["node_id"] for r in rows] == [NOORBUX, TURQUOISE, SPICE, GOVIND]
    first = rows[0]
    assert first == {
        "node_id": NOORBUX,
        "title": "Mohammad Nowsad Noorbux v Police",
        "delivered_by": "Mr Arun Rohamally, Senior District Magistrate",
        "document_number": "2026 LPW 125",
        "delivered_on": "14/10/2026",
        "summary": "",
        "pdf_url": f"{BASE_URL}/system/files/judgment/{NOORBUX}/mohammad-nowsad-noorbux.pdf",
    }
    assert rows[2]["title"] == "POLICE v SPICE FINANCE LTD" and rows[3]["delivered_on"] == "11/10/2026"
    broken = "<table><tbody><tr><td>no link here</td></tr></tbody></table>"
    with pytest.raises(FetchError, match="markup changed"):
        parse_listing(broken)
    foreign = (
        (FIX / "listing-2026-0000.html")
        .read_text(encoding="utf-8")
        .replace("https%3A//supremecourt.govmu.org/system", "https%3A//example.org/system")
    )
    with pytest.raises(FetchError, match="unexpected PDF address"):
        parse_listing(foreign)


def _write(http: PoliteHttp, url: str, key: str, data: bytes, content_type: str, status: int = 200) -> None:
    path = http.cache_path(url, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    meta = {"url": url, "final_url": url, "status": status, "content_type": content_type}
    path.with_name(path.name + ".meta.json").write_text(
        json.dumps({**meta, "retrieved_at": "2026-10-14T21:30:00+00:00"})
    )


def _cache(tmp_path: Path, robots: bytes | None, pdfs: dict[str, bytes | None]) -> Path:
    cache = tmp_path / "cache"
    http = PoliteHttp(cache, UA, allowed_hosts={HOST})
    if robots is not None:
        _write(http, ROBOTS_URL, "robots.txt", robots, "text/plain")
    listing = (FIX / "listing-2026-0000.html").read_bytes()
    _write(http, listing_url(2026, 0), "listing/2026-0000.html", listing, "text/html; charset=UTF-8")
    rows, _ = parse_listing(listing.decode())
    for row in rows:
        data = pdfs.get(row["node_id"])
        if row["node_id"] in pdfs:
            if data is None:
                _write(http, row["pdf_url"], f"pdf/{row['node_id']}.pdf", b"", "text/html", status=404)
            else:
                _write(http, row["pdf_url"], f"pdf/{row['node_id']}.pdf", data, "application/pdf")
    return cache


def _manifest(tmp_path: Path, **extra: Any) -> Path:
    body = {
        "schema_version": 1,
        "source_id": SOURCE,
        "base_url": BASE_URL,
        "user_agent": UA,
        "crawl_delay_seconds": 5,
        "years": [2026],
        "max_documents": None,
        **extra,
    }
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(body))
    return path


@needs_pdftotext
def test_connector_offline_from_cache_builds_the_bundle(tmp_path: Path) -> None:
    pdf = (FIX / "noorbux.pdf").read_bytes()
    cache = _cache(tmp_path, b"", {NOORBUX: pdf})
    docs = list(MuSupremeCourtConnector(_manifest(tmp_path, max_documents=1), SOURCE, cache, offline=True).fetch())
    assert len(docs) == 1
    doc = docs[0]
    assert doc.external_id == f"mu-supreme-court:{NOORBUX}" and doc.media_type == "application/json"
    assert doc.uri == f"{BASE_URL}/system/files/judgment/{NOORBUX}/mohammad-nowsad-noorbux.pdf"
    assert doc.retrieved_at.year == 2026 and doc.metadata["node_id"] == NOORBUX
    bundle = json.loads(doc.content)
    assert bundle["schema"] == BUNDLE_SCHEMA
    assert bundle["pdf"] == {"url": doc.uri, "sha256": hashlib.sha256(pdf).hexdigest(), "bytes": len(pdf)}
    assert bundle["listing"]["document_number"] == "2026 LPW 125" and bundle["listing"]["delivered_on"] == "14/10/2026"
    assert "The Applicant stands provisionally charged with one Count of drug dealing" in bundle["text"]
    # the committed fixture bundle was made the same way: same listing, same PDF, same words
    stored = _bundle(NOORBUX)
    assert stored["listing"] == bundle["listing"] and stored["pdf"] == bundle["pdf"]
    assert _squash(stored["text"]) == _squash(bundle["text"])
    parsed = parse_mu_judgment(doc.content)[0]
    assert parsed.work_uri == "/mu/judgment/2026-lpw-125"


@needs_pdftotext
def test_connector_collects_problem_documents_and_goes_on(tmp_path: Path) -> None:
    pdf = (FIX / "noorbux.pdf").read_bytes()
    cache = _cache(tmp_path, b"", {NOORBUX: pdf, TURQUOISE: None, SPICE: b"<html>Not a PDF</html>", GOVIND: None})
    connector = MuSupremeCourtConnector(_manifest(tmp_path), SOURCE, cache, offline=True)
    docs = list(connector.fetch())
    assert [d.metadata["node_id"] for d in docs] == [NOORBUX]
    assert len(connector.fetch_errors) == 3
    assert "not found (404)" in connector.fetch_errors[0] and TURQUOISE in connector.fetch_errors[0]
    assert "not a PDF" in connector.fetch_errors[1] and SPICE in connector.fetch_errors[1]


def test_connector_refuses_the_live_sites_robots_txt(tmp_path: Path) -> None:
    robots = (FIX / "robots.txt").read_bytes()
    assert robots.decode().rstrip().splitlines()[-1] == "Disallow: /"
    cache = _cache(tmp_path, robots, {})
    with pytest.raises(FetchError, match="disallowed by"):
        list(MuSupremeCourtConnector(_manifest(tmp_path), SOURCE, cache, offline=True).fetch())


def test_a_refusal_stops_the_run_but_a_missing_pdf_does_not(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = _cache(tmp_path, b"", {})
    real_get = PoliteHttp.get

    def get(self: PoliteHttp, url: str, *args: Any, **kwargs: Any) -> Any:
        if "/system/files/" in url:
            raise FetchError(f"{url}: HTTP 403 (not retried: a refusal is respected)")
        return real_get(self, url, *args, **kwargs)

    monkeypatch.setattr(PoliteHttp, "get", get)
    with pytest.raises(FetchError, match="HTTP 403"):
        list(MuSupremeCourtConnector(_manifest(tmp_path), SOURCE, cache, offline=True).fetch())


def test_connector_refuses_bad_manifests(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    for extra, message in (
        ({"source_id": "other"}, "schema or source_id mismatch"),
        ({"base_url": "https://example.org"}, "base_url must be"),
        ({"user_agent": "bot"}, "identifying user_agent"),
        ({"crawl_delay_seconds": 1}, "at least 5"),
        ({"years": []}, "years must be"),
        ({"years": [2026, 2026]}, "years must be"),
        ({"years": ["2026"]}, "years must be"),
        ({"max_documents": 0}, "max_documents"),
    ):
        with pytest.raises(ManifestError, match=message):
            list(MuSupremeCourtConnector(_manifest(tmp_path, **extra), SOURCE, cache, offline=True).fetch())


def test_committed_manifest_loads_and_covers_the_whole_database(tmp_path: Path) -> None:
    connector = MuSupremeCourtConnector(
        REPO_ROOT / "config" / "manifests" / f"{SOURCE}.yaml", SOURCE, tmp_path / "cache", offline=True
    )
    manifest = connector._manifest()
    assert manifest["years"] == [2020, 2021, 2022, 2023, 2024, 2025, 2026] and manifest["max_documents"] is None
    assert manifest["crawl_delay_seconds"] >= 5 and "ULIS-research-bot" in manifest["user_agent"]

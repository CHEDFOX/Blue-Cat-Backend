"""Supreme Court of India judgments (JUDIS PDFs on api.sci.gov.in): parser, citations, connector, search.

The fixtures in ``tests/fixtures/sci_judis`` are real PDFs exactly as received from
``https://api.sci.gov.in/jonew/judis/<n>.pdf`` (gzip-compressed, retrieved 2026-10-16).
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
from ulis.ingestion.pipeline import PARSER_VERSIONS, PARSERS, IngestionReport, Ingestor, build_connector
from ulis.jurisdictions.modules import JudgmentCitationInput, ProvisionCitationInput, get_module
from ulis.retrieval.search import SearchRequest, search
from ulis.settings import REPO_ROOT
from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.polite_http import FetchError, PoliteHttp
from ulis.sources.connectors.sci_judis import ROBOTS_URL, SciJudisConnector, judgment_url
from ulis.sources.normalized import ParsedDocument, ParseSkipped
from ulis.sources.parsers import sci_judis
from ulis.sources.parsers.sci_judis import SciJudisParseError, parse_sci_judis
from ulis.sources.registry import LicenceNotApproved, licence_gate
from ulis.storage.object_store import LocalObjectStore
from ulis.verification.integrity import verify_source_document

SOURCE = "in-sci-judis"
HOST = "api.sci.gov.in"
UA = "ULIS-research-bot/0.1 (+https://github.com/CHEDFOX/Blue-Cat-Backend)"
FIX = REPO_ROOT / "tests" / "fixtures" / "sci_judis"
NATHMAL, MORCHA, GANGADEVI, DEEPA, VIMALESHWAR, PREETAM = 100, 5000, 15000, 30000, 38000, 42000
TRANSFER, RECORD = 33827, 33824
ALL = [NATHMAL, MORCHA, GANGADEVI, DEEPA, VIMALESHWAR, PREETAM, TRANSFER]
BLANK_PDF = (
    b"\x00\x00\x00\x00%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\n"
    b"trailer<</Root 1 0 R/Size 4>>\n%%EOF\n"
)


def _raw(n: int) -> bytes:
    return gzip.decompress((FIX / f"judis_{n}.pdf.gz").read_bytes())


def _doc(n: int) -> ParsedDocument:
    docs = parse_sci_judis(_raw(n))
    assert len(docs) == 1
    return docs[0]


def _para(doc: ParsedDocument, eid: str) -> Any:
    return {p.eid: p for p in doc.paragraphs}[eid]


def _parts(doc: ParsedDocument) -> dict[str, int]:
    out: dict[str, int] = {}
    for p in doc.paragraphs:
        out[p.part] = out.get(p.part, 0) + 1
    return out


# --------------------------------------------------------------------------- parser: the JUDIS header layout


def test_judis_header_judgment_of_1960() -> None:
    doc = _doc(NATHMAL)
    meta = doc.judgment
    assert meta is not None
    assert meta.court_id == "IN-SC" and meta.decision_date == date(1960, 10, 18) and meta.neutral_citation is None
    assert meta.case_name == "NATHMAL TOLARAM v. SUPERINTENDENT OF TAXES, DHUBRI AND ANOTHER."
    assert meta.docket == "Civil Appeal No. 196 of 1958" and doc.number == meta.docket
    assert doc.work_uri.startswith("/in/sc/1960-10-18/nathmal-tolaram-v-superintendent-of-taxes-dhubri-and-another-")
    assert doc.expression_uri == f"{doc.work_uri}/eng@1960-10-18" and doc.version_date == date(1960, 10, 18)
    assert (doc.language, doc.authoritative, doc.jurisdiction_id, doc.text_origin) == (
        "en",
        False,
        "IN",
        "official_conversion",
    )
    assert doc.ext["layout"] == "judis_header"
    assert doc.ext["reporter_citations"] == ["1961 AIR 331", "1961 SCR (2) 40"]
    assert doc.ext["bench"] == [
        "SHAH, J.C.",
        "DAS, S.K.",
        "HIDAYATULLAH, M.",
        "GUPTA, K.C. DAS",
        "AYYANGAR, N. RAJAGOPALA",
    ]
    assert doc.ext["petitioner"] == "NATHMAL TOLARAM"
    assert doc.ext["respondent"] == "SUPERINTENDENT OF TAXES, DHUBRI AND ANOTHER."
    assert _parts(doc) == {"catchwords": 1, "headnote": 2, "text": 6}  # the Court prints no paragraph numbers


def test_page_furniture_is_left_out_and_wrapped_lines_are_joined() -> None:
    doc = _doc(NATHMAL)
    text = " ".join(p.text for p in doc.paragraphs)
    assert "JUDIS.NIC.IN" not in text and "Page 2 of 5" not in text
    # "maunds of 6" is followed on the next printed line by the reporter's page number 42 and then "jute mentioned".
    assert "20,515 maunds of 6 jute mentioned under item (i) is taxable under the Act ?" in text
    assert "\n" not in text and "  " not in text
    assert _para(doc, "cw-1").text.startswith("Sales Tax--Reassessment--jurisdiction of the Sales Tax Officer")
    assert _para(doc, "hn-1").text.startswith(
        "The appellants who were dealers registered under the Assam Sales Tax Act, 1947"
    )
    first = _para(doc, "t-1").text
    assert first.startswith("CIVIL APPELLATE, JURISDICTION: Civil Appeal No. 196 of 1958. Appeal by special leave")
    assert [p.sort_order for p in doc.paragraphs] == list(range(len(doc.paragraphs)))
    assert len({p.eid for p in doc.paragraphs}) == len(doc.paragraphs)
    assert all(
        len(p.text) < 4300 for p in doc.paragraphs if p.part == "text"
    )  # cut near 1,500 characters, at 4,000 at the latest


def test_criminal_appeal_of_1978_with_three_reporter_citations() -> None:
    doc = _doc(MORCHA)
    assert doc.judgment is not None
    assert doc.judgment.case_name == "MORCHA v. THE STATE OF RAJASTHAN"
    assert doc.judgment.decision_date == date(1978, 9, 13)  # "DATE OF JUDGMENT13/09/1978": no colon in this PDF
    assert doc.judgment.docket == "Criminal Appeal No. 43 of 1972"
    assert doc.ext["reporter_citations"] == ["1979 AIR 80", "1979 SCR (1) 744", "1979 SCC (1) 161"]
    assert doc.ext["bench"] == [
        "SINGH, JASWANT",
        "KAILASAM, P.S.",
        "KOSHAL, A.D.",
    ]  # the repeated BENCH: label counts once


def test_short_order_of_1996_has_chunks_only_and_a_bench_on_one_line() -> None:
    doc = _doc(GANGADEVI)
    assert doc.judgment is not None
    assert doc.judgment.case_name == "SMT. GANGADEVI v. UNION OF INDIA & ANR."
    assert doc.judgment.decision_date == date(1996, 10, 1) and doc.judgment.docket is None
    assert doc.ext["bench"] == ["B.P. JEEVAN REDDY", "K.S. PARIPOORNAN"]
    assert set(_parts(doc)) == {"text"} and "reporter_citations" not in doc.ext
    assert _para(doc, "t-1").text.startswith("O R D E R Petitioner is the widow of one Shrikrishna Gopilal Solanki")


def test_judis_header_with_numbered_paragraphs_of_2007() -> None:
    doc = _doc(DEEPA)
    assert doc.judgment is not None
    assert doc.judgment.case_name == "National Insurance Co. Ltd. v. Deepa Devi & Ors"
    assert doc.judgment.decision_date == date(2007, 12, 11) and doc.judgment.docket == "CIVIL APPEAL NO. 5796 OF 2007"
    assert doc.ext["bench"] == ["S.B. Sinha", "Lokeshwar Singh Panta"]
    numbered = [p for p in doc.paragraphs if p.num]
    assert [p.num for p in numbered] == [str(i) for i in range(1, 19)] and numbered[0].eid == "para-1"
    assert numbered[0].part == "considerations" and numbered[0].text == "Leave granted."
    assert _para(doc, "para-2").text.startswith("The short question involved in this appeal arising out of a judgment")
    assert doc.paragraphs[0].part == "text" and doc.paragraphs[0].text.startswith(
        "J U D G M E N T CIVIL APPEAL NO. 5796 OF 2007"
    )


# --------------------------------------------------------------------------- parser: the signed judgment layout


def test_signed_judgment_of_2011() -> None:
    doc = _doc(VIMALESHWAR)
    assert doc.judgment is not None
    assert doc.judgment.case_name == "Vimaleshwar Nagappa Shet v. Noor Ahmed Sheriff & Ors."
    assert doc.judgment.docket == "CIVIL APPEAL NOs. 4279-4280 OF 2011"
    assert doc.judgment.decision_date == date(2011, 5, 11)  # "NEW DELHI; MAY 11, 2011." closes the judgment
    assert doc.ext["layout"] == "signed_judgment" and doc.ext["reportable"] is True
    assert doc.ext["author"] == "P. Sathasivam" and doc.ext["bench"] == ["P. SATHASIVAM", "H.L. GOKHALE"]
    numbered = [p for p in doc.paragraphs if p.num]
    assert [p.num for p in numbered] == [str(i) for i in range(1, 17)]  # "1)", "2)" ... in the PDF
    assert _para(doc, "para-1").text == "Leave granted."
    assert _para(doc, "para-3").text.startswith("Brief facts: (a) The property in question originally belonged to")
    assert _para(doc, "para-16").text.startswith("Accordingly, the appeals fail and the same are dismissed")
    assert doc.paragraphs[0].part == "text" and doc.paragraphs[0].text == "P. Sathasivam, J."
    assert "reporter_citations" not in doc.ext


def test_signed_judgment_followed_by_a_record_of_proceedings() -> None:
    doc = _doc(PREETAM)
    assert doc.judgment is not None
    assert doc.judgment.case_name == "State of Uttar Pradesh v. Preetam Singh and others"
    assert doc.judgment.docket == "CIVIL APPEAL NO. 6307 OF 2010" and doc.judgment.decision_date == date(2014, 9, 23)
    assert doc.ext["bench"] == ["JAGDISH SINGH KHEHAR", "ARUN MISHRA"] and doc.ext["author"] == "J.S.KHEHAR"
    record = [p for p in doc.paragraphs if p.part == "record"]
    assert len(record) == 1 and record[0].text.startswith("ITEM NO.1 COURT NO.7 SECTION XI")
    assert "Date : 23/09/2014" in record[0].text
    judgment = " ".join(p.text for p in doc.paragraphs if p.part != "record")
    assert "RECORD OF PROCEEDINGS" not in judgment and "Court Master" not in judgment
    assert doc.paragraphs[-1].part == "record"


def test_transfer_petition_order_of_2009_closed_by_a_date_with_an_ordinal() -> None:
    doc = _doc(TRANSFER)
    assert doc.judgment is not None
    assert doc.judgment.case_name == "STATE OF U.P. v. SHIV SINGH YADAV & ORS."
    assert doc.judgment.docket == "TRANSFER PETITION (C.) NOS.821-825 OF 2007"
    assert doc.judgment.decision_date == date(2009, 2, 16)  # "16TH FEBRUARY, 2009."
    assert doc.ext["bench"] == ["K.G. BALAKRISHNAN", "P. SATHASIVAM"]  # the Chief Justice's line ends in "CJI."
    assert doc.ext.get("reportable") is None and _parts(doc) == {"text": 1}


def test_a_record_of_proceedings_alone_is_not_a_judgment() -> None:
    with pytest.raises(ParseSkipped, match="Record of Proceedings"):
        parse_sci_judis(_raw(RECORD))


# --------------------------------------------------------------------------- parser: helpers and refusals


def test_page_numbers_are_dropped_but_a_lone_year_is_kept() -> None:
    pages = [
        "http://JUDIS.NIC.IN   SUPREME COURT OF INDIA   Page 1 of 5\nthe turnover of\n41\njute\n1996\nwas taxed\n",
        "Page 2\n1997\nx",
    ]
    assert sci_judis._clean_lines(pages) == ["the turnover of", "jute", "1996", "was taxed", "1997", "x"]
    assert sci_judis._clean_lines(["41\n42\ntext\n43"]) == ["text"]


def test_numbering_starts_at_one_but_a_lost_first_number_or_a_skipped_number_is_tolerated() -> None:
    body = [
        "Leave granted.",
        "2. First reason given.",
        "more of it",
        "3. Second reason.",
        "5. Fourth reason, number 4 lost.",
        "7. a",
        "9. Quoted.",
    ]
    out = sci_judis._body_paragraphs(body)
    assert [(p, n) for p, n, _ in out] == [
        ("text", None),
        ("considerations", "2"),
        ("considerations", "3"),
        ("considerations", "5"),
    ]
    assert out[1][2] == "First reason given. more of it"
    # "7. a" starts with a lower-case letter, and "9." is more than two ahead of 5: both stay inside paragraph 5.
    assert out[3][2] == "Fourth reason, number 4 lost. 7. a 9. Quoted."


def test_closing_date_is_read_only_from_a_place_and_date_line_near_the_end() -> None:
    assert sci_judis._closing_date(["...", "NEW DELHI;", "MAY 11, 2011."]) == date(2011, 5, 11)
    assert sci_judis._closing_date(["...", "New Delhi, 3rd March 2009"]) == date(2009, 3, 3)
    assert sci_judis._closing_date(["...", "Dated: March 3, 2009"]) == date(2009, 3, 3)
    quoted = ["The appeal is dismissed in view of the order dated March 3, 2009 passed by the High Court at Delhi."]
    assert sci_judis._closing_date(quoted) is None  # a date inside the reasons is not the date of the judgment
    assert sci_judis._closing_date(["...", "MAY 11, 2011."]) is None  # no place
    with pytest.raises(SciJudisParseError, match="impossible date"):
        sci_judis._closing_date(["NEW DELHI;", "FEBRUARY 30, 2011."])
    assert sci_judis._record_date(["Date : 23/09/2014 This appeal was called on"]) == date(2014, 9, 23)


def test_party_roles_and_dot_leaders_are_dropped_and_names_are_not_touched() -> None:
    assert sci_judis._party(["State through Intelligence Officer", "Narcotics Control Bureau ... Appellant"]) == (
        "State through Intelligence Officer Narcotics Control Bureau"
    )
    assert sci_judis._party(["Mushtaq Ahmad Etc.   ... Respondents"]) == "Mushtaq Ahmad Etc."
    assert sci_judis._party(["Union of India & Ors. ……. Respondent(s)"]) == "Union of India & Ors."
    assert sci_judis._party(["Versus"]) is None


def test_parser_refuses_what_it_cannot_read_safely() -> None:
    with pytest.raises(SciJudisParseError, match="not a PDF"):
        parse_sci_judis(b"<html>Access Denied</html>")
    with pytest.raises(ParseSkipped, match="no text layer"):
        parse_sci_judis(BLANK_PDF)
    good = _raw(NATHMAL)
    assert good[:4] != b"%PDF" and good[4:9] == b"%PDF-"  # the four-byte prefix is part of the stored bytes
    assert parse_sci_judis(good[4:])[0].work_uri == parse_sci_judis(good)[0].work_uri  # and is optional for the parser
    assert "sci_judis_pdf" in PARSERS and PARSER_VERSIONS["sci_judis_pdf"] == sci_judis.PARSER_VERSION


# --------------------------------------------------------------------------- citations, profile, source


def _citation(doc: ParsedDocument, paragraph: str | None, language: str = "en") -> str:
    assert doc.judgment is not None
    return get_module("in").judgment_citation(
        JudgmentCitationInput(
            doc.judgment.case_name,
            doc.judgment.neutral_citation,
            doc.judgment.decision_date,
            doc.judgment.docket,
            doc.judgment.court_id,
            "Supreme Court of India",
            paragraph,
            language,
            "IN",
            doc.ext,
        )
    )


def test_citations_use_the_reporter_citations_the_court_prints() -> None:
    assert _citation(_doc(NATHMAL), None) == (
        "NATHMAL TOLARAM v. SUPERINTENDENT OF TAXES, DHUBRI AND ANOTHER., 1961 AIR 331; 1961 SCR (2) 40 "
        "(Supreme Court of India, 18 October 1960)"
    )
    assert _citation(_doc(DEEPA), "2") == (
        "National Insurance Co. Ltd. v. Deepa Devi & Ors, CIVIL APPEAL NO. 5796 OF 2007 "
        "(Supreme Court of India, 11 December 2007), para 2"
    )
    assert _citation(_doc(NATHMAL), "7", "hi").endswith("(भारत का सर्वोच्च न्यायालय, 18 अक्टूबर 1960), पैरा 7")
    in_ = get_module("in")
    assert in_.judgment_unit_label("12", "considerations", None) == "para 12"
    assert in_.judgment_unit_label(None, "headnote", None) == "headnote"
    assert in_.judgment_unit_label(None, "record", None) == "record of proceedings"
    neutral = JudgmentCitationInput("A v. B", "2023 INSC 1043", date(2023, 11, 30), None, "IN-SC", "x", "5", "en", "IN")
    assert in_.judgment_citation(neutral) == "A v. B, 2023 INSC 1043 (Supreme Court of India, 30 November 2023), para 5"


def test_legislation_citations_in_english_and_hindi() -> None:
    in_ = get_module("in")

    def cite(title: str, kind: str, path: list[str], language: str = "en") -> str:
        return in_.provision_citation(ProvisionCitationInput(title, "act", "IN", kind, path, language))

    assert cite("The Indian Penal Code, 1860", "section", ["302"]) == "The Indian Penal Code, 1860, s. 302"
    assert cite("The Information Technology Act, 2000", "section", ["66A", "1", "a"]) == (
        "The Information Technology Act, 2000, s. 66A(1)(a)"
    )
    assert cite("The Constitution of India", "article", ["21"]) == "The Constitution of India, art. 21"
    assert cite("भारत का संविधान", "article", ["21"], "hi") == "भारत का संविधान, अनुच्छेद 21"
    assert cite("भारतीय दंड संहिता, 1860", "section", ["302"], "hi") == "भारतीय दंड संहिता, 1860, धारा 302"
    assert in_.validate_ext({"act_number": "45 of 1860", "year": 1860}) == {"act_number": "45 of 1860", "year": 1860}
    with pytest.raises(ValidationError):
        in_.validate_ext({"unknown": 1})
    with pytest.raises(ValidationError):
        in_.validate_ext({"layout": "other"})
    assert in_.validate_ext(_doc(NATHMAL).ext)["layout"] == "judis_header"


def test_profile_source_and_manifest(catalog: Catalog, tmp_path: Path) -> None:
    reg = catalog.jurisdictions
    profile = reg.get("IN")
    assert reg.court_jurisdiction("IN-SC") == "IN" and profile.profile_version == "0.2.0"
    assert profile.citation.module == "in" and profile.coverage.corpus_status == "pilot"
    assert "in-sci-judis" in profile.publication.official_sources
    assert any("eCourts" in gap or "CAPTCHA" in gap for gap in profile.coverage.known_gaps)
    assert any("No Indian legislation is ingested" in gap for gap in profile.coverage.known_gaps)
    source = catalog.sources.get(SOURCE)
    licence_gate(source, commercial=False)
    assert source.licence.review_status == "approved" and source.licence.commercial_use == "unknown"
    with pytest.raises(LicenceNotApproved, match="commercial deployment"):
        licence_gate(source, commercial=True)  # the Court's terms are unread: no commercial deployment on this source
    assert "{retrieved_date}" in (source.licence.attribution_text or "")
    assert isinstance(build_connector(source), SciJudisConnector)
    connector = SciJudisConnector(REPO_ROOT / source.access.endpoint, SOURCE, tmp_path)
    manifest = connector._manifest()
    assert manifest["id_ranges"] == [[1, 45100]] and sum(1 for _ in connector.numbers()) == 45100
    assert manifest["crawl_delay_seconds"] >= 5 and "ULIS-research-bot" in manifest["user_agent"]
    assert judgment_url(44900) == "https://api.sci.gov.in/jonew/judis/44900.pdf"


# --------------------------------------------------------------------------- connector


def _cache(cache: Path, number: int, content: bytes, content_type: str = "application/pdf", status: int = 200) -> None:
    http = PoliteHttp(cache, UA, allowed_hosts={HOST})
    url = judgment_url(number)
    path = http.cache_path(url, f"judis/{number}.pdf")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    meta = {"url": url, "final_url": url, "status": status, "content_type": content_type}
    path.with_name(path.name + ".meta.json").write_text(
        json.dumps({**meta, "retrieved_at": "2026-10-16T08:00:00+00:00"})
    )


def _cache_robots(cache: Path, robots: str | None) -> None:
    http = PoliteHttp(cache, UA, allowed_hosts={HOST})
    path = http.cache_path(ROBOTS_URL, "robots.txt")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((robots or "").encode())
    path.with_name(path.name + ".meta.json").write_text(
        json.dumps({"url": ROBOTS_URL, "status": 404 if robots is None else 200})
    )


def _manifest(tmp_path: Path, ranges: list[Any], **extra: Any) -> Path:
    body = {
        "schema_version": 1,
        "source_id": SOURCE,
        "base_url": f"https://{HOST}",
        "user_agent": UA,
        "crawl_delay_seconds": 5,
        "id_ranges": ranges,
        **extra,
    }
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(body))
    return path


def test_connector_offline_from_cache_yields_exact_pdfs_and_counts_empty_numbers(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    _cache_robots(cache, "")  # an empty robots.txt, as the host serves
    _cache(cache, 100, _raw(NATHMAL))
    _cache(
        cache, 101, b"", content_type="text/html; charset=UTF-8"
    )  # HTTP 200, empty body: no judgment under this number
    _cache(cache, 102, b"", status=404)
    _cache(cache, 103, _raw(MORCHA))
    connector = SciJudisConnector(_manifest(tmp_path, [[100, 103]]), SOURCE, cache, offline=True)
    docs = list(connector.fetch())
    assert [d.external_id for d in docs] == ["sci-judis:100", "sci-judis:103"]
    assert docs[0].content == _raw(NATHMAL)  # exact bytes as received, prefix included
    assert docs[0].uri == "https://api.sci.gov.in/jonew/judis/100.pdf" and docs[0].media_type == "application/pdf"
    assert docs[0].retrieved_at.year == 2026 and docs[0].metadata["judis_id"] == 100
    assert (connector.numbers_asked, connector.numbers_empty) == (4, 2) and connector.fetch_errors == []
    with pytest.raises(FetchError, match="offline and not cached"):
        list(SciJudisConnector(_manifest(tmp_path, [[104, 104]]), SOURCE, cache, offline=True).fetch())


def test_connector_reports_a_non_pdf_answer_and_goes_on(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    _cache_robots(cache, None)
    _cache(cache, 100, b"<html>Access Denied</html>", content_type="text/html")
    _cache(cache, 101, _raw(DEEPA))
    connector = SciJudisConnector(_manifest(tmp_path, [[100, 101]]), SOURCE, cache, offline=True)
    assert [d.external_id for d in connector.fetch()] == ["sci-judis:101"]
    assert len(connector.fetch_errors) == 1 and "not a PDF" in connector.fetch_errors[0]


def test_connector_refuses_bad_manifests_and_obeys_robots_txt(tmp_path: Path) -> None:
    for ranges, extra, message in (
        ([], {}, "no number ranges"),
        ([[0, 5]], {}, "invalid JUDIS number"),
        ([[1, 2000000]], {}, "invalid JUDIS number"),
        ([[10, 5]], {}, "ascending"),
        ([[1, 10], [10, 20]], {}, "ascending"),
        ([[1, 10], [5, 6]], {}, "ascending"),
        ([[1]], {}, "must be"),
        ([["1", "2"]], {}, "must be"),
        ([[1, 5]], {"base_url": "http://api.sci.gov.in"}, "base_url"),
        ([[1, 5]], {"user_agent": "python-urllib"}, "user_agent"),
        ([[1, 5]], {"crawl_delay_seconds": 1}, "at least 5"),
    ):
        with pytest.raises(ManifestError, match=message):
            list(SciJudisConnector(_manifest(tmp_path, ranges, **extra), SOURCE, tmp_path).fetch())
    with pytest.raises(ManifestError, match="source_id"):
        list(SciJudisConnector(_manifest(tmp_path, [[1, 5]], source_id="other"), SOURCE, tmp_path).fetch())
    blocked = tmp_path / "blocked"
    _cache_robots(blocked, "User-agent: *\nDisallow: /jonew/\n")
    _cache(blocked, 100, _raw(NATHMAL))
    with pytest.raises(FetchError, match="disallowed by"):
        list(SciJudisConnector(_manifest(tmp_path, [[100, 100]]), SOURCE, blocked, offline=True).fetch())


def test_a_refusal_stops_the_run(tmp_path: Path) -> None:
    class Refusing(PoliteHttp):
        def get(self, url: str, cache_key: str | None = None, headers: dict[str, str] | None = None) -> Any:
            raise FetchError(f"{url}: HTTP 403 (not retried: a refusal is respected)")

    connector = SciJudisConnector(_manifest(tmp_path, [[1, 3]]), SOURCE, tmp_path, offline=True)
    import ulis.sources.connectors.sci_judis as module

    original = module.PoliteHttp
    module.PoliteHttp = Refusing  # type: ignore[misc]
    try:
        with pytest.raises(FetchError, match="HTTP 403"):
            list(connector.fetch())
    finally:
        module.PoliteHttp = original  # type: ignore[misc]


# --------------------------------------------------------------------------- PostgreSQL


class _Judgments:
    def fetch(self) -> Iterator[RawDocument]:
        for number in ALL:
            yield RawDocument(
                f"sci-judis:{number}",
                judgment_url(number),
                "application/pdf",
                _raw(number),
                datetime(2026, 10, 16, 8, 0, tzinfo=UTC),
                {"judis_id": number},
            )


@pytest.fixture(scope="module")
def judgments(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, ingested: IngestionReport
) -> IngestionReport:
    with session_factory() as session:
        report = Ingestor(session, catalog, store).ingest_source(SOURCE, actor="pytest", connector=_Judgments())
        session.commit()
    return report


def _search(session: Session, catalog: Catalog, **kw: Any) -> dict[str, Any]:
    return search(session, catalog, SearchRequest(jurisdiction="IN", **kw), today=date(2026, 10, 16))


@pytest.mark.db
def test_ingest_report(judgments: IngestionReport) -> None:
    assert judgments.errors == [] and judgments.conflicts == []
    assert judgments.documents_new == 7 and judgments.judgments_created == 7
    expected = sum(len(_doc(n).paragraphs) for n in ALL)
    assert judgments.paragraphs_created == judgments.passages_indexed == expected
    assert judgments.instruments_created == 0 and judgments.citation_links_created == 0


@pytest.mark.db
def test_search_returns_the_passage_with_the_reporter_citation_and_limitations(
    session: Session, catalog: Catalog, judgments: IngestionReport
) -> None:
    res = _search(session, catalog, q="Assam Sales Tax Act reassessment limitation advisory jurisdiction High Court")
    hit = res["results"][0]
    assert hit["kind"] == "judgment_paragraph"
    assert hit["document"]["title"] == "NATHMAL TOLARAM v. SUPERINTENDENT OF TAXES, DHUBRI AND ANOTHER."
    assert (hit["document"]["court_id"], hit["document"]["decision_date"]) == ("IN-SC", "1960-10-18")
    assert hit["citation"] == (
        "NATHMAL TOLARAM v. SUPERINTENDENT OF TAXES, DHUBRI AND ANOTHER., 1961 AIR 331; 1961 SCR (2) 40 "
        "(Supreme Court of India, 18 October 1960)"
    )
    assert hit["unit"]["heading"] in ("catchwords", "headnote", "text")
    assert hit["version"]["authoritative"] is False and hit["version"]["text_origin"] == "official_conversion"
    codes = {lim["code"] for lim in hit["limitations"]}
    assert {"PROFILE_UNVERIFIED", "CITATOR_COVERAGE", "SOURCE_NOT_AUTHENTIC"} <= codes
    assert hit["source"]["attribution"].startswith("Judgment of the Supreme Court of India as published by the Court")


@pytest.mark.db
def test_search_finds_a_numbered_paragraph_with_its_number(
    session: Session, catalog: Catalog, judgments: IngestionReport
) -> None:
    res = _search(
        session,
        catalog,
        q="Defendant Nos. 1, 2, 4 to 7 are directed to return the sum of Rs.1,53,000/- sale consideration",
    )
    hit = res["results"][0]
    assert hit["document"]["title"] == "Vimaleshwar Nagappa Shet v. Noor Ahmed Sheriff & Ors."
    assert hit["unit"]["eid"] == "para-15" and hit["unit"]["label"] == "para 15"
    assert hit["citation"] == (
        "Vimaleshwar Nagappa Shet v. Noor Ahmed Sheriff & Ors., CIVIL APPEAL NOs. 4279-4280 OF 2011 "
        "(Supreme Court of India, 11 May 2011), para 15"
    )


@pytest.mark.db
def test_a_high_court_as_forum_is_bound_by_the_supreme_court(
    session: Session, catalog: Catalog, judgments: IngestionReport
) -> None:
    q = "requisitioned vehicle election duty compensation"
    hit = _search(session, catalog, q=q)["results"][0]
    assert hit["document"]["court_id"] == "IN-SC" and hit["authority"]["category"] == "precedent_vertical"
    bound = _search(session, catalog, q=q, forum_court="IN-HC-DEL")["results"][0]
    assert bound["authority"]["category"] == "binding_precedent"
    assert catalog.jurisdictions.get("IN").courts[0].id == "IN-SC" and len(catalog.jurisdictions.get("IN").courts) == 26


@pytest.mark.db
def test_rows_verification_and_reingest(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, judgments: IngestionReport
) -> None:
    with session_factory() as session:
        rows = {j.decision_date: j for j in session.scalars(select(m.Judgment).where(m.Judgment.court_id == "IN-SC"))}
        assert set(rows) == {
            date(1960, 10, 18),
            date(1978, 9, 13),
            date(1996, 10, 1),
            date(2007, 12, 11),
            date(2011, 5, 11),
            date(2014, 9, 23),
            date(2009, 2, 16),
        }
        preetam = rows[date(2014, 9, 23)]
        assert preetam.docket == "CIVIL APPEAL NO. 6307 OF 2010" and preetam.neutral_citation is None
        assert preetam.ext["layout"] == "signed_judgment" and preetam.ext["bench"] == [
            "JAGDISH SINGH KHEHAR",
            "ARUN MISHRA",
        ]
        again = Ingestor(session, catalog, store).ingest_source(SOURCE, actor="pytest", connector=_Judgments())
        assert again.documents_unchanged == 7 and again.judgments_created == 0 and again.paragraphs_created == 0
        ids = session.scalars(select(m.SourceDocument.id).where(m.SourceDocument.source_id == SOURCE)).all()
        results = [verify_source_document(session, catalog, store, i, checker="pytest") for i in ids]
        assert len(results) == 7 and all(r["result"] == "pass" for r in results)
        session.rollback()

"""eCFR (GPO bulk-data XML, current text): connector, parser, citations and PostgreSQL.

Fixtures (tests/fixtures/ecfr/README.md) are Title 1 as downloaded from govinfo.gov on 2026-10-14 and excerpts of
Titles 12 and 17 (whole CFR parts, exact bytes) from the same day.
"""

from __future__ import annotations

import gzip
import io
import json
import re
import urllib.error
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from defusedxml.ElementTree import fromstring
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from ulis.catalog import Catalog
from ulis.db import models as m
from ulis.ingestion.pipeline import CONNECTORS, PARSERS, IngestionReport, Ingestor, resolve_uri
from ulis.jurisdictions.modules.base import ProvisionCitationInput
from ulis.jurisdictions.modules.us import UsModule
from ulis.retrieval.lookup import provision_at
from ulis.retrieval.search import SearchRequest, search
from ulis.settings import REPO_ROOT
from ulis.sources.connectors import polite_http
from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.ecfr import ENVELOPE_TAG, EcfrConnector, TitleSplit, title_url
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.moj_laws import robots_allows
from ulis.sources.connectors.polite_http import FetchError, PoliteHttp
from ulis.sources.normalized import ParsedDocument, ParseSkipped
from ulis.sources.parsers import ecfr
from ulis.sources.parsers.ecfr import EcfrParseError, _section_number, parse_ecfr
from ulis.sources.registry import licence_gate
from ulis.storage.object_store import LocalObjectStore
from ulis.verification.integrity import verify_source_document

SOURCE = "us-ecfr"
FIX = REPO_ROOT / "tests" / "fixtures" / "ecfr"
MODIFIED = {  # Last-Modified of the downloads of 2026-10-14
    1: "Tue, 11 Aug 2026 20:55:31 GMT",
    12: "Tue, 06 Oct 2026 21:51:17 GMT",
    17: "Fri, 09 Oct 2026 20:46:26 GMT",
}
FILES = {1: "ECFR-title1.xml.gz", 12: "ECFR-title12-excerpt.xml.gz", 17: "ECFR-title17-excerpt.xml.gz"}
USER_AGENT = "ULIS-research-bot/0.1 (+https://github.com/CHEDFOX/Blue-Cat-Backend)"
GOVINFO_ROBOTS = "User-agent: *\nDisallow: /core/\nDisallow: /app/search/*\n"
# ecfr.gov/robots.txt as read on 2026-10-14 (the groups that matter here)
ECFR_GOV_ROBOTS = """User-agent: *
  Sitemap: https://www.ecfr.gov/sitemaps/sitemap.xml.gz
  Disallow: /search
  Disallow: /on/
  # Don't index developer tool links
  Disallow: /api/renderer/v1/content/
  Disallow: /api/versioner/v1/full/
"""


def _file(title: int) -> bytes:
    return gzip.decompress((FIX / FILES[title]).read_bytes())


def _split(title: int, modified: str | None = None) -> TitleSplit:
    stamp = datetime.strptime(modified or MODIFIED[title], "%a, %d %b %Y %H:%M:%S GMT").replace(tzinfo=UTC)
    return TitleSplit(_file(title), title, title_url(title), stamp, "0" * 64)


def _docs(title: int) -> dict[str, ParsedDocument]:
    split = _split(title)
    return {number: parse_ecfr(envelope)[0] for number, envelope in split.parts()}


def _by_eid(doc: ParsedDocument) -> dict[str, Any]:
    return {p.eid: p for p in doc.provisions}


def _ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------- manifest and URLs


def test_committed_manifest_lists_all_fifty_titles_and_passes_the_connectors_validation() -> None:
    path = REPO_ROOT / "config" / "manifests" / "us-ecfr.yaml"
    manifest = EcfrConnector(path, SOURCE, Path("/nonexistent"))._manifest()
    titles = manifest["titles"]
    assert [t["number"] for t in titles] == list(range(1, 51))
    assert [t["number"] for t in titles if t.get("reserved")] == [35]
    assert all(t["name"] for t in titles)
    assert manifest["base_url"] == "https://www.govinfo.gov"
    assert title_url(40) == "https://www.govinfo.gov/bulkdata/ECFR/title-40/ECFR-title40.xml"
    for bad in (0, 51, "12"):
        with pytest.raises(ManifestError):
            title_url(bad)  # type: ignore[arg-type]


def test_source_is_active_licence_approved_registered_and_never_uses_the_ecfr_api(catalog: Catalog) -> None:
    source = catalog.sources.get(SOURCE)
    licence_gate(source)
    assert source.status == "active" and source.document_kinds == ["regulation"]
    assert source.authority == "official_non_authentic"
    assert CONNECTORS[source.connector or ""] is EcfrConnector and source.parser in PARSERS
    assert SOURCE in catalog.jurisdictions.get("US").publication.official_sources
    # The eCFR site's robots.txt closes its full-text API to every robot: such a request is never made ...
    for closed in (
        "/api/versioner/v1/full/2026-10-09/title-12.xml",
        "/api/renderer/v1/content/enhanced/2026-10-09/title-12",
    ):
        assert not robots_allows(ECFR_GOV_ROBOTS, USER_AGENT, f"https://www.ecfr.gov{closed}")
    assert robots_allows(ECFR_GOV_ROBOTS, USER_AGENT, "https://www.ecfr.gov/api/versioner/v1/titles.json")
    # ... and the shared fetcher would refuse the host anyway: only www.govinfo.gov is allowed.
    http = PoliteHttp(Path("/nonexistent"), USER_AGENT, allowed_hosts={"www.govinfo.gov"}, offline=True)
    with pytest.raises(FetchError, match="only https"):
        http.get("https://www.ecfr.gov/api/versioner/v1/full/2026-10-09/title-12.xml")


# ---------------------------------------------------------------- splitting a title file


def test_title_file_is_split_into_parts_with_the_exact_part_bytes() -> None:
    split = _split(1)
    data = split.data
    parts = list(split.parts())
    assert len(parts) == 28 and split.empty_parts == 8 and split.outside_parts == 0
    assert [n for n, _ in parts][:4] == ["1", "2", "3", "5"] and "23-49" not in split.part_numbers
    number, envelope = parts[0]
    root = fromstring(envelope, forbid_dtd=True)
    assert root.tag == ENVELOPE_TAG and root.get("title-number") == "1"
    assert root.get("last-modified") == "2026-08-11T20:55:31+00:00" and root.get("source-url") == title_url(1)
    assert (root.findtext("amddate") or "").strip() == "Dec. 29, 2022(fm)"
    assert [(a.get("level"), a.get("n"), a.get("type")) for a in root.findall("ancestor")] == [
        ("1", "1", "TITLE"),
        ("3", "I", "CHAPTER"),
        ("4", "A", "SUBCHAP"),
    ]
    start = data.index(b'<DIV5 N="1" ')
    assert data[start : data.index(b"</DIV5>", start) + 7] in envelope  # the part exactly as received
    assert sum(len(e) for _, e in parts) < len(data) + 40_000
    # a part's N in two places: the excerpt of Title 12 has parts of several chapters
    assert [n for n, _ in _split(12).parts()] == ["21", "52", "220", "302", "700"]


def test_parser_rejects_foreign_unsafe_or_incomplete_input() -> None:
    for payload in (
        b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><ecfr-part>&e;</ecfr-part>',
        b"<DLPSTEXTCLASS/>",
        b'<ecfr-part title-number="1"/>',
        b'<ecfr-part title-number="1" last-modified="2026-08-11T20:55:31+00:00"><ancestor/></ecfr-part>',
        b"not xml",
    ):
        with pytest.raises(EcfrParseError):
            parse_ecfr(payload)
    reserved = (
        b'<ecfr-part title-number="1" last-modified="2026-08-11T20:55:31+00:00"><amddate/>'
        b'<DIV5 N="23-49" TYPE="PART"><HEAD>PARTS 23-49 [RESERVED]</HEAD></DIV5></ecfr-part>'
    )
    with pytest.raises(ParseSkipped):
        parse_ecfr(reserved)


# ---------------------------------------------------------------- parser: structure, text, dates


def test_part_is_one_dated_regulation_instrument() -> None:
    d = _docs(12)["220"]
    assert (d.doc_kind, d.instrument_type, d.jurisdiction_id, d.language) == ("legislation", "regulation", "US", "en")
    assert d.work_uri == "/us/cfr/t12/p220" and d.number == "220" and d.short_title == "12 CFR Part 220"
    assert d.work_title == "12 CFR Part 220: Credit by Brokers and Dealers (Regulation T)"
    assert d.title.endswith("(eCFR, file of 2026-10-06)")
    assert d.expression_uri == "/us/cfr/t12/p220/en@2026-10-06/ecfr-215117Z"
    # dated by the file's Last-Modified only; no start date is invented for any provision
    assert (str(d.version_date), str(d.currency_date), d.work_date) == ("2026-10-06", "2026-10-06", None)
    assert d.validity_basis == "attested_at_edition" and d.consolidation_kind == "editorial_compilation"
    assert (d.text_origin, d.authoritative) == ("official_consolidation", False)
    assert {(p.valid_from, p.valid_to, p.status) for p in d.provisions} == {(None, None, "in_force")}
    assert d.ext == {
        "cfr_title": 12,
        "cfr_part": "220",
        "cfr_compilation": "ecfr",
        "agency": "Federal Reserve System",  # "(CONTINUED)" of a chapter that spans volumes is dropped
        "cfr_chapter": "II",
        "cfr_authority": "15 U.S.C. 78c, 78g, 78q, and 78w.",
        "volume_amended_on": "2026-10-01",
    }


def test_section_paragraphs_nest_by_their_labels_and_keep_the_source_text() -> None:
    p = _by_eid(_docs(12)["220"])
    sec = p["sec_220.1"]
    assert (sec.kind, sec.num_path, sec.heading) == ("section", ["220.1"], "Authority, purpose, and scope.")
    assert sec.note == "Source credit: [Reg. T, 63 FR 2820, Jan. 16, 1998]"
    assert not sec.is_leaf and not sec.has_own_content and not sec.indexable  # its paragraphs are indexed instead
    b = p["sec_220.1__para_b"]
    assert (b.heading, b.num_path, b.parent_eid) == ("Scope.", ["220.1", "b"], "sec_220.1")
    assert b.text.startswith("Scope.\n(1) This part provides a margin account and four special purpose accounts")
    b3 = p["sec_220.1__para_b__para_3"]
    assert b3.text.startswith("This part does not apply to:\n(i) Financial relations between a customer and a creditor")
    assert b3.has_own_content and b3.indexable and b3.num_path == ["220.1", "b", "3"]
    leaf = p["sec_220.1__para_b__para_3__para_ii"]
    assert leaf.is_leaf and leaf.top_kind == "section" and leaf.context_heading == sec.heading
    assert leaf.text == (
        "Credit extended by a creditor based on a good faith determination that the borrower is an exempted borrower;"
    )
    # sibling order and parents
    order = [e for e in p if e.startswith("sec_220.1__para_b__para_3__")]
    assert order[:4] == [f"sec_220.1__para_b__para_3__para_{r}" for r in ("i", "ii", "iii", "iv")]


def test_labels_inside_one_paragraph_and_italic_levels_and_tables() -> None:
    """'(a) Scope. (1) text' in one P makes two provisions; the fifth level is printed in italics."""
    docs = _docs(17)
    p = _by_eid(docs["450"])
    a2 = p["sec_450.4__para_a__para_2__para_i__para_A"]
    assert a2.num_path == ["450.4", "a", "2", "i", "A"] and a2.is_leaf
    deep = p["sec_450.4__para_a__para_3__para_ii__para_A__para_1"]  # (a)(3)(ii)(A)(1), italic 1
    assert deep.text.startswith("Because of extraordinary circumstances") and deep.parent_eid.endswith("para_A")
    assert "(i) Where customer securities" in p["sec_450.4__para_a__para_2"].text
    defs = p["sec_450.2__para_e"]
    assert defs.text.startswith("Government securities means:\nIf . . . | Then . . .")  # one table row per line
    assert "|" in defs.text
    multi = _by_eid(_docs(12)["21"])["sec_21.3__para_a"]
    assert multi.heading == "Contents of security program." and multi.text.startswith(
        "Contents of security program. The security program shall:\n(1) "
    )


def test_every_source_paragraph_is_in_the_text_of_its_section() -> None:
    """Exactness: leaving whitespace aside, each P of the source appears in the stored text of its section."""
    split = _split(1)
    checked = 0
    for _, envelope in split.parts():
        root = fromstring(envelope, forbid_dtd=True)
        doc = parse_ecfr(envelope)[0]
        by_num = {p.num_path[0]: p for p in doc.provisions if p.kind == "section"}
        for section in root.iter("DIV8"):
            num = (section.get("N") or "").removeprefix("§").strip()
            if num not in by_num:
                continue  # a reserved range
            stored = by_num[num].text
            for para in section.findall("P"):
                if para.find(".//FTREF") is not None or para.find(".//img") is not None:
                    continue
                assert re.sub(r"\s", "", "".join(para.itertext())) in re.sub(r"\s", "", stored), (num, para.text)
                checked += 1
    assert checked > 1000


def test_numbers_repeated_by_definitions_are_flagged_and_irregular_sections_stay_whole() -> None:
    docs = _docs(12)
    p52 = _by_eid(docs["52"])
    assert "sec_52.2__para_1" in p52 and "sec_52.2__para_1_dup2" in p52
    assert "Ambiguous pinpoint" in p52["sec_52.2__para_1_dup2"].note and p52["sec_52.2__para_1"].note is None
    whole = _by_eid(docs["700"])["sec_700.2"]
    assert whole.is_leaf and "Paragraph levels were not derived" in whole.note
    assert not [e for e in _by_eid(docs["700"]) if e.startswith("sec_700.2__")]
    # "(d)-(e) [Reserved]" is one label spanning two paragraphs
    assert [(t.label, t.end) for t in ecfr._leading_labels("(d)-(e) [Reserved]")[0]] == [("d)-(e", "e")]


def test_appendix_footnote_and_notes() -> None:
    p = _by_eid(_docs(12)["302"])
    app = p["app_A"]
    assert (app.kind, app.num_path, app.top_kind) == ("appendix", ["A"], "appendix")
    assert app.heading == "Appendix A to Part 302—Statement Clarifying the Role of Supervisory Guidance"
    assert "Footnote: 1 Government agencies issue regulations" in app.note
    assert "Government agencies issue regulations" not in app.text  # footnotes are notes, not text
    ed = _by_eid(_docs(12)["700"])
    assert ed["sec_700.1"].note.startswith("Source credit: [78 FR 32543, May 31, 2013]")


def test_a_long_appendix_without_a_number_is_cut_at_its_headings() -> None:
    """'Supplement I to Part N' has an empty N in the XML; its text, hundreds of kilobytes in real parts, is cut into
    blocks that start at HD1/HD2 headings (or at a size limit), so that a search hit is a passage, not the appendix."""
    heading = lambda n: f"<HD1>Section {n} Definitions</HD1>"  # noqa: E731
    body = "<P>" + "1. The term includes devices of every kind that a consumer may use. " * 40 + "</P>"
    supplement = "".join(heading(n) + body + f"<HD2>{n}(a) Account</HD2>" + body for n in range(1, 8))
    xml = (
        '<ecfr-part title-number="12" last-modified="2026-10-06T21:51:17+00:00" source-url="https://x"><amddate/>'
        '<DIV5 N="1005" TYPE="PART"><HEAD>PART 1005—ELECTRONIC FUND TRANSFERS (REGULATION E)</HEAD>'
        f'<DIV9 N="" TYPE="APPENDIX"><HEAD>Supplement I to Part 1005—Official Interpretations</HEAD>{supplement}</DIV9>'
        '<DIV9 N="Appendix B" TYPE="APPENDIX"><HEAD>Appendix B to Part 1005 [Reserved]</HEAD></DIV9>'
        "</DIV5></ecfr-part>"
    ).encode()
    doc = parse_ecfr(xml)[0]
    by = _by_eid(doc)
    top = by["app_Supplement_I"]
    assert top.num_path == ["Supplement I"] and not top.is_leaf and not top.indexable
    blocks = [p for p in doc.provisions if p.parent_eid == "app_Supplement_I"]
    assert len(blocks) == 14 and blocks[0].heading == "Section 1 Definitions" and blocks[1].heading == "1(a) Account"
    assert all(b.is_leaf and b.indexable and b.num_path == ["Supplement I", ""] for b in blocks)
    assert blocks[0].eid == "app_Supplement_I__blk_1" and len(blocks[0].text) < 3000
    assert _cite(doc, "app_Supplement_I__blk_3") == "12 CFR part 1005, supplement I"
    assert len(doc.provisions) == 15  # the reserved appendix has no text


def test_label_sequences() -> None:
    def levels(*labels: str, gaps: list[bool] | None = None) -> list[int] | None:
        tokens = [ecfr._Token(label, False) for label in labels]
        return ecfr._assign_levels([ecfr._readings(t) for t in tokens], gaps or [False] * len(labels))

    assert levels("a", "1", "2", "b", "1", "i", "ii", "2") == [1, 2, 2, 1, 2, 3, 3, 2]
    assert levels(*"abcdefghi") == [1] * 9  # (i) after (h) is the ninth letter ...
    assert levels("a", "1", "i", "ii", "2") == [1, 2, 3, 3, 2]  # ... and after (1) it is the roman numeral
    assert levels("a", "i", "ii") is None  # a roman level directly under (a), with no (1) between: not guessed
    assert levels("1", "2", "i") == [2, 2, 3]  # a section may start at "(1)"
    assert levels("a", "c") is None  # a missing (b): the section is kept whole instead of guessing
    assert levels("a", "1", "1", gaps=[False, False, False]) is None
    assert levels("a", "1", "1", gaps=[False, False, True]) == [1, 2, 2]  # numbering restarts after unnumbered text


# ---------------------------------------------------------------- citations and identifiers


def _cite(doc: ParsedDocument, eid: str) -> str:
    p = _by_eid(doc)[eid]
    return UsModule().provision_citation(
        ProvisionCitationInput(
            instrument_title=doc.title,
            instrument_type=doc.instrument_type,
            jurisdiction_id="US",
            top_kind=p.top_kind,
            num_path=p.num_path,
            language="en",
            ext=doc.ext,
            version_date=doc.version_date,
            eid=p.eid,
        )
    )


def test_citations_follow_the_federal_register_form_and_the_annual_edition_form_is_unchanged() -> None:
    d = _docs(12)
    assert _cite(d["220"], "sec_220.1") == "12 CFR 220.1"
    assert _cite(d["220"], "sec_220.1__para_b") == "12 CFR 220.1(b)"
    assert _cite(d["220"], "sec_220.1__para_b__para_3__para_ii") == "12 CFR 220.1(b)(3)(ii)"
    assert _cite(d["302"], "app_A") == "12 CFR part 302, appendix A"
    sample = {"cfr_title": 27, "cfr_part": "478"}
    old = UsModule().provision_citation(
        ProvisionCitationInput(
            "27 CFR Part 478",
            "regulation",
            "US",
            "section",
            ["478.11", "b"],
            "en",
            sample,
            version_date=date(2018, 4, 1),
        )
    )
    assert old == "27 C.F.R. § 478.11(b) (2018)"


def test_cfr_identifiers_resolve_to_the_eids_of_this_parser() -> None:
    doc = _docs(12)["220"]
    work, eid = resolve_uri("/us/cfr/t12/s220.1/b/3/ii")
    assert work == doc.work_uri and eid in _by_eid(doc)


# ---------------------------------------------------------------- connector


def _manifest(tmp_path: Path, numbers: list[int], **extra: Any) -> Path:
    manifest = {
        "schema_version": 1,
        "source_id": SOURCE,
        "base_url": "https://www.govinfo.gov",
        "user_agent": USER_AGENT,
        "titles": [{"number": n} for n in numbers],
    }
    manifest.update(extra)
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(manifest))
    return path


def _seed(cache: Path, url: str, data: bytes, **meta: Any) -> None:
    target = cache / url.removeprefix("https://")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    full = {"url": url, "final_url": url, "status": 200, "content_type": "text/xml", **meta}
    target.with_name(target.name + ".meta.json").write_text(
        json.dumps({"retrieved_at": "2026-10-14T18:30:00+00:00", **full})
    )


def _cache(tmp_path: Path, titles: list[int], robots: str = GOVINFO_ROBOTS) -> Path:
    """A PoliteHttp cache as an earlier online run leaves it, built from the fixtures."""
    cache = tmp_path / "cache"
    _seed(cache, "https://www.govinfo.gov/robots.txt", robots.encode(), content_type="text/plain")
    for n in titles:
        _seed(cache, title_url(n), _file(n), last_modified=MODIFIED[n])
    return cache


def test_connector_offline_from_cache_yields_one_document_per_part(tmp_path: Path) -> None:
    connector = EcfrConnector(
        _manifest(tmp_path, [1, 12, 35, 17], titles=[{"number": 1}, {"number": 12}, {"number": 35, "reserved": True}]),
        SOURCE,
        _cache(tmp_path, [1, 12]),
        offline=True,
    )
    docs = list(connector.fetch())  # title 35 is reserved: never requested (it is not even in the cache)
    assert len(docs) == 28 + 5 and connector.fetch_errors == [] and connector.empty_parts == {1: 8, 12: 0}
    first = docs[0]
    assert first.external_id == "ecfr:t1:p1" and first.uri == f"{title_url(1)}#part-1"
    assert first.media_type == "application/xml" and first.retrieved_at == datetime(2026, 10, 14, 18, 30, tzinfo=UTC)
    assert first.metadata == {"title": 1, "part": "1", "last_modified": "2026-08-11T20:55:31+00:00"}
    assert docs[-1].external_id == "ecfr:t12:p700"
    assert len({d.external_id for d in docs}) == len(docs)


def test_connector_refuses_what_robots_txt_the_manifest_or_the_response_forbid(tmp_path: Path) -> None:
    def run(path: Path, cache: Path) -> list[RawDocument]:
        return list(EcfrConnector(path, SOURCE, cache, offline=True, sleep=lambda s: None).fetch())

    with pytest.raises(FetchError, match="disallowed by"):
        run(_manifest(tmp_path, [1]), _cache(tmp_path, [1], robots="User-agent: *\nDisallow: /bulkdata/\n"))
    with pytest.raises(ManifestError, match="base_url"):
        run(_manifest(tmp_path, [1], base_url="https://www.ecfr.gov"), _cache(tmp_path, [1]))
    with pytest.raises(ManifestError, match="identifying"):
        run(_manifest(tmp_path, [1], user_agent="python-urllib"), _cache(tmp_path, [1]))
    with pytest.raises(ManifestError, match="invalid or repeated"):
        run(_manifest(tmp_path, [1, 1]), _cache(tmp_path, [1]))
    with pytest.raises(ManifestError, match="invalid CFR title"):
        run(_manifest(tmp_path, [51]), _cache(tmp_path, [1]))
    with pytest.raises(ManifestError, match="sha256"):
        run(_manifest(tmp_path, [1], titles=[{"number": 1, "sha256": "0" * 64}]), _cache(tmp_path, [1]))
    with pytest.raises(FetchError, match="not an eCFR XML"):  # a block page is not parsed or stored
        cache = _cache(tmp_path, [])
        _seed(cache, title_url(1), b"<html>Request blocked</html>", last_modified=MODIFIED[1])
        run(_manifest(tmp_path, [1]), cache)


def test_connector_skips_a_missing_or_undated_title_but_stops_on_a_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = _cache(tmp_path, [1])
    _seed(cache, title_url(17), _file(17))  # a file that came without a Last-Modified header
    _seed(cache, title_url(12), b"", status=404)
    connector = EcfrConnector(_manifest(tmp_path, [12, 17, 1]), SOURCE, cache, offline=True)
    docs = list(connector.fetch())
    assert len(docs) == 28  # Title 1 is still read
    assert len(connector.fetch_errors) == 2
    assert "title 12" in connector.fetch_errors[0] and "404" in connector.fetch_errors[0]
    assert "title 17" in connector.fetch_errors[1] and "Last-Modified" in connector.fetch_errors[1]

    def forbidden(request: Any, timeout: float) -> None:
        raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {}, io.BytesIO(b""))  # type: ignore[arg-type]

    monkeypatch.setattr(polite_http.urllib.request, "urlopen", forbidden)
    refused = EcfrConnector(_manifest(tmp_path, [40, 1]), SOURCE, cache, sleep=lambda s: None)
    with pytest.raises(FetchError, match="HTTP 403"):
        list(refused.fetch())


# ---------------------------------------------------------------- PostgreSQL


class _FromCache:
    """The real connector reading the fixture cache offline (so the stored raw documents are its envelopes)."""

    def __init__(self, tmp: Path, modified: dict[int, str] | None = None, titles: list[int] | None = None):
        numbers = titles or [1, 12, 17]
        cache = _cache(tmp, [])
        for n in numbers:
            _seed(cache, title_url(n), _file(n), last_modified=(modified or MODIFIED)[n])
        self.connector = EcfrConnector(_manifest(tmp, numbers), SOURCE, cache, offline=True)

    def fetch(self) -> Iterator[RawDocument]:
        return self.connector.fetch()


@pytest.fixture(scope="module")
def ecfr_db(
    session_factory: sessionmaker[Session],
    catalog: Catalog,
    store: LocalObjectStore,
    ingested: IngestionReport,
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[tuple[Session, IngestionReport, Ingestor]]:
    """The fixtures ingested in one transaction that is never committed, so the shared test database (and the US
    counts other tests make) is untouched."""
    session = session_factory()
    ingestor = Ingestor(session, catalog, store)
    report = ingestor.ingest_source(SOURCE, actor="pytest", connector=_FromCache(tmp_path_factory.mktemp("ecfr1")))
    session.flush()
    try:
        yield session, report, ingestor
    finally:
        session.rollback()
        session.close()


def _search(session: Session, catalog: Catalog, **kw: Any) -> dict[str, Any]:
    return search(session, catalog, SearchRequest(jurisdiction="US", **kw), today=date(2026, 10, 14))


def _codes(item: dict[str, Any]) -> set[str]:
    return {lim["code"] for lim in item["limitations"]}


@pytest.mark.db
def test_ingestion_of_the_fixtures(ecfr_db: tuple[Session, IngestionReport, Ingestor]) -> None:
    session, report, _ = ecfr_db
    assert report.errors == [] and report.conflicts == [] and report.documents_new == 28 + 5 + 1
    assert report.documents_skipped == 0 and report.instruments_created == 34
    rows = session.execute(
        select(m.Instrument.uri, m.Instrument.instrument_type).where(
            m.Instrument.uri.in_(["/us/cfr/t12/p220", "/us/cfr/t1/p1"])
        )
    ).all()
    assert sorted(rows) == [("/us/cfr/t1/p1", "regulation"), ("/us/cfr/t12/p220", "regulation")]
    versions = session.scalars(
        select(m.InstrumentVersion).join(m.Instrument).where(m.Instrument.uri == "/us/cfr/t12/p220")
    ).all()
    assert [(v.version_date, v.validity_basis, v.authoritative) for v in versions] == [
        (date(2026, 10, 6), "attested_at_edition", False)
    ]


@pytest.mark.db
def test_search_finds_a_paragraph_with_the_federal_register_citation_and_limitations(
    ecfr_db: tuple[Session, IngestionReport, Ingestor], catalog: Catalog
) -> None:
    session, _, _ = ecfr_db
    res = _search(
        session,
        catalog,
        q="margin account and four special purpose accounts to record financial relations of a customer and a creditor",
    )
    hit = next(h for h in res["results"] if h["document"]["uri"] == "/us/cfr/t12/p220")
    assert hit["citation"] == "12 CFR 220.1(b)(1)"
    assert hit["text"].startswith("This part provides a margin account and four special purpose accounts")
    assert (
        hit["version"]["currency_date"] == "2026-10-06" and hit["version"]["consolidation"] == "editorial_compilation"
    )
    assert {
        "ATTESTED_ONLY_AT",
        "AS_OF_AFTER_CURRENCY",
        "OFFICIAL_CONSOLIDATION_REPRODUCED",
        "SOURCE_NOT_AUTHENTIC",
        "PROFILE_UNVERIFIED",
    } <= _codes(hit)
    attested = next(lim for lim in hit["limitations"] if lim["code"] == "ATTESTED_ONLY_AT")
    assert "in force on 2026-10-06 (editorial compilation)" in attested["message"]
    # Regulations rank below statutes when a date is known (binding_regulation 1.2 against binding_legislation 1.25);
    # a text attested only at its edition date has an unverified applicability, for statutes and regulations alike.
    assert hit["authority"]["category"] == "applicability_unverified"


@pytest.mark.db
def test_section_and_appendix_lookups_follow_the_source(
    ecfr_db: tuple[Session, IngestionReport, Ingestor], catalog: Catalog
) -> None:
    session, _, _ = ecfr_db
    current = provision_at(session, catalog, "/us/cfr/t12/p220/~sec_220.1", date(2026, 10, 14))
    assert current["status_on_date"] == "in_force_date_unverified"
    assert current["provision"]["citation"] == "12 CFR 220.1"
    assert current["version"]["valid_from"] is None and current["instrument"]["title"].startswith("12 CFR Part 220")
    earlier = provision_at(session, catalog, "/us/cfr/t12/p220/~sec_220.1", date(2020, 1, 1))
    assert earlier["status_on_date"] == "in_force_date_unverified"
    assert "ATTESTED_ONLY_AT" in _codes(earlier) and "AS_OF_AFTER_CURRENCY" not in _codes(earlier)
    appendix = provision_at(session, catalog, "/us/cfr/t12/p302/~app_A", date(2026, 10, 14))
    assert appendix["provision"]["citation"] == "12 CFR part 302, appendix A"
    assert "Footnote:" in (appendix["version"].get("note") or "")


@pytest.mark.db
def test_a_refreshed_file_restates_a_changed_wording_and_ignores_an_unchanged_one(
    ecfr_db: tuple[Session, IngestionReport, Ingestor], tmp_path_factory: pytest.TempPathFactory
) -> None:
    session, _, ingestor = ecfr_db
    again = ingestor.ingest_source(SOURCE, actor="pytest", connector=_FromCache(tmp_path_factory.mktemp("ecfr2")))
    assert again.documents_new == 0 and again.documents_unchanged == 34  # the same bytes are not read twice
    # A later file of Title 12 (the same Last-Modified would be a no-op) whose Part 700 differs in one word.
    connector = _FromCache(tmp_path_factory.mktemp("ecfr3"), {12: "Fri, 09 Oct 2026 20:00:00 GMT"}, titles=[12])
    changed = []
    for raw in connector.fetch():
        if raw.external_id == "ecfr:t12:p700":
            text = raw.content.decode()
            assert "apply to terms used" in text
            raw = RawDocument(
                raw.external_id,
                raw.uri,
                raw.media_type,
                text.replace("apply to terms used", "apply to words used", 1).encode(),
                raw.retrieved_at,
                {},
            )
        changed.append(raw)

    class _Later:
        def fetch(self) -> Iterator[RawDocument]:
            return iter(changed)

    report = ingestor.ingest_source(SOURCE, actor="pytest", connector=_Later())
    assert report.errors == [] and report.conflicts == [] and report.documents_new == 5
    assert report.provision_versions_restated == 1 and report.provision_versions_corroborated > 100
    rows = session.execute(
        select(m.ProvisionVersion.text, m.ProvisionVersion.valid_from, m.ProvisionVersion.valid_from_basis)
        .join(m.Provision)
        .where(m.Provision.uri == "/us/cfr/t12/p700/~sec_700.1")
        .order_by(m.ProvisionVersion.valid_from.nulls_first())
    ).all()
    assert len(rows) == 2 and rows[0][1] is None and "apply to terms used" in rows[0][0]
    # the new wording certainly applied on the date of the later file; the source does not say since when
    assert (rows[1][1], rows[1][2]) == (date(2026, 10, 9), "observed") and "apply to words used" in rows[1][0]


@pytest.mark.db
def test_stored_envelopes_rederive_from_their_raw_documents(
    ecfr_db: tuple[Session, IngestionReport, Ingestor], catalog: Catalog, store: LocalObjectStore
) -> None:
    session, _, _ = ecfr_db
    docs = session.scalars(select(m.SourceDocument).where(m.SourceDocument.source_id == SOURCE).limit(12)).all()
    assert len(docs) == 12
    for doc in docs:
        result = verify_source_document(session, catalog, store, doc.id, checker="pytest")
        assert result["result"] == "pass" and result["checked_units"] > 0, result["problems"][:3]
    paragraphs = session.scalar(
        select(func.count())
        .select_from(m.Provision)
        .join(m.Instrument)
        .where(m.Instrument.uri.like("/us/cfr/t1/%"), m.Provision.kind == "paragraph")
    )
    assert paragraphs and paragraphs > 1000


def test_a_part_with_no_number_and_only_a_reserved_appendix_heading_is_an_empty_part() -> None:
    """Title 24 (2026-10-18) holds ``<DIV5 N="" TYPE="PART">`` whose only content is the heading "Appendixes A-C to
    Subtitle A [Reserved]" as a DIV9: no section, nothing to ingest. It used to stop the whole title ("unexpected part
    number ''"); it is now counted as an empty part. A numberless DIV5 that does hold sections is still refused."""
    head = (
        b'<?xml version="1.0"?><ECFR><DIV1 N="Title 24" TYPE="TITLE"><HEAD>Title 24</HEAD>'
        b'<DIV2 N="Subtitle A" TYPE="SUBTITLE"><HEAD>Subtitle A</HEAD>'
    )
    reserved = (
        b'<DIV5 N="" NODE="24:1.1.1.1.44" TYPE="PART">\n<HEAD> \n</HEAD>\n'
        b'<DIV9 N="" NODE="24:1.1.1.1.44.0.67.1.10" TYPE="APPENDIX">\n'
        b"<HEAD>Appendixes A-C to Subtitle A [Reserved]\n</HEAD>\n</DIV9>\n"
        b"</DIV5>"
    )
    part_5 = (
        b'<DIV5 N="5" NODE="24:1.1.1.1.5" TYPE="PART"><HEAD>PART 5-GENERAL</HEAD>'
        b'<DIV8 N="\xc2\xa7 5.100" NODE="24:1.1.1.1.5.0.1.1" TYPE="SECTION"><HEAD>\xc2\xa7 5.100 Definitions.</HEAD>'
        b"<P>Text.</P></DIV8></DIV5>"
    )
    tail = b"</DIV2></DIV1></ECFR>"
    stamp = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
    split = TitleSplit(head + reserved + part_5 + tail, 24, title_url(24), stamp, "0" * 64)
    parts = list(split.parts())
    assert [n for n, _ in parts] == ["5"] and split.empty_parts == 1 and split.outside_parts == 0
    numberless_with_sections = part_5.replace(b'N="5"', b'N=""')
    with pytest.raises(ManifestError, match="unexpected part number ''"):
        list(TitleSplit(head + numberless_with_sections + tail, 24, title_url(24), stamp, "0" * 64).parts())


def test_a_part_printed_across_several_volumes_is_one_envelope_and_one_document() -> None:
    """26 CFR part 1 fills fifteen volumes: GPO's title file holds fifteen ``<DIV5 N="1" TYPE="PART">`` elements,
    the later ones headed "PART 1-INCOME TAXES (CONTINUED)". The first full load stored the first chunk only and
    refused the fourteen others as a different document with the same version. They are one part now: one envelope
    holding every chunk, parsed into one document whose sections come from all of them."""
    head = (
        b'<?xml version="1.0"?><ECFR><DIV1 N="Title 26" TYPE="TITLE"><HEAD>Title 26</HEAD>'
        b'<DIV3 N="I" TYPE="CHAPTER"><HEAD>CHAPTER I-INTERNAL REVENUE SERVICE</HEAD>'
    )
    chunk_1 = (
        b'<DIV5 N="1" NODE="26:1.0.1.1.1" TYPE="PART"><HEAD>PART 1-INCOME TAXES</HEAD>'
        b"<AUTH><HED>Authority:</HED><PSPACE>26 U.S.C. 7805, unless otherwise noted.</PSPACE></AUTH>"
        b'<DIV8 N="\xc2\xa7 1.1-1" NODE="26:1.0.1.1.1.0.1.1" TYPE="SECTION">'
        b"<HEAD>\xc2\xa7 1.1-1 Income tax on individuals.</HEAD>"
        b"<P>(a) General rule.</P></DIV8></DIV5>"
    )
    between = (
        b'</DIV3></DIV1><DIV1 N="Title 26" TYPE="TITLE"><HEAD>Title 26</HEAD>'
        b'<DIV3 N="I" TYPE="CHAPTER"><HEAD>CHAPTER I-INTERNAL REVENUE SERVICE (CONTINUED)</HEAD>'
    )
    chunk_2 = (
        b'<DIV5 N="1" NODE="26:2.0.1.1.1" TYPE="PART"><HEAD>PART 1-INCOME TAXES (CONTINUED)</HEAD>'
        b"<AUTH><HED>Authority:</HED><PSPACE>26 U.S.C. 7805, unless otherwise noted.</PSPACE></AUTH>"
        b'<DIV8 N="\xc2\xa7 1.61-1" NODE="26:2.0.1.1.1.0.1.1" TYPE="SECTION"><HEAD>\xc2\xa7 1.61-1 Gross income.</HEAD>'
        b"<P>(a) General definition.</P></DIV8></DIV5>"
    )
    part_2 = (
        b'<DIV5 N="2" NODE="26:2.0.1.1.2" TYPE="PART"><HEAD>PART 2-MARITIME CONSTRUCTION RESERVE FUND</HEAD>'
        b'<DIV8 N="\xc2\xa7 2.1-1" NODE="26:2.0.1.1.2.0.1.1" TYPE="SECTION"><HEAD>\xc2\xa7 2.1-1 Definitions.</HEAD>'
        b"<P>(a) As used in the regulations in this part.</P></DIV8></DIV5>"
    )
    tail = b"</DIV3></DIV1></ECFR>"
    stamp = datetime(2026, 10, 13, 20, 47, 54, tzinfo=UTC)
    split = TitleSplit(head + chunk_1 + between + chunk_2 + part_2 + tail, 26, title_url(26), stamp, "0" * 64)
    parts = dict(split.parts())
    assert list(parts) == ["1", "2"] and split.part_numbers == ["1", "2"] and split.outside_parts == 0
    assert parts["1"].count(b"<DIV5 ") == 2 and chunk_1 in parts["1"] and chunk_2 in parts["1"]
    doc = parse_ecfr(parts["1"])[0]
    assert doc.work_uri == "/us/cfr/t26/p1" and doc.short_title == "26 CFR Part 1"
    assert "(continued)" not in doc.work_title.lower() and doc.work_title.endswith("Income Taxes")
    assert {p.num_path[0] for p in doc.provisions if p.kind == "section"} == {"1.1-1", "1.61-1"}
    assert len(parse_ecfr(parts["2"])[0].provisions) >= 1
    with pytest.raises(EcfrParseError, match="mixes the chunks"):
        parse_ecfr(parts["1"].replace(b'<DIV5 N="1" NODE="26:2.0.1.1.1"', b'<DIV5 N="9" NODE="26:2.0.1.1.1"'))


def test_title_48_sections_numbered_without_the_section_sign_are_provisions() -> None:
    """48 CFR (the Federal Acquisition Regulations System) numbers its sections "1.000", "52.212-4", "206.302-3-70"
    without the section sign; the first full load parsed all 817 parts of the title as empty and skipped them. Bare
    numbers are sections now; a bare range ("13.83-13.87", "52.223-13—52.223-18") is still a reserved block."""
    assert _section_number("§ 1.1", "§ 1.1 Purpose.") == ("1.1", "Purpose.")
    assert _section_number("§§ 3.4-3.9", "§§ 3.4-3.9 [Reserved]") == ("", "§§ 3.4-3.9 [Reserved]")
    assert _section_number("1.000", "1.000   Scope of part.") == ("1.000", "Scope of part.")
    assert _section_number("52.212-4", "52.212-4 Contract Terms and Conditions-Commercial Products.") == (
        "52.212-4",
        "Contract Terms and Conditions-Commercial Products.",
    )
    assert _section_number("206.302-3-70", "206.302-3-70 Scope.") == ("206.302-3-70", "Scope.")
    assert _section_number("352.270-4a", "352.270-4a Notice.")[0] == "352.270-4a"
    assert _section_number("752.227-71.", "752.227-71. Rights in data.") == ("752.227-71", "Rights in data.")
    assert _section_number("19-8.1", "19-8.1 Scope.") == ("19-8.1", "Scope.")
    assert _section_number("13.83-13.87", "13.83-13.87 [Reserved]")[0] == ""
    assert _section_number("52.223-13—52.223-18", "[Reserved]")[0] == ""
    assert _section_number("Sec. 1-1", "Sec. 1-1 Applicability.") == ("Sec. 1-1", "Applicability.")
    assert _section_number("Section 01", "Section 01 Scope.") == ("Section 01", "Scope.")
    part = (
        b'<?xml version="1.0" encoding="UTF-8"?><ecfr-part title-number="48" source-url="x"'
        b' last-modified="2026-10-13T20:47:54+00:00" file-sha256="0"><amddate>Oct. 1, 2026</amddate>'
        b'<ancestor level="3" n="1" type="CHAPTER"><HEAD>CHAPTER 1-FEDERAL ACQUISITION REGULATION</HEAD></ancestor>'
        b'<DIV5 N="1" NODE="48:1.0.1.1.1" TYPE="PART"><HEAD>PART 1-FEDERAL ACQUISITION REGULATIONS SYSTEM</HEAD>'
        b"<AUTH><HED>Authority:</HED><PSPACE>41 U.S.C. 1121(b).</PSPACE></AUTH>"
        b'<DIV8 N="1.000" NODE="48:1.0.1.1.1.0.1.1" TYPE="SECTION"><HEAD>1.000   Scope of part.</HEAD>'
        b"<P>This part sets forth basic policies.</P></DIV8>"
        b'<DIV6 N="Subpart 1.1" TYPE="SUBPART"><HEAD>Subpart 1.1-Purpose, Authority, Issuance</HEAD>'
        b'<DIV8 N="1.101" NODE="48:1.0.1.1.1.1.1.1" TYPE="SECTION"><HEAD>1.101   Purpose.</HEAD>'
        b"<P>(a) The Federal Acquisition Regulations System is established.</P>"
        b"<P>(b) It consists of the FAR and agency acquisition regulations.</P></DIV8>"
        b'<DIV8 N="1.102-1" NODE="48:1.0.1.1.1.1.1.3" TYPE="SECTION"><HEAD>1.102-1   Discussion.</HEAD>'
        b"<P>(a) Introduction.</P></DIV8>"
        b'<DIV8 N="1.105-1.107" TYPE="SECTION"><HEAD>1.105-1.107   [Reserved]</HEAD></DIV8>'
        b'<DIV8 N="3.102" TYPE="SECTION"><HEAD>3.102   [Reserved]</HEAD></DIV8>'
        b"</DIV6></DIV5></ecfr-part>"
    )
    doc = parse_ecfr(part)[0]
    sections = {p.num_path[0]: p for p in doc.provisions if p.kind == "section"}
    assert set(sections) == {"1.000", "1.101", "1.102-1"}
    assert sections["1.000"].heading == "Scope of part." and sections["1.000"].eid == "sec_1.000"
    assert sections["1.101"].heading == "Purpose."
    assert any(p.num_path == ["1.101", "a"] for p in doc.provisions)

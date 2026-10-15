"""Constitutions of Africa, the Middle East, Asia and Oceania: the shared parser, connector and citation module.

Fixtures are real: the Chinese and Armenian pages are the publishers' HTML pages (gzip); the South African and Sri
Lankan fixtures are the real text lines of pages of the publishers' PDFs (``pdftotext``), written back into a small PDF
made by ``tests/constitution_pdf.py`` so that the PDF path (text layer, ``-layout``) is exercised without committing
megabytes.
"""

from __future__ import annotations

import base64
import gzip
import json
import re
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from constitution_pdf import make_pdf

from ulis.catalog import Catalog
from ulis.ingestion.pipeline import CONNECTORS, PARSER_VERSIONS, PARSERS, build_connector
from ulis.jurisdictions.modules import ProvisionCitationInput, get_module
from ulis.settings import REPO_ROOT
from ulis.sources.connectors.constitution_docs import ConstitutionDocsConnector, crawl_delay
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.polite_http import FetchError, PoliteHttp
from ulis.sources.normalized import ParsedDocument, ParsedProvision, ParseSkipped
from ulis.sources.parsers.constitution_text import (
    BUNDLE_SCHEMA,
    ConstitutionParseError,
    bundle_bytes,
    document_lines,
    parse_constitution,
    parse_number,
)
from ulis.sources.registry import LicenceNotApproved, licence_gate

FIX = REPO_ROOT / "tests" / "fixtures" / "constitution_text"
RETRIEVED = "2026-10-15T09:00:00+00:00"
UA = "ULIS-test/0.1 (+https://example.org)"


def _gz(name: str) -> bytes:
    return gzip.decompress((FIX / name).read_bytes())


def _pages(name: str) -> list[list[str]]:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def bundle(
    body: bytes,
    media: str,
    *,
    cc: str = "XX",
    language: str = "en",
    structure: dict[str, Any] | None = None,
    retrieved: str = RETRIEVED,
    **document: Any,
) -> bytes:
    doc = {
        "slug": f"{cc.lower()}-{language}",
        "jurisdiction": cc,
        "language": language,
        "title": document.pop("title", "Test constitution"),
        "citation_name": document.pop("citation_name", f"{cc} Constitution"),
        "structure": structure or {},
        **document,
    }
    return bundle_bytes(
        {
            "schema": BUNDLE_SCHEMA,
            "document": doc,
            "url": f"https://example.org/{doc['slug']}",
            "final_url": f"https://example.org/{doc['slug']}",
            "media": media,
            "retrieved_at": retrieved,
            "body_b64": base64.b64encode(body).decode("ascii"),
        }
    )


def text_bundle(lines: list[str], **kw: Any) -> bytes:
    """A bundle of an HTML page with one paragraph per line (the parser reads the lines of the rendered page)."""
    body = "<html><body>" + "".join(f"<p>{x}</p>" for x in lines) + "</body></html>"
    return bundle(body.encode("utf-8"), "html", **kw)


def parse(content: bytes) -> ParsedDocument:
    (doc,) = parse_constitution(content)
    return doc


def prov(doc: ParsedDocument, eid: str) -> ParsedProvision:
    return next(p for p in doc.provisions if p.eid == eid)


def tops(doc: ParsedDocument) -> list[ParsedProvision]:
    return [p for p in doc.provisions if p.parent_eid is None]


CN_HINTS = {"html_select": {"id": "UCAP-CONTENT"}}
ZA_HINTS = {
    "layout": True,
    "page_numbers": True,
    "heading_before": True,
    "unit_kind": "section",
    "drop_lines": ["^Chapter [0-9]+: ", "^Schedule [0-9A-B]+: "],
}
LK_HINTS = {
    "page_numbers": True,
    "heading_before": True,
    "unit_kind": "article",
    "preamble": False,
    "drop_lines": ["^The Constitution of the Democratic Socialist Republic of Sri Lanka$"],
    "tail_regex": "^Devo Vassatukalena$",
    "note_lines": ["^[0-9]{1,3} ?- ?(Inserted|Substituted|Repealed|Amended)", "^[0-9]{1,3}$"],
}


def cn() -> ParsedDocument:
    return parse(
        bundle(
            _gz("cn-gov-cn.html.gz"),
            "html",
            cc="CN",
            language="zh",
            structure=CN_HINTS,
            title="中华人民共和国宪法",
            citation_name="中华人民共和国宪法",
            adopted_on="1982-12-04",
            edition_date="2018-03-22",
            events=[{"type": "adoption", "date": "1982-12-04", "description": "通过"}],
        )
    )


def am() -> ParsedDocument:
    return parse(
        bundle(
            _gz("am-concourt-en.html.gz"),
            "html",
            cc="AM",
            structure={
                "html_select": {"class": "workTimeLineInfoChild"},
                "tail_regex": "^Article 2\\. The amendments to this Constitution shall enter",
                "paragraphs": "dot",
            },
            title="Constitution of the Republic of Armenia (with amendments)",
            citation_name="Armenia Constitution",
        )
    )


def za() -> ParsedDocument:
    return parse(
        bundle(
            make_pdf(_pages("za-en-pages.json")),
            "pdf",
            cc="ZA",
            structure=ZA_HINTS,
            title="Constitution of the Republic of South Africa, 1996",
            citation_name="South Africa Constitution",
            edition_date="2026-05-25",
        )
    )


def lk() -> ParsedDocument:
    return parse(
        bundle(
            make_pdf(_pages("lk-en-pages.json")),
            "pdf",
            cc="LK",
            structure=LK_HINTS,
            title="The Constitution of the Democratic Socialist Republic of Sri Lanka",
            citation_name="Sri Lanka Constitution",
        )
    )


# --------------------------------------------------------------------------- numbers


def test_parse_number_reads_the_numbering_of_many_scripts() -> None:
    assert parse_number("12") == (12, "")
    assert parse_number("12", "A") == (12, "a")
    assert parse_number("12", "-1") == (12, "_1")
    assert parse_number("12", ".1") == (12, "_1")
    assert parse_number("107.º", "-A") == (107, "a")
    assert parse_number("premier") == (1, "")
    assert parse_number("五十一") == (51, "")
    assert parse_number("一百四十三") == (143, "")
    assert parse_number("二十") == (20, "")
    assert parse_number("١٢") == (12, "")  # Arabic-Indic digits
    assert parse_number("๑๒") == (12, "")  # Thai digits
    assert parse_number("pasal") is None


# --------------------------------------------------------------------------- HTML: China and Armenia


def test_china_page_is_split_into_preamble_and_143_articles() -> None:
    doc = cn()
    assert doc.instrument_type == "constitution" and doc.jurisdiction_id == "CN" and doc.language == "zh"
    assert doc.work_uri == "/cn/constitution" and doc.expression_uri == "/cn/constitution/zh@2018-03-22"
    assert doc.short_title == "中华人民共和国宪法" and doc.work_date == date(1982, 12, 4)
    assert doc.validity_basis == "attested_at_edition" and doc.version_date == date(2018, 3, 22)
    assert doc.currency_date == date(2018, 3, 22) and doc.consolidation_kind == "constitution_text"
    assert doc.authoritative is False and doc.text_origin == "official_consolidation"
    articles = [p for p in doc.provisions if p.kind == "article"]
    assert [p.eid for p in articles] == [f"art-{n}" for n in range(1, 144)]
    assert prov(doc, "preamble").text.startswith("中国是世界上历史最悠久的国家之一。")
    # exact text, one line per paragraph of the page
    assert prov(doc, "art-1").text == (
        "中华人民共和国是工人阶级领导的、以工农联盟为基础的人民民主专政的社会主义国家。\n"
        "社会主义制度是中华人民共和国的根本制度。中国共产党领导是中国特色社会主义最本质的特征。禁止任何组织或者个人破坏社会主义制度。"
    )
    art51 = prov(doc, "art-51")
    assert (
        art51.text
        == "中华人民共和国公民在行使自由和权利的时候，不得损害国家的、社会的、集体的利益和其他公民的合法的自由和权利。"
    )
    assert art51.context_heading == "第二章 公民的基本权利和义务"
    assert prov(doc, "art-1").context_heading == "第一章 总纲"
    assert prov(doc, "art-143").status == "in_force" and prov(doc, "art-143").valid_from is None
    assert all(p.valid_from is None and p.valid_to is None for p in doc.provisions)
    assert [e.event_type for e in doc.events] == ["adoption"]


def test_china_citation_uses_the_chinese_form() -> None:
    doc = cn()
    p = prov(doc, "art-51")
    module = get_module("constitution")
    cite = module.provision_citation(
        ProvisionCitationInput(doc.short_title or "", "constitution", "CN", p.top_kind, p.num_path, "zh", eid=p.eid)
    )
    assert cite == "中华人民共和国宪法第51条"


def test_armenia_page_headings_paragraphs_and_trailing_amending_text() -> None:
    doc = am()
    arts = [p for p in doc.provisions if p.kind == "article"]
    assert len(arts) == 220 and arts[0].eid == "art-1" and arts[-1].eid == "art-220"
    # an article that is a single sentence is text, not a heading
    one = prov(doc, "art-1")
    assert (
        one.heading is None
        and one.text == "The Republic of Armenia is a sovereign, democratic, social state governed by the rule of law"
    )
    three = prov(doc, "art-3")
    assert three.heading == "The Human Being, His or Her Dignity, Basic Rights and Freedoms"
    assert three.context_heading == "CHAPTER 1 | FUNDAMENTALS OF THE CONSTITUTIONAL ORDER"
    children = [p for p in doc.provisions if p.parent_eid == "art-3"]
    assert [c.eid for c in children] == ["art-3-par1", "art-3-par2", "art-3-par3"]
    assert children[0].text.startswith("The human being shall be the highest value in the Republic of Armenia.")
    assert three.text.startswith(
        "1. The human being shall be the highest value"
    )  # the parent carries the composite text
    # the amending law's last article printed after Article 220 is a trailing block, not Article 221
    assert "art-221" not in {p.eid for p in doc.provisions}
    blocks = [p for p in doc.provisions if p.kind == "block"]
    assert len(blocks) == 1 and blocks[0].text.startswith(
        "Article 2. The amendments to this Constitution shall enter into force"
    )
    assert "stored as blocks of paragraphs" in (blocks[0].note or "")
    assert "Design and development" not in "".join(p.text for p in doc.provisions)  # the page's footer is not selected


# --------------------------------------------------------------------------- PDF: South Africa and Sri Lanka


def test_south_africa_pdf_layout_headings_above_numbers_chapters_and_tail() -> None:
    doc = za()
    assert doc.version_date == date(2026, 5, 25)
    secs = [p for p in doc.provisions if p.kind == "section" and p.parent_eid is None]
    numbers = [int(p.eid.split("-")[1]) for p in secs]
    assert numbers[:12] == list(range(1, 13)) and numbers == sorted(numbers)
    s1 = prov(doc, "sec-1")
    assert s1.heading == "Republic of South Africa" and s1.context_heading == "CHAPTER 1 FOUNDING PROVISIONS"
    assert s1.text.startswith(
        "The Republic of South Africa is one, sovereign, democratic state founded on the following"
    )
    s7 = prov(doc, "sec-7")
    assert s7.heading == "Rights" and s7.context_heading == "CHAPTER 2 BILL OF RIGHTS"
    # numbered paragraphs "(1)" printed in the margin column stay with their text (pdftotext -layout)
    kids = [p for p in doc.provisions if p.parent_eid == "sec-7"]
    assert [k.eid for k in kids] == ["sec-7-par1", "sec-7-par2", "sec-7-par3"]
    assert kids[1].text.startswith(
        "The state must respect, protect, promote and fulfil the rights in the Bill of Rights."
    )
    assert prov(doc, "sec-8").heading == "Application"
    # the preamble, and the contents list was not mistaken for sections
    pre = prov(doc, "preamble")
    assert pre.heading == "PREAMBLE" and pre.text.startswith("We, the people of South Africa,")
    assert "CONTENTS" not in "".join(p.text for p in secs)
    # running heads of the pages ("Chapter 2: Bill of Rights") are not text
    assert "Chapter 2: Bill of Rights" not in "".join(p.text for p in doc.provisions)
    # schedules after the last section are blocks
    blocks = [p for p in doc.provisions if p.kind == "block"]
    assert blocks and "SCHEDULE 1" in blocks[0].text or any("SCHEDULE 1" in b.text for b in blocks)
    assert "Text after the last numbered section" in (blocks[0].note or "")
    first = secs[0]
    assert first.note and "front matter" in first.note


def test_sri_lanka_pdf_footnotes_and_marginal_notes() -> None:
    doc = lk()
    assert doc.work_date is None
    arts = [p for p in doc.provisions if p.kind == "article" and p.parent_eid is None]
    assert [p.eid for p in arts][:4] == ["art-1", "art-2", "art-3", "art-4"]
    one = prov(doc, "art-1")
    assert one.context_heading == "CHAPTER I THE PEOPLE, THE STATE AND SOVEREIGNTY"
    assert one.text.startswith("Sri Lanka (Ceylon) is a Free, Sovereign,")
    # the contents list on page 7 has every article number but almost no text: it is not the body
    assert len(arts) < 40
    # a footnote that names the amending Amendment is a note, not text
    notes = [p for p in doc.provisions if p.note and "Inserted by the" in p.note]
    assert notes, "the footnote 'Inserted by the Sixteenth Amendment' is kept as a note"
    assert all("Inserted by the" not in p.text for p in doc.provisions)
    # the closing stanza and what follows the last numbered article are trailing blocks
    assert any(p.kind == "block" and "Devo Vassatukalena" in p.text for p in doc.provisions)


# --------------------------------------------------------------------------- HTML: Russian, no dot after the number


def test_russian_page_with_bare_article_numbers() -> None:
    lines = ["Конституция", "ПРЕАМБУЛА", "Мы, единый народ Узбекистана,", "принимаем Конституцию."]
    for n in range(1, 8):
        lines += [f"Статья {n}", f"Текст статьи {n}.", f"Продолжение статьи {n}."]
    doc = parse(text_bundle(lines, cc="UZ", language="ru"))
    assert [p.eid for p in doc.provisions] == ["preamble", *[f"art-{n}" for n in range(1, 8)]]
    assert prov(doc, "art-3").text == "Текст статьи 3.\nПродолжение статьи 3."
    assert prov(doc, "preamble").heading == "ПРЕАМБУЛА"


# --------------------------------------------------------------------------- structure rules


def test_contents_list_is_not_the_body_and_missing_headings_are_noted() -> None:
    toc = [f"Article {n} (Title {n}).....{n}" for n in range(1, 9)]
    toc_plain = [f"Article {n}" for n in range(1, 9)]  # a contents list without page numbers: same numbers, no text
    body: list[str] = []
    for n in range(1, 9):
        if n == 5:
            body += ["The text of the fifth article is run into the fourth."]
            continue
        body += [f"Article {n}", f"Words of article {n} that are long enough to count as text of the body."]
    doc = parse(text_bundle(["CONTENTS", *toc, *toc_plain, "PREAMBLE", "We the people.", *body]))
    assert [p.eid for p in doc.provisions] == [
        "preamble",
        "art-1",
        "art-2",
        "art-3",
        "art-4",
        "art-6",
        "art-7",
        "art-8",
    ]
    assert "run into the fourth" in prov(doc, "art-4").text
    assert "article 5 was not recognised" in (prov(doc, "art-4").note or "")
    assert prov(doc, "art-1").text.startswith("Words of article 1")
    assert "CONTENTS" not in "".join(p.text for p in doc.provisions)


def test_numbered_paragraphs_need_a_sequence_from_one() -> None:
    lines = []
    for n in range(1, 7):
        lines += [f"Article {n}"]
        if n == 2:
            lines += ["(1) First paragraph of article 2.", "(2) Second paragraph.", "(3) Third paragraph."]
        elif n == 3:
            lines += ["(2) A paragraph that does not start at one stays text.", "(4) Another."]
        else:
            lines += [f"Plain text of article {n}."]
    doc = parse(text_bundle(lines))
    assert [p.eid for p in doc.provisions if p.parent_eid == "art-2"] == ["art-2-par1", "art-2-par2", "art-2-par3"]
    assert not [p for p in doc.provisions if p.parent_eid == "art-3"]
    assert prov(doc, "art-3").text == "(2) A paragraph that does not start at one stays text.\n(4) Another."


def test_unstructured_text_is_stored_in_blocks_and_says_so() -> None:
    paragraphs = [
        f"This is paragraph {n} of a constitution whose headings cannot be recognised. " * 4 for n in range(20)
    ]
    doc = parse(text_bundle(paragraphs))
    assert doc.consolidation_kind == "constitution_blocks"
    blocks = tops(doc)
    assert len(blocks) > 1 and all(b.kind == "block" and b.eid == f"blk-{i}" for i, b in enumerate(blocks, 1))
    assert all("Structure not recognised" in (b.note or "") for b in blocks)
    assert " ".join(b.text for b in blocks).split() == " ".join(paragraphs).split()  # every word, in order
    module = get_module("constitution")
    cite = module.provision_citation(
        ProvisionCitationInput("Test Constitution", "constitution", "XX", "block", ["blk:3"], "en", eid="blk-3")
    )
    assert cite == "Test Constitution, text block 3"


def test_number_first_headings_and_the_manifest_hints() -> None:
    lines = []
    for n in range(1, 8):
        lines += [f"{n}-modda.", f"{n}-moddaning matni, bu yerda yetarlicha uzun."]
    doc = parse(text_bundle(lines, language="uz", structure={"keywords_after": ["modda"]}))
    assert [p.eid for p in doc.provisions] == [f"art-{n}" for n in range(1, 8)]
    # a section-numbered text keeps "sec-" ids and a section citation
    lines = []
    for n in range(1, 8):
        lines += [f"{n}. Heading {n}", f"Text of section {n}, long enough."]
    doc = parse(text_bundle(lines, structure={"heading": "same_line", "style": "numdot"}))
    s3 = prov(doc, "sec-3")
    assert s3.heading == "Heading 3" and s3.kind == "section"
    module = get_module("constitution")
    cite = module.provision_citation(
        ProvisionCitationInput("Test Constitution", "constitution", "XX", "section", ["sec:3"], "en", eid="sec-3")
    )
    assert cite == "Test Constitution s. 3"


def test_unnumbered_interlude_between_numbered_articles_is_kept_as_blocks() -> None:
    lines: list[str] = []
    for n in range(1, 7):
        lines += [f"Article {n}", f"Words of article {n}, long enough to count."]
    lines += [
        "Provisional Provisions",
        "Provisional article 1 - first transitional rule of the text.",
        "Second transitional rule.",
    ]
    for n in range(7, 9):
        lines += [f"Article {n}", f"Words of article {n}, long enough to count."]
    doc = parse(text_bundle(lines, structure={"interlude_regex": "^Provisional Provisions$"}))
    assert [p.eid for p in tops(doc)] == [f"art-{n}" for n in range(1, 7)] + ["blk-1", "art-7", "art-8"]
    assert prov(doc, "art-6").text == "Words of article 6, long enough to count."
    block = prov(doc, "blk-1")
    assert block.text.startswith("Provisional Provisions") and "Second transitional rule." in block.text
    assert "Unnumbered text between the numbered articles" in (block.note or "")
    assert prov(doc, "art-7").text == "Words of article 7, long enough to count."


def test_footnote_marks_danda_numbers_headings_on_their_own_line_and_wide_gaps() -> None:
    lines = ["PREAMBLE", "We, the people, adopt this text of the preamble.", "Part I", "THE REPUBLIC"]
    lines += ["The Republic", "1. The Republic is unitary, independent and sovereign."]
    lines += ["The state religion", "5[2A. The state religion is stated here.]"]
    for n in range(3, 8):
        lines += [f"Heading {n}", f"{n}. Text of article {n}, long enough to count."]
    lines += ["Heading 12", "12. Text of article 12 after a wide gap in the numbering."]
    doc = parse(
        text_bundle(
            lines,
            structure={
                "style": "numdot",
                "unit_kind": "article",
                "heading_before": "line",
                "chapter_regex": "^Part [IVX]+$",
                "preamble_regex": "^PREAMBLE$",
                "max_gap": 6,
            },
        )
    )
    assert [p.eid for p in tops(doc)] == ["preamble", "art-1", "art-2a"] + [f"art-{n}" for n in (3, 4, 5, 6, 7, 12)]
    assert prov(doc, "art-1").heading == "The Republic" and prov(doc, "art-1").context_heading == "Part I THE REPUBLIC"
    assert prov(doc, "art-2a").heading == "The state religion"
    assert prov(doc, "art-2a").text == "The state religion is stated here.]"
    assert "footnote reference 5" in (prov(doc, "art-2a").note or "")
    assert prov(doc, "art-12").heading == "Heading 12"
    bengali = ["প্রস্তাবনা", "আমরা এই সংবিধান গ্রহণ করিলাম এবং ইহা প্রস্তাবনার পাঠ।"]
    for number in ("১", "২", "২ক", "৩", "৪", "৫"):
        bengali += [f"শিরোনাম {number}", f"{number}। এই অনুচ্ছেদের পাঠ, যথেষ্ট দীর্ঘ।"]
    doc = parse(
        text_bundle(
            bengali,
            language="bn",
            structure={
                "style": "numdot",
                "unit_kind": "article",
                "heading_before": "line",
                "preamble_regex": "^প্রস্তাবনা$",
            },
        )
    )
    assert [p.eid for p in tops(doc)] == ["preamble", "art-1", "art-2", "art-2a", "art-3", "art-4", "art-5"]


def test_page_furniture_is_only_removed_when_the_manifest_names_it() -> None:
    pages = [
        ["Article 1", "Words of the first article.", "", "- 7 -"],
        ["RUNNING HEAD", "Article 2", "Words of the second article.", "9"],
    ]
    body = make_pdf(pages)
    plain = document_lines(json.loads(bundle(body, "pdf")))
    assert "RUNNING HEAD" in plain and "-7-" in plain and "9" in plain  # pdftotext writes "- 7 -" as "-7-"
    cleaned = document_lines(
        json.loads(bundle(body, "pdf", structure={"page_numbers": True, "drop_lines": ["^RUNNING HEAD$"]}))
    )
    assert "RUNNING HEAD" not in cleaned and "-7-" not in cleaned and "9" not in cleaned
    assert "Words of the second article." in cleaned


def test_parser_rejects_foreign_input_and_empty_documents() -> None:
    with pytest.raises(ConstitutionParseError, match="invalid JSON"):
        parse_constitution(b"<html/>")
    with pytest.raises(ConstitutionParseError, match="schema"):
        parse_constitution(json.dumps({"schema": "other"}).encode())
    bad = json.loads(text_bundle(["x"] * 3))
    bad["document"]["text_origin"] = "made_up"
    with pytest.raises(ConstitutionParseError, match="text_origin"):
        parse_constitution(json.dumps(bad).encode())
    with pytest.raises(ParseSkipped):
        parse_constitution(text_bundle([]))
    with pytest.raises(ConstitutionParseError, match="pdftotext failed"):
        parse_constitution(bundle(b"%PDF-1.4 broken", "pdf"))
    with pytest.raises(ConstitutionParseError, match="start_regex"):
        parse_constitution(text_bundle(["Article 1", "x"] * 6, structure={"start_regex": "^nothing$"}))


# --------------------------------------------------------------------------- citation module


def test_citation_forms() -> None:
    module = get_module("constitution")

    def cite(eid: str, kind: str, path: list[str], title: str = "Ghana Constitution", language: str = "en") -> str:
        return module.provision_citation(
            ProvisionCitationInput(title, "constitution", "GH", kind, path, language, eid=eid)
        )

    assert cite("art-12", "article", ["art:12"]) == "Ghana Constitution art. 12"
    assert cite("art-12-par3", "article", ["art:12", "par:3"]) == "Ghana Constitution art. 12(3)"
    assert cite("art-107a", "article", ["art:107a"]) == "Ghana Constitution art. 107A"
    assert cite("art-13_19", "article", ["art:13_19"]) == "Ghana Constitution art. 13-19"
    assert cite("sec-33", "section", ["sec:33"]) == "Ghana Constitution s. 33"
    assert cite("preamble", "preamble", ["preamble"]) == "Ghana Constitution, preamble"
    assert cite("art-51", "article", ["art:51"], "中华人民共和国宪法", "zh") == "中华人民共和国宪法第51条"
    assert (
        cite("art-51-par2", "article", ["art:51", "par:2"], "中华人民共和国宪法", "zh")
        == "中华人民共和国宪法第51条第2款"
    )
    assert cite("art-23", "article", ["art:23"], "澳門基本法", "zh-Hant") == "澳門基本法第23條"


# --------------------------------------------------------------------------- connector (offline)

HOST = "constitutions.example.org"


def _write(
    http: PoliteHttp,
    url: str,
    data: bytes,
    status: int = 200,
    ctype: str = "text/html",
    final: str | None = None,
    slug: str | None = None,
    ext: str = "html",
) -> None:
    """Put a response into the cache under the key the connector uses (robots.txt, or constitutions/<slug>.<ext>)."""
    if url.endswith("/robots.txt"):
        key, ctype = "robots.txt", "text/plain" if ctype == "text/html" else ctype
    else:
        key = f"constitutions/{slug}.{ext}"
    path = http.cache_path(url, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    meta = {"url": url, "final_url": final or url, "status": status, "content_type": ctype, "retrieved_at": RETRIEVED}
    path.with_name(path.name + ".meta.json").write_text(json.dumps(meta))


def _manifest(tmp_path: Path, documents: list[dict[str, Any]], **extra: Any) -> Path:
    path = tmp_path / "manifest.yaml"
    data = {
        "schema_version": 1,
        "source_id": "x-test",
        "user_agent": UA,
        "crawl_delay_seconds": 5,
        "hosts": [HOST],
        "documents": documents,
        **extra,
    }
    path.write_text(yaml.safe_dump(data, allow_unicode=True))
    return path


def _doc(slug: str, url: str, fmt: str = "html", **extra: Any) -> dict[str, Any]:
    return {
        "slug": slug,
        "jurisdiction": "XX",
        "url": f"https://{HOST}{url}",
        "format": fmt,
        "language": "en",
        "title": "Test constitution",
        "citation_name": "Test Constitution",
        **extra,
    }


def _http(tmp_path: Path) -> tuple[Path, PoliteHttp]:
    cache = tmp_path / "cache"
    return cache, PoliteHttp(cache, UA, allowed_hosts={HOST}, offline=True)


def _page(n: int = 8) -> bytes:
    lines = []
    for k in range(1, n + 1):
        lines += [f"Article {k}", f"Text of article {k} is long enough to be the body."]
    return ("<html><body>" + "".join(f"<p>{x}</p>" for x in lines) + "</body></html>").encode()


def test_connector_yields_bundles_with_the_exact_bytes_and_metadata(tmp_path: Path) -> None:
    cache, http = _http(tmp_path)
    _write(http, f"https://{HOST}/robots.txt", b"", status=404)
    page = _page()
    _write(http, f"https://{HOST}/en/constitution", page, slug="xx-en")
    pdf = make_pdf([["Article 1", "x " * 30]])
    _write(http, f"https://{HOST}/c.pdf", pdf, ctype="application/pdf", slug="xx-pdf", ext="pdf")
    manifest = _manifest(
        tmp_path,
        [
            _doc("xx-en", "/en/constitution", structure={"unit_kind": "article"}, edition_date="2020-01-02"),
            _doc("xx-pdf", "/c.pdf", "pdf"),
        ],
    )
    connector = ConstitutionDocsConnector(manifest, "x-test", cache, offline=True)
    docs = list(connector.fetch())
    assert [d.external_id for d in docs] == ["constitution:xx-en", "constitution:xx-pdf"]
    first = json.loads(docs[0].content)
    assert first["schema"] == BUNDLE_SCHEMA and first["media"] == "html" and first["retrieved_at"] == RETRIEVED
    assert base64.b64decode(first["body_b64"]) == page  # exactly the bytes received
    assert first["document"]["edition_date"] == "2020-01-02" and first["document"]["structure"] == {
        "unit_kind": "article"
    }
    assert docs[0].media_type == "application/json" and docs[0].retrieved_at == datetime(2026, 10, 15, 9, 0, tzinfo=UTC)
    assert base64.b64decode(json.loads(docs[1].content)["body_b64"]) == pdf
    assert connector.fetch_errors == [] and connector.http is not None and connector.http.requests_made == 0
    assert len(parse(docs[0].content).provisions) == 8


def test_connector_joins_the_pages_of_a_document_published_chapter_by_chapter(tmp_path: Path) -> None:
    cache, http = _http(tmp_path)
    _write(http, f"https://{HOST}/robots.txt", b"", status=404)
    pages = []
    for number in range(1, 4):
        body = f"<html><head><title>Site - Chapter {number}</title></head><body>"
        body += f'<span class="hd">Section {number}</span>Title of section {number}'
        for k in range(2 * number - 1, 2 * number + 1):
            body += f"<h2>Article {k}</h2><p>Text of article {k} is long enough to be the body.</p>"
        body += '<div class="nav">Prev | Next</div></body></html>'
        pages.append(body.encode())
        _write(http, f"https://{HOST}/c/ch{number}.html", pages[-1], slug=f"xx-en-{number}")
    parts = [{"url": f"https://{HOST}/c/ch{n}.html", "heading": f"Chapter {n}"} for n in range(1, 4)]
    manifest = _manifest(
        tmp_path,
        [
            _doc(
                "xx-en",
                "/c/index.html",
                parts=parts,
                structure={
                    "html_skip_tags": ["title"],
                    "html_skip_classes": ["nav"],
                    "html_replace": [['(<span class="hd">[^<]*</span>)', "\\1 "]],
                    "chapter_regex": "^Chapter [0-9]+$",
                    "chapter_title_lines": 1,
                    "heading": "text",
                    "unit_kind": "article",
                },
            )
        ],
    )
    connector = ConstitutionDocsConnector(manifest, "x-test", cache, offline=True)
    (raw,) = list(connector.fetch())
    assert connector.fetch_errors == []
    sent = json.loads(raw.content)
    assert [base64.b64decode(p["body_b64"]) for p in sent["parts"]] == pages  # every page, exactly as received
    assert raw.uri == f"https://{HOST}/c/index.html"
    doc = parse(raw.content)
    assert [p.eid for p in doc.provisions] == [f"art-{n}" for n in range(1, 7)]
    assert prov(doc, "art-1").text == "Text of article 1 is long enough to be the body."
    assert "Prev" not in " ".join(p.text for p in doc.provisions) and "Site -" not in " ".join(
        p.text for p in doc.provisions
    )
    assert prov(doc, "art-1").context_heading == "Chapter 1 Section 1 Title of section 1"
    assert prov(doc, "art-6").context_heading == "Chapter 3 Section 3 Title of section 3"
    # a page that is missing stops that document and is reported
    (cache / HOST / "constitutions" / "xx-en-2.html").unlink()
    again = ConstitutionDocsConnector(manifest, "x-test", cache, offline=True)
    assert list(again.fetch()) == [] and "xx-en" in again.fetch_errors[0]


def test_connector_robots_rules_and_soft_robots_pages(tmp_path: Path) -> None:
    cache, http = _http(tmp_path)
    _write(http, f"https://{HOST}/robots.txt", b"User-agent: *\nDisallow: /private/\nCrawl-delay: 12\n")
    _write(http, f"https://{HOST}/ok", _page(), slug="xx-ok")
    _write(http, f"https://{HOST}/private/c", _page(), slug="xx-private")
    connector = ConstitutionDocsConnector(
        _manifest(tmp_path, [_doc("xx-ok", "/ok"), _doc("xx-private", "/private/c")]), "x-test", cache, offline=True
    )
    docs = list(connector.fetch())
    assert [d.external_id for d in docs] == ["constitution:xx-ok"]
    assert (
        len(connector.fetch_errors) == 1
        and "disallowed by" in connector.fetch_errors[0]
        and "xx-private" in connector.fetch_errors[0]
    )
    assert (
        connector.http is not None and connector.http.crawl_delay == 12
    )  # the robots.txt delay is longer than the manifest's
    assert (
        crawl_delay("User-agent: *\nCrawl-delay: 30\n", UA) == 30
        and crawl_delay("User-agent: *\nDisallow: /\n", UA) is None
    )
    assert crawl_delay("User-agent: ULIS-test\nCrawl-delay: 3\n\nUser-agent: *\nCrawl-delay: 30\n", UA) == 3
    # a site that answers robots.txt with an HTML page has no robots.txt
    cache2 = tmp_path / "cache2"
    http2 = PoliteHttp(cache2, UA, allowed_hosts={HOST}, offline=True)
    _write(http2, f"https://{HOST}/robots.txt", b"<!DOCTYPE html><html><body>Welcome</body></html>")
    _write(http2, f"https://{HOST}/ok", _page(), slug="xx-ok")
    soft = ConstitutionDocsConnector(_manifest(tmp_path, [_doc("xx-ok", "/ok")]), "x-test", cache2, offline=True)
    assert len(list(soft.fetch())) == 1 and soft.fetch_errors == []


def test_connector_checks_what_it_got_and_goes_on(tmp_path: Path) -> None:
    cache, http = _http(tmp_path)
    _write(http, f"https://{HOST}/robots.txt", b"", status=404)
    _write(http, f"https://{HOST}/gone", b"", status=404, slug="xx-gone")
    _write(http, f"https://{HOST}/moved", _page(), final=f"https://{HOST}/home", slug="xx-moved")  # inside the host
    _write(http, f"https://{HOST}/elsewhere", _page(), final="https://other.example.org/x", slug="xx-elsewhere")
    _write(http, f"https://{HOST}/notpdf", b"<html>not a pdf</html>", slug="xx-notpdf", ext="pdf")
    _write(
        http,
        f"https://{HOST}/pdf-as-html",
        make_pdf([["Article 1", "x"]]),
        ctype="application/pdf",
        slug="xx-pdf-as-html",
    )
    _write(http, f"https://{HOST}/good", _page(), slug="xx-good")
    docs = [
        _doc("xx-gone", "/gone"),
        _doc("xx-moved", "/moved"),
        _doc("xx-elsewhere", "/elsewhere"),
        _doc("xx-notpdf", "/notpdf", "pdf"),
        _doc("xx-pdf-as-html", "/pdf-as-html", "html"),
        _doc("xx-good", "/good"),
    ]
    connector = ConstitutionDocsConnector(_manifest(tmp_path, docs), "x-test", cache, offline=True)
    assert [d.external_id for d in connector.fetch()] == ["constitution:xx-good"]
    errors = "\n".join(connector.fetch_errors)
    for needle in (
        "xx-gone: https://" + HOST + "/gone not found (404)",
        "xx-moved: redirected to",
        "xx-elsewhere: redirected to another host",
        "xx-notpdf: not a PDF",
        "xx-pdf-as-html: a PDF, but the manifest says html",
    ):
        assert needle in errors, needle
    # a redirect inside the host is accepted when the manifest says so after checking it
    allowed = ConstitutionDocsConnector(
        _manifest(tmp_path, [_doc("xx-moved", "/moved", allow_redirect=True)]), "x-test", cache, offline=True
    )
    assert len(list(allowed.fetch())) == 1


def test_connector_refusal_of_robots_stops_the_host_and_offline_needs_the_cache(tmp_path: Path) -> None:
    cache, http = _http(tmp_path)
    _write(http, f"https://{HOST}/robots.txt", b"", status=404)
    # offline, a document that was never fetched is an error of the run, not a crash
    connector = ConstitutionDocsConnector(_manifest(tmp_path, [_doc("xx-a", "/a")]), "x-test", cache, offline=True)
    assert list(connector.fetch()) == [] and "offline and not cached" in connector.fetch_errors[0]
    # a host whose robots.txt cannot be read is not fetched from
    connector = ConstitutionDocsConnector(
        _manifest(tmp_path, [_doc("xx-a", "/a"), _doc("xx-b", "/b")]), "x-test", tmp_path / "empty", offline=True
    )
    assert list(connector.fetch()) == [] and len(connector.fetch_errors) == 2
    assert all("offline and not cached" in e and "robots.txt" in e for e in connector.fetch_errors)
    # an HTTP refusal (403) of the host stops its remaining documents
    refused = ConstitutionDocsConnector(
        _manifest(tmp_path, [_doc("xx-a", "/a"), _doc("xx-b", "/b")]), "x-test", tmp_path, offline=True
    )

    class Refuse(PoliteHttp):
        def get(self, url: str, cache_key: str | None = None, headers: dict[str, str] | None = None) -> Any:
            raise FetchError(f"{url}: HTTP 403 (not retried: a refusal is respected)")

    import ulis.sources.connectors.constitution_docs as module

    original = module.PoliteHttp
    module.PoliteHttp = Refuse  # type: ignore[misc]
    try:
        assert list(refused.fetch()) == []
    finally:
        module.PoliteHttp = original  # type: ignore[misc]
    assert "HTTP 403" in refused.fetch_errors[0] and "refused an earlier request" in refused.fetch_errors[1]


def test_connector_refuses_bad_manifests(tmp_path: Path) -> None:
    good = [_doc("xx-en", "/en")]
    for docs, extra, message in (
        ([_doc("Bad Slug", "/a")], {}, "slug"),
        ([_doc("xx-a", "/a"), _doc("xx-a", "/b")], {}, "slug"),
        ([{**_doc("xx-a", "/a"), "jurisdiction": "xx"}], {}, "jurisdiction"),
        ([{**_doc("xx-a", "/a"), "format": "docx"}], {}, "format"),
        ([{**_doc("xx-a", "/a"), "url": "http://constitutions.example.org/a"}], {}, "https"),
        ([{**_doc("xx-a", "/a"), "url": "https://evil.example.com/a"}], {}, "https"),
        ([{**_doc("xx-a", "/a"), "edition_date": "May 2020"}], {}, "ISO date"),
        ([{k: v for k, v in _doc("xx-a", "/a").items() if k != "title"}], {}, "title"),
        (good, {"user_agent": "python-urllib"}, "user_agent"),
        (good, {"hosts": []}, "hosts"),
        ([], {}, "no documents"),
    ):
        with pytest.raises(ManifestError, match=message):
            list(ConstitutionDocsConnector(_manifest(tmp_path, docs, **extra), "x-test", tmp_path).fetch())
    with pytest.raises(ManifestError, match="mismatch"):
        list(ConstitutionDocsConnector(_manifest(tmp_path, good), "other-id", tmp_path).fetch())


# --------------------------------------------------------------------------- committed sources and manifests

ACTIVE = [
    "cn-govcn-constitution",
    "za-doj-constitution",
    "uz-constitution-site",
    "kg-shailoo-constitution",
    "am-concourt-constitution",
    "lk-parliament-constitution",
    "th-senate-constitution",
    "id-mpr-constitution",
    "pk-cci-constitution",
    "sn-cc-constitution",
    "hk-cmab-basiclaw",
    "mo-dsaj-basiclaw",
    "lb-presidency-constitution",
    "bd-bdlaws-constitution",
]


@pytest.mark.parametrize("source_id", ACTIVE)
def test_committed_manifest_loads_and_matches_its_source(catalog: Catalog, source_id: str) -> None:
    source = catalog.sources.get(source_id)
    assert (
        source.status == "active" and source.connector == "constitution_docs" and source.parser == "constitution_text"
    )
    assert source.document_kinds == ["constitution"] and source.licence.review_status == "approved"
    licence_gate(source, commercial=False)
    assert source.licence.attribution_required and "{retrieved_date}" in (source.licence.attribution_text or "")
    assert source.licence.reviewed_on == date(2026, 10, 15) and "not counsel" in (source.licence.reviewed_by or "")
    assert isinstance(build_connector(source), ConstitutionDocsConnector)
    manifest = ConstitutionDocsConnector(
        REPO_ROOT / source.access.endpoint, source_id, Path("/nonexistent")
    )._manifest()
    assert "ULIS-research-bot" in manifest["user_agent"] and manifest["crawl_delay_seconds"] >= 5
    countries = {d["jurisdiction"] for d in manifest["documents"]}
    assert countries == set(source.jurisdictions)
    assert {d["language"] for d in manifest["documents"]} == set(source.languages)
    for country in countries:
        profile = catalog.jurisdictions.get(country)
        assert profile.verification.status == "draft_unverified" and profile.instrument_types == ["constitution"]
        assert profile.citation.module == "constitution" and source_id in profile.publication.official_sources
        assert "Only the constitution is loaded" in profile.coverage.known_gaps[0]
    for d in manifest["documents"]:
        for event in d.get("events", []):
            date.fromisoformat(str(event["date"]))
        if d.get("authoritative") is not None:
            assert d["authoritative"] is False


def test_commercial_deployments_are_refused_where_the_terms_forbid_commercial_use(catalog: Catalog) -> None:
    for source_id in ("cn-govcn-constitution", "za-doj-constitution", "hk-cmab-basiclaw"):
        with pytest.raises(LicenceNotApproved, match="commercial"):
            licence_gate(catalog.sources.get(source_id), commercial=True)
    for source_id in (
        "uz-constitution-site",
        "kg-shailoo-constitution",
        "am-concourt-constitution",
        "lk-parliament-constitution",
        "th-senate-constitution",
        "id-mpr-constitution",
        "pk-cci-constitution",
        "sn-cc-constitution",
        "mo-dsaj-basiclaw",
        "lb-presidency-constitution",
        "bd-bdlaws-constitution",
    ):
        assert catalog.sources.get(source_id).licence.commercial_use == "unknown"
        with pytest.raises(LicenceNotApproved, match="commercial"):
            licence_gate(catalog.sources.get(source_id), commercial=True)


def test_registrations() -> None:
    assert CONNECTORS["constitution_docs"] is ConstitutionDocsConnector
    assert "constitution_text" in PARSERS and PARSER_VERSIONS["constitution_text"] == "1"


def test_cn_note_quotes_the_page_heading_exactly() -> None:
    manifest = yaml.safe_load((REPO_ROOT / "config/manifests/cn-govcn-constitution.yaml").read_text(encoding="utf-8"))
    note = manifest["documents"][0]["note"]
    page = _gz("cn-gov-cn.html.gz").decode("utf-8")
    heading = re.sub(r"[\s　]+", "", note.split("article: ", 1)[1])
    assert heading in re.sub(r"[\s　]+", "", re.sub(r"<[^>]+>", "", page))
    # and the adoption/amendment dates of the events are the ones in that heading
    for event in manifest["documents"][0]["events"]:
        d = date.fromisoformat(str(event["date"]))
        assert f"{d.year}年{d.month}月{d.day}日" in heading
